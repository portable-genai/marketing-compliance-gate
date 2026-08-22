"""R8 routing: an escalated compliance review is routed to Hrz7 via the shared review-kit.

Mkt6 is the marketing maker-checker gate, so every regulated-claim disposition sets
``requires_human_review`` and rule R8 says it MUST be handed to the Hrz7 console rather than left
as a boolean. These tests prove the producer half of that loop end to end against the offline local
router (an in-memory outbox), prove the redact-before-wire boundary so no stray identifier reaches
the shared console, and prove the dual-control gate on the strongest finding severity. Routing is
wired ONLY on the review/maker path; the agent never approves.

All data here is fictional.
"""

from __future__ import annotations

import pytest

from marketing_compliance_gate.adapters._review_payload import review_to_kit_review
from marketing_compliance_gate.adapters.local.review_router import LocalReviewRouter
from marketing_compliance_gate.config import Container, Settings
from marketing_compliance_gate.domain.models import (
    ApprovalDecision,
    ApprovalRecord,
    AssetType,
    Citation,
    ClaimFinding,
    FindingStatus,
    Market,
    MarketingAsset,
    Review,
    ReviewOutcome,
    ReviewRequest,
    RuleKind,
    Severity,
    SourceType,
    Vertical,
)
from marketing_compliance_gate.domain.services import ReviewService

ACTOR = "compliance-officer@marketing.test"


def _service(container: Container, router: LocalReviewRouter | None) -> ReviewService:
    return ReviewService(
        rule_provider=container.rule_provider,
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=container.tracer,
        audit=container.audit,
        review_router=router,
    )


def _non_compliant_asset() -> MarketingAsset:
    # A banking creative that trips the seeded SG "guaranteed" claim rule (non-compliant).
    return MarketingAsset(
        id="a-guar-1",
        asset_type=AssetType.CREATIVE,
        title="Savings promo",
        body="Get guaranteed returns of 4.10% with zero risk-free worry!",
        market=Market.SG,
        vertical=Vertical.BANKING,
    )


def test_review_routes_escalated_review_to_outbox(local_container: Container):
    """A non-compliant review enqueues exactly one kit review to the router's outbox (R8)."""
    router = LocalReviewRouter(Settings())
    service = _service(local_container, router)
    assert not router.outbox.pending()

    review = service.review(ReviewRequest(asset=_non_compliant_asset()), actor=ACTOR)
    assert review.outcome is ReviewOutcome.NON_COMPLIANT
    assert review.requires_human_review

    pending = router.outbox.pending()
    assert len(pending) == 1, "the escalated review must be routed to Hrz7 exactly once"
    kit = pending[0].review
    assert kit.action == f"marketing_compliance_review:{review.asset_type.value}"
    assert kit.case_ref == review.id
    assert kit.maker == ACTOR
    assert kit.sod_group == "marketing-compliance-maker-checker"


def test_compliant_release_recommendation_is_routed(local_container: Container):
    """A clean result still recommends release, so maker-checker cannot be bypassed."""
    router = LocalReviewRouter(Settings())
    service = _service(local_container, router)
    asset = MarketingAsset(
        id="a-clean-1",
        asset_type=AssetType.CREATIVE,
        title="Balanced promo",
        body="Big savings this weekend on selected items.",
        market=Market.SG,
        vertical=Vertical.ONLINE_RETAIL,
        fields={"discount_pct": "40", "stock_on_hand": "120"},
        granted_consents=("marketing",),
    )
    review = service.review(ReviewRequest(asset=asset), actor=ACTOR)
    assert review.outcome is ReviewOutcome.COMPLIANT
    assert review.requires_human_review
    assert len(router.outbox.pending()) == 1


def _high_severity_review_with_pii() -> Review:
    # A synthetic identifier in a citation snippet: it must be masked before the wire.
    cite = Citation(
        source_id="SG-BANK-CLAIM-GUARANTEED",
        source_type=SourceType.RULE,
        title="Prohibited guarantee claim",
        snippet="Flagged after a report from officer S1234567D at ops@bank.test.",
    )
    finding = ClaimFinding(
        rule_id="SG-BANK-CLAIM-GUARANTEED",
        rule_kind=RuleKind.CLAIM,
        status=FindingStatus.FAIL,
        severity=Severity.CRITICAL,
        message="Prohibited guarantee claim in the body copy.",
        citations=(cite,),
    )
    review_id = "review-SG-banking-a-guar-1"
    return Review(
        id=review_id,
        asset_id="a-guar-1",
        asset_type=AssetType.CREATIVE,
        market=Market.SG,
        vertical=Vertical.BANKING,
        outcome=ReviewOutcome.NON_COMPLIANT,
        findings=(finding,),
        citations=(cite,),
        approval=ApprovalRecord(review_id=review_id, decision=ApprovalDecision.PENDING),
        requires_human_review=True,
    )


def test_payload_is_redacted_and_carries_tenant_and_dual_control():
    """The payload masks identifiers, carries the tenant, and dual-controls CRITICAL (R1/R8)."""
    kit = review_to_kit_review(_high_severity_review_with_pii(), maker=ACTOR, tenant="demo-brand")

    assert kit.tenant == "demo-brand"
    assert kit.severity == "critical"
    assert kit.required_approvals == 2, "a CRITICAL finding warrants dual control"
    # No raw identifier or email survives into the payload the shared console receives.
    assert "S1234567D" not in kit.summary
    for citation in kit.citations:
        assert "S1234567D" not in citation.snippet
        assert "ops@bank.test" not in citation.snippet
    assert any(c.title == "Prohibited guarantee claim" for c in kit.citations)


def test_no_router_still_produces_review(local_container: Container):
    """Routing is optional: with no router bound, review still returns an escalated review."""
    service = _service(local_container, None)
    review = service.review(ReviewRequest(asset=_non_compliant_asset()), actor=ACTOR)
    assert review.requires_human_review


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
