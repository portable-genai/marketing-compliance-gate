"""ConsentService — orchestration for the consent and preference store (tenant-scoped).

Wraps the pure :class:`~marketing_compliance_gate.domain.consent.ConsentEngine` in the ports the
rest of the hexagon provides: the consent store, the market rule provider, the audit sink,
the tracer, and the Hrz7 review router. It owns no decision logic of its own. Every
consequential answer is computed by the engine, in pure code, from a single snapshot at a
single ``as_of``.

Object-level authorization (fail-closed, server-verified)
---------------------------------------------------------
Consent records are personal data about a named individual, so every method here is gated on
``principal.tenant``, resolved by the IdentityPort from the transport and never supplied by
the client:

* a principal with no tenant is denied outright,
* the snapshot read goes to the store WITH the verified tenant, so it cannot span tenants,
* a single-record read compares the record's tenant to the principal's and raises
  :class:`TenantAccessDeniedError` (HTTP 403) on a mismatch, a denial rather than a 404, and
* every write is stamped with the verified tenant, so a caller cannot write into another
  tenant's store by putting a different tenant in the body.

The maker-checker gate on GRANTS (rule R8)
------------------------------------------
Recording a WITHDRAWAL, a suppression or a channel opt-out takes effect immediately and needs
no human: those only ever reduce what may be done to a person, so delaying them would be the
unsafe direction.

Recording a GRANT is the consequential direction, and it is gated by what the caller can
show. A grant captured with proof (an explicit or soft opt-in, from a named source, carrying
an ``evidence_ref`` locator for the captured statement) is self-evidencing and is stored
GRANTED. A grant asserted on a subject's behalf with no such proof is stored
:attr:`ConsentStatus.PENDING_REVIEW`, which the engine treats as NOT granted, and is routed
to the Hrz7 maker-checker console. It becomes effective only when a human checker confirms it
through :meth:`confirm`. Consent that nobody can evidence is never manufactured by this
service on its own say-so.

No model participates in any of this. Pure domain code: no Google Cloud, ADK or FastAPI.
"""

from __future__ import annotations

import contextlib
import hashlib
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from .consent import (
    SELF_EVIDENCING_BASES,
    ChannelPreference,
    ConsentBasis,
    ConsentDecision,
    ConsentEngine,
    ConsentQuery,
    ConsentRecord,
    ConsentSnapshot,
    ConsentStatus,
    FrequencyCap,
    SendEvent,
    SuppressionEntry,
    SuppressionScope,
)
from .errors import ConsentRecordNotFoundError, ConsentWriteRejectedError, TenantAccessDeniedError
from .identity import Principal
from .models import AuditEvent, Decision, Market, Vertical, utcnow

#: Sends are counted over the selected cap's window. With no cap configured there is nothing
#: to count against, so the service does not go to the store at all: the decision reports
#: ``NO_FREQUENCY_CAP_CONFIGURED`` and the count stays zero.
_NO_CAP_LOOKBACK = timedelta(0)


@dataclass(frozen=True, slots=True)
class ConsentReceipt:
    """The auditable acknowledgement of one consent write.

    ``requires_human_review`` is True exactly when the write was a grant the caller could not
    evidence: the record is stored, it grants nothing yet, and it is queued for a checker.
    """

    record_id: str
    tenant: str
    subject_id: str
    purpose: str
    status: ConsentStatus
    requires_human_review: bool
    reason: str = ""
    accepted_at: Any = None


class ConsentService:
    """Answer and record consent for a data subject. Constructor takes explicit ports."""

    def __init__(
        self,
        consent_store: Any,
        rule_provider: Any,
        tracer: Any,
        audit: Any,
        engine: ConsentEngine | None = None,
        review_router: Any = None,
    ) -> None:
        self._store = consent_store
        self._rules = rule_provider
        self._tracer = tracer
        self._audit = audit
        self._engine = engine or ConsentEngine()
        # Rule R8: an unevidenced grant sets requires_human_review and is handed to the Hrz7
        # console rather than terminating in a per-repo boolean. Optional so unit tests and
        # the CLI can omit it; when unset the write still audits ESCALATED and still grants
        # nothing, it is simply not forwarded to a console.
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Tenant resolution (the one place a tenant is accepted)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tenant_of(principal: Principal) -> str:
        """The verified tenant, or a denial. Never falls back to a client-supplied value."""
        tenant = (principal.tenant or "").strip()
        if not tenant:
            raise TenantAccessDeniedError(
                "the verified principal carries no tenant; consent records are personal data "
                "and are refused rather than served or written without a tenant"
            )
        return tenant

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def snapshot(self, subject_id: str, principal: Principal) -> ConsentSnapshot:
        """Everything the principal's OWN tenant holds for one subject."""
        tenant = self._tenant_of(principal)
        with self._span("consent.snapshot", tenant=tenant):
            snapshot = self._store.snapshot(tenant, subject_id)
        return snapshot  # type: ignore[no-any-return]

    def record_by_id(self, record_id: str, principal: Principal) -> ConsentRecord:
        """Read one consent record; a cross-tenant read is DENIED (403), never hidden as 404."""
        tenant = self._tenant_of(principal)
        record = self._store.get_record(record_id)
        if record is None:
            raise ConsentRecordNotFoundError(f"no consent record with id {record_id!r}")
        if record.tenant != tenant:
            self._audit_denial(record_id, principal, record.tenant)
            raise TenantAccessDeniedError(
                f"consent record {record_id!r} belongs to another tenant; the request is "
                "refused (the record exists, and you may not read it)"
            )
        return record  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ #
    # The decision
    # ------------------------------------------------------------------ #
    def decide(
        self,
        subject_id: str,
        purpose: str,
        channel: Any,
        principal: Principal,
        *,
        market: Market | None = None,
        vertical: Vertical | None = None,
        as_of: Any = None,
    ) -> ConsentDecision:
        """Decide whether the principal's tenant may contact ``subject_id`` right now.

        The whole decision is pure: this method reads ONE snapshot, optionally counts the
        sends in the selected cap's window, loads the market rule set, and hands all of it to
        the engine. Every consequential value on the result was computed by that engine.
        """
        tenant = self._tenant_of(principal)
        moment = as_of or utcnow()
        query = ConsentQuery(
            tenant=tenant,
            subject_id=subject_id,
            purpose=purpose,
            channel=channel,
            market=market or Market.SG,
            vertical=vertical or Vertical.BANKING,
        )
        with self._span("consent.decide", tenant=tenant, market=query.market.value):
            snapshot = self._store.snapshot(tenant, subject_id)
            sends = self._count_sends(query, snapshot, moment)
            rule_set = self._rule_set(query)
            decision = self._engine.decide(
                query,
                snapshot,
                as_of=moment,
                rule_set=rule_set,
                sends_in_window=sends,
            )
            self._audit_decision(decision, principal)
            return decision

    def _count_sends(self, query: ConsentQuery, snapshot: ConsentSnapshot, moment: Any) -> int:
        """Count sends over the window of the cap the engine WOULD select, or zero.

        The window comes from the cap policy rather than a constant, so tightening a cap
        tightens the count it is measured against, and a tenant with no cap costs no read.
        """
        applicable = [
            cap
            for cap in snapshot.caps
            if cap.channel is query.channel
            and (not cap.purpose or cap.purpose.casefold() == query.purpose.casefold())
        ]
        if not applicable:
            return 0
        cap = min(applicable, key=lambda c: (0 if c.purpose else 1, c.max_messages, c.id))
        window = timedelta(hours=max(cap.window_hours, 0)) or _NO_CAP_LOOKBACK
        return int(
            self._store.count_sends(query.tenant, query.subject_id, query.channel, moment - window)
        )

    def _rule_set(self, query: ConsentQuery) -> Any:
        """Load the market rule set; a provider failure leaves the rules out, never open.

        A missing rule set does not ALLOW anything: the engine reports
        ``NO_MARKET_CONSENT_RULES`` and every other check still has to pass on its own. The
        market rules can only ever add a denial, so their absence cannot be a fail-open.
        """
        try:
            return self._rules.rule_set(query.market, query.vertical)
        except Exception:  # noqa: BLE001 - the store's own checks stand without the rule set
            return None

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def record(self, record: ConsentRecord, principal: Principal) -> ConsentReceipt:
        """Store one consent record under the VERIFIED tenant, gating unevidenced grants.

        The submitted ``tenant`` is ignored and replaced with the principal's, so a body
        cannot write into another tenant's store. A grant the caller cannot evidence is
        downgraded to :attr:`ConsentStatus.PENDING_REVIEW` here, before it reaches the store,
        and routed to Hrz7: the engine will not read it as consent until a checker confirms.
        """
        tenant = self._tenant_of(principal)
        self._require(record.subject_id.strip(), "a consent record must name a subject")
        self._require(record.purpose.strip(), "a consent record must name a purpose")
        self._require(record.id.strip(), "a consent record must carry an id")

        gated, reason = self._gate_status(record)
        stored = replace(gated, tenant=tenant)
        with self._span("consent.record", tenant=tenant):
            record_id = self._store.put_record(stored)
        requires_review = stored.status is ConsentStatus.PENDING_REVIEW
        self._audit_write(
            action="consent_record",
            actor=principal.actor,
            record=stored,
            escalated=requires_review,
            reason=reason,
        )
        if requires_review:
            self._route(stored, principal, reason)
        return ConsentReceipt(
            record_id=record_id,
            tenant=tenant,
            subject_id=stored.subject_id,
            purpose=stored.purpose,
            status=stored.status,
            requires_human_review=requires_review,
            reason=reason,
            accepted_at=utcnow(),
        )

    @staticmethod
    def _gate_status(record: ConsentRecord) -> tuple[ConsentRecord, str]:
        """Decide whether a submitted record may take effect immediately. Pure.

        Withdrawals, unknown states and records already marked pending pass through: they
        never widen what may be done to the subject. A GRANT passes through only when it is
        self-evidencing (an explicit or soft opt-in, from a named source, with a locator for
        the captured statement); otherwise it is downgraded to PENDING_REVIEW.
        """
        if record.status is not ConsentStatus.GRANTED:
            return record, "write only ever narrows permission; applied immediately"
        evidenced = (
            record.basis in SELF_EVIDENCING_BASES
            and bool(record.source.strip())
            and bool(record.evidence_ref.strip())
        )
        if evidenced:
            return record, f"grant evidenced by {record.basis.value} from {record.source}"
        missing = []
        if record.basis not in SELF_EVIDENCING_BASES:
            missing.append(f"basis {record.basis.value} is not a captured opt-in")
        if not record.source.strip():
            missing.append("no capture source")
        if not record.evidence_ref.strip():
            missing.append("no evidence_ref for the captured statement")
        return (
            replace(record, status=ConsentStatus.PENDING_REVIEW),
            "grant asserted without capturable proof (" + "; ".join(missing) + ")",
        )

    def confirm(
        self, record_id: str, principal: Principal, *, approved: bool, rationale: str = ""
    ) -> ConsentReceipt:
        """The checker half: a human confirms or refuses a grant that landed PENDING_REVIEW.

        Approving promotes the record to GRANTED; refusing sets it WITHDRAWN, which is the
        fail-closed terminal state (a refused grant must not linger as pending forever). Both
        are audited with the checker as the actor.
        """
        tenant = self._tenant_of(principal)
        record = self.record_by_id(record_id, principal)
        if record.status is not ConsentStatus.PENDING_REVIEW:
            raise ConsentWriteRejectedError(
                f"consent record {record_id!r} is {record.status.value}, not pending review; "
                "only a pending grant can be confirmed"
            )
        promoted = replace(
            record,
            status=ConsentStatus.GRANTED if approved else ConsentStatus.WITHDRAWN,
            note=(record.note + " | " if record.note else "") + f"checker: {rationale}",
        )
        self._store.put_record(promoted)
        self._audit_write(
            action="consent_confirm",
            actor=principal.actor,
            record=promoted,
            escalated=False,
            reason=f"{'approved' if approved else 'refused'}: {rationale}",
        )
        return ConsentReceipt(
            record_id=promoted.id,
            tenant=tenant,
            subject_id=promoted.subject_id,
            purpose=promoted.purpose,
            status=promoted.status,
            requires_human_review=False,
            reason=rationale,
            accepted_at=utcnow(),
        )

    def set_preference(self, preference: ChannelPreference, principal: Principal) -> str:
        """Store one channel preference under the verified tenant."""
        tenant = self._tenant_of(principal)
        self._require(preference.subject_id.strip(), "a channel preference must name a subject")
        self._require(preference.id.strip(), "a channel preference must carry an id")
        stored = replace(preference, tenant=tenant)
        record_id = str(self._store.put_preference(stored))
        self._audit_simple(
            "consent_preference",
            principal.actor,
            {
                "preference_id": record_id,
                "subject_ref": self._subject_ref(stored.tenant, stored.subject_id),
                "channel": stored.channel.value,
                "opted_in": str(stored.opted_in).lower(),
            },
        )
        return record_id

    def suppress(self, entry: SuppressionEntry, principal: Principal) -> str:
        """Store one suppression entry. Takes effect immediately: it only ever denies."""
        tenant = self._tenant_of(principal)
        self._require(entry.subject_id.strip(), "a suppression entry must name a subject")
        self._require(entry.id.strip(), "a suppression entry must carry an id")
        if entry.scope is SuppressionScope.CHANNEL:
            self._require(entry.channel is not None, "a CHANNEL suppression must name a channel")
        if entry.scope is SuppressionScope.PURPOSE:
            self._require(entry.purpose.strip(), "a PURPOSE suppression must name a purpose")
        stored = replace(entry, tenant=tenant)
        record_id = str(self._store.put_suppression(stored))
        self._audit_simple(
            "consent_suppression",
            principal.actor,
            {
                "suppression_id": record_id,
                "subject_ref": self._subject_ref(stored.tenant, stored.subject_id),
                "scope": stored.scope.value,
                "reason": stored.reason.value,
            },
        )
        return record_id

    def set_cap(self, cap: FrequencyCap, principal: Principal) -> str:
        """Store one tenant frequency cap (policy, not consent state)."""
        tenant = self._tenant_of(principal)
        self._require(cap.id.strip(), "a frequency cap must carry an id")
        self._require(cap.max_messages >= 0, "a frequency cap allowance cannot be negative")
        self._require(cap.window_hours > 0, "a frequency cap needs a positive window")
        stored = replace(cap, tenant=tenant)
        return str(self._store.put_cap(stored))

    def note_send(self, send: SendEvent, principal: Principal) -> str:
        """Record one contact so the frequency cap counts it.

        The consuming outreach system calls this after a send, quoting the
        :attr:`ConsentDecision.id` that permitted it, which is what ties a message in the
        audit trail back to the exact stored state that allowed it.
        """
        tenant = self._tenant_of(principal)
        self._require(send.subject_id.strip(), "a send event must name a subject")
        self._require(send.id.strip(), "a send event must carry an id")
        stored = replace(send, tenant=tenant)
        send_id = str(self._store.record_send(stored))
        self._audit_simple(
            "consent_send",
            principal.actor,
            {
                "send_id": send_id,
                "subject_ref": self._subject_ref(stored.tenant, stored.subject_id),
                "channel": stored.channel.value,
                "decision_id": stored.decision_id,
            },
        )
        return send_id

    # ------------------------------------------------------------------ #
    # Rule R8 hand-off
    # ------------------------------------------------------------------ #
    def _route(self, record: ConsentRecord, principal: Principal, reason: str) -> None:
        """Hand an unevidenced grant to the Hrz7 console (best-effort, after the audit write).

        Never fatal: the record is already stored PENDING_REVIEW and already audited, and it
        grants nothing, so a console that is down delays a confirmation rather than opening a
        hole. The kit's outbox retries.
        """
        if self._review_router is None:
            return
        router = getattr(self._review_router, "route_consent_grant", None)
        if router is None:
            return
        with contextlib.suppress(Exception):
            router(record, reason=reason, maker=principal.actor, tenant=record.tenant)

    # ------------------------------------------------------------------ #
    # Cross-cutting
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require(condition: Any, message: str) -> None:
        if not condition:
            raise ConsentWriteRejectedError(message)

    def _span(self, name: str, **attrs: str) -> Any:
        try:
            return self._tracer.span(name, **attrs)
        except Exception:  # noqa: BLE001 - tracing must never break the pipeline
            return nullcontext()

    def _audit_decision(self, decision: ConsentDecision, principal: Principal) -> None:
        self._audit.record(
            AuditEvent(
                action="consent_decision",
                actor=principal.actor,
                decision=Decision.ALLOWED if decision.allowed else Decision.BLOCKED,
                response=decision.explanation,
                citations=decision.citations,
                metadata={
                    "decision_id": decision.id,
                    "tenant": decision.tenant,
                    "subject_ref": self._subject_ref(decision.tenant, decision.subject_id),
                    "purpose": decision.purpose,
                    "channel": decision.channel.value,
                    "market": decision.market.value,
                    "outcome": decision.outcome.value,
                    "reasons": ",".join(r.value for r in decision.reasons),
                    "sends_in_window": str(decision.sends_in_window),
                },
            )
        )

    def _audit_write(
        self,
        *,
        action: str,
        actor: str,
        record: ConsentRecord,
        escalated: bool,
        reason: str,
    ) -> None:
        self._audit.record(
            AuditEvent(
                action=action,
                actor=actor,
                decision=Decision.ESCALATED if escalated else Decision.ALLOWED,
                response=reason,
                metadata={
                    "record_id": record.id,
                    "tenant": record.tenant,
                    "subject_ref": self._subject_ref(record.tenant, record.subject_id),
                    "purpose": record.purpose,
                    "status": record.status.value,
                    "basis": record.basis.value,
                },
            )
        )

    def _audit_simple(self, action: str, actor: str, metadata: dict[str, str]) -> None:
        self._audit.record(
            AuditEvent(action=action, actor=actor, decision=Decision.ALLOWED, metadata=metadata)
        )

    @staticmethod
    def _subject_ref(tenant: str, subject_id: str) -> str:
        """Return a tenant-scoped pseudonym; raw subject ids never enter audit sinks."""
        digest = hashlib.sha256(f"{tenant}\0{subject_id}".encode()).hexdigest()
        return f"subject-sha256:{digest}"

    def _audit_denial(self, record_id: str, principal: Principal, owner: str) -> None:
        self._audit.record(
            AuditEvent(
                action="consent_record_read",
                actor=principal.actor,
                decision=Decision.BLOCKED,
                response="cross-tenant consent record read refused",
                metadata={
                    "record_id": record_id,
                    "principal_tenant": principal.tenant,
                    "record_tenant": owner,
                },
            )
        )


__all__ = ["ConsentBasis", "ConsentReceipt", "ConsentService"]
