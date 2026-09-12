"""RuleProviderPort — the per-market, per-vertical compliance rule source.

D6 grounds every review on the advertising + consumer-protection + consent rules in
force for the asset's (market, vertical). The ``gcp`` adapter serves the versioned rule
pack bundled in the package from memory, so a deployment provisions no managed rule store;
the ``local`` adapter indexes the same pack in SQLite FTS5 (seedable, for tests); the
``platform`` adapter is a thin HTTP client to the shared ``enterprise-knowledge-base``. The
port returns a fully-typed :class:`RuleSet`, stamped with the pack version it came from, so
the deterministic :class:`RuleEngine` can evaluate it; the LLM never sees the rules until
after the engine has decided the findings.
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
