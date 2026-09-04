#!/usr/bin/env python3
"""Offline evaluation gate for the D6 Marketing Compliance system (A4).

This is the **promotion gate**: CI runs it on every change and the build fails if the
agent's compliance reviews fall below the model-risk thresholds agreed for a marketing-
compliance agent (see ``eval/rubrics/*.yaml``)::

    rule_coverage    >= 0.95   (every applicable rule produces a finding)
    finding_accuracy >= 0.90   (the engine flags exactly the expected failing rules)
    citation_accuracy>= 0.99   (every finding cites a rule in the active rule set)
    review_safety    >= 0.99   (every regulated-claim disposition requires human review)

Two evaluators, one gate
------------------------
* **Production evaluator** — the **Gen AI evaluation service** on the Gemini Enterprise
  Agent Platform, wired in as ``EvaluationGatePort`` ->
  ``marketing_compliance_gate.adapters.gcp.genai_eval:GenAiEvalAdapter``. It needs GCP
  credentials. Select it with ``--use-gcp``.

* **Offline evaluator (default)** — a deterministic gate in this file. It needs **no GCP
  credentials and no Google Cloud SDK**, runs the real ``ReviewService`` against the local
  (offline) adapters over the golden set, and computes the four metrics. This is what
  guards the merge in CI.

Usage::

    python eval/run_eval.py                      # offline gate (CI)
    python eval/run_eval.py --dataset path.jsonl # custom golden set
    python eval/run_eval.py --use-gcp            # route through GenAiEvalAdapter

Exit code is ``0`` iff ``EvalReport.passed`` (every metric meets its threshold).
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

# Domain models / config are pure-stdlib + the local adapters are SDK-free, so this script
# runs in the local / on-prem / test profile with no Google Cloud SDK installed.
# The --mode smoke|gate scaffold + aligned report rendering come from the shared
# agent-eval-kit commons; this script keeps only its own offline
# evaluator and gate runner.
from agent_eval_kit import assert_each_can_go_red, eval_main
from pii_kit import UNIVERSAL_PATTERNS, Pattern, national_patterns_for, score_pii_safety

from marketing_compliance_gate.domain.models import (
    AssetType,
    Citation,
    ClaimCoverage,
    ClaimFinding,
    EvalMetricResult,
    EvalReport,
    FindingStatus,
    GreenClaim,
    GreenClaimCategory,
    Market,
    MarketingAsset,
    Review,
    ReviewOutcome,
    ReviewRequest,
    RuleKind,
    Severity,
    SourceType,
    SubstantiationAssessment,
    SubstantiationVerdict,
    Vertical,
)

THRESHOLDS: dict[str, float] = {
    "rule_coverage": 0.95,
    "finding_accuracy": 0.90,
    "citation_accuracy": 0.99,
    "review_safety": 0.99,
    "substantiation_accuracy": 0.99,
    # The consent and preference store. 1.0 for the safety metric: there is no acceptable
    # rate of contacting a person whose stored state refuses it. See
    # eval/rubrics/consent_fail_closed.yaml for why there are two of them.
    "consent_fail_closed": 1.0,
    "consent_decision_accuracy": 1.0,
    "consent_pii_safety": 1.0,
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_reviews.jsonl"
# The green-claim golden set is a second, fixed dataset: --dataset overrides the review set
# only, because the two golden sets answer different questions and are not interchangeable.
GREEN_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_green_claims.jsonl"
# The consent golden set is a third fixed dataset, for the same reason: it answers a
# different question (may this SUBJECT be contacted?) from the asset-review set.
CONSENT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_consent.jsonl"


# --------------------------------------------------------------------------- #
# Golden dataset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GoldenExample:
    id: str
    asset_type: str
    title: str
    body: str
    market: str
    vertical: str
    fields: dict[str, str]
    granted_consents: tuple[str, ...]
    expected_failing_rule_ids: frozenset[str]
    expected_outcome: str


def load_golden(path: Path) -> list[GoldenExample]:
    examples: list[GoldenExample] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        examples.append(
            GoldenExample(
                id=str(obj.get("id", f"example-{lineno}")),
                asset_type=str(obj.get("asset_type", "creative")),
                title=str(obj.get("title", "")),
                body=str(obj["body"]),
                market=str(obj["market"]),
                vertical=str(obj["vertical"]),
                fields={str(k): str(v) for k, v in (obj.get("fields") or {}).items()},
                granted_consents=tuple(obj.get("granted_consents", []) or ()),
                expected_failing_rule_ids=frozenset(obj.get("expected_failing_rule_ids", []) or []),
                expected_outcome=str(obj.get("expected_outcome", "non_compliant")),
            )
        )
    if not examples:
        raise SystemExit(f"{path}: golden dataset is empty")
    return examples


def load_thresholds_from_rubrics() -> dict[str, float]:
    """Read thresholds from ``eval/rubrics/*.yaml`` when PyYAML is available."""
    thresholds = dict(THRESHOLDS)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return thresholds
    rubric_dir = _REPO_ROOT / "eval" / "rubrics"
    for name in (
        "rule_coverage.yaml",
        "finding_accuracy.yaml",
        "substantiation_accuracy.yaml",
        "consent_fail_closed.yaml",
    ):
        rubric_path = rubric_dir / name
        if not rubric_path.exists():
            continue
        doc = yaml.safe_load(rubric_path.read_text(encoding="utf-8")) or {}
        metric = doc.get("metric")
        if isinstance(metric, str) and "threshold" in doc:
            thresholds[metric] = float(doc["threshold"])
        for companion, spec in (doc.get("companion_metrics") or {}).items():
            if isinstance(spec, dict) and "threshold" in spec:
                thresholds[str(companion)] = float(spec["threshold"])
    return thresholds


# --------------------------------------------------------------------------- #
# Green-claim golden dataset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GreenGoldenExample:
    """One green-claim asset, the evidence its (fictional) brand holds, and the oracle."""

    id: str
    asset_type: str
    title: str
    body: str
    market: str
    vertical: str
    fields: dict[str, str]
    as_of: str
    evidence: tuple[dict[str, Any], ...]
    expected_verdict: str
    expected_coverage: float | None


def load_green_golden(path: Path) -> list[GreenGoldenExample]:
    examples: list[GreenGoldenExample] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        examples.append(
            GreenGoldenExample(
                id=str(obj.get("id", f"green-{lineno}")),
                asset_type=str(obj.get("asset_type", "campaign")),
                title=str(obj.get("title", "")),
                body=str(obj["body"]),
                market=str(obj["market"]),
                vertical=str(obj["vertical"]),
                fields={str(k): str(v) for k, v in (obj.get("fields") or {}).items()},
                as_of=str(obj.get("as_of", "")),
                evidence=tuple(obj.get("evidence") or ()),
                expected_verdict=str(obj["expected_verdict"]),
                expected_coverage=(
                    float(obj["expected_coverage"]) if "expected_coverage" in obj else None
                ),
            )
        )
    if not examples:
        raise SystemExit(f"{path}: green-claim golden dataset is empty")
    return examples


# --------------------------------------------------------------------------- #
# Consent golden dataset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ConsentGoldenExample:
    """One invented subject's STORED consent state, the question, and the oracle.

    ``expected_outcome`` is written by hand from the stored state described in the row. It is
    never read back from the pipeline, which is what makes the two consent metrics real
    oracles rather than a re-read of the product's own verdict.
    """

    subject_id: str
    as_of: str
    purpose: str
    channel: str
    market: str
    vertical: str
    records: tuple[dict[str, Any], ...]
    preferences: tuple[dict[str, Any], ...]
    suppressions: tuple[dict[str, Any], ...]
    caps: tuple[dict[str, Any], ...]
    sends_in_window: int
    expected_outcome: str
    expected_reason: str
    planted_identifier: str


def load_consent_golden(path: Path) -> list[ConsentGoldenExample]:
    examples: list[ConsentGoldenExample] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        examples.append(
            ConsentGoldenExample(
                subject_id=str(obj.get("id", f"subject-{lineno}")),
                as_of=str(obj["as_of"]),
                purpose=str(obj.get("purpose", "marketing")),
                channel=str(obj.get("channel", "email")),
                market=str(obj.get("market", "SG")),
                vertical=str(obj.get("vertical", "banking")),
                records=tuple(obj.get("records") or ()),
                preferences=tuple(obj.get("preferences") or ()),
                suppressions=tuple(obj.get("suppressions") or ()),
                caps=tuple(obj.get("caps") or ()),
                sends_in_window=int(obj.get("sends_in_window", 0)),
                expected_outcome=str(obj["expected_outcome"]),
                expected_reason=str(obj.get("expected_reason", "")),
                planted_identifier=str(obj.get("planted_identifier", "")),
            )
        )
    if not examples:
        raise SystemExit(f"{path}: consent golden dataset is empty")
    return examples


# --------------------------------------------------------------------------- #
# Service wiring (the real services over the local offline adapters)
# --------------------------------------------------------------------------- #
def _local_settings() -> Any:
    from marketing_compliance_gate.config import LocalSettings, Settings

    base = Settings.load(str(_REPO_ROOT / "config" / "settings.yaml"))
    settings = Settings(
        project_id=base.project_id,
        region=base.region,
        profile="local",
        vertical=base.vertical,
        market=base.market,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        knowledge_base=base.knowledge_base,
        model_armor=base.model_armor,
        logging=base.logging,
        agent_engine=base.agent_engine,
        green_claims=base.green_claims,
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            evidence_path=":memory:",
            consent_path=":memory:",
        ),
        markets=base.markets,
        adapters=base.adapters,
    )
    return settings


def _make_service() -> Any:
    from marketing_compliance_gate.config import Container
    from marketing_compliance_gate.domain.services import ReviewService

    container = Container(_local_settings())
    return ReviewService(
        rule_provider=container.rule_provider,
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=container.tracer,
        audit=container.audit,
    )


EVAL_TENANT = "eval-brand"


def _make_substantiation_service(examples: list[GreenGoldenExample]) -> Any:
    """Wire the real SubstantiationService over an evidence store seeded from the dataset.

    The evidence is loaded through the ordinary local adapter (an in-memory SQLite store),
    tagged with a single evaluation tenant, so the gate under test is the real one: the same
    tenant-scoped read path, the same pack, the same engine.
    """
    from marketing_compliance_gate.config import Container
    from marketing_compliance_gate.domain.models import EvidenceKind, SubstantiationEvidence
    from marketing_compliance_gate.domain.substantiation import SubstantiationService
    from marketing_compliance_gate.green_pack import pack_for

    settings = _local_settings()
    container = Container(settings)
    records = [
        SubstantiationEvidence(
            id=str(spec["id"]),
            tenant=EVAL_TENANT,
            asset_id=example.id,
            kind=EvidenceKind(str(spec["kind"])),
            title=str(spec.get("title", "")),
            categories=tuple(GreenClaimCategory(str(c)) for c in (spec.get("categories") or ())),
            issued_date=str(spec.get("issued_date", "") or ""),
            valid_until=str(spec.get("valid_until", "") or ""),
            issuer=str(spec.get("issuer", "") or ""),
            independently_verified=bool(spec.get("independently_verified", False)),
        )
        for example in examples
        for spec in example.evidence
    ]
    container.evidence_store.seed(records)
    return SubstantiationService(
        evidence_store=container.evidence_store,
        pack=pack_for(settings),
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=container.tracer,
        audit=container.audit,
    )


def _make_consent_service(examples: list[ConsentGoldenExample]) -> tuple[Any, Any, Any]:
    """Wire the real ConsentService over a consent store seeded from the dataset.

    The stored state is loaded through the ordinary local adapter (an in-memory SQLite
    store), tagged with a single evaluation tenant, so the gate under test is the real one:
    the same tenant-scoped read, the same rule provider, the same engine the API calls.
    """
    from marketing_compliance_gate.config import Container
    from marketing_compliance_gate.domain.consent import (
        ChannelPreference,
        ConsentBasis,
        ConsentChannel,
        ConsentRecord,
        ConsentStatus,
        FrequencyCap,
        SendEvent,
        SuppressionEntry,
        SuppressionReason,
        SuppressionScope,
    )
    from marketing_compliance_gate.domain.consent_service import ConsentService

    settings = _local_settings()
    container = Container(settings)
    store = container.consent_store

    def _dt(value: Any) -> Any:
        text = str(value or "").strip()
        return datetime.fromisoformat(text) if text else None

    records = [
        ConsentRecord(
            id=str(spec["id"]),
            tenant=EVAL_TENANT,
            subject_id=example.subject_id,
            purpose=str(spec.get("purpose", "marketing")),
            status=ConsentStatus(str(spec.get("status", "unknown"))),
            basis=ConsentBasis(str(spec.get("basis", "explicit_opt_in"))),
            effective_from=_dt(spec.get("effective_from")),
            expires_at=_dt(spec.get("expires_at")),
            captured_at=_dt(spec.get("captured_at")) or datetime.fromisoformat(example.as_of),
            source=str(spec.get("source", "")),
            evidence_ref=str(spec.get("evidence_ref", "")),
        )
        for example in examples
        for spec in example.records
    ]
    preferences = [
        ChannelPreference(
            id=str(spec["id"]),
            tenant=EVAL_TENANT,
            subject_id=example.subject_id,
            channel=ConsentChannel(str(spec.get("channel", "email"))),
            opted_in=bool(spec.get("opted_in", False)),
            updated_at=_dt(spec.get("updated_at")) or datetime.fromisoformat(example.as_of),
        )
        for example in examples
        for spec in example.preferences
    ]
    suppressions = [
        SuppressionEntry(
            id=str(spec["id"]),
            tenant=EVAL_TENANT,
            subject_id=example.subject_id,
            scope=SuppressionScope(str(spec.get("scope", "all"))),
            reason=SuppressionReason(str(spec.get("reason", "subject_request"))),
            channel=(
                ConsentChannel(str(spec["channel"])) if str(spec.get("channel", "")) else None
            ),
            purpose=str(spec.get("purpose", "")),
            effective_from=_dt(spec.get("effective_from")),
            expires_at=_dt(spec.get("expires_at")),
        )
        for example in examples
        for spec in example.suppressions
    ]
    caps = [
        FrequencyCap(
            id=str(spec["id"]),
            tenant=EVAL_TENANT,
            channel=ConsentChannel(str(spec.get("channel", "email"))),
            max_messages=int(spec.get("max_messages", 0)),
            window_hours=int(spec.get("window_hours", 0)),
            purpose=str(spec.get("purpose", "")),
        )
        for example in examples
        for spec in example.caps
    ]
    store.seed(tuple(records), tuple(preferences), tuple(suppressions), tuple(caps))
    # Recorded sends are what the cap counts, so a row asking for a cap breach seeds real
    # send events inside the window rather than injecting a number past the store.
    for example in examples:
        moment = datetime.fromisoformat(example.as_of)
        for index in range(example.sends_in_window):
            store.record_send(
                SendEvent(
                    id=f"se-{example.subject_id}-{index}",
                    tenant=EVAL_TENANT,
                    subject_id=example.subject_id,
                    channel=ConsentChannel(example.channel),
                    purpose=example.purpose,
                    sent_at=moment - timedelta(hours=index + 1),
                )
            )
    router = container.review_router
    return (
        ConsentService(
            consent_store=store,
            rule_provider=container.rule_provider,
            tracer=container.tracer,
            audit=container.audit,
            review_router=router,
        ),
        container.audit,
        router,
    )


def _exercise_consent_privacy_paths(
    service: Any,
    example: ConsentGoldenExample,
    principal: Any,
    decision_id: str,
) -> None:
    """Drive every derived consent audit/review path for a planted identifier."""
    from marketing_compliance_gate.domain.consent import (
        ChannelPreference,
        ConsentBasis,
        ConsentChannel,
        ConsentRecord,
        ConsentStatus,
        SendEvent,
        SuppressionEntry,
        SuppressionReason,
        SuppressionScope,
    )

    moment = datetime.fromisoformat(example.as_of)
    stable_id = hashlib.sha256(example.subject_id.encode()).hexdigest()[:12]
    pending_id = f"privacy-record-{stable_id}"
    service.record(
        ConsentRecord(
            id=pending_id,
            tenant="ignored-client-tenant",
            subject_id=example.subject_id,
            purpose=example.purpose,
            status=ConsentStatus.GRANTED,
            basis=ConsentBasis.LEGITIMATE_INTEREST,
            captured_at=moment,
        ),
        principal,
    )
    service.confirm(pending_id, principal, approved=False, rationale="synthetic privacy probe")
    service.set_preference(
        ChannelPreference(
            id=f"privacy-preference-{stable_id}",
            tenant="ignored-client-tenant",
            subject_id=example.subject_id,
            channel=ConsentChannel(example.channel),
            opted_in=False,
            updated_at=moment,
        ),
        principal,
    )
    service.suppress(
        SuppressionEntry(
            id=f"privacy-suppression-{stable_id}",
            tenant="ignored-client-tenant",
            subject_id=example.subject_id,
            scope=SuppressionScope.ALL,
            reason=SuppressionReason.SUBJECT_REQUEST,
            effective_from=moment,
        ),
        principal,
    )
    service.note_send(
        SendEvent(
            id=f"privacy-send-{stable_id}",
            tenant="ignored-client-tenant",
            subject_id=example.subject_id,
            channel=ConsentChannel(example.channel),
            purpose=example.purpose,
            sent_at=moment,
            decision_id=decision_id,
        ),
        principal,
    )


# --------------------------------------------------------------------------- #
# Scorers
# --------------------------------------------------------------------------- #
def applicable_rule_ids(service: Any, ex: GoldenExample) -> set[str]:
    rule_set = service._rules.rule_set(Market(ex.market), Vertical(ex.vertical))
    return {r.id for r in rule_set.rules}


def score_rule_coverage(review: Review, applicable: set[str]) -> float:
    """Every rule in force should produce a finding (pass or fail)."""
    if not applicable:
        return 1.0
    seen = {f.rule_id for f in review.findings}
    return round(len(applicable & seen) / len(applicable), 4)


def score_finding_accuracy(review: Review, expected_failing: frozenset[str]) -> float:
    """The engine should flag exactly the expected failing rules (Jaccard)."""
    actual_failing = {f.rule_id for f in review.failing_findings}
    union = expected_failing | actual_failing
    if not union:
        return 1.0
    return round(len(expected_failing & actual_failing) / len(union), 4)


def score_citation_accuracy(review: Review, applicable: set[str]) -> float:
    """Every finding must cite ITS OWN rule, and that rule must be in the active set.

    Independent oracle (E2): this checks each finding cites the specific rule it claims to
    enforce (``rule_id`` present among its citations' ``source_id``s), not merely that the
    review's citations are a subset of the rules the product already loaded. The weaker
    'cited subset of applicable' form is a tautology, since the engine can only ever cite
    rules it loaded: it scores a constant 1.0 and cannot catch a finding that drops,
    fabricates or mis-attributes its citation. Here a finding with no self-citation goes red.
    """
    if not review.findings:
        return 1.0
    well_cited = sum(
        1
        for f in review.findings
        if f.rule_id in applicable and f.rule_id in {c.source_id for c in f.citations}
    )
    return round(well_cited / len(review.findings), 4)


def score_review_safety(review: Review, expected_outcome: str) -> float:
    """A marketing asset the golden set deems non-compliant MUST be caught and escalated.

    Independent oracle (E2): the safety property is not merely 'if the product itself declared
    this non-compliant it also set the review flag' (that re-reads the product's own output and
    is a constant 1.0). It is 'a real violation is never auto-passed': an asset the golden set
    marks non-compliant that the product instead clears (COMPLIANT, or without human review) is
    a silent auto-approval of a real violation and scores 0.0 here. So an under-flagging
    regression turns this metric RED even though the product set no flag for a weaker metric to
    read.
    """
    if expected_outcome == ReviewOutcome.NON_COMPLIANT.value:
        caught = review.outcome is ReviewOutcome.NON_COMPLIANT and review.requires_human_review
        return 1.0 if caught else 0.0
    # golden-compliant asset: if the product itself flags non-compliance it must still escalate.
    if review.outcome is ReviewOutcome.NON_COMPLIANT:
        return 1.0 if review.requires_human_review else 0.0
    return 1.0


def score_substantiation_accuracy(
    assessment: SubstantiationAssessment, expected_verdict: str
) -> float:
    """The green-claim verdict must match the INDEPENDENT golden verdict.

    Independent oracle (E2): the comparison is against the verdict written from the evidence
    in the dataset row, never against anything the pipeline produced. The regression this is
    here to catch is an overclaim: the product answering ``substantiated`` for an asset whose
    offset record expired, whose certificate is self-declared where the jurisdiction demands
    independent verification, or which has no evidence at all. Any such case scores 0.0, so
    a coverage engine that starts counting unusable evidence turns this metric RED even
    though the product itself reports a confident verdict.
    """
    return 1.0 if assessment.verdict.value == expected_verdict else 0.0


def score_consent_fail_closed(decision: Any, expected_outcome: str) -> float:
    """The store must NEVER allow contact the independent golden state refuses.

    Independent oracle (E2): the comparison is against the outcome written from the stored
    state described in the dataset row, never against anything the pipeline produced. The
    regression this exists to catch is the only one that reaches a real person: a withdrawal
    read as a grant, an expired record treated as live, a suppression skipped, a channel the
    subject never opted into. Any allow-when-denied scores 0.0, which takes the mean below
    the 1.0 threshold on a single example.

    Denying more than the oracle does is safe and scores 1.0 here on purpose, which is why
    :func:`score_consent_decision_accuracy` exists alongside it: an always-deny store passes
    this metric and fails that one.
    """
    if expected_outcome == "denied":
        return 0.0 if decision.allowed else 1.0
    return 1.0


def score_consent_decision_accuracy(decision: Any, expected_outcome: str) -> float:
    """The outcome must match the independent golden outcome exactly, in both directions.

    This is the anti-gaming half of the pair: it goes red on a store that under-denies AND on
    one that over-denies, so the safety metric above cannot be satisfied by a store that
    simply refuses everything.
    """
    return 1.0 if decision.outcome.value == expected_outcome else 0.0


def score_consent_pii_safety(
    surfaces: list[str],
    market: str,
    planted_identifier: str,
    *,
    patterns: list[Pattern] | None = None,
) -> float:
    """Score durable consent surfaces with pack and pack-independent leak checks.

    The selected APAC pack catches PII introduced after pseudonymisation. The literal planted
    identifier is deliberately independent of that pack, so deleting or narrowing the market
    row cannot make the metric pass vacuously.
    """
    rows = patterns
    if rows is None:
        rows = [*UNIVERSAL_PATTERNS, *national_patterns_for((market,))]
    planted = (planted_identifier,) if planted_identifier else ()
    return score_pii_safety(surfaces, rows, planted_tokens=planted)


# --------------------------------------------------------------------------- #
# Not-falsely-green guard (E2): prove the strict metrics can go RED
# --------------------------------------------------------------------------- #
def _synthetic_finding(rule_id: str, *, self_cite: bool) -> ClaimFinding:
    """A minimal FAILING finding for the harness. ``self_cite`` toggles the citation."""
    citation = Citation(
        source_id=rule_id,
        source_type=SourceType.REGULATION,
        title="Synthetic rule (FICTIONAL)",
        snippet="synthetic authority (FICTIONAL)",
    )
    return ClaimFinding(
        rule_id=rule_id,
        rule_kind=RuleKind.CLAIM,
        status=FindingStatus.FAIL,
        severity=Severity.CRITICAL,
        message="synthetic finding (FICTIONAL)",
        citations=(citation,) if self_cite else (),
    )


def _synthetic_review(
    market: Market,
    *,
    outcome: ReviewOutcome,
    requires_human_review: bool,
    findings: tuple[ClaimFinding, ...],
) -> Review:
    return Review(
        id=f"synthetic-{market.value}",
        asset_id=f"synthetic-{market.value}",
        asset_type=AssetType.CREATIVE,
        market=market,
        vertical=Vertical.BANKING,
        outcome=outcome,
        findings=findings,
        requires_human_review=requires_human_review,
    )


def _synthetic_assessment(
    market: Market, *, verdict: SubstantiationVerdict, coverage: float
) -> SubstantiationAssessment:
    claim = GreenClaim(category=GreenClaimCategory.CARBON_NEUTRAL, phrase="carbon neutral")
    return SubstantiationAssessment(
        id=f"synthetic-green-{market.value}",
        asset_id=f"synthetic-green-{market.value}",
        tenant="synthetic-tenant",
        market=market,
        vertical=Vertical.BANKING,
        as_of="2026-08-05",
        verdict=verdict,
        coverage=coverage,
        claims=(ClaimCoverage(claim=claim, verdict=verdict, coverage=coverage),),
    )


def _synthetic_decision(market: Market, *, allowed: bool) -> Any:
    """A minimal consent decision for the harness. ``allowed`` toggles the outcome."""
    from marketing_compliance_gate.domain.consent import (
        ConsentChannel,
        ConsentDecision,
        ConsentOutcome,
        ConsentReason,
    )

    outcome = ConsentOutcome.ALLOWED if allowed else ConsentOutcome.DENIED
    reason = ConsentReason.CONSENT_GRANTED if allowed else ConsentReason.CONSENT_WITHDRAWN
    return ConsentDecision(
        id=f"synthetic-consent-{market.value}",
        tenant="synthetic-tenant",
        subject_id=f"synthetic-{market.value}",
        purpose="marketing",
        channel=ConsentChannel.EMAIL,
        market=market,
        vertical=Vertical.BANKING,
        as_of=datetime(2026, 8, 8, tzinfo=UTC),
        outcome=outcome,
        reasons=(reason,),
    )


def assert_metrics_not_falsely_green(thresholds: dict[str, float]) -> None:
    """Prove the strict metrics can go RED (E2), per market, inside the gate itself.

    Each ``(green, red)`` pair feeds one metric a clean case that must PASS and a degraded case
    that must FAIL. If a degraded case still passes, the metric is falsely green and the gate
    raises here rather than shipping a safety metric that proves nothing. Run per market, per
    the harness's per-segment contract; the marketing safety metrics are market-independent in
    logic, so two markets are a representative sample rather than an exhaustive matrix.
    """
    markets = (Market.SG, Market.JP)
    rule_id = "SYNTH-RULE-001"
    other_rule_id = "SYNTH-RULE-999"

    # review_safety: an asset the golden set marks non-compliant that the product CLEARS
    # (COMPLIANT, no human review) is the under-flagging regression this metric must catch.
    review_safety_cases = {
        m.value: (
            _synthetic_review(
                m,
                outcome=ReviewOutcome.NON_COMPLIANT,
                requires_human_review=True,
                findings=(_synthetic_finding(rule_id, self_cite=True),),
            ),
            _synthetic_review(
                m,
                outcome=ReviewOutcome.COMPLIANT,
                requires_human_review=False,
                findings=(),
            ),
        )
        for m in markets
    }
    assert_each_can_go_red(
        lambda r: score_review_safety(r, ReviewOutcome.NON_COMPLIANT.value),
        review_safety_cases,
        threshold=thresholds["review_safety"],
        metric="review_safety",
    )

    # citation_accuracy: a finding that drops or mis-attributes its citation must go red.
    applicable = {rule_id}
    citation_cases = {
        m.value: (
            _synthetic_review(
                m,
                outcome=ReviewOutcome.NON_COMPLIANT,
                requires_human_review=True,
                findings=(_synthetic_finding(rule_id, self_cite=True),),
            ),
            _synthetic_review(
                m,
                outcome=ReviewOutcome.NON_COMPLIANT,
                requires_human_review=True,
                findings=(_synthetic_finding(rule_id, self_cite=False),),
            ),
        )
        for m in markets
    }
    assert_each_can_go_red(
        lambda r: score_citation_accuracy(r, applicable),
        citation_cases,
        threshold=thresholds["citation_accuracy"],
        metric="citation_accuracy",
    )

    # finding_accuracy: flagging the WRONG rule set must go red (kept for completeness).
    finding_cases = {
        m.value: (
            _synthetic_review(
                m,
                outcome=ReviewOutcome.NON_COMPLIANT,
                requires_human_review=True,
                findings=(_synthetic_finding(rule_id, self_cite=True),),
            ),
            _synthetic_review(
                m,
                outcome=ReviewOutcome.NON_COMPLIANT,
                requires_human_review=True,
                findings=(_synthetic_finding(other_rule_id, self_cite=True),),
            ),
        )
        for m in markets
    }
    assert_each_can_go_red(
        lambda r: score_finding_accuracy(r, frozenset({rule_id})),
        finding_cases,
        threshold=thresholds["finding_accuracy"],
        metric="finding_accuracy",
    )

    # substantiation_accuracy: the greenwashing regression is an OVERCLAIM. The red case is
    # the product declaring a claim substantiated where the golden evidence does not carry it.
    substantiation_cases = {
        m.value: (
            _synthetic_assessment(
                m, verdict=SubstantiationVerdict.PARTIALLY_SUBSTANTIATED, coverage=0.5
            ),
            _synthetic_assessment(m, verdict=SubstantiationVerdict.SUBSTANTIATED, coverage=1.0),
        )
        for m in markets
    }
    assert_each_can_go_red(
        lambda a: score_substantiation_accuracy(
            a, SubstantiationVerdict.PARTIALLY_SUBSTANTIATED.value
        ),
        substantiation_cases,
        threshold=thresholds["substantiation_accuracy"],
        metric="substantiation_accuracy",
    )

    # consent_fail_closed: the regression that reaches a real person is an ALLOW where the
    # stored state refuses. The red case is exactly that, against a golden "denied" row.
    consent_cases = {
        m.value: (_synthetic_decision(m, allowed=False), _synthetic_decision(m, allowed=True))
        for m in markets
    }
    assert_each_can_go_red(
        lambda d: score_consent_fail_closed(d, "denied"),
        consent_cases,
        threshold=thresholds["consent_fail_closed"],
        metric="consent_fail_closed",
    )

    # consent_decision_accuracy: the anti-gaming half. Here the golden row is "allowed", so
    # the DENIED decision is the red case: a store that refuses everything fails this one.
    accuracy_cases = {
        m.value: (_synthetic_decision(m, allowed=True), _synthetic_decision(m, allowed=False))
        for m in markets
    }
    assert_each_can_go_red(
        lambda d: score_consent_decision_accuracy(d, "allowed"),
        accuracy_cases,
        threshold=thresholds["consent_decision_accuracy"],
        metric="consent_decision_accuracy",
    )

    # consent_pii_safety: prove every supported market has a real identifier row and a literal
    # oracle that still fails when that row is removed. The red surface represents a raw subject
    # identifier reaching a durable audit sink; the green surface is the pseudonymised shape.
    planted = {
        Market.SG: "S1234567D",
        Market.JP: "1234 5678 9018",
        Market.AU: "123 456 782",
    }
    privacy_cases = {
        market.value: (
            [f"subject_ref=subject-sha256:{'0' * 64}"],
            [f"subject_id={identifier}"],
        )
        for market, identifier in planted.items()
    }
    for market, identifier in planted.items():
        assert_each_can_go_red(
            lambda surfaces, m=market.value, token=identifier: score_consent_pii_safety(
                surfaces, m, token
            ),
            {market.value: privacy_cases[market.value]},
            threshold=thresholds["consent_pii_safety"],
            metric=f"consent_pii_safety_{market.value.lower()}",
        )


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
@dataclass
class _PerMetric:
    scores: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0


def run_offline(dataset: Path, thresholds: dict[str, float]) -> EvalReport:
    # E2: prove the strict safety metrics can go red BEFORE trusting a green score over the
    # golden set. A tautological metric (re-reading the product's own output) would fail here.
    assert_metrics_not_falsely_green(thresholds)
    examples = load_golden(dataset)
    service = _make_service()
    agg: dict[str, _PerMetric] = {m: _PerMetric() for m in THRESHOLDS}
    print(f"Running offline eval gate over {len(examples)} golden reviews (ReviewService).\n")
    for ex in examples:
        asset = MarketingAsset(
            id=ex.id,
            asset_type=AssetType(ex.asset_type),
            title=ex.title or ex.id,
            body=ex.body,
            market=Market(ex.market),
            vertical=Vertical(ex.vertical),
            fields=dict(ex.fields),
            granted_consents=ex.granted_consents,
        )
        review = service.review(ReviewRequest(asset=asset), actor="eval-bot")
        applicable = applicable_rule_ids(service, ex)
        agg["rule_coverage"].scores.append(score_rule_coverage(review, applicable))
        agg["finding_accuracy"].scores.append(
            score_finding_accuracy(review, ex.expected_failing_rule_ids)
        )
        agg["citation_accuracy"].scores.append(score_citation_accuracy(review, applicable))
        agg["review_safety"].scores.append(score_review_safety(review, ex.expected_outcome))

    green_examples = score_green_claims(agg)
    consent_examples = score_consent(agg)

    order = (
        "rule_coverage",
        "finding_accuracy",
        "citation_accuracy",
        "review_safety",
        "substantiation_accuracy",
        "consent_fail_closed",
        "consent_decision_accuracy",
        "consent_pii_safety",
    )
    results = tuple(
        EvalMetricResult(
            metric=metric,
            score=round(agg[metric].mean, 4),
            threshold=thresholds.get(metric, THRESHOLDS[metric]),
            passed=round(agg[metric].mean, 4) >= thresholds.get(metric, THRESHOLDS[metric]),
        )
        for metric in order
    )
    return EvalReport(
        dataset=f"{dataset} + {GREEN_DATASET} + {CONSENT_DATASET}",
        results=results,
        n_examples=len(examples) + green_examples + consent_examples,
    )


def score_green_claims(agg: dict[str, _PerMetric]) -> int:
    """Run the real green-claims gate over its golden set and score it. Returns the count.

    Uses the same SubstantiationService the API and CLI use, over an evidence store seeded
    from the dataset, so the number scored here is the number the product would return.
    """
    from marketing_compliance_gate.domain.identity import Principal

    examples = load_green_golden(GREEN_DATASET)
    service = _make_substantiation_service(examples)
    principal = Principal(subject="eval-bot", tenant=EVAL_TENANT, source="eval")
    print(f"Running the green-claims gate over {len(examples)} golden assets.\n")
    for ex in examples:
        asset = MarketingAsset(
            id=ex.id,
            asset_type=AssetType(ex.asset_type),
            title=ex.title or ex.id,
            body=ex.body,
            market=Market(ex.market),
            vertical=Vertical(ex.vertical),
            fields=dict(ex.fields),
        )
        assessment = service.assess(asset, principal, as_of=date.fromisoformat(ex.as_of))
        agg["substantiation_accuracy"].scores.append(
            score_substantiation_accuracy(assessment, ex.expected_verdict)
        )
    return len(examples)


def score_consent(agg: dict[str, _PerMetric]) -> int:
    """Run the real consent and preference store over its golden set. Returns the count.

    Uses the same ConsentService the API uses, over a consent store seeded from the dataset,
    so the outcome scored here is the outcome the product would return for that stored state.
    """
    from marketing_compliance_gate.domain.consent import ConsentChannel
    from marketing_compliance_gate.domain.identity import Principal

    examples = load_consent_golden(CONSENT_DATASET)
    service, audit, router = _make_consent_service(examples)
    principal = Principal(subject="eval-bot", tenant=EVAL_TENANT, source="eval")
    print(f"Running the consent and preference store over {len(examples)} golden subjects.\n")
    for ex in examples:
        audit_offset = len(audit.read_all())
        outbox_offset = len(router.outbox.pending())
        decision = service.decide(
            ex.subject_id,
            ex.purpose,
            ConsentChannel(ex.channel),
            principal,
            market=Market(ex.market),
            vertical=Vertical(ex.vertical),
            as_of=datetime.fromisoformat(ex.as_of),
        )
        if ex.planted_identifier:
            _exercise_consent_privacy_paths(service, ex, principal, decision.id)
        agg["consent_fail_closed"].scores.append(
            score_consent_fail_closed(decision, ex.expected_outcome)
        )
        agg["consent_decision_accuracy"].scores.append(
            score_consent_decision_accuracy(decision, ex.expected_outcome)
        )
        derived = [
            json.dumps(event, sort_keys=True, default=str)
            for event in audit.read_all()[audit_offset:]
        ]
        derived.extend(
            json.dumps(entry, sort_keys=True, default=str)
            for entry in router.outbox.pending()[outbox_offset:]
        )
        agg["consent_pii_safety"].scores.append(
            score_consent_pii_safety(derived, ex.market, ex.planted_identifier)
        )
    return len(examples)


def run_gate(dataset: Path) -> tuple[EvalReport, bool]:
    """Promotion verdict via EvaluationGatePort (platform = model-quality-gate, gcp = Gen AI evals).

    Fails closed on the reconciled evaluate + gate result. Refuses to run outside the
    platform/gcp profiles so the offline smoke result is never relabelled a promotion pass.
    """
    from marketing_compliance_gate.config import Settings, build_container

    settings = Settings.load()
    if settings.profile not in ("platform", "gcp"):
        raise SystemExit(
            "--mode gate is the promotion authority and requires "
            "MKT_GOV_PROFILE=platform or gcp "
            f"(got {settings.profile!r}); run --mode smoke for the offline pre-merge check."
        )
    container = build_container(settings)
    gate = container.evaluation
    report = gate.evaluate(str(dataset))
    if not isinstance(report, EvalReport):  # pragma: no cover - defensive
        raise SystemExit("EvaluationGatePort.evaluate did not return an EvalReport")
    gate_passed = bool(gate.gate(str(dataset)))
    return report, gate_passed


def main(argv: list[str] | None = None) -> int:
    """Dispatch --mode via the shared eval_main scaffold (fail-closed exit codes).

    ``--use-gcp`` (the pre-split flag for the production evaluator) is kept as an alias
    for ``--mode gate``.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if "--use-gcp" in args:
        args = [a for a in args if a != "--use-gcp"] + ["--mode", "gate"]
    return eval_main(
        smoke=lambda dataset: run_offline(dataset, load_thresholds_from_rubrics()),
        gate=run_gate,
        default_dataset=DEFAULT_DATASET,
        description="Offline / platform evaluation gate for D6 (A4 / P-08).",
        smoke_label="offline heuristic (no GCP creds)",
        gate_label="promotion gate (EvaluationGatePort: model-quality-gate / Gen AI evals)",
        argv=args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
