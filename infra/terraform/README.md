# `marketing-compliance-gate` Marketing Compliance and Governance: Terraform (APAC-resident, sovereign deploy)

This module provisions the managed stack for the `marketing-compliance-gate` marketing compliance and governance
service and, when `standalone_service_enabled = true`, deploys its FastAPI container (the repo
`Dockerfile`) on Cloud Run v2.

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
| `next-best-action` consent caller boundary (custom OIDC audience, exact caller allowlist, service-level invoker) | Cloud Run custom audience + `roles/run.invoker` | `cloud_run.tf` |
| Gemini reasoning/triage + Gen AI eval | `aiplatform` API | `apis.tf` |
| Model Armor guardrail template `mkt-gov-guardrail` (regional capabilities follow `model_armor_full_capabilities`) | `google_model_armor_template` | `model_armor.tf` |
| Consent and substantiation-evidence stores: one `mkt6-<region>` database per residency region, with the composite indexes the adapters query | `google_firestore_database`, `google_firestore_index` | `firestore.tf` |
| Audit log: bucket (WORM when `worm_locked = true`), sink, and data-access audit unless `manage_audit_config = false` | `logging` | `logging_worm.tf` |
| Tracing | `cloudtrace` API | `apis.tf` |
| Residency org policy + no SA keys + private data plane | `gcp.resourceLocations`, ... | `org_policy.tf` |
| Regional CMEK key + per-service IAM bindings | `cloudkms` | `kms.tf` |
| Existing Shared VPC validation + least-privilege service-agent access | Compute data source/IAM | `network.tf` |
| One `next-best-action`, `marketing-compliance-gate`/host-project VPC Service Controls perimeter (dry-run first) | Access Context Manager | `vpc_sc.tf` |
| Posture alerts (guardrail blocks, SA-key creation, VPC-SC denials, CMEK changes) | log-based metrics + alert policies | `monitoring.tf` |
| Least-privilege runtime identity (Workload Identity, no keys) | `google_service_account` | `iam.tf` |

The APIs enabled in `apis.tf` map one-to-one onto the `gcp:` adapter bindings in
`config/settings.yaml`: only the services those adapters use are enabled, plus the core
deploy services (Cloud Run, Artifact Registry, Cloud KMS, IAM, Org Policy, Access Context
Manager, Monitoring, Compute). The `agent_registry` and `tool_catalog` adapters are HTTP
clients to platform-internal services and need no Google API.

**There is no rule store to provision.** The `gcp` profile's `rule_provider` serves the
versioned rule pack bundled in the package (`adapters/local/_seed.py`, `RULE_PACK_VERSION`),
so the compliance rules a deployed review fires are the rules the offline gate and the
evaluation set proved, at a version every audit event names. Changing a rule is a reviewed
repository change, not a console edit against a managed index, and an operator cannot leave
the deployment pointing at an empty store.

## `next-best-action` -> `marketing-compliance-gate` managed consent authentication

Set `s2s_audience` to a stable reviewed HTTPS audience and
`mkt5_caller_service_account` to the exact `next-best-action` Cloud Run runtime identity. The service uses
the former as its Cloud Run custom audience and `MKT6_S2S_AUDIENCE`, and the latter as both
`MKT6_S2S_ALLOWED_CALLERS` and the sole service-level `roles/run.invoker` member. `next-best-action` targets
the service URL but mints its short-lived Google ID token for that custom audience through
Workload Identity. No static S2S credential is accepted as Terraform input or written to
state. Pass the `service_url` and `s2s_audience` outputs to `next-best-action`'s `consent_store_url` and
`consent_store_audience` inputs respectively.

## Shared network and perimeter topology

The production route has three independent gates: network reachability, VPC-SC membership,
and OIDC/IAM identity. Passing one never bypasses either of the others.

The application modules consume an existing horizontal Shared VPC rather than creating a
repo-local network. Before planning either repo, the network owner must create a region-local
`/26` or larger subnet with Private Google Access, associate both `next-best-action` and `marketing-compliance-gate` service
projects with the host, and permit each application Terraform identity to add the narrow host
network-viewer/subnet-user grants in `network.tf`. Both modules validate fully-qualified
network/subnet resource names; the Cloud Run resource additionally fails its plan precondition
if the subnet is on another network or Private Google Access is disabled.

Both Cloud Run revisions route `ALL_TRAFFIC` through Direct VPC egress. This is required for a
VPC-SC-protected Cloud Run deployment and makes `next-best-action`'s request to the non-RFC1918 `marketing-compliance-gate`
`run.app` URL traverse the VPC. `marketing-compliance-gate` ingress is hard-coded to
`INGRESS_TRAFFIC_INTERNAL_ONLY`; there is no Terraform input that can silently widen it.
Private Google Access keeps Google service traffic private; add Cloud NAT only if other
dependencies need public internet destinations.

`marketing-compliance-gate` owns the one regular perimeter in the reference topology. Its resource contains the
numeric project numbers for the Shared VPC host, `next-best-action` and `marketing-compliance-gate` and restricts the union of both
systems' managed APIs. `next-best-action` declares the identical membership but sets
`manage_shared_vpc_sc_perimeter = false`. A project can belong to only one regular perimeter:
never enable ownership in both states, and move/import Terraform state before transferring
ownership. The access policy id, perimeter short name and all three project numbers must be
identical in both repos.

## Embedded under `journey-portal` in a shared project

The portal creates the `journey-marketing-compliance-gate-api` and `-ui` Cloud Run services from
digest-pinned images. This stack is the support stack beside them and deploys no Cloud Run service
of its own: `standalone_service_enabled` stays `false`, so the Cloud Run, consent-caller and Shared
VPC rows above are absent. In a project where another stack already owns the project-level
controls, decline them by variable:

| Control | Variable | Shared-project value | Why |
|---|---|---|---|
| Org Policies (`gcp.resourceLocations` and three hardening constraints) | `manage_org_policies` | `false` | One value per constraint per project, and this stack's strictest form would narrow a sibling's |
| Data-access audit config | `manage_audit_config` | `false` | Authoritative per service: applying it replaces the owner's configuration |
| VPC-SC perimeter | `enable_vpc_sc` | `false` | A second regular perimeter would enforce where the owner observes |
| Model Armor malicious-URI filter and multi-language detection | `model_armor_full_capabilities` | `false` in `asia-southeast1` | The region serves neither and refuses the whole template |
| WORM lock on the audit bucket | `worm_locked` (no default) | a deliberate `true` or `false` | Irreversible when true |
| Firestore CMEK | `firestore_cmek_key` | `""`, the default | Allowlist-gated by Google; an unadmitted project fails the apply |

`additional_serving_service_accounts` names the portal's API runtime identity, which the portal
mints on its own apply. Apply this stack with the list empty, then the portal, then this stack again
with the identity filled in, which grants it the consent store, the models and the guardrail.

**Images.** `Dockerfile` builds the API (port 8105). `ui/Dockerfile` builds the console (port
3000) with `NEXT_PUBLIC_BASE_PATH=/apps/marketing-compliance-gate` and
`NEXT_PUBLIC_API_BASE=/apps/marketing-compliance-gate/api` as build arguments.

**The API's identity inputs.** `MKT_GOV_PROFILE=gcp`, `MKT_GOV_IAP_AUDIENCE`,
`MKT_GOV_IAP_TENANT_DOMAINS_JSON` mapping each sign-in domain to the tenant the consent seed was
loaded under, and `MKT_GOV_IAP_MACHINE_TENANTS_JSON` mapping each programmatic caller's EXACT
service-account address to the same tenant. Without the maps a verified caller resolves to their
own domain or to no tenant at all, and every consent read comes back empty.

That is now load-bearing for the REVIEW route as well as the consent routes: a review carries an
audience subject id and no consent, and reads that subject's records under the verified tenant,
so an unmapped caller gets a non-compliant review on every asset whose market requires consent.
The machine map is what stops the deployment's own end-to-end identity hitting that: a sibling
service shipped an authorized surface that refused every machine caller because it resolved
tenancy from a human's hosted domain alone. Key machines on the account, never the domain, since
every account in a project shares one.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in project_id, org_id, ...
terraform init -input=false -backend-config=bucket=<state-bucket> -backend-config=prefix=marketing-compliance-gate
terraform plan                                  # review; do NOT auto-apply the WORM lock blindly
terraform apply
```

Or, from the repo root: `make tf-plan TF_STATE_BUCKET=<state-bucket>`.

## State

`providers.tf` declares a partial `backend "gcs" {}`. The bucket and the prefix are init inputs,
never code: `<state-bucket>` is the deployment's state bucket, which every other deployed stack
shares under its own prefix, and this stack's prefix is `marketing-compliance-gate`. Local state
for a stack that owns a KMS key and the Firestore stores exists on exactly one laptop, so a named
deployment does not keep it. The offline proof never touches the bucket: `make tf-validate`
runs `terraform init -backend=false` before `terraform validate` and `terraform test`.

**Migrate existing local state once. Never re-create it.** An installation applied before the
backend was declared holds its state in a gitignored `terraform.tfstate` in this directory,
recording the `mkt6-<region>` Firestore database, its composite indexes, the KMS key ring and
key, and the enabled services. From the directory holding that file, with credentials:

```bash
terraform init -migrate-state -backend-config=bucket=<state-bucket> -backend-config=prefix=marketing-compliance-gate
terraform plan   # expect no creates for the database, the indexes or the key ring
```

Answer `yes` when init offers to copy the existing state into the bucket. Starting from an empty
prefix instead plans the database and the key ring as new, and both creates fail because both
already exist. Keep the local file until the migrated plan shows none of those creates.

Build and push the container before apply, then resolve the immutable digest and put the
regional `@sha256:` URI in `terraform.tfvars` (tags are deliberately refused):

```bash
gcloud builds submit --tag asia-southeast1-docker.pkg.dev/PROJECT/mkt/marketing-compliance-gate:0.1.0
gcloud artifacts docker images describe \
  asia-southeast1-docker.pkg.dev/PROJECT/mkt/marketing-compliance-gate:0.1.0 \
  --format='value(image_summary.fully_qualified_digest)'
```

Deployment order is: provision/associate the Shared VPC; apply `marketing-compliance-gate` in dry-run as the sole
perimeter owner; pass `marketing-compliance-gate`'s `service_url` and `s2s_audience` outputs to `next-best-action`; apply `next-best-action` as a
perimeter consumer; prove the authenticated hop succeeds and direct internet ingress fails;
then promote only `marketing-compliance-gate`'s `vpc_sc_enforce` after the dry-run logs are clean.

## Cautions

- **WORM lock is irreversible** (`logging_worm.tf`). `worm_locked` has no default, so every
  deployment names it; `true` cannot be undone for the full retention window.
- **CMEK key is `prevent_destroy`** (`kms.tf`). Destroying it would strand all encrypted data.
- **VPC-SC is dry-run first** (`vpc_sc.tf`). Apply with `vpc_sc_enforce = false`, watch the
  dry-run audit logs, add your operator/CI identity to an access level, confirm no legitimate
  path breaks, then re-apply with `vpc_sc_enforce = true` to enforce. Never enforce blind.
- **Ingress is fixed internal-only.** Do not put an external load balancer or public custom
  domain in the `next-best-action` consent URL; use this service's default `run.app` output over the Shared
  VPC path.
- **Managed consent is double-gated.** `next-best-action` must have service-level Cloud Run invoker IAM and
  its Google-signed token must match both the reviewed custom audience and application caller
  allowlist. Do not replace this with a long-lived shared secret.
- This module is **not applied** by the offline CI gate. `make tf-validate` runs `terraform
  validate` and the mock-provider plan tests in `tests/` with no credentials.
