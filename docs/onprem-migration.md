# On-prem migration (exit / portability): General Principle P-12

The whole point of the ports-and-adapters shape is that Mkt6's exit story is **demonstrable,
not aspirational**. Switching from the managed GCP stack to a sovereign / on-premise stack is a
one-line profile change (`MKT_GOV_PROFILE=onprem`) plus filling in the adapter bodies. The
domain core, the services, the API, the CLI and the agent wiring do not change.

## What "onprem" gives you today

Setting `MKT_GOV_PROFILE=onprem` rebinds every port to a placeholder adapter under
`src/marketing_compliance_gate/adapters/onprem/`. Those adapters:

- construct cleanly with **no Google Cloud SDK installed** (the contract test proves it),
- structurally satisfy the same `Protocol` as the managed GCP adapter, and
- raise `NotImplementedError` from every method that must not silently no-op (rule provider,
  LLM, guardrail, audit, evaluation, agent registry, tool catalog), while non-essential ports
  return safe defaults (the tracer is a no-op).

This is what makes the contract test `tests/contract/test_port_parity.py` meaningful: it
imports and constructs each on-prem placeholder and asserts interface parity, and separately
proves the `local` family is a WORKING offline stack implementing the same interfaces.

## The migration checklist

To run Mkt6 on a sovereign / on-premise platform, implement these adapter bodies (the only
files that change):

| Port | On-prem file | What to implement |
|------|--------------|-------------------|
| `RuleProviderPort` | `onprem/rules.py` | An on-prem governed rule-set store (your File Search / Hrz2 KB equivalent) (R3) |
| `EvidenceStorePort` | `onprem/evidence.py` | Your on-prem substantiation-evidence store (the document system holding emissions inventories, offset records, test reports and fund disclosures). Keep the tenant semantics: `list_for_asset` MUST filter on the tenant in the store, and `get` stays an unfiltered fetch so the domain owns the 403 |
| `LlmPort` | `onprem/llm.py` | An on-prem model-serving endpoint (e.g. Gemma on your own serving stack) |
| `GuardrailPort` | `onprem/guardrail.py` | An on-prem prompt / response screening backend (R1) |
| `AuditSinkPort` | `onprem/audit.py` | An on-prem immutable (WORM) audit store (R2) |
| `ObservabilityTracerPort` | `onprem/tracer.py` | An on-prem tracing backend (a no-op is acceptable) |
| `EvaluationGatePort` | `onprem/evaluation.py` | An on-prem eval backend and promotion gate (R5) |
| `AgentRegistryPort` | `onprem/registry_agent.py` | An on-prem A2A agent catalog (R4) |
| `ToolCatalogPort` | `onprem/tool_catalog.py` | An on-prem governed MCP tool catalog (R4) |
| `IdentityPort` | `onprem/identity.py` | Your on-prem IdP / SSO assertion verifier |
| `ReviewRouterPort` | `onprem/review_router.py` | Your on-prem human-review console, for both `route` (compliance reviews) and `route_assessment` (green claims) |

Nothing under `src/marketing_compliance_gate/domain/` changes. The review pipeline, the
deterministic rule engine (claim / permission / brand / consent checks), the maker-checker
policy (`review` + `approve`), the citation mapping, the serialization, and the prompts are all
profile-agnostic. The agent wiring (`src/marketing_compliance_gate/agent/`) is unchanged too: the
FunctionTool calls the same domain service, so it runs against whichever adapter family the
profile binds, and it still exposes only the maker (review) half.

## Why this matters for a regulated buyer

A bank's or retailer's marketing-compliance function cannot accept a gate it cannot exit,
especially the one everything else routes through. Because the domain depends only on
Protocols, the regulator-facing properties (rule-grounded reviews, cited findings, maker-checker
with a human approver, WORM audit) survive a platform change unchanged, and the migration is a
bounded, testable piece of work rather than a rewrite. The `local` family is the proof that the
off-cloud path already runs end to end today.
