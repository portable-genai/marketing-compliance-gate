"""The deterministic green-claim coverage engine (pure, replayable, fail-closed).

These tests pin the consequential decision: which environmental claims a piece of copy
makes, and whether the evidence on file carries them. Nothing here touches an LLM, a clock,
a store or a network: the engine is given the asset, the pack and the evidence, and the same
inputs must always produce the same verdict and the same coverage number.

The fail-closed cases are the point of the suite. Every one of them is a way a real
greenwashing gate quietly stops working: evidence that expired, evidence that is a year too
old, a self-declared certificate where the jurisdiction wants an independent one, evidence
filed against a different claim category, or a category nobody configured a requirement for.
"""

from __future__ import annotations

from datetime import date

import pytest

from marketing_compliance_gate.domain.coverage_engine import CoverageEngine, parse_iso_date
from marketing_compliance_gate.domain.models import (
    AssetType,
    Citation,
    EvidenceKind,
    GreenClaim,
    GreenClaimCategory,
    GreenClaimPack,
    Market,
    MarketingAsset,
    SourceType,
    SubstantiationEvidence,
    SubstantiationRequirement,
    SubstantiationVerdict,
    Vertical,
)

AS_OF = date(2026, 8, 5)
CITATION = Citation(
    source_id="test-instrument",
    source_type=SourceType.GUIDELINE,
    title="Test instrument (FICTIONAL)",
)


def _pack(
    *,
    required: tuple[EvidenceKind, ...] = (
        EvidenceKind.EMISSIONS_INVENTORY,
        EvidenceKind.CARBON_OFFSET_RETIREMENT,
    ),
    max_age: int = 365,
    independent: bool = False,
    with_requirement: bool = True,
) -> GreenClaimPack:
    requirements = {}
    if with_requirement:
        requirements[(Market.AU, GreenClaimCategory.CARBON_NEUTRAL)] = SubstantiationRequirement(
            market=Market.AU,
            category=GreenClaimCategory.CARBON_NEUTRAL,
            required_kinds=required,
            max_evidence_age_days=max_age,
            requires_independent_verification=independent,
            remediation="Hold the evidence or drop the claim.",
            citation=CITATION,
        )
    return GreenClaimPack(
        version="test",
        phrases={
            (Market.AU, GreenClaimCategory.CARBON_NEUTRAL): ("carbon neutral", "carbon-neutral"),
            (Market.AU, GreenClaimCategory.RECYCLABLE): ("100% recyclable",),
        },
        requirements=requirements,
    )


def _asset(body: str, title: str = "Campaign") -> MarketingAsset:
    return MarketingAsset(
        id="asset-1",
        asset_type=AssetType.CAMPAIGN,
        title=title,
        body=body,
        market=Market.AU,
        vertical=Vertical.BANKING,
    )


def _evidence(
    kind: EvidenceKind,
    *,
    issued: str = "2026-06-01",
    valid_until: str = "",
    verified: bool = True,
    categories: tuple[GreenClaimCategory, ...] = (GreenClaimCategory.CARBON_NEUTRAL,),
    id_: str = "ev-1",
) -> SubstantiationEvidence:
    return SubstantiationEvidence(
        id=id_,
        tenant="demo-brand",
        asset_id="asset-1",
        kind=kind,
        title="Evidence (FICTIONAL)",
        categories=categories,
        issued_date=issued,
        valid_until=valid_until,
        independently_verified=verified,
    )


CLAIM = GreenClaim(category=GreenClaimCategory.CARBON_NEUTRAL, phrase="carbon neutral")


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def test_detects_a_claim_in_the_body_and_the_title() -> None:
    engine = CoverageEngine()
    pack = _pack()
    from_body = engine.detect_claims(_asset("We are Carbon Neutral by 2030."), pack)
    from_title = engine.detect_claims(_asset("nothing here", title="Carbon-neutral banking"), pack)
    assert [c.category for c in from_body] == [GreenClaimCategory.CARBON_NEUTRAL]
    assert from_body[0].location == "body"
    assert from_title[0].location == "title"


def test_repeated_phrasing_is_one_claim_per_category() -> None:
    claims = CoverageEngine().detect_claims(
        _asset("Carbon neutral. Truly carbon-neutral. Carbon neutral again."), _pack()
    )
    assert len(claims) == 1


def test_detection_is_deterministic_and_ordered() -> None:
    engine = CoverageEngine()
    pack = _pack()
    asset = _asset("Our 100% recyclable packaging ships carbon neutral.")
    first = engine.detect_claims(asset, pack)
    second = engine.detect_claims(asset, pack)
    assert first == second
    assert [c.category.value for c in first] == ["carbon_neutral", "recyclable"]


def test_copy_with_no_green_claim_is_not_applicable() -> None:
    claims, verdict, coverage = CoverageEngine().assess(
        _asset("4.10% per annum on deposits."), _pack(), (), AS_OF
    )
    assert claims == ()
    assert verdict is SubstantiationVerdict.NOT_APPLICABLE
    assert coverage == 1.0


# --------------------------------------------------------------------------- #
# Coverage arithmetic
# --------------------------------------------------------------------------- #
def test_all_required_evidence_present_is_substantiated() -> None:
    evidence = (
        _evidence(EvidenceKind.EMISSIONS_INVENTORY, id_="ev-1"),
        _evidence(EvidenceKind.CARBON_OFFSET_RETIREMENT, id_="ev-2"),
    )
    result = CoverageEngine().cover(CLAIM, _pack(), Market.AU, evidence, AS_OF)
    assert result.verdict is SubstantiationVerdict.SUBSTANTIATED
    assert result.coverage == 1.0
    assert result.missing_kinds == ()
    assert result.evidence_ids == ("ev-1", "ev-2")
    assert result.citations == (CITATION,)


def test_half_the_evidence_is_partially_substantiated() -> None:
    result = CoverageEngine().cover(
        CLAIM, _pack(), Market.AU, (_evidence(EvidenceKind.EMISSIONS_INVENTORY),), AS_OF
    )
    assert result.verdict is SubstantiationVerdict.PARTIALLY_SUBSTANTIATED
    assert result.coverage == 0.5
    assert result.missing_kinds == (EvidenceKind.CARBON_OFFSET_RETIREMENT,)
    assert result.remediation


def test_no_evidence_is_unsubstantiated() -> None:
    result = CoverageEngine().cover(CLAIM, _pack(), Market.AU, (), AS_OF)
    assert result.verdict is SubstantiationVerdict.UNSUBSTANTIATED
    assert result.coverage == 0.0


def test_overall_verdict_is_the_worst_claim() -> None:
    """One unsupported claim holds the whole asset, even when the other is perfect."""
    engine = CoverageEngine()
    pack = GreenClaimPack(
        phrases={
            (Market.AU, GreenClaimCategory.CARBON_NEUTRAL): ("carbon neutral",),
            (Market.AU, GreenClaimCategory.RECYCLABLE): ("100% recyclable",),
        },
        requirements={
            (Market.AU, GreenClaimCategory.CARBON_NEUTRAL): SubstantiationRequirement(
                market=Market.AU,
                category=GreenClaimCategory.CARBON_NEUTRAL,
                required_kinds=(EvidenceKind.EMISSIONS_INVENTORY,),
                citation=CITATION,
            ),
            (Market.AU, GreenClaimCategory.RECYCLABLE): SubstantiationRequirement(
                market=Market.AU,
                category=GreenClaimCategory.RECYCLABLE,
                required_kinds=(EvidenceKind.ACCREDITED_TEST_REPORT,),
                citation=CITATION,
            ),
        },
    )
    claims, verdict, coverage = engine.assess(
        _asset("Carbon neutral shipping in 100% recyclable packaging."),
        pack,
        (_evidence(EvidenceKind.EMISSIONS_INVENTORY),),
        AS_OF,
    )
    assert len(claims) == 2
    assert verdict is SubstantiationVerdict.UNSUBSTANTIATED
    assert coverage == 0.5


# --------------------------------------------------------------------------- #
# Fail-closed evidence predicates
# --------------------------------------------------------------------------- #
def test_expired_evidence_does_not_count() -> None:
    result = CoverageEngine().cover(
        CLAIM,
        _pack(required=(EvidenceKind.CARBON_OFFSET_RETIREMENT,)),
        Market.AU,
        (
            _evidence(
                EvidenceKind.CARBON_OFFSET_RETIREMENT,
                issued="2026-01-01",
                valid_until="2026-06-30",
            ),
        ),
        AS_OF,
    )
    assert result.verdict is SubstantiationVerdict.UNSUBSTANTIATED
    assert result.expired_evidence_ids == ("ev-1",)
    assert "expired" in result.gaps[0]


def test_evidence_older_than_the_jurisdiction_limit_does_not_count() -> None:
    engine = CoverageEngine()
    old = (_evidence(EvidenceKind.EMISSIONS_INVENTORY, issued="2024-01-01"),)
    strict_pack = _pack(required=(EvidenceKind.EMISSIONS_INVENTORY,), max_age=365)
    strict = engine.cover(CLAIM, strict_pack, Market.AU, old, AS_OF)
    lenient_pack = _pack(required=(EvidenceKind.EMISSIONS_INVENTORY,), max_age=0)
    lenient = engine.cover(CLAIM, lenient_pack, Market.AU, old, AS_OF)
    assert strict.verdict is SubstantiationVerdict.UNSUBSTANTIATED
    # max_evidence_age_days = 0 means "does not age out": the SAME evidence now counts, which
    # is what makes the age limit a config knob rather than an engine constant (B4).
    assert lenient.verdict is SubstantiationVerdict.SUBSTANTIATED


def test_undated_and_future_dated_evidence_does_not_count() -> None:
    engine = CoverageEngine()
    pack = _pack(required=(EvidenceKind.EMISSIONS_INVENTORY,))
    undated = engine.cover(
        CLAIM, pack, Market.AU, (_evidence(EvidenceKind.EMISSIONS_INVENTORY, issued=""),), AS_OF
    )
    future = engine.cover(
        CLAIM,
        pack,
        Market.AU,
        (_evidence(EvidenceKind.EMISSIONS_INVENTORY, issued="2027-01-01"),),
        AS_OF,
    )
    assert undated.verdict is SubstantiationVerdict.UNSUBSTANTIATED
    assert future.verdict is SubstantiationVerdict.UNSUBSTANTIATED


def test_self_declared_evidence_fails_where_independent_verification_is_required() -> None:
    engine = CoverageEngine()
    self_declared = (_evidence(EvidenceKind.EMISSIONS_INVENTORY, verified=False),)
    strict = engine.cover(
        CLAIM,
        _pack(required=(EvidenceKind.EMISSIONS_INVENTORY,), independent=True),
        Market.AU,
        self_declared,
        AS_OF,
    )
    relaxed = engine.cover(
        CLAIM,
        _pack(required=(EvidenceKind.EMISSIONS_INVENTORY,), independent=False),
        Market.AU,
        self_declared,
        AS_OF,
    )
    assert strict.verdict is SubstantiationVerdict.UNSUBSTANTIATED
    assert "self-declared" in strict.gaps[0]
    assert relaxed.verdict is SubstantiationVerdict.SUBSTANTIATED


def test_evidence_filed_under_another_category_does_not_count() -> None:
    result = CoverageEngine().cover(
        CLAIM,
        _pack(required=(EvidenceKind.EMISSIONS_INVENTORY,)),
        Market.AU,
        (
            _evidence(
                EvidenceKind.EMISSIONS_INVENTORY,
                categories=(GreenClaimCategory.RECYCLABLE,),
            ),
        ),
        AS_OF,
    )
    assert result.verdict is SubstantiationVerdict.UNSUBSTANTIATED


def test_unconfigured_category_fails_closed() -> None:
    """An unknown category is never waved through as 'nothing to check'."""
    result = CoverageEngine().cover(CLAIM, _pack(with_requirement=False), Market.AU, (), AS_OF)
    assert result.verdict is SubstantiationVerdict.UNSUBSTANTIATED
    assert result.coverage == 0.0
    assert "no substantiation requirement" in result.gaps[0]


def test_requirement_with_no_evidence_kinds_fails_closed() -> None:
    pack = GreenClaimPack(
        phrases={(Market.AU, GreenClaimCategory.CARBON_NEUTRAL): ("carbon neutral",)},
        requirements={
            (Market.AU, GreenClaimCategory.CARBON_NEUTRAL): SubstantiationRequirement(
                market=Market.AU,
                category=GreenClaimCategory.CARBON_NEUTRAL,
                required_kinds=(),
                citation=CITATION,
            )
        },
    )
    result = CoverageEngine().cover(CLAIM, pack, Market.AU, (), AS_OF)
    assert result.verdict is SubstantiationVerdict.UNSUBSTANTIATED


# --------------------------------------------------------------------------- #
# Replayability
# --------------------------------------------------------------------------- #
def test_the_same_inputs_always_give_the_same_answer() -> None:
    engine = CoverageEngine()
    pack = _pack()
    evidence = (
        _evidence(EvidenceKind.EMISSIONS_INVENTORY, id_="ev-1"),
        _evidence(EvidenceKind.CARBON_OFFSET_RETIREMENT, id_="ev-2", valid_until="2026-07-01"),
    )
    runs = [engine.assess(_asset("Carbon neutral."), pack, evidence, AS_OF) for _ in range(5)]
    assert all(run == runs[0] for run in runs)


def test_as_of_is_an_input_so_a_past_assessment_replays() -> None:
    """Ageing is a parameter, not a clock read: last quarter's answer is reproducible."""
    engine = CoverageEngine()
    pack = _pack(required=(EvidenceKind.CARBON_OFFSET_RETIREMENT,))
    evidence = (
        _evidence(
            EvidenceKind.CARBON_OFFSET_RETIREMENT, issued="2026-01-01", valid_until="2026-06-30"
        ),
    )
    before = engine.cover(CLAIM, pack, Market.AU, evidence, date(2026, 3, 1))
    after = engine.cover(CLAIM, pack, Market.AU, evidence, date(2026, 8, 5))
    assert before.verdict is SubstantiationVerdict.SUBSTANTIATED
    assert after.verdict is SubstantiationVerdict.UNSUBSTANTIATED


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2026-08-05", date(2026, 8, 5)), ("", None), ("  ", None), ("not-a-date", None)],
)
def test_parse_iso_date_is_total(value: str, expected: date | None) -> None:
    assert parse_iso_date(value) == expected
