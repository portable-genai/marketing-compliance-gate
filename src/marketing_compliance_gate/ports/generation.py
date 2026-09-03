"""LlmPort — LLM text/reasoning for narrating the compliance findings.

Primary GCP adapter: Gemini models on the Gemini Enterprise Agent Platform
(``gemini-3.5-flash`` for narration, ``gemini-3.5-flash`` for triage). The LLM only
narrates the already-decided findings of the deterministic rule engine; it never decides
whether a rule passes, the severity, or the review outcome.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import LlmRequest, LlmResponse


@runtime_checkable
class LlmPort(Protocol):
    def generate(self, request: LlmRequest) -> LlmResponse:
        """Generate a completion for ``request`` using the configured model."""
        ...

    def classify(self, text: str, labels: list[str]) -> str:
        """Cheap single-label classification (triage/routing tier model)."""
        ...
