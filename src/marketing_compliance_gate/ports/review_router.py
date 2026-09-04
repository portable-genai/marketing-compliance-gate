"""ReviewRouterPort: the boundary that routes an escalated compliance review to human-review-console
(rule R8).

marketing-compliance-gate **is** the marketing maker-checker gate: every clear or block
:class:`Review` sets ``requires_human_review`` and its ``ApprovalRecord`` starts PENDING. Rule R8
says a producer that sets ``requires_human_review`` MUST route the item to the human-review-console
Human-Review & Maker-Checker Console rather than terminate the escalation in a per-repo boolean.
This port is that hand-off, wired ONLY on the review/maker producer path: the agent proposes
(maker), a qualified human disposes (checker), and the console is where that disposition happens.
The domain stays pure: the adapter (not this port) depends on the shared ``review-kit`` client and
does the S2S submission.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.consent import ConsentRecord
from ..domain.models import Review, SubstantiationAssessment


@runtime_checkable
class ReviewRouterPort(Protocol):
    def route(self, review: Review, *, maker: str, tenant: str = "") -> None:
        """Route an escalated compliance review to human-review-console for human review (idempotent
        is ideal).
        """
        ...

    def route_assessment(
        self, assessment: SubstantiationAssessment, *, maker: str, tenant: str = ""
    ) -> None:
        """Route an escalated green-claim substantiation to human-review-console (rule R8).

        A green claim never publishes on the agent's say-so: an assessment that requires
        human review is handed to the same console as a compliance review, so the
        escalation ends in a checker's queue and not in a per-repo boolean.
        """
        ...

    def route_consent_grant(
        self, record: ConsentRecord, *, reason: str, maker: str, tenant: str = ""
    ) -> None:
        """Route a consent grant that nobody could evidence at capture time (rule R8).

        The consent and preference store gates the one write that widens what may be done to
        a person: a grant with no captured proof lands ``PENDING_REVIEW``, grants nothing,
        and comes here so a human checker confirms or refuses it in the same console as every
        other escalation from this repo.
        """
        ...
