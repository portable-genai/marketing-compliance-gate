// The console's Content-Security-Policy, in ONE module so it is built once and read everywhere.
//
// Living inline in `next.config.mjs`, emitted through the static `headers()` table, would carry
// a single directive: `frame-ancestors`. That table cannot express a per-request
// value, which is exactly what a script nonce is, so the console shipped with no `default-src`,
// no `script-src`, no `object-src` and no `base-uri` at all, and a browser fell back to allowing
// every script, frame, form target and plugin the page asked for.
//
// Adding `script-src` alone would have made things worse rather than better. Next serves its
// hydration bootstrap as an INLINE script carrying the Flight payload, so a bare
// `script-src 'self'` blocks it: `__next_f` never fills, React never attaches, and the console
// renders its controls as dead markup while the headers, the type-check, the build and every
// test stay green. So `script-src` takes a per-request nonce, minted in `proxy.ts`, plus
// `'strict-dynamic'` so the nonced bootstrap may load its own chunks and nothing else may run.
//
// `next.config.mjs` no longer emits a `Content-Security-Policy` at all. Two layers both setting
// it would hand the browser two policies to intersect, and the stricter one wins per directive,
// which would quietly reinstate the defect this module exists to remove.

/** Raised when an env var is set but names nothing, so the intent it expressed selects nothing. */
export class ConfiguredEmptyError extends Error {}

const FRAME_ANCESTORS_ENV = "NEXT_PUBLIC_FRAME_ANCESTORS";

/** What `frame-ancestors` says when nobody named a parent origin: framed by itself and nobody else. */
export const DEFAULT_FRAME_ANCESTORS = "'self'";

/**
 * Origin of the API base, when the console is deployed cross-origin from its service.
 *
 * A rooted path is the SAME-ORIGIN deployment, which is what a host portal mounting this console
 * under its own route sets. There is no second origin to name there, and `'self'` already permits
 * it, so "" is the correct answer rather than an error: refusing it made the console answer 500
 * behind the portal, which is a working configuration reported as a broken one.
 *
 * A protocol-relative value is still refused. It names a DIFFERENT host while looking rooted, so
 * treating it as same-origin would drop a genuinely cross-origin API out of `connect-src`, which
 * is the silent-drop this function exists to prevent.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string} an origin to add to `connect-src`, or "" when same-origin
 */
function apiOrigin(env) {
  const raw = (env.NEXT_PUBLIC_API_BASE || "").trim();
  if (!raw) return "";
  if (raw.startsWith("//")) {
    throw new Error(`NEXT_PUBLIC_API_BASE must name its scheme, got: ${raw}`);
  }
  if (raw.startsWith("/")) return "";
  try {
    return new URL(raw).origin;
  } catch {
    throw new Error(
      `NEXT_PUBLIC_API_BASE must be an absolute URL or a rooted same-origin path, got: ${raw}`,
    );
  }
}

/** Raised when an embedding variable names a wildcard instead of the origins it should allow. */
export class WildcardOriginError extends Error {}

/**
 * Exact tokens that must never be accepted as a framing ancestor.
 *
 * `'*'` is what a quoted Terraform variable or a YAML string renders. `*.*` is a host pattern
 * matching every name with a dot in it. `null` is the one that reads as harmless and is not: it
 * is not a wildcard by spelling and behaves as one, because a SANDBOXED iframe presents the
 * origin `null`, so a policy naming it hands framing rights to any page that can open one.
 */
const WILDCARD_TOKENS = new Set(["*", "'*'", "null", "*.*"]);

/**
 * True when an entry may not be a framing ancestor.
 *
 * Exact matching alone is not enough. `https://*.client.example` is in no token set, and CSP
 * honours a host-source wildcard: every subdomain may frame the console, including one an
 * attacker obtains by takeover or on a user-content subdomain. So ANY entry containing an
 * asterisk is refused, which turns away nothing a deployment could correctly hold, since a real
 * origin never contains the character.
 *
 * @param {string} entry
 * @returns {boolean}
 */
function isWildcard(entry) {
  return WILDCARD_TOKENS.has(entry) || entry.includes("*");
}

/**
 * Refuse an allowlist that names a wildcard, before the value can reach a response header.
 *
 * `src/marketing_compliance_gate/api/app.py::_refuse_wildcard` does this for the API surface, and it was the only half that
 * did. There are two `frame-ancestors` emitters, and the one a browser consults before framing
 * this console is the header on the DOCUMENT, which Next serves under the policy this module
 * builds. This resolver passed its configured value straight through, so a deployment whose
 * variable rendered a wildcard refused to start the API and still served a document any origin
 * could frame. The half that was closed is not the half that governs.
 *
 * Tokens are split on commas as well as whitespace. CSP source lists are space separated, so a
 * comma form never names a valid origin anyway; splitting on it here means
 * `*,https://portal.example` is seen as the wildcard it contains rather than as one opaque token
 * that merely fails to equal `*`.
 *
 * @param {string} raw the configured value, before it is normalised
 * @param {string} envName the variable it came from, for the message
 * @throws {WildcardOriginError}
 */
function refuseWildcards(raw, envName) {
  for (const piece of String(raw).split(/[\s,]+/)) {
    const entry = piece.trim();
    if (entry && isWildcard(entry)) {
      throw new WildcardOriginError(
        `${envName} contains ${JSON.stringify(entry)}, which lets ANY origin frame this ` +
          "console: a wildcard frame-ancestors is the clickjacking control switched off, not " +
          `configured. Name the exact parent origins that may frame it, or unset ${envName} to ` +
          "keep the restrictive default.",
      );
    }
  }
}

/**
 * Who may frame this console, read in THREE states so it agrees with the backend.
 *
 * `src/marketing_compliance_gate/api/app.py::_frame_ancestors` resolves the same question for the
 * API surface, and it refuses a set-but-blank value rather than reading it as the default: an
 * operator who empties the variable HAS expressed an intent, and answering with the shipped
 * default would make a deployment that deliberately locked itself down indistinguishable from
 * one that lost the variable. Emitting the blank value verbatim is worse still, because an empty
 * `frame-ancestors` directive is a CSP parse error that browsers discard, taking the restriction
 * with it. The two halves of the embedding posture must not disagree, so this mirrors it exactly:
 *
 * * unset: no intent expressed, so the documented restrictive default stands.
 * * set and blank: refused. `next.config.mjs` calls this at module scope, which `next build` and
 *   `next start` both evaluate, so the refusal is a build/boot refusal rather than a surprise on
 *   some later request.
 * * set with a value: used as given.
 *
 * @param {Record<string, string | undefined>} env
 * @returns {string}
 */
export function frameAncestors(env) {
  const raw = env[FRAME_ANCESTORS_ENV];
  if (raw === undefined || raw === null) return DEFAULT_FRAME_ANCESTORS;
  const named = String(raw).trim().split(/\s+/).filter(Boolean);
  if (named.length === 0) {
    throw new ConfiguredEmptyError(
      `${FRAME_ANCESTORS_ENV} is set but empty. An empty CSP frame-ancestors directive is ` +
        "discarded by browsers, which would leave the console with no clickjacking protection " +
        `at all. Unset ${FRAME_ANCESTORS_ENV} to keep the ${DEFAULT_FRAME_ANCESTORS} default, ` +
        "or name the parent origins that may frame it.",
    );
  }
  refuseWildcards(raw, FRAME_ANCESTORS_ENV);
  return named.join(" ");
}

/**
 * The pre-CSP `X-Frame-Options` backstop, for the two policies it can actually express.
 *
 * A NAMED parent origin has no `X-Frame-Options` spelling, so it gets none rather than a
 * `DENY`/`SAMEORIGIN` that contradicts the CSP in an older agent.
 *
 * @param {string} ancestors the resolved `frame-ancestors` value
 * @returns {string} the header value, or "" when none should be sent
 */
export function frameOptions(ancestors) {
  if (ancestors === "'self'") return "SAMEORIGIN";
  if (ancestors === "'none'") return "DENY";
  return "";
}

/**
 * The full default-deny policy.
 *
 * `style-src` carries `'unsafe-inline'` because the Next runtime injects critical CSS and there
 * is no nonce path for it. `script-src` does NOT: it takes the per-request nonce plus
 * `'strict-dynamic'`. Passing no nonce yields the strict `'self'` form, which is correct for any
 * response that is not a Next-rendered document and wrong for one that is.
 *
 * @param {Record<string, string | undefined>} env
 * @param {string} [nonce] per-request nonce from {@link generateNonce}
 * @returns {string}
 */
export function contentSecurityPolicy(env, nonce) {
  const connectSrc = ["'self'", apiOrigin(env)].filter(Boolean).join(" ");
  const scriptSrc = nonce
    ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`
    : "script-src 'self'";
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src ${connectSrc}`,
    `frame-ancestors ${frameAncestors(env)}`,
  ].join("; ");
}

/** A fresh per-request nonce. Base64 of 16 random bytes from the Web Crypto global. */
export function generateNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

/** Raised when the nonce policy and the rendering mode disagree, which serves un-hydratable HTML. */
export class UnhydratableCspError extends Error {}

/**
 * Refuse a build whose CSP mints a nonce the rendered HTML can never carry.
 *
 * Next can only stamp a per-request nonce onto the scripts of a DYNAMICALLY rendered route. A
 * statically prerendered page was built before the nonce existed, so it emits bare script tags
 * while the header advertises a nonce, and because `'strict-dynamic'` switches off the `'self'`
 * fallback, that combination blocks strictly MORE than the unfixed policy did. The failure is
 * invisible to every check that does not execute the page, so it is refused at build time.
 *
 * No I/O happens here: the caller passes the source as a string, which keeps this module
 * importable from the request-time proxy.
 *
 * @param {string} layoutSource contents of `app/layout.tsx`
 * @throws {UnhydratableCspError}
 */
export function assertHydratableCsp(layoutSource) {
  if (!/export\s+const\s+dynamic\s*=\s*["']force-dynamic["']/.test(layoutSource)) {
    throw new UnhydratableCspError(
      'app/layout.tsx must set `export const dynamic = "force-dynamic"`. The CSP mints a ' +
        "per-request nonce, and Next can only stamp it onto script tags for a dynamically " +
        "rendered route. Statically prerendered HTML was built before the nonce existed, so " +
        "every script is blocked and the page never hydrates.",
    );
  }
}

/**
 * Resolve every policy input, for the side effect of refusing one nobody chose.
 *
 * Called at module scope from `next.config.mjs`, so a deployment whose framing allowlist rendered
 * blank never comes up at all. A refusal at boot is the one outcome a two-state read cannot
 * imitate.
 *
 * @param {Record<string, string | undefined>} env
 */
export function assertCspConfigured(env) {
  contentSecurityPolicy(env, "boot-check");
}
