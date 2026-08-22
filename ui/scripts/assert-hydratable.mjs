#!/usr/bin/env node
// Prove, against a real production server, that the shipped console page can hydrate.
//
// Everything cheaper than this has been fooled by the defect it catches. A unit test asserts the
// CSP string, and the string is right. `tsc --noEmit` is clean. `next build` succeeds. The page
// renders, the headers are correct, and a screenshot looks exactly like a working console. What
// gets shipped can still be dead markup: Next serves its hydration bootstrap as an INLINE script
// carrying the Flight payload, so a `script-src` without a matching nonce blocks it, `__next_f`
// never fills, React never attaches, and no control on the page does anything.
//
// So this check refuses to reason about the policy at all. It starts the BUILT server, fetches
// the document a browser would fetch, and asserts three things about the bytes that come back:
//
//   1. The standard directives are all present, and none of them is empty.
//   2. The response carries a `script-src` with a nonce.
//   3. EVERY `<script>` tag in the document carries that same nonce.
//
// Rule 3 is the one that matters, and it is the one no header assertion can express: the header
// is byte-identical in the working case and in the broken one. A statically prerendered page was
// built before the nonce existed, so it emits bare script tags while the header advertises a
// nonce, and because `'strict-dynamic'` switches off the `'self'` fallback, that combination
// blocks strictly MORE than a plain `script-src 'self'` did. Header and markup have to agree, and
// only the markup knows.
//
// Usage: node scripts/assert-hydratable.mjs [port]
// Expects `npm run build` to have run. Exits non-zero with the reason on any failure.

import { spawn } from "node:child_process";

const REQUESTED_PORT = process.argv[2] ?? "0";
if (!/^\d+$/.test(REQUESTED_PORT)) {
  throw new Error("port must be a non-negative integer");
}
const BOOT_TIMEOUT_MS = 90_000;
const POLL_MS = 250;

/** Directives whose absence is the fleet defect this check exists to prevent regressing. */
const REQUIRED_DIRECTIVES = [
  "default-src",
  "script-src",
  "object-src",
  "base-uri",
  "frame-ancestors",
];

function fail(message) {
  console.error(`FAIL ${message}`);
  process.exitCode = 1;
}

async function waitForServer(url, deadline) {
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { redirect: "manual" });
      if (response.status < 500) return response;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  return null;
}

const server = spawn("npx", ["next", "start", "-p", REQUESTED_PORT], {
  // Resolved from this file so `npm --prefix ui run assert-hydratable` works from any cwd.
  cwd: new URL("..", import.meta.url),
  env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let serverLog = "";
let reportedPort = null;
let exited = false;
function capture(chunk) {
  const text = chunk.toString();
  serverLog += text;
  const match = text.match(/http:\/\/localhost:(\d+)/);
  if (match) reportedPort = Number(match[1]);
}
server.stdout.on("data", capture);
server.stderr.on("data", capture);
server.on("exit", () => {
  exited = true;
});

async function waitForReportedPort(deadline) {
  while (Date.now() < deadline && reportedPort === null && !exited) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  return reportedPort;
}

try {
  const port = await waitForReportedPort(Date.now() + BOOT_TIMEOUT_MS);
  if (port === null) throw new Error(`this Next child never reported a bound port\n${serverLog}`);
  if (REQUESTED_PORT !== "0" && port !== Number(REQUESTED_PORT)) {
    throw new Error(`requested ${REQUESTED_PORT}, but this child bound ${port}`);
  }
  const url = `http://127.0.0.1:${port}/`;
  const response = await waitForServer(url, Date.now() + BOOT_TIMEOUT_MS);
  if (exited) {
    fail(`this Next child exited before its document was checked\n${serverLog}`);
  } else if (!response) {
    fail(`the built server never answered on ${url} within ${BOOT_TIMEOUT_MS}ms\n${serverLog}`);
  } else {
    const csp = response.headers.get("content-security-policy") ?? "";
    const html = await response.text();

    const directives = new Map(
      csp
        .split(";")
        .map((piece) => piece.trim())
        .filter(Boolean)
        .map((piece) => {
          const [name, ...value] = piece.split(/\s+/);
          return [name.toLowerCase(), value.join(" ")];
        }),
    );

    for (const name of REQUIRED_DIRECTIVES) {
      if (!directives.has(name)) {
        fail(`the response CSP has no \`${name}\` directive at all. CSP: ${csp || "(none)"}`);
      }
    }
    // An empty directive is a CSP parse error: the browser discards it, so the restriction the
    // operator asked for silently disappears. Never emit one.
    for (const [name, value] of directives) {
      if (!value) {
        fail(`the CSP directive \`${name}\` is empty, which browsers discard as a parse error`);
      }
    }

    const nonceInHeader = csp.match(/'nonce-([^']+)'/)?.[1];
    if (!nonceInHeader) {
      fail(
        "no nonce in the response CSP, so Next's inline hydration bootstrap is blocked, `__next_f` " +
          `never fills and React never attaches. CSP: ${csp || "(none)"}`,
      );
    }

    const scriptTags = html.match(/<script\b[^>]*>/g) ?? [];
    if (scriptTags.length === 0) {
      fail("the document carries no script tags at all, which is not a hydrating page");
    }

    const unnonced = scriptTags.filter((tag) => !tag.includes(`nonce="${nonceInHeader}"`));
    if (nonceInHeader && unnonced.length > 0) {
      fail(
        `${unnonced.length} of ${scriptTags.length} script tags do not carry the CSP nonce, so ` +
          "the browser blocks them and the page never hydrates. This is what a statically " +
          'prerendered route looks like: check that app/layout.tsx sets `export const dynamic = ' +
          '"force-dynamic"`.\n  ' +
          unnonced.slice(0, 3).join("\n  "),
      );
    }

    if (process.exitCode !== 1) {
      console.log(
        `OK every one of the ${scriptTags.length} script tags carries the CSP nonce; the page hydrates.`,
      );
    }
  }
} finally {
  server.kill("SIGTERM");
}
