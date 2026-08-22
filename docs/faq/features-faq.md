# Features FAQ

For product, compliance, and delivery teams: what this agent does, what is deterministic vs
LLM, and, importantly, where its responsibilities **stop** and a sibling catalog system
takes over. Cross-references: [`README.md`](../../README.md), [`DEMO.md`](../../DEMO.md),
[`COMPLIANCE.md`](../../COMPLIANCE.md).

### What does Mkt6 actually produce?

A cited compliance **Review** of a marketing asset before it runs. From a `MarketingAsset`
(a Campaign, Creative or Offer, its copy plus structured fields) and the per-market,
per-vertical rule set in force, it produces: a compliant / non-compliant **outcome**, a
`ClaimFinding` per rule that fired (with severity), the `ConsentCheck` results, and an
`ApprovalRecord`, every finding carrying a `Citation` to the rule (and its underlying
authority), with a WORM `AuditEvent` for the review and each approval.

### What is deterministic vs done by the LLM?

The consequential decision is **deterministic and replayable** (pure stdlib, unit-tested):
`domain/rule_engine.py` decides which rules pass or fail, the severity, and the outcome from
pure predicates over the asset and its active rule set (`services.py` `_outcome =
any(f.failed)`). The LLM only **narrates** the already-decided findings into prose; it never
decides whether a rule passes. An auditor can recompute every outcome without the model, and
`tests/unit/test_rule_engine.py` / `test_review_pipeline.py` pin the expected failing rule
ids.

### Is anything auto-approved?

No. Every clear or block recommendation sets `requires_human_review` and starts with a
`PENDING` `ApprovalRecord`; nothing auto-executes, and disposition is a human checker action
(`ReviewService.approve`). The strongest finding severity can raise the review to dual
control; it never lowers the bar. The ADK agent exposes only the
`review_marketing_asset` maker tool, the checker half (`approve`) is deliberately **not** a
tool, so the agent cannot approve its own reviews and four-eyes stays server-side.

### Which capabilities does this repo own vs integrate from the catalog?

This is one system in a catalog of composable GRC systems. It **owns** the marketing-review
domain logic (the rule engine, the maker-checker gate, the review output). It **integrates**
several cross-cutting concerns owned by sibling platform systems, do not rebuild these in a
fork:

| Concern | Owned by (catalog id / repo) | Mkt6's role |
|---|---|---|
| Runtime guardrail: prompt-injection / unsafe-output screening | **Hrz1** `agent-guardrail-gateway` | consumes it on every review (input + output, pipeline and model boundary) |
| Governed rule / knowledge base with citations | **Hrz2** `enterprise-knowledge-base` | retrieves the per-market, per-vertical rule set from it |
| Agent registry, versioning, discovery | **Hrz3** `agent-registry` | publishes its A2A AgentCard for discovery |
| AI-quality / eval / model-risk promotion gate | **Hrz4** `model-quality-gate` | its eval metrics gate promotion; the offline gate mirrors it |
| Observability + immutable WORM audit | **Hrz5** `agent-observability` | writes audit events to it; traces spans through it |
| Human-review & maker-checker console | **Hrz7** human-review console | routes a non-compliant review's escalation to it via `review-kit` |
| Regulatory Q&A / control checklists | **Rsk1** `compliance-advisory` | consumes it for regulatory compliance checks |

So the guardrail, rule KB, audit sink, eval platform and review console are *dependencies*,
not features of this repo.

### How does this relate to the other marketing systems in the catalog?

Mkt6 is the marketing tier's **maker-checker gate**. The other marketing systems produce
customer-facing output and route it through Mkt6 rather than re-implementing the gate:
**Mkt1** market-intelligence, **Mkt2** campaign-planning, **Mkt3** brand-safe creative
studio, **Mkt4** performance-marketing-optimisation / attribution, **Mkt5** next-best-action
recommendations. Mkt6 is the catalog's named enforcer of General Principle P-13 (fair,
consented, compliant marketing) and dependency rule R7 for that tier. Check
[the organization's repository index](https://github.com/portable-genai) before building a
review capability that already has a home here.

### Is this bank-only? Does it work outside financial promotions?

It is deliberately generic. `banking` and `online_retail` are both first-class configurable
verticals, and JP, AU and SG are first-class markets, each as config + seed (its residency
region, locales and rule set). Banking's financial-promotion rules are one configured rule
set among others, not the only frame. Adding a market or vertical is a config + seed change,
not a code change (the engines do not branch on market or vertical).

### How do I see it working?

`make demo` runs the review flow and renders static audit-first HTML panels; `make
demo-server` runs a presenter-controlled offline server; `make smoke-local` reviews a
non-compliant banking asset via the CLI. Everything runs on `MKT_GOV_PROFILE=local` with
synthetic, fictional data, no cloud and no API key. Note the demo has no unattended CI
self-test yet (F2 PARTIAL in [`docs/practices-audit.md`](../practices-audit.md)), so re-run
it after a refactor that touches the review path.
