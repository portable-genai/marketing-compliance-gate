# Demo scripts - `marketing-compliance-gate`

All scripts are SDK-free and run against the in-process `local` stack (no Google Cloud,
no API key). They drive the real D6 services, the review rule engine and the green-claims
substantiation gate, over the built-in synthetic banking / online-retail assets across the
JP, AU and SG markets. Run them from the repo root with the domain package on the path:

```bash
export PYTHONPATH=src
export MKT_GOV_PROFILE=local
```

| Script | What it does |
|--------|--------------|
| `demo.py` | Runs the real review + green-claims services over four synthetic assets and two green-claim scenarios, prints a cited trace to stdout, and writes each audit view to `scripts/out/*.json`. Also the end-to-end smoke test for the slice. |
| `render_review_ui.py` | Dependency-free static HTML renderer: reads the `demo.py` JSON output and writes the audit-first console pages (`scripts/out/index.html` + one page per review), the same palette and markup as the live Next.js console. |
| `demo_server.py` | Live, presenter-controlled HTTP server (stdlib only) that reveals one of the four reviews per click, reusing `render_review_ui.py` verbatim. `make demo-server`, then open `http://localhost:8115`. |
| `demo_playwright.py` | Headed, presenter-paced Playwright walkthrough of `demo_server.py`: narrates in the terminal only, clicks "Next" on your cue, and spotlights the panel to look at. See [`../DEMO.md`](../DEMO.md#6-presenter-paced-browser-walkthrough-playwright) for the two-terminal run. |
| `demo_selftest.py` | Drives every presenter step through the real in-process session, verifies rendered evidence, advance and reset behaviour, and runs in `make gate`. |
| `portability_demo.py` | Proves the bounded local profile and portable audit contract without requiring Google Cloud. |
| `rename_fork.py` | Unrelated to the demo: a one-pass rebrand utility for forking this repo (package name, CLI entry point, env-var prefix, resource ids). |
| `lock.py` | Compiles both lockfiles and puts the header back, because `uv pip compile` REPLACES the output file: it writes its own two-line provenance comment and destroys the `tag = commit` map the pin tests check against. `make lock` runs this rather than uv directly. |

Every scenario is obviously-fictional synthetic data (see `demo.py` for the fixed set), so
screenshots and the walkthrough narration never drift between runs.
