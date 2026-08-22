"""Object-level authorization on green-claim substantiation evidence (fail-closed).

Substantiation evidence is the first genuinely tenant-owned resource Mkt6 stores: a brand's
emissions inventory, offset retirement records and fund ESG disclosures. So the check that
matters is not "is the caller authenticated" but "is this record the caller's".

These tests pin the whole contract:

* a principal reads its own tenant's evidence,
* a principal from another tenant is DENIED (403 via ``TenantAccessDeniedError``), not given
  a 404 that hides whether the record exists, and the denial is audited,
* a principal with no tenant is denied outright (fail closed),
* the listing is tenant-filtered, so an id that exists under another tenant simply is not
  there, and
* the tenant used is the VERIFIED principal's; the HTTP body has no tenant field to spoof.

The HTTP tests drive the real FastAPI app with the seeded local personas (``demo-brand`` and
``other-brand``), so they cover the route, the dependency and the domain check together.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps, security
from marketing_compliance_gate.api.app import app
from marketing_compliance_gate.config import Container, LocalSettings, Settings
from marketing_compliance_gate.domain.errors import EvidenceNotFoundError, TenantAccessDeniedError
from marketing_compliance_gate.domain.identity import Principal
from marketing_compliance_gate.domain.substantiation import SubstantiationService

DEMO_EVIDENCE_ID = "ev-demo-0001"
OTHER_EVIDENCE_ID = "ev-other-0001"
GREEN_ASSET_ID = "camp-green-au-001"


@pytest.fixture
def container() -> Container:
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
    return Container(settings)


@pytest.fixture
def service(container: Container) -> SubstantiationService:
    return deps.make_substantiation_service(container)


@pytest.fixture
def client(container: Container, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real app, wired to an in-memory container (the personas resolve identity).

    All THREE module namespaces that imported ``get_container`` are patched, including
    ``security``: without it the identity dependency resolves against the ambient
    environment's container rather than this one.
    """
    monkeypatch.setattr(deps, "get_container", lambda: container)
    monkeypatch.setattr(app_module, "get_container", lambda: container)
    monkeypatch.setattr(security, "get_container", lambda: container)
    return TestClient(app, client=LOOPBACK_PEER)


def _principal(tenant: str, subject: str = "someone@example.test") -> Principal:
    return Principal(subject=subject, tenant=tenant, source="test")


# --------------------------------------------------------------------------- #
# Domain-level authorization
# --------------------------------------------------------------------------- #
def test_own_tenant_evidence_is_served(service: SubstantiationService) -> None:
    record = service.evidence(DEMO_EVIDENCE_ID, _principal("demo-brand"))
    assert record.id == DEMO_EVIDENCE_ID
    assert record.tenant == "demo-brand"


def test_cross_tenant_evidence_read_is_denied(service: SubstantiationService) -> None:
    """The regression this suite exists for: another tenant's record must be REFUSED.

    Without the server-side comparison of the record's tenant against the verified
    principal's tenant, this read succeeds and one brand reads another brand's
    substantiation file.
    """
    with pytest.raises(TenantAccessDeniedError):
        service.evidence(DEMO_EVIDENCE_ID, _principal("other-brand"))

    with pytest.raises(TenantAccessDeniedError):
        service.evidence(OTHER_EVIDENCE_ID, _principal("demo-brand"))


def test_cross_tenant_denial_is_audited(
    service: SubstantiationService, container: Container
) -> None:
    with pytest.raises(TenantAccessDeniedError):
        service.evidence(DEMO_EVIDENCE_ID, _principal("other-brand"))
    denials = [
        e
        for e in container.audit.read_all()
        if e.get("action") == "evidence_read" and e.get("decision") == "blocked"
    ]
    assert denials, "a refused cross-tenant read must leave an audit record"


def test_principal_without_tenant_is_denied(service: SubstantiationService) -> None:
    with pytest.raises(TenantAccessDeniedError):
        service.evidence(DEMO_EVIDENCE_ID, _principal(""))


def test_missing_evidence_is_not_found_not_denied(service: SubstantiationService) -> None:
    with pytest.raises(EvidenceNotFoundError):
        service.evidence("ev-does-not-exist", _principal("demo-brand"))


def test_listing_is_tenant_scoped(service: SubstantiationService) -> None:
    demo = service.evidence_for_asset(GREEN_ASSET_ID, _principal("demo-brand"))
    other = service.evidence_for_asset(GREEN_ASSET_ID, _principal("other-brand"))
    assert {r.id for r in demo} == {"ev-demo-0001", "ev-demo-0002", "ev-demo-0003"}
    assert {r.id for r in other} == {OTHER_EVIDENCE_ID}
    assert all(r.tenant == "demo-brand" for r in demo)


# --------------------------------------------------------------------------- #
# HTTP-level authorization (403, never a silent 404)
# --------------------------------------------------------------------------- #
def test_http_cross_tenant_read_is_403_not_404(client: TestClient) -> None:
    same_tenant = client.get(
        f"/v1/evidence/{DEMO_EVIDENCE_ID}", headers={"X-Dev-Persona": "analyst"}
    )
    assert same_tenant.status_code == 200

    cross_tenant = client.get(
        f"/v1/evidence/{DEMO_EVIDENCE_ID}", headers={"X-Dev-Persona": "other-tenant"}
    )
    assert cross_tenant.status_code == 403, (
        "a cross-tenant evidence read must be an explicit denial, not a 404 and never a 200"
    )
    assert "denied" in cross_tenant.json()["detail"]


def test_http_missing_evidence_is_404(client: TestClient) -> None:
    response = client.get("/v1/evidence/ev-nope", headers={"X-Dev-Persona": "analyst"})
    assert response.status_code == 404


def test_http_listing_only_returns_the_callers_tenant(client: TestClient) -> None:
    response = client.get(
        "/v1/evidence",
        params={"asset_id": GREEN_ASSET_ID},
        headers={"X-Dev-Persona": "other-tenant"},
    )
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [OTHER_EVIDENCE_ID]


def test_http_substantiation_uses_the_verified_tenant(client: TestClient) -> None:
    """The assessment is scoped to the persona's tenant; the body cannot carry a tenant."""
    body = {
        "asset": {
            "id": GREEN_ASSET_ID,
            "asset_type": "campaign",
            "title": "Our carbon neutral home loan",
            "body": "Bank with a carbon-neutral balance sheet. Offset details on request.",
            "market": "AU",
            "vertical": "banking",
            "fields": {"substantiation_ref": "dms://example.test/evidence/pack-1"},
        },
        "as_of": "2026-08-05",
    }
    demo = client.post("/v1/substantiation", json=body, headers={"X-Dev-Persona": "analyst"})
    other = client.post("/v1/substantiation", json=body, headers={"X-Dev-Persona": "other-tenant"})
    assert demo.status_code == 200
    assert other.status_code == 200
    assert demo.json()["tenant"] == "demo-brand"
    assert other.json()["tenant"] == "other-brand"
    # Same asset, same date, different evidence holdings: the tenant boundary is what
    # decides the verdict, and neither tenant sees the other's records.
    assert demo.json()["coverage"] != other.json()["coverage"]
