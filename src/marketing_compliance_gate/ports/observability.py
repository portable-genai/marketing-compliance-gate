"""Observability ports — the A5 (audit/trace) and A4 (eval gate) concerns.

Primary GCP adapters: a **Cloud Logging locked WORM bucket** for immutable audit, **Cloud
Trace via OpenTelemetry** for the reasoning-loop traces, and the **Gen AI evaluation
service** plus the A4 promotion gate for model risk.

``ObservabilityTracerPort`` and ``EvaluationGatePort`` are NOT written out here. They come
from the shared commons, for the same reason ``IdentityPort`` does: sixteen repositories
each hand-copied these Protocols and they had already drifted apart. One had dropped the
evaluation port entirely, two had dropped its ``gate`` method (the half that can refuse a
promotion), and one returned ``str`` from an audit ``record`` that returns ``None``
everywhere else. A Protocol copied into N repositories is N Protocols, and only one of them
gets fixed when a defect is found.

The two ports split across the two commons packages by where their types already live: the
tracer beside the :class:`TokenUsage` it reports, the gate beside the ``EvalReport`` it
returns. Both are typing-only imports, so this module still costs the local and on-prem
profiles nothing: no OpenTelemetry, no cloud SDK.

``AuditSinkPort`` stays declared here: it is typed in this repo's own ``AuditEvent``
vocabulary, which is not the commons'.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_eval_kit import EvaluationGatePort
from hex_service_kit.observability import ObservabilityTracerPort, TokenUsage

from ..domain.models import AuditEvent


@runtime_checkable
class AuditSinkPort(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Write an immutable audit record (WORM)."""
        ...


__all__ = [
    "AuditSinkPort",
    "EvaluationGatePort",
    "ObservabilityTracerPort",
    "TokenUsage",
]
