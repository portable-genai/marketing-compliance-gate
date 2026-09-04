"""Service factories — build domain services from the DI container.

One place that wires the ports resolved by :class:`marketing_compliance_gate.config.Container`
into the domain orchestrator, so the CLI, API and agent layers share identical wiring.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Container, build_container
from ..domain.consent_service import ConsentService
from ..domain.services import ReviewService
from ..domain.substantiation import SubstantiationService
from ..green_pack import pack_for


@lru_cache(maxsize=1)
def get_container() -> Container:
    return build_container()


def make_review_service(container: Container | None = None) -> ReviewService:
    container = container or get_container()
    return ReviewService(
        rule_provider=container.rule_provider,
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=container.tracer,
        audit=container.audit,
        review_router=container.review_router,
    )


def make_consent_service(container: Container | None = None) -> ConsentService:
    """Wire the consent and preference store: the store, the market rules, audit,
    human-review-console.

    The rule provider is wired in because a consent decision cites the market's own consent
    rules through the SAME deterministic rule engine the asset-review path uses, so a denial
    a compliance officer reads carries the same authority as a review finding.
    """
    container = container or get_container()
    return ConsentService(
        consent_store=container.consent_store,
        rule_provider=container.rule_provider,
        tracer=container.tracer,
        audit=container.audit,
        review_router=container.review_router,
    )


def make_substantiation_service(container: Container | None = None) -> SubstantiationService:
    """Wire the green-claims gate: the tenant evidence store plus the jurisdiction pack.

    The pack is resolved from settings (the shipped reference pack unless the adopter points
    ``green_claims.pack_path`` at its own), so every driving adapter runs the same
    jurisdiction policy.
    """
    container = container or get_container()
    return SubstantiationService(
        evidence_store=container.evidence_store,
        pack=pack_for(container.settings),
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=container.tracer,
        audit=container.audit,
        review_router=container.review_router,
    )
