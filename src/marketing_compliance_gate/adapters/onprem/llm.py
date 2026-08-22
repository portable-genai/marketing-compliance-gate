"""On-prem placeholder for ``LlmPort`` — the sovereign migration target.

Constructs cleanly with no external dependencies and structurally satisfies the same
Protocol as the managed Gemini adapter, so the contract tests prove interface parity.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import LlmRequest, LlmResponse

_MESSAGE = (
    "On-prem LlmPort adapter is a migration placeholder; implement against your on-premise "
    "model platform. Core domain logic is unchanged."
)


class OnPremLLMAdapter:
    """Placeholder LLM adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate(self, request: LlmRequest) -> LlmResponse:
        raise NotImplementedError(_MESSAGE)

    def classify(self, text: str, labels: list[str]) -> str:
        raise NotImplementedError(_MESSAGE)
