"""Unit tests for the local deterministic LLM narrator (LlmPort stand-in).

The narrator only turns the already-decided findings into prose; it must never assert a
gate decision that contradicts the deterministic engine. A clean (all-PASS) review remains
a maker proposal: recommending release is consequential and still needs a checker.
"""

from __future__ import annotations

import json

from marketing_compliance_gate.adapters.local.llm import LocalDeterministicLLMAdapter
from marketing_compliance_gate.config import Settings
from marketing_compliance_gate.domain.models import LlmMessage, LlmRequest

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "used_rule_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary"],
}


def _adapter() -> LocalDeterministicLLMAdapter:
    return LocalDeterministicLLMAdapter(Settings())


def _summary(findings_block: str) -> str:
    prompt = f"Summarise the review.\n\nFINDINGS:\n{findings_block}"
    resp = _adapter().generate(
        LlmRequest(
            messages=(LlmMessage(role="user", content=prompt),),
            response_schema=_SUMMARY_SCHEMA,
        )
    )
    return json.loads(resp.text)["summary"]


def test_compliant_summary_still_requires_a_checker():
    """An all-PASS block may recommend release but cannot bypass maker-checker."""
    block = "[R-A] PASS (critical) No prohibited phrase.\n[R-B] PASS (high) Disclosure present."
    summary = _summary(block)
    assert "compliant" in summary
    assert "subject to human review before publication" in summary


def test_non_compliant_summary_claims_human_review():
    block = "[R-A] FAIL (critical) prohibited phrase.\n[R-B] PASS (high) ok."
    summary = _summary(block)
    assert "non-compliant" in summary
    assert "requires human review" in summary


def test_used_rule_ids_only_cite_rules_in_the_block():
    block = "[R-ONE] FAIL (high) bad.\n[R-TWO] PASS (low) ok."
    resp = _adapter().generate(
        LlmRequest(
            messages=(LlmMessage(role="user", content=f"FINDINGS:\n{block}"),),
            response_schema=_SUMMARY_SCHEMA,
        )
    )
    assert json.loads(resp.text)["used_rule_ids"] == ["R-ONE", "R-TWO"]
