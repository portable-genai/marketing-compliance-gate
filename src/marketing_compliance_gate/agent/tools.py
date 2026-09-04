"""ADK FunctionTools that expose the D6 domain service to the agent.

The tool is a thin, side-effect-honest wrapper: it builds the :class:`ReviewService` from a
:class:`~marketing_compliance_gate.config.Container` (so every port is bound to the adapter
selected by the active profile), invokes the domain method, and returns a JSON-safe dict via
:func:`~marketing_compliance_gate.domain.serialization.to_jsonable`.

Maker-checker separation
------------------------
D6 is the marketing maker-checker **gate**. This agent is the **maker**: it exposes only
``review_marketing_asset`` (produce a review). The **checker** half (``ReviewService.approve``)
is deliberately NOT exposed as a tool, because an agent that could approve its own reviews would
defeat the four-eyes control. Approval stays a human action (the API's `approve` path / the
human-review-console).

Design notes
------------
* The domain service owns orchestration and every consequential decision (which rules fail, the
  severity, the compliant / non-compliant outcome, and whether it escalates; SPEC §5). The
  deterministic rule engine decides; the LLM only narrates the findings.
* The green-claims gate is deliberately NOT an agent tool. Reading substantiation evidence is
  authorized against the VERIFIED principal's tenant, and a tool argument is a client-asserted
  value: exposing ``assess`` here would mean an agent could name any tenant it liked. The gate
  is reached through ``POST /v1/substantiation``, where the IdentityPort resolves the principal
  from the transport before any evidence is read.
* ``google.adk`` is imported lazily inside :func:`build_function_tools` so this module imports
  cleanly under the on-prem / local / test profile with no ADK installed (SPEC §4). The plain
  Python tool callable is importable and unit-testable without ADK at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Container, Settings, build_container

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.adk.tools import FunctionTool

_DEFAULT_ACTOR = "compliance-governance-agent"


def _container(settings: Settings | None) -> Container:
    return build_container(settings)


def review_marketing_asset(
    body: str,
    asset_type: str = "creative",
    title: str = "",
    market: str = "SG",
    vertical: str = "banking",
    asset_id: str = "asset-1",
    fields: dict[str, str] | None = None,
    granted_consents: list[str] | None = None,
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Review a marketing asset against its per-market, per-vertical rule set.

    Runs the deterministic claim / permission / brand / consent checks and returns a
    ``Review`` with the findings, the compliant / non-compliant outcome, a narrated summary and
    a PENDING approval record. Every release or block disposition is flagged for human review
    (maker-checker) and must be dispositioned by a human checker; this tool never approves.

    Args:
      body: The marketing copy to check.
      asset_type: "campaign", "creative" or "offer".
      title: Asset title.
      market: Market code: "JP", "AU" or "SG".
      vertical: "banking" or "online_retail".
      asset_id: Asset id.
      fields: Structured metadata the numeric / permission checks evaluate (e.g. {"apr": "4.50"}).
      granted_consents: Consent purposes the audience has granted.
      actor: Authenticated identity the request is made for.

    Returns:
      A JSON-safe ``Review`` dict.
    """
    from ..api.deps import make_review_service
    from ..domain.models import AssetType, Market, MarketingAsset, ReviewRequest, Vertical
    from ..domain.serialization import to_jsonable

    c = _container(settings)
    asset = MarketingAsset(
        id=asset_id,
        asset_type=AssetType(asset_type),
        title=title,
        body=body,
        market=Market(market),
        vertical=Vertical(vertical),
        fields=dict(fields or {}),
        granted_consents=tuple(granted_consents or ()),
    )
    request = ReviewRequest(asset=asset, actor=actor)
    return to_jsonable(make_review_service(c).review(request, actor=actor))


TOOL_FUNCTIONS = (review_marketing_asset,)


def governed_tool_names() -> frozenset[str]:
    """The tool names this agent exposes (mirrors the governed MCP catalog, rule R4)."""
    return frozenset(fn.__name__ for fn in TOOL_FUNCTIONS)


def build_function_tools() -> list[FunctionTool]:
    """Wrap each domain-service callable as an ADK ``FunctionTool``.

    ADK introspects each function's signature and docstring to derive the tool name,
    description and parameter JSON schema. ``google.adk`` is imported here (lazily) so the
    module is import-safe without ADK installed (SPEC §4).
    """
    from google.adk.tools import FunctionTool

    return [FunctionTool(func=fn) for fn in TOOL_FUNCTIONS]
