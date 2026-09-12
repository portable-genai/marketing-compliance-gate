"""The gcp profile's rule source is the bundled, versioned pack, and a review over it fires rules.

Until 2026-09-12 the ``gcp`` profile bound ``rule_provider`` to a Gemini File Search store named
``mkt-gov-rule-kb`` that nothing in ``infra/terraform`` provisioned. Every guard on that binding
was green: the adapter constructed, it satisfied the Protocol, its module imported no SDK. None
of them asked whether a review on the deployment would have any rules to evaluate, and it would
not have. The fix binds the profile to the pack bundled in the repository; these tests are the
missing question, asked three ways.

1. **Resolved from the real binding.** The adapter under test is whatever ``config/settings.yaml``
   binds for ``rule_provider`` under ``gcp``, loaded with ``MKT_GOV_PROFILE=gcp``. A test that
   imported the bundled adapter directly would stay green after somebody rebound the profile to
   an empty store again.
2. **Non-empty AND versioned, for every (market, vertical).** A rule set with rules but no
   version would be rules nobody can trace; a version on an empty set would be a label on
   nothing.
3. **A review actually evaluates rules.** The proof is a NON-COMPLIANT asset: its review must
   fail on named rules from the pack. A compliant review proves nothing here, because the
   service would report ``compliant`` over a single harmless rule too, and the planted-defect
   run below shows what the old binding would have produced: ``RuleSetEmptyError`` on every
   review, never a finding.

The managed LLM, guardrail, tracer and audit need live services, so the review is wired with the
SDK-free local adapters for those four and the GCP-bound rule provider for the one under test.
Nothing here touches the network or imports a Google SDK.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from marketing_compliance_gate.adapters.local._seed import RULE_PACK_VERSION
from marketing_compliance_gate.config import Container, LocalSettings, Settings, instantiate
from marketing_compliance_gate.domain.errors import RuleSetEmptyError
from marketing_compliance_gate.domain.models import (
    AssetType,
    Market,
    MarketingAsset,
    ReviewOutcome,
    ReviewRequest,
    RuleSet,
    Vertical,
)
from marketing_compliance_gate.domain.services import ReviewService

CONFIG_PATH = "config/settings.yaml"

#: A version is a date-shaped revision, like the green-claims pack's. Anything else is a label.
_DATE_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The asset a deployed reviewer would paste first: the headline non-compliant SG banking
#: creative the demo, the eval set and the console default all use.
_NON_COMPLIANT = MarketingAsset(
    id="gcp-rule-source-probe",
    asset_type=AssetType.CREATIVE,
    title="SG savings teaser (FICTIONAL, non-compliant)",
    body="Get guaranteed returns of 4.10% with zero risk-free worry!",
    market=Market.SG,
    vertical=Vertical.BANKING,
)

#: The rules that copy must fail on. Named, so a review that fails on SOMETHING is not enough.
_EXPECTED_FAILING = {"SG-BANK-CLAIM-GUARANTEED", "SG-BANK-RISK-WARNING"}


def _gcp_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """The settings a deployment loads: profile gcp, SG market, the SG residency region."""
    monkeypatch.setenv("MKT_GOV_PROFILE", "gcp")
    monkeypatch.setenv("MKT_MARKET", "SG")
    monkeypatch.setenv("MKT_GOV_REGION", "asia-southeast1")
    return Settings.load(CONFIG_PATH)


def _gcp_rule_provider(settings: Settings):  # noqa: ANN202 - whatever the binding resolves to
    """The adapter the gcp profile ACTUALLY binds, resolved the way the container resolves it."""
    dotted = settings.adapters["rule_provider"]["gcp"]
    return instantiate(dotted, settings)


def _local_side_ports(settings: Settings) -> Container:
    """The SDK-free adapters for everything except the rule source."""
    local = dataclasses.replace(
        settings,
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", evidence_path=":memory:"),
    )
    return Container(local)


def _review_service(rule_provider, side: Container) -> ReviewService:  # noqa: ANN001
    return ReviewService(
        rule_provider=rule_provider,
        llm=side.llm,
        guardrail=side.guardrail,
        tracer=side.tracer,
        audit=side.audit,
    )


def test_the_gcp_binding_is_the_bundled_pack_and_imports_no_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The binding names the bundled adapter, and resolving it pulls in no Google package."""
    import sys

    settings = _gcp_settings(monkeypatch)
    dotted = settings.adapters["rule_provider"]["gcp"]
    assert dotted.endswith(":BundledRulePackAdapter"), (
        f"the gcp rule source is {dotted!r}; the deployment provisions no managed rule store, "
        "so the profile must serve the pack bundled in the repository"
    )
    _gcp_rule_provider(settings)
    assert "google" not in sys.modules, "resolving the gcp rule source imported a Google SDK"


@pytest.mark.parametrize("market", list(Market))
@pytest.mark.parametrize("vertical", list(Vertical))
def test_every_scope_is_non_empty_and_versioned_under_gcp(
    monkeypatch: pytest.MonkeyPatch, market: Market, vertical: Vertical
) -> None:
    provider = _gcp_rule_provider(_gcp_settings(monkeypatch))
    rule_set: RuleSet = provider.rule_set(market, vertical)
    scope = f"{market.value}/{vertical.value}"
    assert rule_set.rules, f"the gcp rule source serves no rules for {scope}"
    assert _DATE_VERSION.match(rule_set.version), (
        f"the gcp rule source serves {len(rule_set.rules)} rules for {market.value}/"
        f"{vertical.value} with version {rule_set.version!r}; rules nobody can trace to a "
        "revision are not a rule source"
    )
    assert rule_set.version == RULE_PACK_VERSION
    assert provider.pack_version == RULE_PACK_VERSION
    # The search path serves the same revision, so a governed-tool lookup is traceable too.
    assert provider.search(market, vertical, "consent").version == RULE_PACK_VERSION


def test_the_gcp_rule_source_ignores_the_local_disk_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A managed container must never inherit a stale on-disk index of unknown provenance."""
    settings = _gcp_settings(monkeypatch)
    stale = tmp_path / "stale-rules.db"
    on_disk = dataclasses.replace(settings, local=LocalSettings(db_path=str(stale)))
    provider = _gcp_rule_provider(on_disk)
    assert not stale.exists(), "the gcp rule source wrote an index to the local disk path"
    assert provider.pack_version == RULE_PACK_VERSION


def test_a_review_under_gcp_fails_on_named_rules_from_the_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-compliant probe fails on the pack's rules, and the audit names the pack version.

    This is the proof the old binding could not give: a review that reached the engine with
    rules in hand. The expected failures are named rule ids, not "any failure", and the audit
    event is read back to check the version travelled.
    """
    settings = _gcp_settings(monkeypatch)
    side = _local_side_ports(settings)
    service = _review_service(_gcp_rule_provider(settings), side)

    review = service.review(ReviewRequest(asset=_NON_COMPLIANT), actor="gcp-rule-probe")

    assert review.outcome is ReviewOutcome.NON_COMPLIANT
    failing = {f.rule_id for f in review.failing_findings}
    assert failing >= _EXPECTED_FAILING, f"expected {_EXPECTED_FAILING} to fail, got {failing}"
    assert all(f.citations for f in review.findings), "a finding without a citation"

    events = [e for e in side.audit.read_all() if e["action"] == "review"]
    assert events, "the review wrote no audit event"
    assert events[-1]["metadata"]["rule_pack_version"] == RULE_PACK_VERSION


def test_the_defect_this_guards_against_is_a_refused_review_not_a_passing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the old binding would have produced on the deployment, made explicit.

    An unprovisioned rule store serves an empty rule set. The service refuses rather than
    reporting compliant, which is the right behaviour for the engine and the wrong deployment:
    every review on it would have ended here. Kept as the negative half so the positive test
    above cannot be satisfied by a provider that merely constructs.
    """

    class _UnprovisionedStore:
        def rule_set(self, market, vertical):  # noqa: ANN001
            return RuleSet(market=market, vertical=vertical, rules=())

        def search(self, market, vertical, text):  # noqa: ANN001
            return self.rule_set(market, vertical)

    side = _local_side_ports(_gcp_settings(monkeypatch))
    service = _review_service(_UnprovisionedStore(), side)
    with pytest.raises(RuleSetEmptyError):
        service.review(ReviewRequest(asset=_NON_COMPLIANT), actor="gcp-rule-probe")
