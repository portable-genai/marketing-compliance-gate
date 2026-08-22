"""Local LLM adapter (LlmPort) — a deterministic, schema-driven narrator.

The ``local`` profile's stand-in for **Gemini**: no model, no network, fully reproducible.
It reads ``request.response_schema`` (the JSON schema the calling service asks for) and
emits a deterministic JSON object whose keys match it, including ``used_rule_ids`` mapped
from the ``[RULE-ID]`` headers in the rendered FINDINGS block, so the narration cites only
rules that the deterministic engine actually evaluated. There is no Google emulator for
Gemini, so this path is unconditional. The LLM never decides the findings: the rule engine
does; this adapter only turns them into prose.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...config import Settings
from ...domain.models import LlmRequest, LlmResponse, TokenUsage

# Rule ids are upper/lower alphanumerics with hyphens (e.g. "SG-BANK-CLAIM-GUARANTEED").
_RULE_HEADER_RE = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9\-]*?)\]")


def _schema_properties(schema: dict | None) -> dict[str, Any]:
    if not schema:
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


class LocalDeterministicLLMAdapter:
    """Deterministic LLM whose ``generate`` returns JSON matching the request schema."""

    REASONING_MODEL = "gemini-3.5-flash"
    TRIAGE_MODEL = "gemini-3.1-flash-lite"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._reasoning_model = settings.models.reasoning or self.REASONING_MODEL
        self._triage_model = settings.models.triage or self.TRIAGE_MODEL

    def generate(self, request: LlmRequest) -> LlmResponse:
        rule_ids = self._rule_ids_from_request(request)
        body = self._body_for_schema(request.response_schema, rule_ids, self._user_content(request))
        return LlmResponse(
            text=json.dumps(body),
            usage=TokenUsage(input_tokens=128, output_tokens=64, thinking_tokens=32),
            model=request.model or self._reasoning_model,
            web_citations=(),
            raw=body,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        return labels[0] if labels else ""

    # ------------------------------------------------------------------ #
    # Schema-driven body
    # ------------------------------------------------------------------ #
    def _rule_ids_from_request(self, request: LlmRequest) -> list[str]:
        seen: list[str] = []
        for rid in _RULE_HEADER_RE.findall(self._user_content(request)):
            if rid not in seen:
                seen.append(rid)
        return seen

    @staticmethod
    def _user_content(request: LlmRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content
        return ""

    def _body_for_schema(
        self, schema: dict | None, rule_ids: list[str], user_content: str = ""
    ) -> dict[str, Any]:
        props = _schema_properties(schema)
        rids = list(rule_ids)
        if "summary" in props:
            # Narrate the already-decided outcome; never assert a gate decision that
            # contradicts the deterministic engine. The rendered FINDINGS block marks each
            # finding FAIL/PASS. Both dispositions remain maker proposals: a compliant
            # recommendation still needs a checker before a regulated claim is released.
            has_failure = "FAIL" in user_content
            if has_failure:
                tail = (
                    "at least one rule failed, so the asset is non-compliant and requires "
                    "human review before it runs."
                )
            else:
                tail = (
                    "every rule passed, so the asset is compliant, subject to human review "
                    "before publication."
                )
            summary = (
                "The deterministic rule engine evaluated this marketing asset against the "
                "per-market, per-vertical rule set; the findings below carry the failing and "
                f"passing rules with their severity. Based on those cited findings, {tail}"
            )
            return {"summary": summary, "used_rule_ids": rids}
        if "narrative" in props:
            # Green-claim substantiation narration. The coverage engine has ALREADY decided
            # the verdict and the coverage; this prose deliberately restates neither, so the
            # offline narrator can never contradict or "improve" a consequential number.
            has_gap = "gaps:" in user_content or "FAIL" in user_content
            tail = (
                "at least one claim is not fully carried by the evidence on file, or a "
                "green-claim rule failed, so the asset must not run until a compliance "
                "officer has signed it off."
                if has_gap
                else "the evidence on file addresses each required item for the claims made, "
                "and a compliance officer still signs off before the asset runs."
            )
            narrative = (
                "The coverage engine classified the environmental claims in this asset and "
                "matched them against the substantiation evidence the tenant holds; the "
                "claim-coverage block lists the evidence counted and the gaps found. Reading "
                f"those cited results, {tail}"
            )
            return {"narrative": narrative, "used_rule_ids": rids}
        # Flat fallback object (self-critique style).
        return {"grounded": bool(rids), "confidence": 0.86 if rids else 0.2, "caveats": []}
