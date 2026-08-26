"""F2: the presenter demo is driven through a real headless browser, not a string.

``scripts/demo_selftest.py`` starts the real server and reads the served bytes, which covers
the server/renderer path browserlessly. This file closes the other half: a pinned headless
Chromium loads the SERVED pages, clicks the presenter's own ``Next`` button through every
step, and reads each asserted figure back out of the LIVE DOM through the stable ``data-*``
evidence hooks. Nothing here is compared against hard-coded prose; every expectation is
recomputed from the running :class:`DemoSession`.

Playwright is pinned in the ``[demo]`` extra and the browser binary is a network download,
so a fork's day-one offline gate (D3) must not depend on either: with nothing set, an absent
extra or an unlaunchable browser still skips LOUDLY (``-rs``, as ``make demo-browser`` runs
it) rather than passing silently. That default is a courtesy to a clean checkout, not a
licence. Set ``DEMO_BROWSER_REQUIRED`` and the same conditions FAIL instead, because a suite
that declines to run reports exactly the green a suite that ran reports, and a runner that
installed a browser on purpose is the one place that must never be handed a skip.
``CHROME_PATH`` names the binary to drive, the same read ``scripts/demo_playwright.py``
makes, so a runner carrying its own chromium is driven rather than quietly ignored.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

import pytest

from marketing_compliance_gate.envread import boolean_setting

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

#: Which local Chrome or Chromium binary Playwright drives, the same read
#: ``scripts/demo_playwright.py`` makes. Unset means Playwright's own pinned download, because
#: ``executable_path=None`` is Playwright's own default, so honouring the variable changes
#: nothing for anyone who leaves it alone. It was NOT honoured here before, and a runner that
#: ships a distribution chromium and exports ``CHROME_PATH`` was therefore ignored: the launch
#: reached for a download that was not there and the suite skipped. Two-state on purpose, and
#: classified posture-free alongside the other ``CHROME_PATH`` read: it names a program on the
#: runner's own machine, never a host, an origin or an audience, and an unusable value fails
#: the launch loudly rather than quietly widening anything.
CHROME_PATH = os.environ.get("CHROME_PATH") or None

#: Whether a browser was EXPECTED here. Three states, never two:
#:
#: * UNSET: nobody said one was expected, so a launch failure may still skip and a day-one
#:   offline checkout with no ``[demo]`` extra keeps a clean gate;
#: * SET AND EMPTY: an intent WAS expressed and it names nothing, so ``boolean_setting``
#:   refuses rather than guessing which way it pointed;
#: * SET AND TRUE: a browser was promised, so an absent extra or a failed launch FAILS.
#:
#: The last state is why this variable exists. A suite that declines to run reports exactly
#: the green a suite that ran reports, so the one place this evidence must never be allowed to
#: skip is the place that installed a browser on purpose.
BROWSER_REQUIRED = boolean_setting("DEMO_BROWSER_REQUIRED")


def _playwright_api() -> Any:
    """The pinned Playwright API, skipping only when nothing promised a browser."""
    if BROWSER_REQUIRED:
        # A browser was promised, so a missing [demo] extra is a broken promise. Let the
        # ImportError travel instead of converting it into a green tick.
        return importlib.import_module("playwright.sync_api")
    return pytest.importorskip(
        "playwright.sync_api", reason="the pinned [demo] extra is not installed"
    )


playwright_api = _playwright_api()


def _no_browser(reason: str) -> NoReturn:
    """Skip only when nothing said a browser was expected; FAIL when something did.

    An unconditional ``pytest.skip`` here was the defect this file exists to remove, one
    layer in: a suite that declines to run reports the same green as one that ran, so the
    runner that installed a browser on purpose learned nothing from its own green tick.
    """
    if BROWSER_REQUIRED:
        pytest.fail(
            "DEMO_BROWSER_REQUIRED is set, so a browser was expected here and this suite "
            f"must not skip. {reason}",
            pytrace=False,
        )
    pytest.skip(reason)


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
                browser = p.chromium.launch(headless=True, executable_path=CHROME_PATH)
            except Exception as exc:  # pragma: no cover - environment-dependent
                _no_browser(f"no pinned browser binary available: {exc}")
            context = browser.new_context()
            yield context.new_page()
            context.close()
            browser.close()
    except NotImplementedError as exc:  # pragma: no cover - environment-dependent
        _no_browser(f"playwright cannot run here: {exc}")


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
