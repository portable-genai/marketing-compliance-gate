# D6 Marketing Compliance and Brand Governance — developer tasks.
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
DEMO_PORT ?= 8115
TF_DIR := infra/terraform

export MKT_GOV_PROFILE := $(PROFILE)

.PHONY: venv install install-demo install-gcp lock lint format typecheck test eval gate \
        ui-install ui-check demo demo-server demo-selftest demo-browser smoke-local run-api run-ui tf-validate tf-plan clean

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip

install: venv ## Install the package + dev tooling (NO GCP SDK — local/onprem profile).
	$(BIN)/python -m pip install -e ".[dev]"

install-demo: venv ## Install the pinned headless-browser extra, then fetch its browser binary.
	$(BIN)/python -m pip install -e ".[dev,demo]"
	$(BIN)/python -m playwright install chromium

install-gcp: ## Install with the managed-stack extra (google-genai, discoveryengine, ...).
	$(BIN)/python -m pip install -e ".[gcp,dev]"

lock: ## Recompile every lockfile from pyproject.toml and restore the tag = commit headers.
	$(BIN)/python scripts/lock.py

lint:
	$(BIN)/ruff check src tests scripts/render_review_ui.py scripts/demo_selftest.py

format:
	$(BIN)/ruff format --check src tests scripts/render_review_ui.py scripts/demo_selftest.py

typecheck:
	$(BIN)/mypy src

test:
	$(BIN)/pytest -m "not integration" -q

eval:
	$(BIN)/python eval/run_eval.py

# The full gate, green before any change lands.
portability:
	PYTHONPATH=src $(BIN)/python scripts/portability_demo.py

gate: lint format typecheck test eval demo-selftest portability

# The ui/ console gate. Requires node; nothing in `make gate` does.
ui-install: ## Install the console's locked dependencies.
	npm ci --prefix $(UI_DIR)

ui-check: ## The console gate: types, CSP unit tests, build, and a REAL hydration check.
	npm --prefix $(UI_DIR) run lint
	npm --prefix $(UI_DIR) test
	NEXT_TELEMETRY_DISABLED=1 npm --prefix $(UI_DIR) run build
	# Runs LAST, and against the artefact the previous line produced. Everything cheaper than
	# this has been fooled by the defect it catches: the CSP header is byte-identical whether
	# the page hydrates or is dead markup, so only starting the built server and reading the
	# served script tags can tell the two apart. See ui/scripts/assert-hydratable.mjs.
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

tf-plan: ## Plan the APAC-resident Terraform deploy (region pinned + validated).
	cd $(TF_DIR) && terraform init -input=false && terraform plan

tf-validate:
	cd $(TF_DIR) && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
