# apis.tf — Enable exactly the managed services Mkt6 depends on.
#
# Control map (SPEC concern):
#   Managed-first / minimal surface: only the services the pinned gcp adapter stack
#     (config/settings.yaml `adapters:`) actually uses are enabled. Nothing speculative.
#   Residency: enabling these APIs is a prerequisite for the regional, CMEK-protected
#     resources defined in the sibling files.
#
# Mapping to config/settings.yaml gcp adapter bindings:
#   rule_provider (file_search_rules) -> aiplatform   (Gemini API File Search over the rule KB)
#   llm           (gemini_llm)        -> aiplatform   (Gemini reasoning / triage)
#   evaluation    (genai_eval)        -> aiplatform   (Vertex Gen AI evaluation)
#   guardrail     (model_armor_*)     -> modelarmor   (regional Model Armor screening)
#   audit         (cloud_logging_*)   -> logging      (WORM locked bucket + audit)
#   tracer        (cloud_trace_*)     -> cloudtrace   (OpenTelemetry spans)
#   agent_registry / tool_catalog     -> HTTP only    (no Google API; platform-internal)
#
# disable_on_destroy = false so a `terraform destroy` of this stack does not yank platform
# APIs out from under other workloads in a shared project.

locals {
  required_services = [
    # --- Mkt6 adapter-backing services (only what the gcp profile uses) ---
    "aiplatform.googleapis.com", # Gemini File Search + reasoning/triage + Gen AI eval
    "modelarmor.googleapis.com", # Model Armor guardrail (regional endpoint)
    "logging.googleapis.com",    # Cloud Logging WORM bucket + audit sink
    "cloudtrace.googleapis.com", # Cloud Trace (OpenTelemetry spans)

    # --- Core deploy + residency-hardening services ---
    "run.googleapis.com",                  # Cloud Run v2 service (the FastAPI container)
    "artifactregistry.googleapis.com",     # image registry (in-region) for the container
    "cloudkms.googleapis.com",             # regional CMEK key ring + key
    "iam.googleapis.com",                  # least-privilege service accounts
    "orgpolicy.googleapis.com",            # Org Policy residency constraints
    "accesscontextmanager.googleapis.com", # VPC Service Controls perimeter
    "monitoring.googleapis.com",           # log-based metrics + posture alert policies
    "compute.googleapis.com",              # networking the perimeter / org policy reference
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
