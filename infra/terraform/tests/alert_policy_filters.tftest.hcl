# alert_policy_filters.tftest.hcl : every posture alert condition restricts resource.type as well
# as metric.type, read as the PLANNED VALUE Terraform will send rather than as template text.
#
# Why the file exists: the first real apply of this stack created 21 of its 25 resources and then
# failed all four alert policies in monitoring.tf with
#
#   Error 400: ... must specify a restriction on "resource.type" in the filter
#
# so the alerting posture had been declared here, claimed in README.md, and had never existed in
# any project. Neither `terraform validate` nor a schema check can catch that, because the API
# refused the VALUE. A plan CAN: these filters are fully known before anything is created.
#
# The companion guard is tests/unit/test_posture_alerts_are_creatable.py, which needs no terraform
# binary and additionally checks that each restriction names a resource type the matching
# log-based metric can actually produce. This file is the half that reads resolved values.
#
# Mock providers and plan-only, so this runs with NO credentials and NO state, which is what
# `make tf-validate` runs. Every value is fictional.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id = "fictional-marketing-project"
  org_id     = "123456789012"
}

run "every_alert_condition_restricts_resource_type_and_names_a_real_one" {
  command = plan

  variables {
    worm_locked   = false
    enable_vpc_sc = false
  }

  # The count is asserted first and separately: every assertion below walks the conditions, and a
  # walk over an empty list passes while proving nothing. Four signals, five conditions, because
  # the app-written guardrail log arrives under two monitored resources.
  assert {
    condition = (
      length(google_monitoring_alert_policy.security) == 4 &&
      length(flatten([
        for policy in values(google_monitoring_alert_policy.security) : policy.conditions
      ])) == 5
    )
    error_message = "Expected 4 posture alert policies carrying 5 conditions between them."
  }

  assert {
    condition = alltrue(flatten([
      for policy in values(google_monitoring_alert_policy.security) : [
        for condition in policy.conditions :
        can(regex("metric\\.type=\"logging\\.googleapis\\.com/user/mkt_gov_[a-z_]+\"", condition.condition_threshold[0].filter))
      ]
    ]))
    error_message = "Every threshold condition must select this stack's own log-based metric."
  }

  assert {
    condition = alltrue(flatten([
      for policy in values(google_monitoring_alert_policy.security) : [
        for condition in policy.conditions :
        can(regex("resource\\.type=\"[a-z_]+\"", condition.condition_threshold[0].filter))
      ]
    ]))
    error_message = "Cloud Monitoring rejects a threshold filter that does not restrict resource.type: the four policies here were refused on exactly that, with 21 of the stack's 25 resources already created."
  }

  # A restriction naming a Cloud Logging resource type that Monitoring does not define is ACCEPTED
  # by the API and then matches no time series for ever, which is a quieter version of the same
  # defect. These four are the types the entries behind these metrics actually show in Logs
  # Explorer, so they are exactly the wrong answers a reader is most likely to write.
  # verify: https://cloud.google.com/logging/docs/api/v2/resource-list#resource-mappings
  assert {
    condition = alltrue(flatten([
      for policy in values(google_monitoring_alert_policy.security) : [
        for condition in policy.conditions :
        !can(regex("resource\\.type=\"(service_account|cloudkms_cryptokey|cloudkms_cryptokeyversion|cloudkms_keyring)\"", condition.condition_threshold[0].filter))
      ]
    ]))
    error_message = "A Logging-only resource type is ingested as `global`: restricting on it creates a policy that can never fire."
  }

  assert {
    condition = alltrue(flatten([
      for key, policy in google_monitoring_alert_policy.security : [
        for condition in policy.conditions :
        can(regex("resource\\.type=\"global\"", condition.condition_threshold[0].filter))
      ] if key == "sa_key_creation" || key == "cmek_changes"
    ]))
    error_message = "The IAM and Cloud KMS admin-activity signals are ingested under `global`, so that is what their conditions must restrict."
  }

  assert {
    condition = alltrue(flatten([
      for key, policy in google_monitoring_alert_policy.security : [
        for condition in policy.conditions :
        can(regex("resource\\.type=\"audited_resource\"", condition.condition_threshold[0].filter))
      ] if key == "vpc_sc_denials"
    ]))
    error_message = "A VPC Service Controls denial is logged on audited_resource, which Monitoring defines under the same name."
  }

  assert {
    condition = (
      length(google_monitoring_alert_policy.security["guardrail_blocks"].conditions) == 2 &&
      alltrue([
        for condition in google_monitoring_alert_policy.security["guardrail_blocks"].conditions :
        can(regex("resource\\.type=\"(cloud_run_revision|global)\"", condition.condition_threshold[0].filter))
      ])
    )
    error_message = "The app-written guardrail log arrives as cloud_run_revision from the deployed service and as global from anywhere else, and a blocked decision from either must alert."
  }

  assert {
    condition     = google_monitoring_alert_policy.security["guardrail_blocks"].combiner == "OR"
    error_message = "Splitting a signal across conditions only preserves its meaning while the combiner is OR."
  }
}
