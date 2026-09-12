# image_registry.tftest.hcl : the repository this stack's two images are promoted into.
#
# Mock providers and plan-only, so this runs with NO credentials and NO state:
# `terraform init -backend=false && terraform test`, which is what `make tf-validate` runs.
#
# Why the file exists at all: this stack was applied into a shared project with no registry of its
# own, so the API and the console image had nowhere to be promoted to and the deployment stopped
# there. The gap was invisible to every check in the repository, because nothing asserted that the
# path README.md tells an operator to push to is a path this Terraform creates.
#
# Every assertion reads a value Terraform knows at PLAN time, never a computed id: a mock provider
# cannot resolve those, and an assertion over an unknown proves nothing.

mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id = "fictional-marketing-project"
  org_id     = "123456789012"
}

run "the_registry_is_regional_docker_cmek_and_immutably_tagged" {
  command = plan

  variables {
    worm_locked   = false
    enable_vpc_sc = false
  }

  # The key's `id` is computed, so under a mock provider a plan does not know it and an assertion
  # reading it is refused rather than failed. Naming the value for the plan phase is what makes
  # the CMEK claim checkable at all here: compared against a literal instead, the assertion would
  # still pass with the repository bound to some OTHER stack's key.
  override_resource {
    target          = google_kms_crypto_key.mkt_gov
    override_during = plan
    values = {
      id = "projects/fictional-marketing-project/locations/asia-southeast1/keyRings/marketing-compliance-gate/cryptoKeys/mkt-gov-cmek"
    }
  }

  assert {
    condition = (
      google_artifact_registry_repository.images.repository_id == "marketing-compliance-gate" &&
      google_artifact_registry_repository.images.location == var.region &&
      google_artifact_registry_repository.images.format == "DOCKER"
    )
    error_message = "The repository must be the in-region Docker repository named marketing-compliance-gate."
  }

  assert {
    condition     = google_artifact_registry_repository.images.kms_key_name == google_kms_crypto_key.mkt_gov.id
    error_message = "An image carries the application and its configuration, so the repository uses this stack's own key."
  }

  assert {
    condition     = google_artifact_registry_repository.images.docker_config[0].immutable_tags == true
    error_message = "Tags must be immutable: a moved tag changes what a reviewer approved with no diff anywhere."
  }

  # CMEK does not cascade, and Artifact Registry encrypts as its own service agent rather than as
  # the caller, so without this binding the repository cannot be created at all.
  assert {
    condition = (
      google_kms_crypto_key_iam_member.artifactregistry.crypto_key_id == google_kms_crypto_key.mkt_gov.id &&
      google_kms_crypto_key_iam_member.artifactregistry.role == "roles/cloudkms.cryptoKeyEncrypterDecrypter"
    )
    error_message = "The Artifact Registry service agent must hold the key it is asked to encrypt with."
  }

  assert {
    condition     = contains(keys(google_project_service.required), "artifactregistry.googleapis.com")
    error_message = "A repository cannot be created in a project where the API was never enabled."
  }

  # Nothing in this stack may delete a running deployment's image on a timer.
  assert {
    condition     = length(google_artifact_registry_repository.images.cleanup_policies) == 0
    error_message = "No cleanup policy: the deployment pins images by digest, so deletion is an operator action, never a default."
  }
}

run "every_identity_that_runs_a_container_can_pull_it_and_no_one_else_is_named" {
  command = plan

  variables {
    worm_locked                         = false
    enable_vpc_sc                       = false
    additional_serving_service_accounts = ["journey-a-mkt-gov@fictional-marketing-project.iam.gserviceaccount.com"]
  }

  # Same reason as above: `name` is computed, so the scoping assertion below needs the repository
  # the bindings point at to be a value the plan knows.
  override_resource {
    target          = google_artifact_registry_repository.images
    override_during = plan
    values = {
      name = "marketing-compliance-gate"
    }
  }

  assert {
    condition = (
      length(google_artifact_registry_repository_iam_member.readers) == 2 &&
      contains(keys(google_artifact_registry_repository_iam_member.readers), "runtime") &&
      contains(
        keys(google_artifact_registry_repository_iam_member.readers),
        "additional:journey-a-mkt-gov@fictional-marketing-project.iam.gserviceaccount.com"
      )
    )
    error_message = "The serving identity and every embedding host identity must hold pull access, and nothing else."
  }

  assert {
    condition = alltrue([
      for reader in values(google_artifact_registry_repository_iam_member.readers) :
      reader.role == "roles/artifactregistry.reader" &&
      reader.repository == google_artifact_registry_repository.images.name
    ])
    error_message = "Pull access is reader, scoped to THIS repository: a project-level grant admits every sibling's images too."
  }
}

run "an_app_deployed_on_its_own_grants_no_host_identity_anything" {
  command = plan

  variables {
    worm_locked   = false
    enable_vpc_sc = false
  }

  assert {
    condition = (
      length(google_artifact_registry_repository_iam_member.readers) == 1 &&
      length([
        for key in keys(google_artifact_registry_repository_iam_member.readers) :
        key if startswith(key, "additional:")
      ]) == 0
    )
    error_message = "With no embedding host, only this stack's own serving identity may be named."
  }
}

run "the_container_image_the_validation_accepts_is_a_digest_in_this_registry" {
  command = plan

  variables {
    worm_locked                    = false
    enable_vpc_sc                  = false
    standalone_service_enabled     = true
    container_image                = "asia-southeast1-docker.pkg.dev/fictional-marketing-project/marketing-compliance-gate/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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

  # Reads the image the service was actually given back against the registry path this stack
  # builds, rather than against a literal. A renamed repository_id, a different region or a
  # project the registry does not live in all break this, where a regex on the digest suffix
  # alone would not.
  assert {
    condition = (
      google_cloud_run_v2_service.mkt_gov[0].template[0].containers[0].image ==
      "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    error_message = "container_image must accept, and the service must run, a digest under this stack's own registry."
  }
}
