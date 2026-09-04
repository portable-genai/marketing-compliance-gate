# `marketing-compliance-gate` Marketing Compliance and Brand Governance: Demo UI

A thin demo console for `marketing-compliance-gate`, the Marketing Compliance and Brand Governance system. It is
a thin presentation layer over the `marketing-compliance-gate` FastAPI backend: it submits a marketing asset (copy +
market + vertical + fields + granted consents) for review, and renders the audit-first
result (the compliant / non-compliant outcome, the deterministic findings with severity,
evidence, remediation and provenance, the consent checks, and the cited rules) with the
maker-checker "human review required" banner. It never bypasses the guardrail or the review
gate: it only shows what the backend returns.

Built with **Next.js (App Router) + TypeScript + Tailwind**. Dependencies are kept minimal:
`next`, `react`, `react-dom`, `tailwindcss`, `postcss`, `autoprefixer`, `typescript`, and the
`@types` packages, nothing else.

## Generic and APAC

The market selector covers **Japan / Australia / Singapore** (each labelled with its
residency region), and the vertical selector covers **banking** and **online retail**. The
console is vertical-agnostic: it renders whatever the backend returns for the selected
market and vertical.

## Configure the backend

Nothing to configure to run against `make run-api`: `NEXT_PUBLIC_API_BASE` already
defaults to the `marketing-compliance-gate` API port 8105. Write the override yourself only when the API is
somewhere else, and write it before `npm run build`, because Next inlines every
`NEXT_PUBLIC_*` value at build time:

```bash
echo 'NEXT_PUBLIC_API_BASE=https://api.elsewhere.example' > .env.local
```

## Run

```bash
# 1. start the `marketing-compliance-gate` API (from the repo root)
make run-api            # uvicorn on :8105, PROFILE=local by default

# 2. start the console
make run-ui             # or: cd ui && npm install && npm run dev
```

Then open http://localhost:3000.

## Source map

| Path | What lives there |
|---|---|
| `app/layout.tsx` | The root layout. `export const dynamic = "force-dynamic"` is **required by the nonce CSP**, not a performance choice: Next can only stamp a per-request nonce onto the scripts of a dynamically rendered route. |
| `app/page.tsx`, `components/` | The review form and the audit-first result rendering. |
| `lib/api.ts`, `lib/types.ts` | The typed client for the `marketing-compliance-gate` FastAPI backend. |
| `lib/csp.mjs` | The **only** place the Content-Security-Policy is built, plus the three-state `frame-ancestors` read that mirrors `src/marketing_compliance_gate/api/app.py::_frame_ancestors` and the two build-time refusals. |
| `proxy.ts` | The **only** place the CSP is emitted (Next 16 names this file `proxy.ts`). Mints the per-request nonce and sets the policy on both the request headers (where Next reads the nonce) and the response (what the browser enforces). |
| `next.config.mjs` | `basePath` / `assetPrefix`, the static-expressible headers (`nosniff`, `Referrer-Policy`), and the module-scope refusals. It deliberately emits **no** CSP: two layers emitting one hand the browser two policies to intersect. |
| `tests/csp.test.mjs` | `node:test` cover for what a policy string can decide. Explicitly not sufficient. |
| `scripts/assert-hydratable.mjs` | The check that is sufficient: it starts the built server and reads the served markup. |

## Gate

From the repo root:

```bash
make ui-install     # npm ci
make ui-check       # types, CSP unit tests, build, then the real hydration check
```

`assert-hydratable` runs **last**, against the artefact the build just produced, and it is the
only step that can see the failure mode it exists to catch. The CSP response header is
byte-identical whether the page hydrates or ships as dead markup, so no header assertion, type
check, unit test or successful build can tell the two apart. Only starting the built server and
reading the served `<script>` tags can. See
[`../docs/embedding-and-identity.md`](../docs/embedding-and-identity.md#1e-the-consoles-content-security-policy).

## Embedding configuration

`NEXT_PUBLIC_FRAME_ANCESTORS` names the parent origins allowed to iframe the console
(space-separated). It is read in three states: **unset** keeps the restrictive `'self'` default;
**set and blank** is refused at build and boot (an empty CSP directive is a parse error browsers
discard, which would silently remove the protection); **set with a value** is used as given. This
matches the backend's `MKT_GOV_FRAME_ANCESTORS` exactly, so the two halves of the embedding
posture cannot disagree. `NEXT_PUBLIC_API_BASE` must be an absolute URL; its origin is what the
CSP `connect-src` widens to.
