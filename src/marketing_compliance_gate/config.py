"""Configuration and the adapter factory (dependency injection for the hexagon).

The factory reads ``config/settings.yaml`` (with ``${ENV_VAR}`` interpolation) and binds
each port to a concrete adapter by dotted path. Switching the whole system from the GCP
managed stack to an on-prem stack is a one-line change of ``profile`` (the ports-and-
adapters / no-lock-in principle). Every adapter follows one construction convention:
``Adapter(settings: Settings)``.

D6 is generic and APAC: the active ``vertical`` (banking | online retail) and ``market``
(JP | AU | SG) are settings, and each market's residency ``region`` and locales come from
the per-market profiles (config + seed), never a hard-coded branch.

The profile is resolved ONCE, here, by :func:`resolve_profile`, and an absent
``MKT_GOV_PROFILE`` is NO CHOICE rather than a silent ``local``. That distinction is the
whole point: ``local`` serves seeded dev personas with no authentication and trusts the
localhost dev origins, so reading a missing variable as ``local`` turns a deployment mistake
into an unauthenticated service. Every posture decision keys off one of the members of
:class:`ProfileChoice`, and no other module may re-derive the profile from the environment
(``tests/unit/test_profile_single_source.py`` fails the build if one does).
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from hex_service_kit.netdefaults import ConfiguredEmptyError, EnvSetting, read_env_setting

from .domain.errors import UnsupportedMarketError
from .domain.models import MARKET_PROFILES, Market, MarketProfile, Vertical
from .envread import setting_or_default

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")

#: The ONE variable that selects the deployment profile. Only :func:`resolve_profile` reads it.
PROFILE_ENV = "MKT_GOV_PROFILE"

#: Every profile the adapter table binds. The comparison against it is exact and
#: case-sensitive: ``Local`` selects none of the ``local`` relaxations but also none of its
#: restrictions, so normalising the case would turn a typo into a silent choice.
RUNTIME_PROFILES = frozenset({"local", "gcp", "platform", "onprem"})

#: The profile string handed to every RELAXATION when no profile was chosen at all. It is
#: deliberately NOT a member of :data:`RUNTIME_PROFILES` and never reaches the adapter table:
#: it exists so that "nobody chose" is a distinct input to the security layers rather than
#: being indistinguishable from a deliberate ``local``.
UNCONSENTED_PROFILE = "unconfigured"


def _validate_profile(profile: str) -> str:
    """Fail closed on a profile string nothing binds, INCLUDING a capitalisation typo."""
    if profile not in RUNTIME_PROFILES:
        expected = ", ".join(sorted(RUNTIME_PROFILES))
        raise ValueError(f"unknown {PROFILE_ENV} {profile!r}; expected one of: {expected}")
    return profile


@dataclass(frozen=True, slots=True)
class ProfileChoice:
    """The ONE resolution of the profile, and what each consumer must key off.

    The derived profile strings differ because the decisions fail closed in OPPOSITE
    directions, so a single "effective profile" would harden one and weaken the other.
    """

    #: Which adapter family to bind. Absent consent this is still ``local`` (the SDK-free
    #: adapters), because the alternative would import Google Cloud SDKs that are not
    #: installed; the seeded-persona identity adapter refuses to construct when
    #: :attr:`explicit` is False, so an unconsented run has data adapters but no identity.
    profile: str = "local"
    #: Was the profile named DELIBERATELY (the variable, or the settings file, said so)?
    explicit: bool = True

    @property
    def exposure_profile(self) -> str:
        """The profile every RELAXATION keys off: the CORS allowlist, the persona picker.

        These grant something extra to ``local``, so an unconsented run must NOT look like
        ``local``: it gets :data:`UNCONSENTED_PROFILE`, which is no origin's allowlist.
        """
        return self.profile if self.explicit else UNCONSENTED_PROFILE

    @property
    def bind_profile(self) -> str:
        """The profile a RESTRICTION keys off, where ``local`` is the confining case.

        The loopback bind guard confines ``local`` and lets fronted profiles take
        ``0.0.0.0``, so here an unconsented run must look like ``local`` and stay confined.
        """
        return self.profile if self.explicit else "local"

    @property
    def personas_configured(self) -> bool:
        """May the no-auth seeded dev personas be served at all?

        False means no profile was chosen, so nobody consented to an identity source that
        authenticates nobody. The adapter refuses to construct and the API answers 401.
        """
        return self.explicit


def _profile_setting(environ: Mapping[str, str] | None) -> EnvSetting:
    """Return the single profile choice with absent and configured-empty kept distinct."""
    if environ is None:
        return read_env_setting(PROFILE_ENV)
    raw = environ.get(PROFILE_ENV)
    return EnvSetting(name=PROFILE_ENV, raw=raw, value="" if raw is None else raw.strip())


def resolve_profile(
    environ: Mapping[str, str] | None = None, *, configured: str = ""
) -> ProfileChoice:
    """Resolve the profile in three states: unset, set-and-empty, set-and-valid.

    ``MKT_GOV_PROFILE`` wins; naming a profile in ``config/settings.yaml`` is an equally
    deliberate choice and is honoured next. An absent variable is NO CHOICE. A present-but-blank
    variable is a configuration error, never an instruction to inherit settings. A value that
    IS present is validated here rather than at first use, so an unknown or
    mis-capitalised profile fails the process rather than serving from a string nothing binds.
    """
    setting = _profile_setting(environ)
    if setting.is_configured_empty:
        raise ConfiguredEmptyError(
            f"{PROFILE_ENV} is set to an empty value, which is not a profile. Unset it to "
            "leave the choice to settings.yaml, or set one of "
            f"{', '.join(sorted(RUNTIME_PROFILES))}."
        )
    if setting.has_value:
        return ProfileChoice(profile=_validate_profile(setting.value), explicit=True)
    settled = configured.strip()
    if settled:
        return ProfileChoice(profile=_validate_profile(settled), explicit=True)
    return ProfileChoice(profile="local", explicit=False)


def _interpolate(value: Any) -> Any:
    """Interpolate settings while keeping absent and configured-empty distinct."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            setting = read_env_setting(m.group(1))
            if setting.is_configured_empty:
                raise ConfiguredEmptyError(
                    f"{m.group(1)} is set to an empty value; unset it to inherit the reviewed "
                    "settings default, or give it a value"
                )
            return (m.group(2) or "") if setting.is_unset else setting.value

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


@dataclass(frozen=True)
class ModelSettings:
    #: The Vertex location the model client calls, NOT the compute region. Gemini 3
    #: serves the `us` and `eu` multi-regions only; `global` carries no residency
    #: guarantee. See models.location in config/settings.yaml.
    location: str = "us"
    reasoning: str = "gemini-3.5-flash"
    triage: str = "gemini-3.5-flash"
    hard_reasoning: str = "gemini-3.5-flash"  # Preview — feature-flagged off by default
    use_hard_reasoning: bool = False


@dataclass(frozen=True)
class KnowledgeBaseSettings:
    """The per-market/vertical compliance rule KB (Gemini File Search / A2)."""

    base_url_env: str = "KNOWLEDGE_BASE_URL"
    data_store_id: str = "mkt-gov-rule-kb"  # only used by the standalone GCP adapter
    location: str = "asia-southeast1"
    top_k: int = 10


@dataclass(frozen=True)
class ModelArmorSettings:
    template_id: str = "mkt-gov-guardrail"
    host: str = "modelarmor.asia-southeast1.rep.googleapis.com"


@dataclass(frozen=True)
class LoggingSettings:
    log_name: str = "marketing-compliance-gate-audit"
    bucket: str = "marketing-compliance-gate-worm"
    retention_days: int = 2557  # ~7 years


@dataclass(frozen=True)
class AgentEngineSettings:
    resource_name: str = ""  # reasoningEngine resource id, set after deploy
    display_name: str = "marketing-compliance-gate"


@dataclass(frozen=True)
class GreenClaimSettings:
    """Where the jurisdiction green-claim rule pack comes from.

    ``pack_path`` empty selects the reference pack shipped inside the package
    (``marketing_compliance_gate/rulepacks/green_claims.yaml``). An adopter tightens its own
    green-claim policy by pointing this at its own file: the forbidden wording, the required
    evidence kinds and the evidence-age limits are config, never engine code (B4).
    """

    pack_path: str = ""


@dataclass(frozen=True)
class LocalSettings:
    """Paths for the SDK-free ``local`` profile stores (SQLite FTS5 + append-only audit).

    Empty strings select the per-package default under ``~/.marketing_compliance_gate/``; tests
    pass ``:memory:`` for ephemeral, deterministic stores. No Google Cloud here.
    """

    db_path: str = ""  # SQLite FTS5 compliance rule-KB index
    audit_path: str = ""  # append-only audit store
    seed_path: str = ""  # rule-set seed JSON ("" => bundled fictional seed)
    evidence_path: str = ""  # SQLite substantiation-evidence store
    consent_path: str = ""  # SQLite consent and preference store


# Per-market residency region overrides (region/locale are config + seed, not hard-coded).
@dataclass(frozen=True)
class MarketOverride:
    region: str = ""
    locales: tuple[str, ...] = ()
    currency: str = ""


@dataclass(frozen=True)
class Settings:
    project_id: str = "your-gcp-project"
    region: str = "asia-southeast1"  # default residency region; per-market profile overrides
    # gcp | local | platform | onprem. There is NO default: see ``resolve_profile``, where
    # an unset MKT_GOV_PROFILE is no choice rather than a silent ``local``.
    profile: str = "local"
    vertical: str = "banking"  # banking | online_retail (the active vertical)
    market: str = "SG"  # JP | AU | SG (the active market)
    grounding_enabled: bool = False
    models: ModelSettings = field(default_factory=ModelSettings)
    knowledge_base: KnowledgeBaseSettings = field(default_factory=KnowledgeBaseSettings)
    model_armor: ModelArmorSettings = field(default_factory=ModelArmorSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    agent_engine: AgentEngineSettings = field(default_factory=AgentEngineSettings)
    green_claims: GreenClaimSettings = field(default_factory=GreenClaimSettings)
    local: LocalSettings = field(default_factory=LocalSettings)
    # Per-market residency overrides keyed by market code, e.g. {"JP": {"region": "..."}}.
    markets: dict[str, MarketOverride] = field(default_factory=dict)
    # port_name -> { profile -> "module.path:ClassName" }
    adapters: dict[str, dict[str, str]] = field(default_factory=dict)
    # Was the profile chosen DELIBERATELY, or merely inherited from the fallback? ``load``
    # sets this False when neither MKT_GOV_PROFILE nor the settings file named one. Direct
    # construction is deliberate by definition (a caller named the profile in code), so the
    # default is True. The seeded-persona identity adapter refuses to serve when this is
    # False: an offline demo identity must never be handed out because a variable went
    # missing.
    profile_explicit: bool = True

    def __post_init__(self) -> None:
        _validate_profile(self.profile)
        if self.profile == "gcp":
            expected = self.market_profile().region
            if self.region != expected:
                raise UnsupportedMarketError(
                    f"managed region {self.region!r} does not match active market "
                    f"{self.market!r} residency region {expected!r}"
                )

    @property
    def profile_choice(self) -> ProfileChoice:
        """The resolved profile as the security layers must see it (never the raw string)."""
        return ProfileChoice(profile=self.profile, explicit=self.profile_explicit)

    @property
    def exposure_profile(self) -> str:
        """The profile every relaxation keys off (see :meth:`ProfileChoice.exposure_profile`)."""
        return self.profile_choice.exposure_profile

    @property
    def bind_profile(self) -> str:
        """The profile every restriction keys off (see :meth:`ProfileChoice.bind_profile`)."""
        return self.profile_choice.bind_profile

    # ------------------------------------------------------------------ #
    # Convenience accessors (validated, config-driven; never hard-coded)
    # ------------------------------------------------------------------ #
    @property
    def active_vertical(self) -> Vertical:
        return Vertical(self.vertical)

    @property
    def active_market(self) -> Market:
        return Market(self.market)

    def market_profile(self, market: Market | None = None) -> MarketProfile:
        """Resolve a market's residency region / locales, applying any settings override."""
        market = market or self.active_market
        base = MARKET_PROFILES[market]
        override = self.markets.get(market.value)
        if override is None:
            return base
        return MarketProfile(
            market=base.market,
            region=override.region or base.region,
            display_name=base.display_name,
            locales=override.locales or base.locales,
            currency=override.currency or base.currency,
        )

    @staticmethod
    def load(path: str | os.PathLike[str] | None = None) -> Settings:
        path = Path(path or setting_or_default("MKT_GOV_SETTINGS", "config/settings.yaml"))
        raw = _interpolate(yaml.safe_load(path.read_text())) if path.exists() else {}
        raw = raw or {}
        markets_raw = raw.pop("markets", {}) or {}
        markets = {
            str(code): MarketOverride(
                region=str(spec.get("region", "")),
                locales=tuple(spec.get("locales", []) or ()),
                currency=str(spec.get("currency", "")),
            )
            for code, spec in markets_raw.items()
            if isinstance(spec, dict)
        }
        nested: dict[str, Any] = {
            "models": ModelSettings(**(raw.pop("models", {}) or {})),
            "knowledge_base": KnowledgeBaseSettings(**(raw.pop("knowledge_base", {}) or {})),
            "model_armor": ModelArmorSettings(**(raw.pop("model_armor", {}) or {})),
            "logging": LoggingSettings(**(raw.pop("logging", {}) or {})),
            "agent_engine": AgentEngineSettings(**(raw.pop("agent_engine", {}) or {})),
            "green_claims": GreenClaimSettings(**(raw.pop("green_claims", {}) or {})),
            "local": LocalSettings(**(raw.pop("local", {}) or {})),
            "markets": markets,
        }
        choice = resolve_profile(configured=str(raw.pop("profile", "") or ""))
        vertical = setting_or_default("MKT_VERTICAL", str(raw.pop("vertical", "banking")))
        market = setting_or_default("MKT_MARKET", str(raw.pop("market", "SG")))
        known = {f for f in Settings.__dataclass_fields__ if f not in nested}
        flat: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        flat.pop("profile_explicit", None)  # a settings FILE cannot manufacture consent
        return Settings(
            profile=choice.profile,
            profile_explicit=choice.explicit,
            vertical=vertical,
            market=market,
            **flat,
            **nested,
        )


def instantiate(dotted: str, settings: Settings) -> Any:
    """Import ``module.path:ClassName`` and construct it with ``settings``."""
    module_path, _, class_name = dotted.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(settings)


class Container:
    """Lazily-built registry of port -> adapter instances.

    Adapters are imported only on first access so that, e.g., a unit test using the
    on-prem or local profile never needs the Google Cloud SDKs installed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _bind(self, port_name: str) -> Any:
        binding = self.settings.adapters.get(port_name, {})
        dotted = binding.get(self.settings.profile)
        if not dotted:
            raise KeyError(
                f"No adapter configured for port '{port_name}' "
                f"under profile '{self.settings.profile}'."
            )
        return instantiate(dotted, self.settings)

    # One cached_property per port keeps wiring declarative and type-greppable.
    @cached_property
    def rule_provider(self) -> Any:
        return self._bind("rule_provider")

    @cached_property
    def evidence_store(self) -> Any:
        return self._bind("evidence_store")

    @cached_property
    def consent_store(self) -> Any:
        return self._bind("consent_store")

    @cached_property
    def llm(self) -> Any:
        return self._bind("llm")

    @cached_property
    def guardrail(self) -> Any:
        return self._bind("guardrail")

    @cached_property
    def audit(self) -> Any:
        return self._bind("audit")

    @cached_property
    def tracer(self) -> Any:
        return self._bind("tracer")

    @cached_property
    def evaluation(self) -> Any:
        return self._bind("evaluation")

    @cached_property
    def agent_registry(self) -> Any:
        return self._bind("agent_registry")

    @cached_property
    def tool_catalog(self) -> Any:
        return self._bind("tool_catalog")

    @cached_property
    def identity(self) -> Any:
        return self._bind("identity")

    @cached_property
    def review_router(self) -> Any:
        return self._bind("review_router")


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings.load())


def identity_adapter_class(settings: Settings) -> type:
    """The identity adapter CLASS the active binding names, resolved WITHOUT constructing it.

    Reads the same ``adapters:`` entry :meth:`Container._bind` binds from, so a deployment that
    rebound identity in ``config/settings.yaml`` (the documented on-premises path: swap the
    placeholder for the client's own IdP adapter) is answered about the adapter it ACTUALLY
    runs, not about the one its profile name suggests. A missing binding raises rather than
    falling back, matching ``_bind``.

    Constructing is deliberately avoided: the seeded-persona adapter REFUSES to construct
    under an inherited profile, so a posture computed from an instance would be unobtainable
    in one of the exact cases it has to describe.
    """
    binding = settings.adapters.get("identity", {})
    dotted = binding.get(settings.profile)
    if not dotted:
        raise KeyError(f"No adapter configured for port 'identity' under {settings.profile!r}.")
    module_path, _, class_name = dotted.partition(":")
    resolved = getattr(importlib.import_module(module_path), class_name)
    if not isinstance(resolved, type):
        raise TypeError(f"identity binding {dotted!r} does not name a class")
    return resolved


def end_user_auth_kind(settings: Settings | None = None) -> str:
    """What the BOUND identity adapter declares it does for END-USER authentication.

    This is the one question "are this service's end-user routes authenticated?" reduces to.
    See ``ports/identity.py``: neither the profile string nor the presence of any service
    credential can answer it.

    Any failure to establish the answer resolves to ``CLIENT_ASSERTED``. A guard that switched
    OFF because a lookup raised would be a guard that fails open, and nothing is lost by
    failing closed here: the same failure surfaces loudly at the first request, when the
    container resolves the identical binding for real.
    """
    from .ports.identity import CLIENT_ASSERTED, declared_end_user_auth

    try:
        return declared_end_user_auth(identity_adapter_class(settings or Settings.load()))
    except Exception:  # noqa: BLE001 - an unanswerable posture must fail CLOSED, never open
        return CLIENT_ASSERTED
