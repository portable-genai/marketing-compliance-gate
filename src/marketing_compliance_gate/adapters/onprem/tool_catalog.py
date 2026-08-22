"""On-prem placeholder for ``ToolCatalogPort`` — the sovereign migration target."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ToolSpec

_MESSAGE = (
    "On-prem ToolCatalogPort adapter is a migration placeholder; implement against your "
    "on-premise tool catalog. Core domain logic is unchanged."
)


class OnPremToolCatalogAdapter:
    """Placeholder tool-catalog adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError(_MESSAGE)

    def get_tool(self, name: str) -> ToolSpec | None:
        raise NotImplementedError(_MESSAGE)
