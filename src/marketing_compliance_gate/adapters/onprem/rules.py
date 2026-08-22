"""On-prem placeholder for ``RuleProviderPort`` — the sovereign migration target.

A reversibility (no-lock-in) placeholder: in the managed profile this port binds to the
Gemini File Search rule-KB adapter; switching ``profile`` to ``onprem`` rebinds it here.
The adapter constructs cleanly with **no external dependencies** and structurally
satisfies the same Protocol, so the contract tests prove interface parity. Porting D6
on-premise is only a matter of filling these bodies in against your on-prem rule store;
the domain rule engine and orchestration do not change.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Market, RuleSet, Vertical

_MESSAGE = (
    "On-prem RuleProviderPort adapter is a migration placeholder; implement against your "
    "on-premise compliance rule store. Core domain logic is unchanged."
)


class OnPremRuleProviderAdapter:
    """Placeholder rule-provider adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def rule_set(self, market: Market, vertical: Vertical) -> RuleSet:
        raise NotImplementedError(_MESSAGE)

    def search(self, market: Market, vertical: Vertical, text: str) -> RuleSet:
        raise NotImplementedError(_MESSAGE)
