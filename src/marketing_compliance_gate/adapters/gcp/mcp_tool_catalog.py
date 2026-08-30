"""MCP tool-catalog adapter (ToolCatalogPort), the governed tool surface for Mkt6.

Backs the domain ``ToolCatalogPort`` by exposing Mkt6's governed, least-privilege
capabilities as :class:`ToolSpec` objects: ``review_asset`` and ``search_rules``. These are
the tools the agent (or a peer agent) may invoke, each with an explicit JSON input schema so
access is scoped and auditable (least privilege).

**``approve_review`` was declared here and is deliberately gone.** It took a ``review_id``
that nothing in this service can resolve, which is how it was found: the catalog had never
been served, so nothing had ever checked that a declared tool could be answered. Supplying
the missing store was the wrong repair. Approval IS the four-eyes control, and this
repository had already decided twice in writing that it is not a tool:
``agent/tools.py`` excludes it from the ADK surface because "an agent that could approve its
own reviews would defeat the four-eyes control", and ``domain/services.py`` records that the
router is "bound only on this maker (review-producer) path; the agent never gets an approve
tool". Serving it over a transport that verifies no human would have let an unauthenticated
caller act as the checker. The checker half is Hrz7's: rule R8 routes an escalated review to
the review console, which resolves a real principal before anyone disposes.

So this catalog declares the maker half only, and
``tests/unit/test_mcp_surface_is_served_and_packaged.py`` holds it there rather than trusting
the absence.

Interop: the catalog speaks **MCP 2026-07-28**. It is served by
``marketing_compliance_gate.mcp``, which binds these declarations to the callables that
already perform them and refuses to start on a mismatch in either direction. The ``mcp``
package is imported LAZILY and only when an actual MCP wire object is requested.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import ToolSpec

# MCP protocol revision this catalog conforms to.
MCP_PROTOCOL_VERSION = "2026-07-28"

# Shared schema fragment: the (market, vertical) scope key, reused across tools.
#
# These read as optional filters ("Restrict to a single market") until the catalog is actually
# SERVED, and then they do not hold. ``RuleProviderPort.search`` takes a market and a vertical
# and every adapter filters on both; there is no search-all-markets path to fall back to. A
# handler answering a call that omitted them would have to pick a market, and picking one means
# silently answering a Singapore question with Japanese rules.
#
# The managed adapter makes it sharper than a wrong answer: ``file_search_rules.search`` calls
# ``resolve_region(settings, market=market)``, so the market is what the per-market RESIDENCY
# check keys on. An optional market is an optional residency check.
#
# They are required, and named as the scope rather than described as a filter.
_SCOPE_SCHEMA: dict[str, Any] = {
    "market": {
        "type": "string",
        "enum": ["JP", "AU", "SG"],
        "description": "The market whose rules apply, and whose residency region is checked.",
    },
    "vertical": {
        "type": "string",
        "enum": ["banking", "online_retail"],
        "description": "The vertical whose rule set applies.",
    },
}

#: The keys of :data:`_SCOPE_SCHEMA`, so ``required`` cannot drift from what it declares.
_SCOPE_REQUIRED: list[str] = list(_SCOPE_SCHEMA)


def _build_catalog() -> dict[str, ToolSpec]:
    """Declare the governed tools with explicit, least-privilege input schemas."""
    return {
        "review_asset": ToolSpec(
            name="review_asset",
            description=(
                "Run a deterministic compliance review of a Campaign / Creative / Offer "
                "against the per-market, per-vertical rule set. Output requires human review "
                "(maker-checker) when non-compliant."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Asset title."},
                    "body": {"type": "string", "description": "Marketing copy to check."},
                    "asset_type": {
                        "type": "string",
                        "enum": ["campaign", "creative", "offer"],
                    },
                    **_SCOPE_SCHEMA,
                },
                "required": ["body", *_SCOPE_REQUIRED],
                "additionalProperties": False,
            },
        ),
        "search_rules": ToolSpec(
            name="search_rules",
            description=(
                "Search the per-market, per-vertical compliance rule KB (File Search) and "
                "return the matching cited rules."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query."},
                    **_SCOPE_SCHEMA,
                },
                "required": ["query", *_SCOPE_REQUIRED],
                "additionalProperties": False,
            },
        ),
    }


class McpToolCatalogAdapter:
    """The MCP 2026-07-28 catalog of Mkt6's governed tools, served by ``..mcp``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._catalog: dict[str, ToolSpec] = _build_catalog()

    # ------------------------------------------------------------------ #
    # ToolCatalogPort
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[ToolSpec]:
        return list(self._catalog.values())

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._catalog.get(name)

    # ------------------------------------------------------------------ #
    # MCP wire helpers (lazy ``mcp`` import, only when actually used)
    # ------------------------------------------------------------------ #
    def as_mcp_tools(self) -> list[Any]:
        """Render the catalog as MCP ``Tool`` objects (MCP 2026-07-28 schema)."""
        from mcp import types as mcp_types  # noqa: PLC0415, lazy

        # verify: https://modelcontextprotocol.io/specification/2026-07-28
        return [
            mcp_types.Tool(name=s.name, description=s.description, input_schema=s.input_schema)
            for s in self._catalog.values()
        ]
