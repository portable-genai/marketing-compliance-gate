# outputs.tf — Values operators need to wire settings.yaml / the environment after apply.

output "project_id" {
  description = "The deployment project id."
  value       = var.project_id
}

output "region" {
  description = "Pinned residency region (asia-southeast1 by default)."
  value       = var.region
}

# --------------------------------- Cloud Run -------------------------------- #
output "service_url" {
  description = "Base URL of the marketing-compliance-gate Cloud Run service."
  value       = google_cloud_run_v2_service.mkt_gov.uri
}

output "service_name" {
  description = "Cloud Run service name."
  value       = google_cloud_run_v2_service.mkt_gov.name
}

output "s2s_audience" {
  description = "Custom OIDC audience managed callers must mint for."
  value       = var.s2s_audience
}

output "mkt5_caller_service_account" {
  description = "Exact next-best-action Workload Identity granted invocation and app-level access."
  value       = var.mkt5_caller_service_account
}

output "agent_card_url" {
  description = "A2A AgentCard discovery URL for the service."
  value       = "${google_cloud_run_v2_service.mkt_gov.uri}/.well-known/agent-card.json"
}

# ----------------------------- Service account ------------------------------ #
output "runtime_service_account" {
  description = "Least-privilege runtime identity (Workload Identity) used by Cloud Run."
  value       = google_service_account.runtime.email
}

# --------------------------------- KMS -------------------------------------- #
output "kms_key" {
  description = "Regional CMEK crypto key id (MKT_GOV_KMS_KEY)."
  value       = google_kms_crypto_key.mkt_gov.id
}

# ------------------------------- WORM logging ------------------------------- #
output "log_bucket" {
  description = "Locked WORM audit log bucket id (settings.yaml logging.bucket)."
  value       = google_logging_project_bucket_config.worm_audit.id
}

output "audit_sink_writer_identity" {
  description = "Sink writer identity (grant it bucket access if cross-project)."
  value       = google_logging_project_sink.audit_to_worm.writer_identity
}

# --------------------------------- VPC-SC ----------------------------------- #
output "vpc_sc_perimeter" {
  description = "Expected shared perimeter name (empty when enable_vpc_sc = false), whether this stack owns it or consumes it."
  value       = var.enable_vpc_sc ? "accessPolicies/${var.access_policy_id}/servicePerimeters/${var.shared_vpc_sc_perimeter_name}" : ""
}

output "vpc_sc_enforced" {
  description = "Whether the perimeter is enforced (true) or dry-run only (false)."
  value       = var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter && var.vpc_sc_enforce
}

output "manages_vpc_sc_perimeter" {
  description = "True only for the Terraform state that owns the shared regular perimeter."
  value       = var.enable_vpc_sc && var.manage_shared_vpc_sc_perimeter
}

output "shared_vpc_network" {
  description = "Existing Shared VPC network used for all Cloud Run egress."
  value       = var.shared_vpc_network
}

output "shared_vpc_subnetwork" {
  description = "Existing region-local Shared VPC subnet used for Direct VPC egress."
  value       = var.shared_vpc_subnetwork
}
