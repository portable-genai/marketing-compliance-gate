# org_policy.tf — Org Policy constraints enforcing residency and no exported keys.
#
# Control map (SPEC concern):
#   Data residency, defence in depth: even if someone hand-edits a resource, the
#     gcp.resourceLocations policy REJECTS creation of resources outside the residency region.
#   No service-account keys: exportable SA keys are disabled; Cloud Run uses Workload Identity
#     (iam.tf). Keys cannot leave the perimeter because they cannot be created.
#   Private data plane: external VM IPs are denied and uniform bucket access is enforced so
#     data and compute stay in-country and private.
#
# Scoped to the project. To enforce org-wide, move these to parent =
# "organizations/${var.org_id}".
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/org_policy_policy

locals {
  # Residency allowlist member for gcp.resourceLocations, derived from the pinned region so
  # the location policy always tracks the single source of truth (var.region).
  resource_location_group = {
    "asia-southeast1"      = "in:asia-southeast1-locations"
    "asia-northeast1"      = "in:asia-northeast1-locations"
    "australia-southeast1" = "in:australia-southeast1-locations"
  }
}

# Master residency policy: only allow the deploy region's location group.
resource "google_org_policy_policy" "resource_locations" {
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        allowed_values = [local.resource_location_group[var.region]]
      }
    }
  }

  depends_on = [google_project_service.required]
}

# Disable creation of exportable service-account keys (use Workload Identity instead).
resource "google_org_policy_policy" "disable_sa_keys" {
  name   = "projects/${var.project_id}/policies/iam.disableServiceAccountKeyCreation"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Disable VM external IPs — keep the data plane private.
resource "google_org_policy_policy" "no_external_ip" {
  name   = "projects/${var.project_id}/policies/compute.vmExternalIpAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      deny_all = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Require uniform bucket-level access (no per-object ACL exfiltration paths).
resource "google_org_policy_policy" "uniform_bucket_access" {
  name   = "projects/${var.project_id}/policies/storage.uniformBucketLevelAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}
