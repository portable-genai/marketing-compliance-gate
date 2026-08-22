"""On-prem placeholder for ``AgentRegistryPort`` — the sovereign migration target."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AgentCard

_MESSAGE = (
    "On-prem AgentRegistryPort adapter is a migration placeholder; implement against your "
    "on-premise agent registry. Core domain logic is unchanged."
)


class OnPremAgentRegistryAdapter:
    """Placeholder agent-registry adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def register(self, card: AgentCard) -> None:
        raise NotImplementedError(_MESSAGE)

    def get(self, name: str) -> AgentCard | None:
        raise NotImplementedError(_MESSAGE)

    def list(self) -> list[AgentCard]:
        raise NotImplementedError(_MESSAGE)
