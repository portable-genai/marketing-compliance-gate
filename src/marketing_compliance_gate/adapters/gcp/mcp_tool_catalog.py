"""MCP tool-catalog adapter (ToolCatalogPort) — the governed tool surface for D6.

Backs the domain ``ToolCatalogPort`` by exposing D6's governed, least-privilege
capabilities as :class:`ToolSpec` objects: ``review_asset``, ``search_rules`` and
``approve_review``. These are the tools the agent (or a peer agent) may invoke, each
with an explicit JSON input schema so access is scoped and auditable (least privilege).

Interop: the catalog speaks **MCP 2026-07-28**. In an ADK deployment these specs are
surfaced to the agent through an ``McpToolset`` connected to an MCP server fronting the
domain services; here the adapter only *declares* the governed catalog (declarative, no live
MCP connection required to list). The ``mcp`` package is imported LAZILY and only when an
actual MCP wire object is requested.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import ToolSpec

# MCP protocol revision this catalog conforms to.
MCP_PROTOCOL_VERSION = "2026-07-28"

# Shared schema fragment: market / vertical scoping reused across tools.
_SCOPE_SCHEMA: dict[str, Any] = {
    "market": {
        "type": "string",
        "enum": ["JP", "AU", "SG"],
        "description": "Restrict to a single market.",
    },
    "vertical": {
        "type": "string",
        "enum": ["banking", "online_retail"],
        "description": "Restrict to a single vertical.",
    },
}


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
                "required": ["body"],
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
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        "approve_review": ToolSpec(
            name="approve_review",
            description=(
                "Record a human checker's approve / reject decision on a review (the "
                "marketing maker-checker gate). Audited; never auto-approves."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "review_id": {"type": "string"},
                    "approved": {"type": "boolean"},
                    "rationale": {"type": "string"},
                },
                "required": ["review_id", "approved"],
                "additionalProperties": False,
            },
        ),
    }


class McpToolCatalogAdapter:
    """Declarative MCP 2026-07-28 catalog of D6's governed tools."""

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
    # MCP wire helpers (lazy ``mcp`` import — only when actually used)
    # ------------------------------------------------------------------ #
    def as_mcp_tools(self) -> list[Any]:
        """Render the catalog as MCP ``Tool`` objects (MCP 2026-07-28 schema)."""
        from mcp import types as mcp_types  # noqa: PLC0415 — lazy

        # verify: https://modelcontextprotocol.io/specification/2026-07-28
        return [
            mcp_types.Tool(name=s.name, description=s.description, inputSchema=s.input_schema)
            for s in self._catalog.values()
        ]
