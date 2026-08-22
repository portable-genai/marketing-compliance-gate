"""Ports — the abstract interfaces (the hexagon boundary).

Every port is a ``typing.Protocol`` (``@runtime_checkable``) so adapters need only
structural conformance and the contract test can verify any adapter family (GCP,
remote-platform, on-prem placeholder, or local) satisfies the same contract.

``IdentityPort``, ``ObservabilityTracerPort`` and ``EvaluationGatePort`` are not redeclared
in this repo: they come from the shared commons packages and are re-exported here so
consumers still have one import site for the boundary set. See :mod:`.observability`.
"""

from .consent import ConsentStorePort
from .evidence import EvidenceStorePort
from .generation import LlmPort
from .governance import AgentRegistryPort, ToolCatalogPort
from .identity import IdentityPort
from .observability import (
    AuditSinkPort,
    EvaluationGatePort,
    ObservabilityTracerPort,
    TokenUsage,
)
from .review_router import ReviewRouterPort
from .rules import RuleProviderPort
from .safety import GuardrailPort

__all__ = [
    "RuleProviderPort",
    "EvidenceStorePort",
    "ConsentStorePort",
    "LlmPort",
    "GuardrailPort",
    "AuditSinkPort",
    "ObservabilityTracerPort",
    "EvaluationGatePort",
    "TokenUsage",
    "AgentRegistryPort",
    "ToolCatalogPort",
    "IdentityPort",
    "ReviewRouterPort",
]
