// The document-layer security headers (Next 16 names this file `proxy.ts`).
//
// The Content-Security-Policy is set HERE rather than in `next.config.mjs` because it carries a
// per-request script nonce, and the static `headers()` table has no way to produce one. Nothing
// here authenticates: identity belongs to the identity-aware proxy in front of this console and
// to the service behind it (see ../docs/embedding-and-identity.md).
//
// The nonce has to reach two places or hydration fails in one of two ways. On the REQUEST
// headers, under the exact name `Content-Security-Policy`, is where Next looks for the nonce it
// stamps onto every script tag it emits; any other header name is silently ignored. On the
// RESPONSE is what the browser enforces. A nonce on only the response blocks the very scripts it
// was added to allow; a nonce on only the request proves nothing.

import { type NextRequest, NextResponse } from "next/server";

import {
  contentSecurityPolicy,
  frameAncestors,
  frameOptions,
  generateNonce,
} from "./lib/csp.mjs";

export function proxy(request: NextRequest) {
  const nonce = generateNonce();
  const csp = contentSecurityPolicy(process.env, nonce);

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);

  const legacy = frameOptions(frameAncestors(process.env));
  if (legacy) response.headers.set("X-Frame-Options", legacy);
  return response;
}

export const config = {
  matcher: "/:path*",
};
