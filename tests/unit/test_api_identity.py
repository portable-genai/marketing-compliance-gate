"""API-boundary tests for server-verified identity (no client-asserted actor).

Proves the HTTP contract the embeddable-secure-ui pattern adds:

* an unknown ``X-Dev-Persona`` is a 401 (identity must resolve, never silently default),
* with no persona header the DEFAULT persona's subject is the audit actor, and with a
  selected persona that persona's subject is the audit actor (the request body cannot
  supply an actor), and
* ``GET /v1/personas`` lists the seeded personas for the local picker.

The container is an in-memory ``local`` container injected by monkeypatching the cached
``get_container`` factory (in both ``deps`` and ``security``) so the audit log is
inspectable and the whole test stays offline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps, security
from marketing_compliance_gate.config import Container, LocalSettings, Settings

CONFIG_PATH = "config/settings.yaml"

_ASSET = {
    "asset": {
        "asset_type": "creative",
        "title": "Submitted asset",
        "body": "Get guaranteed returns of 4.10% with zero risk-free worry!",
        "market": "SG",
        "vertical": "banking",
    }
}


def _local_container() -> Container:
    base = Settings.load(CONFIG_PATH)
    settings = Settings(
        project_id=base.project_id,
        region=base.region,
        profile="local",
        vertical=base.vertical,
        market=base.market,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        knowledge_base=base.knowledge_base,
        model_armor=base.model_armor,
        logging=base.logging,
        agent_engine=base.agent_engine,
        local=LocalSettings(db_path=":memory:", audit_path=":memory:"),
        markets=base.markets,
        adapters=base.adapters,
    )
    return Container(settings)


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch) -> Container:
    c = _local_container()
    # get_container is lru_cached; inject the in-memory container rather than mutating env.
    # All THREE module namespaces that imported it must see the injected container: the
    # identity dependency (security), the service factory (deps) and the routes themselves
    # (app). Leaving one unpatched means that route silently exercises the ambient
    # environment's container instead of this one.
    monkeypatch.setattr(deps, "get_container", lambda: c)
    monkeypatch.setattr(security, "get_container", lambda: c)
    monkeypatch.setattr(app_module, "get_container", lambda: c)
    return c


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(app_module.app, client=LOOPBACK_PEER)


def test_unknown_persona_is_401(client: TestClient) -> None:
    resp = client.post("/v1/review", json=_ASSET, headers={"X-Dev-Persona": "does-not-exist"})
    assert resp.status_code == 401


def test_default_persona_is_the_audit_actor(client: TestClient, container: Container) -> None:
    resp = client.post("/v1/review", json=_ASSET)
    assert resp.status_code == 200
    actors = {e["actor"] for e in container.audit.read_all()}
    # The default seeded persona's subject, NOT any client-supplied value, is the audit actor.
    assert "demo.reviewer@brand.example" in actors


def test_selected_persona_is_the_audit_actor(client: TestClient, container: Container) -> None:
    resp = client.post("/v1/review", json=_ASSET, headers={"X-Dev-Persona": "auditor"})
    assert resp.status_code == 200
    actors = {e["actor"] for e in container.audit.read_all()}
    assert "demo.auditor@brand.example" in actors


def test_personas_route_lists_seeded_personas(client: TestClient) -> None:
    resp = client.get("/v1/personas")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert {"analyst", "approver", "auditor", "other-tenant"} <= ids
