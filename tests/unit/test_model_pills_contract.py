"""The half of the model-pills contract that lives in the BROWSER.

Every served console shows two small pills at the top right of every page: the model that
ANSWERED the last request, and ``Search`` when that answer used an online search tool (owner
decision, 2026-09-23). They replaced a full-width provenance banner that named the model
configuration would call rather than the one that answered. The SERVICE half (the runtime and
``generator_model`` on ``/healthz``, the two answer headers, the CORS exposure) is pinned in
``tests/unit/test_health_provenance.py`` and ``tests/unit/test_answer_provenance.py`` and is not
restated here.

This file pins the other half, because the other half is the one that has broken before. On
2026-09-04 eight consoles were found to have rendered NOTHING on every page load since the
banner landed: the component named ``/api/agent``, the same-origin route handler the service
template ships, in trees that ship no such handler. The health call reached a path nothing
serves, took the failure branch, and the failure branch renders nothing, deliberately. A check
that cannot fail loudly fails as an ABSENCE, and an absence is exactly what no reviewer notices.

This console calls its service DIRECTLY at ``API_BASE`` (resolved once in ``ui/lib/api``), with
no Next route handler in between, so the pills must read that same base, and the service must
list both headers in its CORS ``expose_headers`` (a cross-origin response hides every header
not listed there). ``ui/tests/answer-provenance.test.mjs`` proves the one fetch wrapper itself.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path("ui")
PILLS = UI / "components" / "ModelPills.tsx"
WATCHER = UI / "lib" / "answer-provenance.mjs"

#: Build output and vendored packages are not this console's source.
_NOT_SOURCE = frozenset({"node_modules", ".next", "dist", "out", "coverage"})


def _console_sources() -> list[Path]:
    """Every ``.tsx`` this console actually ships, build output and vendored trees pruned."""
    found: list[Path] = []
    pending = [UI]
    while pending:
        for child in pending.pop().iterdir():
            if child.is_dir():
                if child.name not in _NOT_SOURCE:
                    pending.append(child)
            elif child.suffix == ".tsx":
                found.append(child)
    return sorted(found)


def _code_only(source: str) -> str:
    """``source`` with its comments removed, so an assertion cannot pass on prose."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def test_exactly_one_component_states_where_the_model_runs() -> None:
    """Located by what it SAYS, not by path: this fleet keeps the chrome in three places."""
    hits = [p for p in _console_sources() if "running on GCP" in p.read_text()]
    assert hits == [PILLS], (
        f"the runtime wording is rendered by {[str(p) for p in hits]}; it belongs to the model "
        "pills alone, and a second copy is how two pages come to phrase one fact differently"
    )
    assert not (UI / "components" / "ProvenanceBanner.tsx").exists(), "the old banner is back"


def test_the_pills_are_mounted_in_the_layout_rather_than_in_a_page() -> None:
    """Being at the top of EVERY page is a property of the console, not of any page."""
    layout = (UI / "app" / "layout.tsx").read_text()
    assert "<ModelPills />" in _code_only(layout), "the pills are not mounted on every page"


def test_the_pills_read_the_base_this_console_actually_serves() -> None:
    """The 2026-09-04 defect, stated as an assertion.

    A tree with ``ui/app/api/agent`` proxies through its own origin; this one has none, so the
    pills must reach the service the way every other call does, through ``API_BASE`` from
    ``ui/lib/api``. Both the health call and the fetch wrapper take that one base: the
    ``connect-src`` the console ships is built from the same value, so a base of their own would
    be silently refused.
    """
    assert not (UI / "app" / "api" / "agent").exists(), "a proxy console needs the other shape"
    pills = _code_only(PILLS.read_text())
    assert '"/api/agent"' not in pills, "the pills name a route handler this console lacks"
    assert re.search(r'import \{ API_BASE \} from "(?:\.\./)+lib/api"', pills)
    assert "`${API_BASE}/healthz`" in pills, "the pills do not start from the service's /healthz"
    assert "watchAnswers(window, API_BASE," in pills, "the answer headers are read off another base"
    assert "generator_model" in pills and "runtime" in pills


def test_the_pills_read_both_answer_headers_and_the_service_exposes_them() -> None:
    """A proxy that dropped the headers, or CORS that hid them, keeps the pill configured forever.

    Every node test would still be green, which is why the whole chain is held here.
    """
    watcher = WATCHER.read_text()
    for header in ('"x-answered-by"', '"x-search-used"'):
        assert header in _code_only(watcher), "the pills never read " + header
    app = Path("src/marketing_compliance_gate/api/app.py").read_text()
    assert "expose_headers=[ANSWERED_BY_HEADER, SEARCH_USED_HEADER]" in app, (
        "the service does not list both answer headers in its CORS expose_headers, so a "
        "cross-origin console can never read them"
    )
    assert "install_answer_provenance(app)" in app, "nothing emits the answer headers"
    assert (UI / "tests" / "answer-provenance.test.mjs").exists()


def _rule(css: str, selector: str) -> str | None:
    """The body of one CSS rule, or ``None`` when this stylesheet does not carry it."""
    opening = f"{selector} {{"
    if opening not in css:
        return None
    start = css.index(opening)
    return css[start : css.index("}", start)]


def test_the_pills_sit_fixed_at_the_top_right() -> None:
    """Fixed at the top right, inside the page's own top padding, never a full-width strip.

    ``top`` plus the pill height (11px text at line-height 1.5, 1px borders: about 18px) stays
    under the 24px ``p-6`` the console's ``main`` starts with, so no control is covered.
    """
    css = (UI / "app" / "globals.css").read_text()
    assert _rule(css, ".provenance-banner") is None, "the old banner's style is back"
    block = _rule(css, ".model-pills")
    assert block is not None, "the pills carry no style, so they render inline in the flow"
    assert re.search(r"^\s*position:\s*fixed;", block, re.MULTILINE)
    top = re.search(r"^\s*top:\s*(\d+)px;", block, re.MULTILINE)
    right = re.search(r"^\s*right:\s*(\d+)px;", block, re.MULTILINE)
    assert top and right, "the pills are not anchored to the top right"
    assert int(top.group(1)) + 18 <= 24, "the pills reach down into the console's controls"
    assert 'data-testid="model-pills"' in PILLS.read_text()
