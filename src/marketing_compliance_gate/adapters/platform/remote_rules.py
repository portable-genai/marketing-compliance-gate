"""Remote-platform rule-provider adapter (RuleProviderPort) — thin HTTP client to A2.

When D6 reuses the shared platform, the compliance rule KB is served by the **A2
Enterprise Knowledge Base**. This adapter implements the port by calling A2's rule
endpoints (base URL from ``KNOWLEDGE_BASE_URL``). Constructs cleanly with no Google Cloud SDK;
the HTTP body is wired in the platform phase.
"""

from __future__ import annotations

from ...domain.errors import ComplianceGovError
from ...domain.models import Market, RuleSet, Vertical
from ...envread import setting_or_default

_DEFAULT_URL = "http://localhost:8082"
_PHASE = "RemoteRuleProviderAdapter is wired in the platform phase."


class RemoteRuleProviderError(ComplianceGovError):
    """Raised when the A2 knowledge-base service returns a non-2xx response."""


class RemoteRuleProviderAdapter:
    """HTTP client for the shared A2 enterprise knowledge base (rule KB)."""

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._base_url = setting_or_default("KNOWLEDGE_BASE_URL", _DEFAULT_URL).rstrip("/")

    def rule_set(self, market: Market, vertical: Vertical) -> RuleSet:
        raise NotImplementedError(_PHASE)

    def search(self, market: Market, vertical: Vertical, text: str) -> RuleSet:
        raise NotImplementedError(_PHASE)
