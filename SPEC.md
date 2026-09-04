# SPEC: `marketing-compliance-gate` Marketing Compliance and Brand Governance

## 1. Purpose and scope

`marketing-compliance-gate` reviews a marketing **asset** (a `Campaign`, `Creative` or `Offer`) against the
per-market, per-vertical advertising, consumer-protection and consent rules in force, and
produces a cited **Review** plus a maker-checker **ApprovalRecord**. It also runs the
**green-claims gate**: for an asset that makes environmental claims it produces a cited
**SubstantiationAssessment** saying whether the evidence the brand holds actually carries
those claims (see section 8). It is generic marketing
compliance: banking and online retail are configurable verticals, and Japan, Australia and
Singapore are first-class markets. Compliance / Marketing Operations is the owner; the
output is a gate, never an auto-executed action. Banking financial-promotion rules are one
configured rule set among others, not the only frame.

## 2. Configuration axes

| Setting | Env | Values | Notes |
|---|---|---|---|
| `profile` | `MKT_GOV_PROFILE` | `gcp` `local` `platform` `onprem` | selects the adapter stack. No default: unset is no choice, which binds the SDK-free adapters but refuses every `local` relaxation (no seeded personas, empty CORS allowlist). Prod sets `gcp` explicitly |
| `vertical` | `MKT_VERTICAL` | `banking` `online_retail` | the active vertical |
| `market` | `MKT_MARKET` | `JP` `AU` `SG` | the active market |

Per-market residency regions and locales come from `MARKET_PROFILES` (overridable under
`markets:` in `config/settings.yaml`), validated by `Settings.market_profile()` and the GCP
`resolve_region`:

| Market | Region | Locales | Currency |
|---|---|---|---|
| JP | `asia-northeast1` (Tokyo) | ja, en | JPY |
| AU | `australia-southeast1` (Sydney) | en | AUD |
| SG | `asia-southeast1` (Singapore) | en | SGD |

Per-market and per-vertical rule sets (JP Act on Specified Commercial Transactions and
Premiums & Representations Act, APPI; AU Australian Consumer Law / ASIC, Spam / Privacy Act;
SG PDPA and advertising standards; banking financial-promotion rules as one set among
others) are config + seed, served by the `RuleProviderPort`. They are never hard-coded in
the rule engine.

## 3. Ports (the hexagon boundary)

| Port | Method(s) | GCP backing |
|---|---|---|
| `RuleProviderPort` | `rule_set`, `search` | Gemini API File Search over the rule KB |
| `EvidenceStorePort` | `list_for_asset`, `get`, `put` | Firestore, in the market's residency region |
| `LlmPort` | `generate`, `classify` | Gemini (`gemini-3.5-flash`, `gemini-3.5-flash`) |
| `GuardrailPort` | `screen` | Model Armor |
| `AuditSinkPort` | `record` | Cloud Logging locked WORM bucket |
| `ObservabilityTracerPort` | `span`, `record_token_usage` | Cloud Trace via OpenTelemetry |
| `EvaluationGatePort` | `evaluate`, `gate` | Gen AI evaluation service (`model-quality-gate`) |
| `AgentRegistryPort` | `register`, `get`, `list` | A2A AgentCard registry (`agent-registry`) |
| `ToolCatalogPort` | `list_tools`, `get_tool` | governed MCP tool catalog |
| `IdentityPort` | `resolve` | IAP-injected signed assertion |
| `ReviewRouterPort` | `route`, `route_assessment` | `human-review-console` via `review-kit` |

Every port is a `@runtime_checkable` `Protocol`; adapters need only structural conformance.

The governed catalog is SERVED, not only declared: `marketing_compliance_gate.mcp` answers it
over MCP 2026-07-28 on stdio (`make mcp-serve`), and `hex_service_kit.mcpserve.bind` refuses at
start-up if the declared tools and the bound handlers disagree in either direction.

It declares the MAKER half only. `approve_review` was declared here and removed: approval IS the
four-eyes control, this transport verifies no human, and rule R8 already routes an escalated
review to the `human-review-console`, which resolves a real principal before anyone disposes. The ADK tool
surface and the A2A card exclude it for the same reason, so all three surfaces now agree.
`market` and `vertical` are required rather than optional on both tools, because
`RuleProviderPort.search` cannot run without them and the managed adapter keys its per-market
residency check on the market.

## 4. The deterministic engine (`domain/rule_engine.py`)

`RuleEngine` is the heart: pure (stdlib only), replayable (same asset + rule set produce the
same findings) and unit-tested. The LLM never decides any of this. Each `CheckType` maps to
one transparent predicate over the asset's title + body / fields / granted consents:

| CheckType | Predicate |
|---|---|
| `FORBIDDEN_PHRASE` | title + body must NOT contain any pattern (case-insensitive) |
| `REQUIRED_DISCLOSURE` | title + body MUST contain every pattern |
| `REQUIRED_FIELD` | a metadata field MUST be set (non-blank) |
| `NUMERIC_MAX` | a numeric field MUST be <= a limit (absent/unparseable fails closed) |
| `CONSENT_REQUIRED` | a consent purpose MUST be in the asset's granted consents |

`RuleKind` is `claim`, `permission`, `brand`, `consent` or `green_claim`. Green-claim rules
carry `applies_to_categories`: they are selected only when the asset actually makes a claim in
one of those categories, so a "disclose the offsetting basis" rule never fires on copy that
says nothing about carbon. The selection happens in the substantiation service; the engine
itself stays generic.

Findings are ordered deterministically (failures first, then severity descending, then rule
id). Consent rules are evaluated separately and yield both a `ConsentCheck` and a finding.

## 5. Orchestration (`ReviewService`)

```
guardrail.screen(INPUT over asset copy)  -> blocked: audit BLOCKED + raise GuardrailBlockedError
rule_provider.rule_set(market, vertical) -> RuleSet (empty: raise RuleSetEmptyError)
rule_engine.check                        -> claim / permission / brand findings
rule_engine.consent_checks               -> consent checks + findings
decide outcome + requires_human_review   -> pure (outcome by findings; review always required)
llm.generate                             -> summary narrative (narration only)
assemble Review (+ PENDING ApprovalRecord)
guardrail.screen(OUTPUT over summary)    -> blocked: audit BLOCKED + raise
audit.record                             -> Decision.ESCALATED when human review required
```

`approve(review, checker, approved, rationale)` is the checker half of maker-checker: it
records a human's terminal decision on a previously-built review and audits it.

## 6. Output artifacts

* **`Review`**: `outcome` (compliant / non_compliant), `findings` (each cited, with
  severity, evidence and remediation), `consent_checks`, `summary`, `citations`, `approval`
  (a `PENDING` `ApprovalRecord`), `requires_human_review`.
* **`ApprovalRecord`**: `decision` (pending / approved / rejected), `checker`, `rationale`,
  `decided_at`.

All artifacts serialise to plain JSON via `domain.serialization.to_jsonable` (enums to
values, datetimes to ISO, dataclasses to dicts).

## 7. Quality gate (`model-quality-gate`)

`eval/run_eval.py` runs the real `ReviewService` over `eval/datasets/golden_reviews.jsonl` and
the real `SubstantiationService` over `eval/datasets/golden_green_claims.jsonl`, both on the
local profile, and enforces: `rule_coverage >= 0.95`, `finding_accuracy >= 0.90`,
`citation_accuracy >= 0.99`, `review_safety >= 0.99`, `substantiation_accuracy >= 0.99`. Exit
non-zero on failure.

`substantiation_accuracy` scores the verdict against each golden row's `expected_verdict`,
which is written from the evidence in the row rather than read back from the product, so an
overclaim (declaring a claim substantiated where the evidence is expired, stale, self-declared
or absent) scores 0. `agent_eval_kit.assert_each_can_go_red` runs over it, alongside the other
strict metrics, before the gate scores anything.

On the `platform` profile, `EvaluationGatePort` is a real HTTP client to the shared `model-quality-gate`
AI-quality service (not a stub): `evaluate` POSTs `/v1/evaluations` and `gate` POSTs
`/v1/gate`, both with a structured `{target: {model, prompt_version, dataset_id, system},
dataset_id, bundle: "mkt6-compliance"}` body, and `evaluate` maps the returned `results[]`
into an `EvalReport`. Metric selection is server-side by the registered bundle name
`mkt6-compliance`: the client never sends a metric list, so tightening a threshold stays
`model-quality-gate`'s concern. The base URL comes from `QUALITY_GATE_URL`.

## 8. The green-claims gate

### 8.1 Vocabulary and the rule pack

`GreenClaimCategory` is the closed `StrEnum` claim vocabulary (`carbon_neutral`, `net_zero`,
`recyclable`, `biodegradable`, `renewable_energy`, `sustainable_sourcing`, `esg_fund_label`,
`green_finance_proceeds`) and `EvidenceKind` is the closed vocabulary of substantiation
evidence. Everything jurisdiction-specific is CONFIG, in the pack at
`src/marketing_compliance_gate/rulepacks/green_claims.yaml` (override with
`green_claims.pack_path` / `MKT_GOV_GREEN_PACK`):

| Pack section | What it holds |
|---|---|
| `citations` | the regulator instruments the rules and requirements cite, by id |
| `categories` | the detection phrases that classify copy into a category (base plus per-jurisdiction, so JP adds Japanese wording) |
| `requirement_defaults` and per-jurisdiction `requirements` | required evidence kinds, `max_evidence_age_days`, `requires_independent_verification`, remediation |
| per-jurisdiction `rules` | the `green_claim` rules: `forbidden_phrase`, `required_disclosure` and `required_field`, each with a `citation` id and the verticals it binds to |

Instruments cited in the reference pack: the ACCC's *Making environmental claims: A guide for
business* (2023), the Australian Consumer Law (Competition and Consumer Act 2010 Sch 2, ss 18
and 29(1)(a)), ASIC Information Sheet 271, MAS Circular CFC 02/2022 (Disclosure and Reporting
Guidelines for Retail ESG Funds), the Singapore Code of Advertising Practice, the Consumer
Protection (Fair Trading) Act 2003, Japan's Act against Unjustifiable Premiums and Misleading
Representations (Act No. 134 of 1962), the Consumer Affairs Agency's views on environmental
labelling, the FSA's ESG-fund supervisory guidelines (2023), and ISO 14021:2016. The thresholds
alongside them are adopter-owned policy with the shipped defaults equal to this reference, not
quoted regulatory limits.

Loading is fail-closed: a missing file, an unknown category / evidence kind / check type, a
rule citing an undefined instrument, or a requirement with no evidence kinds raises
`GreenClaimPackError` rather than running a gate with a hole in it.

### 8.2 The coverage engine (`domain/coverage_engine.py`)

Pure, stdlib-only, no clock and no I/O.

1. **Detect.** Case-insensitive phrase matching of the pack's vocabulary over title + body,
   at most one claim per category, deterministically ordered.
2. **Cover.** For each claim, take the jurisdiction's `SubstantiationRequirement` and, for each
   required evidence kind, look for evidence that names the claim's category, is dated, is not
   future-dated, is unexpired at `as_of`, is within `max_evidence_age_days`, and is
   independently verified where the requirement demands it.
   `coverage = satisfied kinds / required kinds`, rounded to 4 places.
3. **Roll up.** Overall coverage is the mean of the per-claim coverages; the overall verdict is
   the WORST per-claim verdict. An asset making no green claim is `not_applicable` with
   coverage 1.0, never trivially "substantiated".

`SubstantiationVerdict` is `not_applicable`, `substantiated`, `partially_substantiated` or
`unsubstantiated`. Every uncertainty resolves against the claim: an unconfigured category, a
requirement with no evidence kinds, undated evidence, or evidence filed under another category
all produce `unsubstantiated`.

### 8.3 Orchestration (`SubstantiationService`)

```
resolve tenant from the VERIFIED principal   -> no tenant: TenantAccessDeniedError (403)
guardrail.screen(INPUT over asset copy)      -> blocked: audit BLOCKED + raise
evidence_store.list_for_asset(tenant, asset) -> tenant-filtered IN THE STORE
coverage_engine.assess(...)                  -> claims, verdict, coverage   (pure)
pack.rules_for(market, vertical, categories) -> the applicable green-claim rules
rule_engine.check(...)                       -> green-claim findings        (pure)
llm.generate                                 -> narrative (narration only)
assemble SubstantiationAssessment (cited)
guardrail.screen(OUTPUT over the narrative)  -> blocked: audit BLOCKED + raise
audit.record                                 -> Decision.ESCALATED when human review required
review_router.route_assessment               -> `human-review-console` (rule R8), best effort, after the audit
```

`requires_human_review` is true whenever the asset makes any green claim at all, or fails any
green-claim rule. Unlike a routine review this does not wait for a failure: signing off an
environmental claim is a judgement a qualified person owns.

### 8.4 Object-level authorization

Substantiation evidence is tenant-owned, and the tenant always comes from the `Principal` the
`IdentityPort` verified, never from the request:

* `POST /v1/substantiation` carries an asset and an optional `as_of`, and no tenant field.
* `GET /v1/evidence?asset_id=...` lists only the caller's tenant (the filter is in the store,
  with a defense-in-depth re-filter in the domain).
* `GET /v1/evidence/{id}` returns `403` when the record belongs to another tenant, and `404`
  only when no such record exists. The denial is written to the audit log.

`tests/unit/test_substantiation_tenant_access.py` pins all of this, including a cross-tenant
denial test that fails when the check is removed.
