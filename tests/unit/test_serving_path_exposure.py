"""The loopback bound is a property of the APP OBJECT, not of any entry point.

Red before the fix, proved by execution: the agent had no app-bound exposure guard at all, and
the shipped entry points both hand the app object straight to uvicorn:

    Dockerfile:  CMD exec uvicorn marketing_compliance_gate.api.app:app
                 --host 0.0.0.0 --port ${PORT}
    Makefile:    run-api -> $(BIN)/uvicorn marketing_compliance_gate.api.app:app
                 --host $(API_HOST) ...

A peer on the LAN therefore got ``200`` on ``/v1/personas`` with the full seeded-persona list,
including the approver's groups and tenant, and could then act as any of them by echoing the id
back in ``X-Dev-Persona``. A start-up bind check cannot close that: it is a property of the ONE
entry point that calls it, and neither shipped entry point calls one.

Green after: the guard is registered on the app object at module scope, LAST so it is the
OUTERMOST middleware, and both directions are asserted below. The loopback direction is not a
formality: a guard that broke the offline demo it exists to protect would simply be deleted.

What decides whether the guard stands down is the IDENTITY BINDING and nothing else (see
``ports/identity.py``). The profile names an adapter family, not an authentication scheme, and
no service credential authenticates an end user, so neither may answer the question. The
derivation is asserted here too, including a scan of the guard's own argument, because the
defect this whole file exists for is the kind that passes every offline test: unit tests talk to
loopback, which is the one peer the guard always admits.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from tests.conftest import CONFIG_PATH, LAN_PEER, LOOPBACK_PEER

from marketing_compliance_gate.adapters.gcp.iap_identity import IapIdentityAdapter
from marketing_compliance_gate.adapters.local.identity import LocalPersonaIdentityAdapter
from marketing_compliance_gate.adapters.onprem.identity import OnPremIdentityAdapter
from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps
from marketing_compliance_gate.config import Settings, end_user_auth_kind, identity_adapter_class
from marketing_compliance_gate.ports.identity import (
    CLIENT_ASSERTED,
    END_USER_AUTH_ATTR,
    END_USER_AUTH_KINDS,
    UNIMPLEMENTED,
    VERIFIED,
    declared_end_user_auth,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_SOURCE = _REPO_ROOT / "src" / "marketing_compliance_gate" / "api" / "app.py"

_PROFILE_ENV = "MKT_GOV_PROFILE"
_INSECURE_DEMO_ENV = "MKT_GOV_ALLOW_INSECURE_DEMO"

#: The guard call whose argument must never be derived from a credential.
_GUARD_CALL = "add_loopback_exposure_guard"

#: Anything naming a SERVICE credential. The guard bounds END-USER routes, so none of these may
#: appear in the expression deciding whether it is on, at any depth.
_CREDENTIAL_MARKERS: tuple[str, ...] = ("S2S", "TOKEN", "SECRET", "BEARER")


def _client(peer: tuple[str, int], target: object = None) -> TestClient:
    return TestClient(target if target is not None else app_module.app, client=peer)


# --------------------------------------------------------------------------------------- #
# Both directions, on the wire, against the exact app object the Dockerfile CMD names.
# --------------------------------------------------------------------------------------- #
def test_a_lan_peer_is_refused_the_seeded_persona_list() -> None:
    """The defect itself: 200 with every seeded persona, to a peer with no credential."""
    response = _client(LAN_PEER).get("/v1/personas")
    assert response.status_code == 503
    assert "non-loopback peer" in response.json()["detail"]
    assert "demo.reviewer@brand.example" not in response.text


def test_a_lan_peer_is_refused_the_consequential_route_too() -> None:
    """The persona list was the way in; the governed route was the thing worth reaching."""
    response = _client(LAN_PEER).post("/v1/review", json={}, headers={"X-Dev-Persona": "approver"})
    assert response.status_code == 503


def test_a_loopback_peer_still_gets_the_local_demo() -> None:
    """The guard must not break the offline demo it exists to protect, or it will be reverted."""
    response = _client(LOOPBACK_PEER).get("/v1/personas")
    assert response.status_code == 200
    assert {p["id"] for p in response.json()} >= {"analyst", "approver", "auditor"}
    assert _client(LOOPBACK_PEER).get("/healthz").status_code == 200


def test_the_guard_is_the_outermost_middleware() -> None:
    """Refused BEFORE CORS and before the header baseline, so a refusal leaks nothing.

    Registered last means outermost in Starlette, and outermost is what makes the refusal
    independent of every route, dependency and middleware inside it.
    """
    response = _client(LAN_PEER).get("/v1/personas", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 503
    assert "access-control-allow-origin" not in response.headers
    assert "content-security-policy" not in response.headers


def test_a_forwarding_header_disqualifies_even_a_loopback_peer() -> None:
    """A proxy has already rewritten the scope peer, so the header's presence is disqualifying."""
    response = _client(LOOPBACK_PEER).get("/healthz", headers={"X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 503
    assert "forwarding header" in response.json()["detail"]


def test_only_the_documented_opt_out_restores_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """A RELAXATION, so it fails closed: exactly "1" opts in and nothing else does."""
    for value in ("", "0", "true", " 1 "):
        monkeypatch.setenv(_INSECURE_DEMO_ENV, value)
        assert _client(LAN_PEER).get("/healthz").status_code == 503, value
    monkeypatch.setenv(_INSECURE_DEMO_ENV, "1")
    assert _client(LAN_PEER).get("/healthz").status_code == 200


def test_the_shipped_entry_points_serve_the_app_object() -> None:
    """If this stopped being true, a bound living in an entry point would be enough. It is not."""
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "marketing_compliance_gate.api.app:app" in dockerfile
    assert "--host 0.0.0.0" in dockerfile
    assert "marketing_compliance_gate.api.app:app" in makefile


# --------------------------------------------------------------------------------------- #
# The control: a VERIFYING identity binding stands the guard down. Without it, "the LAN peer is
# refused" would be satisfied by a guard that is simply always on, which is not a service.
# --------------------------------------------------------------------------------------- #
@pytest.fixture
def verifying_identity(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The app module reassembled under the managed profile, whose adapter verifies an assertion.

    Re-imported rather than patched after the fact, because the posture is decided at import:
    that is the whole point of binding it to the app object.
    """
    monkeypatch.setenv(_PROFILE_ENV, "gcp")
    deps.get_container.cache_clear()
    try:
        yield importlib.reload(app_module)
    finally:
        monkeypatch.setenv(_PROFILE_ENV, "local")
        deps.get_container.cache_clear()
        importlib.reload(app_module)


def test_a_verifying_identity_binding_lets_the_service_be_reached(
    verifying_identity: ModuleType,
) -> None:
    """A fronted deployment must stay reachable, or nothing could ever be deployed."""
    assert _client(LAN_PEER, verifying_identity.app).get("/healthz").status_code == 200


def test_but_that_binding_hands_a_lan_peer_no_personas(verifying_identity: ModuleType) -> None:
    """The guard stands down because the ROUTE authenticates, so the route had better do it."""
    response = _client(LAN_PEER, verifying_identity.app).get("/v1/personas")
    assert response.status_code == 200
    assert response.json() == [], "seeded personas must not exist outside the local binding"


# --------------------------------------------------------------------------------------- #
# The derivation: the identity BINDING answers, and nothing else may.
# --------------------------------------------------------------------------------------- #
def _settings(profile: str) -> Settings:
    return Settings(profile=profile, adapters=Settings.load(CONFIG_PATH).adapters)


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (LocalPersonaIdentityAdapter, CLIENT_ASSERTED),
        (IapIdentityAdapter, VERIFIED),
        (OnPremIdentityAdapter, UNIMPLEMENTED),
    ],
)
def test_every_shipped_adapter_declares_what_it_does(adapter: type, expected: str) -> None:
    assert declared_end_user_auth(adapter) == expected
    assert END_USER_AUTH_ATTR in vars(adapter), "the declaration belongs on the class itself"


@pytest.mark.parametrize("profile", ["local", "gcp", "platform", "onprem"])
def test_every_bound_identity_adapter_declares_explicitly(profile: str) -> None:
    """A new adapter must SAY what it does; inheriting the safe default silently is not enough."""
    adapter = identity_adapter_class(_settings(profile))
    assert any(END_USER_AUTH_ATTR in vars(klass) for klass in adapter.__mro__), (
        f"{adapter.__name__} (the {profile} identity binding) declares no {END_USER_AUTH_ATTR}; "
        f"set one of {sorted(END_USER_AUTH_KINDS)} on the class"
    )


class _Undeclared:
    """An adapter that says nothing at all."""


class _Misdeclared:
    """An adapter whose declaration is a typo, which must not read as a verification claim."""

    end_user_auth = "Verified"


@pytest.mark.parametrize("adapter", [_Undeclared, _Misdeclared, object()])
def test_silence_and_typos_read_as_client_asserted(adapter: object) -> None:
    """The fail-closed default, in the only direction that matters: never VERIFIED."""
    assert declared_end_user_auth(adapter) == CLIENT_ASSERTED


def test_the_posture_follows_a_REBOUND_identity_port_not_the_profile_name() -> None:
    """The on-premises migration path: bind a real IdP adapter and the posture changes with it.

    A guard keyed on the word "onprem" would confine a client who wired their own verifying
    adapter to loopback forever, with no way out but the insecure-demo opt-out.
    """
    base = Settings.load(CONFIG_PATH)
    rebound = {port: dict(table) for port, table in base.adapters.items()}
    rebound["identity"]["onprem"] = (
        "marketing_compliance_gate.adapters.gcp.iap_identity:IapIdentityAdapter"
    )
    assert end_user_auth_kind(Settings(profile="onprem", adapters=rebound)) == VERIFIED


def test_an_unresolvable_binding_fails_CLOSED_rather_than_raising_past_the_guard() -> None:
    """A guard that switches off because a lookup raised is a guard that fails open."""
    base = Settings.load(CONFIG_PATH)
    broken = {port: dict(table) for port, table in base.adapters.items()}
    broken["identity"]["local"] = "marketing_compliance_gate.nope:Missing"
    settings = Settings(profile="local", adapters=broken)
    assert end_user_auth_kind(settings) == CLIENT_ASSERTED
    with pytest.raises(ModuleNotFoundError):
        identity_adapter_class(settings)


def test_the_declaration_is_readable_without_constructing_the_adapter() -> None:
    """The seeded-persona adapter REFUSES to construct under an inherited profile.

    A posture computed from an instance would be unobtainable in one of the exact cases it has
    to describe, so the whole derivation reads the class.
    """
    inherited = Settings(
        profile="local", profile_explicit=False, adapters=_settings("local").adapters
    )
    with pytest.raises(Exception):  # noqa: B017 - LocalPersonaProfileError, an IdentityError
        LocalPersonaIdentityAdapter(inherited)
    assert end_user_auth_kind(inherited) == CLIENT_ASSERTED


# --------------------------------------------------------------------------------------- #
# The drift guard: the guard's argument, expanded through the constants it names.
# --------------------------------------------------------------------------------------- #
def guard_posture_source(source: str) -> str:
    """Everything the guard's ``unauthenticated`` argument reaches, as one blob.

    Transitive on purpose. The fleet-wide defect this vocabulary replaced was one indirection
    deep: the call site read ``unauthenticated=_UNCONSENTED or _ZERO_SECRET_LOCAL`` and the
    credential was named a few lines above, so a check that read only the call site passed it.
    """
    tree = ast.parse(source)
    constants = {
        node.targets[0].id: ast.unparse(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    expressions = [
        ast.unparse(kw.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(_GUARD_CALL)
        for kw in node.keywords
        if kw.arg == "unauthenticated"
    ]
    assert expressions, f"no {_GUARD_CALL}(unauthenticated=...) call found"
    seen: set[str] = set()
    reached = list(expressions)
    pending = list(expressions)
    while pending:
        for name in ast.walk(ast.parse(pending.pop(), mode="eval")):
            if isinstance(name, ast.Name) and name.id not in seen:
                seen.add(name.id)
                if name.id in constants:
                    reached.append(constants[name.id])
                    pending.append(constants[name.id])
    return "\n".join(reached + sorted(seen))


def test_the_exposure_guard_reads_no_service_credential() -> None:
    """A credential for SERVICES may never decide a bound on END-USER routes."""
    reached = guard_posture_source(_APP_SOURCE.read_text(encoding="utf-8")).upper()
    offenders = [marker for marker in _CREDENTIAL_MARKERS if marker in reached]
    assert offenders == [], (
        f"the exposure guard's posture reaches {offenders}. Derive it from the identity binding "
        "(config.end_user_auth_kind) instead."
    )


def test_the_exposure_guard_is_derived_from_the_identity_binding() -> None:
    """Not merely "no credential": the posture must come from the thing that actually knows."""
    reached = guard_posture_source(_APP_SOURCE.read_text(encoding="utf-8"))
    assert "end_user_auth_kind" in reached


#: The fleet-wide defect exactly as it was written, one indirection deep. A scanner nobody
#: proved can find anything is a green tick over an empty set.
_MUTANT = (
    "_TOKEN_ENV = 'MKT_GOV_S2S_TOKEN'\n"
    "_ZERO_SECRET_LOCAL = (\n"
    "    _CHOICE.bind_profile == 'local' and not read_env_setting(_TOKEN_ENV).has_value\n"
    ")\n"
    "add_loopback_exposure_guard(app, unauthenticated=_ZERO_SECRET_LOCAL, posture=_EXPOSURE)\n"
)


def test_the_scan_finds_the_defect_it_was_written_for() -> None:
    reached = guard_posture_source(_MUTANT).upper()
    caught = {marker for marker in _CREDENTIAL_MARKERS if marker in reached}
    assert caught == {"S2S", "TOKEN", "SECRET"}, (
        "the scan no longer finds the credential in the expression the defect was written as, "
        "so a green result from it means nothing"
    )
