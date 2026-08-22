"""Remote-platform guardrail adapter (GuardrailPort) — thin HTTP client to A1.

Routes screening to the shared **A1 Guardrail Gateway**. Constructs cleanly with no Google
Cloud SDK; the HTTP body is wired in the platform phase.
"""

from __future__ import annotations

from ...domain.models import Direction, GuardrailVerdict
from ...envread import setting_or_default

_DEFAULT_URL = "http://localhost:8081"
_PHASE = "RemoteGuardrailAdapter screen() is wired in the platform phase."


class RemoteGuardrailAdapter:
    """HTTP client for the shared A1 guardrail gateway."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = setting_or_default("HRZ_GUARDRAIL_URL", _DEFAULT_URL).rstrip("/")

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        raise NotImplementedError(_PHASE)
