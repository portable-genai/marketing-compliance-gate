"""Serve the governed tool catalog Mkt6 already declares, over MCP 2026-07-28.

The catalog declared three governed tools and served none of them. This supplies the callables
that answer the surviving declarations and declares nothing new;
``hex_service_kit.mcpserve.bind`` refuses a catalog/handler mismatch in either direction at
start-up, which is what turns a declaration into a promise something has to keep.

**One of the three declarations did not survive, and it is the reason this module exists.**
``approve_review`` took a ``review_id`` nothing here can resolve. The missing store was the
symptom; the cause is that approval IS the four-eyes control, and this repository had already
decided twice in writing that it is not a tool. Serving it on stdio, which verifies no human,
would have handed an unauthenticated caller the checker's half of a maker-checker gate. Rule R8
routes an escalated review to the Hrz7 console, which resolves a real principal first. The
reasoning is kept where the declaration was, in ``adapters/gcp/mcp_tool_catalog.py``.

**Both surviving tools take the (market, vertical) scope, and neither invents one.** The
schemas made those optional; the rule port makes them mandatory, and the managed adapter keys
its residency check on the market. A handler that defaulted them would answer a Singapore
question with Japanese rules and skip that check on the way. The schemas now require them, so a
caller that omits one is refused by the schema rather than quietly served the wrong
jurisdiction.

MCP stdio verifies no end user, so the caller is recorded as a SERVICE caller and no tenant is
asserted. That is also why nothing here reads tenant-scoped evidence: the green-claims gate is
excluded for the same reason ``agent/tools.py`` excludes it, because a tool argument is a
client-asserted value and substantiation is authorized against a verified principal's tenant.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit import mcpserve

from ..config import Container, build_container

#: The tools this module answers, as data, so a test can hold it against the catalog.
HANDLER_NAMES: tuple[str, ...] = ("review_asset", "search_rules")

#: The asset type assumed when a caller names none. The schema leaves it optional and the
#: domain requires one, so it is chosen here rather than left undefined. ``creative`` is the
#: narrowest of the three: a campaign or an offer carries obligations a creative does not, so
#: assuming one of those would apply rules the caller never said were in scope.
_DEFAULT_ASSET_TYPE = "creative"

#: The id recorded for an asset the caller did not identify. MCP stdio carries no asset
#: registry, and a blank id would make two unrelated reviews indistinguishable in the trail.
_UNIDENTIFIED_ASSET_ID = "mcp-unidentified"


def build_handlers(actor: str, container: Container | None = None) -> dict[str, mcpserve.Handler]:
    """Bind each declared tool to the service that already performs it.

    ``container`` is explicit so a caller can choose its own profile rather than inherit the
    process-wide one. A test that borrows its posture from whatever the Makefile exported is a
    test measuring the build file, and this repository has been caught by that before.
    """
    from ..api.deps import get_container, make_review_service
    from ..domain.models import AssetType, Market, MarketingAsset, ReviewRequest, Vertical
    from ..domain.serialization import to_jsonable

    def _scope(arguments: dict[str, Any]) -> tuple[Market, Vertical]:
        """Resolve the scope key. The schema requires both, so neither is defaulted here."""
        return Market(str(arguments["market"])), Vertical(str(arguments["vertical"]))

    def _container() -> Any:
        return container if container is not None else get_container()

    def review_asset(**arguments: Any) -> Any:
        market, vertical = _scope(arguments)
        asset = MarketingAsset(
            id=_UNIDENTIFIED_ASSET_ID,
            asset_type=AssetType(str(arguments.get("asset_type") or _DEFAULT_ASSET_TYPE)),
            title=str(arguments.get("title", "") or ""),
            body=str(arguments["body"]),
            market=market,
            vertical=vertical,
        )
        request = ReviewRequest(asset=asset, actor=actor)
        return to_jsonable(make_review_service(_container()).review(request, actor=actor))

    def search_rules(**arguments: Any) -> Any:
        market, vertical = _scope(arguments)
        rules = _container().rule_provider.search(market, vertical, str(arguments["query"]))
        return to_jsonable(rules)

    return {"review_asset": review_asset, "search_rules": search_rules}


def build_server(actor: str, *, with_audit_tools: bool = True) -> Any:
    """Build the MCP server for Mkt6's catalog, refusing on any catalog/handler mismatch."""
    container = build_container()
    return mcpserve.build_server(
        name="marketing-compliance-gate",
        version=str(getattr(container.settings, "version", "") or "0.0.1"),
        catalog=container.tool_catalog,
        handlers=build_handlers(actor, container),
        audit_store=getattr(container, "audit", None) if with_audit_tools else None,
    )
