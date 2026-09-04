# Adopting this repo as your base

This repository is a **common base** that marketing, brand and compliance teams fork to
build their own marketing-compliance review agent: financial-promotions review, ad-copy and
creative pre-clearance, offer / promotion checking, consumer-protection and consent gating.
It ships a reusable hexagonal core (a pure-stdlib domain, typed ports, swappable adapter
profiles, a green offline gate) plus a fully worked marketing maker-checker gate (Campaigns,
Creatives and Offers, across the banking and online-retail verticals and the JP, AU and SG
markets) you can keep, replace, or learn from.

This guide is the step-by-step for making it yours. It has two halves: a **mechanical
rebrand** (one script) and the **human decisions** the script cannot make for you.

> Related reading: [`ARCHITECTURE.md`](../ARCHITECTURE.md) (port table, pipeline),
> [`CONTRIBUTING.md`](../CONTRIBUTING.md) (adding a deterministic engine, a market or a
> vertical), the [`faq/`](faq/) directory.

---

## 1. What you keep vs what you rewrite

The hexagon draws the boundary for you: the domain speaks only to ports, and the
consequential decision is pure code, so most of the machinery transfers and only the
marketing artifacts and the rule content are yours to own.

| Layer | Where | For your fork |
|---|---|---|
| **Reusable core** (vertical-neutral) | the stable `domain/kernel.py` import surface, deterministic checker mechanics, serialization/identity modules and generic ports | keep untouched |
| **Compliance numbers** (your rules) | the seeded `RuleSet` (bundled seed in `adapters/local/_seed.py`, or `local.seed_path` in `config/settings.yaml`): claim / disclosure patterns, numeric limits, per-rule severities and citations | change by rule data, not code |
| **Vertical** (marketing artifacts) | the `MarketingAsset` / `Review` artifacts in `domain/models.py`, the narration in `domain/services.py`, `domain/prompts` wording, the local fixtures, the eval golden set, the UI review views | rewrite for your assets and markets |

If your product is another *marketing-compliance* vertical, the deterministic rule engine,
the maker-checker gate, the port layer and the four profiles transfer directly; you replace
the rule seed (your markets and authorities), retune the artifact fields, and rebuild the
golden set.

## 2. Core-vs-adopter-owned files (so upstream merges stay mechanical)

Upstream keeps evolving these; avoid diverging from them so you can pull fixes cleanly:

- **Upstream-owned** (take our changes): `domain/rule_engine.py` mechanics, `ports/`,
  `tests/contract/`, the eval harness (`eval/run_eval.py` mechanics), CI workflows, the
  hexagon wiring (`config.py` `Container`).
- **Adopter-owned** (yours; expect to edit): `config/settings.yaml` *values*, the rule seed
  (`adapters/local/_seed.py` or your `seed_path`), the local fixtures, `adapters/onprem/*`,
  UI theming / branding, the golden eval dataset, the `COMPLIANCE.md` regulator crosswalk
  rows.

Track upstream via git tags; rebase your adopter-owned
changes onto each release rather than merging `main` continuously.

## 3. The mechanical rebrand (one script)

`scripts/rename_fork.py` rewrites the package name, the CLI entry point, the `MKT_GOV_` env
prefix, and the baked-in resource ids across the tree in one pass. Preview first, then
apply:

```bash
# Preview (writes nothing):
python scripts/rename_fork.py --package acme_ad_review --cli acme-adrev \
    --env-prefix ACME --resource acme-ad-review --dry-run

# Apply:
python scripts/rename_fork.py --package acme_ad_review --cli acme-adrev \
    --env-prefix ACME --resource acme-ad-review --yes

# Then recreate the environment (the distribution name changed) and prove it is green:
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make gate
```

For `marketing-compliance-gate` the distribution name and the resource-id stem are the same string
(`marketing-compliance-gate`), so `--dist` defaults to `--resource`; pass `--dist`
explicitly only if your fork wants a distribution name that differs from its resource stem.
Add `--include-docs` to sweep Markdown prose too. The script deliberately does NOT touch the
human decisions below.

## 4. The human decisions (the script can't make these)

1. **Region / residency.** The build pins a residency region per market (`markets:` in
   `config/settings.yaml`: JP `asia-northeast1`, AU `australia-southeast1`, SG
   `asia-southeast1`, default `asia-southeast1`) and validates it to fail fast. Set the
   Terraform `region` / tfvars and your `MKT_GOV_REGION` (now `<PREFIX>_REGION`) to your
   in-country region, and add a `markets:` entry for any new market. See
   [`docs/runbook.md`](runbook.md).
2. **Identity / IdP.** The repo owns no web login: `local` = seeded dev personas (no IdP),
   `secure` = the GCP IAP-injected assertion verified server-side, `onprem` = a client-IdP
   placeholder. Wire your IdP behind the `IdentityPort` and set `MKT_GOV_IAP_AUDIENCE` (and
   the CORS / frame-ancestors allowlists) for the secure profile. See
   [`docs/embedding-and-identity.md`](embedding-and-identity.md).
3. **The rule seed is your compliance content.** The bundled `RuleSet` is a reference, not
   your policy. Replace the claim / disclosure patterns, numeric limits, severities and the
   per-rule `Citation` (to the real authority) for each of your markets and verticals in
   `adapters/local/_seed.py` (or point `local.seed_path` at your own), and own those numbers
   with your compliance function. The engine has no hard-coded compliance threshold; it
   reads the rule data.
4. **Markets and verticals.** Adding a market or vertical is a config + seed change, not a
   code change: add the `Market` / `Vertical` value, its `MARKET_PROFILES` / `markets:`
   entry (residency region + locales), and its seed rules. The engines do not branch on
   market or vertical. See [`CONTRIBUTING.md`](../CONTRIBUTING.md).
5. **Reference data is fictional.** Every fixture, the seed rules and the golden set use
   obviously-fake names and `example.test` URLs. Swap them for your own synthetic data.
   **Do not run against live marketing or customer data without your own legal, brand,
   security and model-risk sign-off.**
6. **Eval golden set.** Rebuild `eval/datasets/` and the rubrics for your markets: a fork
   inherits a green gate that measures the WRONG thing until you do. The gate structure and
   the independent-oracle safety metrics are generic; the golden cases are yours.
7. **Deployment posture.** Review the Dockerfile (digest-pinned base, non-root, `USER
   appuser`), `infra/terraform/` (Org Policy resource-location allowlist, regional CMEK,
   the WORM audit bucket, the dry-run-first VPC-SC perimeter), and the loopback-by-default
   API bind before you expose anything.

## 5. Do not duplicate the platform

This repo is one system in a catalog of composable GRC systems. Several concerns it
*touches* are owned by sibling platform services, and you should integrate rather than
rebuild them (see [`docs/faq/features-faq.md`](faq/features-faq.md) for the full map): the
guardrail gateway (`agent-guardrail-gateway`), the governed rule / knowledge base (`enterprise-knowledge-base`), the agent registry
(`agent-registry`), the AI-quality / eval gate (`model-quality-gate`), observability + WORM audit (`agent-observability`), the
human-review and maker-checker console escalations route to (`human-review-console`), and the compliance
assistant (`compliance-advisory`). The `platform` profile's adapters are already thin HTTP clients to those
services. `marketing-compliance-gate` is itself the marketing tier's maker-checker gate: `market-intelligence`..`next-best-action` route their
customer-facing output through it, so a fork should not re-implement the review gate inside
another marketing system.

## 6. Adoption checklist

- [ ] Ran `scripts/rename_fork.py`, recreated the venv, `make gate` green.
- [ ] Set region + Terraform tfvars to your in-country region; added a `markets:` entry per market.
- [ ] Wired your IdP behind the `IdentityPort` and set the IAP audience + CORS / frame-ancestors allowlists.
- [ ] Replaced the rule seed (patterns, limits, severities, per-rule citations) for your markets and verticals.
- [ ] Owned the compliance numbers in the rule seed with your compliance function.
- [ ] Replaced every synthetic fixture and the seed with your own fictional data.
- [ ] Rebuilt the eval golden set + rubrics for your markets; confirmed each metric can go red.
- [ ] Reviewed the deploy posture (Dockerfile, Terraform, API bind address).
- [ ] Decided which sibling platform services you integrate vs stub.
- [ ] Recorded your baseline upstream tag so you can take future fixes.
