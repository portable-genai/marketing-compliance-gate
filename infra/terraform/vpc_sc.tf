# vpc_sc.tf — VPC Service Controls perimeter around the AI/data plane (dry-run first).
#
# Control map (SPEC concern):
#   Residency + exfiltration control: one regular perimeter draws a logical boundary around the
#     sovereignty-critical APIs (Vertex/Gemini, Model Armor, Logging, Cloud Trace, KMS). Data
#     cannot be read across the boundary to a non-resident project. It contains next-best-action, marketing-compliance-gate and
#     their Shared VPC host so the consent hop stays inside one production boundary.
#
# DRY-RUN FIRST (the skill's rule): the perimeter is created with its enforced config under
# `spec` and dry_run = true so violations are only LOGGED, not blocked. Watch the dry-run
# audit logs, add your operator/CI identity to an access level, confirm no legitimate path
# breaks, THEN set vpc_sc_enforce = true to move the same config into `status` (enforced).
#
# marketing-compliance-gate owns this perimeter by default. next-best-action declares the same membership but does not create a
# second regular perimeter. Exactly one Terraform state may set manage_shared_vpc_sc_perimeter.
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/access_context_manager_service_perimeter

locals {
  perimeter_restricted_services = [
    "aiplatform.googleapis.com",
    "bigquery.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudtrace.googleapis.com",
    "discoveryengine.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "run.googleapis.com",
  ]

  shared_perimeter_resources = [
    "projects/${var.shared_vpc_host_project_number}",
    "projects/${var.mkt5_project_number}",
    "projects/${var.mkt6_project_number}",
  ]
}

resource "google_access_context_manager_service_perimeter" "mkt_gov" {
  count = var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter ? 1 : 0

  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/${var.shared_vpc_sc_perimeter_name}"
  title  = var.shared_vpc_sc_perimeter_name

  perimeter_type = "PERIMETER_TYPE_REGULAR"

  # use_explicit_dry_run_spec lets the same config sit in dry-run (spec) and be promoted to
  # enforced (status) by flipping vpc_sc_enforce, without re-declaring the perimeter.
  use_explicit_dry_run_spec = !var.vpc_sc_enforce

  # Dry-run config: present (and active) only while NOT enforcing. Violations are logged.
  dynamic "spec" {
    for_each = var.vpc_sc_enforce ? [] : [1]
    content {
      resources           = local.shared_perimeter_resources
      restricted_services = local.perimeter_restricted_services

      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = local.perimeter_restricted_services
      }
    }
  }

  # Enforced config: present (and active) only once vpc_sc_enforce = true.
  dynamic "status" {
    for_each = var.vpc_sc_enforce ? [1] : []
    content {
      resources           = local.shared_perimeter_resources
      restricted_services = local.perimeter_restricted_services

      vpc_accessible_services {
        enable_restriction = true
        allowed_services   = local.perimeter_restricted_services
      }
    }
  }

  depends_on = [google_project_service.required]
}
