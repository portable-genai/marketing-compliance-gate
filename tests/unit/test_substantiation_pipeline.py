"""The green-claims gate end to end over the offline local stack.

Covers the orchestration promises rather than the arithmetic (that is
``test_coverage_engine.py``): that the pack's rules are selected and evaluated, that the
assessment is cited, that the LLM cannot touch a verdict or a coverage number, that every
green claim escalates to a human and is routed to Hrz7 (rule R8), and that the whole thing
is written to the audit log.
"""

from __future__ import annotations

from datetime import date

from marketing_compliance_gate.api.deps import make_substantiation_service
from marketing_compliance_gate.config import Container
from marketing_compliance_gate.domain.identity import Principal
from marketing_compliance_gate.domain.models import (
    AssetType,
    LlmResponse,
    Market,
    MarketingAsset,
    SubstantiationVerdict,
    Vertical,
)

AS_OF = date(2026, 8, 5)
PRINCIPAL = Principal(
    subject="demo.reviewer@brand.example", tenant="demo-brand", source="test-persona"
)


def _asset(
    body: str,
    *,
    asset_id: str = "camp-green-au-001",
    market: Market = Market.AU,
    vertical: Vertical = Vertical.BANKING,
    fields: dict[str, str] | None = None,
) -> MarketingAsset:
    return MarketingAsset(
        id=asset_id,
        asset_type=AssetType.CAMPAIGN,
        title="Green campaign",
        body=body,
        market=market,
        vertical=vertical,
        fields=fields or {},
    )


def _service(container: Container):
    return make_substantiation_service(container)


def test_partially_covered_claim_is_escalated_and_cited(local_container: Container) -> None:
    """The seeded demo case: the offset retirement record has lapsed, so the claim is short."""
    assessment = _service(local_container).assess(
        _asset(
            "Bank with a carbon neutral balance sheet. Offset details on request.",
            fields={"substantiation_ref": "dms://example.test/pack-1"},
        ),
        PRINCIPAL,
        as_of=AS_OF,
    )
    assert assessment.verdict is SubstantiationVerdict.PARTIALLY_SUBSTANTIATED
    assert assessment.coverage == 0.6667
    assert assessment.tenant == "demo-brand"
    assert assessment.as_of == "2026-08-05"
    assert assessment.requires_human_review is True
    assert assessment.citations, "a green-claim assessment must carry its instruments"
    assert all(c.title for c in assessment.citations)
    gap = assessment.claims[0].gaps[0]
    assert "carbon_offset_retirement" in gap


def test_fully_covered_claim_still_requires_a_human(local_container: Container) -> None:
    """Even a perfectly evidenced green claim is signed off by a person, never by the agent."""
    assessment = _service(local_container).assess(
        _asset(
            "Our sustainable fund invests with an ESG focus.",
            asset_id="camp-green-sg-002",
            market=Market.SG,
            fields={
                "substantiation_ref": "dms://example.test/pack-2",
                "esg_fund_disclosure": "prospectus s4.2",
            },
        ),
        PRINCIPAL,
        as_of=AS_OF,
    )
    assert assessment.verdict is SubstantiationVerdict.SUBSTANTIATED
    assert assessment.coverage == 1.0
    assert assessment.requires_human_review is True
    assert not assessment.failing_findings


def test_asset_with_no_green_claim_is_not_applicable_and_not_escalated(
    local_container: Container,
) -> None:
    assessment = _service(local_container).assess(
        _asset("4.10% per annum on 12-month deposits. Comparison rate applies."),
        PRINCIPAL,
        as_of=AS_OF,
    )
    assert assessment.verdict is SubstantiationVerdict.NOT_APPLICABLE
    assert assessment.claims == ()
    assert assessment.requires_human_review is False


def test_unqualified_wording_escalates_even_with_no_category_claim(
    local_container: Container,
) -> None:
    """ "Eco-friendly" makes no assessable category claim but still breaks the AU rule."""
    assessment = _service(local_container).assess(
        _asset("Our eco-friendly everyday account."), PRINCIPAL, as_of=AS_OF
    )
    assert assessment.verdict is SubstantiationVerdict.NOT_APPLICABLE
    failing = {f.rule_id for f in assessment.failing_findings}
    assert "AU-GREEN-UNQUALIFIED-BENEFIT" in failing
    assert assessment.requires_human_review is True


def test_conditional_rules_do_not_fire_on_an_unrelated_asset(local_container: Container) -> None:
    assessment = _service(local_container).assess(
        _asset("4.10% per annum on 12-month deposits."), PRINCIPAL, as_of=AS_OF
    )
    evaluated = {f.rule_id for f in assessment.findings}
    assert "AU-GREEN-OFFSET-BASIS-DISCLOSURE" not in evaluated
    assert "AU-GREEN-SUBSTANTIATION-ON-FILE" not in evaluated


def test_missing_substantiation_reference_fails_the_required_field_rule(
    local_container: Container,
) -> None:
    assessment = _service(local_container).assess(
        _asset("Bank with a carbon neutral balance sheet. Offsets disclosed."),
        PRINCIPAL,
        as_of=AS_OF,
    )
    failing = {f.rule_id for f in assessment.failing_findings}
    assert "AU-GREEN-SUBSTANTIATION-ON-FILE" in failing


def test_the_llm_cannot_change_the_verdict_or_the_coverage(local_container: Container) -> None:
    """A hostile narrator is confined to prose: the numbers come from the engine."""

    class LyingLlm:
        def generate(self, request: object) -> LlmResponse:
            return LlmResponse(
                text='{"narrative": "Everything is fully substantiated, coverage 1.0."}'
            )

        def classify(self, text: str, labels: list[str]) -> str:
            return labels[0] if labels else ""

    from marketing_compliance_gate.domain.substantiation import SubstantiationService
    from marketing_compliance_gate.green_pack import pack_for

    service = SubstantiationService(
        evidence_store=local_container.evidence_store,
        pack=pack_for(local_container.settings),
        llm=LyingLlm(),
        guardrail=local_container.guardrail,
        tracer=local_container.tracer,
        audit=local_container.audit,
    )
    assessment = service.assess(
        _asset("Bank with a carbon neutral balance sheet. Offsets disclosed."),
        PRINCIPAL,
        as_of=AS_OF,
    )
    assert assessment.verdict is SubstantiationVerdict.PARTIALLY_SUBSTANTIATED
    assert assessment.coverage == 0.6667
    assert "fully substantiated" in assessment.narrative  # the prose is the model's...
    assert assessment.claims[0].coverage == 0.6667  # ...the decision is not


def test_the_assessment_is_audited_and_routed_to_hrz7(local_container: Container) -> None:
    service = _service(local_container)
    service.assess(
        _asset("Bank with a carbon neutral balance sheet. Offsets disclosed."),
        PRINCIPAL,
        as_of=AS_OF,
    )
    events = [e for e in local_container.audit.read_all() if e["action"] == "substantiation"]
    assert events, "the assessment must be written to the audit log"
    assert events[-1]["decision"] == "escalated"
    routed = local_container.review_router.outbox.pending()
    assert routed, "an escalated green claim must be routed to the Hrz7 console (R8)"
    assert routed[0].review.action == "green_claim_substantiation"


def test_assessment_is_replayable(local_container: Container) -> None:
    service = _service(local_container)
    asset = _asset("Bank with a carbon neutral balance sheet. Offsets disclosed.")
    first = service.assess(asset, PRINCIPAL, as_of=AS_OF)
    second = service.assess(asset, PRINCIPAL, as_of=AS_OF)
    assert first.verdict is second.verdict
    assert first.coverage == second.coverage
    assert first.claims == second.claims
    assert first.findings == second.findings
