# monitoring.tf — Security alerting: log-based metrics + alert policies.
#
# Control map (SPEC concern):
#   Detect, not just record: DATA_READ logging (logging_worm.tf) records reads, but recording
#     is not detection. These log-based metrics + alert policies SURFACE security-relevant
#     events so an operator is notified, rather than the signal sitting unread in the WORM
#     bucket.
#
# Signals covered:
#   - guardrail_blocks : a guardrail BLOCKED decision in the app audit log (Model Armor screen).
#   - sa_key_creation  : an exportable SA key was created (org policy forbids it; defence depth).
#   - vpc_sc_denials   : a VPC Service Controls violation (perimeter working / being probed).
#   - cmek_changes     : a CMEK key destroy/update (key-material change).
#
# Alert policies are always created; var.alert_notification_channels attaches channels (an
# empty list still creates the policy, just with nowhere to notify — wire a channel in prod).
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/logging_metric
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_alert_policy

locals {
  security_metrics = {
    guardrail_blocks = {
      description = "Guardrail BLOCKED decision in the app audit log"
      filter      = "logName=\"projects/${var.project_id}/logs/marketing-compliance-gate-audit\" AND jsonPayload.decision=\"blocked\""
    }
    sa_key_creation = {
      description = "Service-account key created (org policy should forbid this)"
      filter      = "protoPayload.methodName=\"google.iam.admin.v1.CreateServiceAccountKey\""
    }
    vpc_sc_denials = {
      description = "VPC Service Controls violation"
      filter      = "protoPayload.metadata.@type=\"type.googleapis.com/google.cloud.audit.VpcServiceControlAuditMetadata\""
    }
    cmek_changes = {
      description = "CMEK key destroy/update operation"
      filter      = "protoPayload.serviceName=\"cloudkms.googleapis.com\" AND (protoPayload.methodName:\"DestroyCryptoKeyVersion\" OR protoPayload.methodName:\"UpdateCryptoKey\")"
    }
  }
}

resource "google_logging_metric" "security" {
  for_each = local.security_metrics

  project     = var.project_id
  name        = "mkt_gov_${each.key}"
  description = each.value.description
  filter      = each.value.filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "security" {
  for_each = local.security_metrics

  project      = var.project_id
  display_name = "marketing-compliance-gate security: ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = each.value.description

    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.security[each.key].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = var.alert_notification_channels

  documentation {
    content   = "Security signal '${each.key}' fired for the marketing-compliance-gate marketing compliance and governance service. Investigate the matching entries in Cloud Logging and the WORM audit bucket."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.required]
}
