"""Built-in, OBVIOUSLY-FICTIONAL synthetic seed for the local consent and preference store.

Six invented data subjects across two invented tenants, chosen so that every branch of the
deterministic consent decision is reachable offline, with no network, no IdP and no personal
data of any real person. Names are plainly made up, every address is at a ``.example``
domain, and every subject id is a synthetic counter.

The scenarios, in the order the engine evaluates them:

============== ================================================================
subject        what a decision for ``marketing`` on ``email`` demonstrates
============== ================================================================
subj-000101    ALLOWED: an evidenced explicit opt-in, an email opt-in, under cap
subj-000102    DENIED: the subject withdrew
subj-000103    DENIED: the grant expired before ``as_of``
subj-000104    DENIED: suppressed after a complaint, even though consent is on file
subj-000105    DENIED: the channel preference was never expressed (silence is not opt-in)
subj-000106    DENIED: the grant is still pending its four-eyes confirmation
subj-000201    the OTHER tenant, for proving a read cannot span tenants
============== ================================================================

The second tenant matters as much as the scenarios: ``other-brand`` holds a subject with a
perfectly good grant, so a cross-tenant read that "worked" would be visibly wrong rather than
merely empty. It mirrors the two-tenant substantiation-evidence seed next door.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...domain.consent import (
    ChannelPreference,
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentStatus,
    FrequencyCap,
    SuppressionEntry,
    SuppressionReason,
    SuppressionScope,
)


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


DEMO_TENANT = "demo-brand"
OTHER_TENANT = "other-brand"

SEED_RECORDS: tuple[ConsentRecord, ...] = (
    ConsentRecord(
        id="cr-demo-0001",
        tenant=DEMO_TENANT,
        subject_id="subj-000101",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        effective_from=_at(2026, 1, 5),
        expires_at=_at(2030, 1, 5),
        captured_at=_at(2026, 1, 5),
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-demo-0001",
        note="Ada Kestrel (FICTIONAL), ada.kestrel@example.com",
    ),
    ConsentRecord(
        id="cr-demo-0002",
        tenant=DEMO_TENANT,
        subject_id="subj-000102",
        purpose="marketing",
        status=ConsentStatus.WITHDRAWN,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        captured_at=_at(2026, 4, 2),
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-demo-0002",
        note="Bo Rivendell (FICTIONAL) withdrew after a campaign",
    ),
    ConsentRecord(
        id="cr-demo-0003",
        tenant=DEMO_TENANT,
        subject_id="subj-000103",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.SOFT_OPT_IN,
        effective_from=_at(2024, 6, 30),
        expires_at=_at(2025, 6, 30),
        captured_at=_at(2024, 6, 30),
        source="branch-onboarding.example",
        evidence_ref="dms://example.test/consent/cr-demo-0003",
        note="Cleo Marchpane (FICTIONAL): a lapsed soft opt-in",
    ),
    ConsentRecord(
        id="cr-demo-0004",
        tenant=DEMO_TENANT,
        subject_id="subj-000104",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        effective_from=_at(2026, 2, 1),
        expires_at=_at(2030, 2, 1),
        captured_at=_at(2026, 2, 1),
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-demo-0004",
        note="Dara Winnowsett (FICTIONAL): consent on file, but suppressed",
    ),
    ConsentRecord(
        id="cr-demo-0005",
        tenant=DEMO_TENANT,
        subject_id="subj-000105",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        effective_from=_at(2026, 3, 3),
        expires_at=_at(2030, 3, 3),
        captured_at=_at(2026, 3, 3),
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-demo-0005",
        note="Eero Blythestone (FICTIONAL): no channel preference was ever expressed",
    ),
    ConsentRecord(
        id="cr-demo-0006",
        tenant=DEMO_TENANT,
        subject_id="subj-000106",
        purpose="marketing",
        status=ConsentStatus.PENDING_REVIEW,
        basis=ConsentBasis.LEGITIMATE_INTEREST,
        captured_at=_at(2026, 7, 20),
        source="",
        evidence_ref="",
        note="Fen Alderquist (FICTIONAL): asserted by an operator with nothing to show",
    ),
    ConsentRecord(
        id="cr-other-0001",
        tenant=OTHER_TENANT,
        subject_id="subj-000201",
        purpose="marketing",
        status=ConsentStatus.GRANTED,
        basis=ConsentBasis.EXPLICIT_OPT_IN,
        effective_from=_at(2026, 1, 9),
        expires_at=_at(2030, 1, 9),
        captured_at=_at(2026, 1, 9),
        source="preference-centre.example",
        evidence_ref="dms://example.test/consent/cr-other-0001",
        note="Gale Thornbury (FICTIONAL), a different brand's customer",
    ),
)

SEED_PREFERENCES: tuple[ChannelPreference, ...] = (
    ChannelPreference(
        id="cp-demo-0001",
        tenant=DEMO_TENANT,
        subject_id="subj-000101",
        channel=ConsentChannel.EMAIL,
        opted_in=True,
        updated_at=_at(2026, 1, 5),
        source="preference-centre.example",
    ),
    ChannelPreference(
        id="cp-demo-0002",
        tenant=DEMO_TENANT,
        subject_id="subj-000101",
        channel=ConsentChannel.SMS,
        opted_in=False,
        updated_at=_at(2026, 1, 5),
        source="preference-centre.example",
    ),
    ChannelPreference(
        id="cp-demo-0003",
        tenant=DEMO_TENANT,
        subject_id="subj-000102",
        channel=ConsentChannel.EMAIL,
        opted_in=True,
        updated_at=_at(2026, 1, 6),
        source="preference-centre.example",
    ),
    ChannelPreference(
        id="cp-demo-0004",
        tenant=DEMO_TENANT,
        subject_id="subj-000103",
        channel=ConsentChannel.EMAIL,
        opted_in=True,
        updated_at=_at(2024, 6, 30),
        source="branch-onboarding.example",
    ),
    ChannelPreference(
        id="cp-demo-0005",
        tenant=DEMO_TENANT,
        subject_id="subj-000104",
        channel=ConsentChannel.EMAIL,
        opted_in=True,
        updated_at=_at(2026, 2, 1),
        source="preference-centre.example",
    ),
    ChannelPreference(
        id="cp-demo-0006",
        tenant=DEMO_TENANT,
        subject_id="subj-000106",
        channel=ConsentChannel.EMAIL,
        opted_in=True,
        updated_at=_at(2026, 7, 20),
        source="preference-centre.example",
    ),
    ChannelPreference(
        id="cp-other-0001",
        tenant=OTHER_TENANT,
        subject_id="subj-000201",
        channel=ConsentChannel.EMAIL,
        opted_in=True,
        updated_at=_at(2026, 1, 9),
        source="preference-centre.example",
    ),
)

SEED_SUPPRESSIONS: tuple[SuppressionEntry, ...] = (
    SuppressionEntry(
        id="sup-demo-0001",
        tenant=DEMO_TENANT,
        subject_id="subj-000104",
        scope=SuppressionScope.ALL,
        reason=SuppressionReason.COMPLAINT,
        effective_from=_at(2026, 5, 4),
        note="Complaint logged; do not contact on any channel (FICTIONAL)",
    ),
)

SEED_CAPS: tuple[FrequencyCap, ...] = (
    FrequencyCap(
        id="fc-demo-email",
        tenant=DEMO_TENANT,
        channel=ConsentChannel.EMAIL,
        max_messages=3,
        window_hours=168,
    ),
    FrequencyCap(
        id="fc-demo-sms",
        tenant=DEMO_TENANT,
        channel=ConsentChannel.SMS,
        max_messages=1,
        window_hours=24,
    ),
    FrequencyCap(
        id="fc-other-email",
        tenant=OTHER_TENANT,
        channel=ConsentChannel.EMAIL,
        max_messages=2,
        window_hours=168,
    ),
)
