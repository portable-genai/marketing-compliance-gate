# network.tf — Existing Shared VPC contract for managed Cloud Run egress.
#
# The horizontal network stack owns the network and service-project associations. This module
# verifies the selected subnet and grants only the marketing-compliance-gate Cloud Run service agent permission to
# discover the host network and consume that subnet. Keeping the association in the network
# owner's state avoids two Terraform states competing for the Shared VPC lifecycle.
#
# Everything here exists only for the standalone service (var.standalone_service_enabled). An
# embedded installation reaches no Shared VPC and grants nothing on a host project.

locals {
  shared_vpc_host_project_id = try(split("/", var.shared_vpc_network)[1], "")
  shared_vpc_subnet_name     = try(split("/", var.shared_vpc_subnetwork)[5], "")
}

# Materialise the managed service identity before host-project IAM references it. This avoids
# a first-deploy race in a new service project where run.googleapis.com is enabled but its
# service agent has not yet been created.
resource "google_project_service_identity" "run" {
  count    = var.standalone_service_enabled ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = "run.googleapis.com"

  depends_on = [google_project_service.required]
}

data "google_compute_subnetwork" "shared_cloud_run" {
  count   = var.standalone_service_enabled ? 1 : 0
  project = local.shared_vpc_host_project_id
  region  = var.region
  name    = local.shared_vpc_subnet_name
}

data "google_project" "shared_vpc_host" {
  count      = var.standalone_service_enabled ? 1 : 0
  project_id = local.shared_vpc_host_project_id
}

# Shared VPC least privilege: view the host network, consume only the selected subnet.
resource "google_project_iam_member" "cloud_run_shared_vpc_viewer" {
  count   = var.standalone_service_enabled ? 1 : 0
  project = local.shared_vpc_host_project_id
  role    = "roles/compute.networkViewer"
  member  = "serviceAccount:${google_project_service_identity.run[0].email}"
}

resource "google_compute_subnetwork_iam_member" "cloud_run_shared_vpc_user" {
  count      = var.standalone_service_enabled ? 1 : 0
  project    = local.shared_vpc_host_project_id
  region     = var.region
  subnetwork = local.shared_vpc_subnet_name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_project_service_identity.run[0].email}"
}
