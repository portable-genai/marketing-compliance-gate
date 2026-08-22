# Mkt6 Marketing Compliance and Brand Governance (`marketing-compliance-gate`)

**Industries:** Banking, Retail & e-commerce, Insurance, Pharma (DTC advertising), Telecom, Gambling

A reference agentic service that reviews marketing **Campaigns, Creatives and Offers** for
compliance before they run. A deterministic rule engine checks each asset's **claims,
permissions, brand and consent** against the **per-market, per-vertical** advertising,
consumer-protection and consent rules in force, produces a cited finding per rule, and
gates the result behind a **marketing maker-checker** approval. A second deterministic
engine runs the **green-claims gate**: it classifies the environmental claims a piece of
copy makes and decides whether the substantiation evidence the brand holds actually carries
them. The LLM only narrates; it never decides whether a rule passes, whether a green claim
is substantiated, or what the coverage figure is.

Built ports-and-adapters on the Gemini Enterprise Agent Platform, and deliberately
**generic**: banking and online retail are both first-class configurable verticals, and
Japan, Australia and Singapore are first-class markets. Banking's financial-promotion rules
are one configured rule set among others, not the only frame.

## Why it is trustworthy

- **The deterministic engine is the heart.** Whether an asset complies is decided by pure,
  replayable code (`domain/rule_engine.py`), not by a model. An auditor can re-run it and a
  test can pin it. The LLM (`LlmPort`) only turns the already-decided findings into prose.
- **Every finding carries a `Citation`** to the exact rule (and its underlying authority),
  so a review is fully auditable.
- **Green claims are gated on evidence, not on wording.** The coverage engine
  (`domain/coverage_engine.py`) matches each detected claim against the evidence on file and
  fails closed on evidence that is expired, too old for the jurisdiction, self-declared where
  independent verification is demanded, or filed under a different claim. A green claim never
  publishes on the agent's say-so, even when the evidence fully carries it.
- **The marketing maker-checker gate.** Any non-compliant review sets
  `requires_human_review` and starts with a `PENDING` `ApprovalRecord`: the agent proposes,
  a qualified compliance officer disposes before the asset may run.
- **No lock-in (ports and adapters).** Switching the whole managed stack to on-prem is a
  one-line `profile` change; nothing in `domain/` moves.

## Generic, multi-vertical, APAC

- **Verticals**: `banking` and `online_retail`, each with its own seeded rule set and
  taxonomy. Nothing bank-only is hard-coded in the engine.
- **Markets**: `JP` (asia-northeast1), `AU` (australia-southeast1), `SG` (asia-southeast1),
  with per-market residency region and locales (ja + en) as config + seed, validated at
  deploy. A region outside the per-market allow-list is rejected.

## Profiles

| profile  | what it is | Google Cloud SDK |
| -------- | ---------- | ---------------- |
| `local`  | a WORKING, offline, deterministic stack (SQLite FTS5 rule KB) | none |
| `gcp`    | the managed stack: Gemini API File Search rule KB, Gemini narration, Model Armor, Cloud Logging WORM, Cloud Trace, Gen AI eval | `[gcp]` extra |
| `onprem` | fail-fast `NotImplementedError` placeholders (the sovereign migration target) | none |

`local` is the dev/test/CI default and needs no `google-cloud-*` packages.

## Quick start (offline, no cloud)

```bash
make install          # 3.14 venv + [dev] only (no GCP SDK)
make gate             # ruff + ruff format + mypy + pytest + eval, all green
make smoke-local      # review a non-compliant banking asset via the CLI
make demo             # run the review flow + render the static audit-first HTML
```

Review a banking asset and an online-retail asset directly:

```bash
MKT_GOV_PROFILE=local mkt-gov review \
  "Get guaranteed returns of 4.10% with zero risk-free worry!" -m SG -v banking

MKT_GOV_PROFILE=local mkt-gov review \
  "Lowest price guaranteed on everything!" -m AU -v online_retail --type offer -f discount_pct=90
```

Assess an asset's green claims against the substantiation evidence on file:

```bash
MKT_GOV_PROFILE=local mkt-gov substantiate \
  "Bank with a carbon neutral balance sheet. Offsets are disclosed in our report." \
  -m AU -v banking --as-of 2026-08-05
```

List the rule set in force for a market and vertical:

```bash
MKT_GOV_PROFILE=local mkt-gov rules -m JP -v online_retail
```

## The hard gate (green before done)

Run in a fresh `[dev]`-only 3.14 venv (NO `google-cloud-*`):

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -m 'not integration' -q
python eval/run_eval.py
```

## Layout

```
src/marketing_compliance_gate/
  domain/            pure hexagon core (no SDK/framework imports)
    models.py        Vertical, Market, Rule, RuleSet, MarketingAsset, ClaimFinding,
                     ConsentCheck, ApprovalRecord, Review, Citation
    rule_engine.py   the deterministic claim/permission/brand/consent checker (the heart)
    coverage_engine.py  the deterministic green-claim detector + substantiation coverage
    services.py      ReviewService: the orchestrator + the maker-checker gate
    substantiation.py   SubstantiationService: the green-claims gate, tenant-scoped
  ports/             the Protocols (RuleProvider, Llm, Guardrail, Audit, Tracer, Eval, ...)
  adapters/          gcp / local / onprem / platform families, one per port
  rulepacks/         the jurisdiction green-claim rule pack (config, not code)
  api/               thin FastAPI boundary (port 8105)
  cli/               the `mkt-gov` Typer CLI
config/settings.yaml profile, vertical, market, per-market regions, port -> adapter bindings
eval/                the Hrz4 offline promotion gate + golden review and green-claim
                     datasets + rubrics
scripts/             offline demo, static HTML renderer, presenter demo server
ui/                  thin Next.js console (compiles with `npm run build`)
```

## Ports (the hexagon boundary)

`RuleProviderPort` (the rule KB), `EvidenceStorePort` (tenant-scoped green-claim
substantiation evidence), `ConsentStorePort` (the tenant-scoped consent and preference
store), `LlmPort`, `GuardrailPort`, `AuditSinkPort`, `ObservabilityTracerPort`,
`EvaluationGatePort`, `AgentRegistryPort`, `ToolCatalogPort`, `IdentityPort`
(server-verified end-user identity), `ReviewRouterPort` (the Hrz7 hand-off).
The contract test proves the `local` and `onprem` families satisfy every Protocol with no
Google Cloud SDK installed, and that the port map and the settings bindings cannot drift
apart.

## The green-claims gate (anti-greenwashing)

Environmental claims are the highest-exposure copy a bank or retailer publishes, so Mkt6
treats them as their own gate:

- **Green-claim rules** are a `RuleKind.GREEN_CLAIM` set in the jurisdiction rule pack
  (`src/marketing_compliance_gate/rulepacks/green_claims.yaml`): forbidden phrases, required
  disclosures and required fields for JP, AU and SG, each stating the regulator instrument
  it comes from (ACCC environmental-claims guidance, the Australian Consumer Law, ASIC INFO
  271, MAS Circular CFC 02/2022, the Singapore Code of Advertising Practice, the Consumer
  Protection (Fair Trading) Act, Japan's Act against Unjustifiable Premiums and Misleading
  Representations and the Consumer Affairs Agency's environmental-labelling guidance, the
  FSA's ESG-fund supervisory guidelines, and ISO 14021).
- **Substantiation coverage** is decided by pure code. Each detected claim needs the
  evidence kinds its jurisdiction requires, within that jurisdiction's evidence-age limit
  and independence expectation; coverage is satisfied kinds over required kinds, and the
  worst claim decides the asset. `as_of` is an explicit input, so a past assessment replays.
- **The pack is config (B4).** Forbidden wording, required evidence, age limits and the
  independent-verification switch live in the YAML pack. Point `green_claims.pack_path` at
  your own file to run your own policy without touching the engine.
- **Evidence is tenant-owned.** Reads are authorized against the verified `Principal`'s
  tenant: a cross-tenant read is a `403`, never a `404`, and the denial is audited.

```
POST /v1/substantiation      -> SubstantiationAssessment (your tenant's evidence only)
GET  /v1/evidence?asset_id=  -> the evidence your tenant holds for an asset
GET  /v1/evidence/{id}       -> one record; another tenant's record is 403
```

## The consent and preference store

Mkt6 already decided whether an ASSET carries the marketing permission its market requires
(a `CONSENT`-kind rule with the `CONSENT_REQUIRED` check). The consent and preference store
answers the other half: may we contact THIS data subject, for THIS purpose, on THIS channel,
right now? It is built here rather than in a separate service because the two halves share
the rule engine and the rule citations.

- **What it holds.** Consent records (per purpose, with a lawful basis and an explicit
  validity window), channel preferences, tenant frequency caps, suppression entries, and the
  recorded sends a cap counts.
- **The decision is pure code.** No model is in the path at all: a decision is a legal
  position about a person, so it is replayable byte for byte from one snapshot at one
  `as_of`, and its explanation is generated deterministically. The decision id is a content
  hash of the question and the answer, so a send that quotes it can be reconciled against a
  replay of the store months later.
- **Unknown fails closed.** No record, an explicitly unknown record, a grant that has not
  started or has expired, a grant still pending confirmation, a channel the subject never
  expressed a preference for: every one of them denies. Suppression is checked first and
  outranks everything.
- **It reuses the rule engine.** The engine resolves which purposes the subject's stored
  records grant at `as_of` and hands that set to `RuleEngine.consent_checks_for`, the same
  code path the asset review uses, so a denial carries the market rule's own citation.
- **Recording a grant is the gated write.** A withdrawal, an opt-out or a suppression applies
  immediately: those only ever narrow what may be done to a person. A grant captured with
  proof (an explicit or soft opt-in, a named source, a locator for the captured statement) is
  stored granted. A grant asserted with none of that is stored pending review, grants nothing,
  and is routed to the Hrz7 maker-checker console (rule R8) until a checker confirms it.

```
POST /v1/consent/decision                  -> the deterministic, cited decision
GET  /v1/consent/subjects/{subject_id}     -> your tenant's snapshot for one subject
POST /v1/consent/records                   -> store a record (a grant may land pending)
GET  /v1/consent/records/{id}              -> one record; another tenant's record is 403
POST /v1/consent/records/{id}/confirm      -> the checker half of the grant gate
POST /v1/consent/preferences               -> store a channel preference
POST /v1/consent/suppressions              -> store a suppression entry
POST /v1/service/consent/decision          -> the S2S intake (consent-preference-kit)
POST /v1/service/consent/sends             -> record a contact, so the cap counts it
```

The `/v1/service/...` pair authenticates the CALLING SERVICE rather than an end user, which
is the only way a proactive outreach system with no user in the loop can ask at all, so those
two take the tenant in the body. That is the same trust model Hrz7's own service intake uses.
Clients talk to them through
[`consent-preference-kit`](https://github.com/portable-genai/consent-preference-kit).
In `gcp`, the caller presents a short-lived Google-signed ID token for the reviewed
`MKT6_S2S_AUDIENCE`; the verifier accepts only the exact service-account emails in
`MKT6_S2S_ALLOWED_CALLERS`. Terraform configures that custom audience, grants only Mkt5's
Workload Identity `roles/run.invoker`, and injects the same audience/caller policy into the
application. Local remains a zero-cloud, shared-secret-capable contract test path; a static
bearer is never placed in managed Terraform state.

## Embeddable, secure UI (identity + embedding)

The console is a portable micro-frontend: it drops into a host app same-origin (behind a
reverse-proxy sub-path) or runs standalone, and the backend verifies identity server-side
instead of trusting a client-supplied `actor`. An `IdentityPort` resolves a verified
`Principal` per request (a seeded dev persona in `local` mode via the `X-Dev-Persona`
header, or the GCP IAP-injected assertion in secure mode), and that principal's subject is
the audit actor. `POST /v1/review` takes no `actor` field. The embedding-surface controls
(per-tenant CORS allowlist, CSP `frame-ancestors`) and the three deployment shapes are
described in [`docs/embedding-and-identity.md`](docs/embedding-and-identity.md).

Config knobs: `MKT_GOV_PROFILE` (local | gcp | platform | onprem), `MKT_GOV_IAP_AUDIENCE`,
`MKT_GOV_CORS_ORIGINS`, `MKT_GOV_FRAME_ANCESTORS`, and the UI's `NEXT_PUBLIC_API_BASE` /
`NEXT_PUBLIC_BASE_PATH` / `NEXT_PUBLIC_EMBED`.
