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
- `gcp`: the managed stack (the bundled versioned rule pack, Model Armor, Cloud Logging).
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

There are two topologies. **Embedded under `journey-portal`**, which is the reference deployment:
the portal runs the API and the console as its own Cloud Run services, and this stack provides the
Firestore stores, the guardrail template, the key and the audit bucket, with
`standalone_service_enabled = false` (the default) and the shared-project declines listed in
`infra/terraform/README.md`. **Standalone, for the `next-best-action` consent hop**: set
`standalone_service_enabled = true`. The rest of this section describes the standalone topology.

The network platform must first associate both service projects with one existing Shared VPC
host and provide a `/26` or larger region-local subnet with Private Google Access. `marketing-compliance-gate` owns
the single regular VPC-SC perimeter in the reference topology; its membership is the host,
`next-best-action` and `marketing-compliance-gate` numeric project numbers. `next-best-action` declares the identical inputs but sets
`manage_shared_vpc_sc_perimeter = false`.

```bash
# 1. Provision infra (review the plan; the WORM bucket lock is irreversible when
#    worm_locked = true, and the variable has no default).
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id, org_id, access_policy_id
# State lives in the deployment's GCS state bucket under this stack's own prefix.
terraform init -input=false -backend-config=bucket=<state-bucket> -backend-config=prefix=marketing-compliance-gate
terraform plan
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

**An installation applied before the GCS backend existed** holds its state in a local, gitignored
`infra/terraform/terraform.tfstate`. Migrate it once, from that directory, before any other plan:

```bash
terraform init -migrate-state -backend-config=bucket=<state-bucket> -backend-config=prefix=marketing-compliance-gate
terraform plan   # expect no creates for the Firestore database, its indexes or the key ring
```

Do not re-create instead: the Firestore database and the key ring already exist, so their creates
fail. The procedure and why are in `infra/terraform/README.md` under State.

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
false` and `worm_locked = false` so everything stays deletable (not compliant for
production). See `infra/terraform/terraform.tfvars.example` and `infra/terraform/README.md`.

The ADK agent is deployed to Agent Runtime separately via the Agent Platform SDK; see the
docstring in `src/marketing_compliance_gate/agent/root_agent.py`. Record the resulting
`reasoningEngine` resource name in `settings.agent_engine.resource_name` (or `MKT_AGENT_ENGINE`).
To attach an out-of-process governed MCP tool server, set `MKT_GOV_MCP_SERVER_URL`; unset, the
agent uses its in-process FunctionTools.

## 3. Rule sets and grounding

Every review is grounded in the per-market, per-vertical rule set (`RuleProviderPort`). Under
`gcp` and `local` the rule set is the versioned rule pack bundled in the package
(`adapters/local/_seed.py`, `RULE_PACK_VERSION`); only `platform` fetches it from the
`enterprise-knowledge-base` governed KB over HTTP. **The deployment provisions no rule store**,
so there is no managed index for an operator to edit, leave empty, or let drift from the rules
the gate proved: changing a rule is a reviewed repository change that bumps the version.

The version travels. `RuleSet.version` is stamped by the provider, `mkt-gov rules` prints it,
and `_record_review` writes it to the audit event as `rule_pack_version`, so a finding is
traceable to the revision of the rules that produced it. A provider that cannot say which
revision it served records an empty string, which is visible rather than a guess.

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

## The Firestore stores

Two ports bind Firestore under `gcp` / `platform`: the substantiation evidence store and the
consent store. **Nothing in `infra/terraform/` created either of them** until now, and the
region both adapters validated never reached the client: they called `resolve_region(...)`,
discarded the result and built a client against the project's DEFAULT database, whose location
is fixed at creation and is a single one of the three in-country regions.

So there is one NAMED database per residency region this installation serves, `mkt6-<region>`,
and the adapters select it from the region they resolved. `var.residency_regions` lists them
and defaults to `[var.region]`: a single-market install provisions exactly one store, because a
database in a country nobody is serving is standing cost and a residency surface with no user.

**CMEK on these databases is off by default, and that is not a preference.** Firestore
customer-managed encryption is allowlist-gated by Google: a project that has not been admitted
cannot create a CMEK database and the apply fails outright rather than degrading. The reference
deployment is not admitted, which `org-metadata/docs/deployment-posture.md` records as
externally blocked. `var.firestore_cmek_key` is empty by default and a deployment that HAS been
admitted sets it to the stack's own key; the service-agent key binding is already in `kms.tf`,
so turning it on is one variable.

Composite indexes are declared for every multi-field query the two adapters run. Firestore
maintains single-field indexes itself, and a composite query with no index fails at REQUEST
time with `FAILED_PRECONDITION`: the first time a compliance officer opens a subject, on the
deployment and nowhere else.

### Seeding the demo consent

The marketing journey reads consent from this service over HTTP. On a deployment that leg is
empty until something writes a record, and a subject with no record on file is a subject nobody
may be sent anything: the correct answer to an empty store, and the wrong demo.

```bash
# apply infra/terraform first: the loader writes documents, it does not create databases
python scripts/load_consent_seed.py --project "$PROJECT" --tenant "$HOSTED_DOMAIN" --market SG
```

It writes the same seven subjects the offline profile serves, through the **managed adapter's
own write methods** rather than a second serializer, so there is one description of the
document shape. `--tenant` is required for the usual reason, `other-brand` is kept separate as
`<tenant>-other` so a cross-tenant read that "worked" is visibly wrong rather than merely empty,
and it refuses unless `mkt6_book_manifest/current` says what the store holds is fictional or the
store is empty. `--dry-run` needs no credentials.

The **evidence store is provisioned and not seeded**: it is created, indexed, keyed and
region-routed, and its records are whatever an institution ingests.

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
outside it fails at `terraform plan`, before anything is created. Cloud Logging, the WORM
bucket and the Firestore stores are all created in the selected region, and a
`gcp.resourceLocations` Org Policy
hard-restricts resource creation to it. The app also validates the active market's region at
load, so a mismatched deploy fails fast on both sides.

## 5. Key rotation, retention and the WORM lock

The CMEK crypto key (`kms.tf`) rotates on schedule; rotation is transparent to the app. The
audit bucket retention is `retention_days` (default 2557, ~7 years, and at least that whenever
the bucket is locked). The lock is `worm_locked`, which has **no default**: `true` is
**irreversible** for the retention window, and `false` keeps the bucket destroyable (not
compliant for production). Only screened prompts and responses are ever
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
