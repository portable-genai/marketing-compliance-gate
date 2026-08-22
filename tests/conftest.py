"""Shared test fixtures.

The unit tests for the deterministic engines are pure (they construct domain objects
directly). The orchestrator / pipeline tests use the local SDK-free adapters wired through
an in-memory container so the whole suite runs offline with no Google Cloud SDK.
"""

from __future__ import annotations

import pytest

from marketing_compliance_gate.config import Container, LocalSettings, Settings

CONFIG_PATH = "config/settings.yaml"

#: A loopback peer for every ``TestClient``. The app-object exposure guard refuses the
#: unauthenticated local posture to any other peer, and ``TestClient``'s default peer is the
#: literal host ``"testclient"``, which is not loopback and is therefore refused.
LOOPBACK_PEER = ("127.0.0.1", 50000)

#: A peer on the LAN. RFC 5737 documentation address: no real host, and obviously fictional.
LAN_PEER = ("203.0.113.7", 51234)


def _settings(profile: str = "local") -> Settings:
    base = Settings.load(CONFIG_PATH)
    return Settings(
        project_id=base.project_id,
        region=base.region,
        profile=profile,
        vertical=base.vertical,
        market=base.market,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        knowledge_base=base.knowledge_base,
        model_armor=base.model_armor,
        logging=base.logging,
        agent_engine=base.agent_engine,
        green_claims=base.green_claims,
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            evidence_path=":memory:",
            consent_path=":memory:",
        ),
        markets=base.markets,
        adapters=base.adapters,
    )


@pytest.fixture
def local_settings() -> Settings:
    return _settings("local")


@pytest.fixture
def local_container(local_settings: Settings) -> Container:
    return Container(local_settings)
