#!/usr/bin/env python3
"""Offline synthetic-data demo for D6 (audit-first).

Runs the real ``ReviewService`` over the local (offline) adapters for a few marketing
assets spanning both verticals (banking + online retail) and the JP/AU/SG markets, then the
real ``SubstantiationService`` (the green-claims gate) over a carbon-neutral campaign and a
retail ESG fund creative, prints a readable, cited trace to stdout, and writes the audit
views to ``scripts/out/*.json`` for the dependency-free renderer / screenshots. It is also
the end-to-end smoke test for the slice: deterministic, so screenshots never drift.

The green-claims part is pinned to a fixed ``as_of`` date, because the verdict depends on how
old the evidence is: a demo that aged evidence against "today" would tell a different story
every quarter.

Usage::

    MKT_GOV_PROFILE=local python scripts/demo.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from marketing_compliance_gate.api.deps import make_review_service
from marketing_compliance_gate.config import Container, LocalSettings, Settings
from marketing_compliance_gate.domain.errors import TenantAccessDeniedError
from marketing_compliance_gate.domain.identity import Principal
from marketing_compliance_gate.domain.models import (
    AssetType,
    Market,
    MarketingAsset,
    ReviewRequest,
    Vertical,
)
from marketing_compliance_gate.domain.serialization import to_jsonable
from marketing_compliance_gate.domain.substantiation import SubstantiationService
from marketing_compliance_gate.green_pack import pack_for

_OUT = Path(__file__).resolve().parent / "out"

# Obviously-fictional synthetic assets: a non-compliant and a compliant case per vertical,
# across all three markets, so the demo shows the rule engine and the maker-checker gate.
_SCENARIOS = [
    MarketingAsset(
        id="sg-bank-bad-promo",
        asset_type=AssetType.CREATIVE,
        title="SG savings teaser (non-compliant)",
        body="Get guaranteed returns of 4.10% with zero risk-free worry!",
        market=Market.SG,
        vertical=Vertical.BANKING,
        fields={},
        granted_consents=(),
    ),
    MarketingAsset(
        id="au-bank-clean-offer",
        asset_type=AssetType.OFFER,
        title="AU home loan (compliant)",
        body="Variable home loan at 6.05% with a comparison rate of 6.20%. Talk to a lender.",
        market=Market.AU,
        vertical=Vertical.BANKING,
        fields={},
        granted_consents=("marketing",),
    ),
    MarketingAsset(
        id="sg-retail-bad-sale",
        asset_type=AssetType.OFFER,
        title="SG mega sale (non-compliant)",
        body="Lowest price guaranteed on everything!",
        market=Market.SG,
        vertical=Vertical.ONLINE_RETAIL,
        fields={"discount_pct": "90"},
        granted_consents=(),
    ),
    MarketingAsset(
        id="jp-retail-clean-loyalty",
        asset_type=AssetType.CREATIVE,
        title="JP loyalty programme (compliant)",
        body="Join our points programme. All prices shown are tax included.",
        market=Market.JP,
        vertical=Vertical.ONLINE_RETAIL,
        fields={"discount_pct": "20", "stock_on_hand": "50"},
        granted_consents=("marketing",),
    ),
]


# The green-claims scenarios. The evidence behind these asset ids is the obviously-fictional
# seed in adapters/local/_seed.py, filed against the ``demo-brand`` tenant.
_AS_OF = date(2026, 8, 5)
_DEMO_PRINCIPAL = Principal(
    subject="demo.reviewer@brand.example", tenant="demo-brand", source="local-persona:analyst"
)
_OTHER_TENANT_PRINCIPAL = Principal(
    subject="user@other-tenant.example", tenant="other-brand", source="local-persona:other-tenant"
)

_GREEN_SCENARIOS = [
    MarketingAsset(
        id="camp-green-au-001",
        asset_type=AssetType.CAMPAIGN,
        title="AU carbon neutral home loan (evidence lapsed)",
        body="Bank with a carbon neutral balance sheet. Offsets are disclosed in our report.",
        market=Market.AU,
        vertical=Vertical.BANKING,
        fields={"substantiation_ref": "dms://example.test/pack/au-001"},
    ),
    MarketingAsset(
        id="camp-green-sg-002",
        asset_type=AssetType.CREATIVE,
        title="SG sustainable fund (fully evidenced)",
        body="Invest in our sustainable fund, built on a published ESG screening strategy.",
        market=Market.SG,
        vertical=Vertical.BANKING,
        fields={
            "substantiation_ref": "dms://example.test/pack/sg-002",
            "esg_fund_disclosure": "prospectus s4.2",
        },
    ),
]


def _settings() -> Settings:
    base = Settings.load("config/settings.yaml")
    settings = Settings(
        project_id=base.project_id,
        region=base.region,
        profile="local",
        vertical=base.vertical,
        market=base.market,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        knowledge_base=base.knowledge_base,
        model_armor=base.model_armor,
        logging=base.logging,
        agent_engine=base.agent_engine,
        green_claims=base.green_claims,
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", evidence_path=":memory:"),
        markets=base.markets,
        adapters=base.adapters,
    )
    return settings


def _service(container: Container):
    """Use the production composition root, including canonical human-review-console routing."""
    return make_review_service(container)


def _green_service(container: Container) -> SubstantiationService:
    return SubstantiationService(
        evidence_store=container.evidence_store,
        pack=pack_for(container.settings),
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=container.tracer,
        audit=container.audit,
        review_router=container.review_router,
    )


def _run_green_claims(container: Container) -> None:
    """The green-claims gate, plus a live demonstration of the tenant boundary."""
    service = _green_service(container)
    for asset in _GREEN_SCENARIOS:
        assessment = service.assess(asset, _DEMO_PRINCIPAL, as_of=_AS_OF)
        print("=" * 78)
        print(f"GREEN CLAIMS: {asset.title}  [{asset.market.value}/{asset.vertical.value}]")
        print(f"  verdict         : {assessment.verdict.value}")
        print(f"  coverage        : {assessment.coverage}  (evidence aged at {assessment.as_of})")
        print(f"  review required : {assessment.requires_human_review}")
        for coverage in assessment.claims:
            print(
                f"  CLAIM {coverage.claim.category.value}: {coverage.verdict.value} "
                f"({coverage.coverage})"
            )
            for gap in coverage.gaps:
                print(f"    gap: {gap}")
        for f in assessment.failing_findings:
            print(f"  FAIL [{f.severity.value}]: {f.rule_id} — {f.message}")
        print(f"  citations       : {len(assessment.citations)}")
        out_path = _OUT / f"{assessment.id}.json"
        out_path.write_text(json.dumps(to_jsonable(assessment), indent=2), encoding="utf-8")
        print(f"  wrote           : {out_path}")

    # The tenant boundary, demonstrated rather than asserted: the SAME asset assessed by a
    # principal from another brand sees only that brand's evidence, and a direct read of this
    # brand's evidence record is refused.
    other = service.assess(_GREEN_SCENARIOS[0], _OTHER_TENANT_PRINCIPAL, as_of=_AS_OF)
    print("=" * 78)
    print("TENANT BOUNDARY (same asset, a different brand's principal)")
    print("  demo-brand      : reads only its own evidence (see the assessment above)")
    print(f"  other-brand     : {other.verdict.value} (coverage {other.coverage})")
    try:
        service.evidence("ev-demo-0001", _OTHER_TENANT_PRINCIPAL)
        print("  cross-tenant read: NOT DENIED (this is a defect)")
    except TenantAccessDeniedError as exc:
        print(f"  cross-tenant read: DENIED (403) — {exc}")


def main() -> int:
    _OUT.mkdir(parents=True, exist_ok=True)
    container = Container(_settings())
    service = _service(container)
    for asset in _SCENARIOS:
        review = service.review(ReviewRequest(asset=asset), actor="demo")
        print("=" * 78)
        print(f"REVIEW: {asset.title}  [{asset.market.value}/{asset.vertical.value}]")
        print(f"  outcome         : {review.outcome.value}")
        print(f"  review required : {review.requires_human_review}")
        for f in review.failing_findings:
            print(f"  FAIL [{f.severity.value}]: {f.rule_id} — {f.message}")
        print(f"  findings        : {len(review.findings)}  citations: {len(review.citations)}")
        out_path = _OUT / f"{review.id}.json"
        out_path.write_text(json.dumps(to_jsonable(review), indent=2), encoding="utf-8")
        print(f"  wrote           : {out_path}")
    _run_green_claims(container)
    print("=" * 78)
    print("Demo complete. Audit views written to scripts/out/ (obviously-fictional data).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
