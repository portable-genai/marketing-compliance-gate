# Adoption FAQ

For an engineering lead forking this repo as their team's base. The step-by-step is
[`docs/ADOPTING.md`](../ADOPTING.md); this answers the "will it hurt later?" questions.

### How do I rebrand it for my team?

`scripts/rename_fork.py` rewrites the package name, CLI entry point, `MKT_GOV_` env prefix,
and resource ids in one pass (preview with `--dry-run`, apply with `--yes`). Then recreate
the venv, `pip install -e ".[dev]"`, and run `make gate`. The script does the mechanical
rename; the human decisions (region, IdP, rule seed, eval golden set, fixtures) are the
checklist in `ADOPTING.md`. For `marketing-compliance-gate` the distribution name and the resource stem are the
same string, so `--dist` defaults to `--resource`.

### If several teams fork this, how does each take upstream fixes?

Track upstream via **git tags** (semver). The repo declares a **core-vs-adopter-owned boundary** (ADOPTING §2): upstream owns
`domain/rule_engine.py` mechanics, `ports/`, `tests/contract/`, the eval harness mechanics
and CI; you own `config/settings.yaml` values, the rule seed, fixtures, `adapters/onprem/*`,
UI theming, and the eval golden set. Rebase your adopter-owned changes onto each release
rather than merging `main` continuously, so conflicts stay in files you were told to expect.

### How do I change the rules without touching engine code?

The compliance numbers are **rule data, not code**. The claim / disclosure patterns, numeric
limits, per-rule severities and citations live in the seeded `RuleSet` (`adapters/local/
_seed.py`, or point `local.seed_path` at your own file). The `RuleEngine` has no hard-coded
compliance threshold; it reads the active rule set. Boundary tests
(`test_rule_engine.py::test_numeric_max_boundary_and_missing`,
`test_config_and_local_rules.py`) show a seeded limit driving behavior. Note there is no
separate `policy:` dataclass section here (unlike some sibling repos): the numbers are the
rule seed.

### How do I add a market or vertical?

It is a config + seed change, not a code change: add the `Market` / `Vertical` value, its
`MARKET_PROFILES` (or `markets:` override in `config/settings.yaml`) entry carrying the
residency region and locales, and the seed rules in `adapters/local/_seed.py`. The engines do
not branch on market or vertical. See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) ("Adding a
market or vertical").

### How do I add a new outbound dependency (a new port)?

Define the `@runtime_checkable` Protocol under `ports/`, re-export it from
`ports/__init__.py`, implement one adapter per profile (at least `local` and `onprem`, each
`__init__(self, settings: Settings)`), bind all of them in `config/settings.yaml` under
`adapters:`, add a `cached_property` on the `Container` (`config.py`), and wire it in
`api/deps.py`. The contract test (`tests/contract/test_port_parity.py`) fails if a bound port
is missing an adapter, so you find a missed step at CI time. (An explicit full-touch-list
section in `CONTRIBUTING.md` for "adding a port / adapter" is a known doc gap, G6 PARTIAL;
the steps here are the touch list.)

### How do I add a new deterministic engine or sub-service?

Follow the `deterministic-domain-service` skill: a frozen, dependency-free stdlib service
with tunables as fields, structured + severity-ranked + cited output, an `escalates` /
review flag, and tests covering happy path, each finding kind, ranking, boundaries,
determinism and defaults. Re-export it from `domain/services.py`. See
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) ("Adding a deterministic engine").

### Does the CI run for my fork out of the box?

Yes. CI and the eval gate run on the `local` profile with **no cloud credentials and no org
secrets** (`ci.yaml` / `eval-gate.yaml` set `MKT_GOV_PROFILE: local`), so a fork's build is
green immediately. You add secrets only when you wire the `gcp` / `platform` profiles. Note
the eval gate measures the *reference* rule set until you rebuild the golden set for your
markets, that is an explicit adoption step, not a silent pass, and the gate refuses to score
if a safety metric cannot go red.

### Will the demo rot after I diverge?

Partly guarded. `make demo` drives the real `ReviewService` and there is an API boot smoke in
CI, but there is no unattended demo self-test asserting each step's live state yet (F2
PARTIAL in [`docs/practices-audit.md`](../practices-audit.md)). Re-run `make demo` after any
change that touches the review path until that self-test lands.
