"use client";

import { useEffect, useState } from "react";
import { ReviewView } from "@/components/ReviewView";
import { SubstantiationPanel } from "@/components/SubstantiationPanel";
import { api, API_BASE, ApiError, setDevPersona } from "@/lib/api";
import type {
  AssetType,
  Health,
  Market,
  Persona,
  Review,
  SubstantiationAssessment,
  SubstantiationEvidence,
  Vertical,
} from "@/lib/types";

const IS_EMBEDDED = process.env.NEXT_PUBLIC_EMBED === "1";

const MARKETS: { value: Market; label: string }[] = [
  { value: "JP", label: "Japan (asia-northeast1)" },
  { value: "AU", label: "Australia (australia-southeast1)" },
  { value: "SG", label: "Singapore (asia-southeast1)" },
];
const VERTICALS: { value: Vertical; label: string }[] = [
  { value: "banking", label: "Banking" },
  { value: "online_retail", label: "Online retail" },
];
const ASSET_TYPES: { value: AssetType; label: string }[] = [
  { value: "campaign", label: "Campaign" },
  { value: "creative", label: "Creative" },
  { value: "offer", label: "Offer" },
];

export default function Page() {
  const [body, setBody] = useState("Get guaranteed returns of 4.10% with zero risk-free worry!");
  const [market, setMarket] = useState<Market>("SG");
  const [vertical, setVertical] = useState<Vertical>("banking");
  const [assetType, setAssetType] = useState<AssetType>("creative");
  const [fieldsText, setFieldsText] = useState("");
  const [consentsText, setConsentsText] = useState("");
  const [assetId, setAssetId] = useState("camp-green-au-001");
  const [asOf, setAsOf] = useState("2026-08-05");
  const [review, setReview] = useState<Review | null>(null);
  const [assessment, setAssessment] = useState<SubstantiationAssessment | null>(null);
  const [evidence, setEvidence] = useState<SubstantiationEvidence[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const status = await api.healthz();
      if (cancelled) return;
      setHealth(status);
      // The persona picker is a LOCAL-mode-only convenience: secure profiles resolve
      // identity from the IAP assertion, so /v1/personas is empty there.
      if (status?.profile !== "local") return;
      const list = await api.listPersonas();
      if (cancelled || list.length === 0) return;
      setPersonas(list);
      setSelectedPersona(list[0].id);
      setDevPersona(list[0].id);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function onPersonaChange(id: string) {
    setSelectedPersona(id);
    setDevPersona(id);
  }

  function parseFields(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    text
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .forEach((pair) => {
        const idx = pair.indexOf("=");
        if (idx > 0) out[pair.slice(0, idx).trim()] = pair.slice(idx + 1).trim();
      });
    return out;
  }

  function assetBody() {
    return {
      id: assetId,
      asset_type: assetType,
      title: "Submitted asset",
      body,
      market,
      vertical,
      fields: parseFields(fieldsText),
      granted_consents: consentsText
        .split(",")
        .map((c) => c.trim())
        .filter(Boolean),
    };
  }

  async function onReview() {
    setLoading(true);
    setError(null);
    setReview(null);
    setAssessment(null);
    try {
      setReview(await api.reviewAsset({ asset: assetBody() }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  // The green-claims gate. The tenant is NOT sent: the backend scopes the evidence read to
  // the verified principal, so switching the demo persona to another brand shows that
  // brand's holdings and never this one's.
  async function onSubstantiate() {
    setLoading(true);
    setError(null);
    setReview(null);
    setAssessment(null);
    try {
      const result = await api.substantiate({ asset: assetBody(), as_of: asOf.trim() });
      setAssessment(result);
      setEvidence(await api.listEvidence(assetId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-6xl gap-6 p-6">
      <aside className="w-80 shrink-0">
        {IS_EMBEDDED ? null : (
          <>
            <h1 className="text-base font-semibold">
              D6 Marketing Compliance &amp; Brand Governance
            </h1>
            <p className="mb-4 text-xs text-ink-500">
              Deterministic claim / permission / brand / consent review of Campaigns, Creatives
              and Offers, the marketing maker-checker gate, generic across banking and online
              retail and the JP/AU/SG markets.
            </p>
          </>
        )}

        {personas.length > 0 ? (
          <div className="mb-3 rounded-xl border border-ink-200 bg-white p-4 shadow-panel">
            <label className="mb-1 block text-xs font-semibold text-ink-600">
              Demo identity
            </label>
            <select
              className="w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
              value={selectedPersona}
              onChange={(e) => onPersonaChange(e.target.value)}
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.subject} · {p.tenant}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="rounded-xl border border-ink-200 bg-white p-4 shadow-panel">
          <label className="mb-1 block text-xs font-semibold text-ink-600">
            Marketing copy
          </label>
          <textarea
            className="mb-3 h-24 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />

          <label className="mb-1 block text-xs font-semibold text-ink-600">Market</label>
          <select
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={market}
            onChange={(e) => setMarket(e.target.value as Market)}
          >
            {MARKETS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          <label className="mb-1 block text-xs font-semibold text-ink-600">Vertical</label>
          <select
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={vertical}
            onChange={(e) => setVertical(e.target.value as Vertical)}
          >
            {VERTICALS.map((v) => (
              <option key={v.value} value={v.value}>
                {v.label}
              </option>
            ))}
          </select>

          <label className="mb-1 block text-xs font-semibold text-ink-600">Asset type</label>
          <select
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={assetType}
            onChange={(e) => setAssetType(e.target.value as AssetType)}
          >
            {ASSET_TYPES.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>

          <label className="mb-1 block text-xs font-semibold text-ink-600">
            Fields (key=value, comma-separated)
          </label>
          <input
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={fieldsText}
            onChange={(e) => setFieldsText(e.target.value)}
            placeholder="e.g. discount_pct=90, risk_warning=..."
          />

          <label className="mb-1 block text-xs font-semibold text-ink-600">
            Granted consents (comma-separated)
          </label>
          <input
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={consentsText}
            onChange={(e) => setConsentsText(e.target.value)}
            placeholder="e.g. marketing"
          />

          <label className="mb-1 block text-xs font-semibold text-ink-600">Asset id</label>
          <input
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={assetId}
            onChange={(e) => setAssetId(e.target.value)}
            placeholder="the id the substantiation evidence is filed against"
          />

          <label className="mb-1 block text-xs font-semibold text-ink-600">
            Evidence aged as of (ISO date)
          </label>
          <input
            className="mb-3 w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-sm"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            placeholder="YYYY-MM-DD (blank = today)"
          />

          <button
            onClick={onReview}
            disabled={loading || !body.trim()}
            className="w-full rounded-lg bg-brand-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            {loading ? "Reviewing…" : "Run compliance review"}
          </button>

          <button
            onClick={onSubstantiate}
            disabled={loading || !body.trim()}
            className="mt-2 w-full rounded-lg border border-brand-600 px-3 py-2 text-sm font-semibold text-brand-700 disabled:opacity-40"
          >
            {loading ? "Checking…" : "Check green claims"}
          </button>
        </div>

        <div className="mt-3 rounded-xl border border-ink-200 bg-white p-3 text-xs text-ink-500 shadow-panel">
          <div>
            API <span className="font-mono">{API_BASE}</span>
          </div>
          {health ? (
            <div className="mt-1">
              profile <b className="text-ink-700">{health.profile}</b> · status{" "}
              <b className="text-ink-700">{health.status}</b>
            </div>
          ) : (
            <div className="mt-1 text-amber-700">backend not reachable (start the API)</div>
          )}
        </div>
      </aside>

      <section className="min-w-0 flex-1">
        {error ? (
          <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        {!review && !assessment && !error ? (
          <div className="rounded-xl border border-dashed border-ink-200 bg-white p-10 text-center text-sm text-ink-400">
            Paste marketing copy, pick a market and vertical, then run a compliance review or
            check the asset&rsquo;s green claims against the evidence on file.
          </div>
        ) : null}
        {review ? <ReviewView review={review} /> : null}
        {assessment ? (
          <SubstantiationPanel assessment={assessment} evidence={evidence} />
        ) : null}
      </section>
    </main>
  );
}
