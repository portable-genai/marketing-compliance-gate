"""Residency-region validation for the GCP adapter family (D6).

D6 supports the three APAC markets (JP, AU, SG) and each carries a residency region
(``asia-northeast1`` Tokyo, ``australia-southeast1``, ``asia-southeast1`` Singapore). The
region is **selectable at deploy and validated**: a GCP adapter resolves the effective
region from the active market (or the explicit ``settings.region``) and refuses to talk to a
region outside the allow-list, so data never leaves the configured residency boundary.

This module is pure stdlib (no Google import), so importing a GCP adapter under the local /
on-prem / test profile never pulls in ``google-*``. The allow-list is derived from
:data:`marketing_compliance_gate.domain.models.MARKET_PROFILES`, which is itself config + seed
(``config/settings.yaml`` may override a market's region), so regions are never hard-coded
into a branch here.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.errors import UnsupportedMarketError
from ...domain.models import MARKET_PROFILES, Market


def allowed_regions(settings: Settings) -> frozenset[str]:
    """The set of residency regions D6 is permitted to use, from the market profiles."""
    return frozenset(settings.market_profile(m).region for m in MARKET_PROFILES)


def resolve_region(settings: Settings, market: Market | None = None) -> str:
    """Resolve and validate the residency region for a managed-stack call.

    Prefers the requested (or active) market's residency region; falls back to
    ``settings.region``. Raises :class:`UnsupportedMarketError` if the resolved region is
    not in the per-market allow-list, so a GCP adapter can never be pointed at a region
    outside the configured residency boundary (data-residency guarantee for JP/AU/SG).
    """
    region = settings.market_profile(market).region if market is not None else settings.region
    permitted = allowed_regions(settings)
    if region not in permitted:
        allowed = ", ".join(sorted(permitted))
        raise UnsupportedMarketError(
            f"Residency region '{region}' is not in the permitted set for D6 ({allowed}). "
            "Regions are config + seed (per-market profiles); add the market/region to "
            "config/settings.yaml before deploying there."
        )
    return region
