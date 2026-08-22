import type { Citation } from "@/lib/types";

const SOURCE_LABEL: Record<string, string> = {
  rule: "RULE",
  regulation: "REG",
  guideline: "GUIDE",
  policy: "POLICY",
  other: "SRC",
};

export function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) {
    return <div className="text-xs text-ink-400">(no citations)</div>;
  }
  return (
    <div className="mt-2 flex flex-col gap-1.5">
      {citations.map((c, i) => (
        <div
          key={`${c.source_id}-${i}`}
          className="flex items-baseline gap-2 rounded-md border border-ink-200 bg-ink-50 px-2.5 py-1.5"
        >
          <span className="rounded border border-brand-100 bg-brand-50 px-1.5 font-mono text-[11px] font-semibold text-brand-700">
            {SOURCE_LABEL[c.source_type] ?? "SRC"}
          </span>
          <span className="text-xs text-ink-700">{c.title}</span>
          <span className="ml-auto font-mono text-[11px] text-ink-500">{c.source_id}</span>
          {c.url ? (
            <a
              href={c.url}
              target="_blank"
              rel="noreferrer"
              className="text-[11px] text-brand-600"
            >
              open
            </a>
          ) : null}
        </div>
      ))}
    </div>
  );
}
