"""The profile has ONE source of truth, and it fails closed on an unset variable.

The defect this locks out is absence read as consent: ``MKT_GOV_PROFILE`` unset used to
resolve to ``local``, which is the profile that serves seeded dev personas with no
authentication and trusts the localhost dev origins. A drift guard is part of the fix,
because any module that reads the variable directly can reintroduce the whole class with its
own permissive fallback: only ``config.resolve_profile`` may read it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError, cors_allowlist

from marketing_compliance_gate.adapters.local.identity import (
    LocalPersonaIdentityAdapter,
    LocalPersonaProfileError,
)
from marketing_compliance_gate.config import (
    PROFILE_ENV,
    RUNTIME_PROFILES,
    UNCONSENTED_PROFILE,
    Settings,
    resolve_profile,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "marketing_compliance_gate"
_CONFIG = _SRC / "config.py"


def _python_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if p != _CONFIG)


def test_only_the_resolver_reads_the_profile_variable_from_the_environment() -> None:
    offenders = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(os\.environ|os\.getenv)[^\n]*PROFILE", line):
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "these modules re-derive the profile instead of calling config.resolve_profile, so "
        f"an unset {PROFILE_ENV} can again be read as consent:\n" + "\n".join(offenders)
    )


def test_the_resolver_treats_an_absent_variable_as_no_choice() -> None:
    choice = resolve_profile({})
    assert choice.explicit is False
    assert choice.personas_configured is False


@pytest.mark.parametrize("value", ["", "   "])
def test_the_resolver_refuses_a_configured_empty_profile(value: str) -> None:
    with pytest.raises(ConfiguredEmptyError, match=PROFILE_ENV):
        resolve_profile({PROFILE_ENV: value})


def test_an_unconsented_run_is_not_the_local_profile_for_any_relaxation() -> None:
    choice = resolve_profile({})
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.exposure_profile != "local"
    assert UNCONSENTED_PROFILE not in RUNTIME_PROFILES


def test_an_unconsented_run_gets_no_cross_origin_trust() -> None:
    """The relaxation and the restriction fail closed in OPPOSITE directions."""
    choice = resolve_profile({})
    assert cors_allowlist(choice.exposure_profile, origins_env="MKT_GOV_CORS_ORIGINS") == []
    assert cors_allowlist("local", origins_env="MKT_GOV_CORS_ORIGINS") != []
    # The bind guard confines ``local``, so an unconsented run must look like it and stay in.
    assert choice.bind_profile == "local"


def test_a_deliberate_profile_is_carried_through_unchanged() -> None:
    choice = resolve_profile({PROFILE_ENV: "gcp"})
    assert (choice.profile, choice.explicit) == ("gcp", True)
    assert choice.exposure_profile == "gcp"
    assert choice.bind_profile == "gcp"


def test_the_settings_file_may_name_the_profile_when_the_variable_is_unset() -> None:
    choice = resolve_profile({}, configured="onprem")
    assert (choice.profile, choice.explicit) == ("onprem", True)
    # ...but the variable still wins when both are present.
    assert resolve_profile({PROFILE_ENV: "gcp"}, configured="onprem").profile == "gcp"


@pytest.mark.parametrize("value", ["bogus", "Local", "GCP", "LOCAL"])
def test_an_unknown_or_miscapitalised_profile_is_refused_not_normalised(value: str) -> None:
    with pytest.raises(ValueError, match=PROFILE_ENV):
        resolve_profile({PROFILE_ENV: value})
    with pytest.raises(ValueError, match=PROFILE_ENV):
        Settings(profile=value)


def test_the_seeded_personas_refuse_to_serve_an_unconsented_run() -> None:
    """The no-auth identity source is the thing an unset variable must not switch on."""
    with pytest.raises(LocalPersonaProfileError, match=PROFILE_ENV):
        LocalPersonaIdentityAdapter(Settings(profile="local", profile_explicit=False))
    # A deliberate local run is unaffected: the offline demo still works.
    assert LocalPersonaIdentityAdapter(Settings(profile="local")).personas()


def test_the_seeded_personas_refuse_to_serve_a_non_local_profile() -> None:
    with pytest.raises(LocalPersonaProfileError):
        LocalPersonaIdentityAdapter(Settings(profile="gcp"))
