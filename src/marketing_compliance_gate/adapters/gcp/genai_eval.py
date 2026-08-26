"""Gen AI evaluation gate adapter (EvaluationGatePort) — the A4 promotion gate for D6.

Backs the domain ``EvaluationGatePort`` with the **Gen AI evaluation service**, accessed
through ``vertexai.Client(project, location).evals``. Over a golden dataset of marketing
assets it scores the system on the metrics that matter for a cited compliance-governance
system (rule coverage, finding accuracy, citation accuracy, review safety) and maps the
result onto an :class:`EvalReport` whose ``passed`` flag gates promotion in CI/CD.

The Vertex AI SDK import is LAZY so the on-prem / local / test profile imports without it.
The offline gate (``eval/run_eval.py``, used by the local adapter and CI) mirrors these
thresholds so a release that fails offline can never be promoted on-cloud.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import EvalMetricResult, EvalReport

# Promotion thresholds (0..1). A metric passes when its score >= threshold; the report
# passes only when every metric passes. These mirror eval/rubrics/*.yaml.
_THRESHOLDS: dict[str, float] = {
    "rule_coverage": 0.95,
    "finding_accuracy": 0.90,
    "citation_accuracy": 0.99,
    "review_safety": 0.99,
}


class GenAiEvalAdapter:
    """Run the Gen AI evaluation service and map results to a domain ``EvalReport``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy SDK plumbing
    # ------------------------------------------------------------------ #
    def _evals(self) -> Any:
        if self._client is None:
            import vertexai  # noqa: PLC0415 — lazy: gcp profile only

            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation
            self._client = vertexai.Client(
                project=self._settings.project_id, location=self._settings.models.location
            )
        return self._client.evals

    # ------------------------------------------------------------------ #
    # EvaluationGatePort
    # ------------------------------------------------------------------ #
    def evaluate(self, dataset_path: str) -> EvalReport:
        """Score the golden dataset at ``dataset_path`` and return a pass/fail report."""
        evals = self._evals()
        # verify: exact evals API shape —
        # https://cloud.google.com/vertex-ai/generative-ai/docs/models/run-evaluation
        inference = evals.run_inference(model=self._settings.models.reasoning, src=dataset_path)
        result = evals.evaluate(dataset=inference, metrics=list(_THRESHOLDS))
        return self._to_report(dataset_path, result)

    def gate(self, target: str) -> bool:
        """Promotion gate: True iff ``target`` clears the A4 quality thresholds."""
        return self.evaluate(target).passed

    # ------------------------------------------------------------------ #
    # Response mapping
    # ------------------------------------------------------------------ #
    def _to_report(self, dataset_path: str, result: Any) -> EvalReport:
        summary = getattr(result, "summary_metrics", None) or {}
        results: list[EvalMetricResult] = []
        for metric, threshold in _THRESHOLDS.items():
            score = float(summary.get(metric, 0.0) or 0.0)
            results.append(
                EvalMetricResult(
                    metric=metric, score=score, threshold=threshold, passed=score >= threshold
                )
            )
        n = int(getattr(result, "num_examples", 0) or 0)
        return EvalReport(dataset=dataset_path, results=tuple(results), n_examples=n)
