#!/usr/bin/env python3
"""Render the D6 audit-first console from the demo JSON into static HTML pages.

Server-side, dependency-free rendering of a cited :class:`Review` (outcome, the findings
with their severity / rule / evidence / remediation and provenance, the consent checks,
the rule citations, and the maker-checker "human review required" banner). It reuses the
exact palette of the thin Next.js console so screenshots match the live UI, and runs
entirely offline over the obviously-fictional synthetic reviews written by ``scripts/demo.py``.

    PYTHONPATH=src python scripts/demo.py
    PYTHONPATH=src python scripts/render_review_ui.py scripts/out

Writes ``index.html`` (a small chooser) plus one ``<review-id>.html`` per review. The
rendering functions are also imported by ``scripts/demo_server.py`` for the presenter demo.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

SEV_COLOR = {
    "low": ("#eef2f7", "#546b8b"),
    "medium": ("#fef3c7", "#92400e"),
    "high": ("#ffedd5", "#c2410c"),
    "critical": ("#fee2e2", "#b91c1c"),
}
SOURCE_LABEL = {
    "rule": "RULE",
    "regulation": "REG",
    "guideline": "GUIDE",
    "policy": "POLICY",
    "other": "SRC",
}
MARKET_LABEL = {"JP": "Japan", "AU": "Australia", "SG": "Singapore"}
VERTICAL_LABEL = {"banking": "Banking", "online_retail": "Online retail"}
OUTCOME_LABEL = {"compliant": "Compliant", "non_compliant": "Non-compliant"}

CSS = """
:root{--ink-50:#f5f7fa;--ink-100:#e6ebf2;--ink-200:#cdd7e4;--ink-300:#a6b6cc;
--ink-400:#7790ae;--ink-500:#546b8b;--ink-600:#3f5470;--ink-700:#33445b;--ink-800:#1f2a3a;
--brand-50:#eef4ff;--brand-100:#dbe7ff;--brand-600:#2945d6;--brand-700:#2237ad;
--ok:#059669;--ok-bg:#ecfdf5;--bad:#b91c1c;--bad-bg:#fef2f2;
--warn:#d97706;--warn-bg:#fffbeb;
--shadow:0 1px 2px rgba(11,16,26,.06),0 8px 24px rgba(11,16,26,.06);}
*{box-sizing:border-box}
body{margin:0;background:var(--ink-50);color:var(--ink-800);
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
font-size:14px;line-height:1.5;padding:24px 18px}
.wrap{max-width:920px;margin:0 auto}
h1{font-size:18px;margin:0 0 2px}
.sub{color:var(--ink-500);font-size:13px;margin:0 0 16px}
.sub b{color:var(--ink-800)}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:2px 9px;border-radius:999px;
border:1px solid var(--brand-100);background:var(--brand-50);color:var(--brand-700);margin-right:6px}
.outcome{display:inline-block;font-size:12px;font-weight:700;padding:3px 11px;border-radius:999px}
.outcome.compliant{background:var(--ok-bg);color:var(--ok);border:1px solid #a7f3d0}
.outcome.non_compliant{background:var(--bad-bg);color:var(--bad);border:1px solid #fecaca}
.panel{border:1px solid var(--ink-200);background:#fff;border-radius:10px;box-shadow:var(--shadow);margin-bottom:16px}
.panel>h2{border-bottom:1px solid var(--ink-100);padding:11px 16px;margin:0;font-size:13px;font-weight:600;color:var(--ink-800)}
.panel>.body{padding:16px}
.review{border:1px solid #fcd34d;background:var(--warn-bg);color:#92400e;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:14px}
.summary{font-size:14px;line-height:1.6}
.row{display:flex;gap:10px;align-items:baseline;padding:8px 0;border-bottom:1px solid var(--ink-100)}
.row:last-child{border-bottom:0}
.sev{font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;white-space:nowrap}
.status{font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;white-space:nowrap}
.status.fail{background:var(--bad-bg);color:var(--bad)}
.status.pass{background:var(--ok-bg);color:var(--ok)}
.muted{color:var(--ink-400);font-size:12px}
.fix{color:var(--ink-600);font-size:12px;margin-top:2px}
.cites{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.cite{display:flex;gap:8px;align-items:baseline;border:1px solid var(--ink-200);background:var(--ink-50);border-radius:7px;padding:7px 10px}
.cite .src{font-family:ui-monospace,Menlo,monospace;font-size:11px;font-weight:600;color:var(--brand-700);background:var(--brand-50);border:1px solid var(--brand-100);border-radius:5px;padding:1px 6px;white-space:nowrap}
.cite .title{font-size:12px;color:var(--ink-700)}
.cite .id{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink-500);margin-left:auto;white-space:nowrap}
.cite a{color:var(--brand-600);text-decoration:none;font-size:11px}
a.choose{display:block;padding:10px 12px;border:1px solid var(--ink-200);border-radius:8px;background:#fff;margin-bottom:8px;text-decoration:none;color:var(--ink-800)}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def slug(title: str) -> str:
    """Stable, styling-independent identifier for a result panel (F2 evidence hook).

    Panel titles are prose and the CSS classes are cosmetic, so neither is a safe anchor for
    an anti-rot assertion. The slug is: lowercase, non-alphanumerics collapsed to one dash.
    """
    out: list[str] = []
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
        f"<div class='wrap'>{body}</div></body></html>"
    )


def _citations(citations: list[dict[str, Any]], scope: str = "") -> str:
    if not citations:
        return f"<div class='muted' data-citation-count='0' data-citation-scope='{esc(scope)}'>(no citations)</div>"
    rows = []
    for c in citations:
        label = SOURCE_LABEL.get(str(c.get("source_type")), "SRC")
        url = c.get("url") or ""
        link = f"<a href='{esc(url)}'>open</a>" if url else ""
        rows.append(
            f"<div class='cite' data-citation-source='{esc(c.get('source_id'))}' "
            f"data-citation-type='{esc(c.get('source_type'))}'>"
            f"<span class='src'>{esc(label)}</span>"
            f"<span class='title'>{esc(c.get('title'))}</span>"
            f"<span class='id'>{esc(c.get('source_id'))}</span>{link}"
            "</div>"
        )
    return (
        f"<div class='cites' data-citation-count='{len(citations)}' "
        f"data-citation-scope='{esc(scope)}'>" + "".join(rows) + "</div>"
    )


def _panel(title: str, body: str) -> str:
    return (
        f"<div class='panel' data-panel='{slug(title)}'><h2>{esc(title)}</h2>"
        f"<div class='body' data-panel-body='{slug(title)}'>{body}</div></div>"
    )


def render_review(data: dict[str, Any]) -> str:
    """Render one cited Review dict into a standalone HTML page."""
    market = MARKET_LABEL.get(str(data.get("market")), str(data.get("market")))
    vertical = VERTICAL_LABEL.get(str(data.get("vertical")), str(data.get("vertical")))
    outcome = str(data.get("outcome"))
    findings = data.get("findings", [])
    consent_checks = data.get("consent_checks", [])
    failing = sum(1 for f in findings if str(f.get("status")) == "fail")
    missing_consent = sum(1 for c in consent_checks if not c.get("granted"))
    # The load-bearing figures of a review, as stable attributes: styling and prose can be
    # rewritten freely, but these cannot silently stop matching what the engine decided.
    hooks = (
        f"<div data-review-id='{esc(data.get('id'))}' "
        f"data-review-asset='{esc(data.get('asset_id'))}' "
        f"data-review-market='{esc(data.get('market'))}' "
        f"data-review-vertical='{esc(data.get('vertical'))}' "
        f"data-review-outcome='{esc(outcome)}' "
        f"data-review-findings='{len(findings)}' "
        f"data-review-failing='{failing}' "
        f"data-review-consent-checks='{len(consent_checks)}' "
        f"data-review-consent-missing='{missing_consent}' "
        f"data-review-citations='{len(data.get('citations', []))}' "
        f"data-review-human-review='{str(bool(data.get('requires_human_review'))).lower()}'></div>"
    )
    head = (
        hooks + f"<h1>Compliance review — {esc(data.get('asset_id'))}</h1>"
        f"<p class='sub'><span class='pill'>{esc(market)}</span>"
        f"<span class='pill'>{esc(vertical)}</span>"
        f"<span class='pill'>{esc(data.get('asset_type'))}</span> "
        f"<span class='outcome {esc(outcome)}'>{esc(OUTCOME_LABEL.get(outcome, outcome))}</span>"
        f" &nbsp; id <b>{esc(data.get('id'))}</b></p>"
    )
    review = ""
    if data.get("requires_human_review"):
        review = (
            "<div class='review' data-maker-checker='required'>HUMAN REVIEW REQUIRED — "
            "maker-checker gate. Do not run this "
            "asset until a qualified compliance officer signs off.</div>"
        )

    summary = _panel("Summary", f"<div class='summary'>{esc(data.get('summary'))}</div>")

    finding_rows = []
    for f in findings:
        status = str(f.get("status"))
        bg, fg = SEV_COLOR.get(str(f.get("severity")), SEV_COLOR["medium"])
        ev = (
            f"<div class='muted'>evidence: {esc(f.get('evidence'))}</div>"
            if f.get("evidence")
            else ""
        )
        fix = (
            f"<div class='fix'>fix: {esc(f.get('remediation'))}</div>"
            if f.get("remediation")
            else ""
        )
        finding_rows.append(
            f"<div class='row' data-finding-rule='{esc(f.get('rule_id'))}' "
            f"data-finding-status='{esc(status)}' "
            f"data-finding-severity='{esc(f.get('severity'))}' "
            f"data-finding-kind='{esc(f.get('rule_kind'))}'>"
            f"<span class='status {esc(status)}'>{esc(status.upper())}</span>"
            f"<span class='sev' style='background:{bg};color:{fg}'>{esc(f.get('severity'))}</span>"
            f"<div style='flex:1'><b>{esc(f.get('rule_id'))}</b> "
            f"<span class='muted'>{esc(f.get('rule_kind'))}</span> — {esc(f.get('message'))}"
            f"{ev}{fix}{_citations(f.get('citations', []), scope='finding')}</div></div>"
        )
    findings_panel = _panel(
        "Findings (deterministic rule engine)",
        f"<div data-finding-count='{len(finding_rows)}' data-finding-failing='{failing}'>"
        + ("".join(finding_rows) or "<div class='muted'>none</div>")
        + "</div>",
    )

    consent_rows = []
    for c in consent_checks:
        granted = c.get("granted")
        state = "granted" if granted else "MISSING"
        color = "var(--ok)" if granted else "var(--bad)"
        consent_rows.append(
            f"<div class='row' data-consent-purpose='{esc(c.get('purpose'))}' "
            f"data-consent-rule='{esc(c.get('rule_id'))}' "
            f"data-consent-granted='{str(bool(granted)).lower()}'>"
            f"<div style='flex:1'>{esc(c.get('purpose'))} "
            f"<span class='muted'>· rule {esc(c.get('rule_id'))}</span></div>"
            f"<div style='color:{color};font-weight:700'>{esc(state)}</div></div>"
        )
    consent = _panel(
        "Consent checks",
        f"<div data-consent-count='{len(consent_rows)}' "
        f"data-consent-missing='{missing_consent}'>"
        + ("".join(consent_rows) or "<div class='muted'>none</div>")
        + "</div>",
    )

    cites = _panel("Cited rules", _citations(data.get("citations", []), scope="review"))

    body = head + review + summary + findings_panel + consent + cites
    return _page(f"Compliance review — {data.get('asset_id')}", body)


VERDICT_LABEL = {
    "substantiated": "Substantiated",
    "partially_substantiated": "Partially substantiated",
    "unsubstantiated": "Unsubstantiated",
    "not_applicable": "No green claim made",
}


def _pct(value: Any) -> str:
    try:
        return f"{round(float(value) * 100)}%"
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return "n/a"


def render_assessment(data: dict[str, Any]) -> str:
    """Render one cited green-claim SubstantiationAssessment into a standalone HTML page.

    Audit-first: the verdict and the coverage figure shown here were decided by the
    deterministic coverage engine and serialised; this renderer computes nothing.
    """
    market = MARKET_LABEL.get(str(data.get("market")), str(data.get("market")))
    vertical = VERTICAL_LABEL.get(str(data.get("vertical")), str(data.get("vertical")))
    verdict = str(data.get("verdict"))
    outcome_class = "compliant" if verdict == "substantiated" else "non_compliant"
    hooks = (
        f"<div data-assessment-id='{esc(data.get('id'))}' "
        f"data-assessment-asset='{esc(data.get('asset_id'))}' "
        f"data-assessment-tenant='{esc(data.get('tenant'))}' "
        f"data-assessment-market='{esc(data.get('market'))}' "
        f"data-assessment-verdict='{esc(verdict)}' "
        f"data-assessment-coverage='{esc(data.get('coverage'))}' "
        f"data-assessment-as-of='{esc(data.get('as_of'))}' "
        f"data-assessment-claims='{len(data.get('claims', []))}' "
        f"data-assessment-citations='{len(data.get('citations', []))}' "
        f"data-assessment-human-review="
        f"'{str(bool(data.get('requires_human_review'))).lower()}'></div>"
    )
    head = (
        hooks + f"<h1>Green claims — {esc(data.get('asset_id'))}</h1>"
        f"<p class='sub'><span class='pill'>{esc(market)}</span>"
        f"<span class='pill'>{esc(vertical)}</span>"
        f"<span class='pill'>tenant {esc(data.get('tenant'))}</span> "
        f"<span class='outcome {outcome_class}'>"
        f"{esc(VERDICT_LABEL.get(verdict, verdict))}</span>"
        f" &nbsp; coverage <b>{esc(_pct(data.get('coverage')))}</b>"
        f" &nbsp; evidence aged at <b>{esc(data.get('as_of'))}</b></p>"
    )
    review = ""
    if data.get("requires_human_review"):
        review = (
            "<div class='review' data-maker-checker='required'>HUMAN REVIEW REQUIRED — "
            "a green claim never publishes on the "
            "agent's say-so. A qualified compliance officer signs off before this asset runs.</div>"
        )

    narrative = _panel(
        "Explanation (narrated from the already-decided result)",
        f"<div class='summary'>{esc(data.get('narrative'))}</div>",
    )

    claim_rows = []
    for coverage in data.get("claims", []):
        claim = coverage.get("claim", {})
        claim_verdict = str(coverage.get("verdict"))
        gaps = "".join(
            f"<div class='fix'>gap: {esc(gap)}</div>" for gap in coverage.get("gaps", [])
        )
        fix = (
            f"<div class='fix'>fix: {esc(coverage.get('remediation'))}</div>"
            if coverage.get("remediation")
            else ""
        )
        counted = ", ".join(coverage.get("satisfied_kinds", [])) or "none"
        missing = ", ".join(coverage.get("missing_kinds", [])) or "none"
        evidence_ids = ", ".join(coverage.get("evidence_ids", [])) or "none"
        claim_rows.append(
            f"<div class='row' data-claim-category='{esc(claim.get('category'))}' "
            f"data-claim-verdict='{esc(claim_verdict)}' "
            f"data-claim-coverage='{esc(coverage.get('coverage'))}'>"
            f"<span class='status {'pass' if claim_verdict == 'substantiated' else 'fail'}'>"
            f"{esc(_pct(coverage.get('coverage')))}</span>"
            f"<div style='flex:1'><b>{esc(claim.get('category'))}</b> "
            f"<span class='muted'>&ldquo;{esc(claim.get('phrase'))}&rdquo; in the "
            f"{esc(claim.get('location'))}</span> — "
            f"{esc(VERDICT_LABEL.get(claim_verdict, claim_verdict))}"
            f"<div class='muted'>evidence counted: {esc(counted)} ({esc(evidence_ids)})</div>"
            f"<div class='muted'>still required: {esc(missing)}</div>"
            f"{gaps}{fix}{_citations(coverage.get('citations', []), scope='claim')}</div></div>"
        )
    claims = _panel(
        "Claims and their coverage (deterministic coverage engine)",
        f"<div data-claim-count='{len(claim_rows)}'>"
        + (
            "".join(claim_rows)
            or "<div class='muted'>no environmental claim was detected in this copy</div>"
        )
        + "</div>",
    )

    finding_rows = []
    for f in data.get("findings", []):
        status = str(f.get("status"))
        bg, fg = SEV_COLOR.get(str(f.get("severity")), SEV_COLOR["medium"])
        ev = (
            f"<div class='muted'>evidence: {esc(f.get('evidence'))}</div>"
            if f.get("evidence")
            else ""
        )
        fix = (
            f"<div class='fix'>fix: {esc(f.get('remediation'))}</div>"
            if f.get("remediation")
            else ""
        )
        finding_rows.append(
            f"<div class='row' data-finding-rule='{esc(f.get('rule_id'))}' "
            f"data-finding-status='{esc(status)}' "
            f"data-finding-severity='{esc(f.get('severity'))}'>"
            f"<span class='status {esc(status)}'>{esc(status.upper())}</span>"
            f"<span class='sev' style='background:{bg};color:{fg}'>{esc(f.get('severity'))}</span>"
            f"<div style='flex:1'><b>{esc(f.get('rule_id'))}</b> — {esc(f.get('message'))}"
            f"{ev}{fix}{_citations(f.get('citations', []), scope='finding')}</div></div>"
        )
    findings = _panel(
        "Green-claim rules in force",
        f"<div data-finding-count='{len(finding_rows)}'>"
        + (
            "".join(finding_rows)
            or "<div class='muted'>no green-claim rule applies to this asset</div>"
        )
        + "</div>",
    )

    cites = _panel("Instruments cited", _citations(data.get("citations", []), scope="assessment"))
    body = head + review + narrative + claims + findings + cites
    return _page(f"Green claims — {data.get('asset_id')}", body)


def render_index(reviews: list[tuple[str, dict[str, Any]]]) -> str:
    rows = []
    for fname, data in reviews:
        market = MARKET_LABEL.get(str(data.get("market")), str(data.get("market")))
        vertical = VERTICAL_LABEL.get(str(data.get("vertical")), str(data.get("vertical")))
        outcome = str(data.get("outcome"))
        if outcome == "None":  # a green-claim assessment carries a verdict, not an outcome
            verdict = str(data.get("verdict"))
            label = VERDICT_LABEL.get(verdict, verdict)
            outcome = "compliant" if verdict == "substantiated" else "non_compliant"
        else:
            label = OUTCOME_LABEL.get(outcome, outcome)
        rows.append(
            f"<a class='choose' href='{esc(fname)}'><b>{esc(data.get('asset_id'))}</b> "
            f"<span class='muted'>· {esc(market)} · {esc(vertical)} · </span>"
            f"<span class='outcome {esc(outcome)}'>{esc(label)}</span></a>"
        )
    body = (
        "<h1>D6 Marketing Compliance and Brand Governance — demo reviews</h1>"
        "<p class='sub'>Compliance reviews and green-claim substantiations. Offline, "
        "obviously-fictional synthetic data. Local profile, no cloud.</p>" + "".join(rows)
    )
    return _page("D6 demo reviews", body)


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("scripts/out")
    reviews: list[tuple[str, dict[str, Any]]] = []
    for json_path in sorted(out_dir.glob("review-*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        html_name = json_path.stem + ".html"
        (out_dir / html_name).write_text(render_review(data), encoding="utf-8")
        reviews.append((html_name, data))
        print(f"wrote {out_dir / html_name}")
    for json_path in sorted(out_dir.glob("green-*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        html_name = json_path.stem + ".html"
        (out_dir / html_name).write_text(render_assessment(data), encoding="utf-8")
        reviews.append((html_name, data))
        print(f"wrote {out_dir / html_name}")
    (out_dir / "index.html").write_text(render_index(reviews), encoding="utf-8")
    print(f"wrote {out_dir / 'index.html'}  ({len(reviews)} review(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
