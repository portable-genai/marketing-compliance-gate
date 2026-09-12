# shared_project_declines.tftest.hcl : what an embedded, shared-project deployment declines and
# what the next-best-action topology still gets, as plans.
#
# Every run uses mock providers and is plan-only, so the file runs with NO credentials, NO
# project and NO state beyond the provider download:
#
#   terraform init -backend=false && terraform test
#
# which is what `make tf-validate` runs. Nothing here is applied anywhere and every value is
# fictional.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id = "fictional-marketing-project"
  org_id     = "123456789012"
}

run "an_embedded_install_in_a_shared_project_creates_only_what_the_console_reads" {
  command = plan

  variables {
    worm_locked                         = false
    retention_days                      = 30
    manage_org_policies                 = false
    manage_audit_config                 = false
    enable_vpc_sc                       = false
    model_armor_full_capabilities       = false
    additional_serving_service_accounts = ["journey-a-fictional@fictional-marketing-project.iam.gserviceaccount.com"]
  }

  assert {
    condition = (
      length(google_cloud_run_v2_service.mkt_gov) +
      length(google_cloud_run_v2_service_iam_member.mkt5_invoker) +
      length(google_project_iam_member.cloud_run_shared_vpc_viewer) +
      length(google_compute_subnetwork_iam_member.cloud_run_shared_vpc_user) +
      length(google_kms_crypto_key_iam_member.run)
    ) == 0
    error_message = "The standalone service is a standing hourly charge; with no opt-in it and everything it needs must be absent."
  }

  assert {
    condition     = output.service_url == null
    error_message = "With no standalone service there is no service URL to hand next-best-action."
  }

  assert {
    condition = (
      length(google_org_policy_policy.resource_locations) +
      length(google_org_policy_policy.disable_sa_keys) +
      length(google_org_policy_policy.no_external_ip) +
      length(google_org_policy_policy.uniform_bucket_access)
    ) == 0
    error_message = "A stack that does not own the project's Org Policies must write none of them."
  }

  assert {
    condition     = length(google_project_iam_audit_config.data_access) == 0 && length(google_access_context_manager_service_perimeter.mkt_gov) == 0
    error_message = "The authoritative audit config and a second regular perimeter must both be declinable."
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.locked == false
    error_message = "worm_locked = false must leave the audit bucket unlocked and destroyable."
  }

  assert {
    condition = (
      length(google_model_armor_template.mkt_gov_guardrail.filter_config[0].malicious_uri_filter_settings) +
      length(google_model_armor_template.mkt_gov_guardrail.template_metadata[0].multi_language_detection)
    ) == 0
    error_message = "A region that serves neither capability must be able to decline both, or the template is refused."
  }

  assert {
    condition     = google_model_armor_template.mkt_gov_guardrail.template_id == "mkt-gov-guardrail" && google_model_armor_template.mkt_gov_guardrail.location == var.region
    error_message = "The guardrail adapter screens through mkt-gov-guardrail in the deployment region; that template must be what this stack creates."
  }

  assert {
    condition     = google_firestore_database.store["asia-southeast1"].name == "mkt6-asia-southeast1" && google_firestore_database.store["asia-southeast1"].location_id == var.region
    error_message = "The consent store the adapter routes to must be the regional mkt6-<region> database."
  }

  assert {
    condition     = length(google_firestore_index.composite) == 5 && length(google_firestore_database.store["asia-southeast1"].cmek_config) == 0
    error_message = "The five composite indexes must stay, and Firestore CMEK must stay off until the project is admitted."
  }

  assert {
    condition     = contains([for member in values(google_project_iam_member.additional_serving) : member.role], "roles/datastore.user")
    error_message = "The embedding host's identity must be able to read the consent store."
  }
}

run "the_next_best_action_topology_still_plans_whole" {
  command = plan

  variables {
    worm_locked                    = true
    standalone_service_enabled     = true
    container_image                = "asia-southeast1-docker.pkg.dev/fictional-marketing-project/marketing-compliance-gate/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    shared_vpc_network             = "projects/fictional-network-host/global/networks/mkt-prod"
    shared_vpc_subnetwork          = "projects/fictional-network-host/regions/asia-southeast1/subnetworks/cloud-run-mkt"
    s2s_audience                   = "https://mkt6-consent.internal.example"
    mkt5_caller_service_account    = "mkt-nba-run@fictional-nba-project.iam.gserviceaccount.com"
    access_policy_id               = "987654321098"
    mkt5_project_number            = "111111111111"
    mkt6_project_number            = "222222222222"
    shared_vpc_host_project_number = "333333333333"
  }

  override_data {
    target = data.google_project.this
    values = { number = "222222222222" }
  }

  override_data {
    target = data.google_project.shared_vpc_host
    values = { number = "333333333333" }
  }

  override_data {
    target = data.google_compute_subnetwork.shared_cloud_run
    values = {
      private_ip_google_access = true
      ip_cidr_range            = "10.10.0.0/26"
      network                  = "https://www.googleapis.com/compute/v1/projects/fictional-network-host/global/networks/mkt-prod"
    }
  }

  assert {
    condition     = length(google_cloud_run_v2_service.mkt_gov) == 1 && google_cloud_run_v2_service.mkt_gov[0].ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    error_message = "Opting in must create the internal-only consent service."
  }

  assert {
    condition     = google_cloud_run_v2_service.mkt_gov[0].template[0].scaling[0].min_instance_count == 1
    error_message = "The standalone service keeps its instance floor of 1 unless the deployment lowers it."
  }

  assert {
    condition = (
      length(google_cloud_run_v2_service_iam_member.mkt5_invoker) +
      length(google_project_iam_member.cloud_run_shared_vpc_viewer) +
      length(google_compute_subnetwork_iam_member.cloud_run_shared_vpc_user) +
      length(google_kms_crypto_key_iam_member.run)
    ) == 4
    error_message = "Opting in must bring the invoker, both Shared VPC grants and the revision's CMEK binding with it."
  }

  assert {
    condition     = length(google_access_context_manager_service_perimeter.mkt_gov) == 1 && length(google_project_iam_audit_config.data_access) == 1
    error_message = "With no override, the shared perimeter and the audit config are created."
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.locked
    error_message = "A deployment that names worm_locked = true must get the locked bucket."
  }
}

run "the_standalone_service_refuses_to_plan_without_its_image" {
  command = plan

  variables {
    worm_locked                    = false
    standalone_service_enabled     = true
    enable_vpc_sc                  = false
    shared_vpc_network             = "projects/fictional-network-host/global/networks/mkt-prod"
    shared_vpc_subnetwork          = "projects/fictional-network-host/regions/asia-southeast1/subnetworks/cloud-run-mkt"
    s2s_audience                   = "https://mkt6-consent.internal.example"
    mkt5_caller_service_account    = "mkt-nba-run@fictional-nba-project.iam.gserviceaccount.com"
    mkt5_project_number            = "111111111111"
    mkt6_project_number            = "222222222222"
    shared_vpc_host_project_number = "333333333333"
  }

  override_data {
    target = data.google_project.this
    values = { number = "222222222222" }
  }

  override_data {
    target = data.google_project.shared_vpc_host
    values = { number = "333333333333" }
  }

  override_data {
    target = data.google_compute_subnetwork.shared_cloud_run
    values = {
      private_ip_google_access = true
      ip_cidr_range            = "10.10.0.0/26"
      network                  = "https://www.googleapis.com/compute/v1/projects/fictional-network-host/global/networks/mkt-prod"
    }
  }

  expect_failures = [var.container_image]
}

# Terraform reports the first failing variable and stops, so each number gets its own run with
# the other two supplied: one run naming all three would prove only the first.
run "owning_the_perimeter_still_requires_mkt5_project_number" {
  command = plan

  variables {
    worm_locked                    = false
    access_policy_id               = "987654321098"
    mkt6_project_number            = "222222222222"
    shared_vpc_host_project_number = "333333333333"
  }

  expect_failures = [var.mkt5_project_number]
}

run "owning_the_perimeter_still_requires_mkt6_project_number" {
  command = plan

  variables {
    worm_locked                    = false
    access_policy_id               = "987654321098"
    mkt5_project_number            = "111111111111"
    shared_vpc_host_project_number = "333333333333"
  }

  expect_failures = [var.mkt6_project_number]
}

run "owning_the_perimeter_still_requires_shared_vpc_host_project_number" {
  command = plan

  variables {
    worm_locked         = false
    access_policy_id    = "987654321098"
    mkt5_project_number = "111111111111"
    mkt6_project_number = "222222222222"
  }

  expect_failures = [var.shared_vpc_host_project_number]
}

run "a_locked_bucket_refuses_a_short_window" {
  command = plan

  variables {
    worm_locked    = true
    retention_days = 30
    enable_vpc_sc  = false
  }

  expect_failures = [var.retention_days]
}
