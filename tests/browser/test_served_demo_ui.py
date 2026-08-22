"""F2: the presenter demo is driven through a real headless browser, not a string.

``scripts/demo_selftest.py`` starts the real server and reads the served bytes, which covers
the server/renderer path browserlessly. This file closes the other half: a pinned headless
Chromium loads the SERVED pages, clicks the presenter's own ``Next`` button through every
step, and reads each asserted figure back out of the LIVE DOM through the stable ``data-*``
evidence hooks. Nothing here is compared against hard-coded prose; every expectation is
recomputed from the running :class:`DemoSession`.

Playwright is pinned in the ``[demo]`` extra. The browser binary is a network download, so a
fork's day-one offline gate (D3) must not depend on it: the module skips LOUDLY (``-rs``)
when the browser is absent, and ``make demo-browser`` runs it for anyone who has the extra.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="the pinned [demo] extra is not installed"
)


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


demo_server = _load("demo_server")


@pytest.fixture(scope="module")
def served() -> Iterator[tuple[str, Any]]:
    """The REAL demo server, on an ephemeral port, for the duration of the module."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo_server.Handler)
    session = demo_server.DemoSession()
    server.session = session
    server.lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", session
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def page(served: tuple[str, Any]) -> Iterator[Any]:
    try:
        with playwright_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - environment-dependent
                pytest.skip(f"no pinned browser binary available: {exc}")
            context = browser.new_context()
            yield context.new_page()
            context.close()
            browser.close()
    except NotImplementedError as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"playwright cannot run here: {exc}")


def test_the_served_demo_walks_every_review_in_a_real_browser(
    page: Any, served: tuple[str, Any]
) -> None:
    base, session = served
    page.goto(f"{base}/restart", wait_until="load")

    for index in range(len(session.reviews)):
        bar = page.locator("[data-demo='presenter-step']")
        assert bar.get_attribute("data-step") == str(index)
        assert bar.get_attribute("data-step-count") == str(len(session.reviews))
        assert bar.get_attribute("data-routed-count") == str(session.routed_count)

        live = session.reviews[session.idx]
        findings = live["findings"]
        consent_checks = live["consent_checks"]
        failing = sum(1 for f in findings if f["status"] == "fail")
        missing = sum(1 for c in consent_checks if not c["granted"])

        # Figures read out of the LIVE DOM, checked against the running app.
        header = page.locator("[data-review-id]")
        assert header.get_attribute("data-review-id") == live["id"]
        assert header.get_attribute("data-review-asset") == live["asset_id"]
        assert header.get_attribute("data-review-market") == live["market"]
        assert header.get_attribute("data-review-vertical") == live["vertical"]
        assert header.get_attribute("data-review-outcome") == live["outcome"]
        assert header.get_attribute("data-review-findings") == str(len(findings))
        assert header.get_attribute("data-review-failing") == str(failing)
        assert header.get_attribute("data-review-consent-checks") == str(len(consent_checks))
        assert header.get_attribute("data-review-consent-missing") == str(missing)
        assert header.get_attribute("data-review-citations") == str(len(live["citations"]))
        assert (
            header.get_attribute("data-review-human-review")
            == str(bool(live["requires_human_review"])).lower()
        )
        assert page.locator("[data-maker-checker='required']").count() == 1

        for panel in (
            "summary",
            "findings-deterministic-rule-engine",
            "consent-checks",
            "cited-rules",
        ):
            assert page.locator(f"[data-panel='{panel}']").count() == 1, panel

        assert page.locator("[data-finding-count]").get_attribute("data-finding-count") == str(
            len(findings)
        )
        assert page.locator("[data-finding-failing]").get_attribute("data-finding-failing") == str(
            failing
        )
        assert _attrs(page, "[data-finding-rule]", "data-finding-rule") == [
            f["rule_id"] for f in findings
        ]
        assert _attrs(page, "[data-finding-status]", "data-finding-status") == [
            f["status"] for f in findings
        ]
        assert _attrs(page, "[data-consent-purpose]", "data-consent-purpose") == [
            c["purpose"] for c in consent_checks
        ]
        assert _attrs(page, "[data-consent-granted]", "data-consent-granted") == [
            str(bool(c["granted"])).lower() for c in consent_checks
        ]
        assert page.locator("[data-citation-scope='review']").get_attribute(
            "data-citation-count"
        ) == str(len(live["citations"]))

        if index < len(session.reviews) - 1:
            page.locator(".democtl button.next:not([disabled])").click()
            page.wait_for_load_state("load")

    assert page.locator(".democtl button.next[disabled]").count() == 1
    assert "HUMAN REVIEW REQUIRED" in page.content()


def test_the_sources_page_serves_every_routed_record_in_the_browser(
    page: Any, served: tuple[str, Any]
) -> None:
    base, session = served
    page.goto(f"{base}/sources", wait_until="load")

    assert page.locator("[data-outbox-count]").get_attribute("data-outbox-count") == str(
        session.routed_count
    )
    assert _attrs(page, "[data-outbox-case]", "data-outbox-case") == [
        r["case_ref"] for r in session.routed
    ]
    assert _attrs(page, "[data-outbox-severity]", "data-outbox-severity") == [
        r["severity"] for r in session.routed
    ]

    cited = {c["source_id"] for review in session.reviews for c in review["citations"]}
    assert cited, "the running app produced no citations to prove"
    assert page.locator("[data-citation-scope='demo']").get_attribute("data-citation-count") == str(
        len(cited)
    )
    content = page.content()
    for source_id in cited:
        assert source_id in content


def _attrs(page: Any, selector: str, attribute: str) -> list[str]:
    return page.locator(selector).evaluate_all(
        f"els => els.map(e => e.getAttribute('{attribute}'))"
    )
