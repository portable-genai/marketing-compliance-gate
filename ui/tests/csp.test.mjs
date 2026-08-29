// Unit cover for the one place the console's CSP is built.
//
// These tests are cheap and they are NOT sufficient. The whole point of
// `scripts/assert-hydratable.mjs` is that the header string asserted here is byte-identical in
// the working case and in the broken one: a statically prerendered route serves the same CSP as a
// dynamic one, and only the script tags in the served markup say whether the nonce reached them.
// A string assertion cannot see whether the page hydrates. What these DO cover is the part that
// is decidable from the string: the directives that must exist, the three-state framing read, and
// the fact that no directive is ever emitted empty.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ConfiguredEmptyError,
  UnhydratableCspError,
  WildcardOriginError,
  assertHydratableCsp,
  contentSecurityPolicy,
  frameAncestors,
  frameOptions,
  generateNonce,
} from "../lib/csp.mjs";

// Every assertion below is about the policy a DEPLOYMENT serves, so every one of them names the
// environment it is asserting. `contentSecurityPolicy` widens `script-src` and `connect-src` on a
// development server and only there, and a test that left NODE_ENV unset would silently be
// checking the dev policy while claiming to pin the shipped one.
const PROD = { NODE_ENV: "production" };

/** Split a policy into a directive -> value map. */
function directives(policy) {
  return new Map(
    policy
      .split(";")
      .map((piece) => piece.trim())
      .filter(Boolean)
      .map((piece) => {
        const [name, ...value] = piece.split(/\s+/);
        return [name, value.join(" ")];
      }),
  );
}

test("the policy carries every directive the fleet standard requires", () => {
  const parsed = directives(contentSecurityPolicy(PROD, "abc123"));
  for (const name of [
    "default-src",
    "base-uri",
    "form-action",
    "object-src",
    "script-src",
    "style-src",
    "connect-src",
    "frame-ancestors",
  ]) {
    assert.ok(parsed.has(name), `missing ${name}`);
  }
  assert.equal(parsed.get("object-src"), "'none'");
  assert.equal(parsed.get("base-uri"), "'self'");
});

test("no directive is ever emitted empty", () => {
  for (const env of [{}, { NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.example" }]) {
    for (const [name, value] of directives(contentSecurityPolicy({ ...PROD, ...env }, "n"))) {
      // An empty directive is a CSP parse error: the browser discards it, taking the restriction
      // with it, so the strictest-looking configuration would end up the least restrictive.
      assert.ok(value, `${name} is empty for env ${JSON.stringify(env)}`);
    }
  }
});

test("script-src takes the nonce and 'strict-dynamic' only when a nonce is supplied", () => {
  assert.equal(
    directives(contentSecurityPolicy(PROD, "abc123")).get("script-src"),
    "'self' 'nonce-abc123' 'strict-dynamic'",
  );
  assert.equal(directives(contentSecurityPolicy(PROD)).get("script-src"), "'self'");
});

test("frame-ancestors is the same three-state read the backend does", () => {
  // Mirrors src/marketing_compliance_gate/api/app.py::_frame_ancestors: unset keeps the default,
  // set-and-blank REFUSES, a real value is used as given.
  assert.equal(frameAncestors({}), "'self'");
  assert.equal(
    frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: " https://a.example  https://b.example " }),
    "https://a.example https://b.example",
  );
  for (const raw of ["", "   ", "\t\n"]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: raw }),
      ConfiguredEmptyError,
      `blank value ${JSON.stringify(raw)} must be refused, not read as the default`,
    );
  }
});

test("a blank framing allowlist takes the whole policy down with it, at build time", () => {
  assert.throws(
    () => contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_FRAME_ANCESTORS: "" }, "n"),
    ConfiguredEmptyError,
  );
});

test("X-Frame-Options is sent only for the two states it can express", () => {
  assert.equal(frameOptions("'self'"), "SAMEORIGIN");
  assert.equal(frameOptions("'none'"), "DENY");
  assert.equal(frameOptions("https://parent.example"), "");
});

test("connect-src widens to the API origin only, never the whole API URL", () => {
  const parsed = directives(
    contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_API_BASE: "https://api.example:8443/v1/reviews" }, "n"),
  );
  assert.equal(parsed.get("connect-src"), "'self' https://api.example:8443");
});

test("a rooted API base stays same-origin rather than being refused", () => {
  // A host portal mounting this console under its own route sets exactly this. Same-origin is
  // already covered by 'self', so it widens nothing, and refusing it answered 500 on a working
  // deployment. What must never happen is the value being dropped while it names a real origin,
  // which is the case below.
  assert.doesNotThrow(() => contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_API_BASE: "/apps/x/api" }, "n"));
});

test("a protocol-relative API base is refused rather than read as same-origin", () => {
  assert.throws(
    () => contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_API_BASE: "//api.example/v1" }, "n"),
    /must name its scheme/,
  );
});

test("an API base that is neither absolute nor rooted is refused", () => {
  assert.throws(
    () => contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_API_BASE: "api.example/v1" }, "n"),
    /NEXT_PUBLIC_API_BASE/,
  );
});

test("every nonce is fresh and base64", () => {
  const nonces = new Set(Array.from({ length: 50 }, () => generateNonce()));
  assert.equal(nonces.size, 50);
  for (const nonce of nonces) assert.match(nonce, /^[A-Za-z0-9+/]+={0,2}$/);
});

test("a layout without force-dynamic is refused, because its HTML cannot carry the nonce", () => {
  assert.throws(
    () => assertHydratableCsp("export default function RootLayout() { return null; }"),
    UnhydratableCspError,
  );
  assert.doesNotThrow(() => assertHydratableCsp('export const dynamic = "force-dynamic";'));
});

test("a wildcard frame-ancestors is refused in every spelling a config can render", () => {
  // The FastAPI half already refuses these. This is the OTHER emitter, and it is the one a
  // browser honours for the document, so closing only the service side left the console
  // framable by any origin while every check stayed green.
  for (const wildcard of ["*", "'*'", "null", "*.*"]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: wildcard }),
      WildcardOriginError,
      `${JSON.stringify(wildcard)} must be refused, not passed through to the header`,
    );
  }
  assert.throws(
    () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.client.example *" }),
    WildcardOriginError,
    "a wildcard standing beside named origins is still a wildcard",
  );
  assert.throws(
    () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "*,https://portal.client.example" }),
    WildcardOriginError,
    "a comma is not CSP list syntax, so a comma-joined wildcard must still be seen",
  );
  // A HOST-SOURCE wildcard is the spelling an exact-token set misses, and CSP honours it: every
  // subdomain may frame the console, including one an attacker takes over or registers on a
  // user-content domain. A real origin never contains an asterisk, so refusing the character
  // outright turns away nothing a deployment could correctly hold.
  for (const hostSource of [
    "https://*.client.example",
    "*.client.example",
    "https://*",
    "https://portal.client.example https://*.evil.example",
  ]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: hostSource }),
      WildcardOriginError,
      `${JSON.stringify(hostSource)} is a host-source wildcard and must be refused`,
    );
  }
});

test("the policy the proxy actually serves refuses a wildcard too", () => {
  // `contentSecurityPolicy` is what `proxy.ts` puts on the document response. Refusing inside
  // the resolver alone would be theatre if this path could still build a policy around it.
  for (const wildcard of ["*", "'*'", "null", "*.*", "https://*.client.example"]) {
    assert.throws(
      () => contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_FRAME_ANCESTORS: wildcard }, "n0nce"),
      WildcardOriginError,
      `the served document policy must not carry frame-ancestors ${wildcard}`,
    );
  }
});

test("a legitimate named allowlist is unaffected by the wildcard refusal", () => {
  // A refusal that also refuses valid input is an outage, not a control.
  assert.equal(
    frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.client.example" }),
    "https://portal.client.example",
  );
  assert.equal(
    frameAncestors({
      NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.client.example https://intranet.client.example",
    }),
    "https://portal.client.example https://intranet.client.example",
  );
  assert.equal(frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "'self'" }), "'self'");
  assert.equal(frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: "'none'" }), "'none'");
  assert.match(
    contentSecurityPolicy({ ...PROD, NEXT_PUBLIC_FRAME_ANCESTORS: "https://portal.client.example" }, "n"),
    /frame-ancestors https:\/\/portal\.client\.example/,
  );
});

test("the unset and emptied states are exactly what they were before wildcards were refused", () => {
  // Pinned so a later edit cannot drift them. THIS repo REFUSES an emptied value rather than
  // mapping it to 'none', mirroring its own FastAPI half; the wildcard case is an addition to
  // that behaviour, never a replacement for it.
  assert.equal(frameAncestors({}), "'self'");
  for (const blank of ["", "   ", "\t", "\n", " \t\n "]) {
    assert.throws(
      () => frameAncestors({ NEXT_PUBLIC_FRAME_ANCESTORS: blank }),
      ConfiguredEmptyError,
      `blank value ${JSON.stringify(blank)} must still be refused as configured-empty`,
    );
  }
});

test("'unsafe-eval' and the HMR websocket exist on the dev server and NOWHERE else", () => {
  // RED before the dev branch existed: `next dev` was served the production policy, so React
  // reported that eval is unavailable, `__next_f` never filled, and the console rendered its
  // controls as dead markup while the header, the type-check, the build and every other test
  // stayed green. Both relaxations are keyed off NODE_ENV alone, so `next build` and `next start`
  // cannot emit either one, and `scripts/assert-hydratable.mjs` re-proves that on the artefact.
  const dev = directives(contentSecurityPolicy({ NODE_ENV: "development" }, "n0nce"));
  assert.match(dev.get("script-src"), /'unsafe-eval'/);
  assert.match(dev.get("connect-src"), /ws: wss:/);

  for (const nonce of [undefined, "n0nce"]) {
    const policy = contentSecurityPolicy(PROD, nonce);
    assert.doesNotMatch(policy, /unsafe-eval/, `unsafe-eval reached production (nonce: ${nonce})`);
    assert.doesNotMatch(policy, /ws:/, `a websocket source reached production (nonce: ${nonce})`);
  }

  // The relaxation widens the two directives it names and nothing else: `'unsafe-inline'` is the
  // token an XSS actually needs in `script-src`, and it is absent in both modes.
  assert.equal(dev.get("default-src"), "'self'");
  assert.equal(dev.get("object-src"), "'none'");
  assert.doesNotMatch(dev.get("script-src"), /unsafe-inline/);
});
