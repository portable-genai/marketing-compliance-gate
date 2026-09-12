"""Remote-platform rule-provider adapter (RuleProviderPort) — thin HTTP client to the shared KB.

When D6 reuses the shared platform, the compliance rule set is served by the
**enterprise-knowledge-base**. This adapter implements the port by calling its rule endpoints
(base URL from the variable ``settings.knowledge_base.base_url_env`` names, ``KNOWLEDGE_BASE_URL``
by default). It is the only profile whose rules arrive over the network: ``gcp`` and ``local``
serve the versioned pack bundled in the package. Constructs cleanly with no Google Cloud SDK;
the HTTP body is wired in the platform phase.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.errors import ComplianceGovError
from ...domain.models import Market, RuleSet, Vertical
from ...envread import setting_or_default

_DEFAULT_URL = "http://localhost:8082"
_PHASE = "RemoteRuleProviderAdapter is wired in the platform phase."


class RemoteRuleProviderError(ComplianceGovError):
    """Raised when the enterprise-knowledge-base service returns a non-2xx response."""


class RemoteRuleProviderAdapter:
    """HTTP client for the shared enterprise-knowledge-base (the platform rule source)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        env_name = settings.knowledge_base.base_url_env
        self._base_url = setting_or_default(env_name, _DEFAULT_URL).rstrip("/")

    def rule_set(self, market: Market, vertical: Vertical) -> RuleSet:
        raise NotImplementedError(_PHASE)

    def search(self, market: Market, vertical: Vertical, text: str) -> RuleSet:
        raise NotImplementedError(_PHASE)
