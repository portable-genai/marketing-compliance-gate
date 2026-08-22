"""API-boundary tests for the consent and preference store.

Proves the HTTP contract the store adds:

* the tenant comes from the VERIFIED principal, so the other tenant's persona sees nothing
  for a subject it does not own and is refused (403) on a record it does not own,
* an unknown consent state answers DENIED rather than erroring or allowing,
* a grant nobody can evidence is accepted, reported as requiring human review, and grants
  nothing until a checker confirms it through the confirm route, and
* a suppression written over the API takes effect on the very next decision.

The container is an in-memory ``local`` container injected by monkeypatching the cached
``get_container`` factory in all three module namespaces that imported it, so the whole test
stays offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps, security
from marketing_compliance_gate.config import Container, LocalSettings, Settings

CONFIG_PATH = "config/settings.yaml"


def _now() -> str:
    """The instant to evaluate a decision at: read per call, never pinned to a literal.

    Suppressions and send events written over the API are stamped with the real clock, so
    a decision must be evaluated at or after the write to see it. Two things follow, and
    this file has been bitten by both. A hard-coded date rots: it was written on 2026-08-08
    with ``as_of`` pinned to that same morning and went red on 2026-08-09, one day later,
    with no code change behind it. A module-level constant is not enough either, because it
    is captured at import and every write in a test happens after that.

    The seeded consent runs to 2030, so "now" stays inside every seeded window.
    """
    return datetime.now(UTC).isoformat()


OTHER_TENANT_PERSONA = {"X-Dev-Persona": "other-tenant"}


def _local_container() -> Container:
    base = Settings.load(CONFIG_PATH)
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
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            evidence_path=":memory:",
            consent_path=":memory:",
        ),
        markets=base.markets,
        adapters=base.adapters,
    )
    return Container(settings)


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch) -> Container:
    c = _local_container()
    monkeypatch.setattr(deps, "get_container", lambda: c)
    monkeypatch.setattr(security, "get_container", lambda: c)
    monkeypatch.setattr(app_module, "get_container", lambda: c)
    return c


@pytest.fixture
def client(container: Container) -> TestClient:
    return TestClient(app_module.app, client=LOOPBACK_PEER)


def _decision(client: TestClient, subject: str, headers: dict | None = None, **extra):
    body = {
        "subject_id": subject,
        "purpose": "marketing",
        "channel": "email",
        "market": "SG",
        "vertical": "banking",
        "as_of": _now(),
    }
    body.update(extra)
    return client.post("/v1/consent/decision", json=body, headers=headers or {})


def test_a_seeded_subject_is_allowed_and_carries_a_decision_id(client: TestClient) -> None:
    resp = _decision(client, "subj-000101")
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "allowed"
    assert body["id"].startswith("consent-")
    assert body["citations"], "an allowed decision still cites the rules it satisfied"


@pytest.mark.parametrize(
    ("subject", "reason"),
    [
        ("subj-000102", "consent_withdrawn"),
        ("subj-000103", "consent_expired"),
        ("subj-000104", "suppressed"),
        ("subj-000105", "channel_preference_unknown"),
        ("subj-000106", "consent_pending_review"),
        ("subj-000000", "consent_unknown"),
    ],
)
def test_denials_answer_200_with_the_reason_rather_than_erroring(
    client: TestClient, subject: str, reason: str
) -> None:
    """A denial is an ANSWER, not a failure: the caller gets the reason and the citations."""
    resp = _decision(client, subject)
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "denied"
    assert reason in body["reasons"]


def test_a_bad_channel_is_a_422(client: TestClient) -> None:
    resp = _decision(client, "subj-000101", channel="carrier-pigeon")
    assert resp.status_code == 422


def test_the_tenant_comes_from_the_principal_not_the_request(client: TestClient) -> None:
    """The other tenant's persona cannot ask about demo-brand's subject and get an answer."""
    mine = _decision(client, "subj-000101").json()
    theirs = _decision(client, "subj-000101", headers=OTHER_TENANT_PERSONA).json()
    assert mine["outcome"] == "allowed"
    assert theirs["outcome"] == "denied"
    assert "consent_unknown" in theirs["reasons"]
    assert theirs["tenant"] == "other-brand"


def test_a_cross_tenant_record_read_is_403(client: TestClient) -> None:
    assert client.get("/v1/consent/records/cr-demo-0001").status_code == 200
    denied = client.get("/v1/consent/records/cr-demo-0001", headers=OTHER_TENANT_PERSONA)
    assert denied.status_code == 403


def test_a_missing_record_is_404(client: TestClient) -> None:
    assert client.get("/v1/consent/records/cr-not-here").status_code == 404


def test_the_snapshot_route_is_tenant_scoped(client: TestClient) -> None:
    mine = client.get("/v1/consent/subjects/subj-000101").json()
    theirs = client.get("/v1/consent/subjects/subj-000101", headers=OTHER_TENANT_PERSONA).json()
    assert mine["records"]
    assert theirs["records"] == []


def test_an_unevidenced_grant_is_pending_and_grants_nothing_until_confirmed(
    client: TestClient,
) -> None:
    write = client.post(
        "/v1/consent/records",
        json={
            "id": "cr-api-0001",
            "subject_id": "subj-000401",
            "purpose": "marketing",
            "status": "granted",
            "basis": "legitimate_interest",
            "expires_at": "2030-01-01T00:00:00+00:00",
        },
    )
    assert write.status_code == 200
    assert write.json()["status"] == "pending_review"
    assert write.json()["requires_human_review"] is True

    # The subject has an opted-in channel, so the ONLY thing denying is the pending grant.
    client.post(
        "/v1/consent/preferences",
        json={
            "id": "cp-api-0001",
            "subject_id": "subj-000401",
            "channel": "email",
            "opted_in": True,
        },
    )
    before = _decision(client, "subj-000401").json()
    assert before["outcome"] == "denied"
    assert "consent_pending_review" in before["reasons"]

    confirmed = client.post(
        "/v1/consent/records/cr-api-0001/confirm",
        json={"approved": True, "rationale": "signed form located (FICTIONAL)"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "granted"
    assert _decision(client, "subj-000401").json()["outcome"] == "allowed"


def test_a_suppression_written_over_the_api_takes_effect_immediately(
    client: TestClient,
) -> None:
    assert _decision(client, "subj-000101").json()["outcome"] == "allowed"
    resp = client.post(
        "/v1/consent/suppressions",
        json={
            "id": "sup-api-0001",
            "subject_id": "subj-000101",
            "scope": "all",
            "reason": "complaint",
        },
    )
    assert resp.status_code == 200
    after = _decision(client, "subj-000101").json()
    assert after["outcome"] == "denied"
    assert "suppressed" in after["reasons"]


# --------------------------------------------------------------------------- #
# The service-to-service intake (what consent-preference-kit talks to)
# --------------------------------------------------------------------------- #
def _service_body(**overrides: object) -> dict:
    """The S2S intake body, with `as_of` read at call time (see :func:`_now`)."""
    return {
        "tenant": "demo-brand",
        "subject_id": "subj-000101",
        "purpose": "marketing",
        "channel": "email",
        "market": "SG",
        "vertical": "banking",
        "as_of": _now(),
        **overrides,
    }


def test_the_service_intake_answers_for_the_asserted_tenant(client: TestClient) -> None:
    """Under a DELIBERATE local profile with no secret set, the offline posture is open."""
    resp = client.post("/v1/service/consent/decision", json=_service_body())
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "allowed"
    assert resp.json()["tenant"] == "demo-brand"


def test_the_service_intake_still_isolates_tenants(client: TestClient) -> None:
    """An asserted tenant is trusted for WHO is calling, never for what it may read.

    The calling service names its own tenant, and the store answers only from that tenant's
    rows: naming demo-brand's subject under other-brand's tenant reads nothing and denies.
    """
    body = _service_body(tenant="other-brand")
    denied = client.post("/v1/service/consent/decision", json=body).json()
    assert denied["outcome"] == "denied"
    assert "consent_unknown" in denied["reasons"]


def test_the_service_intake_refuses_a_blank_tenant(client: TestClient) -> None:
    body = _service_body(tenant="  ")
    assert client.post("/v1/service/consent/decision", json=body).status_code == 403


def test_a_configured_service_secret_is_enforced(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the shared secret configured, the bearer is checked in constant time."""
    monkeypatch.setenv("MKT6_S2S_TOKEN", "s3cr3t-fictional")
    assert client.post("/v1/service/consent/decision", json=_service_body()).status_code == 401
    wrong = client.post(
        "/v1/service/consent/decision",
        json=_service_body(),
        headers={"Authorization": "Bearer nope"},
    )
    assert wrong.status_code == 401
    right = client.post(
        "/v1/service/consent/decision",
        json=_service_body(),
        headers={"Authorization": "Bearer s3cr3t-fictional"},
    )
    assert right.status_code == 200


def test_a_blank_service_secret_is_never_read_as_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set-and-empty is an expressed intent that authenticates nobody: 503, not the opening."""
    monkeypatch.setenv("MKT6_S2S_TOKEN", "")
    assert client.post("/v1/service/consent/decision", json=_service_body()).status_code == 503


def test_a_recorded_send_over_the_service_intake_counts_against_the_cap(
    client: TestClient,
) -> None:
    for index in range(3):
        resp = client.post(
            "/v1/service/consent/sends",
            json={
                "tenant": "demo-brand",
                "id": f"se-svc-{index}",
                "subject_id": "subj-000101",
                "channel": "email",
                "purpose": "marketing",
                "decision_id": "consent-earlier",
            },
        )
        assert resp.status_code == 200
    after = client.post("/v1/service/consent/decision", json=_service_body()).json()
    assert after["outcome"] == "denied"
    assert "frequency_cap_exceeded" in after["reasons"]


def test_a_channel_suppression_without_a_channel_is_422(client: TestClient) -> None:
    resp = client.post(
        "/v1/consent/suppressions",
        json={
            "id": "sup-api-bad",
            "subject_id": "subj-000101",
            "scope": "channel",
            "reason": "hard_bounce",
        },
    )
    assert resp.status_code == 422
