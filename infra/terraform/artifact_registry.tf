# artifact_registry.tf: the registry this stack's own images are promoted into.
#
# Control map (SPEC concern):
#   Managed-first: one regional Docker repository for this application, not a per-image decision.
#   No lock-in: Terraform is the only place infrastructure is described, so the registry a
#     deployment pulls from is not a resource that exists because somebody once ran a gcloud
#     command. Every sibling stack in the deployment project owns the repository its images live
#     in; this one owned none, so its API and console images had nowhere to be promoted to and the
#     deployment stopped there. The same gap two siblings closed earlier today, found the same
#     way: by trying to deploy. No check in this repository noticed, because nothing asserted that
#     the path README.md tells an operator to push to is a path this Terraform creates.
#   Residency: regional, pinned to var.region like every other resource here.
#   CMEK explicit: a container image carries the application and its configuration, which is
#     customer material, so the repository is bound to the same key as the rest of the stack.
#     CMEK does not cascade, hence the explicit service-agent grant below.
#
# This stack deploys neither the API nor the console when it is embedded: the portal runs both
# from digest-pinned images. It still owns the repository those digests live in, because the
# repository is where residency, CMEK and tag immutability are decided, and those are this
# stack's concerns rather than the host's.

# The Artifact Registry service agent does not exist until it is asked for, and a CMEK repository
# cannot be created before it holds the key grant. Creating the identity makes the ordering
# explicit rather than a race.
resource "google_project_service_identity" "artifactregistry" {
  provider = google-beta
  project  = var.project_id
  service  = "artifactregistry.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key_iam_member" "artifactregistry" {
  crypto_key_id = google_kms_crypto_key.mkt_gov.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.artifactregistry.email}"
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "marketing-compliance-gate"
  description   = "Promoted marketing-compliance-gate API and console images, CMEK-encrypted."
  format        = "DOCKER"

  kms_key_name = google_kms_crypto_key.mkt_gov.id

  # Immutable tags: a promoted release tag must always name the same bytes. Without this a
  # digest-pinned deployment can still be undermined by the tag that produced it being moved
  # under a reviewer who checked the tag rather than the digest.
  docker_config {
    immutable_tags = true
  }

  # No cleanup policy, deliberately, and the same choice every sibling stack here made. The
  # deployment pins images by digest, in this stack's container_image and in the host's embedded
  # apps entry, so a policy that deleted an untagged or an older version would be the only
  # resource in this stack able to delete a RUNNING deployment's image, and it would do it on a
  # timer rather than in a plan anybody read. Retention here is a deliberate operator action.

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.artifactregistry,
  ]
}

# --------------------------------------------------------------------------- #
# Pull access, repository-scoped.
# --------------------------------------------------------------------------- #
# Every identity that runs one of this application's containers is named here rather than left to
# the project-wide reader that `roles/run.serviceAgent` happens to carry: this stack is applied
# into a SHARED project, where the project-level grant belongs to somebody else and can be
# tightened without this stack noticing. A repository-scoped binding is also the least-privilege
# form of the same access, since it admits this repository and no sibling's.
#
#   runtime    the serving API identity this stack creates (iam.tf), which the standalone
#              Cloud Run service runs as and which pulls var.container_image
#   additional the embedding host's runtime identities, which are what the deployed API and
#              console containers actually authenticate as under the portal
locals {
  # Key by a label known at plan time, never by the email. `for_each` over a set of computed
  # service-account emails is refused outright ("depends on resource attributes that cannot be
  # determined until apply"); the VALUE may be computed freely.
  image_pull_identities = merge(
    {
      runtime = google_service_account.runtime.email
    },
    {
      for email in var.additional_serving_service_accounts :
      "additional:${email}" => email
    },
  )
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each = local.image_pull_identities

  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${each.value}"
}

output "image_registry" {
  description = "Registry path the deployment promotes this application's API and console images into."
  value       = "${google_artifact_registry_repository.images.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
