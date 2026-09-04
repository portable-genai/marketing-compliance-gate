# Security FAQ

For an application-security team reviewing this repo before adopting it as a base. Answers
reflect the current code. Cross-references: [`ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`COMPLIANCE.md`](../../COMPLIANCE.md),
[`docs/embedding-and-identity.md`](../embedding-and-identity.md),
[`docs/practices-audit.md`](../practices-audit.md).

### How is a request authenticated? Can a client spoof its identity?

No. Identity is resolved **server-side** from the transport context by an `IdentityPort`
adapter (`api/security.py`), never from the request body. `POST /v1/review` carries no
`actor` field (`api/schemas.py` `ReviewRequestModel`), and any client-asserted actor is
discarded. The audit actor comes from the verified `Principal`. Per profile: `local` =
seeded dev personas (no IdP, offline only, selected by the `X-Dev-Persona` header), `secure`
(gcp / platform) = the IAP-injected signed assertion verified upstream, `onprem` = a
client-IdP placeholder. `tests/unit/test_api_identity.py` proves an unknown persona is 401,
the server subject is the audit actor, and the body cannot supply an actor.

### Is there multi-tenant object-level authorization?

Not needed here, by design. `marketing-compliance-gate` reviews marketer-authored asset copy against a **shared
reference rule set** (per market / vertical) and computes the review on demand; there is no
per-tenant or per-customer stored resource to authorize and no ACL-filtered retrieval. The
`Principal` carries `tenant` / `principals` for the audit record, but there is no
tenant-scoped evidence store. If your fork adds one, derive its ACL server-side from the
verified principal (never the request body) and tag evidence with the tenant.

### What about the service-to-service calls in the `platform` profile?

The one real outbound call today (the `model-quality-gate` eval / promotion-gate client,
`adapters/platform/remote_evaluation.py`) is built on the shared `hex-service-kit` /
`agent-eval-kit` S2S client: it requires an `https://` base URL outside loopback (rejected
at construction) and attaches a bearer credential. The R8 review-router producer
(`review-kit`) scrubs the descriptor, summary and citation snippets before the wire.
The remaining platform delegates (guardrail, rules, audit, registry) are phase stubs; wire
them to the same S2S client when you enable them.

### Does anything bind 0.0.0.0 by default?

No. Under the `local` profile the API and `make run-api` bind **loopback (127.0.0.1)** by
default (`Makefile` `API_HOST ?= 127.0.0.1`), and CORS is an explicit
`MKT_GOV_CORS_ORIGINS` allowlist, never `*`, with the localhost dev-origin fallback and the
`X-Dev-Persona` header **local-profile-only**. The container sets `MKT_GOV_PROFILE=gcp` and
runs behind the platform's ingress.

### What HTTP security headers are set? (Partly closed.)

The **console** now serves a full default-deny CSP, built in one place
(`ui/lib/csp.mjs`) and emitted from one place (`ui/proxy.ts`): `default-src 'self'`,
`base-uri 'self'`, `form-action 'self'`, `object-src 'none'`, a per-request nonced
`script-src`, `connect-src` scoped to the API origin, and the existing three-state
`frame-ancestors`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
See [`docs/embedding-and-identity.md`](../embedding-and-identity.md#1e-the-consoles-content-security-policy).

The **API** surface still emits only `Content-Security-Policy: frame-ancestors` +
`X-Frame-Options` from `api/app.py`. A full CSP on API responses and HSTS on secure profiles
are **not yet** set (tracked as the C6 PARTIAL in
[`docs/practices-audit.md`](../practices-audit.md)). A fork exposing the API beyond the
platform's ingress should close this before going live.

### Is there a web login flow to review?

No. The repo owns no OIDC / PKCE login: `local` uses seeded dev personas, `secure` trusts
the upstream IAP assertion, `onprem` is a client-IdP placeholder. There is no in-repo
session-cookie or token-exchange code path to audit (C8 is N-A in the practices audit).

### How tamper-evident is the audit trail? What are its limits?

The `local` audit store (`LocalAppendOnlyAuditAdapter`) wraps the shared
`hex_service_kit.audit.HashChainedAuditLog`: a SHA-256 hash chain over canonical JSON, SQLite
`UPDATE` / `DELETE` triggers enforcing append-only, JSONL export / restore, and
`verify_chain()`. The module docstring states its honest limits (a chain with no external
anchor cannot alone detect full-file truncation or rewrite). In production the `gcp` profile
writes to a locked WORM bucket, and the enterprise WORM audit system is the sibling `agent-observability`;
this repo does not replace it. Proven by `tests/unit/test_audit_chain.py`.

### Is customer PII processed?

No. `marketing-compliance-gate` reviews marketer-authored asset copy (campaign / creative / offer text plus
structured fields) and reference rule text; it does not ingest, index or store customer PII
or per-customer consent records (`MarketingAsset.granted_consents` is a tuple of
consent-purpose labels, not customer data). There is therefore no PII de-identification
boundary in this repo (C3 / C4 N-A). Latent note: if your fork ever submits PII-bearing
copy, add a redaction step, because `AuditEvent` stores the raw prompt / response today.

### Supply chain: are dependencies pinned and scanned?

Yes. Committed lockfiles (`requirements-dev.lock`, `requirements-gcp.lock`, uv pip compile,
py3.12) are installed in CI and the Docker build; the base image is digest-pinned; GitHub
Actions are SHA-pinned; `dependabot` proposes bumps; and a CI job runs `pip-audit` +
`npm audit` as hard gates. `ruff` is pinned exactly (`ruff==0.15.18`). The shared commons
(`hex-service-kit`, `agent-eval-kit`, `review-kit`) are pinned by tag.

### Where are secrets? Are any committed?

No secret values are in the repo. `config/settings.yaml` stores only `*_env` names and
non-secret ids; values are read at construction and never logged. Every fixture and the rule
seed use obviously-fictional names and `example.test` URLs.

### What is explicitly out of scope / a residual risk?

- The full security-header baseline (CSP `default-src`, `nosniff`, `Referrer-Policy`, HSTS)
  is not yet set (C6 PARTIAL).
- The platform guardrail / rules / audit / registry delegates are phase stubs, not live S2S
  clients yet.
- No CI `terraform fmt` / `validate` job, and provider binaries are committed under
  `infra/terraform/.terraform/` (D5 PARTIAL, repo hygiene).
- This is a reference build: run your own pen-test, threat model and model-risk review before
  any live-data deployment.
