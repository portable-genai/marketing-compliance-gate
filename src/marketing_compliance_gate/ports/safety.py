"""GuardrailPort — A1 Guardrail Gateway concern.

Primary GCP adapter: **Model Armor**. Screens prompts/outputs for prompt-injection,
jailbreak and other unsafe content so a brief is never built from screened-out input or
returned with unsafe output. The local adapter is a deterministic heuristic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Direction, GuardrailVerdict


@runtime_checkable
class GuardrailPort(Protocol):
    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        """Screen ``text`` (INPUT or OUTPUT) and return an allow/block verdict."""
        ...
