# iam.tf — Least-privilege runtime service account for the Mkt6 Cloud Run service.
#
# Control map (SPEC concern):
#   Least privilege: ONE dedicated runtime identity for the serving / API container, granted
#     only the roles it needs (call the reasoning model + File Search + eval, screen with
#     Model Armor, write audit + traces). No broad / project-owner roles, no shared SA.
#   No keys: the identity is used via Workload Identity by Cloud Run; org_policy.tf forbids
#     exportable SA keys, so this account can never have a key minted for it.
#   CMEK explicit: the runtime gets its own cryptoKey use binding for envelope ops it performs.

resource "google_service_account" "runtime" {
  account_id   = "mkt-gov-run"
  display_name = "Mkt6 Marketing Compliance and Governance — Cloud Run runtime"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Serving path: call Gemini (reasoning/triage), File Search and Gen AI eval; screen with
  # Model Armor; write audit events to the WORM sink; emit OpenTelemetry spans.
  runtime_roles = [
    "roles/aiplatform.user",         # Gemini reasoning + File Search rule KB + Gen AI eval
    "roles/modelarmor.user",         # Model Armor guardrail screening
    "roles/logging.logWriter",       # write audit events to the WORM sink
    "roles/cloudtrace.agent",        # OpenTelemetry spans (content OFF)
    "roles/monitoring.metricWriter", # emit its own metrics
  ]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset(local.runtime_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

# The runtime uses the CMEK for envelope ops it performs directly.
resource "google_kms_crypto_key_iam_member" "runtime" {
  crypto_key_id = google_kms_crypto_key.mkt_gov.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.runtime.email}"
}
