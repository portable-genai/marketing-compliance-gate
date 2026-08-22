"""Shared conversion from an escalated compliance review to an ``review-kit`` payload.

Lives in the adapter layer (not the pure domain) because it depends on the kit. Mkt6's canonical
consent authority does hold subject identifiers, so the shared Hrz7 sink is covered by the same
versioned SG, JP and AU ``pii-kit`` as the offline privacy gate. Descriptor, summary and citation
snippets are scrubbed before they leave the process, and Hrz7 redacts again before its own audit
write. The maker and tenant are asserted here and trusted by Hrz7 because this is an authenticated
S2S caller (per-hop OBO is the deferred next layer).
"""

from __future__ import annotations

from pii_kit import UNIVERSAL_PATTERNS, national_patterns_for, redact
from review_kit import Citation as KitCitation
from review_kit import Review as KitReview

from ..domain.consent import ConsentRecord
from ..domain.models import Review, Severity, SubstantiationAssessment, SubstantiationVerdict

# Cap the citations carried on the wire: enough to let a checker trace the review without copying
# the entire evidence set into the review console.
_MAX_CITATIONS = 8

# Dual control for the strongest finding severities; a HIGH/CRITICAL failing finding warranting
# four-eyes is the conservative default (checker count is policy, not code).
_APPROVALS_BY_SEVERITY: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 2,
}

_PII_PATTERNS = (*UNIVERSAL_PATTERNS, *national_patterns_for(("SG", "JP", "AU")))


def _redact(text: str) -> str:
    """Mask email / phone / long identifier runs, then collapse whitespace, before the wire."""
    return " ".join(redact(text, _PII_PATTERNS).split())


def _severity(review: Review) -> Severity:
    """The review's strongest failing-finding severity, or LOW when none is failing."""
    return review.highest_severity or Severity.LOW


def consent_grant_to_kit_review(
    record: ConsentRecord, *, reason: str, maker: str, tenant: str = ""
) -> KitReview:
    """Build the kit review for a consent GRANT nobody could evidence at capture time.

    Recording a grant is the one consent write that WIDENS what may be done to a person, so a
    grant asserted on a subject's behalf with no captured proof is stored
    ``PENDING_REVIEW`` (it grants nothing) and handed to a checker here. Dual control is the
    floor: manufacturing consent is exactly the act four eyes exist for. The subject id is a
    synthetic key rather than a name, and it still goes through the same redaction as every
    other payload leaving this process for the shared console.
    """
    descriptor = (
        f"Consent grant pending confirmation: subject {record.subject_id}, "
        f"purpose {record.purpose}, basis {record.basis.value}"
    )
    summary = f"status={record.status.value}; {reason}"
    return KitReview(
        action="consent_grant_confirmation",
        subject=_redact(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_redact(summary),
        severity=Severity.HIGH.value,
        required_approvals=_APPROVALS_BY_SEVERITY[Severity.HIGH],
        sod_group="marketing-compliance-maker-checker",
        case_ref=record.id,
    )


def _kit_citations(item: Review | SubstantiationAssessment) -> tuple[KitCitation, ...]:
    seen: set[str] = set()
    out: list[KitCitation] = []
    for c in item.citations:
        if c.source_id in seen:
            continue
        seen.add(c.source_id)
        out.append(KitCitation(source_id=c.source_id, title=c.title, snippet=_redact(c.snippet)))
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def review_to_kit_review(review: Review, *, maker: str, tenant: str = "") -> KitReview:
    """Build the kit review a producer submits to Hrz7 when a compliance review escalates."""
    descriptor = (
        f"Marketing compliance review of {review.asset_type.value} asset {review.asset_id} "
        f"(market={review.market.value}, vertical={review.vertical.value})"
    )
    failing = review.failing_findings
    summary = (
        f"outcome={review.outcome.value}; findings={len(review.findings)}; "
        f"failing={len(failing)}; consent_checks={len(review.consent_checks)}"
    )
    severity = _severity(review)
    return KitReview(
        action=f"marketing_compliance_review:{review.asset_type.value}",
        subject=_redact(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_redact(summary),
        severity=severity.value,
        required_approvals=_APPROVALS_BY_SEVERITY.get(severity, 1),
        sod_group="marketing-compliance-maker-checker",
        case_ref=review.id,
        citations=_kit_citations(review),
    )


# A green-claim escalation is at least HIGH: the exposure from an unsupported environmental
# claim is a regulator matter, so it gets four eyes even when no rule finding failed.
_ASSESSMENT_FLOOR: Severity = Severity.HIGH


def assessment_to_kit_review(
    assessment: SubstantiationAssessment, *, maker: str, tenant: str = ""
) -> KitReview:
    """Build the kit review a producer submits to Hrz7 when a green claim needs sign-off."""
    descriptor = (
        f"Green-claim substantiation of asset {assessment.asset_id} "
        f"(market={assessment.market.value}, vertical={assessment.vertical.value}, "
        f"as_of={assessment.as_of})"
    )
    unsupported = assessment.unsupported_claims
    summary = (
        f"verdict={assessment.verdict.value}; coverage={assessment.coverage}; "
        f"claims={len(assessment.claims)}; unsupported={len(unsupported)}; "
        f"failing_rules={len(assessment.failing_findings)}"
    )
    severity = assessment.highest_severity or _ASSESSMENT_FLOOR
    if assessment.verdict is SubstantiationVerdict.UNSUBSTANTIATED:
        severity = Severity.CRITICAL
    return KitReview(
        action="green_claim_substantiation",
        subject=_redact(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_redact(summary),
        severity=severity.value,
        required_approvals=_APPROVALS_BY_SEVERITY.get(severity, 1),
        sod_group="marketing-compliance-maker-checker",
        case_ref=assessment.id,
        citations=_kit_citations(assessment),
    )
