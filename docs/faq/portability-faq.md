# Portability FAQ

For architecture, cloud-governance, and exit-planning teams. The claim this repo makes is
"no vendor lock-in, demonstrably" (General Principle P-02 / P-12), and it is designed to be
*shown*, not asserted. Cross-references: [`ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`docs/onprem-migration.md`](../onprem-migration.md), [`DEMO.md`](../../DEMO.md).

### What does "portable" actually mean here?

Two axes, each with a rehearsed exit: **compute** (the whole stack migrates by a one-line
profile change, no `domain/` edits) and **data** (the audit trail exports in an open,
documented format and reloads elsewhere with the hash chain re-verified). Identity resolves
across hosts by an `IdentityPort` swap rather than a rewrite (see
[security-faq.md](security-faq.md)).

### How does the profile switch work?

The pure-domain core speaks only to `typing.Protocol` **ports**; four **adapter families**
implement them, and `config/settings.yaml` binds one adapter per port per profile. Setting
`MKT_GOV_PROFILE` (or `profile:` in the settings) rebinds the entire stack:

- `local`: a WORKING offline stack (SQLite FTS5 rule KB that self-seeds, deterministic rule
  engine and LLM, hash-chained audit). No Google Cloud SDK. What dev / test / CI run, but it
  must be named: unset is no choice, not a silent `local`.
- `gcp`: the managed stack (Gemini API File Search rule KB, Gemini narration, Model Armor,
  Cloud Logging WORM, Cloud Trace, Gen AI eval).
- `platform`: thin HTTP clients delegating to the sibling horizontal-platform and
  de-risking services.
- `onprem`: fail-fast `NotImplementedError` placeholders that still satisfy every Protocol
  (the sovereign-exit target).

No `domain/` code changes across any of these. The contract test
(`tests/contract/test_port_parity.py`) proves both the `local` and `onprem` families
construct with a single `Settings` arg and satisfy all nine ports with no cloud SDK
installed, so deleting or mis-binding a port fails CI.

### The domain is not split into a kernel and a vertical, does that hurt portability?

No. The deterministic engine (`domain/rule_engine.py`), the neutral types in
`domain/models.py` (`Citation`, `AuditEvent`, `LlmRequest`, `GuardrailVerdict`, `EvalReport`,
`Severity`) and the port layer are already vertical-neutral in practice; a fork keeps them
and replaces the rule seed and the marketing artifacts. Making that boundary an explicit
`kernel.py` module is a documented enhancement (tracked as the A7 PARTIAL in the practices
audit), not a portability blocker: nothing in the core imports a cloud SDK or a framework
today.

### How do we get our data out?

The audit trail exports to JSON Lines via the shared `hex-service-kit` audit log
(`export` / `restore`), one hash-chained record per line, and reloads into a fresh store
with the chain re-verified line by line (`verify_chain()`). Reviews and their findings
serialize the same way through `domain/serialization.py` (`to_jsonable`). The exit story for
the audit trail is "copy the JSONL file", not "migrate a product".

### Is on-prem / sovereign deployment real or aspirational?

The `onprem` adapters are deliberate fail-fast placeholders (they raise
`NotImplementedError`) that nonetheless satisfy every Protocol and construct with a single
`Settings` arg, so the *interface contract* for a sovereign migration is proven and enforced
by CI today. The actual on-prem implementations are the migration work, scoped in
[`docs/onprem-migration.md`](../onprem-migration.md). This repo is not the sovereign-exit
*planner* (that is the sibling **Rgc9** `operational-resilience-mapping`, module
`domain/concentration_exit/`); this repo is one of the systems whose exit that planner
reasons about.

### Does residency compromise portability?

No: residency is a deploy-time pin (a per-market region, the Org Policy resource-location
allowlist, CMEK, VPC-SC), and portability is the ability to change *where* the stack runs by
configuration. They are orthogonal. The region is validated per market to fail fast, and a
new market or region is a `markets:` + tfvars change, not a fork. Residency enforcement infra
overlaps with the sibling **Rsk3** `architecture-validator` (`domain/residency/`, a CI gate
for region violations), which a fork should run rather than re-implement.

### What is NOT yet portable?

The `platform`-profile delegates for the guardrail, rule KB, audit and registry ports are
phase stubs today (only the Hrz4 eval client and the Hrz7 review router are live), so a full
platform-profile deployment is not yet exercised end-to-end. The `local` and `gcp` review
pipeline, and the `onprem` parity contract, are.
