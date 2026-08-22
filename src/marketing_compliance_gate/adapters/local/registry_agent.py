"""Local agent-registry adapter (AgentRegistryPort) — in-process A3 registry.

The ``local`` profile's stand-in for the **A3 Agent Registry**: a small in-process store of
A2A AgentCards, seedable and deterministic. Under ``local`` this client uses an in-process
implementation rather than HTTP to a sibling service. SDK-free and unconditional.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AgentCard


class LocalAgentRegistryAdapter:
    """In-process agent registry of A2A AgentCards."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cards: dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list(self) -> list[AgentCard]:
        return list(self._cards.values())
