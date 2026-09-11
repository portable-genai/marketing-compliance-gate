# providers.tf: provider pinning and the state backend for the marketing-compliance-gate Marketing
# Compliance and Governance deploy.
#
# Control map (marketing-compliance-gate has no numbered COMPLIANCE.md; controls are referenced by SPEC concern):
#   Data residency (SPEC §2): every provider call is pinned to a Singapore region,
#     asia-southeast1 by default. There is no global / multi-region default.
#   No lock-in (hexagon): Terraform is the only place infra is described; the app talks to
#     ports (SPEC §3), never to these resources directly.
#
# google-beta is declared because some Org Policy v2 / Access Context Manager surfaces are
# only available on the beta provider line as of the pinned version.

terraform {
  required_version = ">= 1.9.0"

  # Partial backend: the bucket and this stack's prefix (marketing-compliance-gate) are supplied
  # at init, which keeps the module reusable while making accidental local state impossible in a
  # named deployment. Local state for a stack that owns a KMS key and the Firestore stores is
  # state that exists on exactly one laptop. The offline paths (make tf-validate, terraform test,
  # the CI runner) initialise with -backend=false and never read it.
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0" # 6.x GA line (mid-2026)
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }
}

# Primary (GA) provider: every resource defaults to the pinned Singapore region.
provider "google" {
  project = var.project_id
  region  = var.region # asia-southeast1 (Singapore), pinned, never global
}

# Beta provider: same project/region, used only where a resource needs the beta surface.
provider "google-beta" {
  project = var.project_id
  region  = var.region
}
