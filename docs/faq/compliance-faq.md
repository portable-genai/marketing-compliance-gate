# Compliance FAQ

For compliance, brand, and model-risk teams assessing the repo's regulatory posture.
Cross-references: [`COMPLIANCE.md`](../../COMPLIANCE.md) (the full principle-to-control map
and the MAS regulator crosswalk appendix), [`SPEC.md`](../../SPEC.md).

### Is this making marketing-approval decisions autonomously?

No. `marketing-compliance-gate` **is** the maker-checker gate (P-06). The deterministic rule engine produces a
documented, replayable compliance verdict; a qualified human checker disposes. A non-compliant
review sets `requires_human_review`, starts with a `PENDING` `ApprovalRecord`, and does not
execute until a human approves or rejects (`ReviewService.approve`). The agent is the maker
and has no approve tool, so it cannot approve its own reviews. The strongest finding severity
raises the review to dual control; it never lowers the bar.

### What does the review actually check, per market?

Advertising claims and disclosures, brand guidelines, and marketing consent against the
per-market, per-vertical rule set: SG (ASAS / consumer-protection), AU (ACCC / ASIC
advertising and fair-trading), JP (fair-trade / premiums-and-representations), across the
`banking` and `online_retail` verticals. Each rule carries a `Citation` to its authority, so
a finding is traceable to the rule it fired. The rule set is a versioned reference source
retrieved from the `enterprise-knowledge-base` governed KB (or the local seed offline).

### How is customer PII and consent handled?

`marketing-compliance-gate` reviews marketer-authored asset copy and rule text; it does **not** ingest, index or
store customer PII or per-customer consent records. `MarketingAsset.granted_consents` is a
tuple of consent-purpose labels (which permissions the campaign asserts it holds), and the
rule engine's `ConsentCheck` verifies the asset's asserted consents against what the rule
requires, it is not a customer-data store. So there is no PII de-identification boundary in
this repo by design (C2 / C3 / C4 are N-A in [`docs/practices-audit.md`](../practices-audit.md)).
The runtime guardrail itself is the sibling `agent-guardrail-gateway`, consumed on every review.

### How is the work auditable / reproducible?

Every review and every approval writes an immutable WORM `AuditEvent` with the decision and
the citation set (P-07). Every finding carries a `Citation` to the rule it fired (P-10). The
outcome is decided by the deterministic engine, so an auditor can recompute any review from
the same asset and rule set. The enterprise WORM audit system is `agent-observability`; the in-repo
hash-chained store is the offline / local stand-in (see [security-faq.md](security-faq.md)
for its exact tamper-evidence limits).

### What is the model-risk story?

An offline eval gate (`eval/run_eval.py`, `--mode smoke | gate`) scores finding accuracy,
review safety and citation accuracy against a golden set, failing the build below threshold
(P-08); gate mode refuses to run outside `MKT_GOV_PROFILE = platform | gcp`, so `model-quality-gate` owns
promotion while an offline smoke guards every merge. The two strict (0.99) safety metrics are
built on an **independent golden oracle**, not the product's own output: `review_safety`
reads the golden `expected_outcome` (so an under-flagging regression, a real violation
auto-passed, goes red), and `citation_accuracy` requires each finding to cite its own active
rule. The gate runs `assert_each_can_go_red` per market before scoring, so a refactor back to
a self-referential metric fails the build (`tests/unit/test_eval_not_falsely_green.py`). A
fork must rebuild the golden set for its own markets, or the gate measures the wrong thing.

### Which regulators does this map to?

`COMPLIANCE.md` maps the internal P-01..P-13 / R1..R8 controls to concrete code, plus an
**adopter-owned regulator crosswalk appendix** with a MAS (Singapore) reference mapping as the
template (advertising / consumer-protection per market, MAS FEAT accountability for the
maker-checker four-eyes control, MAS TRM for auditability, outsourcing / cloud for residency
and exit). To add other regulators, copy the appendix table, swap the regulator-reference
column, and re-review with local counsel, the `marketing-compliance-gate`-control column is stable across
regulators. At scale, the sibling `compliance-advisory` and its control-mapping
module (`domain/control_mapping/`) generate and maintain these crosswalks; a large estate
should integrate them rather than hand-maintain the table.

### Is data residency enforced?

Yes, at deploy time, per market: a residency region selected from an allowlist (JP
`asia-northeast1`, AU `australia-southeast1`, SG `asia-southeast1`, default
`asia-southeast1`), validated to fail fast, with regional endpoints, a
`gcp.resourceLocations` Org Policy allowlist, regional CMEK, and a dry-run-first VPC-SC
perimeter (P-03, P-09).

**Agent Search is the one service that follows none of it:** it serves only `global` / `us` /
`eu`, so the retrieval corpus defaults to `global` and is unlocated. `us` or `eu` confines it to
one jurisdiction where an obligation bites, and the location Org Policy must be wide enough to
permit the choice. It is recorded in [`COMPLIANCE.md`](../../COMPLIANCE.md) rather than
absorbed.

The residency-violation CI gate is the sibling `architecture-validator` (`domain/residency/`); the exit / concentration-risk plan is `operational-resilience-mapping` (`domain/concentration_exit/`). This repo enforces residency in
its own infra and is one of the systems those tools reason about.

### Can we run it against real marketing or customer data today?

Not without your own legal, brand, security and model-risk sign-off. Every fixture and the
rule seed are obviously-fictional (`example.test` URLs, FICTIONAL names), and the docs state
throughout that this is a reference build. The adoption checklist
([`docs/ADOPTING.md`](../ADOPTING.md) §6) lists the steps, replace the rule seed with your own
authorities, own the numbers, wire your IdP, rebuild the eval golden set, that must precede
any live-data use.
