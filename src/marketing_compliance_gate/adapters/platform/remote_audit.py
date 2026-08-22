"""Remote-platform audit adapter (AuditSinkPort) — thin HTTP client to A5.

Routes audit records to the shared **A5 Observability / Audit** service. Constructs cleanly
with no Google Cloud SDK; the HTTP body is wired in the platform phase.
"""

from __future__ import annotations

from ...domain.models import AuditEvent
from ...envread import setting_or_default

_DEFAULT_URL = "http://localhost:8085"
_PHASE = "RemoteAuditAdapter record() is wired in the platform phase."


class RemoteAuditAdapter:
    """HTTP client for the shared A5 audit service."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = setting_or_default("HRZ_AUDIT_URL", _DEFAULT_URL).rstrip("/")

    def record(self, event: AuditEvent) -> None:
        raise NotImplementedError(_PHASE)
