"""SubstantiationService — the green-claims gate (orchestration, tenant-scoped).

The maker half of the anti-greenwashing gate. Given a marketing asset and the VERIFIED
principal that submitted it, it loads that tenant's substantiation evidence, lets the pure
:class:`~marketing_compliance_gate.domain.coverage_engine.CoverageEngine` decide which green
claims the copy makes and whether the evidence carries them, runs the jurisdiction's
``GREEN_CLAIM`` rules through the existing deterministic :class:`RuleEngine`, and assembles a
cited :class:`SubstantiationAssessment`.

What is code and what is model
------------------------------
The verdict, the coverage number, the claim classification and every rule finding are PURE
CODE. The LLM is handed the already-decided result and asked only for a paragraph a
reviewer can read; if it fails, is blocked, or returns something unusable, a deterministic
fallback sentence is used and the assessment stands unchanged. No model output can move a
verdict or a coverage figure.

Object-level authorization (fail-closed, server-verified)
---------------------------------------------------------
Evidence is tenant-owned. Every read here is gated on ``principal.tenant`` (resolved by the
IdentityPort from the transport, never supplied by the client):

* a principal with no tenant is denied outright,
* listings go to the store with the verified tenant, so they cannot span tenants, and
* a single-record read compares the record's tenant to the principal's and raises
  :class:`TenantAccessDeniedError` (HTTP 403) on a mismatch. It is a denial, not a 404: the
  request was understood and refused, and the refusal is written to the audit log.

Escalation (rule R8)
--------------------
A green claim never publishes on the agent's say-so. Any assessment that makes a green claim
at all, or that fails a green-claim rule, sets ``requires_human_review`` and is routed to the
human-review-console maker-checker console after the durable audit record; the hand-off is
best-effort and
never invalidates an already-audited assessment.

Pure domain code: no Google Cloud, ADK or FastAPI imports.
"""

from __future__ import annotations

import contextlib
import json
from contextlib import nullcontext
from datetime import date
from typing import Any

from .coverage_engine import CoverageEngine
from .errors import EvidenceNotFoundError, GuardrailBlockedError, TenantAccessDeniedError
from .identity import Principal
from .models import (
    AuditEvent,
    Citation,
    ClaimCoverage,
    ClaimFinding,
    Decision,
    Direction,
    GreenClaimPack,
    GuardrailVerdict,
    LlmMessage,
    LlmRequest,
    MarketingAsset,
    RuleSet,
    SubstantiationAssessment,
    SubstantiationEvidence,
    SubstantiationVerdict,
    utcnow,
)
from .rule_engine import RuleEngine

_NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "used_rule_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["narrative"],
}


class SubstantiationService:
    """Assess a marketing asset's green claims against the tenant's evidence on file."""

    def __init__(
        self,
        evidence_store: Any,
        pack: GreenClaimPack,
        llm: Any,
        guardrail: Any,
        tracer: Any,
        audit: Any,
        coverage_engine: CoverageEngine | None = None,
        rule_engine: RuleEngine | None = None,
        review_router: Any = None,
    ) -> None:
        self._evidence = evidence_store
        self._pack = pack
        self._llm = llm
        self._guardrail = guardrail
        self._tracer = tracer
        self._audit = audit
        self._coverage = coverage_engine or CoverageEngine()
        self._rules = rule_engine or RuleEngine()
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Tenant-scoped reads (object-level authorization lives here)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tenant_of(principal: Principal) -> str:
        """The verified tenant, or a denial. Never falls back to a client-supplied value."""
        tenant = (principal.tenant or "").strip()
        if not tenant:
            raise TenantAccessDeniedError(
                "the verified principal carries no tenant; substantiation evidence is "
                "tenant-owned and is refused rather than served without a tenant"
            )
        return tenant

    def evidence_for_asset(
        self, asset_id: str, principal: Principal
    ) -> tuple[SubstantiationEvidence, ...]:
        """List the evidence the principal's OWN tenant holds for ``asset_id``."""
        tenant = self._tenant_of(principal)
        records = tuple(self._evidence.list_for_asset(tenant, asset_id))
        # Defense in depth: the store filters, and we refuse to hand on anything that came
        # back tagged with another tenant regardless.
        return tuple(r for r in records if r.tenant == tenant)

    def evidence(self, evidence_id: str, principal: Principal) -> SubstantiationEvidence:
        """Read ONE evidence record, denied (403) when it belongs to another tenant."""
        tenant = self._tenant_of(principal)
        record = self._evidence.get(evidence_id)
        if record is None:
            raise EvidenceNotFoundError(f"no substantiation evidence with id '{evidence_id}'")
        if record.tenant != tenant:
            self._audit.record(
                AuditEvent(
                    action="evidence_read",
                    actor=principal.actor,
                    decision=Decision.BLOCKED,
                    response="cross-tenant substantiation-evidence read denied",
                    metadata={
                        "evidence_id": evidence_id,
                        "principal_tenant": tenant,
                        "reason": "tenant_mismatch",
                    },
                )
            )
            raise TenantAccessDeniedError(
                f"evidence '{evidence_id}' belongs to another tenant; access denied"
            )
        self._audit.record(
            AuditEvent(
                action="evidence_read",
                actor=principal.actor,
                decision=Decision.ALLOWED,
                response=record.title,
                metadata={"evidence_id": evidence_id, "principal_tenant": tenant},
            )
        )
        return record

    # ------------------------------------------------------------------ #
    # The gate
    # ------------------------------------------------------------------ #
    def assess(
        self,
        asset: MarketingAsset,
        principal: Principal,
        as_of: date | None = None,
    ) -> SubstantiationAssessment:
        """Assess ``asset``'s green claims against the principal's tenant evidence."""
        tenant = self._tenant_of(principal)
        actor = principal.actor or "service"
        effective_as_of = as_of or utcnow().date()
        with self._span(
            "substantiation.assess",
            market=asset.market.value,
            vertical=asset.vertical.value,
        ):
            self._guard(asset.body or asset.title, Direction.INPUT, actor)

            evidence = self.evidence_for_asset(asset.id, principal)
            claims, verdict, coverage = self._coverage.assess(
                asset, self._pack, evidence, effective_as_of
            )
            categories = self._coverage.categories(tuple(c.claim for c in claims))
            applicable = self._pack.rules_for(asset.market, asset.vertical, categories)
            findings = self._rules.check(
                asset, RuleSet(market=asset.market, vertical=asset.vertical, rules=applicable)
            )

            narrative = self._narrate(asset, claims, findings, verdict, coverage)
            assessment = SubstantiationAssessment(
                id=self._assessment_id(asset, tenant),
                asset_id=asset.id,
                tenant=tenant,
                market=asset.market,
                vertical=asset.vertical,
                as_of=effective_as_of.isoformat(),
                verdict=verdict,
                coverage=coverage,
                claims=claims,
                findings=findings,
                narrative=narrative,
                citations=self._merge_citations(claims, findings),
                requires_human_review=self._requires_human_review(verdict, findings),
            )
            self._guard(narrative, Direction.OUTPUT, actor)
            self._record(assessment, actor)
            # Rule R8: hand the escalation to human-review-console AFTER the durable audit record.
            # Routing is
            # best effort and must never invalidate an already-audited assessment.
            if self._review_router is not None and assessment.requires_human_review:
                with contextlib.suppress(Exception):
                    self._review_router.route_assessment(assessment, maker=actor, tenant=tenant)
            return assessment

    # ------------------------------------------------------------------ #
    # Deterministic decisions (pure)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _requires_human_review(
        verdict: SubstantiationVerdict, findings: tuple[ClaimFinding, ...]
    ) -> bool:
        """A green claim, or a failed green-claim rule, always needs a human checker.

        Unlike a routine compliance review this does not wait for a failure: an asset that
        makes ANY environmental claim is escalated even when the evidence fully carries it,
        because signing off a green claim is a judgement a qualified person owns. The only
        assessments that clear without a human are those making no green claim and failing
        no green-claim rule.
        """
        if verdict is not SubstantiationVerdict.NOT_APPLICABLE:
            return True
        return any(f.failed for f in findings)

    @staticmethod
    def _assessment_id(asset: MarketingAsset, tenant: str) -> str:
        return f"green-{tenant}-{asset.market.value}-{asset.id}"

    @staticmethod
    def _merge_citations(
        claims: tuple[ClaimCoverage, ...], findings: tuple[ClaimFinding, ...]
    ) -> tuple[Citation, ...]:
        seen: dict[tuple[str, int | None], Citation] = {}
        for coverage in claims:
            for citation in coverage.citations:
                seen.setdefault((citation.source_id, citation.page), citation)
        for finding in findings:
            for citation in finding.citations:
                seen.setdefault((citation.source_id, citation.page), citation)
        return tuple(seen[key] for key in sorted(seen, key=lambda k: (k[0], k[1] or 0)))

    # ------------------------------------------------------------------ #
    # LLM narration (narration only; never decides a verdict or a number)
    # ------------------------------------------------------------------ #
    def _narrate(
        self,
        asset: MarketingAsset,
        claims: tuple[ClaimCoverage, ...],
        findings: tuple[ClaimFinding, ...],
        verdict: SubstantiationVerdict,
        coverage: float,
    ) -> str:
        prompt = (
            f"Explain, for a marketing compliance reviewer, the green-claim substantiation "
            f"of {asset.asset_type.value} '{asset.title}' in market {asset.market.value}. "
            f"The deterministic coverage engine has ALREADY decided the verdict "
            f"({verdict.value}) and the coverage ({coverage}). Do not restate them as your "
            f"own conclusion, do not compute any number, and use ONLY the evidence below. "
            f"Cite the rule ids you reference.\n\n"
            f"CLAIM COVERAGE:\n{self._render_claims(claims)}\n\n"
            f"GREEN-CLAIM RULES:\n{self._render_findings(findings)}"
        )
        try:
            response = self._llm.generate(
                LlmRequest(
                    messages=(LlmMessage(role="user", content=prompt),),
                    response_schema=_NARRATIVE_SCHEMA,
                )
            )
        except Exception:  # noqa: BLE001 - narration is best-effort; the verdict stands
            return self._fallback_narrative(asset, claims, verdict, coverage)
        return self._extract_narrative(response.text, asset, claims, verdict, coverage)

    @staticmethod
    def _render_claims(claims: tuple[ClaimCoverage, ...]) -> str:
        lines = []
        for coverage in claims:
            gaps = f" gaps: {'; '.join(coverage.gaps)}" if coverage.gaps else ""
            lines.append(
                f"[{coverage.claim.category.value}] '{coverage.claim.phrase}' "
                f"{coverage.verdict.value} coverage={coverage.coverage} "
                f"evidence={', '.join(coverage.evidence_ids) or 'none'}.{gaps}"
            )
        return "\n".join(lines) or "(no green claim detected)"

    @staticmethod
    def _render_findings(findings: tuple[ClaimFinding, ...]) -> str:
        lines = []
        for finding in findings:
            status = "FAIL" if finding.failed else "PASS"
            evidence = f" — {finding.evidence}" if finding.evidence else ""
            lines.append(
                f"[{finding.rule_id}] {status} ({finding.severity.value}) "
                f"{finding.message}{evidence}"
            )
        return "\n".join(lines) or "(no applicable green-claim rule)"

    @classmethod
    def _extract_narrative(
        cls,
        text: str,
        asset: MarketingAsset,
        claims: tuple[ClaimCoverage, ...],
        verdict: SubstantiationVerdict,
        coverage: float,
    ) -> str:
        try:
            obj = json.loads(text)
            narrative = obj.get("narrative", "")
            if isinstance(narrative, str) and narrative:
                return narrative
        except (json.JSONDecodeError, AttributeError):
            pass
        return cls._fallback_narrative(asset, claims, verdict, coverage)

    @staticmethod
    def _fallback_narrative(
        asset: MarketingAsset,
        claims: tuple[ClaimCoverage, ...],
        verdict: SubstantiationVerdict,
        coverage: float,
    ) -> str:
        unsupported = [c for c in claims if not c.substantiated]
        return (
            f"Green-claim substantiation of {asset.asset_type.value} '{asset.title}' "
            f"({asset.market.value}/{asset.vertical.value}): {verdict.value} at coverage "
            f"{coverage} across {len(claims)} claim(s), {len(unsupported)} not fully carried "
            f"by the evidence on file. Human review is required before the asset runs."
        )

    # ------------------------------------------------------------------ #
    # Cross-cutting: guardrail, tracing, audit
    # ------------------------------------------------------------------ #
    def _guard(self, text: str, direction: Direction, actor: str) -> None:
        verdict: GuardrailVerdict = self._guardrail.screen(text, direction)
        if not verdict.allowed:
            self._audit.record(
                AuditEvent(
                    action="substantiation",
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

    def _record(self, assessment: SubstantiationAssessment, actor: str) -> None:
        self._audit.record(
            AuditEvent(
                action="substantiation",
                actor=actor,
                decision=(
                    Decision.ESCALATED if assessment.requires_human_review else Decision.ALLOWED
                ),
                response=assessment.narrative,
                citations=assessment.citations,
                metadata={
                    "assessment_id": assessment.id,
                    "asset_id": assessment.asset_id,
                    "tenant": assessment.tenant,
                    "market": assessment.market.value,
                    "vertical": assessment.vertical.value,
                    "verdict": assessment.verdict.value,
                    "coverage": str(assessment.coverage),
                    "as_of": assessment.as_of,
                    "claims": str(len(assessment.claims)),
                    "failing_rules": str(len(assessment.failing_findings)),
                },
            )
        )
