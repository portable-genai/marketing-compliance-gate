import type { Review } from "@/lib/types";
import { CitationList } from "./CitationList";

const MARKET_LABEL: Record<string, string> = {
  JP: "Japan",
  AU: "Australia",
  SG: "Singapore",
};
const VERTICAL_LABEL: Record<string, string> = {
  banking: "Banking",
  online_retail: "Online retail",
};
const SEV_COLOR: Record<string, string> = {
  low: "bg-ink-100 text-ink-600",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-orange-100 text-orange-700",
  critical: "bg-red-100 text-red-700",
};

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-4 rounded-xl border border-ink-200 bg-white shadow-panel">
      <h2 className="border-b border-ink-100 px-4 py-2.5 text-[13px] font-semibold text-ink-800">
        {title}
      </h2>
      <div className="p-4">{children}</div>
    </section>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span className="mr-1.5 inline-block rounded-full border border-brand-100 bg-brand-50 px-2.5 py-0.5 text-[11px] font-semibold text-brand-700">
      {children}
    </span>
  );
}

export function ReviewView({ review }: { review: Review }) {
  const compliant = review.outcome === "compliant";
  return (
    <div>
      <h1 className="text-lg font-semibold">Compliance review — {review.asset_id}</h1>
      <p className="mb-4 text-[13px] text-ink-500">
        <Pill>{MARKET_LABEL[review.market] ?? review.market}</Pill>
        <Pill>{VERTICAL_LABEL[review.vertical] ?? review.vertical}</Pill>
        <Pill>{review.asset_type}</Pill>
        <span
          className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-bold ${
            compliant
              ? "border border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border border-red-200 bg-red-50 text-red-700"
          }`}
        >
          {compliant ? "Compliant" : "Non-compliant"}
        </span>{" "}
        id <b className="text-ink-800">{review.id}</b>
      </p>

      {review.requires_human_review ? (
        <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800">
          HUMAN REVIEW REQUIRED — maker-checker gate. Do not run this asset until a
          qualified compliance officer signs off.
        </div>
      ) : null}

      <Panel title="Summary">
        <p className="leading-relaxed">{review.summary}</p>
      </Panel>

      <Panel title="Findings (deterministic rule engine)">
        {review.findings.length === 0 ? (
          <div className="text-xs text-ink-400">none</div>
        ) : (
          review.findings.map((f, i) => (
            <div key={i} className="flex gap-3 border-b border-ink-100 py-2 last:border-0">
              <span
                className={`h-fit rounded px-1.5 text-[11px] font-bold ${
                  f.status === "fail"
                    ? "bg-red-50 text-red-700"
                    : "bg-emerald-50 text-emerald-700"
                }`}
              >
                {f.status.toUpperCase()}
              </span>
              <span
                className={`h-fit rounded px-1.5 text-[11px] font-bold ${
                  SEV_COLOR[f.severity] ?? SEV_COLOR.medium
                }`}
              >
                {f.severity}
              </span>
              <div className="flex-1">
                <b>{f.rule_id}</b>{" "}
                <span className="text-xs text-ink-400">{f.rule_kind}</span> — {f.message}
                {f.evidence ? (
                  <div className="text-xs text-ink-400">evidence: {f.evidence}</div>
                ) : null}
                {f.remediation ? (
                  <div className="text-xs text-ink-600">fix: {f.remediation}</div>
                ) : null}
                <CitationList citations={f.citations} />
              </div>
            </div>
          ))
        )}
      </Panel>

      <Panel title="Consent checks">
        {review.consent_checks.length === 0 ? (
          <div className="text-xs text-ink-400">none</div>
        ) : (
          review.consent_checks.map((c, i) => (
            <div
              key={i}
              className="flex items-center gap-3 border-b border-ink-100 py-2 last:border-0"
            >
              <div className="flex-1">
                {c.purpose}{" "}
                <span className="text-xs text-ink-400">· rule {c.rule_id}</span>
              </div>
              <span
                className={`font-semibold ${
                  c.granted ? "text-emerald-600" : "text-red-600"
                }`}
              >
                {c.granted ? "granted" : "MISSING"}
              </span>
            </div>
          ))
        )}
      </Panel>

      <Panel title="Cited rules">
        <CitationList citations={review.citations} />
      </Panel>
    </div>
  );
}
