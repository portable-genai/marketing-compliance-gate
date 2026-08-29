"""Remote-platform agent-registry adapter (AgentRegistryPort) — thin HTTP client to A3.

Routes AgentCard publish / resolve to the shared **A3 Agent Registry**. Constructs cleanly
with no Google Cloud SDK; the HTTP body is wired in the platform phase.
"""

from __future__ import annotations

from ...domain.models import AgentCard
from ...envread import setting_or_default

_DEFAULT_URL = "http://localhost:8083"
_PHASE = "RemoteRegistryAdapter is wired in the platform phase."


class RemoteRegistryAdapter:
    """HTTP client for the shared A3 agent registry."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = setting_or_default("AGENT_REGISTRY_URL", _DEFAULT_URL).rstrip("/")

    def register(self, card: AgentCard) -> None:
        raise NotImplementedError(_PHASE)

    def get(self, name: str) -> AgentCard | None:
        raise NotImplementedError(_PHASE)

    def list(self) -> list[AgentCard]:
        raise NotImplementedError(_PHASE)
