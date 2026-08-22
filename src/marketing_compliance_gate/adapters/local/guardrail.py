"""Local guardrail adapter (GuardrailPort) — heuristic prompt-injection screening.

The ``local`` profile's stand-in for **Model Armor**: a deterministic heuristic that allows
benign input and BLOCKS on prompt-injection / jailbreak patterns. SDK-free and
unconditional. Under ``local`` this A1 safety client runs in-process, not HTTP to the
guardrail-gateway sibling.
"""

from __future__ import annotations

import re

from ...config import Settings
from ...domain.models import (
    Direction,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
)

_INJ = GuardrailCategory.PROMPT_INJECTION
_JB = GuardrailCategory.JAILBREAK
_INJECTION_PATTERNS: tuple[tuple[re.Pattern[str], GuardrailCategory], ...] = (
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I), _INJ),
    (re.compile(r"disregard\s+(all\s+)?(prior|previous)\s+", re.I), _INJ),
    (re.compile(r"system\s+prompt", re.I), _INJ),
    (re.compile(r"\bexfiltrat", re.I), _INJ),
    (re.compile(r"reveal\s+(your\s+)?(secret|api\s*key|credential)", re.I), _INJ),
    (re.compile(r"\bjailbreak\b", re.I), _JB),
    (re.compile(r"override\s+(your\s+)?safety", re.I), _JB),
)


class LocalHeuristicGuardrailAdapter:
    """Heuristic guardrail: allow benign text, block known injection / jailbreak patterns."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        findings = [
            GuardrailFinding(
                category=category,
                confidence="high",
                detail=f"matched {category.value} pattern",
            )
            for pattern, category in _INJECTION_PATTERNS
            if pattern.search(text or "")
        ]
        if findings:
            return GuardrailVerdict(
                allowed=False,
                direction=direction,
                findings=tuple(findings),
                sanitized_text=None,
                reason="blocked by guardrail: prompt-injection / jailbreak pattern detected",
            )
        return GuardrailVerdict(
            allowed=True,
            direction=direction,
            findings=(),
            sanitized_text=text,
            reason="ok",
        )
