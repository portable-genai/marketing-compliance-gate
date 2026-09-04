"""A2A registry adapter (AgentRegistryPort): agent discovery and governance for
marketing-compliance-gate (A3).

Backs the domain ``AgentRegistryPort`` with an in-process, **A2A v1.0**-style registry of
:class:`AgentCard` objects. In a standalone deployment marketing-compliance-gate registers its own
card here and can serve it at the well-known A2A discovery path; inside the full platform the
``platform`` profile swaps this for a thin client to the shared agent registry.

A2A discovery contract: an agent publishes its capabilities as an **AgentCard** served at
``/.well-known/agent-card.json``; peers fetch that card to learn the agent's skills,
endpoint URL and version before initiating an A2A task. ``agent_card_dict`` produces that
JSON body. No external call is required: this adapter is pure, in-memory governance, so it
needs no Google import and constructs cleanly under any profile.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AgentCard, AgentSkill

# The A2A well-known discovery path for an agent's card.
AGENT_CARD_PATH = "/.well-known/agent-card.json"

# marketing-compliance-gate's own skills, surfaced on its AgentCard so peers / the registry can
# discover the
# governed compliance-governance capabilities the system offers (generic across verticals).
#
# **This list used to carry a second skill, and it contradicted the card actually served.**
# ``approve_review`` ("Maker-checker approval") was advertised here while
# ``agent/agent_card.py``, which is what ``GET /.well-known/agent-card.json`` returns, declares
# the maker skill only and states that approval "is deliberately not advertised as an agent
# skill". So a peer discovering marketing-compliance-gate through the registry was told it could
# have a review
# approved, and a peer reading the served card was told it could not, from one repository. The
# route's own docstring claimed a peer and the registry "sees one capability surface"; they did
# not.
#
# It is gone for the reason the served card already gave. Approval IS the four-eyes control:
# advertising it to a peer AGENT offers the checker's half to a caller that is not a human, and
# rule R8 already routes an escalated review to the human-review-console, which resolves a real
# principal before anyone disposes. The same declaration was removed from the MCP catalog in
# the same change, so all three surfaces now agree.
#
# ``tests/unit/test_mcp_surface_is_served_and_packaged.py`` holds the two cards together rather
# than trusting them to stay in step.
_D6_SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="review_asset",
        name="Marketing compliance review",
        description=(
            "Review a Campaign / Creative / Offer against the per-market, per-vertical "
            "advertising, consumer-protection and consent rules, for any of banking / "
            "online retail across JP/AU/SG, with a cited finding per rule. The maker half: "
            "a non-compliant review escalates to a human checker and is never self-approved."
        ),
    ),
)


class A2ARegistryAdapter:
    """In-process A2A AgentCard registry: register / get / list, plus card export."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cards: dict[str, AgentCard] = {}
        # Seed the registry with marketing-compliance-gate's own card so a standalone deployment is
        # discoverable.
        self.register(self._self_card())

    # ------------------------------------------------------------------ #
    # AgentRegistryPort
    # ------------------------------------------------------------------ #
    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list(self) -> list[AgentCard]:
        return list(self._cards.values())

    # ------------------------------------------------------------------ #
    # A2A discovery helper
    # ------------------------------------------------------------------ #
    def agent_card_dict(self, name: str | None = None) -> dict:
        """Return the ``/.well-known/agent-card.json`` body for ``name`` (default:
        marketing-compliance-gate's).
        """
        card = self.get(name) if name else self._cards.get(self._self_name())
        if card is None:
            raise KeyError(f"No AgentCard registered for '{name}'.")
        return {
            "name": card.name,
            "description": card.description,
            "url": card.url,
            "version": card.version,
            "provider": card.provider,
            "skills": [
                {"id": s.id, "name": s.name, "description": s.description} for s in card.skills
            ],
        }

    # ------------------------------------------------------------------ #
    # D6's own card
    # ------------------------------------------------------------------ #
    def _self_name(self) -> str:
        return self._settings.agent_engine.display_name or "marketing-compliance-gate"

    def _self_card(self) -> AgentCard:
        return AgentCard(
            name=self._self_name(),
            description=(
                "Marketing Compliance and Brand Governance: a deterministic rule engine "
                "checking claims, permissions, brand and consent against per-market, "
                "per-vertical rules, generic across banking and online retail and the "
                "JP/AU/SG markets, with a cited finding per rule and a maker-checker gate."
            ),
            url=f"https://marketing-compliance-gate.{self._settings.region}.example/a2a",
            version="1.0.0",
            skills=_D6_SKILLS,
            provider="marketing-compliance-gate",
        )
