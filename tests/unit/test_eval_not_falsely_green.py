"""E2: the strict eval metrics must be able to go RED (not falsely green).

The marketing-compliance gate is the marketing maker-checker of record, so its safety metrics
must be able to catch a real regression rather than re-read the product's own output. This test
loads ``eval/run_eval.py`` directly (``eval/`` is a script dir, not a package) and exercises the
scorers plus the in-gate ``assert_metrics_not_falsely_green`` harness, including a proof that a
tautological metric WOULD be rejected.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from agent_eval_kit import NotFalselyGreenError, assert_each_can_go_red
from pii_kit import UNIVERSAL_PATTERNS, national_patterns_for, pack_leak

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_run_eval():
    spec = importlib.util.spec_from_file_location("run_eval", _REPO_ROOT / "eval" / "run_eval.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec: run_eval defines dataclasses, whose introspection looks the module
    # up in sys.modules by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_eval = _load_run_eval()


def test_gate_runs_the_not_falsely_green_harness():
    """The exact guard the gate runs passes at the real thresholds."""
    run_eval.assert_metrics_not_falsely_green(run_eval.THRESHOLDS)


@pytest.mark.parametrize(
    ("market", "planted"),
    (("SG", "S1234567D"), ("JP", "1234 5678 9018"), ("AU", "123 456 782")),
)
def test_consent_privacy_oracle_stays_red_when_the_market_pack_row_is_removed(
    market: str, planted: str
) -> None:
    """The literal oracle catches the defect that the shared-pack scan cannot see."""
    healthy = [*UNIVERSAL_PATTERNS, *national_patterns_for((market,))]
    raw_surface = [f"subject_id={planted}"]
    assert pack_leak(raw_surface[0], healthy)
    assert run_eval.score_consent_pii_safety(raw_surface, market, planted) == 0.0

    # Simulate deleting/narrowing the jurisdiction row: the pack-dependent leg is blind, but
    # the literal planted identifier remains an independent oracle and keeps the gate red.
    broken = list(UNIVERSAL_PATTERNS)
    assert not pack_leak(raw_surface[0], broken)
    assert run_eval.score_consent_pii_safety(raw_surface, market, planted, patterns=broken) == 0.0


@pytest.mark.parametrize("market", ("SG", "JP", "AU"))
def test_consent_privacy_metric_accepts_only_pseudonymised_audit_surface(market: str) -> None:
    planted = {"SG": "S1234567D", "JP": "1234 5678 9018", "AU": "123 456 782"}[market]
    safe = [f"subject_ref=subject-sha256:{'0' * 64}"]
    assert run_eval.score_consent_pii_safety(safe, market, planted) == 1.0


@pytest.mark.parametrize("market", ("SG", "JP", "AU"))
def test_consent_privacy_gate_drives_every_derived_sink(market: str) -> None:
    from marketing_compliance_gate.domain.consent import ConsentChannel
    from marketing_compliance_gate.domain.identity import Principal

    examples = run_eval.load_consent_golden(run_eval.CONSENT_DATASET)
    example = next(item for item in examples if item.market == market and item.planted_identifier)
    service, audit, router = run_eval._make_consent_service(examples)
    principal = Principal(subject="eval-bot", tenant=run_eval.EVAL_TENANT, source="eval")
    decision = service.decide(
        example.subject_id,
        example.purpose,
        ConsentChannel(example.channel),
        principal,
        market=run_eval.Market(example.market),
        vertical=run_eval.Vertical(example.vertical),
        as_of=datetime.fromisoformat(example.as_of),
    )
    run_eval._exercise_consent_privacy_paths(service, example, principal, decision.id)
    events = audit.read_all()
    actions = {event["action"] for event in events}
    assert {
        "consent_decision",
        "consent_record",
        "consent_confirm",
        "consent_preference",
        "consent_suppression",
        "consent_send",
    } <= actions
    surfaces = [json.dumps(event, sort_keys=True, default=str) for event in events]
    routed = [json.dumps(entry, sort_keys=True, default=str) for entry in router.outbox.pending()]
    assert routed, (
        "the unevidenced grant must exercise the outbound human-review-console review payload"
    )
    assert example.planted_identifier not in "\n".join([*surfaces, *routed])
    assert (
        run_eval.score_consent_pii_safety(
            [*surfaces, *routed], example.market, example.planted_identifier
        )
        == 1.0
    )


def test_review_safety_goes_red_on_under_flagging():
    """An asset the golden set marks non-compliant that the product CLEARS scores 0."""
    cleared = run_eval._synthetic_review(
        run_eval.Market.SG,
        outcome=run_eval.ReviewOutcome.COMPLIANT,
        requires_human_review=False,
        findings=(),
    )
    assert run_eval.score_review_safety(cleared, "non_compliant") == 0.0
    # The clean case still passes.
    caught = run_eval._synthetic_review(
        run_eval.Market.SG,
        outcome=run_eval.ReviewOutcome.NON_COMPLIANT,
        requires_human_review=True,
        findings=(run_eval._synthetic_finding("SYNTH-RULE-001", self_cite=True),),
    )
    assert run_eval.score_review_safety(caught, "non_compliant") == 1.0


def test_citation_accuracy_goes_red_on_missing_citation():
    """A failing finding that drops its self-citation scores below the bar."""
    finding = run_eval._synthetic_finding("SYNTH-RULE-001", self_cite=False)
    review = run_eval._synthetic_review(
        run_eval.Market.SG,
        outcome=run_eval.ReviewOutcome.NON_COMPLIANT,
        requires_human_review=True,
        findings=(finding,),
    )
    assert run_eval.score_citation_accuracy(review, {"SYNTH-RULE-001"}) < 0.99


def test_tautological_review_safety_would_be_caught():
    """The OLD tautological metric (reads only the product's own flag) is rejected by the harness.

    This is the regression guard: it re-introduces the pre-fix metric and proves the harness
    the gate runs would fail on it, so a future refactor back to a self-referential metric
    cannot pass CI unnoticed.
    """

    def tautological(review: object) -> float:
        r = review
        if r.outcome is run_eval.ReviewOutcome.NON_COMPLIANT:  # type: ignore[attr-defined]
            return 1.0 if r.requires_human_review else 0.0  # type: ignore[attr-defined]
        return 1.0

    green = run_eval._synthetic_review(
        run_eval.Market.SG,
        outcome=run_eval.ReviewOutcome.NON_COMPLIANT,
        requires_human_review=True,
        findings=(run_eval._synthetic_finding("SYNTH-RULE-001", self_cite=True),),
    )
    # The under-flagging regression: a real violation cleared as compliant.
    red = run_eval._synthetic_review(
        run_eval.Market.SG,
        outcome=run_eval.ReviewOutcome.COMPLIANT,
        requires_human_review=False,
        findings=(),
    )
    with pytest.raises(NotFalselyGreenError):
        assert_each_can_go_red(
            tautological, {"sg": (green, red)}, threshold=0.99, metric="tautological"
        )


def test_substantiation_accuracy_goes_red_on_an_overclaim():
    """The greenwashing regression: a claim the golden set says is short, reported carried."""
    overclaimed = run_eval._synthetic_assessment(
        run_eval.Market.AU,
        verdict=run_eval.SubstantiationVerdict.SUBSTANTIATED,
        coverage=1.0,
    )
    honest = run_eval._synthetic_assessment(
        run_eval.Market.AU,
        verdict=run_eval.SubstantiationVerdict.PARTIALLY_SUBSTANTIATED,
        coverage=0.5,
    )
    expected = run_eval.SubstantiationVerdict.PARTIALLY_SUBSTANTIATED.value
    assert run_eval.score_substantiation_accuracy(overclaimed, expected) == 0.0
    assert run_eval.score_substantiation_accuracy(honest, expected) == 1.0


def test_tautological_substantiation_metric_would_be_caught():
    """A metric that reads the assessment's OWN coverage instead of the golden verdict.

    It is always green (the product agrees with itself), so the harness the gate runs must
    reject it. This stops a future refactor from quietly turning the green-claims metric into
    a mirror.
    """

    def tautological(assessment: object) -> float:
        return 1.0 if assessment.coverage >= 0.0 else 0.0  # type: ignore[attr-defined]

    green = run_eval._synthetic_assessment(
        run_eval.Market.AU,
        verdict=run_eval.SubstantiationVerdict.PARTIALLY_SUBSTANTIATED,
        coverage=0.5,
    )
    red = run_eval._synthetic_assessment(
        run_eval.Market.AU,
        verdict=run_eval.SubstantiationVerdict.SUBSTANTIATED,
        coverage=1.0,
    )
    with pytest.raises(NotFalselyGreenError):
        assert_each_can_go_red(
            tautological, {"au": (green, red)}, threshold=0.99, metric="tautological-green"
        )
