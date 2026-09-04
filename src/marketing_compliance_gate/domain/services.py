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
      -> rule_engine.check(asset, rule_set)          (claim / permission / brand)
      -> rule_engine.consent_checks(asset, rule_set) (consent)
      -> decide outcome + requires_human_review      (pure)
      -> llm.generate(summary narrative)             (narration only, over findings)
      -> assemble Review (+ pending ApprovalRecord)
      -> guardrail.screen(OUTPUT over the summary)   [blocked -> audit BLOCKED + raise]
      -> audit.record

``approve`` is the checker half of maker-checker: it records a human's terminal decision
on a previously-built review and writes it to the audit log.

Pure domain code: no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

import contextlib
import json
from contextlib import nullcontext
from typing import Any

from .errors import GuardrailBlockedError, RuleSetEmptyError
from .models import (
    ApprovalDecision,
    ApprovalRecord,
    AuditEvent,
    ClaimFinding,
    ConsentCheck,
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
        engine: RuleEngine | None = None,
        review_router: Any = None,
    ) -> None:
        self._rules = rule_provider
        self._llm = llm
        self._guardrail = guardrail
        self._tracer = tracer
        self._audit = audit
        self._engine = engine or RuleEngine()
        # Rule R8: when a review requires human review it is routed to the human-review-console
        # maker-checker
        # console, not left as a boolean. Optional so unit tests and the CLI can omit it; when
        # unset the escalation still audits ESCALATED, it just is not forwarded to a console.
        # Bound only on this maker (review-producer) path; the agent never gets an approve tool.
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Public API — the maker half
    # ------------------------------------------------------------------ #
    def review(self, request: ReviewRequest, actor: str, tenant: str = "") -> Review:
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

            findings = list(self._engine.check(asset, rule_set))
            consent_checks, consent_findings = self._engine.consent_checks(asset, rule_set)
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
            )
            self._guard(summary, Direction.OUTPUT, actor)
            self._record_review(review, actor)
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

    def _record_review(self, review: Review, actor: str) -> None:
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
                },
            )
        )
