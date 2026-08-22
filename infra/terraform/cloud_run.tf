# cloud_run.tf — Cloud Run v2 service running the Mkt6 FastAPI container.
#
# Control map (SPEC concern):
#   Residency: the service is pinned to var.region (asia-southeast1 by default); the image
#     lives in an in-region Artifact Registry (var.container_image).
#   Least privilege / no keys: runs as the dedicated runtime identity (iam.tf) via Workload
#     Identity — no exported keys.
#   CMEK: the revision is encrypted with the regional CMEK key (kms.tf).
#   Controlled ingress: fixed INTERNAL_ONLY — no variable can weaken the perimeter at apply.
#   Controlled egress: all traffic uses Direct VPC egress over the reviewed Shared VPC. This
#     is both VPC-SC compliant and reachable from Mkt5 over the same internal network path.
#   Profile opt-in: MKT_GOV_PROFILE=gcp is set EXPLICITLY here (the app defaults to the
#     offline `local` profile when unset; prod must opt in to the managed stack).
#
# The container listens on 8105 (Dockerfile EXPOSE 8105 / uvicorn --port ${PORT}); the probe
# hits /healthz. Env vars drive settings.yaml ${ENV:-default} interpolation, so no code or
# config-file change is needed between environments.
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_service

locals {
  service_name   = "marketing-compliance-gate"
  container_port = 8105
  market_regions = {
    SG = "asia-southeast1"
    JP = "asia-northeast1"
    AU = "australia-southeast1"
  }
}

resource "google_cloud_run_v2_service" "mkt_gov" {
  name     = local.service_name
  location = var.region
  project  = var.project_id

  # A stable reviewed audience avoids a two-apply dependency on the generated service URI.
  # Mkt5 mints a Google-signed ID token for this exact value.
  custom_audiences = [var.s2s_audience]

  # VPC-SC-protected Cloud Run must accept internal sources only. This is intentionally not
  # variable-driven: a permissive value would disable the network side of the boundary.
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    # Encrypt the revision with the regional CMEK key.
    encryption_key                   = google_kms_crypto_key.mkt_gov.id
    service_account                  = google_service_account.runtime.email
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = 1
      max_instance_count = 4
    }

    vpc_access {
      egress = "ALL_TRAFFIC"

      network_interfaces {
        network    = var.shared_vpc_network
        subnetwork = var.shared_vpc_subnetwork
        tags       = ["mkt6-managed-egress"]
      }
    }

    containers {
      image = var.container_image

      ports {
        container_port = local.container_port
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      # Opt in to the managed stack EXPLICITLY (app defaults to offline `local` when unset).
      env {
        name  = "MKT_GOV_PROFILE"
        value = "gcp"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "MKT_MARKET"
        value = var.deploy_market
      }
      env {
        name  = "MKT_GOV_REGION"
        value = var.region
      }
      env {
        name  = "PORT"
        value = tostring(local.container_port)
      }
      env {
        name  = "MKT_GOV_KMS_KEY"
        value = google_kms_crypto_key.mkt_gov.id
      }
      env {
        name  = "MKT6_S2S_AUDIENCE"
        value = var.s2s_audience
      }
      env {
        name  = "MKT6_S2S_ALLOWED_CALLERS"
        value = var.mkt5_caller_service_account
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = local.container_port
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }

      liveness_probe {
        http_get {
          path = "/healthz"
          port = local.container_port
        }
        period_seconds = 30
      }
    }
  }

  depends_on = [
    google_project_iam_member.cloud_run_shared_vpc_viewer,
    google_compute_subnetwork_iam_member.cloud_run_shared_vpc_user,
    google_project_service.required,
    google_kms_crypto_key_iam_member.run,
    google_project_iam_member.runtime,
  ]

  lifecycle {
    precondition {
      condition     = var.region == local.market_regions[var.deploy_market]
      error_message = "region must match deploy_market exactly (SG/asia-southeast1, JP/asia-northeast1, AU/australia-southeast1)."
    }

    precondition {
      condition     = startswith(var.container_image, "${var.region}-docker.pkg.dev/")
      error_message = "container_image must be hosted in the selected residency region."
    }

    precondition {
      condition     = tostring(data.google_project.this.number) == var.mkt6_project_number
      error_message = "mkt6_project_number must be the numeric project number resolved from project_id."
    }

    precondition {
      condition     = tostring(data.google_project.shared_vpc_host.number) == var.shared_vpc_host_project_number
      error_message = "shared_vpc_host_project_number must match the host project encoded in shared_vpc_network."
    }

    precondition {
      condition     = data.google_compute_subnetwork.shared_cloud_run.private_ip_google_access
      error_message = "The Shared VPC subnet must enable Private Google Access for VPC-SC-compliant all-traffic egress."
    }

    precondition {
      condition     = tonumber(split("/", data.google_compute_subnetwork.shared_cloud_run.ip_cidr_range)[1]) <= 26
      error_message = "Direct VPC egress requires the Shared VPC subnet to be /26 or larger."
    }

    precondition {
      condition     = endswith(data.google_compute_subnetwork.shared_cloud_run.network, var.shared_vpc_network)
      error_message = "shared_vpc_subnetwork does not belong to shared_vpc_network."
    }
  }
}

# Cloud Run IAM is the first gate; the FastAPI verifier independently checks the same caller
# email and audience. Both must pass, and both are driven by the same reviewed variables.
resource "google_cloud_run_v2_service_iam_member" "mkt5_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.mkt_gov.location
  name     = google_cloud_run_v2_service.mkt_gov.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.mkt5_caller_service_account}"
}
