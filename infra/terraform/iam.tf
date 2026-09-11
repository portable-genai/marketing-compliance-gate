# iam.tf — Least-privilege runtime service account for the marketing-compliance-gate Cloud Run service.
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
  display_name = "marketing-compliance-gate Marketing Compliance and Governance — Cloud Run runtime"
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
    # The two tenant-owned Firestore stores (firestore.tf). datastore.user rather than
    # datastore.owner: this service reads and writes documents and never administers a
    # database, an index or a backup.
    "roles/datastore.user", # firestore_consent.py, firestore_evidence.py
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

# --------------------- Embedding host's runtime identity -------------------- #
# A portal that mounts this console same-origin runs the API under a service account of the
# PORTAL's making. That identity is the one the container authenticates as, so without these
# grants the deployed app starts, authenticates, and then fails on its first consent read or
# guardrail call, which reads as a broken application rather than as a missing binding. Empty by
# default: an app deployed on its own needs none of this.
#
# Narrower than the runtime identity above, deliberately. The host already grants every embedded
# identity its runtime baseline (logs, traces, metrics), so none of that is repeated. No CMEK
# key grant: the Firestore stores are Google-managed-key by default (firestore.tf) and, when a
# key is set, Firestore decrypts through its own service agent (kms.tf), never through the caller.
locals {
  additional_serving_project_roles = [
    "roles/aiplatform.user", # Gemini narration and File Search
    "roles/modelarmor.user", # screen through the mkt-gov-guardrail template
    "roles/datastore.user",  # the consent and evidence stores
  ]
}

resource "google_project_iam_member" "additional_serving" {
  for_each = {
    for pair in setproduct(var.additional_serving_service_accounts, local.additional_serving_project_roles) :
    "${pair[0]}|${pair[1]}" => { email = pair[0], role = pair[1] }
  }
  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${each.value.email}"
}
