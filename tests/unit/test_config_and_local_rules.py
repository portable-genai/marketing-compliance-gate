"""Unit tests for config (market profiles / region validation) and the local rule KB.

Pin the generic + APAC promises: every (market, vertical) pair has a seeded rule set,
region/locale are config + seed, a bad region is rejected, and the SQLite FTS5 rule store
round-trips the seeded rules and supports a market/vertical-scoped search.
"""

from __future__ import annotations

import dataclasses

import pytest

from marketing_compliance_gate.adapters.local.rules import LocalRuleProviderAdapter
from marketing_compliance_gate.config import LocalSettings, Settings
from marketing_compliance_gate.domain.errors import UnsupportedMarketError
from marketing_compliance_gate.domain.models import Market, Vertical

CONFIG_PATH = "config/settings.yaml"


def _settings() -> Settings:
    base = Settings.load(CONFIG_PATH)
    return dataclasses.replace(
        base, profile="local", local=LocalSettings(db_path=":memory:", audit_path=":memory:")
    )


def test_settings_refuses_a_configured_empty_interpolated_value(monkeypatch) -> None:
    from hex_service_kit.netdefaults import ConfiguredEmptyError

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
    with pytest.raises(ConfiguredEmptyError, match="GOOGLE_CLOUD_PROJECT"):
        Settings.load(CONFIG_PATH)


def test_market_profiles_cover_all_three_markets_with_regions():
    s = _settings()
    expected = {
        Market.JP: "asia-northeast1",
        Market.AU: "australia-southeast1",
        Market.SG: "asia-southeast1",
    }
    for market, region in expected.items():
        assert s.market_profile(market).region == region
    # JP is bilingual ja+en; AU/SG en.
    assert "ja" in s.market_profile(Market.JP).locales


def test_managed_settings_honor_matching_market_and_region_env(monkeypatch) -> None:
    monkeypatch.setenv("MKT_GOV_PROFILE", "gcp")
    monkeypatch.setenv("MKT_MARKET", "JP")
    monkeypatch.setenv("MKT_GOV_REGION", "asia-northeast1")

    settings = Settings.load(CONFIG_PATH)

    assert settings.active_market is Market.JP
    assert settings.region == "asia-northeast1"


def test_managed_settings_reject_market_region_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("MKT_GOV_PROFILE", "gcp")
    monkeypatch.setenv("MKT_MARKET", "AU")
    monkeypatch.setenv("MKT_GOV_REGION", "asia-southeast1")

    with pytest.raises(UnsupportedMarketError, match="does not match active market"):
        Settings.load(CONFIG_PATH)


def test_region_validation_rejects_unknown_region():
    from marketing_compliance_gate.adapters.gcp._region import resolve_region

    s = dataclasses.replace(_settings(), region="europe-west1")
    with pytest.raises(UnsupportedMarketError):
        resolve_region(s)


@pytest.mark.parametrize("market", list(Market))
@pytest.mark.parametrize("vertical", list(Vertical))
def test_every_market_vertical_has_a_seeded_rule_set(market: Market, vertical: Vertical):
    adapter = LocalRuleProviderAdapter(_settings())
    rule_set = adapter.rule_set(market, vertical)
    assert rule_set.rules, f"no rules seeded for {market.value}/{vertical.value}"
    # Banking is one vertical among others: it must NOT be the only one with rules.
    assert rule_set.market is market and rule_set.vertical is vertical
    assert all(r.market is market and r.vertical is vertical for r in rule_set.rules)


def test_local_rule_store_round_trips_rule_data():
    adapter = LocalRuleProviderAdapter(_settings())
    rule_set = adapter.rule_set(Market.SG, Vertical.ONLINE_RETAIL)
    by_id = {r.id: r for r in rule_set.rules}
    numeric = by_id["SG-RETAIL-DISCOUNT-MAX"]
    assert numeric.limit == 80.0
    assert numeric.field == "discount_pct"
    consent = by_id["SG-RETAIL-CONSENT-PDPA"]
    assert consent.consent_purpose == "marketing"
    forbidden = by_id["SG-RETAIL-CLAIM-LOWEST"]
    assert forbidden.patterns  # patterns survived the round-trip
    assert all(r.citation is not None for r in rule_set.rules)


def test_local_rule_store_search_is_market_vertical_scoped():
    adapter = LocalRuleProviderAdapter(_settings())
    hits = adapter.search(Market.SG, Vertical.BANKING, "guaranteed")
    assert hits.rules
    assert all(r.market is Market.SG and r.vertical is Vertical.BANKING for r in hits.rules)


def test_banking_and_retail_rule_sets_differ():
    """Generic, not bank-only: the retail rule set is genuinely different from banking."""
    adapter = LocalRuleProviderAdapter(_settings())
    bank = {r.id for r in adapter.rule_set(Market.SG, Vertical.BANKING).rules}
    retail = {r.id for r in adapter.rule_set(Market.SG, Vertical.ONLINE_RETAIL).rules}
    assert bank and retail
    assert bank.isdisjoint(retail)
