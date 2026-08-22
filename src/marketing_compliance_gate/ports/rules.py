"""RuleProviderPort — the per-market, per-vertical compliance rule KB.

D6 grounds every review on the advertising + consumer-protection + consent rules in
force for the asset's (market, vertical). The primary GCP adapter is **Gemini API File
Search** over the rule corpus (the rule KB); the ``platform`` adapter is a thin HTTP
client to the shared A2 Enterprise Knowledge Base; the local adapter is an in-process
SQLite FTS5 store over the seeded fictional rule sets. The port returns a fully-typed
:class:`RuleSet` so the deterministic :class:`RuleEngine` can evaluate it; the LLM never
sees the rules until after the engine has decided the findings.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Market, RuleSet, Vertical


@runtime_checkable
class RuleProviderPort(Protocol):
    def rule_set(self, market: Market, vertical: Vertical) -> RuleSet:
        """Return the rules in force for ``(market, vertical)`` (may be empty)."""
        ...

    def search(self, market: Market, vertical: Vertical, text: str) -> RuleSet:
        """Return the subset of rules whose text matches ``text`` (KB-style lookup)."""
        ...
