"""Local ReviewRouterPort: enqueue the routed review to an in-memory outbox (no live Hrz7).

Exercises the R8 routing path offline: an escalated compliance review is converted to a kit review
and enqueued (the same transactional outbox the platform adapter flushes to Hrz7), so tests and the
offline demo can assert that an escalation is routed without a running console.
"""

from __future__ import annotations

from review_kit import InMemoryOutbox

from ...config import Settings
from ...domain.consent import ConsentRecord
from ...domain.models import Review, SubstantiationAssessment
from .._review_payload import (
    assessment_to_kit_review,
    consent_grant_to_kit_review,
    review_to_kit_review,
)


class LocalReviewRouter:
    """Record routed reviews in an in-memory outbox for the SDK-free ``local`` profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._outbox = InMemoryOutbox()

    def route(self, review: Review, *, maker: str, tenant: str = "") -> None:
        self._outbox.enqueue(
            review_to_kit_review(review, maker=maker, tenant=tenant),
            actor="compliance-governance",
        )

    def route_assessment(
        self, assessment: SubstantiationAssessment, *, maker: str, tenant: str = ""
    ) -> None:
        self._outbox.enqueue(
            assessment_to_kit_review(assessment, maker=maker, tenant=tenant),
            actor="compliance-governance",
        )

    def route_consent_grant(
        self, record: ConsentRecord, *, reason: str, maker: str, tenant: str = ""
    ) -> None:
        self._outbox.enqueue(
            consent_grant_to_kit_review(record, reason=reason, maker=maker, tenant=tenant),
            actor="compliance-governance",
        )

    @property
    def outbox(self) -> InMemoryOutbox:
        """Expose the outbox for inspection in tests and the demo."""
        return self._outbox
