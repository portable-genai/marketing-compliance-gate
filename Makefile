# marketing-compliance-gate Marketing Compliance and Brand Governance: developer tasks.
#
# The gate (lint + format + types + tests + eval) runs on the local profile with the
# [dev] extra only (no google-cloud-*), matching CI. Override PROFILE=gcp for the managed
# stack, or PROFILE=onprem for the fail-fast migration target.

PY ?= python3.14
VENV ?= .venv
BIN := $(VENV)/bin
PROFILE ?= local

API_APP := marketing_compliance_gate.api.app:app
API_HOST ?= 127.0.0.1  # no-auth local dev binds loopback; override deliberately
API_PORT ?= 8105
UI_DIR := ui
# The shape the console actually ships in: mounted under the portal's sub-path, calling its API
# same-origin through that prefix. Both are BUILD-time inputs to Next.js, so this is a different
# artefact from the default build and `ui-check` proves both.
UI_BASE_PATH ?= /apps/marketing-compliance-gate
UI_API_BASE  ?= /apps/marketing-compliance-gate/api
DEMO_PORT ?= 8115
TF_DIR := infra/terraform

export MKT_GOV_PROFILE := $(PROFILE)

.PHONY: venv install install-demo install-gcp lock lint format typecheck test eval gate \
        ui-install ui-check demo demo-server demo-selftest demo-browser smoke-local run-api run-ui tf-validate tf-plan clean

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip

install: venv ## Install the package + dev tooling (NO GCP SDK: local/onprem profile).
	$(BIN)/python -m pip install -e ".[dev]"

install-demo: venv ## Install the pinned headless-browser extra, then fetch its browser binary.
	$(BIN)/python -m pip install -e ".[dev,demo]"
	$(BIN)/python -m playwright install chromium

install-gcp: ## Install with the managed-stack extra (google-genai, discoveryengine, ...).
	$(BIN)/python -m pip install -e ".[gcp,dev]"

lock: ## Recompile every lockfile from pyproject.toml and restore the tag = commit headers.
	$(BIN)/python scripts/lock.py

lint:
	$(BIN)/ruff check src tests scripts/render_review_ui.py scripts/demo_selftest.py \
		scripts/render_plugin.py scripts/load_consent_seed.py

format:
	$(BIN)/ruff format --check src tests scripts/render_review_ui.py scripts/demo_selftest.py \
		scripts/render_plugin.py scripts/load_consent_seed.py

typecheck:
	$(BIN)/mypy src

test:
	$(BIN)/pytest -m "not integration" -q

eval:
	$(BIN)/python eval/run_eval.py

eval-narrative:
	$(BIN)/python eval/run_narrative_eval.py

evals-doc:
	$(BIN)/python scripts/render_evals_doc.py

evals-doc-check:
	$(BIN)/python scripts/render_evals_doc.py --check

plugin: ## Render the Agent Plugins 1.0.0 directory from this repo's own declarations.
	PYTHONPATH=src $(BIN)/python scripts/render_plugin.py --dest dist/plugin

mcp-serve: ## Serve the governed tool catalog over MCP 2026-07-28 (stdio; needs [gcp]).
	PYTHONPATH=src $(BIN)/python -m marketing_compliance_gate.mcp

# The full gate, green before any change lands.
portability:
	PYTHONPATH=src $(BIN)/python scripts/portability_demo.py

gate: lint format typecheck test eval eval-narrative evals-doc-check demo-selftest portability plugin

# The ui/ console gate. Requires node; nothing in `make gate` does.
ui-install: ## Install the console's locked dependencies.
	npm ci --prefix $(UI_DIR)

ui-check: ## The console gate: types, CSP tests, then build + HYDRATION in the default AND the embedded shape.
	npm --prefix $(UI_DIR) run lint
	npm --prefix $(UI_DIR) test
	NEXT_TELEMETRY_DISABLED=1 npm --prefix $(UI_DIR) run build
	# Runs LAST, and against the artefact the previous line produced. Everything cheaper than
	# this has been fooled by the defect it catches: the CSP header is byte-identical whether
	# the page hydrates or is dead markup, so only starting the built server and reading the
	# served script tags can tell the two apart. See ui/scripts/assert-hydratable.mjs.
	npm --prefix $(UI_DIR) run assert-hydratable
	# And again in the shape that actually ships. The base path and the API base are BUILD-time
	# inputs to Next, so the embedded console is a DIFFERENT artefact and a green default build
	# says nothing about it: a sibling's `docker build ui` failed six CSP assertions on a commit
	# whose own ui-check was green, because only the default shape was ever built.
	NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_BASE_PATH=$(UI_BASE_PATH) NEXT_PUBLIC_API_BASE=$(UI_API_BASE) \
		npm --prefix $(UI_DIR) run build
	NEXT_PUBLIC_BASE_PATH=$(UI_BASE_PATH) NEXT_PUBLIC_API_BASE=$(UI_API_BASE) \
		npm --prefix $(UI_DIR) run assert-hydratable

demo: ## Offline demo: run the review flow + render the static audit-first HTML (scripts/out).
	MKT_GOV_PROFILE=local PYTHONPATH=src $(BIN)/python scripts/demo.py
	MKT_GOV_PROFILE=local PYTHONPATH=src $(BIN)/python scripts/render_review_ui.py scripts/out

demo-server: ## Live, presenter-controlled offline demo server on :$(DEMO_PORT).
	MKT_GOV_PROFILE=local PYTHONPATH=src $(BIN)/python scripts/demo_server.py --port $(DEMO_PORT)

demo-selftest: ## Prove the SERVED presenter states and evidence hooks cannot rot silently.
	MKT_GOV_PROFILE=local PYTHONPATH=src $(BIN)/python scripts/demo_selftest.py

demo-browser: ## Drive the SERVED demo through pinned headless Chromium (needs the [demo] extra).
	MKT_GOV_PROFILE=local $(BIN)/pytest tests/browser -q -rs

smoke-local: ## End-to-end offline smoke: review a non-compliant asset under the local profile.
	MKT_GOV_PROFILE=local $(BIN)/mkt-gov review "Get guaranteed returns with zero risk-free worry!" -m SG -v banking

run-api: ## Run the real FastAPI service on :$(API_PORT) (PROFILE=$(PROFILE)).
	$(BIN)/uvicorn $(API_APP) --host $(API_HOST) --port $(API_PORT)

run-ui: ## Run the thin Next.js console (dev server); set NEXT_PUBLIC_API_BASE to the API.
	cd $(UI_DIR) && npm install && npm run dev

tf-plan: ## Plan the APAC-resident deploy against its GCS state; needs TF_STATE_BUCKET and credentials.
	cd $(TF_DIR) && terraform init -input=false \
		"-backend-config=bucket=$${TF_STATE_BUCKET:?set TF_STATE_BUCKET to the GCS state bucket}" \
		-backend-config=prefix=marketing-compliance-gate && terraform plan

tf-validate: ## Offline Terraform proof: fmt, validate and the mock-provider plan tests (no credentials).
	cd $(TF_DIR) && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate && terraform test

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
