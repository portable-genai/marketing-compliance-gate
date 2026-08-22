# COMPLIANCE: Mkt6 Marketing Compliance and Brand Governance

This maps every General Principle (P-01..P-13) and dependency rule (R1..R8) to a concrete
control in **this** repo. Where a principle does not apply to Mkt6, it is marked **n/a** with
the reason. Mkt6 **is** the marketing maker-checker gate: it is the system that enforces P-13
and rule R7 for the rest of the marketing tier (Mkt1..Mkt5), so its load-bearing controls are
the deterministic rule engine, provenance, maker-checker and audit.

> The rule, asset and consent data in `tests/`, `eval/` and the local seed is **fictional**.
> This build is a reference piece and is **not** intended for live use without your own legal,
> security and model-risk sign-off.

---

## General Principles

| # | Principle | How Mkt6 implements it | Evidence |
|---|-----------|----------------------|----------|
| **P-01** | Managed-first, minimal surface | Only the managed services the pinned stack uses are enabled; the agent is hosted on Agent Runtime | `infra/terraform/apis.tf`, `agent/root_agent.py` |
| **P-02** | No vendor lock-in (ports and adapters) | Domain depends only on `Protocol` ports; a profile switch rebinds adapters with no domain change. The `local` family proves the same domain runs entirely off-cloud (deterministic rule engine and LLM, in-memory rule sets, no Google Cloud SDK) | `ports/`, `config.py`, `adapters/local/*`, `adapters/onprem/*` |
| **P-03** | Data residency (in-country) | Region selected at deploy from a residency allowlist, with per-market overrides (JP / AU / SG), validated to fail fast; regional endpoints; `gcp.resourceLocations` Org Policy; VPC-SC perimeter | `config/settings.yaml` (`markets`), `infra/terraform/variables.tf`, `org_policy.tf`, `vpc_sc.tf` |
| **P-04** | Minimise data to the model | Mkt6 reviews marketing assets and rule text, no customer PII; the model-boundary callback still guardrail-screens every prompt and response, and spans capture no content | `agent/callbacks.py`, `domain/services.py` |
| **P-05** | Grounding over fine-tuning | Reviews are grounded in the per-market, per-vertical rule set retrieved from the Hrz2 KB; the model narrates, it is not trained on rules | `ports/rules.py`, `domain/services.py` |
| **P-06** | Human-in-the-loop / maker-checker | Mkt6 is the maker-checker gate itself: every regulated-claim review, clear or block, sets `requires_human_review=True` and its `ApprovalRecord` starts PENDING until a human checker approves or rejects (`approve`); the agent is the maker and never approves. Any environmental claim is likewise checker-gated. Per rule R8 these items route to Hrz7 via `review-kit`, not a terminal boolean | `domain/services.py` (`review`, `approve`), `domain/substantiation.py` (`assess`), `ports/review_router.py`, `agent/root_agent.py` instruction |
| **P-07** | Auditable and explainable by design | Every review and every approval writes a WORM `AuditEvent` with the decision and citations; the ADK after-agent callback audits again at the model boundary | `domain/services.py`, `adapters/gcp/cloud_logging_audit.py`, `agent/callbacks.py` |
| **P-08** | Eval-gated promotion | Offline eval gate scores finding accuracy, review safety, citation accuracy and green-claim substantiation accuracy, each against an independent golden oracle; Hrz4 at promotion | `eval/run_eval.py`, `eval/rubrics/*.yaml`, `ports/observability.py` (`EvaluationGatePort.gate`) |
| **P-09** | Defense in depth / zero trust | CMEK, least-privilege IAM, private endpoints, a distinct agent identity; the guardrail screens twice (domain pipeline and model-boundary callback) | `infra/terraform/kms.tf`, `iam.tf`, `agent/callbacks.py` |
| **P-10** | Provenance on every claim | Every finding carries a source-and-page `Citation` to the rule it fired, and every green-claim coverage result cites the regulator instrument behind the requirement it was measured against; the deterministic engines decide, the model only narrates | `domain/models.py` (`Citation`), `domain/rule_engine.py`, `domain/coverage_engine.py`, `rulepacks/green_claims.yaml` |
| **P-11** | Cost and latency control | A small triage-tier model handles routing / pre-checks; the reasoning model only narrates the already-decided findings | `config.py` (`ModelSettings.triage`) |
| **P-12** | Reversibility / documented exit | The `local` adapters run the whole pipeline off-cloud today (the working proof), and the `onprem` placeholders satisfy the same Protocols as the fail-fast sovereign target; the contract test proves parity for both | `adapters/local/*`, `adapters/onprem/*`, `tests/contract/test_port_parity.py`, `docs/onprem-migration.md` |
| **P-13** | Fair, consented marketing (advertising compliance) | **This is Mkt6's job.** It checks marketing assets against per-market, per-vertical advertising / consumer-protection / fair-trading rules, brand guidelines and marketing consent, and gates environmental claims on the substantiation evidence actually held, per jurisdiction. It is the catalog's named enforcer of P-13 | `domain/rule_engine.py`, `domain/services.py`, `domain/coverage_engine.py`, `domain/substantiation.py`, the [P-13 enforcement entry](https://github.com/portable-genai) on the organization front page |

---

## Dependency rules

Mkt6's mandatory dependencies are **Hrz1, Hrz2, Hrz3, Hrz4 (gate) and Hrz5** (see
`systems/`; the banking vertical may additionally reuse Rsk1). Each rule is satisfied by
consuming the sibling service through a `platform` adapter (with an on-prem stub), never by
re-implementing the concern.

| Rule | Requirement | How Mkt6 satisfies it | Evidence |
|------|-------------|---------------------|----------|
| **R1** | Customer PII handling: Hrz1 guardrail + DLP redaction | Mkt6 consumes the Hrz1 guardrail for prompt-injection and unsafe-output screening. The consent authority keeps raw subject ids inside its tenant store, pseudonymizes them before audit, and gates that boundary with SG/JP/AU jurisdiction packs plus an independent planted-leak oracle. | `ports/safety.py`, `domain/services.py`, `domain/consent_service.py`, `eval/run_eval.py` |
| **R2** | Audit to Hrz5 | Every review and approval writes an immutable WORM `AuditEvent`; the `platform` adapter posts to Hrz5 `/v1/audit` | `adapters/gcp/cloud_logging_audit.py`, `adapters/platform/remote_audit.py` |
| **R3** | Governed RAG via Hrz2 | The per-market, per-vertical rule set is retrieved from the Hrz2 governed KB (`RuleProviderPort` / File Search) so every review is grounded in a versioned rule source | `ports/rules.py`, `adapters/platform/remote_knowledge_base.py` |
| **R4** | Register in Hrz3 | The A2A AgentCard is published at `/.well-known/agent-card.json` and resolvable via Hrz3; the governed MCP tool catalog scopes access least-privilege | `agent/agent_card.py`, `api/app.py`, `adapters/platform/remote_registry.py`, `adapters/gcp/mcp_tool_catalog.py` |
| **R5** | Hrz4 promotion gate | `EvaluationGatePort.gate` checks the Hrz4 thresholds before promotion; the offline gate guards merges | `ports/observability.py`, `adapters/platform/remote_evaluation.py`, `eval/run_eval.py` |
| **R6** | Validated by Rsk3 at intake | As a new project, Mkt6 is validated by the Rsk3 intake validator externally. n/a in-repo | intake handled by Rsk3 externally |
| **R7** | Marketing compliance via Mkt6 | **Mkt6 IS the R7 enforcer**, so this rule is n/a as a *consumer*: Mkt1..Mkt5 route their customer-facing output through Mkt6, not the other way around. Mkt6's own review output is internal (a compliance verdict), not published advertising | `domain/services.py` (`review`), the R7 destination for the marketing tier |
| **R8** | Route escalations to Hrz7 | Every regulated-claim review, plus each green-claim assessment requiring sign-off (`route_assessment`), routes to the Hrz7 Human-Review and Maker-Checker Console through `review-kit`, not a terminal per-repo boolean. Descriptor, summary and citation snippets are scrubbed before the wire and the strongest finding severity sets dual control. The agent has no approve tool | `ports/review_router.py`, `adapters/_review_payload.py`, `adapters/{local,platform,onprem}/review_router.py`, `domain/services.py` (`review`) |

---

## Customer identifiers stop at the consent-store boundary (R1, C3, C4)

- **The review surface remains corporate; consent is a separate authority.** Marketing assets,
  rules and substantiation evidence contain corporate material. The canonical consent store does
  hold tenant-scoped subject ids so it can answer purpose/channel decisions, but those values do
  not enter the LLM or durable audit. `ConsentService._subject_ref` replaces them with a
  tenant-scoped SHA-256 reference at the audit boundary.
- **The privacy gate can go red.** The offline consent evaluation plants valid synthetic SG, JP
  and AU identifiers and drives decision, grant recording, grant confirmation, preference,
  suppression and send paths plus the outbound Hrz7 review payload. It uses the matching
  `pii-kit` rules and independently searches every derived audit/outbox object for each exact
  planted literal. Tests deliberately remove the jurisdiction patterns and prove that the
  independent oracle still catches the leak.
- **Tenant-scoped evidence is a real object-level authorization surface (C2).** Substantiation
  evidence belongs to one brand. Reads are gated on the verified `Principal`'s tenant, a
  cross-tenant read is refused with `403` and audited, and a cross-tenant denial test proves it
  (`tests/unit/test_substantiation_tenant_access.py`).
- **The guardrail still runs, twice.** The Hrz1 guardrail screens INPUT and OUTPUT inside the
  domain pipeline and again at the ADK model boundary, catching prompt injection and unsafe
  output even though there is no PII to redact.
- **Determinism decides compliance (P-10, P-13).** The rule engine decides which rules fail,
  the severity and the compliant / non-compliant outcome, and the coverage engine decides which
  green claims an asset makes and whether the evidence carries them; the model only narrates, so
  every verdict is replayable and each finding is cited to its rule or instrument.
- **Maker-checker is the whole point (P-06).** A non-compliant review escalates to a human
  checker; the agent produces the review (maker) and never approves it (checker), and the
  approval record is auditable.

---

## Appendix: regulator crosswalk (adopter-owned)

The `P-*` / `R*` catalog above is this build's internal control language; a regulated adopter
maps it onto its own supervisor's requirements. The rows below are a **reference mapping** for
the home markets (JP / AU / SG); a fork adds a column per additional regulator. This appendix
is *adopter-owned*: a template, not legal advice.

| Mkt6 control | Reference regime | What a supervisor looks for |
|---|---|---|
| P-13 rule engine (claim + brand + consent) | SG ASAS / consumer-protection; AU ACCC / ASIC advertising; JP fair-trade / premiums-and-representations | Advertising claims substantiated and consumer-protection-compliant per market; consent honoured |
| P-13 green-claims gate (coverage engine + rule pack) | AU: ACCC environmental-claims guidance (2023), Australian Consumer Law ss 18 / 29(1)(a), ASIC INFO 271. SG: MAS Circular CFC 02/2022, Singapore Code of Advertising Practice, Consumer Protection (Fair Trading) Act 2003. JP: Act against Unjustifiable Premiums and Misleading Representations, Consumer Affairs Agency environmental-labelling guidance, FSA ESG-fund supervisory guidelines. Cross-border: ISO 14021:2016 | Environmental claims substantiated by evidence held BEFORE publication, evidence current and independently verified where required, sustainability fund labels matched by disclosed strategy, and a named human signing off every green claim |
| C2 tenant isolation on substantiation evidence | MAS TRM (access control); privacy and confidentiality expectations | One brand cannot read another's substantiation file; access is derived server-side and denials are auditable |
| P-06 maker-checker (review + approve) | MAS FEAT (Accountability); four-eyes control | A qualified human disposes of every clear or block recommendation; the maker cannot approve |
| P-07 WORM audit; P-10 provenance | MAS TRM (auditability); record-keeping | Immutable records; every finding traceable to the rule it fired |
| P-03 residency; P-12 exit | MAS Outsourcing / Cloud guidelines | In-country data residency and a demonstrable exit / portability plan |
| P-08 quality / model-risk gate | MAS FEAT; model-risk expectations | A promotion gate with finding-accuracy / safety metrics and model documentation |

**To add another regulator**: copy this table, replace the reference column with that
supervisor's instrument and section numbers, and re-review the third column with local
counsel. The Mkt6-control column is stable across regulators; only the mapping changes. Because
Mkt6 is the shared gate the rest of the marketing tier depends on, adding a regulator here
extends coverage for every marketing system at once.
