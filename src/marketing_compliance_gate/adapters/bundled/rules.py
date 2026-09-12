"""Bundled rule-pack adapter (RuleProviderPort) — the managed profile's rule source.

The ``gcp`` profile used to bind its rule source to a Gemini File Search store that nothing
in ``infra/terraform`` provisioned, so a deployed review would have loaded an empty rule set
and refused (``RuleSetEmptyError``), or, had the store existed, evaluated whatever documents
somebody had uploaded to it. The decision (2026-09-12) is that the deployment needs no managed
rule store: it serves the versioned rule pack bundled in the repository, the same pack the
``local`` profile indexes, so the rules a review fires on the deployment are the rules the
tests and the evaluation gate proved, at a version the audit event names.

This is the local SQLite FTS5 adapter held entirely in memory. It ignores
``settings.local.db_path`` on purpose: a managed container has no durable disk and must never
inherit a stale on-disk index, and a store found already populated would report an unknown
pack version. Each process seeds the pack at start-up, which is a few hundred rows.

Editing the rules is a repository change (``adapters/local/_seed.py``, bumping
``RULE_PACK_VERSION``), reviewed and gated like any other, never a console edit against a
managed store. Stdlib only; no cloud SDK is imported anywhere on this path.
"""

from __future__ import annotations

import dataclasses

from ...config import Settings
from ..local.rules import LocalRuleProviderAdapter


class BundledRulePackAdapter(LocalRuleProviderAdapter):
    """Serve the bundled, versioned rule pack from an in-memory FTS5 index."""

    def __init__(self, settings: Settings) -> None:
        in_memory = dataclasses.replace(
            settings, local=dataclasses.replace(settings.local, db_path=":memory:")
        )
        super().__init__(in_memory)


__all__ = ["BundledRulePackAdapter"]
