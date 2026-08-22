"""Cloud Logging WORM audit adapter (AuditSinkPort) — GCP managed stack for D6.

Backs the domain ``AuditSinkPort`` with **Cloud Logging**. Each :class:`AuditEvent` is
written as a structured log entry; a Cloud Logging **sink** (provisioned in Terraform)
routes the ``marketing-compliance-gate-audit`` log to a **locked log bucket** (WORM, ~7-year
retention), so records are write-once and immutable: the regulator-grade audit guarantee
(A5).

This sink does not redact; it serialises and writes already-screened events. Structured
labels keep the WORM bucket queryable for demos and audit pulls without parsing the JSON
payload.

The Cloud Logging SDK import is LAZY so the on-prem / local / test profile imports this
module without ``google-cloud-logging`` installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import AuditEvent, Decision

# Severity by decision: blocked/escalated interactions surface at a higher log level than
# ordinary allowed runs, so reviewers can filter the WORM bucket for what needs attention.
_SEVERITY_BY_DECISION: dict[Decision, str] = {
    Decision.ALLOWED: "INFO",
    Decision.ESCALATED: "WARNING",
    Decision.BLOCKED: "WARNING",
}


class CloudLoggingAuditAdapter:
    """Write immutable :class:`AuditEvent` records to the locked WORM log bucket."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._log_name = settings.logging.log_name
        self._client: Any | None = None
        self._logger: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy SDK plumbing
    # ------------------------------------------------------------------ #
    def _get_logger(self) -> Any:
        if self._logger is not None:
            return self._logger
        from google.cloud import logging_v2  # noqa: PLC0415 — lazy: gcp profile only

        # verify: https://cloud.google.com/logging/docs/reference/libraries
        self._client = logging_v2.Client(project=self._settings.project_id)
        self._logger = self._client.logger(self._log_name)
        return self._logger

    # ------------------------------------------------------------------ #
    # AuditSinkPort
    # ------------------------------------------------------------------ #
    def record(self, event: AuditEvent) -> None:
        """Serialise and write one immutable audit record (routed to WORM by a sink)."""
        from ...domain.serialization import to_jsonable  # noqa: PLC0415 — reuse domain serializer

        logger = self._get_logger()
        payload = to_jsonable(event)
        severity = _SEVERITY_BY_DECISION.get(event.decision, "INFO")
        labels = {
            "action": event.action,
            "actor": event.actor,
            "decision": event.decision.value,
            "resource": event.resource,
        }
        if event.trace_id:
            labels["trace_id"] = event.trace_id
        logger.log_struct(payload, severity=severity, labels=labels)
