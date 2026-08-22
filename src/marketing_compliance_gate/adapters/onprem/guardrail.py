"""On-prem placeholder for ``GuardrailPort`` — the sovereign migration target."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Direction, GuardrailVerdict

_MESSAGE = (
    "On-prem GuardrailPort adapter is a migration placeholder; implement against your "
    "on-premise safety gateway. Core domain logic is unchanged."
)


class OnPremGuardrailAdapter:
    """Placeholder guardrail adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        raise NotImplementedError(_MESSAGE)
