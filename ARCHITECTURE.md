# ARCHITECTURE: Mkt6 Marketing Compliance and Brand Governance

## Hexagonal ports and adapters

Mkt6 is built as a hexagon: a pure-stdlib **domain core** surrounded by typed **ports**, with
interchangeable **adapter families** selected by a single profile switch. The domain has
zero dependency on any framework, SDK or cloud. That is what makes it testable offline,
portable across vendors, and honest about its boundaries.

```
                +-------------------- driving (inbound) --------------------+
                |  cli/main.py        api/app.py         agent/ (A2A)       |
                +----------------------------+------------------------------+
                                             v
                          +------------------------------------+
                          |  domain/  (PURE, stdlib only)      |
                          |    models.py                       |
                          |    rule_engine.py    (the engine)  |
                          |    coverage_engine.py (green claims)|
                          |    services.py       (orchestr. +  |
                          |                       maker-checker)|
                          |    substantiation.py (green gate)  |
                          +------------------+-----------------+
                                             v  ports (typing.Protocol)
   RuleProvider  EvidenceStore  Llm  Guardrail  Audit  Tracer  Evaluation  Registry
   ToolCatalog  Identity  ReviewRouter
                                             v
       +---------------+----------------+-------------------+-----------------+
       | adapters/gcp  | adapters/local | adapters/platform | adapters/onprem |
       | (lazy SDK)    | (offline)      | (HTTP to Hrz1..Hrz5)  | (fail-fast stub) |
       +---------------+----------------+-------------------+-----------------+
```

## The four profiles

| Profile | Role | Backing |
|---|---|---|
| `gcp` | primary, managed | Gemini API File Search (rule KB), Gemini narration, Model Armor, Cloud Logging WORM, Cloud Trace, Gen AI eval. SDK imports are lazy. |
| `local` | dev / test / CI default | a WORKING offline stack: a deterministic SQLite FTS5 rule KB seeded per (market, vertical), a deterministic schema-driven LLM narrator, a heuristic guardrail, append-only audit, no-op tracer, in-process registry / tool-catalog, the offline eval gate. SDK-free and seedable. |
| `platform` | shared-platform reuse | thin HTTP clients to the shared Hrz1 guardrail, Hrz2 KB, Hrz3 registry, Hrz4 eval (a real client: `POST /v1/evaluations` + `/v1/gate`, `mkt6-compliance` bundle), Hrz5 audit. |
| `onprem` | portability proof | fail-fast `NotImplementedError` stubs satisfying the same Protocols. |

Switching the whole backend is a one-line `profile` change in `config/settings.yaml` (or
the `MKT_GOV_PROFILE` env var). The contract test proves the local and onprem families
satisfy every port Protocol, so the profiles never drift.

## The two deterministic engines

`RuleEngine` answers "does this asset break a rule?" and `CoverageEngine` answers "does the
evidence on file carry the environmental claims this asset makes?". They are separate because
the second question needs data the first does not have (the tenant's substantiation evidence)
and produces a different artifact (a coverage figure per claim, not a pass or fail per rule).
They compose: the substantiation service uses the coverage engine to work out which claim
categories are in play, then hands the applicable green-claim rules to the SAME generic
`RuleEngine`. The rule engine never learned what a green claim is.

Green-claim rules are kept OUT of the `RuleProviderPort` rule set on purpose. Most of them are
conditional (a "disclose the offsetting basis" rule is meaningless on an asset that says
nothing about carbon), and a conditional rule evaluated unconditionally would fail every asset
in the catalog. Applicability is data on the rule, and the selection happens before the engine
runs.

## Why the engine is deterministic

Whether a marketing asset complies drives real legal and brand exposure. It must be
auditable: a compliance officer has to be able to re-run a review and get the same answer,
and a test has to be able to pin it. So the rule engine is a pure, frozen domain service
with no LLM, clock, randomness or I/O inside; each `CheckType` is a transparent predicate
over the asset, and the rule data is config + seed. The LLM's job is narrow: narrate the
already-decided findings into prose. It never decides whether a rule passes.

The same argument holds, and holds harder, for the green-claims gate. Whether a carbon-neutral
claim is carried by the evidence on file decides whether a bank publishes a claim a regulator
can act on. So the classification, the per-claim coverage and the verdict are pure code with an
explicit `as_of` date (no clock read, so a past assessment replays exactly), and the model is
handed the finished result and asked only for a paragraph. `test_substantiation_pipeline.py`
includes a hostile narrator that asserts everything is fully substantiated, and proves the
verdict and the coverage number are unmoved.

See the `deterministic-domain-service` skill in `.agents/skills/`.

## Generic, multi-vertical, APAC by construction

* `Vertical` (banking, online retail) and `Market` (JP, AU, SG) are closed `StrEnum`s: the
  SET of supported markets and verticals is a fixed, validated catalogue by design, and the
  active values in play are chosen in settings.
* The engine takes the asset and rule set as parameters; it never branches on a specific
  market or vertical. All locale/market/vertical specificity lives in the seeded rule sets.
* Per-market residency region, locales and the rule sets come from `MARKET_PROFILES` plus
  the `markets:` overrides in `config/settings.yaml` and the local seed
  (`adapters/local/_seed.py`), which is keyed by `(market, vertical)` and spans both
  verticals across all three markets, with disjoint banking and retail rule sets.

Adding or tuning a RULE for a supported market/vertical is a config + seed change, not a
code change. The set of markets and verticals is itself a closed `StrEnum` by design, so
introducing a brand-new market or vertical also extends that enum: a small, localized code
change, not a rule-engine change.

## Tenant isolation on substantiation evidence

The rule KB is shared reference data, but substantiation evidence is not: it is a brand's own
emissions inventories, offset retirement records, test reports and fund disclosures. That makes
object-level authorization real here, and it is fail-closed and server-verified:

* the tenant comes from the `Principal` the `IdentityPort` resolved, never from the request
  body, a query parameter or a tool argument;
* a principal with no tenant reads nothing;
* `EvidenceStorePort.list_for_asset` takes the tenant and filters in the store (SQL `WHERE` in
  the local adapter, a server-side `where` in Firestore), and the domain re-filters what comes
  back;
* `EvidenceStorePort.get` is deliberately an unfiltered fetch by id, so the tenant comparison
  lives in ONE place, the domain service, and every driving adapter inherits it. A mismatch is
  a `403` with an audit record, not a `404` that pretends the record does not exist.

The green-claims gate is therefore not exposed as an ADK tool: a tool argument is a
client-asserted value, and there is no verified tenant on that path.

## The consent and preference store

The store lives inside Mkt6 rather than in a service of its own, and the reason is the rule
engine. Mkt6 already models consent as a `CONSENT`-kind rule with a `CONSENT_REQUIRED` check,
evaluated by the deterministic `RuleEngine` into a `ConsentCheck` carrying the market rule's
citation. A separate consent service would either duplicate that vocabulary or diverge from
it, and a consent denial that cited nothing would be worth much less to a compliance officer
than one that cites the same instrument the asset review cites.

So the consent module feeds the existing engine rather than replacing it.
`ConsentEngine.granted_purposes` resolves, from the subject's stored records at an explicit
`as_of`, which purposes are actually granted, and hands that set to
`RuleEngine.consent_checks_for`. That method is the shared core; `consent_checks(asset, ...)`
is now a thin wrapper over it that passes the asset's declared consents. One engine, one set
of citations, two callers asking different questions about the same idea.

The decision itself has no model in it at all. It is assembled from ONE snapshot of the
subject's state (records, preferences, suppressions, caps read together, because a decision
built from reads taken at different instants is not replayable and could miss a withdrawal
landing between two of them) plus the recorded sends in the selected cap's window. The
outcome is defined as "no attached reason is in `DENYING_REASONS`", which keeps the fail-open
surface to a single set literal rather than a chain of branches, and no step short-circuits,
so the audit record says all of why rather than the first why.

The `PENDING_REVIEW` status is what makes the maker-checker gate on grants work without a
second store. A grant asserted with no captured proof is stored in that state, the engine
reads it as not granted, and the record is routed to Hrz7 through the same `ReviewRouterPort`
that carries escalated reviews and green-claim assessments. Withdrawals, opt-outs and
suppressions are never gated: they only ever narrow permission, so delaying them would be the
unsafe direction.

Tenant isolation is the same posture as the evidence store, method for method: `snapshot`
filters on the tenant in the store, `get_record` is an unfiltered fetch whose tenant
comparison lives once in the domain service, and a mismatch is a `403` with an audit record.
The engine repeats the check on the snapshot it is handed, so a service that fetched the
wrong snapshot cannot get an answer out of it either.

## Data residency

Each market's residency region is validated and selectable at deploy via
`Settings.market_profile().region` (JP `asia-northeast1`, AU `australia-southeast1`,
SG `asia-southeast1`). The GCP adapters resolve and validate the region before any call
(`adapters/gcp/_region.py`), and a region outside the per-market allow-list is rejected. The
WORM audit bucket is regional.

## Jurisdiction policy as configuration

The green-claim rule pack (`src/marketing_compliance_gate/rulepacks/green_claims.yaml`) is the one
place a jurisdiction's green-marketing policy lives: the phrases that classify a claim, the
evidence each category requires, how old that evidence may be, whether it must be independently
verified, and the forbidden or required wording, each naming the regulator instrument behind
it. The engines take the pack as a parameter and know no jurisdiction by name, so an adopter
runs its own policy by pointing `green_claims.pack_path` at its own file. A malformed or
missing pack is a hard error, because a green-claims gate with an empty pack is worse than no
gate at all.

## Auditability

Every review is written to the audit sink as an immutable record (`Decision.ESCALATED` when
human review is required, maker-checker). Every finding carries its `Citation`s to the rule
and its authority, and the review serialises to plain JSON for an explainable, audit-first
view (see the `audit-first-demo` skill and `DEMO.md`).

## Identity and the embeddable UI

Authentication is a port like any other. `IdentityPort.resolve(RequestContext) -> Principal`
verifies the end-user server-side: the `local` adapter maps the `X-Dev-Persona` header to a
seeded dev persona (no IdP, offline demos and tests), the `gcp` / `platform` adapter verifies
the GCP Identity-Aware-Proxy assertion (`x-goog-iap-jwt-assertion`, audience
`MKT_GOV_IAP_AUDIENCE`), and the `onprem` adapter is a fail-fast placeholder for the client
IdP. The FastAPI boundary resolves the `Principal` on `POST /v1/review` (a 401 if it cannot),
uses `principal.actor` as the audit actor, and carries NO `actor` field in the request body,
so a client cannot spoof identity. The Next.js console is an embeddable micro-frontend
(`NEXT_PUBLIC_BASE_PATH` sub-path mount + `NEXT_PUBLIC_EMBED` chrome-off), and the API sets a
per-tenant CORS allowlist (`MKT_GOV_CORS_ORIGINS`) plus a CSP `frame-ancestors`
(`MKT_GOV_FRAME_ANCESTORS`) header. Full deployment shapes and the client integration guide
are in [`docs/embedding-and-identity.md`](docs/embedding-and-identity.md).
