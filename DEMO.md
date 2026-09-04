# DEMO: `marketing-compliance-gate` Marketing Compliance and Brand Governance

Two ways to demo: a fully offline local demo (no cloud, no keys) and a GCP demo (the
managed Gemini Enterprise Agent Platform stack). Both review obviously-fictional synthetic
marketing assets across both verticals (banking + online retail) and the JP/AU/SG markets.

## A. Local demo (offline, deterministic, no cloud)

Everything runs on the `local` profile: a SQLite FTS5 rule KB, the deterministic rule
engine, and a deterministic LLM narrator. No Google Cloud SDK, no API key.

### 1. Install and prove the gate

```bash
make install
make gate     # ruff + ruff format + mypy + pytest + eval, all green
```

### 2. CLI: review a banking asset and a retail asset

```bash
# Banking financial promotion with prohibited claims and missing consent/disclosure:
MKT_GOV_PROFILE=local mkt-gov review \
  "Get guaranteed returns of 4.10% with zero risk-free worry!" -m SG -v banking

# Online-retail offer with an over-ceiling discount and a banned superlative:
MKT_GOV_PROFILE=local mkt-gov review \
  "Lowest price guaranteed on everything!" -m AU -v online_retail --type offer -f discount_pct=90

# A compliant release recommendation (no failing findings, still checker-gated):
MKT_GOV_PROFILE=local mkt-gov review \
  "Big savings this weekend on selected items." -m SG -v online_retail \
  -f discount_pct=40 -f stock_on_hand=120 -c marketing
```

Each review prints the findings (rule id, severity, evidence and fix), consent checks, cited
rules and maker-checker "HUMAN REVIEW REQUIRED" banner. A compliant result is a release
recommendation, not an auto-approval.

### 3. CLI: the green-claims gate

```bash
# A carbon-neutral campaign whose offset retirement record has lapsed: partially
# substantiated, and held for a human. The as-of date pins how the evidence is aged, so the
# demo tells the same story next quarter.
MKT_GOV_PROFILE=local mkt-gov substantiate \
  "Bank with a carbon neutral balance sheet. Offsets are disclosed in our report." \
  -m AU -v banking --as-of 2026-08-05

# A fully evidenced retail ESG fund creative: substantiated, and STILL held for a human.
MKT_GOV_PROFILE=local mkt-gov substantiate \
  "Invest in our sustainable fund, built on a published ESG screening strategy." \
  -m SG -v banking --type creative --id camp-green-sg-002 --as-of 2026-08-05 \
  -f substantiation_ref=dms://example.test/pack/sg-002 -f esg_fund_disclosure="prospectus s4.2"

# The same asset read by a different brand's principal: it sees only its own evidence.
MKT_GOV_PROFILE=local mkt-gov substantiate \
  "Bank with a carbon neutral balance sheet. Offsets are disclosed in our report." \
  -m AU -v banking --tenant other-brand --as-of 2026-08-05
```

Each assessment prints the claims detected, the coverage per claim, the evidence that
counted, the exact gap (expired, too old, self-declared, missing), the green-claim rules in
force with the instrument each cites, and the human-review banner.

### 4. Static audit-first artifacts (for screenshots)

```bash
make demo
# writes scripts/out/review-*.json + green-*.json, their HTML panels, and index.html
open scripts/out/index.html
```

`make demo` also prints the tenant boundary end to end: the same asset assessed by two
brands' principals, and a direct cross-tenant evidence read refused with a 403.

### 5. Live presenter demo server (offline)

```bash
make demo-server        # http://localhost:8115 ; click "Next" through the reviews
```

### 6. Presenter-paced browser walkthrough (Playwright)

A guided, narrated run of the same demo server: a real Chrome window opens, each step is
announced on the terminal (never on screen, so the audience sees a clean console) and waits
for you to press Enter before it clicks "Next" and highlights the panel to look at.

```bash
# one-time
.venv/bin/pip install playwright && .venv/bin/playwright install chromium

# terminal 1
make demo-server

# terminal 2
.venv/bin/python scripts/demo_playwright.py
```

Unattended (self-test / recording): `HEADLESS=1 DEMO_AUTO=1 .venv/bin/python scripts/demo_playwright.py`.
It walks the same non-compliant/compliant pairs across banking and online-retail (SG, AU,
JP) that `demo.py` writes to `scripts/out/`, so the narration matches what a reviewer would
see reading the JSON.

### 7. The thin Next.js console against the local API

```bash
make run-api            # FastAPI on :8105 (local profile)
# the console, on a PRODUCTION build; paste copy, pick market/vertical, review:
cd ui && npm install && npm run build && npm run start   # http://localhost:3000
```

`NEXT_PUBLIC_API_BASE` needs no setting here: the console already defaults to `:8105`, the
port `make run-api` binds. Demo the built console, never `make run-ui`: that target is the
developer loop and serves `next dev`, and the standing rule for every demo in the fleet is
`org-metadata/docs/demos/demo-inventory.md`: production builds only.

"Check green claims" runs the substantiation gate and opens the green-claims panel: the
verdict and coverage bar per claim, the evidence counted, the gaps, the green-claim rules,
the evidence your tenant holds, and the instruments cited. Switch the persona picker to the
cross-tenant persona and run it again on the same asset id: the evidence table changes and
the coverage drops, because the backend scoped the read to the verified principal, not to
anything the browser sent.

In `local` mode the console shows a "Demo identity" persona picker (reviewer / approver /
auditor / cross-tenant), each backed by a seeded `Principal`. The picked persona goes out as
the `X-Dev-Persona` header, and the backend uses that verified persona's subject as the audit
actor: no `actor` is ever sent in the request body. To embed the console into a host app
same-origin, set `NEXT_PUBLIC_BASE_PATH` (reverse-proxy sub-path) and `NEXT_PUBLIC_EMBED=1`
(drop our chrome); see [`docs/embedding-and-identity.md`](docs/embedding-and-identity.md).

## B. GCP demo (managed Gemini Enterprise Agent Platform)

The same code, the `gcp` profile: the rule KB is **Gemini API File Search**, narration is
**Gemini**, safety is **Model Armor**, audit is a **Cloud Logging WORM** bucket, tracing is
**Cloud Trace**, and the `model-quality-gate` is the **Gen AI evaluation service**. All Google SDK
imports are lazy, and the residency region is resolved from the active market and validated
against the per-market allow-list.

### 1. Install the managed extra and authenticate

```bash
make install-gcp
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=your-project
```

### 2. Choose a residency region by market

`config/settings.yaml` maps JP -> asia-northeast1, AU -> australia-southeast1,
SG -> asia-southeast1. A region outside this allow-list is rejected before any call.

### 3. Run the API and the eval gate on GCP

```bash
PROFILE=gcp make run-api
python eval/run_eval.py --use-gcp     # routes through the Gen AI evaluation service
```

## What to point out

- The outcome is decided by `domain/rule_engine.py` (pure code), not the model. Re-running
  any review yields byte-identical findings.
- The green-claim verdict and the coverage number come from `domain/coverage_engine.py`, and
  `as_of` is an input, so an assessment made last quarter replays exactly. The model writes
  the paragraph and nothing else.
- A green claim is held for a human even when the evidence fully carries it. The gate is a
  sign-off, not an approval bot.
- Substantiation evidence is tenant-owned: a cross-tenant read is a 403 with an audit
  record, never a silent 404 and never a 200.
- Every finding cites the exact rule and its authority.
- Every clear or block recommendation is gated: it requires human review and holds a
  `PENDING` approval.
- Switching banking <-> online_retail or JP/AU/SG is config + seed only; nothing bank-only
  is hard-coded.
- Switching `gcp` -> `onprem` is a one-line profile change; `domain/` does not move.
