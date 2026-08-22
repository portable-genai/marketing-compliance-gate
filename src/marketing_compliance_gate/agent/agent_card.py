"""A2A AgentCard for the D6 Marketing Compliance agent (A3 Registry & Governance).

This builds the agent's discovery card (the same minimal A2A shape the ``agent-registry``
service stores and serves, SPEC §6). It is published at ``/.well-known/agent-card.json``;
:func:`agent_card_document` returns the JSON-safe body the API layer serves there, and the
``platform`` registry adapter registers the same card in Hrz3 (rule R4).

The card advertises the skill D6 produces as the **maker** half of the marketing maker-checker
gate (review_marketing_asset), mirroring the ADK FunctionTool. Approval (the checker half) is a
human action and is deliberately not advertised as an agent skill.

This module is pure (domain models only) and imports without ADK or any Google Cloud SDK
installed (SPEC §4).
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..domain.models import AgentCard, AgentSkill

SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="review_marketing_asset",
        name="Marketing compliance review",
        description=(
            "Review a marketing asset (campaign / creative / offer) against its per-market "
            "(JP / AU / SG), per-vertical (banking / online retail) advertising, "
            "consumer-protection and brand-guideline rules plus marketing consent, and return "
            "cited findings, a compliant / non-compliant outcome and a PENDING approval record. "
            "The maker half of the gate: a non-compliant review escalates to a human checker "
            "(P-06). This agent never approves its own reviews."
        ),
    ),
)

_DESCRIPTION = (
    "Marketing compliance and brand-governance agent for a bank or online retailer: the maker "
    "half of the marketing maker-checker gate. Reviews campaigns, creatives and offers against "
    "per-market, per-vertical advertising / consumer-protection / fair-trading rules, brand "
    "guidelines and marketing consent (JP / AU / SG; banking and online retail), flags "
    "non-compliant claims and escalates to a human checker. Built ports-and-adapters on the "
    "Gemini Enterprise Agent Platform. The deterministic rule engine decides the outcome; the "
    "model only narrates the findings, and every finding carries a citation."
)


def build_agent_card(settings: Settings) -> AgentCard:
    """Construct the A2A :class:`AgentCard` for this agent."""
    return AgentCard(
        name="marketing-compliance-gate",
        description=_DESCRIPTION,
        url=_resolve_url(settings),
        version="0.1.0",
        skills=SKILLS,
        provider="marketing-compliance-gate",
    )


def agent_card_document(settings: Settings) -> dict[str, Any]:
    """Return the JSON-safe body to serve at ``/.well-known/agent-card.json``."""
    from ..domain.serialization import to_jsonable

    return to_jsonable(build_agent_card(settings))


def _resolve_url(settings: Settings) -> str:
    """Best-effort public URL for the card, region-pinned to the active market."""
    resource = settings.agent_engine.resource_name
    if resource:
        return f"https://aiplatform.googleapis.com/v1/{resource}"
    return "https://marketing-compliance-gate.mkt.internal/a2a"
