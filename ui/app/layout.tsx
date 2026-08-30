import type { Metadata } from "next";
import "./globals.css";
import { ProvenanceBanner } from "../components/ProvenanceBanner";

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
  // The banner renders in BOTH modes, and embedded is the mode that needs it most: a panel
  // inside somebody else's portal is where a viewer has least context about where the answer
  // came from. It is mounted in the LAYOUT rather than in a page because "at the top of every
  // page" is a property of the console, and a page that forgot it would be the one page a
  // screenshot came from.
  return (
    <html lang="en">
      <body className={embed ? undefined : "min-h-screen"}>
        <ProvenanceBanner />
        {children}
      </body>
    </html>
  );
}
