# marketing-compliance-gate — Marketing Compliance & Governance API service image.
#
# Builds the FastAPI service with the managed-stack extra ([gcp]) installed, so the deployed
# container talks to the managed services in asia-southeast1. The image is region-agnostic at
# build time; residency is enforced at runtime via config/settings.yaml (region pinned) and the
# deploy environment. The app defaults to the offline `local` profile when the profile env var
# is unset, so this image sets MKT_GOV_PROFILE=gcp EXPLICITLY to opt in to the managed stack in
# production (never rely on a baked-in default to select cloud).

# --------------------------------------------------------------------------- #
# Builder — install dependencies into a venv we can copy into a slim runtime.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY requirements-gcp.lock ./
COPY src ./src
COPY config ./config

RUN pip install --upgrade pip \
 && pip install -r requirements-gcp.lock && pip install --no-deps .

# --------------------------------------------------------------------------- #
# Runtime — slim, non-root, venv copied from builder.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    MKT_GOV_PROFILE=gcp \
    MKT_GOV_SETTINGS=/app/config/settings.yaml \
    PORT=8105

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY config ./config

USER appuser
EXPOSE 8105

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8105')+'/healthz')" || exit 1

CMD exec uvicorn marketing_compliance_gate.api.app:app --host 0.0.0.0 --port ${PORT}
