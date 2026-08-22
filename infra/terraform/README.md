# Mkt6 Marketing Compliance and Governance: Terraform (APAC-resident, sovereign deploy)

This module provisions the managed stack for the Mkt6 marketing compliance and governance
service and deploys its FastAPI container (the repo `Dockerfile`) on Cloud Run v2.

Region is **pinned to an APAC residency region** for every resource. The default is
**`asia-southeast1` (Singapore)**; `asia-northeast1` (JP) and `australia-southeast1` (AU) are
the only other accepted values, validated at `terraform plan` time and mirrored in the app
(`config.market_profile` / `resolve_region`) so an off-region deploy fails fast in both
places. `deploy_market` and `region` must be the exact SG/JP/AU pair, and Terraform injects
both into the runtime. The container must be an immutable digest in that same region; a
tagged or cross-region image is refused before deployment.

## What it provisions

| Concern | Resource(s) | File |
|---|---|---|
| FastAPI container (port 8105, `MKT_GOV_PROFILE=gcp`, CMEK, fixed internal-only ingress, Direct VPC all-traffic egress, `/healthz` probe) | `google_cloud_run_v2_service` | `cloud_run.tf` |
| Mkt5 consent caller boundary (custom OIDC audience, exact caller allowlist, service-level invoker) | Cloud Run custom audience + `roles/run.invoker` | `cloud_run.tf` |
| Gemini reasoning/triage + File Search rule KB + Gen AI eval | `aiplatform` API | `apis.tf` |
| Model Armor guardrail | `modelarmor` API | `apis.tf` |
| WORM audit log (locked bucket + sink + data-access audit) | `logging` | `logging_worm.tf` |
| Tracing | `cloudtrace` API | `apis.tf` |
| Residency org policy + no SA keys + private data plane | `gcp.resourceLocations`, ... | `org_policy.tf` |
| Regional CMEK key + per-service IAM bindings | `cloudkms` | `kms.tf` |
| Existing Shared VPC validation + least-privilege service-agent access | Compute data source/IAM | `network.tf` |
| One Mkt5/Mkt6/host-project VPC Service Controls perimeter (dry-run first) | Access Context Manager | `vpc_sc.tf` |
| Posture alerts (guardrail blocks, SA-key creation, VPC-SC denials, CMEK changes) | log-based metrics + alert policies | `monitoring.tf` |
| Least-privilege runtime identity (Workload Identity, no keys) | `google_service_account` | `iam.tf` |

The APIs enabled in `apis.tf` map one-to-one onto the `gcp:` adapter bindings in
`config/settings.yaml`: only the services those adapters use are enabled, plus the core
deploy services (Cloud Run, Artifact Registry, Cloud KMS, IAM, Org Policy, Access Context
Manager, Monitoring, Compute). The `agent_registry` and `tool_catalog` adapters are HTTP
clients to platform-internal services and need no Google API.

## Mkt5 -> Mkt6 managed consent authentication

Set `s2s_audience` to a stable reviewed HTTPS audience and
`mkt5_caller_service_account` to the exact Mkt5 Cloud Run runtime identity. The service uses
the former as its Cloud Run custom audience and `MKT6_S2S_AUDIENCE`, and the latter as both
`MKT6_S2S_ALLOWED_CALLERS` and the sole service-level `roles/run.invoker` member. Mkt5 targets
the service URL but mints its short-lived Google ID token for that custom audience through
Workload Identity. No static S2S credential is accepted as Terraform input or written to
state. Pass the `service_url` and `s2s_audience` outputs to Mkt5's `consent_store_url` and
`consent_store_audience` inputs respectively.

## Shared network and perimeter topology

The production route has three independent gates: network reachability, VPC-SC membership,
and OIDC/IAM identity. Passing one never bypasses either of the others.

The application modules consume an existing horizontal Shared VPC rather than creating a
repo-local network. Before planning either repo, the network owner must create a region-local
`/26` or larger subnet with Private Google Access, associate both Mkt5 and Mkt6 service
projects with the host, and permit each application Terraform identity to add the narrow host
network-viewer/subnet-user grants in `network.tf`. Both modules validate fully-qualified
network/subnet resource names; the Cloud Run resource additionally fails its plan precondition
if the subnet is on another network or Private Google Access is disabled.

Both Cloud Run revisions route `ALL_TRAFFIC` through Direct VPC egress. This is required for a
VPC-SC-protected Cloud Run deployment and makes Mkt5's request to the non-RFC1918 Mkt6
`run.app` URL traverse the VPC. Mkt6 ingress is hard-coded to
`INGRESS_TRAFFIC_INTERNAL_ONLY`; there is no Terraform input that can silently widen it.
Private Google Access keeps Google service traffic private; add Cloud NAT only if other
dependencies need public internet destinations.

Mkt6 owns the one regular perimeter in the reference topology. Its resource contains the
numeric project numbers for the Shared VPC host, Mkt5 and Mkt6 and restricts the union of both
systems' managed APIs. Mkt5 declares the identical membership but sets
`manage_shared_vpc_sc_perimeter = false`. A project can belong to only one regular perimeter:
never enable ownership in both states, and move/import Terraform state before transferring
ownership. The access policy id, perimeter short name and all three project numbers must be
identical in both repos.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in project_id, org_id, ...
terraform init -input=false
terraform plan                                  # review; do NOT auto-apply the WORM lock blindly
terraform apply
```

Or, from the repo root: `make tf-plan`.

Build and push the container before apply, then resolve the immutable digest and put the
regional `@sha256:` URI in `terraform.tfvars` (tags are deliberately refused):

```bash
gcloud builds submit --tag asia-southeast1-docker.pkg.dev/PROJECT/mkt/marketing-compliance-gate:0.1.0
gcloud artifacts docker images describe \
  asia-southeast1-docker.pkg.dev/PROJECT/mkt/marketing-compliance-gate:0.1.0 \
  --format='value(image_summary.fully_qualified_digest)'
```

Deployment order is: provision/associate the Shared VPC; apply Mkt6 in dry-run as the sole
perimeter owner; pass Mkt6's `service_url` and `s2s_audience` outputs to Mkt5; apply Mkt5 as a
perimeter consumer; prove the authenticated hop succeeds and direct internet ingress fails;
then promote only Mkt6's `vpc_sc_enforce` after the dry-run logs are clean.

## Cautions

- **WORM lock is irreversible** (`logging_worm.tf`). Confirm `retention_days` before apply;
  `locked = true` cannot be undone for the full retention window.
- **CMEK key is `prevent_destroy`** (`kms.tf`). Destroying it would strand all encrypted data.
- **VPC-SC is dry-run first** (`vpc_sc.tf`). Apply with `vpc_sc_enforce = false`, watch the
  dry-run audit logs, add your operator/CI identity to an access level, confirm no legitimate
  path breaks, then re-apply with `vpc_sc_enforce = true` to enforce. Never enforce blind.
- **Ingress is fixed internal-only.** Do not put an external load balancer or public custom
  domain in the Mkt5 consent URL; use this service's default `run.app` output over the Shared
  VPC path.
- **Managed consent is double-gated.** Mkt5 must have service-level Cloud Run invoker IAM and
  its Google-signed token must match both the reviewed custom audience and application caller
  allowlist. Do not replace this with a long-lived shared secret.
- This module is **not run** as part of the offline CI gate; it is infra-as-code for review.
