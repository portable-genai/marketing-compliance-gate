"""The consent and preference domain module — records, grants, preferences, caps, suppression.

Mkt6 already models consent as one aspect of a marketing-compliance review: a
``CONSENT``-kind :class:`~marketing_compliance_gate.domain.models.Rule` with the
:attr:`~marketing_compliance_gate.domain.models.CheckType.CONSENT_REQUIRED` check, evaluated by
the deterministic :class:`~marketing_compliance_gate.domain.rule_engine.RuleEngine` into a
:class:`~marketing_compliance_gate.domain.models.ConsentCheck`. That answers "does this ASSET
have the marketing permission its market requires?".

This module is the other half, and it is the catalog's consent and preference store: "may
we contact THIS data subject, for THIS purpose, on THIS channel, right now?". It does not
duplicate the rule engine, it feeds it: the engine resolves which purposes the subject's
stored records grant at ``as_of`` and hands that set to the SAME rule engine's consent path
(:meth:`RuleEngine.consent_checks_for`), so the market's consent rules and their citations
are the ones that decide, exactly as they do on the asset path.

What lives here
---------------
* :class:`ConsentRecord` — one stored statement of a subject's consent state for one purpose.
* :class:`PurposeGrant` — the engine's deterministic resolution of a record at ``as_of``.
* :class:`ChannelPreference` — the subject's per-channel contact preference.
* :class:`FrequencyCap` — the tenant's per-channel (optionally per-purpose) rate policy.
* :class:`SuppressionEntry` — a do-not-contact entry, the strongest signal in the store.
* :class:`SendEvent` — one recorded contact, which is what the frequency cap counts.
* :class:`ConsentSnapshot` — all of the above for one (tenant, subject), read in one shot.
* :class:`ConsentEngine` — the PURE decision, and :class:`ConsentDecision` its cited result.

Fail closed, always
-------------------
An unknown consent state is NOT consent. No record, an explicitly ``unknown`` record, a
record whose grant has not started or has expired, a grant still pending its four-eyes
confirmation, a channel the subject never expressed a preference for, a snapshot belonging
to another tenant: every one of those DENIES. The engine has no branch that reads an absence
as permission, and :meth:`ConsentEngine.decide` collects every denying reason rather than
short-circuiting, so the audit trail says all of why, not just the first why.

No model, ever
--------------
There is no LLM in this module and none in :class:`ConsentDecision`. A consent decision is a
legal position about a person; it is pure code, replayable byte for byte from the snapshot
and ``as_of``, and its ``explanation`` is generated deterministically. The model's place in
the wider outreach flow is drafting a message body AFTER a decision has already allowed it.

Pure domain code: standard library only, no Google Cloud, ADK or FastAPI imports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from hex_service_kit import StrEnum

from .models import (
    Citation,
    ClaimFinding,
    ConsentCheck,
    Market,
    RuleSet,
    Vertical,
    utcnow,
)
from .rule_engine import RuleEngine


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
class ConsentChannel(StrEnum):
    """A contact channel a preference, cap, suppression or send is scoped to."""

    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    VOICE = "voice"
    CHAT = "chat"
    POST = "post"


class ConsentStatus(StrEnum):
    """The stored state of one consent record.

    ``PENDING_REVIEW`` is the fail-closed landing state for a grant asserted on a subject's
    behalf with no captured proof: it is stored, it is auditable, and it does NOT grant
    anything until a human checker confirms it (see
    :meth:`~marketing_compliance_gate.domain.consent_service.ConsentService.record`).
    ``UNKNOWN`` is an explicit "we hold no position", which denies exactly like no record.
    """

    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    PENDING_REVIEW = "pending_review"
    UNKNOWN = "unknown"


class ConsentBasis(StrEnum):
    """The lawful basis the record was captured under (recorded, never inferred)."""

    EXPLICIT_OPT_IN = "explicit_opt_in"
    SOFT_OPT_IN = "soft_opt_in"
    CONTRACTUAL = "contractual"
    LEGITIMATE_INTEREST = "legitimate_interest"


#: The bases that can carry a MARKETING purpose without a checker confirming the record.
#: Anything else asserted on a subject's behalf is consequential and lands PENDING_REVIEW.
SELF_EVIDENCING_BASES: frozenset[ConsentBasis] = frozenset(
    {ConsentBasis.EXPLICIT_OPT_IN, ConsentBasis.SOFT_OPT_IN}
)


class SuppressionScope(StrEnum):
    """How wide a suppression entry reaches."""

    ALL = "all"  # every channel, every purpose (do-not-contact)
    CHANNEL = "channel"  # one channel, every purpose
    PURPOSE = "purpose"  # one purpose, every channel


class SuppressionReason(StrEnum):
    """Why the subject is suppressed (audit vocabulary, never a decision input)."""

    SUBJECT_REQUEST = "subject_request"
    COMPLAINT = "complaint"
    HARD_BOUNCE = "hard_bounce"
    REGULATOR_ORDER = "regulator_order"
    VULNERABILITY = "vulnerability"
    DECEASED = "deceased"


class ConsentOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


class ConsentReason(StrEnum):
    """Every reason the engine can attach to a decision.

    Reasons whose name starts with a denial condition are listed in
    :data:`DENYING_REASONS`; anything else is informational and appears on an ALLOWED
    decision so the audit record shows what was actually checked.
    """

    # Denials
    TENANT_UNRESOLVED = "tenant_unresolved"
    SUBJECT_UNRESOLVED = "subject_unresolved"
    SNAPSHOT_TENANT_MISMATCH = "snapshot_tenant_mismatch"
    PURPOSE_UNRESOLVED = "purpose_unresolved"
    CONSENT_UNKNOWN = "consent_unknown"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    CONSENT_EXPIRED = "consent_expired"
    CONSENT_NOT_YET_EFFECTIVE = "consent_not_yet_effective"
    CONSENT_PENDING_REVIEW = "consent_pending_review"
    SUPPRESSED = "suppressed"
    CHANNEL_PREFERENCE_UNKNOWN = "channel_preference_unknown"
    CHANNEL_OPTED_OUT = "channel_opted_out"
    FREQUENCY_CAP_EXCEEDED = "frequency_cap_exceeded"
    MARKET_CONSENT_RULE_UNSATISFIED = "market_consent_rule_unsatisfied"
    # Informational
    CONSENT_GRANTED = "consent_granted"
    CHANNEL_OPTED_IN = "channel_opted_in"
    WITHIN_FREQUENCY_CAP = "within_frequency_cap"
    NO_FREQUENCY_CAP_CONFIGURED = "no_frequency_cap_configured"
    MARKET_CONSENT_RULES_SATISFIED = "market_consent_rules_satisfied"
    NO_MARKET_CONSENT_RULES = "no_market_consent_rules"


#: The reasons that DENY. Membership is the single definition of the outcome: the engine
#: computes ``ALLOWED`` iff no reason it attached is in this set, so adding a new denying
#: condition means adding its reason here and nowhere else.
DENYING_REASONS: frozenset[ConsentReason] = frozenset(
    {
        ConsentReason.TENANT_UNRESOLVED,
        ConsentReason.SUBJECT_UNRESOLVED,
        ConsentReason.SNAPSHOT_TENANT_MISMATCH,
        ConsentReason.PURPOSE_UNRESOLVED,
        ConsentReason.CONSENT_UNKNOWN,
        ConsentReason.CONSENT_WITHDRAWN,
        ConsentReason.CONSENT_EXPIRED,
        ConsentReason.CONSENT_NOT_YET_EFFECTIVE,
        ConsentReason.CONSENT_PENDING_REVIEW,
        ConsentReason.SUPPRESSED,
        ConsentReason.CHANNEL_PREFERENCE_UNKNOWN,
        ConsentReason.CHANNEL_OPTED_OUT,
        ConsentReason.FREQUENCY_CAP_EXCEEDED,
        ConsentReason.MARKET_CONSENT_RULE_UNSATISFIED,
    }
)

#: Deterministic reason ordering (denials first, in evaluation order, then informational).
_REASON_ORDER: tuple[ConsentReason, ...] = (
    ConsentReason.TENANT_UNRESOLVED,
    ConsentReason.SUBJECT_UNRESOLVED,
    ConsentReason.SNAPSHOT_TENANT_MISMATCH,
    ConsentReason.PURPOSE_UNRESOLVED,
    ConsentReason.SUPPRESSED,
    ConsentReason.CONSENT_UNKNOWN,
    ConsentReason.CONSENT_WITHDRAWN,
    ConsentReason.CONSENT_EXPIRED,
    ConsentReason.CONSENT_NOT_YET_EFFECTIVE,
    ConsentReason.CONSENT_PENDING_REVIEW,
    ConsentReason.CHANNEL_PREFERENCE_UNKNOWN,
    ConsentReason.CHANNEL_OPTED_OUT,
    ConsentReason.FREQUENCY_CAP_EXCEEDED,
    ConsentReason.MARKET_CONSENT_RULE_UNSATISFIED,
    ConsentReason.CONSENT_GRANTED,
    ConsentReason.CHANNEL_OPTED_IN,
    ConsentReason.WITHIN_FREQUENCY_CAP,
    ConsentReason.NO_FREQUENCY_CAP_CONFIGURED,
    ConsentReason.MARKET_CONSENT_RULES_SATISFIED,
    ConsentReason.NO_MARKET_CONSENT_RULES,
)
_REASON_RANK: dict[ConsentReason, int] = {r: i for i, r in enumerate(_REASON_ORDER)}


# --------------------------------------------------------------------------- #
# Stored records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """One stored statement of a data subject's consent for one purpose.

    Tenant-owned, exactly like substantiation evidence: ``tenant`` is the isolation boundary
    and every read is authorized against the VERIFIED principal's tenant, never a
    client-supplied value.

    ``effective_from`` / ``expires_at`` make the record's window explicit so a decision is
    replayable at any ``as_of``: the engine never asks "is it granted?" without a date.
    ``evidence_ref`` is the locator for the captured proof (the signed form, the recorded
    call, the preference-centre event id); a grant asserted without one is not self-evidencing
    and lands :attr:`ConsentStatus.PENDING_REVIEW`.
    """

    id: str
    tenant: str
    subject_id: str
    purpose: str
    status: ConsentStatus
    basis: ConsentBasis = ConsentBasis.EXPLICIT_OPT_IN
    effective_from: datetime | None = None
    expires_at: datetime | None = None
    captured_at: datetime = field(default_factory=utcnow)
    source: str = ""  # where the statement was captured (fictional in the seed)
    evidence_ref: str = ""  # locator for the captured proof
    note: str = ""


@dataclass(frozen=True, slots=True)
class ChannelPreference:
    """The subject's stated preference for one channel.

    Absence is NOT an opt-in. A channel with no stored preference denies with
    :attr:`ConsentReason.CHANNEL_PREFERENCE_UNKNOWN`.
    """

    id: str
    tenant: str
    subject_id: str
    channel: ConsentChannel
    opted_in: bool
    updated_at: datetime = field(default_factory=utcnow)
    source: str = ""


@dataclass(frozen=True, slots=True)
class FrequencyCap:
    """A tenant's contact-rate policy for a channel, optionally narrowed to one purpose.

    A cap is POLICY, not consent state, which is why an absent cap does not deny: it is
    reported as :attr:`ConsentReason.NO_FREQUENCY_CAP_CONFIGURED` on the decision so an
    unconfigured tenant is visible in the audit trail rather than silently unlimited. The
    consent state itself is the thing that fails closed.
    """

    id: str
    tenant: str
    channel: ConsentChannel
    max_messages: int
    window_hours: int
    purpose: str = ""  # "" => applies to every purpose


@dataclass(frozen=True, slots=True)
class SuppressionEntry:
    """A do-not-contact entry: the strongest signal in the store, checked before consent."""

    id: str
    tenant: str
    subject_id: str
    scope: SuppressionScope
    reason: SuppressionReason
    channel: ConsentChannel | None = None  # required when scope is CHANNEL
    purpose: str = ""  # required when scope is PURPOSE
    effective_from: datetime | None = None
    expires_at: datetime | None = None  # None => permanent
    note: str = ""

    def applies(self, *, channel: ConsentChannel, purpose: str, as_of: datetime) -> bool:
        """Is this entry in force for (channel, purpose) at ``as_of``? Pure and total."""
        if self.effective_from is not None and as_of < self.effective_from:
            return False
        if self.expires_at is not None and as_of >= self.expires_at:
            return False
        if self.scope is SuppressionScope.ALL:
            return True
        if self.scope is SuppressionScope.CHANNEL:
            return self.channel is channel
        return self.purpose.casefold() == purpose.casefold()


@dataclass(frozen=True, slots=True)
class SendEvent:
    """One recorded contact. The frequency cap counts these; nothing else does."""

    id: str
    tenant: str
    subject_id: str
    channel: ConsentChannel
    purpose: str = ""
    decision_id: str = ""
    sent_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class ConsentSnapshot:
    """Everything the store holds for one (tenant, subject), read in a single shot.

    One snapshot per decision is deliberate: a decision assembled from several reads taken at
    different instants is not replayable, and a withdrawal landing between two reads could be
    missed. The store returns the whole picture and the engine decides from that alone.
    """

    tenant: str
    subject_id: str
    records: tuple[ConsentRecord, ...] = ()
    preferences: tuple[ChannelPreference, ...] = ()
    suppressions: tuple[SuppressionEntry, ...] = ()
    caps: tuple[FrequencyCap, ...] = ()

    def preference_for(self, channel: ConsentChannel) -> ChannelPreference | None:
        """The most recently updated preference for ``channel``, or ``None`` (which denies)."""
        candidates = [p for p in self.preferences if p.channel is channel]
        if not candidates:
            return None
        return max(candidates, key=lambda p: (p.updated_at, p.id))


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ConsentQuery:
    """The question put to the engine: contact THIS subject, for THIS purpose, on THIS channel."""

    tenant: str
    subject_id: str
    purpose: str
    channel: ConsentChannel
    market: Market
    vertical: Vertical


@dataclass(frozen=True, slots=True)
class PurposeGrant:
    """The engine's deterministic resolution of a subject's records for ONE purpose.

    ``granted`` is decided by pure code from the stored records and ``as_of``; it is never
    read from a field, so a store that hands back a stale ``granted`` boolean cannot move it.
    """

    purpose: str
    granted: bool
    status: ConsentStatus
    reason: ConsentReason
    record_id: str = ""
    basis: ConsentBasis | None = None
    effective_from: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    """The cited, replayable outcome of one consent question.

    Consequential and PURE: no model contributed to any field. ``id`` is a content hash of
    the inputs and the outcome (see :func:`decision_id`), so quoting a decision id in a send
    record pins exactly what was decided, and re-running the same snapshot at the same
    ``as_of`` reproduces the same id.
    """

    id: str
    tenant: str
    subject_id: str
    purpose: str
    channel: ConsentChannel
    market: Market
    vertical: Vertical
    as_of: datetime
    outcome: ConsentOutcome
    reasons: tuple[ConsentReason, ...] = ()
    grant: PurposeGrant | None = None
    granted_purposes: tuple[str, ...] = ()
    consent_checks: tuple[ConsentCheck, ...] = ()
    findings: tuple[ClaimFinding, ...] = ()
    suppression_ids: tuple[str, ...] = ()
    preference_id: str = ""
    cap_id: str = ""
    cap_limit: int | None = None
    cap_window_hours: int | None = None
    sends_in_window: int = 0
    citations: tuple[Citation, ...] = ()
    explanation: str = ""
    decided_at: datetime = field(default_factory=utcnow)

    @property
    def allowed(self) -> bool:
        return self.outcome is ConsentOutcome.ALLOWED

    @property
    def denying_reasons(self) -> tuple[ConsentReason, ...]:
        return tuple(r for r in self.reasons if r in DENYING_REASONS)


def decision_id(
    query: ConsentQuery,
    as_of: datetime,
    outcome: ConsentOutcome,
    reasons: tuple[ConsentReason, ...],
) -> str:
    """A stable content hash of what was asked and what was answered.

    Deterministic by construction: the same question against the same stored state at the
    same ``as_of`` yields the same id, so a send that quotes a decision id can be reconciled
    against a replay of the store months later.
    """
    payload = json.dumps(
        {
            "tenant": query.tenant,
            "subject": query.subject_id,
            "purpose": query.purpose,
            "channel": query.channel.value,
            "market": query.market.value,
            "vertical": query.vertical.value,
            "as_of": as_of.isoformat(),
            "outcome": outcome.value,
            "reasons": [r.value for r in reasons],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=10).hexdigest()
    return f"consent-{digest}"


# --------------------------------------------------------------------------- #
# The pure engine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ConsentEngine:
    """The deterministic consent decision. Pure, stdlib-only, replayable, fail-closed.

    Evaluation order (every step runs; none short-circuits, so the decision reports ALL of
    why it landed where it did):

    1. the question is answerable at all (tenant, subject, purpose present; the snapshot
       belongs to the asked-for tenant),
    2. suppression, the strongest signal, checked FIRST so a do-not-contact entry is visible
       in the reasons even when consent would otherwise have been fine,
    3. the purpose grant resolved from the stored records at ``as_of``,
    4. the channel preference (absence denies),
    5. the frequency cap against the recorded sends in the window,
    6. the market's own ``CONSENT_REQUIRED`` rules, evaluated by the SAME
       :class:`RuleEngine` the asset-review path uses, which is where the citations come from.

    The outcome is ``ALLOWED`` iff no attached reason is in :data:`DENYING_REASONS`.
    """

    rule_engine: RuleEngine = field(default_factory=RuleEngine)

    # ------------------------------------------------------------------ #
    # Purpose grants
    # ------------------------------------------------------------------ #
    def resolve_grant(
        self, purpose: str, snapshot: ConsentSnapshot, as_of: datetime
    ) -> PurposeGrant:
        """Resolve the subject's records for ONE purpose at ``as_of``. Fail-closed and total.

        The latest record wins, ordered by ``(captured_at, id)`` so the result never depends
        on store iteration order. No record at all is
        :attr:`ConsentReason.CONSENT_UNKNOWN`, which is a denial: silence is not consent.
        """
        matching = [r for r in snapshot.records if r.purpose.casefold() == purpose.casefold()]
        if not matching:
            return PurposeGrant(
                purpose=purpose,
                granted=False,
                status=ConsentStatus.UNKNOWN,
                reason=ConsentReason.CONSENT_UNKNOWN,
            )
        latest = max(matching, key=lambda r: (r.captured_at, r.id))
        reason = self._grant_reason(latest, as_of)
        return PurposeGrant(
            purpose=purpose,
            granted=reason is ConsentReason.CONSENT_GRANTED,
            status=latest.status,
            reason=reason,
            record_id=latest.id,
            basis=latest.basis,
            effective_from=latest.effective_from,
            expires_at=latest.expires_at,
        )

    @staticmethod
    def _grant_reason(record: ConsentRecord, as_of: datetime) -> ConsentReason:
        if record.status is ConsentStatus.WITHDRAWN:
            return ConsentReason.CONSENT_WITHDRAWN
        if record.status is ConsentStatus.PENDING_REVIEW:
            return ConsentReason.CONSENT_PENDING_REVIEW
        if record.status is not ConsentStatus.GRANTED:
            return ConsentReason.CONSENT_UNKNOWN
        if record.effective_from is not None and as_of < record.effective_from:
            return ConsentReason.CONSENT_NOT_YET_EFFECTIVE
        if record.expires_at is not None and as_of >= record.expires_at:
            return ConsentReason.CONSENT_EXPIRED
        return ConsentReason.CONSENT_GRANTED

    def granted_purposes(self, snapshot: ConsentSnapshot, as_of: datetime) -> tuple[str, ...]:
        """Every purpose the subject's records actually grant at ``as_of``, sorted.

        This is the bridge into the existing rule engine: it is exactly the shape
        :meth:`RuleEngine.consent_checks_for` takes, which is what the asset-review path
        derives from ``MarketingAsset.granted_consents``. One engine, two sources of truth
        about who granted what.
        """
        purposes = {r.purpose for r in snapshot.records}
        granted = [p for p in sorted(purposes) if self.resolve_grant(p, snapshot, as_of).granted]
        return tuple(granted)

    # ------------------------------------------------------------------ #
    # The decision
    # ------------------------------------------------------------------ #
    def decide(
        self,
        query: ConsentQuery,
        snapshot: ConsentSnapshot,
        *,
        as_of: datetime,
        rule_set: RuleSet | None = None,
        sends_in_window: int = 0,
    ) -> ConsentDecision:
        """Decide whether ``query`` may proceed against ``snapshot`` at ``as_of``."""
        reasons: list[ConsentReason] = []
        answerable = self._check_answerable(query, snapshot, reasons)

        suppression_ids = self._check_suppression(query, snapshot, as_of, reasons)
        grant = self.resolve_grant(query.purpose, snapshot, as_of) if answerable else None
        if grant is not None:
            reasons.append(grant.reason)
        preference_id = self._check_preference(query, snapshot, reasons)
        cap, sends = self._check_cap(query, snapshot, sends_in_window, reasons)
        granted = self.granted_purposes(snapshot, as_of) if answerable else ()
        checks, findings, citations = self._check_market_rules(granted, rule_set, reasons)

        ordered = self._order(reasons)
        outcome = (
            ConsentOutcome.DENIED
            if any(r in DENYING_REASONS for r in ordered)
            else ConsentOutcome.ALLOWED
        )
        return ConsentDecision(
            id=decision_id(query, as_of, outcome, ordered),
            tenant=query.tenant,
            subject_id=query.subject_id,
            purpose=query.purpose,
            channel=query.channel,
            market=query.market,
            vertical=query.vertical,
            as_of=as_of,
            outcome=outcome,
            reasons=ordered,
            grant=grant,
            granted_purposes=granted,
            consent_checks=checks,
            findings=findings,
            suppression_ids=suppression_ids,
            preference_id=preference_id,
            cap_id=cap.id if cap is not None else "",
            cap_limit=cap.max_messages if cap is not None else None,
            cap_window_hours=cap.window_hours if cap is not None else None,
            sends_in_window=sends,
            citations=citations,
            explanation=self._explain(query, outcome, ordered),
        )

    # ------------------------------------------------------------------ #
    # Steps (each pure, each appends its own reasons)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_answerable(
        query: ConsentQuery, snapshot: ConsentSnapshot, reasons: list[ConsentReason]
    ) -> bool:
        ok = True
        if not query.tenant.strip():
            reasons.append(ConsentReason.TENANT_UNRESOLVED)
            ok = False
        if not query.subject_id.strip():
            reasons.append(ConsentReason.SUBJECT_UNRESOLVED)
            ok = False
        if not query.purpose.strip():
            reasons.append(ConsentReason.PURPOSE_UNRESOLVED)
            ok = False
        # A snapshot for a different tenant or subject cannot answer this question. Reading
        # it anyway would be the cross-tenant leak the whole store is built to refuse, so it
        # denies here as well as at the service boundary (defense in depth).
        if ok and (snapshot.tenant != query.tenant or snapshot.subject_id != query.subject_id):
            reasons.append(ConsentReason.SNAPSHOT_TENANT_MISMATCH)
            ok = False
        return ok

    @staticmethod
    def _check_suppression(
        query: ConsentQuery,
        snapshot: ConsentSnapshot,
        as_of: datetime,
        reasons: list[ConsentReason],
    ) -> tuple[str, ...]:
        hits = tuple(
            sorted(
                entry.id
                for entry in snapshot.suppressions
                if entry.applies(channel=query.channel, purpose=query.purpose, as_of=as_of)
            )
        )
        if hits:
            reasons.append(ConsentReason.SUPPRESSED)
        return hits

    @staticmethod
    def _check_preference(
        query: ConsentQuery, snapshot: ConsentSnapshot, reasons: list[ConsentReason]
    ) -> str:
        preference = snapshot.preference_for(query.channel)
        if preference is None:
            reasons.append(ConsentReason.CHANNEL_PREFERENCE_UNKNOWN)
            return ""
        reasons.append(
            ConsentReason.CHANNEL_OPTED_IN
            if preference.opted_in
            else ConsentReason.CHANNEL_OPTED_OUT
        )
        return preference.id

    @staticmethod
    def _check_cap(
        query: ConsentQuery,
        snapshot: ConsentSnapshot,
        sends_in_window: int,
        reasons: list[ConsentReason],
    ) -> tuple[FrequencyCap | None, int]:
        """Select the most restrictive applicable cap and compare the recorded sends to it.

        A purpose-specific cap beats a channel-wide one; among equals the lowest allowance
        wins, then the id, so the selection is deterministic.
        """
        applicable = [
            cap
            for cap in snapshot.caps
            if cap.channel is query.channel
            and (not cap.purpose or cap.purpose.casefold() == query.purpose.casefold())
        ]
        if not applicable:
            reasons.append(ConsentReason.NO_FREQUENCY_CAP_CONFIGURED)
            return None, sends_in_window
        cap = min(applicable, key=lambda c: (0 if c.purpose else 1, c.max_messages, c.id))
        if sends_in_window >= cap.max_messages:
            reasons.append(ConsentReason.FREQUENCY_CAP_EXCEEDED)
        else:
            reasons.append(ConsentReason.WITHIN_FREQUENCY_CAP)
        return cap, sends_in_window

    def _check_market_rules(
        self,
        granted_purposes: tuple[str, ...],
        rule_set: RuleSet | None,
        reasons: list[ConsentReason],
    ) -> tuple[tuple[ConsentCheck, ...], tuple[ClaimFinding, ...], tuple[Citation, ...]]:
        """Run the market's own consent rules through the EXISTING deterministic rule engine.

        This is the reuse the consent store is built on: the citations a compliance officer
        reads on a consent denial are the same rule citations the asset-review path produces,
        because they come from the same rule set and the same engine.
        """
        if rule_set is None or not rule_set.rules:
            reasons.append(ConsentReason.NO_MARKET_CONSENT_RULES)
            return (), (), ()
        checks, findings = self.rule_engine.consent_checks_for(granted_purposes, rule_set)
        if not checks and not findings:
            reasons.append(ConsentReason.NO_MARKET_CONSENT_RULES)
        elif any(not c.satisfied for c in checks) or any(f.failed for f in findings):
            reasons.append(ConsentReason.MARKET_CONSENT_RULE_UNSATISFIED)
        else:
            reasons.append(ConsentReason.MARKET_CONSENT_RULES_SATISFIED)
        seen: dict[tuple[str, int | None], Citation] = {}
        for check in checks:
            for citation in check.citations:
                seen.setdefault((citation.source_id, citation.page), citation)
        for finding in findings:
            for citation in finding.citations:
                seen.setdefault((citation.source_id, citation.page), citation)
        citations = tuple(seen[k] for k in sorted(seen, key=lambda k: (k[0], k[1] or 0)))
        return checks, findings, citations

    # ------------------------------------------------------------------ #
    # Presentation (deterministic; never a model)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _order(reasons: list[ConsentReason]) -> tuple[ConsentReason, ...]:
        unique = dict.fromkeys(reasons)
        return tuple(sorted(unique, key=lambda r: _REASON_RANK[r]))

    @staticmethod
    def _explain(
        query: ConsentQuery, outcome: ConsentOutcome, reasons: tuple[ConsentReason, ...]
    ) -> str:
        denials = [r.value for r in reasons if r in DENYING_REASONS]
        target = f"{query.purpose} on {query.channel.value} ({query.market.value})"
        if outcome is ConsentOutcome.ALLOWED:
            return f"Contact permitted for {target}: {', '.join(r.value for r in reasons)}."
        return f"Contact refused for {target}: {', '.join(denials)}."
