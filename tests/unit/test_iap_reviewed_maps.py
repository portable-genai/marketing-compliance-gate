"""The reviewed maps that turn a verified IAP caller into the tenant its consent rows carry.

The consent store is read and written under ``principal.tenant`` and nothing else. A deployment
seeds it under a tenant id it chose, and signs users in from Workspace domains whose names are
nothing like it. With the hosted domain as the only source of a tenant, every verified user
resolved to their own domain and every consent snapshot came back empty: a seeded store that
reads exactly like an unseeded one, because an invisible row and an absent row look the same.

Observed failing first: this file failed at import, because the adapter held one literal policy
and a deployment had no way to name a tenant. Deleting the env reads from
``_federation_policy`` turns the mapped-domain and machine-caller tests red again.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from hex_service_kit.federation import IAP_ASSERTION_HEADER, IAP_ISSUER

from marketing_compliance_gate.adapters.gcp.iap_identity import (
    IapIdentityAdapter,
    _federation_policy,
)
from marketing_compliance_gate.domain.consent_service import ConsentService
from marketing_compliance_gate.domain.identity import RequestContext

_AUDIENCE = "/projects/1234567890/global/backendServices/42"
_TENANTS = "MKT_GOV_IAP_TENANT_DOMAINS_JSON"
_MACHINES = "MKT_GOV_IAP_MACHINE_TENANTS_JSON"
_E2E = "portal-e2e@fictional-project.iam.gserviceaccount.com"


@pytest.fixture(autouse=True)
def _no_reviewed_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (_TENANTS, _MACHINES):
        monkeypatch.delenv(name, raising=False)


def _token() -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    return f"{header}.e30.c2ln"


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": IAP_ISSUER,
        "aud": _AUDIENCE,
        "sub": "accounts.google.com:100000000000000000001",
        "email": "demo.reviewer@brand.example",
        "hd": "brand.example",
        "exp": 4102444800,
    }
    claims.update(overrides)
    return {name: value for name, value in claims.items() if value is not None}


def _resolve(claims: dict[str, Any]) -> Any:
    """The shipped claim half with the cryptography stubbed, as the claim-half suite does."""
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = _AUDIENCE
    adapter._audience_configured_empty = False
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims))
    return adapter.resolve(RequestContext(headers={IAP_ASSERTION_HEADER: _token()}))


class _RecordingStore:
    """Records the tenant the consent service asks the store for, and holds nothing."""

    def __init__(self) -> None:
        self.tenants: list[str] = []

    def snapshot(self, tenant: str, subject_id: str) -> Any:
        from marketing_compliance_gate.domain.consent import ConsentSnapshot

        self.tenants.append(tenant)
        return ConsentSnapshot(tenant=tenant, subject_id=subject_id)


def _store_tenant_for(principal: Any) -> str:
    store = _RecordingStore()
    service = ConsentService(
        consent_store=store, rule_provider=None, tracer=None, audit=None, review_router=None
    )
    service.snapshot("subject-0001", principal)
    return store.tenants[-1]


def test_unset_maps_leave_the_hosted_domain_passthrough_unchanged() -> None:
    policy = _federation_policy()
    assert policy.tenant_from_hosted_domain is True
    assert dict(policy.domain_tenants) == {}
    assert dict(policy.machine_tenants) == {}
    assert _store_tenant_for(_resolve(_claims())) == "brand.example"


def test_a_mapped_domain_reads_the_consent_rows_seeded_for_its_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_TENANTS, json.dumps({"Brand.EXAMPLE": "reference-bank"}))
    assert _store_tenant_for(_resolve(_claims())) == "reference-bank"


def test_a_machine_caller_is_tenanted_by_its_exact_account_and_never_by_its_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_MACHINES, json.dumps({_E2E: "reference-bank"}))
    assert _resolve(_claims(hd=None, email=_E2E)).tenant == "reference-bank"
    sibling = "some-other-runner@fictional-project.iam.gserviceaccount.com"
    assert _resolve(_claims(hd=None, email=sibling)).tenant == ""


@pytest.mark.parametrize("name", [_TENANTS, _MACHINES])
def test_an_emptied_map_is_a_configuration_error_not_an_absent_one(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "")
    with pytest.raises(ValueError, match=name):
        _federation_policy()


@pytest.mark.parametrize(
    "name,value",
    [
        (_TENANTS, "not json"),
        (_TENANTS, "[]"),
        (_TENANTS, json.dumps({"": "reference-bank"})),
        (_TENANTS, json.dumps({"*": "reference-bank"})),
        (_TENANTS, json.dumps({"brand.example": ""})),
        (_MACHINES, json.dumps({_E2E: None})),
    ],
)
def test_a_malformed_map_refuses_rather_than_mapping_nothing(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        _federation_policy()
