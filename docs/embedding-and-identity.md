# Embedding and identity: client integration guide (Mkt6 marketing-compliance-gate)

This guide explains how to embed the D6 Marketing Compliance and Brand Governance console
into a client's existing web app (or run it standalone), and how the backend verifies the
end-user identity server-side instead of trusting a client-supplied `actor`. It is the
repo-specific, in-scope subset of the broader pattern; the "Further layers" section at the
end points at the reference implementation for the cross-origin and OIDC variants that this
repo deliberately does not ship.

## The two pieces

The system ships as two artifacts:

1. A FastAPI backend (`marketing_compliance_gate.api.app:app`, default port 8105) that exposes
   `POST /v1/review`, `GET /healthz`, and `GET /v1/personas`.
2. A Next.js console (`ui/`) that is a thin client over that API.

Both are deployable together (the console iframes into a host app, same-origin) or the
console can be skipped and the API called directly (server-to-server).

## The identity contract (the core rule)

The client never asserts who the user is. `POST /v1/review` carries NO `actor` field: the
request body is only the marketing asset to review. On every review the backend resolves a
verified `Principal` via the `IdentityPort`, and:

- `principal.actor` (the verified subject) becomes the audit actor written to the WORM audit
  log, so non-repudiation holds (MAS TRM / CPS 234 style), and
- `principal.principals` / `principal.tenant` are available for per-user, per-tenant
  authorization decisions.

If identity cannot be resolved, the route returns `401`. A client-supplied identity, in any
header or body field the app does not itself verify, is ignored.

The `IdentityPort` has one method, `resolve(RequestContext) -> Principal`, and three
adapters selected by `MKT_GOV_PROFILE`:

| Profile | Adapter | How identity is established |
|---|---|---|
| `local` | `LocalPersonaIdentityAdapter` | Seeded dev persona chosen by the `X-Dev-Persona` header (default = first persona). No IdP, no AD/LDAP. For demos and tests. |
| `gcp`, `platform` | `IapIdentityAdapter` | Verifies the GCP Identity-Aware Proxy assertion (`x-goog-iap-jwt-assertion`): signature, audience (`MKT_GOV_IAP_AUDIENCE`), issuer, expiry. Subject from `email`/`sub`, tenant from `hd`. The assertion is never logged. |
| `onprem` | `OnPremIdentityAdapter` | Fail-fast placeholder: implement verification against the client's own enterprise IdP (OIDC/SAML) and map the verified claims to a `Principal`. |

The seeded local personas (reviewer, approver, auditor, and a cross-tenant user) let you
exercise per-user and per-tenant authorization fully offline: pick a persona in the console
and the audit actor changes accordingly.

## Deployment shape 1: embedded, same-origin reverse-proxy (recommended)

Serve the agent under the parent origin (for example `portal.client.com/compliance/*`) so
the iframe is first-party. Same-origin means no third-party-cookie problem and no CORS at
all: the browser treats the framed console and its API calls as the parent's own origin.

### 1a. Reverse-proxy the agent under a sub-path (nginx)

```nginx
# On https://portal.client.com
location /compliance/ {
    proxy_pass http://compliance-ui:3000/compliance/;   # the Next.js console
    proxy_set_header Host $host;
}
location /compliance/api/ {
    proxy_pass http://compliance-api:8105/;             # the FastAPI backend
    proxy_set_header Host $host;
    # The edge (IAP / your gateway) injects the verified identity header here; the backend
    # re-verifies it. Do NOT let a client set x-goog-iap-jwt-assertion from outside the edge.
}
```

### 1b. Mount the console under the sub-path and drop its chrome

Build the console with:

```bash
# In ./ui (build-time env)
NEXT_PUBLIC_BASE_PATH=/compliance         # basePath + assetPrefix, so assets resolve under /compliance
NEXT_PUBLIC_EMBED=1                        # drop the app header/branding; the host owns the chrome
NEXT_PUBLIC_API_BASE=/compliance/api       # same-origin API calls (no CORS)
```

`NEXT_PUBLIC_BASE_PATH` drives `basePath` / `assetPrefix` in `next.config.mjs`; blank keeps
the standalone app unchanged. `NEXT_PUBLIC_EMBED=1` makes `layout.tsx` and `page.tsx` render
the review UI without the outer header, so the host page owns the surrounding chrome.

### 1c. The iframe tag (host page)

```html
<iframe
  src="https://portal.client.com/compliance/"
  title="Marketing compliance review"
  style="width: 100%; height: 900px; border: 0;"
  sandbox="allow-scripts allow-forms allow-same-origin"
></iframe>
```

### 1d. Allow the parent origin to frame the console

The API emits a `Content-Security-Policy: frame-ancestors ...` header on every response,
driven by `MKT_GOV_FRAME_ANCESTORS` (default `'self'`). For a same-origin embed under the
parent origin, `'self'` is sufficient. To allow one or more distinct parent origins to frame
the console, list them (space-separated, per the CSP grammar):

```bash
export MKT_GOV_FRAME_ANCESTORS="https://portal.client.example https://admin.client.example"
```

When the allowlist is exactly `'self'`, the API also sets `X-Frame-Options: SAMEORIGIN`; for
a multi-origin allowlist the CSP header is authoritative (X-Frame-Options cannot express
multiple origins, so it is omitted).

`MKT_GOV_FRAME_ANCESTORS` is read in three states, not two. Leaving it **unset** keeps the
restrictive `'self'` default. Setting it to a **blank** value is refused at boot: the service
will not start. That is deliberate, because a blank value used to render
`Content-Security-Policy: frame-ancestors ` with an empty directive, which browsers discard as
a parse error, and the `X-Frame-Options` fallback was skipped at the same time, so the
clickjacking control disappeared with no signal. If you meant "no parent may frame this", that
is the `'self'` default, so unset the variable. The same rule applies to the UI's
`NEXT_PUBLIC_FRAME_ANCESTORS`, which is refused at build time when set and blank.

### 1e. The console's Content-Security-Policy

The console's policy is built in exactly one module, [`ui/lib/csp.mjs`](../ui/lib/csp.mjs), and
emitted from exactly one place, [`ui/proxy.ts`](../ui/proxy.ts). `ui/next.config.mjs` no longer
emits a `Content-Security-Policy` at all: a browser given two policies intersects them and the
stricter wins per directive, so a second copy is a silent way to reinstate whatever the first
copy was fixing.

The policy is default-deny, and `script-src` carries a **per-request nonce** plus
`'strict-dynamic'`:

```
default-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none';
script-src 'self' 'nonce-<per request>' 'strict-dynamic'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self' data:; connect-src 'self' <API origin>;
frame-ancestors <as above>
```

The nonce is not cosmetic. Next serves its hydration bootstrap as an **inline** script carrying
the Flight payload, so a bare `script-src 'self'` blocks it: the server HTML renders, `__next_f`
never fills, React never attaches, and the console shows its controls as dead markup while the
headers, the type-check, the build and every unit test stay green.

Two things must both hold, and the second is the one that bites:

1. `proxy.ts` sets the policy on the **request** headers (where Next reads the nonce it stamps
   onto each script tag) *and* on the **response** (what the browser enforces). Either one alone
   fails, in opposite directions.
2. The route must be **dynamically rendered**. `app/layout.tsx` sets
   `export const dynamic = "force-dynamic"` for this reason alone. Statically prerendered HTML
   was built before the nonce existed, so nothing carries it, and because `'strict-dynamic'`
   switches off the `'self'` fallback, a nonce on a static page blocks strictly *more* than the
   unfixed policy did. `next.config.mjs` refuses to build that combination.

Because none of that is decidable from the header string (it is byte-identical in the working
and the broken case), the gate proves it by execution: `npm run assert-hydratable` (wired into
`make ui-check` and CI) starts the **built** server, fetches the served document, and asserts
that every `<script>` tag carries the nonce the response advertised.

## Deployment shape 2: standalone behind Cloud IAP

Run the console and API as their own site (no host app) fronted by GCP Identity-Aware Proxy
(Cloud Run behind an HTTPS load balancer + IAP). IAP authenticates the user against the
configured IdP (Google Workspace, or an external client IdP via Workforce Identity
Federation) and injects a signed assertion. Deploy with:

```bash
export MKT_GOV_PROFILE=gcp
export MKT_GOV_IAP_AUDIENCE="/projects/<PROJECT_NUMBER>/global/backendServices/<BACKEND_ID>"
```

The backend verifies that assertion on every request and derives the `Principal`. Because
authentication is configured ON the GCP service, the app holds almost no auth code: it just
verifies the injected assertion and maps it to a `Principal`. If the console runs on its own
origin here, set `MKT_GOV_CORS_ORIGINS` to that origin (see the CORS note below).

## Deployment shape 3: local dev, no auth

The `local` profile runs the whole pipeline offline with seeded dev personas and no IdP:

```bash
# Backend (repo root)
MKT_GOV_PROFILE=local make run-api      # FastAPI on :8105

# Console (in ./ui)
NEXT_PUBLIC_API_BASE=http://localhost:8105 npm run dev   # Next.js on :3000
```

The console shows a "Demo identity" picker (rendered only when `GET /healthz` reports
`profile === "local"`). It loads `GET /v1/personas`, default-selects the first persona, and
sends the chosen persona as the `X-Dev-Persona` header on each request. In any non-local
profile `GET /v1/personas` returns an empty list and the picker does not render.

## CORS (only for cross-origin, standalone)

Same-origin embedding needs no CORS. For the cross-origin / standalone case the API uses an
explicit per-tenant allowlist from `MKT_GOV_CORS_ORIGINS` (comma-separated), defaulting to
the local dev origins and NEVER `"*"`:

```bash
export MKT_GOV_CORS_ORIGINS="https://console.client.com,https://staging.client.com"
```

Allowed methods are `GET, POST, OPTIONS`; allowed headers are `Content-Type`,
`Authorization`, and `X-Dev-Persona`.

## Config knobs

| Variable | Where | Default | Purpose |
|---|---|---|---|
| `MKT_GOV_PROFILE` | backend | (unset = no choice) | `local` \| `gcp` \| `platform` \| `onprem`: selects the identity adapter (and the whole stack). Unset refuses the `local` relaxations rather than assuming them. |
| `MKT_GOV_IAP_AUDIENCE` | backend | (empty) | Expected audience of the IAP assertion; required in `gcp`/`platform`. |
| `MKT_GOV_CORS_ORIGINS` | backend | dev origins under a deliberate `local`, otherwise empty | Comma-separated per-tenant CORS allowlist. Never `"*"`. |
| `MKT_GOV_FRAME_ANCESTORS` | backend | `'self'` when unset | CSP `frame-ancestors`: which parent origins may iframe the console. Set and blank is refused at boot, never read as the default. |
| `MKT_GOV_ALLOW_INSECURE_DEMO` | backend | (unset = guard on) | The ONE opt-out from the loopback exposure bound. When the bound identity adapter does not verify the end user, a non-loopback peer gets 503; set this to exactly `1` to accept that exposure deliberately. `0`, `true`, blank and ` 1 ` all leave the guard on. |
| `X-Dev-Persona` | request header | (none) | `local` only: selects the seeded dev persona. Ignored in secure profiles. |
| `NEXT_PUBLIC_FRAME_ANCESTORS` | console | `'self'` when unset | CSP `frame-ancestors` for the document Next serves. Same three-state read as the backend variable: set and blank is refused at build/boot, never read as the default. |
| `NEXT_PUBLIC_API_BASE` | console | `http://localhost:8105` | Base URL the console calls. Use a same-origin sub-path when embedded. Must be absolute; it also widens the CSP `connect-src` to that URL's origin. |
| `NEXT_PUBLIC_BASE_PATH` | console | (empty) | Mount the console under a reverse-proxy sub-path. Blank keeps standalone. |
| `NEXT_PUBLIC_EMBED` | console | (empty) | `1` drops the app header/chrome so the host owns it. |

## Client-side integration checklist

- [ ] Decide the deployment shape: same-origin reverse-proxy (preferred), standalone behind
      IAP, or local dev.
- [ ] For same-origin: reverse-proxy both the console and the API under one origin sub-path;
      build the console with `NEXT_PUBLIC_BASE_PATH`, `NEXT_PUBLIC_EMBED=1`, and a
      same-origin `NEXT_PUBLIC_API_BASE`.
- [ ] Add the `<iframe>` to the host page with a minimal `sandbox` (`allow-scripts`,
      `allow-forms`, `allow-same-origin`).
- [ ] Set `MKT_GOV_FRAME_ANCESTORS` to the exact parent origin(s), not a wildcard.
- [ ] For standalone: front the service with IAP and set `MKT_GOV_IAP_AUDIENCE`.
- [ ] Confirm the host never lets a client set the IAP assertion header from outside the edge.
- [ ] Confirm the console never sends an `actor`; the audit actor comes from the verified
      `Principal`.

## Security checklist

- [ ] Identity is resolved server-side by the `IdentityPort`; a failure is a `401`.
- [ ] The request body has no `actor`; the audit actor is `principal.actor`.
- [ ] The IAP assertion is verified (signature, audience, issuer, expiry) and never logged.
- [ ] The edge (IAP / gateway) is the only source of the assertion header; the backend
      re-verifies it (defense in depth: edge PEP, then per-backend re-validation).
- [ ] CORS is an explicit per-tenant allowlist, never `"*"`; same-origin needs no CORS.
- [ ] `frame-ancestors` lists only the intended parent origins. Never set the variable to a
      blank value to mean "default": the service refuses to boot on it.
- [ ] The `onprem` adapter fails fast until the client IdP integration is implemented, so an
      unverified caller is never accepted.

## Further layers (out of scope for this repo, documented for reference)

This repo ships the same-origin embed and the profile-gated `IdentityPort` (local personas,
GCP IAP verification, on-prem placeholder). It deliberately does NOT ship the following,
which the reference implementation `cdd-sow-research` covers in
`docs/embedding-and-identity.md`:

- Cross-origin embedding via a versioned SRI-pinned loader, a web component, and a
  host <-> iframe `postMessage` contract.
- Cross-origin authentication as a bearer-token-in-memory handoff with a host-minted token
  verified against a trusted issuer's JWKS.
- A "launch in new tab" OIDC Authorization Code + PKCE session-cookie login mode.
- Per-hop OAuth2 token exchange (OBO) + Workload Identity + mTLS to the shared Hrz platform
  services, and DPoP / step-up (acr/amr) for high-value actions.

For any of those, follow the reference guide and add the corresponding adapter behind the
same `IdentityPort` seam.
