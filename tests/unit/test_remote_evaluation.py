"""Contract test for the platform evaluation adapter (RemoteEvaluationAdapter -> Hrz4).

Proves the thin HTTP client speaks Hrz4's *hardened* AI-quality contract, as enforced by
the agent-eval-kit ``PromotionGateClient``, which refuses thin evidence:

* ``POST /v1/evaluations`` carries a structured ``target``
  (``{model, prompt_version, dataset_id, system}``), a top-level ``dataset_id`` that
  equals ``target.dataset_id``, and the opaque ``bundle`` name — never a metric-name list;
* the ``{"results": [...]}`` body is parsed into an :class:`EvalReport`; the response must
  also carry ``n_examples`` plus durable ``run_id`` / ``dataset_digest`` / ``evaluator``
  identifiers, and each row's ``passed`` must agree with score vs threshold;
* ``gate()`` POSTs to ``/v1/gate`` (POST, not GET) and returns the aggregate verdict of a
  full GateDecision (attested eval evidence, a consistent red-team report, model-card and
  MRM references). A naked ``{"passed": bool}`` or a contradictory body is refused;
* a non-2xx response or inconsistent evidence surfaces as ``RemoteEvaluationError``.

Uses ``respx`` to intercept ``httpx`` with no live Hrz4 service — SDK-free, deterministic.
All identifiers in the fixtures are obviously fictional.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from marketing_compliance_gate import ports
from marketing_compliance_gate.adapters.platform.remote_evaluation import (
    RemoteEvaluationAdapter,
    RemoteEvaluationError,
)
from marketing_compliance_gate.config import Settings
from marketing_compliance_gate.domain.models import EvalReport

_BASE = "https://hrz4.test"


def _adapter(monkeypatch: pytest.MonkeyPatch) -> RemoteEvaluationAdapter:
    monkeypatch.setenv("QUALITY_GATE_URL", _BASE)
    return RemoteEvaluationAdapter(Settings())


def _eval_body(*, coverage_passed: bool = False) -> dict[str, object]:
    """A contract-consistent evaluation body: rows agree with their scores, evidence durable."""
    return {
        "results": [
            {
                "metric": "citation_accuracy",
                "score": 0.995,
                "threshold": 0.99,
                "passed": True,
            },
            {
                "metric": "rule_coverage",
                "score": 0.96 if coverage_passed else 0.90,
                "threshold": 0.95,
                "passed": coverage_passed,
            },
        ],
        "n_examples": 12,
        "run_id": "run-fictional-0001",
        "dataset_version": "golden-marketing@2026-08-01",
        "dataset_digest": "sha256:feedfacefeedfacefeedfacefeedfacefeedface",
        "evaluator": "hrz4-ai-quality (FICTIONAL)",
        "schema_version": "eval-run/v1",
        "artifact_refs": ["gs://fictional-hrz4-evidence/run-fictional-0001/report.json"],
        "attested": True,
    }


def _gate_body(*, eval_passed: bool, redteam_passed: bool = True) -> dict[str, object]:
    """A full, internally consistent GateDecision (the only shape the client accepts)."""
    return {
        "passed": eval_passed and redteam_passed,
        "eval_report": _eval_body(coverage_passed=eval_passed),
        "redteam_report": {
            "passed": redteam_passed,
            "results": [
                {"probe": "prompt-injection", "passed": redteam_passed, "blocked": redteam_passed},
                {"probe": "data-exfiltration", "passed": True, "blocked": True},
            ],
        },
        "model_card_ref": "gs://fictional-hrz4-evidence/model-cards/mkt6-compliance.md",
        "mrm_evidence_ref": "https://mrm.example/evidence/run-fictional-0001",
    }


def test_adapter_satisfies_evaluation_gate_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(_adapter(monkeypatch), ports.EvaluationGatePort)


@respx.mock
def test_evaluate_sends_hardened_body_and_parses_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_eval_body())
    )

    adapter = _adapter(monkeypatch)
    report = adapter.evaluate("eval/data/golden-marketing.jsonl")

    # --- request: hardened, bundle-selected, no metric list -----------------
    assert route.called
    body = json.loads(route.calls.last.request.content)

    target = body["target"]
    assert isinstance(target, dict)
    assert target["model"] == Settings().models.reasoning  # pinned reasoning model
    assert target["prompt_version"] == "v1"
    assert target["system"] == ""
    # dataset_id = basename without .jsonl; top-level MUST equal target.dataset_id.
    assert target["dataset_id"] == "golden-marketing"
    assert body["dataset_id"] == target["dataset_id"]
    # Metrics chosen ONLY by the bundle; never a metric-name list.
    assert body["bundle"] == "mkt6-compliance"
    assert "metrics" not in body
    assert "metrics" not in target

    # --- response: results[] parsed into EvalReport -------------------------
    assert isinstance(report, EvalReport)
    assert report.dataset == "eval/data/golden-marketing.jsonl"
    assert report.n_examples == 12
    assert [(r.metric, r.score, r.threshold, r.passed) for r in report.results] == [
        ("citation_accuracy", 0.995, 0.99, True),
        ("rule_coverage", 0.90, 0.95, False),
    ]
    assert report.passed is False  # one metric failed

    # --- the run evidence SURVIVES the port boundary ------------------------
    # An adapter that rebuilds a three-field domain report here demands attested
    # evidence on the wire and then discards every field of it. A verdict whose run_id,
    # digest, evaluator and artifact refs are gone is not replayable and not auditable.
    assert report.run_id == "run-fictional-0001"
    assert report.dataset_version == "golden-marketing@2026-08-01"
    assert report.dataset_digest == "sha256:feedfacefeedfacefeedfacefeedfacefeedface"
    assert report.evaluator == "hrz4-ai-quality (FICTIONAL)"
    assert report.schema_version == "eval-run/v1"
    assert report.artifact_refs == ("gs://fictional-hrz4-evidence/run-fictional-0001/report.json",)
    assert report.attested is True


@respx.mock
def test_evaluate_refuses_thin_evidence_without_run_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client fails closed: results plus n_examples alone are not evaluation evidence."""
    thin = {
        "results": [
            {"metric": "citation_accuracy", "score": 0.995, "threshold": 0.99, "passed": True}
        ],
        "n_examples": 12,
    }
    respx.post(f"{_BASE}/v1/evaluations").mock(return_value=httpx.Response(200, json=thin))
    with pytest.raises(RemoteEvaluationError):
        _adapter(monkeypatch).evaluate("eval/data/golden-marketing.jsonl")


@respx.mock
def test_gate_posts_to_v1_gate_and_returns_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.post(f"{_BASE}/v1/gate").mock(
        return_value=httpx.Response(200, json=_gate_body(eval_passed=True))
    )

    adapter = _adapter(monkeypatch)
    assert adapter.gate("eval/data/golden-marketing.jsonl") is True

    assert route.called
    assert route.calls.last.request.method == "POST"  # POST, not GET
    body = json.loads(route.calls.last.request.content)
    assert body["bundle"] == "mkt6-compliance"
    assert body["dataset_id"] == body["target"]["dataset_id"] == "golden-marketing"
    assert "metrics" not in body


@respx.mock
def test_gate_returns_false_through_consistent_failing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAIL must be reached through consistent evidence: one metric row genuinely failing."""
    respx.post(f"{_BASE}/v1/gate").mock(
        return_value=httpx.Response(200, json=_gate_body(eval_passed=False))
    )
    assert _adapter(monkeypatch).gate("eval/data/golden-marketing.jsonl") is False


@respx.mock
def test_gate_refuses_a_naked_aggregate_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unhardened ``{"passed": true}`` shape is rejected, not trusted."""
    respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json={"passed": True}))
    with pytest.raises(RemoteEvaluationError):
        _adapter(monkeypatch).gate("eval/data/golden-marketing.jsonl")


@respx.mock
def test_gate_refuses_an_unattested_report_even_when_every_metric_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unattested evidence certifies nothing, whatever its scores say."""
    body = _gate_body(eval_passed=True)
    body["eval_report"] = {**_eval_body(coverage_passed=True), "attested": False}
    respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(RemoteEvaluationError):
        _adapter(monkeypatch).gate("eval/data/golden-marketing.jsonl")


@respx.mock
def test_gate_refuses_an_aggregate_that_contradicts_its_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``passed: true`` over a failing metric row is an inconsistency, never a promotion."""
    body = _gate_body(eval_passed=False)
    body["passed"] = True  # contradicts the failing rule_coverage row
    respx.post(f"{_BASE}/v1/gate").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(RemoteEvaluationError):
        _adapter(monkeypatch).gate("eval/data/golden-marketing.jsonl")


@respx.mock
def test_non_2xx_raises_remote_evaluation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(f"{_BASE}/v1/evaluations").mock(
        return_value=httpx.Response(500, text="model-risk backend unavailable")
    )

    adapter = _adapter(monkeypatch)
    with pytest.raises(RemoteEvaluationError):
        adapter.evaluate("eval/data/golden-marketing.jsonl")
