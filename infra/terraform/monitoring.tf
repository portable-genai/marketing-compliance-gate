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
# empty list still creates the policy, just with nowhere to notify, so wire a channel in prod).
#
# EVERY alert condition restricts resource.type as well as metric.type, because Cloud Monitoring
# REFUSES a threshold condition that names only the metric:
#
#   Error 400: Field alert_policy.conditions[0].condition_threshold.filter had an invalid value
#   of "metric.type=\"logging.googleapis.com/user/mkt_gov_vpc_sc_denials\"": must specify a
#   restriction on "resource.type" in the filter
#
# That is how the first real apply of this stack (2026-09-12) created 21 of its 25 resources and
# then failed all four of these policies, so until that date the posture below was declared here
# and had never existed in any project. No `terraform validate` and no mock-provider plan can see
# it: the API refused the VALUE, not the schema.
#
# Which resource type to name is not free either. A log-based metric's time series carry the
# MONITORED RESOURCE OF THE LOG ENTRIES they counted, and Cloud Monitoring has no equivalent for
# some Cloud Logging resource types, so those are ingested as `global` and the type printed on the
# entry in Logs Explorer is NOT the type to alert on. Naming the wrong one is worse than naming
# none: the API accepts it and the policy then matches no time series for ever. Each signal's
# `resource_types` below is derived from the entries its own filter selects, and said out loud.
# verify: https://cloud.google.com/logging/docs/api/v2/resource-list#resource-mappings
# verify: https://cloud.google.com/monitoring/api/v3/filters
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/logging_metric
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_alert_policy

locals {
  security_metrics = {
    guardrail_blocks = {
      description = "Guardrail BLOCKED decision in the app audit log"
      filter      = "logName=\"projects/${var.project_id}/logs/marketing-compliance-gate-audit\" AND jsonPayload.decision=\"blocked\""
      # The application writes this log itself, through google-cloud-logging, whose Logger infers
      # the monitored resource from the environment it runs in: `cloud_run_revision` inside the
      # deployed Cloud Run service, and `global` when the same gcp-profile audit adapter writes
      # from anywhere that is not a Cloud Run service or job (an operator running the CLI, a seed
      # loader). Both are real writers of a BLOCKED decision, so the alert covers both.
      resource_types = ["cloud_run_revision", "global"]
    }
    sa_key_creation = {
      description = "Service-account key created (org policy should forbid this)"
      filter      = "protoPayload.methodName=\"google.iam.admin.v1.CreateServiceAccountKey\""
      # The IAM admin-activity entry for CreateServiceAccountKey carries `service_account`, which
      # exists in Logging and NOT in Monitoring, so this metric's series arrive as `global` (the
      # project_id label is all that survives that mapping).
      resource_types = ["global"]
    }
    vpc_sc_denials = {
      description = "VPC Service Controls violation"
      filter      = "protoPayload.metadata.@type=\"type.googleapis.com/google.cloud.audit.VpcServiceControlAuditMetadata\""
      # A VPC-SC denial is a policy-denied audit entry on `audited_resource`, and Monitoring
      # defines that type under the same name, so the series keeps it instead of collapsing to
      # `global`. This is the one signal here whose Logs Explorer type is also its alert type.
      resource_types = ["audited_resource"]
    }
    cmek_changes = {
      description = "CMEK key destroy/update operation"
      filter      = "protoPayload.serviceName=\"cloudkms.googleapis.com\" AND (protoPayload.methodName:\"DestroyCryptoKeyVersion\" OR protoPayload.methodName:\"UpdateCryptoKey\")"
      # This signal genuinely spans two Logging resource types: DestroyCryptoKeyVersion is logged
      # on `cloudkms_cryptokeyversion` and UpdateCryptoKey on `cloudkms_cryptokey`. Neither exists
      # in Monitoring and both map to `global`, so one `global` restriction carries the whole
      # signal and narrows nothing, while naming either cloudkms_* type would be accepted by the
      # API and then match nothing at all.
      resource_types = ["global"]
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

  # One condition per resource type the signal can arrive under, rather than one condition whose
  # filter lists several. The API requires each condition_threshold filter to RESTRICT
  # resource.type, and a plain equality is the only form certain to read as a restriction: the
  # Monitoring filter grammar offers `one_of()` for labels but not for resource.type, and an OR of
  # two restrictions is not a restriction on either branch. `combiner = "OR"` already fires the
  # policy on whichever condition has data, so splitting a multi-resource signal this way costs
  # nothing and narrows nothing.
  dynamic "conditions" {
    for_each = each.value.resource_types

    content {
      display_name = "${each.value.description} (${conditions.value})"

      condition_threshold {
        filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.security[each.key].name}\" AND resource.type=\"${conditions.value}\""
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
  }

  notification_channels = var.alert_notification_channels

  documentation {
    content   = "Security signal '${each.key}' fired for the marketing-compliance-gate marketing compliance and governance service. Investigate the matching entries in Cloud Logging and the WORM audit bucket."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.required]
}
