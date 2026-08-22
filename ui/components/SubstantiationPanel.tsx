import type { ClaimCoverage, SubstantiationAssessment, SubstantiationEvidence } from "@/lib/types";
import { CitationList } from "./CitationList";

/**
 * The green-claims panel: what the asset claims about the environment, what evidence the
 * brand actually holds, and what is missing.
 *
 * Audit-first by design. The verdict and the coverage figure come from the backend's
 * deterministic coverage engine, so this component renders them and never computes one; the
 * narrative underneath is the model's prose about an already-decided result. Every gap names
 * the evidence it is missing and the instrument that requires it, which is what a compliance
 * officer needs in order to sign off or send the asset back.
 */

const VERDICT_STYLE: Record<string, string> = {
  substantiated: "border-emerald-200 bg-emerald-50 text-emerald-700",
  partially_substantiated: "border-amber-300 bg-amber-50 text-amber-800",
  unsubstantiated: "border-red-200 bg-red-50 text-red-700",
  not_applicable: "border-ink-200 bg-ink-50 text-ink-600",
};

const VERDICT_LABEL: Record<string, string> = {
  substantiated: "Substantiated",
  partially_substantiated: "Partially substantiated",
  unsubstantiated: "Unsubstantiated",
  not_applicable: "No green claim made",
};

function humanise(value: string): string {
  return value.replace(/_/g, " ");
}

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

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

function CoverageBar({ coverage }: { coverage: number }) {
  const width = Math.max(0, Math.min(1, coverage)) * 100;
  return (
    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
      <div
        className={`h-full ${coverage >= 1 ? "bg-emerald-500" : coverage > 0 ? "bg-amber-500" : "bg-red-500"}`}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

function ClaimCard({ coverage }: { coverage: ClaimCoverage }) {
  return (
    <div className="border-b border-ink-100 py-3 last:border-0">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[13px] font-semibold text-ink-800">
          {humanise(coverage.claim.category)}
        </span>
        <span className="text-xs text-ink-500">
          &ldquo;{coverage.claim.phrase}&rdquo; in the {coverage.claim.location}
        </span>
        <span
          className={`ml-auto rounded-full border px-2 py-0.5 text-[11px] font-bold ${
            VERDICT_STYLE[coverage.verdict] ?? VERDICT_STYLE.not_applicable
          }`}
        >
          {VERDICT_LABEL[coverage.verdict] ?? coverage.verdict} · {pct(coverage.coverage)}
        </span>
      </div>
      <CoverageBar coverage={coverage.coverage} />

      <dl className="mt-2 grid grid-cols-1 gap-1 text-xs text-ink-600 sm:grid-cols-2">
        <div>
          <dt className="font-semibold text-ink-500">Evidence counted</dt>
          <dd className="font-mono">
            {coverage.satisfied_kinds.map(humanise).join(", ") || "none"}
            {coverage.evidence_ids.length > 0 ? ` (${coverage.evidence_ids.join(", ")})` : ""}
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-ink-500">Still required</dt>
          <dd className="font-mono">{coverage.missing_kinds.map(humanise).join(", ") || "none"}</dd>
        </div>
      </dl>

      {coverage.gaps.length > 0 ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-red-700">
          {coverage.gaps.map((gap, i) => (
            <li key={i}>{gap}</li>
          ))}
        </ul>
      ) : null}

      {coverage.remediation ? (
        <p className="mt-2 text-xs text-ink-600">
          <span className="font-semibold">Fix: </span>
          {coverage.remediation}
        </p>
      ) : null}
    </div>
  );
}

export function SubstantiationPanel({
  assessment,
  evidence,
}: {
  assessment: SubstantiationAssessment;
  evidence: SubstantiationEvidence[];
}) {
  return (
    <div>
      <h1 className="text-lg font-semibold">Green claims &mdash; {assessment.asset_id}</h1>
      <p className="mb-4 text-[13px] text-ink-500">
        <span
          className={`inline-block rounded-full border px-2.5 py-0.5 text-[11px] font-bold ${
            VERDICT_STYLE[assessment.verdict] ?? VERDICT_STYLE.not_applicable
          }`}
        >
          {VERDICT_LABEL[assessment.verdict] ?? assessment.verdict}
        </span>{" "}
        coverage <b className="text-ink-800">{pct(assessment.coverage)}</b> · evidence aged
        against <b className="text-ink-800">{assessment.as_of}</b> · tenant{" "}
        <b className="text-ink-800">{assessment.tenant}</b>
      </p>

      {assessment.requires_human_review ? (
        <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800">
          HUMAN REVIEW REQUIRED &mdash; a green claim never publishes on the agent&rsquo;s say-so.
          A qualified compliance officer signs off in the review console before this asset runs.
        </div>
      ) : null}

      <Panel title="Explanation (narrated from the already-decided result)">
        <p className="leading-relaxed">{assessment.narrative}</p>
      </Panel>

      <Panel title="Claims and their coverage (deterministic coverage engine)">
        {assessment.claims.length === 0 ? (
          <div className="text-xs text-ink-400">
            No environmental claim was detected in this copy, so no substantiation is required.
          </div>
        ) : (
          assessment.claims.map((coverage, i) => <ClaimCard key={i} coverage={coverage} />)
        )}
      </Panel>

      <Panel title="Green-claim rules in force">
        {assessment.findings.length === 0 ? (
          <div className="text-xs text-ink-400">no green-claim rule applies to this asset</div>
        ) : (
          assessment.findings.map((f, i) => (
            <div key={i} className="flex gap-3 border-b border-ink-100 py-2 last:border-0">
              <span
                className={`h-fit rounded px-1.5 text-[11px] font-bold ${
                  f.status === "fail" ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"
                }`}
              >
                {f.status === "fail" ? "FAIL" : "PASS"}
              </span>
              <div className="min-w-0">
                <div className="text-[13px]">
                  <span className="font-mono text-xs text-ink-500">{f.rule_id}</span>{" "}
                  <span className="text-ink-800">{f.message}</span>
                </div>
                {f.evidence ? (
                  <div className="text-xs text-ink-500">evidence: {f.evidence}</div>
                ) : null}
                {f.remediation ? (
                  <div className="text-xs text-ink-600">fix: {f.remediation}</div>
                ) : null}
              </div>
            </div>
          ))
        )}
      </Panel>

      <Panel title="Evidence on file (your tenant only)">
        {evidence.length === 0 ? (
          <div className="text-xs text-ink-400">
            no substantiation evidence is filed against this asset for your tenant
          </div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="text-ink-500">
              <tr>
                <th className="py-1 pr-3 font-semibold">Id</th>
                <th className="py-1 pr-3 font-semibold">Kind</th>
                <th className="py-1 pr-3 font-semibold">Title</th>
                <th className="py-1 pr-3 font-semibold">Issued</th>
                <th className="py-1 pr-3 font-semibold">Valid until</th>
                <th className="py-1 font-semibold">Independent</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((record) => (
                <tr key={record.id} className="border-t border-ink-100 align-top">
                  <td className="py-1 pr-3 font-mono">{record.id}</td>
                  <td className="py-1 pr-3">{humanise(record.kind)}</td>
                  <td className="py-1 pr-3 text-ink-700">{record.title}</td>
                  <td className="py-1 pr-3">{record.issued_date || "undated"}</td>
                  <td className="py-1 pr-3">{record.valid_until || "no stated expiry"}</td>
                  <td className="py-1">{record.independently_verified ? "yes" : "self-declared"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="Instruments cited">
        <CitationList citations={assessment.citations} />
      </Panel>
    </div>
  );
}
