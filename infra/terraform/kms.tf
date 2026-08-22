# kms.tf — Regional Customer-Managed Encryption Key (CMEK) in the residency region.
#
# Control map (SPEC concern):
#   CMEK does NOT cascade: a CMEK on one resource does not automatically protect data that
#     resource hands to another service. Each managed service that supports CMEK is given its
#     OWN IAM binding below; there is no project-wide grant. We keep ONE regional key ring +
#     crypto key here and wire it into every resource that supports CMEK in its own file
#     (Cloud Run revision, WORM log bucket, Vertex eval state).
#   Residency: the key ring location is var.region (asia-southeast1 by default) — a regional
#     key, never the global / multi-region key. Regional CMEK pins crypto material in-country.

resource "google_kms_key_ring" "mkt_gov" {
  name     = "marketing-compliance-gate"
  location = var.region # regional, in-country key material

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "mkt_gov" {
  name     = "mkt-gov-cmek"
  key_ring = google_kms_key_ring.mkt_gov.id

  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days — periodic rotation for key hygiene

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    # A destroyed key is unrecoverable and would strand all CMEK-encrypted data.
    prevent_destroy = true
  }
}

data "google_project" "this" {
  project_id = var.project_id
}

# --------------------------------------------------------------------------- #
# Per-service IAM bindings. CMEK does not cascade: every service agent that
# encrypts with this key needs its OWN binding here.
# --------------------------------------------------------------------------- #

# Cloud Run service agent (CMEK on the service revision).
resource "google_kms_crypto_key_iam_member" "run" {
  crypto_key_id = google_kms_crypto_key.mkt_gov.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@serverless-robot-prod.iam.gserviceaccount.com"
}

# Cloud Logging service agent (CMEK on the WORM audit bucket).
resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.mkt_gov.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-logging.iam.gserviceaccount.com"
}

# Vertex AI / Gen AI eval service agent (CMEK on evaluation + reasoning state).
resource "google_kms_crypto_key_iam_member" "aiplatform" {
  crypto_key_id = google_kms_crypto_key.mkt_gov.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}
