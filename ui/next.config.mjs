/** @type {import('next').NextConfig} */
// The Content-Security-Policy and X-Frame-Options are NOT set here. They carry a per-request
// script nonce, which a static `headers()` table cannot express, so `proxy.ts` owns them and
// builds them from `lib/csp.mjs`. Setting the policy in both places would hand the browser two
// policies to intersect, and the stricter one wins per directive, which would reinstate the
// frame-ancestors-only policy that left this console with no `default-src`, no `script-src`, no
// `object-src` and no `base-uri` at all.
//
// What IS here are the two refusals. `next build` and `next start` both evaluate this file at
// module scope, so a layout that has lost its `force-dynamic` (and therefore cannot carry the
// nonce), or a framing allowlist that renders blank, fails the build instead of shipping a
// console whose controls silently do nothing or whose clickjacking protection silently vanished.
import { readFileSync } from "node:fs";

import { assertCspConfigured, assertHydratableCsp } from "./lib/csp.mjs";

assertHydratableCsp(readFileSync(new URL("./app/layout.tsx", import.meta.url), "utf8"));
assertCspConfigured(process.env);

// basePath / assetPrefix let the console mount under a reverse-proxy sub-path (e.g.
// portal.client.com/compliance/*) so it embeds same-origin. Blank => standalone unchanged.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  reactStrictMode: true,
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // Only the headers a static table can express correctly. Anything per-request lives
          // in proxy.ts.
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
