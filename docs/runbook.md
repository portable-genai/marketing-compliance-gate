# Runbook: `marketing-compliance-gate` Marketing Compliance and Brand Governance

Operational notes for deploying and running `marketing-compliance-gate` on the Gemini Enterprise Agent Platform in a
residency region (defaults `asia-southeast1`; JP and AU are per-market overrides). `marketing-compliance-gate` is the
shared marketing maker-checker gate the rest of the marketing tier (`market-intelligence`..`next-best-action`) routes through
for P-13 / R7. This is a reference build; adapt it to your own change-management and model-risk
sign-off before any live use.

## 0. Profiles

`MKT_GOV_PROFILE` selects the adapter stack. There is no default: leaving it unset is
"nobody chose", which is not the same as choosing `local`. An unset run still binds the
SDK-free adapters, because nothing else is installed, but every relaxation is refused: the
seeded no-auth personas are not served (every artifact route answers 401) and the CORS
allowlist is empty. Name the profile deliberately.

- `local` (SDK-free): the whole pipeline runs offline (deterministic rule engine and
  LLM, in-memory rule sets). No Google Cloud SDK. This is what CI and the demo run.
- `gcp`: the managed stack (File Search rule KB, Model Armor, Cloud Logging).
- `platform`: consume the shared Hrz services (guardrail / KB / audit / eval / registry) over
  S2S.
- `onprem`: fail-fast placeholders that raise `NotImplementedError`, the migration target (see
  `docs/onprem-migration.md`).

`MKT_VERTICAL` (`banking` | `online_retail`) and `MKT_MARKET` (`JP` | `AU` | `SG`) select the
active vertical and market; the market's residency region and locales come from the per-market
profile in `config/settings.yaml`, never a hard-coded branch.

## 1. Offline demo and smoke (no cloud)

```bash
make demo          # review an asset + render the static audit-first HTML into scripts/out
make smoke-local   # end-to-end offline: review one asset under the local profile
make run-api       # FastAPI on 127.0.0.1:8105 (local profile binds loopback by default)
```

The agent card is served at `GET /.well-known/agent-card.json` and the health probe at
`GET /healthz`. The agent is the **maker** (it produces reviews via `/v1/review`); approval is a
separate human **checker** action, so the agent never clears an asset itself.

## 2. Deploy (managed stack)

The network platform must first associate both service projects with one existing Shared VPC
host and provide a `/26` or larger region-local subnet with Private Google Access. `marketing-compliance-gate` owns
the single regular VPC-SC perimeter in the reference topology; its membership is the host,
`next-best-action` and `marketing-compliance-gate` numeric project numbers. `next-best-action` declares the identical inputs but sets
`manage_shared_vpc_sc_perimeter = false`.

```bash
# 1. Provision infra (review the plan; the WORM bucket lock is irreversible when
#    locked = true, the default).
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id, org_id, access_policy_id
terraform init -input=false && terraform plan
terraform apply

# 2. Export the outputs the app reads.
export GOOGLE_CLOUD_PROJECT="$(terraform output -raw project_id)"
export MKT_GOV_REGION="$(terraform output -raw region)"
export MKT_GOV_KMS_KEY="$(terraform output -raw kms_key)"
export MKT_GOV_LOG_BUCKET="$(terraform output -raw log_bucket)"
export MKT6_S2S_AUDIENCE="$(terraform output -raw s2s_audience)"

# 3. Install the managed stack and run the API.
pip install -e ".[gcp,dev]"
export GOOGLE_CLOUD_PROJECT=your-sg-project MKT_GOV_PROFILE=gcp
gcloud auth application-default login
make run-api PROFILE=gcp          # FastAPI on :8105 (front with the platform ingress)
```

For `next-best-action` consent traffic, pass `marketing-compliance-gate`'s `service_url` and `s2s_audience` outputs to `next-best-action` as
`consent_store_url` and `consent_store_audience`. Terraform grants only the reviewed `next-best-action`
runtime service account Cloud Run invoker and injects that same email into
`MKT6_S2S_ALLOWED_CALLERS`. `next-best-action` then mints a short-lived Google ID token through Workload
Identity. A request must pass Cloud Run IAM and the application audience/caller verifier; no
static bearer is seeded or stored in Terraform state.

Apply `marketing-compliance-gate` first with `vpc_sc_enforce = false`, then `next-best-action`. Both revisions use Direct VPC
egress with `ALL_TRAFFIC`; `marketing-compliance-gate` ingress is fixed internal-only. Verify an authenticated `next-best-action`
request succeeds and a direct internet request to `marketing-compliance-gate` fails before promoting the owner to
enforced VPC-SC. Do not use an external custom domain for `next-best-action`'s consent URL; use the default
`run.app` output over the Shared VPC path.

For a quick project-scoped evaluation WITHOUT org-level prerequisites, set `enable_vpc_sc =
false` and the audit bucket `locked = false` so everything stays deletable (not compliant for
production). See `infra/terraform/terraform.tfvars.example` and `infra/terraform/README.md`.

The ADK agent is deployed to Agent Runtime separately via the Agent Platform SDK; see the
docstring in `src/marketing_compliance_gate/agent/root_agent.py`. Record the resulting
`reasoningEngine` resource name in `settings.agent_engine.resource_name` (or `MKT_AGENT_ENGINE`).
To attach an out-of-process governed MCP tool server, set `MKT_GOV_MCP_SERVER_URL`; unset, the
agent uses its in-process FunctionTools.

## 3. Rule sets and grounding

Every review is grounded in the per-market, per-vertical rule set (`RuleProviderPort`). Under
`gcp` / `platform` the rule set comes from the `enterprise-knowledge-base` governed KB (File Search); under `local` it
is the bundled fictional rule seed. Keep the rule KB versioned: a review is only as current as
the rules it fired, and the audit record cites the rule ids so a change is traceable.

## 3b. The green-claims gate: the rule pack and the evidence store

Two operational inputs, both of which fail closed:

**The jurisdiction rule pack.** `src/marketing_compliance_gate/rulepacks/green_claims.yaml` carries,
per market, the phrases that classify an environmental claim, the evidence each category
requires, how old that evidence may be, whether it must be independently verified, and the
green-claim rules with the regulator instrument each cites. Point `green_claims.pack_path`
(`MKT_GOV_GREEN_PACK`) at your own file to run your own policy. The thresholds in the pack are
adopter-owned policy, not quoted regulatory limits: review them with counsel before go-live, and
version the file, because an assessment is only as current as the pack that produced it.

A malformed pack, an unknown category or evidence kind, or a rule citing an instrument the pack
does not define raises `GreenClaimPackError` and the affected request returns HTTP 500. That is
deliberate: a green-claims gate running on a half-parsed pack would clear claims it never
checked. Validate a new pack before deploying it:

```bash
python -c "from marketing_compliance_gate.green_pack import load_pack; \
p = load_pack('path/to/pack.yaml'); print(p.version, len(p.rules), len(p.requirements))"
```

**The evidence store.** Under `gcp` / `platform` this is Firestore in the market's residency
region (collection `mkt6_substantiation_evidence`); under `local` it is a SQLite file
(`local.evidence_path`, `MKT_GOV_LOCAL_EVIDENCE`) seeded with fictional records. Every record
carries a `tenant`, and every read is authorized against the verified principal's tenant, so
loading evidence with the wrong tenant tag makes it invisible to its owner rather than visible
to everyone. Evidence with no `issued_date`, or with a `valid_until` in the past relative to the
assessment's `as_of`, never counts towards coverage: an ingestion job that drops dates will
quietly turn substantiated claims into unsubstantiated ones, so treat date fidelity as part of
the ingestion contract.

Assessments accept an explicit `as_of` date. Use it when re-running a past assessment for an
auditor; omit it to age evidence against today.

## 4. Region selection and fail-fast

The Terraform `region` is validated against the residency allowlist; an apply against a region
outside it fails at `terraform plan`, before anything is created. File Search, Cloud Logging and
the WORM bucket are all created in the selected region, and a `gcp.resourceLocations` Org Policy
hard-restricts resource creation to it. The app also validates the active market's region at
load, so a mismatched deploy fails fast on both sides.

## 5. Key rotation, retention and the WORM lock

The CMEK crypto key (`kms.tf`) rotates on schedule; rotation is transparent to the app. The
audit bucket retention is `retention_days` (default 2557, ~7 years) and the bucket is
`locked = true` by default, which is **irreversible**. To trial without locking, set
`locked = false` (not compliant for production). Only screened prompts and responses are ever
written to the audit log.

## 6. Kill switch

To stop serving without tearing down state: scale the Cloud Run / Agent Runtime deployment to
zero, or remove the app service account's `roles/aiplatform.user` binding. The audit trail
remains intact.

## 7. Common failures

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `NotImplementedError` from a CLI command (exit 2) | `MKT_GOV_PROFILE=onprem` with placeholder adapters | Set `MKT_GOV_PROFILE=gcp` (or implement the on-prem adapter) |
| `RuleSetEmptyError` on a review (HTTP 404) | No rule set for the asset's market / vertical | Seed / publish a rule set for that (market, vertical) pair |
| `403` on `GET /v1/evidence/{id}` | The record belongs to another tenant | Expected: object-level authorization refused it. Check the principal's tenant, not the record |
| Every green claim comes back `unsubstantiated` | Evidence missing its `issued_date`, tagged with the wrong tenant, or filed under another category | Fix the ingestion mapping; undated, mis-tenanted and mis-categorised evidence never counts |
| `GreenClaimPackError` (HTTP 500) on `/v1/substantiation` | The configured green-claim pack is missing or invalid | Validate the pack (section 3b); revert `MKT_GOV_GREEN_PACK` to the shipped reference pack |
| Guardrail block on a benign asset (HTTP 400) | Model Armor template too strict | Tune the `model_armor` template filter confidence levels |
| CORS error from the embedded UI | Origin not in the per-tenant allowlist | Add the parent origin to `MKT_GOV_CORS_ORIGINS` (never `*`) |
| HTTP 503 "refusing to serve the unauthenticated ... posture" | The bound identity adapter does not verify the end user (seeded personas, the on-prem placeholder, or no profile chosen) and the peer is not loopback | Front the service with IAP and set `MKT_GOV_PROFILE=gcp`, or serve the offline demo on loopback only. `MKT_GOV_ALLOW_INSECURE_DEMO=1` accepts the exposure deliberately |
| `next-best-action` is rejected before app verification | Source request did not traverse the Shared VPC | Confirm both service projects are associated with the same host, the subnet has Private Google Access, and `next-best-action` uses `ALL_TRAFFIC` Direct VPC egress to this service's `run.app` URL |
| VPC-SC denies the apply or consent hop | Distinct regular perimeters, missing host membership, or runner outside the boundary | Keep the owner in dry-run and confirm its one perimeter contains Shared VPC host + `next-best-action` + `marketing-compliance-gate` before enforcement |
