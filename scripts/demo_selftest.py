#!/usr/bin/env python3
"""Headless guard for every presenter-paced compliance-governance demo step.

Two stages, both executed, neither comparing against hard-coded prose:

1. **In-process** -- the real :class:`DemoSession` computes the four live D6 reviews over the
   local stack and renders / advances / resets every presenter step.
2. **Served** -- the real ``ThreadingHTTPServer`` is started on an ephemeral port and the whole
   presenter journey is driven over HTTP with ``POST /advance``. Every figure asserted at this
   stage is read out of the SERVED bytes through the stable ``data-*`` evidence hooks and
   compared with what the RUNNING app computed (``server.session``), so a renderer that stops
   emitting a figure, a server that stops advancing, or a hook that gets renamed all fail here.
   A check that never served a byte could not see whether serving works.

The headless-browser journey over the same served pages lives in
``tests/browser/test_served_demo_ui.py`` and needs the pinned ``[demo]`` extra.
"""

from __future__ import annotations

import re
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

from demo_server import DemoSession, Handler


def _hook(html: str, attribute: str) -> str:
    """Read one stable ``data-*`` evidence hook out of served markup."""
    match = re.search(rf"{attribute}='([^']*)'", html) or re.search(rf'{attribute}="([^"]*)"', html)
    assert match, f"evidence hook {attribute} is missing from the served page"
    return match.group(1)


def _hooks(html: str, attribute: str) -> list[str]:
    return re.findall(rf"{attribute}='([^']*)'", html) or re.findall(
        rf'{attribute}="([^"]*)"', html
    )


def _citation_count(html: str, scope: str) -> int:
    """Citations rendered for one scope (the renderer emits count and scope together)."""
    match = re.search(rf"data-citation-count='(\d+)' data-citation-scope='{scope}'", html)
    assert match, f"the served page carries no citation block for scope {scope!r}"
    return int(match.group(1))


def check_in_process() -> None:
    session = DemoSession()
    assert len(session.reviews) == 4
    assert session.routed_count == 4
    for step, review in enumerate(session.reviews, 1):
        assert review["requires_human_review"] is True
        assert review["citations"]
        page = session.render()
        assert f"Step {step}/{len(session.reviews)}" in page
        assert "HUMAN REVIEW REQUIRED" in page
        if step < len(session.reviews):
            session.advance()
    assert session.at_end
    session.reset()
    assert session.idx == 0
    print("PASS demo self-test: 4/4 live compliance reviews rendered, advanced, and reset")


def check_served() -> None:
    """Drive the REAL demo server over HTTP and assert live figures from served bytes."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    session = DemoSession()
    server.session = session  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        for index in range(len(session.reviews)):
            with urllib.request.urlopen(f"{base}/", timeout=20) as response:  # noqa: S310
                assert response.status == 200
                page = response.read().decode("utf-8")

            # The served page is at the step the served app believes it is at.
            assert _hook(page, "data-step") == str(index), f"served step marker is not {index}"
            assert _hook(page, "data-step-count") == str(len(session.reviews))
            assert _hook(page, "data-routed-count") == str(session.routed_count)

            # Live figures: served bytes vs what the running app computed for THIS step.
            live: dict[str, Any] = session.reviews[session.idx]
            findings = live["findings"]
            consent_checks = live["consent_checks"]
            failing = sum(1 for f in findings if f["status"] == "fail")
            missing = sum(1 for c in consent_checks if not c["granted"])

            assert _hook(page, "data-review-id") == live["id"]
            assert _hook(page, "data-review-asset") == live["asset_id"]
            assert _hook(page, "data-review-market") == live["market"]
            assert _hook(page, "data-review-vertical") == live["vertical"]
            assert _hook(page, "data-review-outcome") == live["outcome"]
            assert _hook(page, "data-review-findings") == str(len(findings))
            assert _hook(page, "data-review-failing") == str(failing)
            assert _hook(page, "data-review-consent-checks") == str(len(consent_checks))
            assert _hook(page, "data-review-consent-missing") == str(missing)
            assert _hook(page, "data-review-citations") == str(len(live["citations"]))
            assert (
                _hook(page, "data-review-human-review")
                == str(bool(live["requires_human_review"])).lower()
            ), "the served page lost the universal maker-checker state"
            assert _hook(page, "data-maker-checker") == "required"

            panels = _hooks(page, "data-panel")
            for required in (
                "summary",
                "findings-deterministic-rule-engine",
                "consent-checks",
                "cited-rules",
            ):
                assert required in panels, f"served page lost the {required} panel hook"

            assert _hook(page, "data-finding-count") == str(len(findings))
            assert _hook(page, "data-finding-failing") == str(failing)
            assert _hooks(page, "data-finding-rule") == [f["rule_id"] for f in findings]
            assert _hooks(page, "data-finding-status") == [f["status"] for f in findings]
            assert _hooks(page, "data-finding-severity") == [f["severity"] for f in findings]
            assert _hook(page, "data-consent-count") == str(len(consent_checks))
            assert _hook(page, "data-consent-missing") == str(missing)
            assert _hooks(page, "data-consent-purpose") == [c["purpose"] for c in consent_checks]
            assert _hooks(page, "data-consent-granted") == [
                str(bool(c["granted"])).lower() for c in consent_checks
            ]
            assert _citation_count(page, "review") == len(live["citations"])
            served_sources = _hooks(page, "data-citation-source")
            for citation in live["citations"]:
                assert citation["source_id"] in served_sources, (
                    f"the served page dropped citation {citation['source_id']}"
                )

            if index < len(session.reviews) - 1:
                request = urllib.request.Request(f"{base}/advance", method="POST", data=b"")
                with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                    assert response.status in (200, 303)
            else:
                assert "Demo complete" in page

        # The sources/audit page must serve too, with every routed record and citation on it.
        with urllib.request.urlopen(f"{base}/sources", timeout=20) as response:  # noqa: S310
            assert response.status == 200
            sources = response.read().decode("utf-8")

        assert _hook(sources, "data-outbox-count") == str(session.routed_count)
        assert _hooks(sources, "data-outbox-case") == [r["case_ref"] for r in session.routed]
        assert _hooks(sources, "data-outbox-severity") == [r["severity"] for r in session.routed]
        assert _hooks(sources, "data-outbox-approvals") == [
            str(r["required_approvals"]) for r in session.routed
        ]
        cited = {c["source_id"] for review in session.reviews for c in review["citations"]}
        assert _citation_count(sources, "demo") == len(cited)
        for source_id in cited:
            assert source_id in sources, f"the audit page lost citation {source_id}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    print(
        "PASS served: every presenter step, panel hook, live figure and routed "
        "human-review-console record "
        "read back over HTTP from the running demo server"
    )


def main() -> int:
    check_in_process()
    check_served()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
