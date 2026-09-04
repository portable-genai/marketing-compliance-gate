#!/usr/bin/env python3
"""Live, presenter-controlled demo server for D6 (stdlib only, fully offline).

Holds a real set of D6 services over the in-memory ``local`` stack and reveals one cited
compliance review per click, walking the presenter across both verticals (banking + online
retail) and the JP/AU/SG markets. Each step renders the audit-first console reused verbatim
from ``render_review_ui``. No Google Cloud, no API key, no extra dependencies.

    MKT_GOV_PROFILE=local PYTHONPATH=src python scripts/demo_server.py [--port 8115]

Then open http://localhost:8115 and click "Next", or drive it with Playwright. The demo
port (8115) is deliberately distinct from the FastAPI API port (8105) and the Next.js
console port (3000) so all three can run side by side.
"""

from __future__ import annotations

import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import demo as scenario  # sibling: the synthetic scenarios + real local run
import render_review_ui as r  # sibling: reuse the exact audit-first rendering

from marketing_compliance_gate.config import Container
from marketing_compliance_gate.domain.models import ReviewRequest
from marketing_compliance_gate.domain.serialization import to_jsonable

_CONTROL_CSS = """
.democtl{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:12px;
  margin:-24px -18px 16px;padding:12px 18px;background:#0b101a;color:#fff}
.democtl .lbl{font-size:13px}.democtl .lbl b{color:#90b2ff}
.democtl .spacer{flex:1}.democtl form{margin:0}
.democtl button{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:7px;padding:7px 14px;cursor:pointer}
.democtl .next{background:#3a60f0;color:#fff}.democtl .next:disabled{opacity:.4;cursor:default}
.democtl .restart{background:transparent;color:#a6b6cc;border:1px solid #33445b}
"""


class DemoSession:
    """Compute the real D6 reviews once, then reveal one per click."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        container = Container(scenario._settings())
        service = scenario._service(container)  # real services over the in-memory local stack
        self.reviews = []
        for asset in scenario._SCENARIOS:
            review = service.review(ReviewRequest(asset=asset), actor="demo")
            self.reviews.append(to_jsonable(review))
        pending = container.review_router.outbox.pending()
        # Keep the routed human-review-console maker-checker records themselves, not just their count, so the
        # audit page can show what was actually handed to the review console.
        self.routed = [
            {
                "case_ref": entry.review.case_ref,
                "severity": entry.review.severity,
                "required_approvals": entry.review.required_approvals,
                "sod_group": entry.review.sod_group,
                "maker": entry.review.maker,
                "summary": entry.review.summary,
                "citations": [
                    {
                        "source_id": c.source_id,
                        "source_type": "rule",
                        "title": c.title,
                        "url": "",
                    }
                    for c in entry.review.citations
                ],
            }
            for entry in pending
        ]
        self.routed_count = len(pending)
        self.idx = 0

    @property
    def at_end(self) -> bool:
        return self.idx >= len(self.reviews) - 1

    def advance(self) -> None:
        if not self.at_end:
            self.idx += 1

    def render(self) -> str:
        data = self.reviews[self.idx]
        return self._inject_controls(r.render_review(data), data)

    def render_sources(self) -> str:
        """The audit page: every rule cited across the demo, and every routed human-review-console
        record.

        Nothing here is written by hand: the citations are the ones the rule engine attached
        and the records are the ones the router actually placed in the outbox.
        """
        seen: dict[str, dict] = {}
        for review in self.reviews:
            for citation in review.get("citations", []):
                seen.setdefault(str(citation.get("source_id")), citation)
        cited = [seen[key] for key in sorted(seen)]

        rows = []
        for record in self.routed:
            rows.append(
                f"<div class='row' data-outbox-case='{r.esc(record['case_ref'])}' "
                f"data-outbox-severity='{r.esc(record['severity'])}' "
                f"data-outbox-approvals='{r.esc(record['required_approvals'])}' "
                f"data-outbox-sod-group='{r.esc(record['sod_group'])}'>"
                f"<span class='status fail'>{r.esc(record['severity'])}</span>"
                f"<div style='flex:1'><b>{r.esc(record['case_ref'])}</b> "
                f"<span class='muted'>maker {r.esc(record['maker'])} · "
                f"{r.esc(record['required_approvals'])} approval(s) · "
                f"SoD {r.esc(record['sod_group'])}</span>"
                f"<div class='muted'>{r.esc(record['summary'])}</div>"
                f"{r._citations(record['citations'], scope='outbox')}</div></div>"
            )
        outbox = r._panel(
            "Routed to the human-review-console maker-checker console",
            f"<div data-outbox-count='{len(self.routed)}'>"
            + ("".join(rows) or "<div class='muted'>none</div>")
            + "</div>",
        )
        cites = r._panel("Every rule cited in this demo", r._citations(cited, scope="demo"))
        body = (
            "<h1>Demo sources and audit trail</h1>"
            "<p class='sub'>Obviously-fictional synthetic rules and assets, local profile, "
            "no cloud. <a href='/'>back to the walkthrough</a></p>" + outbox + cites
        )
        return r._page("D6 demo sources", body)

    def _inject_controls(self, page_html: str, data: dict) -> str:
        nxt = None if self.at_end else "Reveal the next compliance review"
        if nxt:
            next_btn = (
                "<form method='post' action='/advance'><button class='next' type='submit'>"
                f"Next &nbsp;·&nbsp; {r.esc(nxt)}</button></form>"
            )
        else:
            next_btn = "<button class='next' disabled>Demo complete</button>"
        label = f"{data.get('market')} / {data.get('vertical')} — {data.get('asset_id')}"
        bar = (
            # data-* here is the presenter journey's own evidence: which step the SERVED app
            # believes it is on, how many there are, and how many reviews it routed to human-review-console.
            f"<div class='democtl' data-demo='presenter-step' data-step='{self.idx}' "
            f"data-step-count='{len(self.reviews)}' data-routed-count='{self.routed_count}'>"
            f"<span class='lbl'>Step {self.idx + 1}/{len(self.reviews)} — <b>{r.esc(label)}</b></span>"
            f"<span class='spacer'></span>{next_btn}"
            "<a class='restart' href='/sources' style='text-decoration:none;padding:7px "
            "14px;border-radius:7px'>Sources</a>"
            "<form method='post' action='/restart'><button class='restart' "
            "type='submit'>Restart</button></form>"
            "</div>"
        )
        page_html = page_html.replace("</style>", _CONTROL_CSS + "</style>", 1)
        return page_html.replace("<div class='wrap'>", "<div class='wrap'>" + bar, 1)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, to: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    @property
    def _sess(self) -> DemoSession:
        return self.server.session  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/":
                self._send(self._sess.render())
            elif path == "/sources":
                self._send(self._sess.render_sources())
            elif path == "/restart":
                self._sess.reset()
                self._redirect("/")
            else:
                self._send("<h1>404</h1>", 404)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/advance":
                self._sess.advance()
            elif path == "/restart":
                self._sess.reset()
        self._redirect("/")

    def log_message(self, *args: object) -> None:  # quiet console
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Live D6 compliance-governance demo server")
    parser.add_argument("--port", type=int, default=8115)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.session = DemoSession()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    print(f"D6 demo server on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
