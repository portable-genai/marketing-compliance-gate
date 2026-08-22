"""Import-safety + wiring tests for the D6 ADK agent layer.

The local / on-prem / test profile installs **no Google Cloud SDK**, so importing the agent
wiring modules (and building the AgentCard, and calling the plain tool callable) must never pull
in ``google.adk`` / ``google-cloud-*``. The agent-card endpoint is also exercised end-to-end
against the local SDK-free stack via a monkeypatched in-memory container. A separate assertion
proves the agent exposes only the maker tool (never the checker/approve half).
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api.app import app
from marketing_compliance_gate.config import Container, Settings

_EXPECTED_SKILLS = {"review_marketing_asset"}


# --------------------------------------------------------------------------- #
# Import safety (no ADK installed)
# --------------------------------------------------------------------------- #
def test_agent_package_imports_without_adk() -> None:
    module = importlib.import_module("marketing_compliance_gate.agent")
    assert module.build_root_agent is not None
    assert module.build_agent_card is not None
    assert "google.adk" not in sys.modules


def test_agent_root_imports_without_adk() -> None:
    module = importlib.import_module("marketing_compliance_gate.agent.root_agent")
    assert repr(module.root_agent)  # touching the lazy proxy must not build the agent
    assert "google.adk" not in sys.modules


def test_mcp_toolset_is_none_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    ra = importlib.import_module("marketing_compliance_gate.agent.root_agent")

    monkeypatch.delenv(ra.MCP_SERVER_URL_ENV, raising=False)
    assert ra._build_mcp_toolset() is None
    assert "google.adk" not in sys.modules


def test_mcp_toolset_refuses_a_configured_empty_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ra = importlib.import_module("marketing_compliance_gate.agent.root_agent")
    monkeypatch.setenv(ra.MCP_SERVER_URL_ENV, "   ")

    with pytest.raises(ra.ConfiguredEmptyError, match=ra.MCP_SERVER_URL_ENV):
        ra._build_mcp_toolset()


def test_mcp_toolset_honors_a_configured_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ra = importlib.import_module("marketing_compliance_gate.agent.root_agent")
    monkeypatch.setenv(ra.MCP_SERVER_URL_ENV, " https://mcp.fictional.example/tools ")

    class FakeParams:
        def __init__(self, *, url: str) -> None:
            self.url = url

    class FakeToolset:
        def __init__(self, *, connection_params: FakeParams) -> None:
            self.connection_params = connection_params

    modules = {
        name: ModuleType(name)
        for name in (
            "google",
            "google.adk",
            "google.adk.tools",
            "google.adk.tools.mcp_tool",
            "google.adk.tools.mcp_tool.mcp_toolset",
        )
    }
    for package in ("google", "google.adk", "google.adk.tools", "google.adk.tools.mcp_tool"):
        modules[package].__path__ = []  # type: ignore[attr-defined]
    modules["google.adk.tools.mcp_tool"].mcp_session_manager = SimpleNamespace(  # type: ignore[attr-defined]
        SseConnectionParams=FakeParams
    )
    modules["google.adk.tools.mcp_tool.mcp_toolset"].MCPToolset = FakeToolset  # type: ignore[attr-defined]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    toolset = ra._build_mcp_toolset()

    assert isinstance(toolset, FakeToolset)
    assert toolset.connection_params.url == "https://mcp.fictional.example/tools"


# --------------------------------------------------------------------------- #
# The AgentCard is pure domain (no ADK)
# --------------------------------------------------------------------------- #
def test_agent_card_is_pure(local_settings: Settings) -> None:
    from marketing_compliance_gate.agent.agent_card import build_agent_card

    card = build_agent_card(local_settings)
    assert card.name == "marketing-compliance-gate"
    assert {s.id for s in card.skills} == _EXPECTED_SKILLS


def test_governed_tools_match_card_skills() -> None:
    """Least privilege (R4): the tool surface and the advertised skills stay in step."""
    from marketing_compliance_gate.agent import tools
    from marketing_compliance_gate.agent.agent_card import SKILLS

    assert tools.governed_tool_names() == {s.id for s in SKILLS}


def test_agent_never_exposes_the_checker_half() -> None:
    """Maker-checker separation: the agent must not expose an approve/checker tool."""
    from marketing_compliance_gate.agent import tools

    names = tools.governed_tool_names()
    assert not any("approve" in n or "checker" in n for n in names)
    assert names == {"review_marketing_asset"}


# --------------------------------------------------------------------------- #
# The plain tool callable runs offline against the local stack (no ADK)
# --------------------------------------------------------------------------- #
def test_review_tool_offline_flags_non_compliant(local_settings: Settings) -> None:
    from marketing_compliance_gate.agent.tools import review_marketing_asset

    result = review_marketing_asset(
        "Get guaranteed returns of 4.10% with zero risk-free worry!",
        asset_type="creative",
        title="Savings push",
        market="SG",
        vertical="banking",
        actor="reviewer@brand.example",
        settings=local_settings,
    )
    assert result["outcome"] == "non_compliant"
    assert result["requires_human_review"] is True
    assert result["findings"], "a non-compliant review must carry findings"
    assert result["approval"]["decision"] == "pending", "the agent (maker) must not pre-approve"
    assert "google.adk" not in sys.modules


# --------------------------------------------------------------------------- #
# The agent-card endpoint end-to-end (local stack)
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, local_settings: Settings) -> TestClient:
    container = Container(local_settings)
    monkeypatch.setattr(app_module, "get_container", lambda: container)
    return TestClient(app, client=LOOPBACK_PEER)


def test_agent_card_endpoint(client: TestClient) -> None:
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "marketing-compliance-gate"
    assert {s["id"] for s in body["skills"]} == _EXPECTED_SKILLS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
