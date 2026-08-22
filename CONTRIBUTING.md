# Contributing to Mkt6 (`marketing-compliance-gate`)

This repo follows the catalog's non-negotiable conventions (shared with Rsk1
`compliance-advisory` and Doc1 `cdd-sow-research`, and the reusable skills in
`.agents/skills/`). Read these before opening a change.

## Non-negotiable conventions

1. **Keep the domain pure.** Nothing under `src/marketing_compliance_gate/domain/` imports a
   cloud SDK, an LLM SDK, an HTTP/web framework, or a validation lib. Standard library only.
   If the domain needs something external, it calls a port.
2. **Lazy SDK imports in adapters.** In `adapters/gcp/*`, every heavy import lives inside a
   method (or under `TYPE_CHECKING`). The local / onprem profile must import every module
   with no `google-cloud-*` installed.
3. **One adapter constructor.** Every adapter is `def __init__(self, settings: Settings)`.
4. **One binding surface.** `config/settings.yaml` maps each port to an adapter dotted path
   per profile. Switching the whole backend is a one-line `profile` change, never a code
   edit.
5. **Provenance on every claim.** Every generated statement, finding or number that leaves
   the system carries a `Citation`.
6. **Human-in-the-loop on consequential output.** A non-compliant `Review` sets
   `requires_human_review=True` and starts with a `PENDING` `ApprovalRecord`; nothing
   consequential auto-executes.
7. **Deterministic engine, narrating LLM.** The consequential decision (whether each rule
   passes, the severity, the outcome) lives in the pure, unit-tested `RuleEngine`. The LLM
   only narrates the already-decided findings; it never decides whether a rule passes.
8. **Contract tests for parity.** A single test asserts every adapter family (local +
   onprem) satisfies every port Protocol, so the offline and exit profiles never drift.
9. **Generic, multi-vertical, APAC.** No bank-only logic in the domain. Banking and online
   retail are configurable verticals; JP/AU/SG are config + seed (residency region, locale,
   rules), never a hard-coded branch.
10. **Obviously-fictional synthetic data only.** Company names are suffixed FICTIONAL and
    all URLs point at `example.test`. Never wire to live/production data without sign-off.
11. **Plain prose in docs.** In every markdown file and commit/PR message, do not overuse
    em-dashes (prefer a colon, comma, parentheses, or two sentences). No space-colon-space
    in YAML scalars.

## The gate (must be green before any change lands)

Run in a fresh `[dev]`-only venv (no `google-cloud-*`):

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -m "not integration" -q
python eval/run_eval.py            # exit 0
```

The linter/formatter version is pinned (`ruff==0.15.18`) so CI and local agree.

## Adding a deterministic engine

Follow the `deterministic-domain-service` skill: a frozen, dependency-free service with
tunables as fields, structured + severity-ranked + cited output, an `escalates` / review
flag, and tests covering happy path, each finding kind, ranking, boundaries, determinism and
defaults. Re-export it from `domain/services.py`.

## Changing green-claim policy or adding a jurisdiction

Green-claim policy is DATA, not code. To change what a jurisdiction forbids, discloses or
requires as evidence, edit `src/marketing_compliance_gate/rulepacks/green_claims.yaml` (or ship your
own file and point `green_claims.pack_path` at it). The touch list:

1. `rulepacks/green_claims.yaml`: add the instrument to `citations` (a real, nameable regulator
   publication, never an invented one), then the `categories` phrases, the `requirements` and
   the `rules` under the jurisdiction. Every rule must carry a `citation` id the pack defines,
   or loading fails.
2. `eval/datasets/golden_green_claims.jsonl`: add at least one golden asset whose
   `expected_verdict` is written from the evidence in the row, not from a product run.
3. `tests/unit/test_green_pack.py`: extend the reference-pack assertions if the new jurisdiction
   introduces a shape the tests do not cover yet.

Nothing in `domain/coverage_engine.py` should change: if you find yourself adding a jurisdiction
branch there, the pack schema is missing a field instead.

## Adding a market or vertical

This is a controlled vocabulary, config and seed extension. Add the `Market` / `Vertical`
wire value, its `MARKET_PROFILES` entry (or `markets:` override in settings.yaml), and the
seed data in `adapters/local/_seed.py`. The engines do not branch on market or vertical.

## Adding an adapter

1. Implement the existing Protocol in `src/<package>/adapters/<profile>/<name>.py` with the
   single constructor `Adapter(settings)`; cloud SDK imports stay inside methods.
2. Add the dotted binding under the existing port in `config/settings.yaml` for that profile.
3. Add the adapter to the constructor and behavioral cases in
   `tests/contract/test_port_parity.py`; a placeholder must construct and fail fast.
4. Add profile-specific boundary tests, including unavailable service and malformed response
   cases. Do not copy business rules into the adapter.
5. Run `make gate`, the UI gate when applicable, and `make tf-validate` when deployment
   configuration changed.

## Adding a new port or sub-service

1. Add a `@runtime_checkable` Protocol in `src/<package>/ports/<name>.py` and re-export it once
   from `ports/__init__.py`.
2. Add one binding per declared profile in `config/settings.yaml`: working local, managed GCP
   or platform, and an honest on-premises implementation or fail-fast placeholder.
3. Register the Protocol in the `PORT_PROTOCOLS` map used by
   `tests/contract/test_port_parity.py`; the reverse set-equality assertion must stay green.
4. Wire the port only in the composition root or service factory. Domain services accept the
   Protocol dependency and never import an adapter.
5. Add behavioral parity tests and an end-to-end local test, then update the architecture,
   compliance evidence and adopter guidance.
