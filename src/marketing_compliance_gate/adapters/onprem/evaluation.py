"""On-prem placeholder for ``EvaluationGatePort`` — the sovereign migration target."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import EvalReport

_MESSAGE = (
    "On-prem EvaluationGatePort adapter is a migration placeholder; implement against your "
    "on-premise evaluation harness. Core domain logic is unchanged."
)


class OnPremEvalAdapter:
    """Placeholder evaluation-gate adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, dataset_path: str) -> EvalReport:
        raise NotImplementedError(_MESSAGE)

    def gate(self, target: str) -> bool:
        raise NotImplementedError(_MESSAGE)
