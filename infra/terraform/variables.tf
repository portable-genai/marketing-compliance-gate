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
# Mkt6 is generic and APAC: JP -> asia-northeast1, AU -> australia-southeast1,
# SG -> asia-southeast1. The default is Singapore (asia-southeast1) per config/settings.yaml.

variable "project_id" {
  description = "Target GCP project id (required). Single-tenant, APAC-resident."
  type        = string
}

variable "region" {
  description = <<-EOT
    Deployment region. Must be one of Mkt6's in-country residency regions (SPEC §2):
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
    error_message = "Mkt6 is APAC-resident: region must be asia-southeast1 (SG), asia-northeast1 (JP) or australia-southeast1 (AU)."
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
  description = "WORM audit-log retention in days. Default ~7 years. Lock is irreversible."
  type        = number
  default     = 2557 # mirrors config/settings.yaml logging.retention_days

  validation {
    condition     = var.retention_days >= 2557
    error_message = "Compliance retention must be at least 2557 days (~7 years)."
  }
}

variable "org_id" {
  description = "Organization id — required for Org Policy and Access Context Manager."
  type        = string
}

variable "container_image" {
  description = <<-EOT
    Reviewed immutable Artifact Registry image in the deployment region. Tags are refused:
    use REGION-docker.pkg.dev/PROJECT/REPOSITORY/IMAGE@sha256:DIGEST.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+-docker\\.pkg\\.dev/[^[:space:]]+@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "container_image must be an immutable Artifact Registry image pinned by sha256 digest."
  }
}

variable "shared_vpc_network" {
  description = "Fully-qualified existing Shared VPC network: projects/HOST_PROJECT/global/networks/NETWORK."
  type        = string

  validation {
    condition = can(regex(
      "^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/global/networks/[a-z][a-z0-9-]{0,61}[a-z0-9]$",
      var.shared_vpc_network,
    ))
    error_message = "shared_vpc_network must be a fully-qualified projects/HOST_PROJECT/global/networks/NETWORK resource name."
  }
}

variable "shared_vpc_subnetwork" {
  description = "Fully-qualified existing Shared VPC subnet. It must be in region, on shared_vpc_network, and have Private Google Access."
  type        = string

  validation {
    condition = (
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
  description = "Reviewed HTTPS custom audience for Google-signed service ID tokens. Mkt5 must mint for this exact value."
  type        = string

  validation {
    condition     = can(regex("^https://[^[:space:]]+$", var.s2s_audience))
    error_message = "s2s_audience must be a reviewed nonblank HTTPS audience."
  }
}

variable "mkt5_caller_service_account" {
  description = "Exact Mkt5 Workload Identity email allowed to invoke the consent service."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+@[A-Za-z0-9-]+\\.iam\\.gserviceaccount\\.com$", var.mkt5_caller_service_account))
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
    Participate in the shared Mkt5/Mkt6 VPC Service Controls contract. The designated owner
    creates it in DRY-RUN mode first (vpc_sc.tf, vpc_sc_enforce = false): confirm no legitimate
    path is broken in the dry-run audit logs before enforcing.
  EOT
  type        = bool
  default     = true
}

variable "manage_shared_vpc_sc_perimeter" {
  description = <<-EOT
    Whether this module owns the one regular perimeter shared by Mkt5, Mkt6 and their Shared
    VPC host project. Exactly one stack may own it. The governance stack is the reference
    owner; set false only after moving/importing the perimeter into another Terraform state.
  EOT
  type        = bool
  default     = true
}

variable "shared_vpc_sc_perimeter_name" {
  description = "Short name of the single regular VPC-SC perimeter shared by Mkt5 and Mkt6."
  type        = string
  default     = "mkt_marketing_sg"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,49}$", var.shared_vpc_sc_perimeter_name))
    error_message = "shared_vpc_sc_perimeter_name must be a lower-case Access Context Manager short name (max 50 characters)."
  }
}

variable "mkt5_project_number" {
  description = "Numeric project number of the Mkt5 service project; included in the shared perimeter."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.mkt5_project_number))
    error_message = "mkt5_project_number must be a numeric GCP project number, not a project id."
  }
}

variable "mkt6_project_number" {
  description = "Numeric project number of the Mkt6 service project; included in the shared perimeter."
  type        = string

  validation {
    condition = (
      can(regex("^[0-9]{6,20}$", var.mkt6_project_number)) &&
      var.mkt6_project_number != var.mkt5_project_number
    )
    error_message = "mkt6_project_number must be numeric and distinct from mkt5_project_number."
  }
}

variable "shared_vpc_host_project_number" {
  description = "Numeric project number of the Shared VPC host; VPC-SC requires the host in the same regular perimeter."
  type        = string

  validation {
    condition = (
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
