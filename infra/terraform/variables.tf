# variables.tf — The only knobs. Everything else is a concrete in-region value.
#
# Control map (SPEC concern):
#   Data residency: `region` is constrained to the in-country allowlist and validated so a
#     caller cannot point this stack at a non-APAC-resident region. The same allowlist is
#     mirrored in the app (config.market_profile / resolve_region) so it fails fast off-region
#     too.
#   Auditability / retention: `retention_days` is a deliberate variable because the WORM
#     bucket lock is irreversible (see logging_worm.tf).
#
# marketing-compliance-gate is generic and APAC: JP -> asia-northeast1, AU -> australia-southeast1,
# SG -> asia-southeast1. The default is Singapore (asia-southeast1) per config/settings.yaml.

variable "project_id" {
  description = "Target GCP project id (required). Single-tenant, APAC-resident."
  type        = string
}

variable "region" {
  description = <<-EOT
    Deployment region. Must be one of marketing-compliance-gate's in-country residency regions (SPEC §2):
    asia-southeast1 (SG), asia-northeast1 (JP) or australia-southeast1 (AU).
    Validated to fail fast so the stack can never be pointed off-region. Default SG.
  EOT
  type        = string
  default     = "asia-southeast1"

  validation {
    condition = contains(
      ["asia-southeast1", "asia-northeast1", "australia-southeast1"],
      var.region,
    )
    error_message = "marketing-compliance-gate is APAC-resident: region must be asia-southeast1 (SG), asia-northeast1 (JP) or australia-southeast1 (AU)."
  }
}

variable "deploy_market" {
  description = "Active governed market. It must map exactly to region: SG/asia-southeast1, JP/asia-northeast1 or AU/australia-southeast1."
  type        = string
  default     = "SG"

  validation {
    condition     = contains(["SG", "JP", "AU"], var.deploy_market)
    error_message = "deploy_market must be SG, JP or AU."
  }
}

variable "retention_days" {
  description = <<-EOT
    Audit-log retention in days on the marketing-compliance-gate-worm bucket. Default ~7 years.

    The 2557-day compliance floor binds whenever worm_locked = true. A stack that declines the
    lock is not keeping a record anyone relies on for seven years and may keep less. The floor
    is conditional on the lock rather than removed, so a LOCKED bucket can never be created with
    a short window.
  EOT
  type        = number
  default     = 2557 # mirrors config/settings.yaml logging.retention_days

  validation {
    condition     = var.retention_days >= 1 && (!var.worm_locked || var.retention_days >= 2557)
    error_message = "retention_days must be at least 1, and at least 2557 (~7 years) whenever worm_locked = true."
  }
}

variable "worm_locked" {
  type        = bool
  description = <<-EOT
    Lock the marketing-compliance-gate-worm audit bucket.

    #########################################################################
    # WARNING: LOCKING IS IRREVERSIBLE. With true, the bucket and its       #
    # retention window can NEVER be reduced or deleted until every entry    #
    # ages out (retention_days), not even with project-owner rights.        #
    #########################################################################

    NO DEFAULT, and that is the decision. An irreversible control must never arrive because a
    deployment said nothing, so there is no default of true. A fork running this as a system
    of record must not quietly lose the WORM guarantee either, so there is no default of false.
    Every plan names it.

    true is the compliant production posture. false keeps the bucket, its retention and its
    sink, and leaves the bucket destroyable: an evaluation or reference posture, NOT WORM.
    Setting false against a bucket that is ALREADY locked does not unlock it; the API refuses.
    This governs the first apply.
  EOT
}

variable "manage_org_policies" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether THIS stack writes the project's Org Policies (gcp.resourceLocations,
    iam.disableServiceAccountKeyCreation, compute.vmExternalIpAccess and
    storage.uniformBucketLevelAccess).

    True by default, because a fork deploying this app on its own project should inherit the
    residency guardrail rather than have to remember it. Set false where another stack in the
    same project already owns them: two stacks declaring the same project-level policy is a
    last-writer-wins race, and the loser is whichever application needed the wider boundary.
    This stack derives the STRICTEST location form from its own region, so applying it into a
    shared project narrows gcp.resourceLocations to that region and breaks every sibling that
    reaches another one, and nothing in this stack's plan says so.
  EOT
}

variable "manage_audit_config" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether THIS stack writes the project's data-access audit configuration.

    True by default: data-access logging is what shows who read a data subject's consent, and
    an app deployed on its own project should turn it on rather than rely on being told to.

    Set false where another stack in the same project already owns it.
    `google_project_iam_audit_config` is AUTHORITATIVE for the service it names, so a second
    stack declaring `allServices` does not add to the configuration, it replaces it. Terraform
    shows that as a create, not a change, because this stack holds no prior state for a
    resource that is already live.
  EOT
}

variable "model_armor_full_capabilities" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether the guardrail template asks for the capabilities that are not served in every
    region: the malicious-URI filter and multi-language detection.

    True by default, because a deployment should get the whole guardrail unless it has a reason
    not to. asia-southeast1 serves neither, and Model Armor does not degrade: it refuses the
    template with CAPABILITY_NOT_SUPPORTED, so the stack does not deploy at all. A deployment
    there sets this false, which narrows the guardrail and is a disclosure to make in the
    deployment's posture record rather than a silent downgrade.
  EOT
}

variable "standalone_service_enabled" {
  type        = bool
  default     = false
  description = <<-EOT
    Deploy this stack's OWN Cloud Run service: the internal-only consent endpoint that
    next-best-action calls over the Shared VPC (cloud_run.tf, network.tf).

    False by default, because it is a standing charge that most installations never use. The
    service runs with an instance floor, so it bills by the hour whether or not a request
    arrives, and it needs a Shared VPC, a reviewed audience and a named caller that only the
    next-best-action topology has. An installation embedding this console under a portal runs
    the API as the PORTAL's service instead, and needs none of it. Set true for the
    next-best-action consent hop, and supply the inputs that topology requires.
  EOT
}

variable "standalone_service_min_instances" {
  type        = number
  default     = 1
  description = <<-EOT
    Instance floor for the standalone consent service when standalone_service_enabled = true.
    1 keeps next-best-action's consent check free of a cold start and bills around the clock; 0
    scales to zero and bills only while serving.
  EOT
  validation {
    condition     = var.standalone_service_min_instances >= 0 && var.standalone_service_min_instances <= 4
    error_message = "standalone_service_min_instances must be between 0 and the service's ceiling of 4."
  }
}

variable "additional_serving_service_accounts" {
  type        = list(string)
  default     = []
  description = <<-EOT
    Service-account emails, other than this stack's own runtime identity, that run this
    application's API and therefore need its Firestore stores, its models and its guardrail.

    Exists for embedding hosts. A portal that mounts this console same-origin runs the API under
    a runtime identity of the PORTAL's making, which this stack cannot know and the runtime
    identity's grants do not cover; without this the deployed app authenticates fine and then
    fails on its first consent read. Empty by default, because an app deployed on its own needs
    none. The host grants its own runtime baseline (logs, traces, metrics); this grants only
    what reaches this application's data and models.
  EOT
  validation {
    condition = alltrue([
      for email in var.additional_serving_service_accounts :
      can(regex("^[a-z0-9-]+@[a-z0-9-]+\\.iam\\.gserviceaccount\\.com$", email))
    ])
    error_message = "each additional_serving_service_accounts entry must be a service-account email."
  }
}

variable "org_id" {
  description = "Organization id — required for Org Policy and Access Context Manager."
  type        = string
}

variable "container_image" {
  description = <<-EOT
    Reviewed immutable Artifact Registry image in the deployment region for the standalone
    service. Tags are refused: use REGION-docker.pkg.dev/PROJECT/REPOSITORY/IMAGE@sha256:DIGEST.
    Required only when standalone_service_enabled = true.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = (!var.standalone_service_enabled && var.container_image == "") || can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/[^[:space:]]+@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "container_image must be an immutable Artifact Registry image pinned by sha256 digest."
  }
}

variable "shared_vpc_network" {
  description = "Fully-qualified existing Shared VPC network: projects/HOST_PROJECT/global/networks/NETWORK. Required only when standalone_service_enabled = true."
  type        = string
  default     = ""

  validation {
    condition = (!var.standalone_service_enabled && var.shared_vpc_network == "") || can(regex(
      "^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/global/networks/[a-z][a-z0-9-]{0,61}[a-z0-9]$",
      var.shared_vpc_network,
    ))
    error_message = "shared_vpc_network must be a fully-qualified projects/HOST_PROJECT/global/networks/NETWORK resource name."
  }
}

variable "shared_vpc_subnetwork" {
  description = "Fully-qualified existing Shared VPC subnet. It must be in region, on shared_vpc_network, and have Private Google Access. Required only when standalone_service_enabled = true."
  type        = string
  default     = ""

  validation {
    condition = (!var.standalone_service_enabled && var.shared_vpc_subnetwork == "") || (
      can(regex(
        "^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/regions/${var.region}/subnetworks/[a-z][a-z0-9-]{0,61}[a-z0-9]$",
        var.shared_vpc_subnetwork,
      )) &&
      try(split("/", var.shared_vpc_subnetwork)[1], "") == try(split("/", var.shared_vpc_network)[1], "")
    )
    error_message = "shared_vpc_subnetwork must be fully qualified, in var.region, and owned by the same host project as shared_vpc_network."
  }
}

variable "s2s_audience" {
  description = "Reviewed HTTPS custom audience for Google-signed service ID tokens. next-best-action must mint for this exact value. Required only when standalone_service_enabled = true."
  type        = string
  default     = ""

  validation {
    condition     = (!var.standalone_service_enabled && var.s2s_audience == "") || can(regex("^https://[^[:space:]]+$", var.s2s_audience))
    error_message = "s2s_audience must be a reviewed nonblank HTTPS audience."
  }
}

variable "mkt5_caller_service_account" {
  description = "Exact next-best-action Workload Identity email allowed to invoke the consent service. Required only when standalone_service_enabled = true."
  type        = string
  default     = ""

  validation {
    condition     = (!var.standalone_service_enabled && var.mkt5_caller_service_account == "") || can(regex("^[A-Za-z0-9-]+@[A-Za-z0-9-]+\\.iam\\.gserviceaccount\\.com$", var.mkt5_caller_service_account))
    error_message = "mkt5_caller_service_account must be one service-account email."
  }
}

variable "access_policy_id" {
  description = <<-EOT
    Existing Access Context Manager policy id (numeric, no prefix) for the org.
    Required when enable_vpc_sc = true; the service perimeter is created under it.
    Create once per org with:
      gcloud access-context-manager policies create \
        --organization=ORG_ID --title="apac-residency"
  EOT
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_vpc_sc || can(regex("^[0-9]{6,20}$", var.access_policy_id))
    error_message = "access_policy_id must be numeric when the shared VPC-SC contract is enabled."
  }
}

variable "enable_vpc_sc" {
  description = <<-EOT
    Participate in the shared next-best-action, marketing-compliance-gate VPC Service Controls contract. The designated owner
    creates it in DRY-RUN mode first (vpc_sc.tf, vpc_sc_enforce = false): confirm no legitimate
    path is broken in the dry-run audit logs before enforcing.
  EOT
  type        = bool
  default     = true
}

variable "manage_shared_vpc_sc_perimeter" {
  description = <<-EOT
    Whether this module owns the one regular perimeter shared by next-best-action, marketing-compliance-gate and their Shared
    VPC host project. Exactly one stack may own it. The governance stack is the reference
    owner; set false only after moving/importing the perimeter into another Terraform state.
  EOT
  type        = bool
  default     = true
}

variable "shared_vpc_sc_perimeter_name" {
  description = "Short name of the single regular VPC-SC perimeter shared by next-best-action and marketing-compliance-gate."
  type        = string
  default     = "mkt_marketing_sg"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,49}$", var.shared_vpc_sc_perimeter_name))
    error_message = "shared_vpc_sc_perimeter_name must be a lower-case Access Context Manager short name (max 50 characters)."
  }
}

variable "mkt5_project_number" {
  description = "Numeric project number of the next-best-action service project; included in the shared perimeter. Required when the standalone service runs or this stack owns the perimeter."
  type        = string
  default     = ""

  validation {
    condition     = (!(var.standalone_service_enabled || (var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter)) && var.mkt5_project_number == "") || can(regex("^[0-9]{6,20}$", var.mkt5_project_number))
    error_message = "mkt5_project_number must be a numeric GCP project number, not a project id."
  }
}

variable "mkt6_project_number" {
  description = "Numeric project number of the marketing-compliance-gate service project; included in the shared perimeter. Required when the standalone service runs or this stack owns the perimeter."
  type        = string
  default     = ""

  validation {
    condition = (!(var.standalone_service_enabled || (var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter)) && var.mkt6_project_number == "") || (
      can(regex("^[0-9]{6,20}$", var.mkt6_project_number)) &&
      var.mkt6_project_number != var.mkt5_project_number
    )
    error_message = "mkt6_project_number must be numeric and distinct from mkt5_project_number."
  }
}

variable "shared_vpc_host_project_number" {
  description = "Numeric project number of the Shared VPC host; VPC-SC requires the host in the same regular perimeter. Required when the standalone service runs or this stack owns the perimeter."
  type        = string
  default     = ""

  validation {
    condition = (!(var.standalone_service_enabled || (var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter)) && var.shared_vpc_host_project_number == "") || (
      can(regex("^[0-9]{6,20}$", var.shared_vpc_host_project_number)) &&
      !contains(
        [var.mkt5_project_number, var.mkt6_project_number],
        var.shared_vpc_host_project_number,
      )
    )
    error_message = "shared_vpc_host_project_number must be numeric and distinct from both service-project numbers."
  }
}

variable "vpc_sc_enforce" {
  description = <<-EOT
    Enforce the VPC-SC perimeter. Keep false (DRY-RUN) until the dry-run audit logs are clean
    and the operator/CI identity is in an access level, then flip to true.
  EOT
  type        = bool
  default     = false
}

variable "alert_notification_channels" {
  description = <<-EOT
    Monitoring notification channel ids to attach to the posture alert policies
    (monitoring.tf). An empty list still creates the policies; wire a channel in prod.
  EOT
  type        = list(string)
  default     = []
}

variable "resource_location_values" {
  description = <<-EOT
    Value groups for the gcp.resourceLocations Org Policy. Empty (the default) derives the
    strictest form from the deploy region: that region and its sub-locations, nothing else.

    Widen it ONLY where a service this stack genuinely needs has no presence at single-region
    granularity, and treat the width as the residency claim rather than as plumbing. Two
    services in this catalog force the question:

      * Agent Search serves `global`, `us` and `eu` and NO Cloud region at all.
      * Document AI serves the deploy region only once Google grants single-region access,
        and routes to the `us` multi-region until then.

    Move to the smallest value group that still describes ONE JURISDICTION -- `in:us-locations`
    keeps every resource inside the United States -- and state the residency claim at that
    granularity rather than pretending it is still single-region. NEVER list an individual
    foreign region to unblock one service: that turns a jurisdiction boundary into a list of
    exceptions nobody can reason about.

    NOT YET VERIFIED BY EXECUTION: whether a `global` Agent Search data store is subject to
    this constraint at all, or is exempt as a global resource. Confirm at first apply and
    record the answer rather than guessing; the failure mode if it IS subject is an apply
    error naming discoveryengine, which is the good kind of failure.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for value in var.resource_location_values : startswith(value, "in:") || startswith(value, "is:")])
    error_message = "Each value must be an Org Policy location value group (in:...) or a literal location (is:...)."
  }
}
