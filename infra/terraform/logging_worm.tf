# logging_worm.tf — WORM audit trail: locked Cloud Logging bucket + sink + audit config.
#
# Control map (SPEC concern):
#   Immutable audit / WORM: the audit log is routed to a Cloud Logging bucket whose retention
#     is var.retention_days (~7 years) and which is Write-Once-Read-Many when
#     var.worm_locked = true. The variable has no default: see variables.tf.
#     The gcp audit adapter (cloud_logging_audit) writes AuditEvents here; the app redacts
#     before it logs, the infra guarantees the log cannot be altered or deleted.
#   Residency: bucket location is var.region (in-country).
#   CMEK explicit: the bucket is CMEK-encrypted (logging SA key binding in kms.tf).
#
# ############################################################################ #
# # WARNING — LOCKING IS IRREVERSIBLE.                                        # #
# # Setting `locked = true` permanently prevents reducing retention or        # #
# # deleting this bucket for the full retention window. You CANNOT undo it,   # #
# # not even with project-owner rights. Confirm retention_days before apply.  # #
# # To trial without locking, set worm_locked = false (NOT WORM, not prod).   # #
# ############################################################################ #

resource "google_logging_project_bucket_config" "worm_audit" {
  project        = var.project_id
  location       = var.region                       # in-country residency
  bucket_id      = "marketing-compliance-gate-worm" # matches settings.yaml logging.bucket
  description    = "Audit bucket for marketing-compliance-gate marketing compliance (WORM when worm_locked = true)."
  retention_days = var.retention_days # 2557 (~7 years) by default

  # IRREVERSIBLE when true: see the WARNING banner above. WORM compliance requires true, and the
  # variable has no default, so no plan can lock this bucket without the deployment saying so.
  locked = var.worm_locked

  # CMEK on the log bucket — explicit, does not cascade.
  cmek_settings {
    kms_key_name = google_kms_crypto_key.mkt_gov.id
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.logging,
  ]
}

# Route the audit log stream into the locked WORM bucket.
resource "google_logging_project_sink" "audit_to_worm" {
  project     = var.project_id
  name        = "mkt-gov-audit-to-worm"
  description = "Routes the marketing-compliance-gate-audit log to the locked WORM bucket."

  destination = "logging.googleapis.com/${google_logging_project_bucket_config.worm_audit.id}"

  # Capture this app's audit log + all Cloud Audit Logs (admin/data access).
  filter = <<-EOT
    logName="projects/${var.project_id}/logs/marketing-compliance-gate-audit"
    OR logName:"cloudaudit.googleapis.com"
  EOT

  unique_writer_identity = true
}

# --------------------------------------------------------------------------- #
# Enable Data Access audit logs so every read of the rule KB / reviewed assets
# and the audit store itself is itself audited.
# --------------------------------------------------------------------------- #
#
# Authoritative for the service it names, which is the point and also the hazard: it REPLACES the
# project's audit config for `allServices` rather than adding to it. Declined by
# var.manage_audit_config where another stack in the project owns that configuration.
resource "google_project_iam_audit_config" "data_access" {
  count   = var.manage_audit_config ? 1 : 0
  project = var.project_id
  service = "allServices"

  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
  audit_log_config {
    log_type = "ADMIN_READ"
  }
}
