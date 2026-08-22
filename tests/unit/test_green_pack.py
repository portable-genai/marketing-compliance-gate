"""The jurisdiction green-claim rule pack: loading, validation and applicability.

Two things are being protected here.

First, the pack is CONFIG (B4). The forbidden wording, the required disclosures and fields,
the required evidence kinds and the evidence-age limits live in a YAML file an adopter can
point elsewhere, and none of them appear as constants in the engines. The tests load the
shipped reference pack and then load a hand-written pack to prove the engines follow the
file rather than a hard-coded jurisdiction.

Second, the pack must be honest and fail closed. Every green-claim rule has to name a real
regulator instrument, and a pack that is missing, malformed, or cites an instrument it never
defines must refuse to load rather than run a green-claims gate with nothing in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketing_compliance_gate.config import Settings
from marketing_compliance_gate.domain.errors import GreenClaimPackError
from marketing_compliance_gate.domain.models import (
    CheckType,
    GreenClaimCategory,
    Market,
    RuleKind,
    Vertical,
)
from marketing_compliance_gate.green_pack import DEFAULT_PACK_PATH, load_pack, pack_for

MARKETS = (Market.JP, Market.AU, Market.SG)


@pytest.fixture(scope="module")
def pack():
    return load_pack()


# --------------------------------------------------------------------------- #
# The shipped reference pack
# --------------------------------------------------------------------------- #
def test_reference_pack_covers_every_market(pack) -> None:
    for market in MARKETS:
        assert pack.phrases_for(market), f"{market.value} has no detection vocabulary"
        assert any(r.market is market for r in pack.rules), f"{market.value} has no rules"
        assert any(m is market for m, _ in pack.requirements), (
            f"{market.value} has no substantiation requirements"
        )


def test_every_rule_is_a_green_claim_rule_with_a_named_instrument(pack) -> None:
    """A rule whose citation cannot be named is worthless to a compliance officer."""
    for rule in pack.rules:
        assert rule.kind is RuleKind.GREEN_CLAIM
        assert rule.citation is not None, f"{rule.id} has no citation"
        assert rule.citation.title.strip(), f"{rule.id} cites an unnamed instrument"
        assert rule.description.strip(), f"{rule.id} has no description"
        assert rule.remediation.strip(), f"{rule.id} tells the marketer nothing to do"


def test_the_pack_uses_all_three_rule_shapes(pack) -> None:
    checks = {rule.check for rule in pack.rules}
    assert CheckType.FORBIDDEN_PHRASE in checks
    assert CheckType.REQUIRED_DISCLOSURE in checks
    assert CheckType.REQUIRED_FIELD in checks


def test_every_requirement_names_evidence_and_remediation(pack) -> None:
    for (market, category), requirement in pack.requirements.items():
        assert requirement.market is market
        assert requirement.category is category
        assert requirement.required_kinds, f"{market.value}/{category.value} requires nothing"
        assert requirement.remediation.strip()


def test_requirements_differ_by_jurisdiction(pack) -> None:
    """The pack is jurisdiction-parameterised, not one global policy wearing three hats."""
    sg_fund = pack.requirement(Market.SG, GreenClaimCategory.ESG_FUND_LABEL)
    au_fund = pack.requirement(Market.AU, GreenClaimCategory.ESG_FUND_LABEL)
    assert sg_fund is not None and au_fund is not None
    assert sg_fund.required_kinds != au_fund.required_kinds


def test_japanese_phrasing_is_only_configured_for_japan(pack) -> None:
    jp = pack.phrases_for(Market.JP)[GreenClaimCategory.CARBON_NEUTRAL]
    au = pack.phrases_for(Market.AU)[GreenClaimCategory.CARBON_NEUTRAL]
    assert "カーボンニュートラル" in jp
    assert "カーボンニュートラル" not in au
    assert "carbon neutral" in jp, "the base vocabulary still applies in Japan"


def test_pack_for_settings_uses_the_reference_pack_by_default() -> None:
    resolved = pack_for(Settings())
    assert resolved.version == load_pack(DEFAULT_PACK_PATH).version
    assert resolved.rules


# --------------------------------------------------------------------------- #
# Applicability: a conditional rule must not fire on an unrelated asset
# --------------------------------------------------------------------------- #
def test_category_scoped_rules_only_apply_when_the_claim_is_made(pack) -> None:
    none_claimed = pack.rules_for(Market.AU, Vertical.BANKING, frozenset())
    carbon_claimed = pack.rules_for(
        Market.AU, Vertical.BANKING, frozenset({GreenClaimCategory.CARBON_NEUTRAL})
    )
    assert {r.id for r in none_claimed} < {r.id for r in carbon_claimed}
    assert all(not r.applies_to_categories for r in none_claimed), (
        "only unconditional rules may apply to an asset that makes no green claim"
    )
    assert "AU-GREEN-OFFSET-BASIS-DISCLOSURE" in {r.id for r in carbon_claimed}


def test_rules_are_scoped_to_their_vertical(pack) -> None:
    categories = frozenset({GreenClaimCategory.ESG_FUND_LABEL})
    banking = {r.id for r in pack.rules_for(Market.SG, Vertical.BANKING, categories)}
    retail = {r.id for r in pack.rules_for(Market.SG, Vertical.ONLINE_RETAIL, categories)}
    assert "SG-GREEN-RETAIL-ESG-FUND-DISCLOSURE" in banking
    assert "SG-GREEN-RETAIL-ESG-FUND-DISCLOSURE" not in retail


def test_rule_selection_is_deterministic(pack) -> None:
    categories = frozenset({GreenClaimCategory.CARBON_NEUTRAL})
    first = pack.rules_for(Market.JP, Vertical.ONLINE_RETAIL, categories)
    second = pack.rules_for(Market.JP, Vertical.ONLINE_RETAIL, categories)
    assert first == second
    assert [r.id for r in first] == sorted(r.id for r in first)


# --------------------------------------------------------------------------- #
# The pack is config: an adopter file changes behaviour with no code change
# --------------------------------------------------------------------------- #
_ADOPTER_PACK = """
version: "adopter-1"
citations:
  house_policy:
    title: "Example Bank green marketing standard (FICTIONAL)"
    source_type: policy
    snippet: "Adopter-owned house policy."
categories:
  carbon_neutral: ["climate friendly"]
requirement_defaults: &defaults
  carbon_neutral:
    required_evidence: [emissions_inventory]
    max_evidence_age_days: 90
    requires_independent_verification: true
    remediation: "Hold a current, assured inventory."
jurisdictions:
  AU:
    requirements:
      <<: *defaults
    rules:
      - id: HOUSE-GREEN-001
        check: forbidden_phrase
        severity: critical
        verticals: [banking]
        description: "House policy forbids this wording."
        patterns: ["climate friendly"]
        remediation: "Remove it."
        citation: house_policy
"""


def test_an_adopter_pack_replaces_the_policy_without_touching_code(tmp_path: Path) -> None:
    path = tmp_path / "house_pack.yaml"
    path.write_text(_ADOPTER_PACK, encoding="utf-8")
    adopter = load_pack(path)
    requirement = adopter.requirement(Market.AU, GreenClaimCategory.CARBON_NEUTRAL)
    assert requirement is not None
    assert requirement.max_evidence_age_days == 90
    phrases = adopter.phrases_for(Market.AU)[GreenClaimCategory.CARBON_NEUTRAL]
    assert phrases == ("climate friendly",)
    assert [r.id for r in adopter.rules] == ["HOUSE-GREEN-001"]


def test_an_adopter_must_choose_an_evidence_age_policy_explicitly(tmp_path: Path) -> None:
    """Omission must not inherit the deliberate no-expiry sentinel."""
    path = tmp_path / "missing-age-policy.yaml"
    path.write_text(_ADOPTER_PACK.replace("    max_evidence_age_days: 90\n", ""), encoding="utf-8")
    with pytest.raises(GreenClaimPackError, match="must be explicit"):
        load_pack(path)


@pytest.mark.parametrize("value", ["-1", "true", '"90"'])
def test_an_adopter_age_policy_is_a_non_negative_integer(tmp_path: Path, value: str) -> None:
    path = tmp_path / "bad-age-policy.yaml"
    path.write_text(
        _ADOPTER_PACK.replace("max_evidence_age_days: 90", f"max_evidence_age_days: {value}"),
        encoding="utf-8",
    )
    with pytest.raises(GreenClaimPackError, match="non-negative integer"):
        load_pack(path)


# --------------------------------------------------------------------------- #
# Fail-closed loading
# --------------------------------------------------------------------------- #
def test_a_missing_pack_is_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(GreenClaimPackError):
        load_pack(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("citation: house_policy", "citation: not_defined_anywhere"),
        ("check: forbidden_phrase", "check: numeric_max"),
        ('carbon_neutral: ["climate friendly"]', 'not_a_category: ["x"]'),
        ("required_evidence: [emissions_inventory]", "required_evidence: []"),
        ("verticals: [banking]", "verticals: []"),
    ],
)
def test_an_invalid_pack_refuses_to_load(tmp_path: Path, mutation: str, reason: str) -> None:
    """Every one of these would otherwise ship a green-claims gate with a hole in it."""
    path = tmp_path / "broken.yaml"
    path.write_text(_ADOPTER_PACK.replace(mutation, reason), encoding="utf-8")
    with pytest.raises(GreenClaimPackError):
        load_pack(path)


def test_a_pack_with_no_jurisdictions_refuses_to_load(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("version: 'x'\ncitations:\n  a:\n    title: 'A'\n", encoding="utf-8")
    with pytest.raises(GreenClaimPackError):
        load_pack(path)
