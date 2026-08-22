"""File Search rule-provider adapter (RuleProviderPort) — GCP managed stack.

The primary rule-KB backend: the **Gemini API File Search** tool over the per-market,
per-vertical compliance rule corpus (advertising + consumer-protection + consent rules).
File Search is the managed RAG store in the unified **Google GenAI SDK** (``google-genai``):
the adapter issues a grounded ``generate_content`` against the configured File Search store,
scoped to the market and vertical, and maps each grounding chunk back to a domain
:class:`Rule` carrying a :class:`Citation` so every rule the engine evaluates is traceable.

The residency region is resolved from the active market and **validated** against the
per-market allow-list, so rule retrieval stays inside the configured residency boundary
(JP/AU/SG).

All Google Cloud / GenAI SDK imports are LAZY so the on-prem / local / test profile imports
this module without ``google-genai`` installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.models import (
    CheckType,
    Citation,
    Market,
    Rule,
    RuleKind,
    RuleSet,
    Severity,
    SourceType,
    Vertical,
)
from ._region import resolve_region

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google import genai


class FileSearchRuleProviderAdapter:
    """Retrieve compliance rules via the Gemini File Search tool over the rule KB."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store_id = settings.knowledge_base.data_store_id
        self._top_k = settings.knowledge_base.top_k
        self._model = settings.models.reasoning
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy, region-validated client construction
    # ------------------------------------------------------------------ #
    def _get_client(self) -> genai.Client:
        region = resolve_region(self._settings, market=None)
        if self._client is None:
            from google import genai  # noqa: PLC0415 — lazy: gcp profile only

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.project_id,
                location=region,
            )
        return self._client

    # ------------------------------------------------------------------ #
    # RuleProviderPort
    # ------------------------------------------------------------------ #
    def rule_set(self, market: Market, vertical: Vertical) -> RuleSet:
        """Return every rule in force for ``(market, vertical)`` via File Search."""
        return self.search(market, vertical, "all advertising and consent rules")

    def search(self, market: Market, vertical: Vertical, text: str) -> RuleSet:
        """Retrieve rules relevant to ``text`` for ``(market, vertical)`` via File Search."""
        from google.genai import types  # noqa: PLC0415

        # Validate residency for the requested market before any retrieval.
        resolve_region(self._settings, market=market)
        client = self._get_client()
        query = (
            f"market={market.value} vertical={vertical.value}: {text}. "
            "Return the applicable compliance rules."
        )
        file_search = types.Tool(
            file_search=types.FileSearch(file_search_store_names=[self._store_id])
        )
        response = client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=query)])],
            config=types.GenerateContentConfig(tools=[file_search], temperature=0.0),
        )
        rules = self._to_rules(response, market, vertical)
        return RuleSet(market=market, vertical=vertical, rules=rules)

    # ------------------------------------------------------------------ #
    # Response mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_rules(response: Any, market: Market, vertical: Vertical) -> tuple[Rule, ...]:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ()
        metadata = getattr(candidates[0], "grounding_metadata", None)
        if metadata is None:
            return ()
        chunks = getattr(metadata, "grounding_chunks", None) or []
        rules: list[Rule] = []
        for idx, chunk in enumerate(chunks):
            retrieved = getattr(chunk, "retrieved_context", None)
            if retrieved is None:
                continue
            text = getattr(retrieved, "text", "") or ""
            title = getattr(retrieved, "title", "") or f"rule-{idx}"
            uri = getattr(retrieved, "uri", "") or ""
            rules.append(
                Rule(
                    id=title,
                    kind=RuleKind.CLAIM,
                    check=CheckType.FORBIDDEN_PHRASE,
                    description=text[:200],
                    market=market,
                    vertical=vertical,
                    severity=Severity.MEDIUM,
                    citation=Citation(
                        source_id=title,
                        source_type=SourceType.RULE,
                        title=title,
                        url=uri,
                        snippet=text[:200],
                    ),
                )
            )
        return tuple(rules)
