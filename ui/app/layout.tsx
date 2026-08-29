import type { Metadata } from "next";
import "./globals.css";

// Required by the nonce CSP, not a performance preference. `proxy.ts` mints a per-request script
// nonce and Next can only stamp it onto the script tags of a DYNAMICALLY rendered route;
// statically prerendered HTML was built before the nonce existed, so nothing would carry it and
// `'strict-dynamic'` would block every script. `next.config.mjs` refuses to build without this.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Marketing Compliance and Brand Governance",
  description:
    "Cited marketing-compliance reviews from a deterministic claim / permission / brand / consent rule engine, the marketing maker-checker gate, generic across banking and online retail and the JP/AU/SG markets.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // EMBED mode: the host page owns the outer chrome, so drop the full-height min-h-screen
  // shell (and page.tsx drops its header/branding) and let the host frame size us.
  const embed = process.env.NEXT_PUBLIC_EMBED === "1";
  return (
    <html lang="en">
      <body className={embed ? undefined : "min-h-screen"}>{children}</body>
    </html>
  );
}
