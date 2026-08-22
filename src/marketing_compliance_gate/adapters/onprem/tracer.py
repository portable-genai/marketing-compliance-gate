"""On-prem placeholder for ``ObservabilityTracerPort`` — the sovereign migration target."""

from __future__ import annotations

from contextlib import AbstractContextManager

from ...config import Settings
from ...domain.models import TokenUsage

_MESSAGE = (
    "On-prem ObservabilityTracerPort adapter is a migration placeholder; implement against "
    "your on-premise tracing backend. Core domain logic is unchanged."
)


class OnPremTracerAdapter:
    """Placeholder tracer adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def span(self, name: str, **attributes: str) -> AbstractContextManager[None]:
        raise NotImplementedError(_MESSAGE)

    def record_token_usage(self, usage: TokenUsage, model: str) -> None:
        raise NotImplementedError(_MESSAGE)
