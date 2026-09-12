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
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder

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
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    MKT_GOV_PROFILE=gcp \
    MKT_GOV_SETTINGS=/app/config/settings.yaml \
    PORT=8105

WORKDIR /app

# A digest pin freezes the base image, which means it also freezes its unpatched packages.
# Reproducible and patched are independent properties: the pin buys the first and is then cited as
# the supply-chain evidence for both. Without this line the promotion scan
# (`trivy image --exit-code 1 --ignore-unfixed --severity HIGH,CRITICAL`) reports 30 fixable HIGH
# from the pinned Debian 13.6 base alone, most of them the util-linux family plus openssl. The
# distribution's security updates are applied on top, so the image is reproducible AND patched.
# Both sibling stacks this deployment runs beside found it the same way, by scanning the image they
# were about to promote.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY config ./config

# Remove pip from the RUNTIME image, in both the system prefix and the venv.
#
# Two reasons, and the second is the one a scan reports. First, a serving container installs
# nothing, so a package manager in it is an install capability an attacker can use and the
# application never can. Second, pip VENDORS its dependencies: msgpack and setuptools live inside
# pip/_vendor, pinned by pip/_vendor/vendor.txt, so a scanner reports pip's bundled copies as
# installed packages. That is where the scan's 2 remaining HIGH came from. Neither package is a
# dependency of this application, neither appears in any lockfile here, and no lock move could
# reach them, because they were never resolved: they arrived inside pip itself.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /opt/venv/lib/python3.14/site-packages/pip \
           /opt/venv/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /opt/venv/bin/pip /opt/venv/bin/pip3

USER appuser
EXPOSE 8105

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8105')+'/healthz')" || exit 1

CMD exec uvicorn marketing_compliance_gate.api.app:app --host 0.0.0.0 --port ${PORT}
