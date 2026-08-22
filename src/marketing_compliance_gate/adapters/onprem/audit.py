"""On-prem placeholder for ``AuditSinkPort`` — the sovereign migration target."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AuditEvent

_MESSAGE = (
    "On-prem AuditSinkPort adapter is a migration placeholder; implement against your "
    "on-premise WORM audit store. Core domain logic is unchanged."
)


class OnPremAuditAdapter:
    """Placeholder audit adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def record(self, event: AuditEvent) -> None:
        raise NotImplementedError(_MESSAGE)
