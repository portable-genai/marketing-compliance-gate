# marketing-compliance-gate

The shared working agreement is [`.github/AGENTS.md`](https://github.com/portable-genai/.github/blob/main/AGENTS.md).
It carries the architecture rules, the gate contract, the fleet invariants, the
falsification discipline, versions and house style, and it holds in every repository
here. Read it first. This file carries only what is specific to this one.

## What this is

Catalog id **Mkt6**. Marketing compliance and brand governance: a deterministic rule engine
checks a `Campaign`, `Creative` or `Offer` against the per-market, per-vertical advertising,
consumer-protection and consent rules in force, emits a cited finding per rule, and gates the
result behind a marketing maker-checker `ApprovalRecord`. A second deterministic engine is the
green-claims gate (`green_pack.py`, rule packs under `rulepacks/`): it classifies the
environmental claims a piece of copy makes and decides whether the substantiation evidence the
brand holds carries them. The LLM narrates; it never decides whether a rule passes, whether a
green claim is substantiated, or what the coverage figure is.

## Concrete bindings

| | |
|---|---|
| Catalog id | `Mkt6` |
| Package | `src/marketing_compliance_gate/` |
| Profile variable | `MKT_GOV_PROFILE` |
| Adapter families | `gcp`, `local`, `onprem`, `platform` |
| Gate | `make gate` |

`config.resolve_profile` is the one place that reads that variable, in three states: unset is
no choice and falls through to `config/settings.yaml`, set-and-empty raises
`ConfiguredEmptyError` rather than inheriting the unset behaviour, and an unknown or
mis-capitalised value raises. `Settings.profile_explicit` records whether anyone actually
chose, and the seeded-persona identity adapter refuses to serve when nobody did.

Residency is checked against the active market, not merely configured: under the `gcp`
profile `Settings.__post_init__` raises `UnsupportedMarketError` when `region` does not match
the residency region the active market's profile names.

## What this repository still owes

The `Capability gaps` cell on this repository's row in the maintainer's system tracker
is the authoritative list. Its verdict against the shared checks, including the ones it
does not pass, is in [`docs/practices-audit.md`](docs/practices-audit.md).
