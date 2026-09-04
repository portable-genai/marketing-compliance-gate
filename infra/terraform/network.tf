# network.tf — Existing Shared VPC contract for managed Cloud Run egress.
#
# The horizontal network stack owns the network and service-project associations. This module
# verifies the selected subnet and grants only the marketing-compliance-gate Cloud Run service agent permission to
# discover the host network and consume that subnet. Keeping the association in the network
# owner's state avoids two Terraform states competing for the Shared VPC lifecycle.

locals {
  shared_vpc_host_project_id = split("/", var.shared_vpc_network)[1]
  shared_vpc_subnet_name     = split("/", var.shared_vpc_subnetwork)[5]
}

# Materialise the managed service identity before host-project IAM references it. This avoids
# a first-deploy race in a new service project where run.googleapis.com is enabled but its
# service agent has not yet been created.
resource "google_project_service_identity" "run" {
  provider = google-beta
  project  = var.project_id
  service  = "run.googleapis.com"

  depends_on = [google_project_service.required]
}

data "google_compute_subnetwork" "shared_cloud_run" {
  project = local.shared_vpc_host_project_id
  region  = var.region
  name    = local.shared_vpc_subnet_name
}

data "google_project" "shared_vpc_host" {
  project_id = local.shared_vpc_host_project_id
}

# Shared VPC least privilege: view the host network, consume only the selected subnet.
resource "google_project_iam_member" "cloud_run_shared_vpc_viewer" {
  project = local.shared_vpc_host_project_id
  role    = "roles/compute.networkViewer"
  member  = "serviceAccount:${google_project_service_identity.run.email}"
}

resource "google_compute_subnetwork_iam_member" "cloud_run_shared_vpc_user" {
  project    = local.shared_vpc_host_project_id
  region     = var.region
  subnetwork = local.shared_vpc_subnet_name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_project_service_identity.run.email}"
}
