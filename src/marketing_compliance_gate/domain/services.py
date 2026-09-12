"""ReviewService — the marketing-compliance orchestrator (the maker-checker gate).

Owns the full review pipeline and calls only ports plus the deterministic
:class:`RuleEngine`. The rule engine decides every consequential thing (which rules
fail, the severity, the outcome); the LLM only narrates the already-decided findings.
Every review is maker-checker gated: any non-compliant review (any failing finding)
requires human review, and the :class:`ApprovalRecord` starts PENDING until a human
checker approves or rejects.

Pipeline (each step wrapped in ``tracer.span``; audited at the end):

    tracer.span("review.build"):
      guardrail.screen(INPUT over the asset body)   [blocked -> audit BLOCKED + raise]
      -> rule_provider.rule_set: load the (market, vertical) RuleSet
                                          [empty -> RuleSetEmptyError]
      -> consent_store.snapshot(tenant, asset.audience_subject_id)
                                          (the subject's STORED consent; never the request's)
      -> rule_engine.check(asset, rule_set)          (claim / permission / brand)
      -> rule_engine.consent_checks_for(granted, rule_set, asset=asset)  (consent)
      -> decide outcome + requires_human_review      (pure)
      -> llm.generate(summary narrative)             (narration only, over findings)
      -> assemble Review (+ pending ApprovalRecord)
      -> guardrail.screen(OUTPUT over the summary)   [blocked -> audit BLOCKED + raise]
      -> audit.record

``approve`` is the checker half of maker-checker: it records a human's terminal decision
on a previously-built review and writes it to the audit log.

Consent is READ, never accepted (2026-09-12)
--------------------------------------------
The asset used to carry a ``granted_consents`` tuple, which a reviewer filled in: the gate
could be told any consent simply by typing it, and the regional consent and preference store
this same service maintains was never consulted on the review path. It is now the only source.
``review`` resolves the purposes the asset's ``audience_subject_id`` has on file for the
VERIFIED tenant through :class:`~marketing_compliance_gate.ports.consent.ConsentStorePort`, and
the market's ``CONSENT_REQUIRED`` rules decide from those. Nothing in the request can influence
the answer except which subject to look up.

Four states, and three of them grant nothing: no subject named, no verified tenant, and a
subject the store holds no record for all produce an empty purpose set, so the consent rules
FAIL and the review is non-compliant and escalated. Silence is not consent. The store's own
errors are not swallowed either: a review cannot honestly report "consent not granted" when it
could not look, so a failing read travels to the caller instead of being rendered as a denial.
``Review.consent_source`` records which of the four happened, and the audit event carries the
subject's tenant-scoped pseudonym rather than the id.

Pure domain code: no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

import contextlib
import json
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from .consent import ConsentEngine, subject_ref
from .errors import GuardrailBlockedError, RuleSetEmptyError
from .models import (
    ApprovalDecision,
    ApprovalRecord,
    AuditEvent,
    ClaimFinding,
    ConsentCheck,
    ConsentSource,
    Decision,
    Direction,
    GuardrailVerdict,
    LlmMessage,
    LlmRequest,
    MarketingAsset,
    Review,
    ReviewOutcome,
    ReviewRequest,
    Severity,
    utcnow,
)
from .rule_engine import RuleEngine

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "used_rule_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary"],
}

# Severity rank for deterministic ordering of findings (failures first, severity desc).
_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


class ReviewService:
    """Review a marketing asset against its rule set. Constructor takes explicit ports."""

    def __init__(
        self,
        rule_provider: Any,
        llm: Any,
        guardrail: Any,
        tracer: Any,
        audit: Any,
        consent_store: Any,
        engine: RuleEngine | None = None,
        consent_engine: ConsentEngine | None = None,
        review_router: Any = None,
    ) -> None:
        self._rules = rule_provider
        self._llm = llm
        self._guardrail = guardrail
        self._tracer = tracer
        self._audit = audit
        # The consent and preference store, and it is REQUIRED rather than optional. An
        # optional one would make the single most consequential input in this pipeline
        # default to absent, and a review assembled without it would look exactly like a
        # review whose subject granted nothing. Callers that cannot supply a store are
        # callers that must not produce consent findings.
        self._consent_store = consent_store
        self._engine = engine or RuleEngine()
        # Resolves a snapshot's records into the purposes they actually grant at an instant.
        # The same engine the consent store's own service uses, so "granted" means one thing.
        self._consent_engine = consent_engine or ConsentEngine(rule_engine=self._engine)
        # Rule R8: when a review requires human review it is routed to the human-review-console
        # maker-checker
        # console, not left as a boolean. Optional so unit tests and the CLI can omit it; when
        # unset the escalation still audits ESCALATED, it just is not forwarded to a console.
        # Bound only on this maker (review-producer) path; the agent never gets an approve tool.
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Public API — the maker half
    # ------------------------------------------------------------------ #
    def review(
        self,
        request: ReviewRequest,
        actor: str,
        tenant: str = "",
        *,
        as_of: datetime | None = None,
    ) -> Review:
        """Review one asset. ``tenant`` is the VERIFIED principal's, never the request's.

        ``as_of`` is the instant the subject's consent records are resolved at. It exists for
        the demo and the evaluation gate, which must reproduce the same verdict next quarter,
        and it is deliberately NOT exposed on the HTTP route: a caller able to choose the
        instant could resurrect a grant that has since expired, which is the typed-consent hole
        in another spelling. Over HTTP a review is always decided at now.
        """
        asset = request.asset
        actor = actor or request.actor or "service"
        with self._span("review.build", market=asset.market.value, vertical=asset.vertical.value):
            self._guard(asset.body or asset.title, Direction.INPUT, actor)

            rule_set = self._rules.rule_set(asset.market, asset.vertical)
            if not rule_set.rules:
                raise RuleSetEmptyError(
                    f"no rules configured for {asset.market.value}/{asset.vertical.value}; "
                    "a compliance review must be grounded in a rule set"
                )

            consent_source = self._consent_source(asset, tenant, as_of or utcnow())
            findings = list(self._engine.check(asset, rule_set))
            consent_checks, consent_findings = self._engine.consent_checks_for(
                consent_source.granted_purposes, rule_set, asset=asset
            )
            findings.extend(consent_findings)
            findings = self._order(findings)

            outcome = self._outcome(findings)
            requires_review = self._requires_human_review(outcome)
            summary = self._narrate(asset, findings, outcome)
            citations = self._merge_citations(findings, consent_checks)

            review_id = self._review_id(asset)
            review = Review(
                id=review_id,
                asset_id=asset.id,
                asset_type=asset.asset_type,
                market=asset.market,
                vertical=asset.vertical,
                outcome=outcome,
                findings=tuple(findings),
                consent_checks=consent_checks,
                summary=summary,
                citations=citations,
                approval=ApprovalRecord(review_id=review_id, decision=ApprovalDecision.PENDING),
                requires_human_review=requires_review,
                consent_source=consent_source,
            )
            self._guard(summary, Direction.OUTPUT, actor)
            self._record_review(review, actor, rule_pack_version=rule_set.version, tenant=tenant)
            # Rule R8: hand an escalated review to the human-review-console. Routing is a
            # best-effort
            # hand-off after the durable audit ESCALATED record, never fatal to an already-
            # assembled, already-audited review (the outbox path retries).
            if self._review_router is not None and review.requires_human_review:
                with contextlib.suppress(Exception):
                    self._review_router.route(review, maker=actor, tenant=tenant)
            return review

    # ------------------------------------------------------------------ #
    # Public API — the checker half (maker-checker)
    # ------------------------------------------------------------------ #
    def approve(
        self, review: Review, checker: str, approved: bool, rationale: str = ""
    ) -> ApprovalRecord:
        """Record a human checker's terminal decision on a previously-built review.

        This is the second half of the maker-checker gate: the agent (maker) produced
        the review; a qualified human (checker) now disposes. The decision is audited.
        """
        decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED
        record = ApprovalRecord(
            review_id=review.id,
            decision=decision,
            checker=checker,
            rationale=rationale,
            decided_at=utcnow(),
        )
        self._audit.record(
            AuditEvent(
                action="approve",
                actor=checker,
                decision=Decision.ALLOWED if approved else Decision.BLOCKED,
                response=f"{decision.value}: {rationale}",
                citations=review.citations,
                metadata={
                    "review_id": review.id,
                    "asset_id": review.asset_id,
                    "outcome": review.outcome.value,
                },
            )
        )
        return record

    # ------------------------------------------------------------------ #
    # Deterministic decisions (pure)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _outcome(findings: list[ClaimFinding]) -> ReviewOutcome:
        return (
            ReviewOutcome.NON_COMPLIANT
            if any(f.failed for f in findings)
            else ReviewOutcome.COMPLIANT
        )

    @staticmethod
    def _requires_human_review(outcome: ReviewOutcome) -> bool:
        """Require a checker for every regulated-claim disposition.

        ``COMPLIANT`` is consequential too: it is the recommendation that allows an
        outbound claim to proceed. The deterministic engine may propose that disposition,
        but only a qualified checker may release it. Keeping the unused ``outcome`` input
        makes the policy intent explicit and prevents callers from branching around it.
        """
        _ = outcome
        return True

    @staticmethod
    def _order(findings: list[ClaimFinding]) -> list[ClaimFinding]:
        return sorted(
            findings,
            key=lambda f: (
                0 if f.failed else 1,
                -_SEVERITY_ORDER[f.severity],
                f.rule_id,
            ),
        )

    @staticmethod
    def _review_id(asset: MarketingAsset) -> str:
        return f"review-{asset.market.value}-{asset.vertical.value}-{asset.id}"

    @staticmethod
    def _merge_citations(
        findings: list[ClaimFinding], consent_checks: tuple[ConsentCheck, ...]
    ) -> tuple:
        seen: dict[tuple[str, int | None], Any] = {}
        for f in findings:
            for c in f.citations:
                seen.setdefault((c.source_id, c.page), c)
        for chk in consent_checks:
            for c in chk.citations:
                seen.setdefault((c.source_id, c.page), c)
        return tuple(seen[k] for k in sorted(seen, key=lambda k: (k[0], k[1] or 0)))

    # ------------------------------------------------------------------ #
    # LLM narration (narration only; never decides the findings)
    # ------------------------------------------------------------------ #
    def _narrate(
        self, asset: MarketingAsset, findings: list[ClaimFinding], outcome: ReviewOutcome
    ) -> str:
        evidence = self._render_evidence(findings)
        prompt = (
            f"Summarise the compliance review of {asset.asset_type.value} '{asset.title}' "
            f"in market {asset.market.value}, vertical {asset.vertical.value}. The "
            f"deterministic rule engine decided the outcome is {outcome.value}. Use ONLY "
            f"the findings below and cite the rule ids you reference.\n\nFINDINGS:\n{evidence}"
        )
        try:
            response = self._llm.generate(
                LlmRequest(
                    messages=(LlmMessage(role="user", content=prompt),),
                    response_schema=_SUMMARY_SCHEMA,
                )
            )
        except Exception:  # noqa: BLE001 - narration is best-effort; the findings stand
            return self._fallback_summary(asset, findings, outcome)
        return self._extract_summary(response.text, asset, findings, outcome)

    @staticmethod
    def _render_evidence(findings: list[ClaimFinding]) -> str:
        lines = []
        for f in findings:
            status = "FAIL" if f.failed else "PASS"
            ev = f" — {f.evidence}" if f.evidence else ""
            lines.append(f"[{f.rule_id}] {status} ({f.severity.value}) {f.message}{ev}")
        return "\n".join(lines) or "(no findings)"

    @classmethod
    def _extract_summary(
        cls,
        text: str,
        asset: MarketingAsset,
        findings: list[ClaimFinding],
        outcome: ReviewOutcome,
    ) -> str:
        try:
            obj = json.loads(text)
            summary = obj.get("summary", "")
            if isinstance(summary, str) and summary:
                return summary
        except (json.JSONDecodeError, AttributeError):
            pass
        return cls._fallback_summary(asset, findings, outcome)

    @staticmethod
    def _fallback_summary(
        asset: MarketingAsset, findings: list[ClaimFinding], outcome: ReviewOutcome
    ) -> str:
        failing = [f for f in findings if f.failed]
        return (
            f"Compliance review of {asset.asset_type.value} '{asset.title}' "
            f"({asset.market.value}/{asset.vertical.value}): {outcome.value} with "
            f"{len(failing)} failing finding(s). Human review required before the asset runs."
        )

    # ------------------------------------------------------------------ #
    # Cross-cutting: guardrail, tracing, audit
    # ------------------------------------------------------------------ #
    def _guard(self, text: str, direction: Direction, actor: str) -> None:
        verdict: GuardrailVerdict = self._guardrail.screen(text, direction)
        if not verdict.allowed:
            self._audit.record(
                AuditEvent(
                    action="review",
                    actor=actor,
                    decision=Decision.BLOCKED,
                    prompt=text if direction is Direction.INPUT else "",
                    response=text if direction is Direction.OUTPUT else "",
                    metadata={"reason": verdict.reason, "direction": direction.value},
                )
            )
            raise GuardrailBlockedError(verdict.reason or "guardrail blocked the request")

    def _span(self, name: str, **attrs: str) -> Any:
        try:
            return self._tracer.span(name, **attrs)
        except Exception:  # noqa: BLE001 - tracing must never break the pipeline
            return nullcontext()

    def _consent_source(self, asset: MarketingAsset, tenant: str, as_of: datetime) -> ConsentSource:
        """Resolve the purposes the asset's audience has ON FILE. Fail-closed and total.

        Three of the four outcomes grant nothing, and each says so rather than looking like
        the fourth:

        * no ``audience_subject_id``: nobody was named, so there is no record to read;
        * no verified tenant: consent records are tenant-owned and a blank tenant owns none,
          so the read is not attempted rather than issued and hoped to come back empty;
        * a subject with no records: the store answered and holds nothing, which is the
          REFUSAL the whole change is for. Silence is never read as permission;
        * records on file: the consent engine resolves which purposes they grant at ``as_of``,
          and those are the only purposes the rule engine will see.

        A store that RAISES is not handled here on purpose. "Consent is not granted" and "the
        consent store could not be read" are different statements, and rendering the second as
        the first would let an outage quietly become a compliance verdict.
        """
        subject = asset.audience_subject_id.strip()
        if not subject:
            return ConsentSource(reason="no audience subject named on the asset")
        if not tenant.strip():
            return ConsentSource(
                subject_id=subject,
                reason="no verified tenant, so no tenant's consent records could be read",
            )
        snapshot = self._consent_store.snapshot(tenant.strip(), subject)
        if not snapshot.records:
            return ConsentSource(
                subject_id=subject,
                reason="the consent store holds no record for this subject",
            )
        return ConsentSource(
            subject_id=subject,
            records_read=len(snapshot.records),
            granted_purposes=self._consent_engine.granted_purposes(snapshot, as_of),
        )

    def _record_review(
        self, review: Review, actor: str, *, rule_pack_version: str, tenant: str
    ) -> None:
        """Audit the review, naming the rule pack and the consent the findings rested on.

        A finding is only as current as the rules it fired, so the audit event carries the
        pack version the provider stamped on the rule set. A provider that could not say
        which revision it served records an empty string, which is visible rather than a
        guess.

        The consent half is recorded the same way, because "this asset is compliant" is a
        claim about a person's permission: the event names how many records were read, which
        purposes they granted, and, when none were, why. The subject appears only as its
        tenant-scoped pseudonym; raw subject ids never enter a durable sink.
        """
        source = review.consent_source
        self._audit.record(
            AuditEvent(
                action="review",
                actor=actor,
                decision=Decision.ESCALATED if review.requires_human_review else Decision.ALLOWED,
                response=review.summary,
                citations=review.citations,
                metadata={
                    "review_id": review.id,
                    "asset_id": review.asset_id,
                    "market": review.market.value,
                    "vertical": review.vertical.value,
                    "outcome": review.outcome.value,
                    "failing": str(len(review.failing_findings)),
                    "rule_pack_version": rule_pack_version,
                    "consent_subject_ref": (
                        subject_ref(tenant, source.subject_id) if source.subject_id else ""
                    ),
                    "consent_records_read": str(source.records_read),
                    "consent_granted_purposes": ",".join(source.granted_purposes),
                    "consent_not_read_because": source.reason,
                },
            )
        )
