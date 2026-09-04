"""The declared tool catalog is now SERVED, and the plugin is rendered from the declarations.

This repo declared its capability surface twice over, as an A2A agent card and as a governed
tool catalog of JSON Schemas, and served neither. A surface described in two places could be
read by a human and reached by nobody, so nothing had ever checked that a declared tool could
actually be answered. Serving it found that one could not.

These guards are about the seam rather than the transport. What goes wrong here is not that MCP
breaks; it is that the served surface and the declared surface drift apart, so the catalog says
one thing and the process does another. ``bind`` refuses that in both directions.

The MCP SDK is in the ``[gcp]`` extra and the offline gate does not install it, so the binding
guards go through ``bind``, which is pure, rather than through a live server object.

**The handlers ARE executed here, which is the part the fleet pattern leaves out.** ``bind``
pairs names with callables and checks nothing about what a callable does when called, so a
handler that reaches for a field its domain object does not have binds perfectly cleanly; one in
the fleet did. Both tools here resolve to the SDK-free local adapters, so they can be run in an
offline gate rather than deferred to a managed suite, and they are. The execution guards pass an
explicit local container so they choose their own profile instead of inheriting whatever the
Makefile exported.

**What they still do not prove:** the managed adapters. ``file_search_rules`` needs a live
Gemini File Search store, so its residency call is asserted by the schema guard below rather
than executed. That belongs in the managed suite.
"""

from __future__ import annotations

import json
import pathlib
import sys

import jsonschema
import pytest
from hex_service_kit.mcpserve import ToolDispatchError, bind
from hex_service_kit.plugin import load_schema

from marketing_compliance_gate.adapters.gcp.mcp_tool_catalog import McpToolCatalogAdapter
from marketing_compliance_gate.config import Container, Settings
from marketing_compliance_gate.mcp import server as mcp_server

CONFIG_PATH = "config/settings.yaml"

#: The checker's vocabulary. Matched as substrings so a differently-spelled approval capability
#: added later is caught, rather than only the one name that was removed.
_APPROVAL_WORDS = ("approve", "approval", "reject", "sign_off", "signoff", "dispose", "checker")


def _approval_named(labels: list[str]) -> list[str]:
    """Return the labels that name the checker half of maker-checker."""
    return [label for label in labels if any(word in label.lower() for word in _APPROVAL_WORDS)]


@pytest.fixture
def catalog() -> McpToolCatalogAdapter:
    return McpToolCatalogAdapter(Settings.load(CONFIG_PATH))


def test_every_declared_tool_has_a_handler_and_no_handler_is_undeclared(
    catalog: McpToolCatalogAdapter,
) -> None:
    """The whole point of binding at start-up rather than on the first call."""
    bound = bind(catalog, mcp_server.build_handlers(actor="svc:test"))

    assert set(bound) == {spec.name for spec in catalog.list_tools()}


def test_a_declared_tool_with_no_handler_refuses_to_start(
    catalog: McpToolCatalogAdapter,
) -> None:
    """A capability the service advertises and cannot perform must not be served.

    This is the guard that would have caught ``approve_review`` on the day it was declared.
    """
    handlers = mcp_server.build_handlers(actor="svc:test")
    handlers.pop(next(iter(handlers)))

    with pytest.raises(ToolDispatchError, match="no handler"):
        bind(catalog, handlers)


def test_a_handler_for_an_undeclared_tool_refuses_to_start(
    catalog: McpToolCatalogAdapter,
) -> None:
    """An ungoverned entry point is the more dangerous direction of the same mismatch."""
    handlers = mcp_server.build_handlers(actor="svc:test")
    handlers["exfiltrate_everything"] = lambda **_: None

    with pytest.raises(ToolDispatchError, match="does not declare"):
        bind(catalog, handlers)


def test_the_handler_roster_matches_the_catalog_exactly(
    catalog: McpToolCatalogAdapter,
) -> None:
    """``HANDLER_NAMES`` is documentation, so it is held to the catalog rather than trusted."""
    assert set(mcp_server.HANDLER_NAMES) == {spec.name for spec in catalog.list_tools()}


def test_the_catalog_declares_no_approval_tool(catalog: McpToolCatalogAdapter) -> None:
    """The checker half of maker-checker is not a tool, and this holds it there.

    ``approve_review`` was declared in this catalog and could not be served: it took a
    ``review_id`` nothing here resolves. Supplying the store would have been the wrong repair.
    Approval IS the four-eyes control, MCP stdio verifies no human, and rule R8 already routes an
    escalated review to the human-review-console where a real principal is resolved.
    ``agent/tools.py``
    excludes it from the ADK surface for exactly this reason, so the ADK and MCP surfaces now
    agree instead of contradicting each other.

    Asserted against the PORT's own vocabulary rather than the single name that was removed, so
    a differently-spelled approval tool added later is caught rather than quietly served.
    """
    declared = [spec.name for spec in catalog.list_tools()]

    offending = _approval_named(declared)

    assert not offending, (
        f"{offending} would serve the checker half of maker-checker over a transport that "
        "verifies no human. Approval belongs to the human-review-console via rule R8."
    )
    assert declared, "a forbidden-substring check over an empty catalog asserts nothing"


def test_neither_agent_card_advertises_an_approval_skill(local_settings: Settings) -> None:
    """marketing-compliance-gate publishes TWO A2A cards, and they disagreed about the four-eyes
    control.

    ``GET /.well-known/agent-card.json`` serves ``agent.agent_card``, which declares the maker
    skill only and says approval "is deliberately not advertised as an agent skill". The
    registry adapter seeded a DIFFERENT card carrying an extra ``approve_review`` skill named
    "Maker-checker approval", and that is the card a peer discovering marketing-compliance-gate
    through agent-registry reads.
    So one repository told a peer two different things about who may approve, and the route's
    own docstring claimed a peer and the registry "sees one capability surface".

    Two hand-authored lists that must agree will drift again, so they are held together here
    rather than merely corrected. Both are checked, because fixing one and trusting the other is
    how this started.
    """
    from marketing_compliance_gate.adapters.gcp.a2a_registry import _D6_SKILLS
    from marketing_compliance_gate.agent.agent_card import SKILLS, build_agent_card

    served = build_agent_card(local_settings).skills
    registered = _D6_SKILLS

    assert served and registered, "an empty skill list would make both assertions vacuous"
    for label, skills in (("served card", served), ("registry card", registered)):
        offending = _approval_named(
            [skill.id for skill in skills] + [skill.name for skill in skills]
        )
        assert not offending, (
            f"the {label} advertises {offending}: it offers the checker's half of "
            "maker-checker to a peer AGENT, which is not the human rule R8 routes to"
        )
    assert {skill.id for skill in served} == {skill.id for skill in SKILLS}


def test_every_tool_requires_the_scope_its_rule_lookup_cannot_do_without(
    catalog: McpToolCatalogAdapter,
) -> None:
    """``market`` and ``vertical`` are the scope key, not optional filters.

    They were declared optional and described as "Restrict to a single market", which reads as a
    filter over some broader default. There is no broader default: ``RuleProviderPort.search``
    takes both and every adapter filters on both, so a handler serving a call that omitted them
    would have to pick a market. Picking one answers a Singapore question with Japanese rules.

    The managed adapter makes it a residency question rather than only a wrong answer:
    ``file_search_rules.search`` calls ``resolve_region(settings, market=market)``, so an
    optional market is an optional per-market residency check.
    """
    for spec in catalog.list_tools():
        properties = spec.input_schema["properties"]
        required = set(spec.input_schema["required"])
        for key in ("market", "vertical"):
            if key in properties:
                assert key in required, (
                    f"{spec.name} declares {key} optional; the rule lookup it resolves to "
                    f"cannot be performed without it, and the managed adapter keys the "
                    f"residency check on it"
                )


def test_review_asset_executes_and_cannot_dispose_of_its_own_review(
    local_container: Container,
) -> None:
    """Run the tool, not just bind it, and check the maker-checker posture of what it returns.

    The MCP surface is the MAKER. A non-compliant asset must come back flagged for human review
    with a PENDING approval, because the only thing that could move it off PENDING is the
    checker tool this catalog deliberately does not declare.
    """
    handlers = mcp_server.build_handlers(actor="svc:test", container=local_container)

    review = handlers["review_asset"](
        body="Guaranteed 8% returns, no risk whatsoever.",
        market="SG",
        vertical="banking",
    )

    assert review["findings"], "an empty finding list would make every assertion below vacuous"
    assert review["outcome"] == "non_compliant"
    assert review["requires_human_review"] is True
    assert review["approval"]["decision"] == "pending"


def test_search_rules_executes_and_returns_rules_scoped_to_the_requested_market(
    local_container: Container,
) -> None:
    """The scope key is load-bearing, so the executed result is checked against it.

    A handler that dropped the scope would still bind, still return rules, and return the wrong
    jurisdiction's. Asserting the returned rules carry the market asked for is what catches that.
    """
    handlers = mcp_server.build_handlers(actor="svc:test", container=local_container)

    result = handlers["search_rules"](query="guaranteed returns", market="SG", vertical="banking")

    assert result["rules"], "a scope check over an empty rule list asserts nothing"
    assert result["market"] == "SG"
    assert result["vertical"] == "banking"
    for rule in result["rules"]:
        assert rule["market"] == "SG"
        assert rule["vertical"] == "banking"
        assert rule["citation"]["source_id"], "every rule reaches a caller with its citation"


@pytest.mark.parametrize("omitted", ["market", "vertical"])
def test_a_handler_refuses_a_call_that_omits_the_scope_rather_than_inventing_one(
    local_container: Container, omitted: str
) -> None:
    """The schema requires the scope; this proves the handler does not quietly supply one.

    Written after a first version of the guard above was watched staying GREEN against a handler
    that defaulted a missing market to JP. Asserting that a PRESENT market is honoured cannot see
    that defect, because the assertion always passes one. The failure mode is a caller who omits
    it, so the call that omits it is what has to be made.

    ``required`` in the schema is enforced by the MCP layer, not by ``bind``, so the handler is
    the second of the two places this can go wrong and the only one an offline gate reaches.
    """
    arguments = {"query": "guaranteed returns", "market": "SG", "vertical": "banking"}
    arguments.pop(omitted)
    handlers = mcp_server.build_handlers(actor="svc:test", container=local_container)

    with pytest.raises(KeyError, match=omitted):
        handlers["search_rules"](**arguments)


def _render(tmp_path: pathlib.Path) -> pathlib.Path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
    import render_plugin

    render_plugin.main(["--dest", str(tmp_path / "plugin")])
    return tmp_path / "plugin"


def test_the_manifest_validates_against_the_vendored_specification_schema(
    tmp_path: pathlib.Path,
) -> None:
    """``jsonschema`` is a hard dev dependency so this can never quietly skip into green."""
    manifest = json.loads((_render(tmp_path) / "plugin.json").read_text())

    jsonschema.validate(manifest, load_schema("plugin"))


def test_the_manifest_advertises_exactly_the_declared_tools(
    tmp_path: pathlib.Path, catalog: McpToolCatalogAdapter
) -> None:
    """Rendered from the declarations, so it is not a second description to maintain."""
    manifest = json.loads((_render(tmp_path) / "plugin.json").read_text())
    declared = {spec.name.replace("_", "-") for spec in catalog.list_tools()}

    assert set(manifest["keywords"]) == declared
