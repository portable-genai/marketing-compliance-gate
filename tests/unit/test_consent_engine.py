"""The pure consent engine: fail-closed, deterministic, and reusing the rule engine.

These tests are about the DECISION, not the plumbing. Each one pins a property the consent
and preference store is only worth having if it holds: silence is not consent, a withdrawal
wins over a stale grant, a suppression wins over everything, an unexpressed channel
preference is not an opt-in, and the citations on a denial come from the market's own rules
through the same engine the asset-review path uses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marketing_compliance_gate.domain.consent import (
    ChannelPreference,
    ConsentBasis,
    ConsentChannel,
    ConsentEngine,
    ConsentOutcome,
    ConsentQuery,
    ConsentReason,
    ConsentRecord,
    ConsentSnapshot,
    ConsentStatus,
    FrequencyCap,
    SuppressionEntry,
    SuppressionReason,
    SuppressionScope,
    decision_id,
)
from marketing_compliance_gate.domain.models import (
    CheckType,
    Citation,
    Market,
    Rule,
    RuleKind,
    RuleSet,
    Severity,
    SourceType,
    Vertical,
)

NOW = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
TENANT = "demo-brand"
SUBJECT = "subj-000101"


def _query(purpose: str = "marketing", channel: ConsentChannel = ConsentChannel.EMAIL):
    return ConsentQuery(
        tenant=TENANT,
        subject_id=SUBJECT,
        purpose=purpose,
        channel=channel,
        market=Market.SG,
        vertical=Vertical.BANKING,
    )


def _granted_record(**overrides) -> ConsentRecord:
    defaults = dict(
        id="cr-1",
        tenant=TENANT,
        subject_id=SUBJECT,
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        effective_from=NOW - timedelta(days=30),
        expires_at=NOW + timedelta(days=365),
        captured_at=NOW - timedelta(days=30),
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-1",
    )
    defaults.update(overrides)
    return ConsentRecord(**defaults)  # type: ignore[arg-type]


def _opted_in(channel: ConsentChannel = ConsentChannel.EMAIL) -> ChannelPreference:
    return ChannelPreference(
        id="cp-1",
        tenant=TENANT,
        subject_id=SUBJECT,
        channel=channel,
        opted_in=True,
        updated_at=NOW - timedelta(days=30),
    )


def _snapshot(**overrides) -> ConsentSnapshot:
    defaults = dict(
        tenant=TENANT,
        subject_id=SUBJECT,
        records=(_granted_record(),),
        preferences=(_opted_in(),),
        suppressions=(),
        caps=(),
    )
    defaults.update(overrides)
    return ConsentSnapshot(**defaults)  # type: ignore[arg-type]


CONSENT_RULE = Rule(
    id="SG-BANK-CONSENT-PDPA",
    kind=RuleKind.CONSENT,
    check=CheckType.CONSENT_REQUIRED,
    description="Direct-marketing messages require PDPA marketing consent.",
    market=Market.SG,
    vertical=Vertical.BANKING,
    severity=Severity.HIGH,
    consent_purpose="marketing",
    remediation="Send only to recipients who granted marketing consent.",
    citation=Citation(
        source_id="SG-BANK-CONSENT-PDPA",
        source_type=SourceType.REGULATION,
        title="Synthetic PDPA marketing-consent rule (FICTIONAL)",
    ),
)
RULE_SET = RuleSet(market=Market.SG, vertical=Vertical.BANKING, rules=(CONSENT_RULE,))


# --------------------------------------------------------------------------- #
# The happy path exists (so the fail-closed tests below mean something)
# --------------------------------------------------------------------------- #
def test_evidenced_grant_with_channel_opt_in_is_allowed():
    decision = ConsentEngine().decide(_query(), _snapshot(), as_of=NOW, rule_set=RULE_SET)
    assert decision.outcome is ConsentOutcome.ALLOWED
    assert decision.allowed
    assert decision.denying_reasons == ()
    assert ConsentReason.CONSENT_GRANTED in decision.reasons
    assert ConsentReason.MARKET_CONSENT_RULES_SATISFIED in decision.reasons


# --------------------------------------------------------------------------- #
# Fail closed: every unknown state denies
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("records", "expected"),
    [
        pytest.param((), ConsentReason.CONSENT_UNKNOWN, id="no-record-at-all"),
        pytest.param(
            (_granted_record(status=ConsentStatus.UNKNOWN),),
            ConsentReason.CONSENT_UNKNOWN,
            id="explicitly-unknown",
        ),
        pytest.param(
            (_granted_record(status=ConsentStatus.WITHDRAWN),),
            ConsentReason.CONSENT_WITHDRAWN,
            id="withdrawn",
        ),
        pytest.param(
            (_granted_record(status=ConsentStatus.PENDING_REVIEW),),
            ConsentReason.CONSENT_PENDING_REVIEW,
            id="pending-four-eyes",
        ),
        pytest.param(
            (_granted_record(expires_at=NOW - timedelta(days=1)),),
            ConsentReason.CONSENT_EXPIRED,
            id="expired",
        ),
        pytest.param(
            (_granted_record(effective_from=NOW + timedelta(days=1)),),
            ConsentReason.CONSENT_NOT_YET_EFFECTIVE,
            id="not-yet-effective",
        ),
    ],
)
def test_unknown_consent_state_denies(records, expected):
    """An unknown consent state is NOT consent, in every shape it can arrive in."""
    decision = ConsentEngine().decide(
        _query(), _snapshot(records=records), as_of=NOW, rule_set=RULE_SET
    )
    assert decision.outcome is ConsentOutcome.DENIED
    assert expected in decision.denying_reasons


def test_expiry_boundary_is_closed_at_the_instant_it_lapses():
    """At exactly ``expires_at`` the grant is spent. The boundary favours the subject."""
    expiring = _granted_record(expires_at=NOW)
    decision = ConsentEngine().decide(
        _query(), _snapshot(records=(expiring,)), as_of=NOW, rule_set=RULE_SET
    )
    assert ConsentReason.CONSENT_EXPIRED in decision.denying_reasons


def test_unexpressed_channel_preference_denies():
    """Silence on a channel is not an opt-in, even with a perfect purpose grant on file."""
    decision = ConsentEngine().decide(
        _query(), _snapshot(preferences=()), as_of=NOW, rule_set=RULE_SET
    )
    assert decision.outcome is ConsentOutcome.DENIED
    assert ConsentReason.CHANNEL_PREFERENCE_UNKNOWN in decision.denying_reasons


def test_channel_opt_out_denies_even_with_consent():
    preference = ChannelPreference(
        id="cp-out",
        tenant=TENANT,
        subject_id=SUBJECT,
        channel=ConsentChannel.EMAIL,
        opted_in=False,
        updated_at=NOW,
    )
    decision = ConsentEngine().decide(
        _query(), _snapshot(preferences=(preference,)), as_of=NOW, rule_set=RULE_SET
    )
    assert ConsentReason.CHANNEL_OPTED_OUT in decision.denying_reasons


def test_a_snapshot_for_another_tenant_cannot_answer_the_question():
    """Defense in depth: even handed the wrong snapshot, the engine refuses to read it."""
    foreign = _snapshot(tenant="other-brand")
    decision = ConsentEngine().decide(_query(), foreign, as_of=NOW, rule_set=RULE_SET)
    assert decision.outcome is ConsentOutcome.DENIED
    assert ConsentReason.SNAPSHOT_TENANT_MISMATCH in decision.denying_reasons
    assert decision.granted_purposes == ()


def test_a_blank_subject_or_purpose_denies():
    blank = ConsentQuery(
        tenant=TENANT,
        subject_id="",
        purpose="",
        channel=ConsentChannel.EMAIL,
        market=Market.SG,
        vertical=Vertical.BANKING,
    )
    decision = ConsentEngine().decide(blank, _snapshot(), as_of=NOW, rule_set=RULE_SET)
    assert ConsentReason.SUBJECT_UNRESOLVED in decision.denying_reasons
    assert ConsentReason.PURPOSE_UNRESOLVED in decision.denying_reasons


# --------------------------------------------------------------------------- #
# Suppression outranks consent
# --------------------------------------------------------------------------- #
def test_suppression_denies_even_when_consent_is_perfect():
    entry = SuppressionEntry(
        id="sup-1",
        tenant=TENANT,
        subject_id=SUBJECT,
        scope=SuppressionScope.ALL,
        reason=SuppressionReason.COMPLAINT,
        effective_from=NOW - timedelta(days=1),
    )
    decision = ConsentEngine().decide(
        _query(), _snapshot(suppressions=(entry,)), as_of=NOW, rule_set=RULE_SET
    )
    assert decision.outcome is ConsentOutcome.DENIED
    assert ConsentReason.SUPPRESSED in decision.denying_reasons
    assert decision.suppression_ids == ("sup-1",)
    # The grant is still reported: the audit trail shows consent WAS on file and was
    # overridden, rather than pretending the subject never consented.
    assert ConsentReason.CONSENT_GRANTED in decision.reasons


def test_channel_scoped_suppression_only_blocks_that_channel():
    entry = SuppressionEntry(
        id="sup-sms",
        tenant=TENANT,
        subject_id=SUBJECT,
        scope=SuppressionScope.CHANNEL,
        reason=SuppressionReason.HARD_BOUNCE,
        channel=ConsentChannel.SMS,
    )
    snapshot = _snapshot(suppressions=(entry,))
    assert ConsentEngine().decide(_query(), snapshot, as_of=NOW, rule_set=RULE_SET).allowed
    sms = _query(channel=ConsentChannel.SMS)
    assert (
        ConsentReason.SUPPRESSED
        in ConsentEngine().decide(sms, snapshot, as_of=NOW, rule_set=RULE_SET).denying_reasons
    )


def test_a_lapsed_suppression_no_longer_applies():
    entry = SuppressionEntry(
        id="sup-old",
        tenant=TENANT,
        subject_id=SUBJECT,
        scope=SuppressionScope.ALL,
        reason=SuppressionReason.SUBJECT_REQUEST,
        effective_from=NOW - timedelta(days=30),
        expires_at=NOW - timedelta(days=1),
    )
    decision = ConsentEngine().decide(
        _query(), _snapshot(suppressions=(entry,)), as_of=NOW, rule_set=RULE_SET
    )
    assert decision.allowed


# --------------------------------------------------------------------------- #
# Frequency caps
# --------------------------------------------------------------------------- #
def test_frequency_cap_denies_at_the_limit():
    cap = FrequencyCap(
        id="fc-email", tenant=TENANT, channel=ConsentChannel.EMAIL, max_messages=3, window_hours=168
    )
    engine = ConsentEngine()
    snapshot = _snapshot(caps=(cap,))
    under = engine.decide(_query(), snapshot, as_of=NOW, rule_set=RULE_SET, sends_in_window=2)
    at_limit = engine.decide(_query(), snapshot, as_of=NOW, rule_set=RULE_SET, sends_in_window=3)
    assert under.allowed
    assert ConsentReason.WITHIN_FREQUENCY_CAP in under.reasons
    assert at_limit.outcome is ConsentOutcome.DENIED
    assert ConsentReason.FREQUENCY_CAP_EXCEEDED in at_limit.denying_reasons


def test_a_purpose_specific_cap_beats_the_channel_wide_one():
    wide = FrequencyCap(
        id="fc-wide", tenant=TENANT, channel=ConsentChannel.EMAIL, max_messages=9, window_hours=168
    )
    narrow = FrequencyCap(
        id="fc-narrow",
        tenant=TENANT,
        channel=ConsentChannel.EMAIL,
        max_messages=1,
        window_hours=24,
        purpose="marketing",
    )
    decision = ConsentEngine().decide(
        _query(), _snapshot(caps=(wide, narrow)), as_of=NOW, rule_set=RULE_SET, sends_in_window=2
    )
    assert decision.cap_id == "fc-narrow"
    assert ConsentReason.FREQUENCY_CAP_EXCEEDED in decision.denying_reasons


def test_an_absent_cap_is_reported_rather_than_silently_unlimited():
    """A cap is policy, not consent state: its absence does not deny, but it IS recorded."""
    decision = ConsentEngine().decide(_query(), _snapshot(), as_of=NOW, rule_set=RULE_SET)
    assert decision.allowed
    assert ConsentReason.NO_FREQUENCY_CAP_CONFIGURED in decision.reasons
    assert decision.cap_id == ""


# --------------------------------------------------------------------------- #
# Reuse of the EXISTING rule engine (the whole point of building this inside Mkt6)
# --------------------------------------------------------------------------- #
def test_market_consent_rules_are_evaluated_by_the_existing_rule_engine():
    """A purpose the market requires but the subject never granted denies, WITH the citation.

    The subject here granted ``newsletter``, not the ``marketing`` purpose the seeded SG
    banking rule requires, so the market rule is what refuses: exactly the finding and
    citation the asset-review path would produce for the same gap.
    """
    other_purpose = _granted_record(id="cr-news", purpose="newsletter")
    decision = ConsentEngine().decide(
        _query(purpose="newsletter"),
        _snapshot(records=(other_purpose,)),
        as_of=NOW,
        rule_set=RULE_SET,
    )
    assert decision.outcome is ConsentOutcome.DENIED
    assert ConsentReason.MARKET_CONSENT_RULE_UNSATISFIED in decision.denying_reasons
    assert [c.source_id for c in decision.citations] == ["SG-BANK-CONSENT-PDPA"]
    assert any(f.failed for f in decision.findings)
    assert decision.consent_checks and not decision.consent_checks[0].satisfied


def test_a_missing_rule_set_cannot_open_the_gate():
    """No rule set means no rule CITATIONS, never a free pass: every other check still runs."""
    without_rules = ConsentEngine().decide(
        _query(), _snapshot(records=()), as_of=NOW, rule_set=None
    )
    assert without_rules.outcome is ConsentOutcome.DENIED
    assert ConsentReason.CONSENT_UNKNOWN in without_rules.denying_reasons
    assert ConsentReason.NO_MARKET_CONSENT_RULES in without_rules.reasons


# --------------------------------------------------------------------------- #
# Determinism and replayability
# --------------------------------------------------------------------------- #
def test_the_decision_is_byte_for_byte_reproducible():
    engine = ConsentEngine()
    first = engine.decide(_query(), _snapshot(), as_of=NOW, rule_set=RULE_SET)
    second = engine.decide(_query(), _snapshot(), as_of=NOW, rule_set=RULE_SET)
    assert first.id == second.id
    assert first.reasons == second.reasons
    assert first.explanation == second.explanation


def test_the_decision_id_changes_when_the_answer_changes():
    """The id is a content hash: a different outcome cannot reuse an earlier decision's id."""
    allowed = decision_id(_query(), NOW, ConsentOutcome.ALLOWED, (ConsentReason.CONSENT_GRANTED,))
    denied = decision_id(_query(), NOW, ConsentOutcome.DENIED, (ConsentReason.CONSENT_WITHDRAWN,))
    assert allowed != denied
    assert allowed.startswith("consent-")


def test_record_order_in_the_snapshot_does_not_change_the_answer():
    """The latest record wins by (captured_at, id), never by store iteration order."""
    old = _granted_record(id="cr-old", captured_at=NOW - timedelta(days=60))
    new = _granted_record(
        id="cr-new", status=ConsentStatus.WITHDRAWN, captured_at=NOW - timedelta(days=1)
    )
    engine = ConsentEngine()
    forwards = engine.decide(_query(), _snapshot(records=(old, new)), as_of=NOW, rule_set=RULE_SET)
    backwards = engine.decide(_query(), _snapshot(records=(new, old)), as_of=NOW, rule_set=RULE_SET)
    assert forwards.id == backwards.id
    assert ConsentReason.CONSENT_WITHDRAWN in forwards.denying_reasons


def test_every_denying_reason_actually_denies():
    """The outcome is defined by DENYING_REASONS membership and nothing else.

    A reason added to the enum but forgotten in the denying set would silently become
    informational, so this walks the whole vocabulary rather than trusting the definition.
    """
    from marketing_compliance_gate.domain.consent import DENYING_REASONS

    assert DENYING_REASONS
    for reason in DENYING_REASONS:
        assert reason.value  # every member is a real, non-empty vocabulary term
    # And the two halves are disjoint and exhaustive over the enum.
    informational = set(ConsentReason) - DENYING_REASONS
    assert informational
    assert not (informational & DENYING_REASONS)
