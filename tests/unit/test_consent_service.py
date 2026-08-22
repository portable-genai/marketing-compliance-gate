"""The consent service: tenant isolation, the grant gate, and the R8 hand-off.

Wired over the real local SQLite consent store and the real seeded rule KB, so these are
end-to-end through the domain: the only thing faked is the audit sink, which is captured so
the tests can assert what the audit trail actually says.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marketing_compliance_gate.config import Container
from marketing_compliance_gate.domain.consent import (
    ChannelPreference,
    ConsentBasis,
    ConsentChannel,
    ConsentOutcome,
    ConsentReason,
    ConsentRecord,
    ConsentStatus,
    SendEvent,
    SuppressionEntry,
    SuppressionReason,
    SuppressionScope,
)
from marketing_compliance_gate.domain.consent_service import ConsentService
from marketing_compliance_gate.domain.errors import (
    ConsentRecordNotFoundError,
    ConsentWriteRejectedError,
    TenantAccessDeniedError,
)
from marketing_compliance_gate.domain.identity import Principal
from marketing_compliance_gate.domain.models import Market, Vertical

NOW = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
DEMO = Principal(subject="officer@example.com", tenant="demo-brand", source="test")
OTHER = Principal(subject="rival@example.com", tenant="other-brand", source="test")
NO_TENANT = Principal(subject="stranger@example.com", tenant="", source="test")


class _CapturingAudit:
    def __init__(self) -> None:
        self.events: list = []

    def record(self, event) -> None:
        self.events.append(event)

    def actions(self) -> list[str]:
        return [e.action for e in self.events]


class _RecordingRouter:
    """Stands in for the Hrz7 hand-off so the escalation is observable in a unit test."""

    def __init__(self) -> None:
        self.routed: list[tuple] = []

    def route_consent_grant(self, record, *, reason, maker, tenant=""):
        self.routed.append((record, reason, maker, tenant))


@pytest.fixture
def audit() -> _CapturingAudit:
    return _CapturingAudit()


@pytest.fixture
def router() -> _RecordingRouter:
    return _RecordingRouter()


@pytest.fixture
def service(local_container: Container, audit: _CapturingAudit, router: _RecordingRouter):
    return ConsentService(
        consent_store=local_container.consent_store,
        rule_provider=local_container.rule_provider,
        tracer=local_container.tracer,
        audit=audit,
        review_router=router,
    )


def _decide(service, subject: str, principal=DEMO, channel=ConsentChannel.EMAIL):
    return service.decide(
        subject,
        "marketing",
        channel,
        principal,
        market=Market.SG,
        vertical=Vertical.BANKING,
        as_of=NOW,
    )


# --------------------------------------------------------------------------- #
# The seeded scenarios, end to end
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        pytest.param("subj-000102", ConsentReason.CONSENT_WITHDRAWN, id="withdrawn"),
        pytest.param("subj-000103", ConsentReason.CONSENT_EXPIRED, id="expired"),
        pytest.param("subj-000104", ConsentReason.SUPPRESSED, id="suppressed"),
        pytest.param(
            "subj-000105", ConsentReason.CHANNEL_PREFERENCE_UNKNOWN, id="no-channel-preference"
        ),
        pytest.param(
            "subj-000106", ConsentReason.CONSENT_PENDING_REVIEW, id="grant-awaiting-four-eyes"
        ),
        pytest.param("subj-999999", ConsentReason.CONSENT_UNKNOWN, id="subject-not-on-file"),
    ],
)
def test_seeded_denials(service, subject, expected):
    decision = _decide(service, subject)
    assert decision.outcome is ConsentOutcome.DENIED
    assert expected in decision.denying_reasons


def test_the_seeded_happy_path_is_allowed_and_cited(service):
    decision = _decide(service, "subj-000101")
    assert decision.allowed
    assert decision.citations, "an allowed decision still cites the market rules it satisfied"
    assert decision.granted_purposes == ("marketing",)


def test_a_decision_is_audited_with_its_id_and_reasons(service, audit):
    decision = _decide(service, "subj-000102")
    assert "consent_decision" in audit.actions()
    event = next(e for e in audit.events if e.action == "consent_decision")
    assert "subject_id" not in event.metadata
    assert event.metadata["subject_ref"].startswith("subject-sha256:")
    assert event.metadata["decision_id"] == decision.id
    assert event.metadata["outcome"] == "denied"
    assert ConsentReason.CONSENT_WITHDRAWN.value in event.metadata["reasons"]
    assert event.actor == DEMO.actor


def test_no_consent_audit_event_contains_a_raw_subject_id(service, audit):
    subject = "planted-person@example.test"
    service.set_preference(
        ChannelPreference(
            id="pref-planted",
            tenant="ignored",
            subject_id=subject,
            channel=ConsentChannel.EMAIL,
            opted_in=False,
        ),
        DEMO,
    )
    payload = repr(audit.events[-1].metadata)
    assert subject not in payload
    assert "subject_id" not in audit.events[-1].metadata


# --------------------------------------------------------------------------- #
# Tenant isolation (server-verified, fail-closed)
# --------------------------------------------------------------------------- #
def test_another_tenant_sees_nothing_for_a_subject_it_does_not_own(service):
    """The other tenant's read of demo-brand's subject denies on an empty snapshot."""
    decision = _decide(service, "subj-000101", principal=OTHER)
    assert decision.outcome is ConsentOutcome.DENIED
    assert ConsentReason.CONSENT_UNKNOWN in decision.denying_reasons
    # And its OWN subject still works, so the isolation is a boundary and not a broken store.
    assert _decide(service, "subj-000201", principal=OTHER).allowed


def test_a_principal_with_no_tenant_is_refused_outright(service):
    with pytest.raises(TenantAccessDeniedError):
        _decide(service, "subj-000101", principal=NO_TENANT)
    with pytest.raises(TenantAccessDeniedError):
        service.snapshot("subj-000101", NO_TENANT)


def test_a_cross_tenant_record_read_is_denied_not_hidden(service, audit):
    """403, not 404: the request was understood and refused, and the refusal is audited."""
    assert service.record_by_id("cr-demo-0001", DEMO).subject_id == "subj-000101"
    with pytest.raises(TenantAccessDeniedError):
        service.record_by_id("cr-demo-0001", OTHER)
    assert "consent_record_read" in audit.actions()
    denial = next(e for e in audit.events if e.action == "consent_record_read")
    assert denial.metadata["principal_tenant"] == "other-brand"
    assert denial.metadata["record_tenant"] == "demo-brand"


def test_a_missing_record_is_a_not_found(service):
    with pytest.raises(ConsentRecordNotFoundError):
        service.record_by_id("cr-nope-0000", DEMO)


def test_a_write_cannot_smuggle_in_another_tenant(service):
    """The body's tenant is ignored: the verified principal's tenant is stamped on the row."""
    record = ConsentRecord(
        id="cr-smuggle",
        tenant="other-brand",  # a lie
        subject_id="subj-000777",
        purpose="marketing",
        status=ConsentStatus.WITHDRAWN,
        captured_at=NOW,
    )
    receipt = service.record(record, DEMO)
    assert receipt.tenant == "demo-brand"
    assert service.record_by_id("cr-smuggle", DEMO).tenant == "demo-brand"


# --------------------------------------------------------------------------- #
# The grant gate (rule R8)
# --------------------------------------------------------------------------- #
def test_an_evidenced_grant_takes_effect_immediately(service, router):
    record = ConsentRecord(
        id="cr-new-evidenced",
        tenant="",
        subject_id="subj-000301",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        effective_from=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=365),
        captured_at=NOW,
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-new-evidenced",
    )
    receipt = service.record(record, DEMO)
    assert receipt.status is ConsentStatus.GRANTED
    assert receipt.requires_human_review is False
    assert router.routed == []


def test_an_unevidenced_grant_grants_nothing_and_is_routed(service, router, audit):
    """The consequential write: asserting consent nobody can show lands pending four eyes."""
    record = ConsentRecord(
        id="cr-new-asserted",
        tenant="",
        subject_id="subj-000302",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.LEGITIMATE_INTEREST,
        captured_at=NOW,
    )
    receipt = service.record(record, DEMO)
    assert receipt.status is ConsentStatus.PENDING_REVIEW
    assert receipt.requires_human_review is True
    assert len(router.routed) == 1
    routed, reason, maker, tenant = router.routed[0]
    assert routed.id == "cr-new-asserted"
    assert maker == DEMO.actor
    assert tenant == "demo-brand"
    assert "proof" in reason
    escalation = next(e for e in audit.events if e.action == "consent_record")
    assert escalation.decision.value == "escalated"
    # And it really does grant nothing until confirmed.
    assert not _decide(service, "subj-000302").allowed


def test_a_withdrawal_is_never_gated(service, router):
    """A write that only narrows permission applies at once: delaying it is the unsafe way."""
    record = ConsentRecord(
        id="cr-withdrawal",
        tenant="",
        subject_id="subj-000101",
        purpose="marketing",
        status=ConsentStatus.WITHDRAWN,
        captured_at=NOW,
    )
    receipt = service.record(record, DEMO)
    assert receipt.requires_human_review is False
    assert router.routed == []
    assert not _decide(service, "subj-000101").allowed


def test_a_checker_can_confirm_a_pending_grant(service):
    receipt = service.confirm(
        "cr-demo-0006", DEMO, approved=True, rationale="call recording located (FICTIONAL)"
    )
    assert receipt.status is ConsentStatus.GRANTED
    decision = _decide(service, "subj-000106")
    assert decision.allowed


def test_a_refused_grant_becomes_a_withdrawal_not_a_lingering_pending(service):
    receipt = service.confirm("cr-demo-0006", DEMO, approved=False, rationale="no proof found")
    assert receipt.status is ConsentStatus.WITHDRAWN
    assert ConsentReason.CONSENT_WITHDRAWN in _decide(service, "subj-000106").denying_reasons


def test_only_a_pending_record_can_be_confirmed(service):
    with pytest.raises(ConsentWriteRejectedError):
        service.confirm("cr-demo-0001", DEMO, approved=True, rationale="already granted")


def test_the_service_still_gates_when_no_router_is_wired(local_container, audit):
    """Without a console the grant is still refused effect; only the hand-off is missing."""
    service = ConsentService(
        consent_store=local_container.consent_store,
        rule_provider=local_container.rule_provider,
        tracer=local_container.tracer,
        audit=audit,
        review_router=None,
    )
    receipt = service.record(
        ConsentRecord(
            id="cr-no-router",
            tenant="",
            subject_id="subj-000303",
            purpose="marketing",
            status=ConsentStatus.GRANTED,
            basis=ConsentBasis.CONTRACTUAL,
            captured_at=NOW,
        ),
        DEMO,
    )
    assert receipt.status is ConsentStatus.PENDING_REVIEW
    assert receipt.requires_human_review is True


# --------------------------------------------------------------------------- #
# Write validation and the rest of the store
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "record",
    [
        pytest.param(
            ConsentRecord(
                id="cr-x",
                tenant="",
                subject_id="",
                purpose="marketing",
                status=ConsentStatus.GRANTED,
                captured_at=NOW,
            ),
            id="no-subject",
        ),
        pytest.param(
            ConsentRecord(
                id="cr-x",
                tenant="",
                subject_id="subj-1",
                purpose="",
                status=ConsentStatus.GRANTED,
                captured_at=NOW,
            ),
            id="no-purpose",
        ),
        pytest.param(
            ConsentRecord(
                id="",
                tenant="",
                subject_id="subj-1",
                purpose="marketing",
                status=ConsentStatus.GRANTED,
                captured_at=NOW,
            ),
            id="no-id",
        ),
    ],
)
def test_a_record_the_engine_could_never_resolve_is_refused(service, record):
    with pytest.raises(ConsentWriteRejectedError):
        service.record(record, DEMO)


def test_a_channel_suppression_must_name_its_channel(service):
    with pytest.raises(ConsentWriteRejectedError):
        service.suppress(
            SuppressionEntry(
                id="sup-bad",
                tenant="",
                subject_id="subj-000101",
                scope=SuppressionScope.CHANNEL,
                reason=SuppressionReason.HARD_BOUNCE,
            ),
            DEMO,
        )


def test_a_new_suppression_takes_effect_on_the_next_decision(service):
    assert _decide(service, "subj-000101").allowed
    service.suppress(
        SuppressionEntry(
            id="sup-new",
            tenant="",
            subject_id="subj-000101",
            scope=SuppressionScope.ALL,
            reason=SuppressionReason.SUBJECT_REQUEST,
            effective_from=NOW - timedelta(minutes=1),
        ),
        DEMO,
    )
    assert ConsentReason.SUPPRESSED in _decide(service, "subj-000101").denying_reasons


def test_a_preference_write_flips_the_channel(service):
    service.set_preference(
        ChannelPreference(
            id="cp-demo-0001",
            tenant="",
            subject_id="subj-000101",
            channel=ConsentChannel.EMAIL,
            opted_in=False,
            updated_at=NOW,
        ),
        DEMO,
    )
    assert ConsentReason.CHANNEL_OPTED_OUT in _decide(service, "subj-000101").denying_reasons


def test_recorded_sends_are_counted_against_the_seeded_cap(service):
    """The seeded demo cap is 3 email messages per 168 hours. The fourth is refused."""
    for index in range(3):
        service.note_send(
            SendEvent(
                id=f"se-{index}",
                tenant="",
                subject_id="subj-000101",
                channel=ConsentChannel.EMAIL,
                purpose="marketing",
                decision_id="consent-earlier",
                sent_at=NOW - timedelta(hours=index + 1),
            ),
            DEMO,
        )
    decision = _decide(service, "subj-000101")
    assert decision.sends_in_window == 3
    assert decision.cap_limit == 3
    assert ConsentReason.FREQUENCY_CAP_EXCEEDED in decision.denying_reasons


def test_sends_outside_the_window_do_not_count(service):
    service.note_send(
        SendEvent(
            id="se-old",
            tenant="",
            subject_id="subj-000101",
            channel=ConsentChannel.EMAIL,
            sent_at=NOW - timedelta(days=30),
        ),
        DEMO,
    )
    decision = _decide(service, "subj-000101")
    assert decision.sends_in_window == 0
    assert decision.allowed


def test_a_rule_provider_failure_cannot_open_the_gate(local_container, audit):
    """A rule set that will not load leaves the store's own checks standing, never opens."""

    class _BrokenRules:
        def rule_set(self, market, vertical):
            raise RuntimeError("rule KB unavailable")

    service = ConsentService(
        consent_store=local_container.consent_store,
        rule_provider=_BrokenRules(),
        tracer=local_container.tracer,
        audit=audit,
    )
    denied = _decide(service, "subj-000102")
    assert denied.outcome is ConsentOutcome.DENIED
    assert ConsentReason.NO_MARKET_CONSENT_RULES in denied.reasons
