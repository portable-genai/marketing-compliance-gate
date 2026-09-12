"""A review's consent comes from the STORE, and nothing a caller sends can change it.

Until 2026-09-12 the review form took consent as free text. ``MarketingAsset.granted_consents``
was a tuple of purpose labels the caller filled in, the console had a "Granted consents
(comma-separated)" box wired straight to it, and the deterministic ``CONSENT_REQUIRED`` rules
read nothing else. So the gate could be told any consent at all by typing it, and the seeded
regional consent and preference store this same service maintains, with its withdrawals, its
expiries, its suppressions and its four-eyes pending grants, was never consulted on the review
path. Every check in this repository was green the whole time: the rule fired, the finding was
cited, the audit event was written, the eval scored 1.0. None of them asked where the consent
came from, because the request was allowed to say.

What is proved here, at the layer each claim actually lives at:

1. **The route.** ``POST /v1/review`` accepts no consent in any spelling: the retired field is
   rejected by the schema rather than ignored, and a review's consent findings follow the
   records in the store for the named subject under the VERIFIED tenant.
2. **The four read states.** A grant on file clears the consent rules. A withdrawn record, a
   subject the store has never heard of, and a request with no subject named all fail them.
   The last two are the ones that matter most: **an absent record is a REFUSAL, never implied
   consent**, and the review says which of the two happened rather than reporting one shape.
3. **The tenant boundary.** Another brand's persona asking about this brand's subject reads
   none of its records, so the same asset fails. Tenancy is resolved, never accepted.
4. **A MAPPED MACHINE CALLER reaches the read.** The deployment's programmatic identity is
   tenanted by its exact service-account address through ``MKT_GOV_IAP_MACHINE_TENANTS_JSON``.
   A sibling repository shipped an authorized surface that refused every machine caller because
   it resolved tenancy from a human's hosted domain alone, and a consent read that no machine
   can reach is a consent read the deployment's own end-to-end check cannot exercise.
5. **The audit trail.** The event names how many records were read, which purposes they
   granted, and why none were when none were, with the subject appearing only as its
   tenant-scoped pseudonym. A raw subject id never enters a durable sink.

Observed failing first. Restoring the old binding (passing ``asset.granted_consents`` into
``consent_checks_for``) turns the store-state tests green-on-a-lie and the schema test red;
reading the consent store without the tenant filter turns the cross-tenant test red; and
treating an absent record as a grant turns the refusal tests red. The spelling-scan test was
written against the field while it still existed and reported it.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit.federation import IAP_ASSERTION_HEADER, IAP_ISSUER
from tests.conftest import LOOPBACK_PEER

from marketing_compliance_gate.api import app as app_module
from marketing_compliance_gate.api import deps, security
from marketing_compliance_gate.api.schemas import AssetModel
from marketing_compliance_gate.config import Container, LocalSettings, Settings
from marketing_compliance_gate.domain.consent import subject_ref
from marketing_compliance_gate.domain.identity import Principal

CONFIG_PATH = "config/settings.yaml"

#: The seeded local subjects (adapters/local/_consent_seed.py) and what each one's STORED state
#: means for a review. Nothing in this file states a consent; it names a subject and reads back.
SUBJECT_GRANTED = "subj-000101"  # an evidenced explicit opt-in for "marketing"
SUBJECT_WITHDRAWN = "subj-000102"  # a record on file that grants nothing
SUBJECT_PENDING = "subj-000106"  # asserted by an operator, awaiting a checker
SUBJECT_OTHER_TENANT = "subj-000201"  # a perfectly good grant, belonging to another brand
SUBJECT_ABSENT = "subj-000999"  # the store has never heard of this subject

#: The SG banking consent rule the seeded rule pack requires for this market and vertical.
SG_CONSENT_RULE = "SG-BANK-CONSENT-PDPA"

OTHER_TENANT_PERSONA = {"X-Dev-Persona": "other-tenant"}

#: A compliant SG banking creative, so the ONLY thing that can make a review non-compliant is
#: its consent. A body with a claim defect would fail whatever the store said, and a test that
#: cannot distinguish the two is not testing consent.
_CLEAN_BODY = (
    "Earn 4.10% per annum on your savings account. Returns are not guaranteed and your "
    "capital is at risk. Contact our team to learn more."
)
_CLEAN_FIELDS = {"risk_warning": "Returns are subject to market conditions."}


def _local_container() -> Container:
    base = Settings.load(CONFIG_PATH)
    return Container(
        Settings(
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
    )


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


def _review(client: TestClient, headers: dict | None = None, **asset: Any):
    body = {
        "asset": {
            "id": "consent-read-probe",
            "asset_type": "creative",
            "title": "SG savings promo (FICTIONAL, clean copy)",
            "body": _CLEAN_BODY,
            "market": "SG",
            "vertical": "banking",
            "fields": _CLEAN_FIELDS,
            **asset,
        }
    }
    return client.post("/v1/review", json=body, headers=headers or {})


def _consent_finding(payload: dict) -> dict:
    matching = [f for f in payload["findings"] if f["rule_id"] == SG_CONSENT_RULE]
    assert matching, f"the review carries no {SG_CONSENT_RULE} finding to read"
    return matching[0]


# --------------------------------------------------------------------------- #
# 1. The route accepts no consent, in any spelling
# --------------------------------------------------------------------------- #
def test_the_asset_schema_has_no_field_a_caller_could_state_a_consent_in() -> None:
    """Structural, not a list of names: every field is checked, so a rename is caught too."""
    fields = set(AssetModel.model_fields)
    assert "audience_subject_id" in fields
    for name in fields:
        assert "consent" not in name, (
            f"AssetModel carries {name!r}; a review's consent is read from the store under the "
            "verified tenant, so no request field may carry one"
        )


def test_the_retired_consent_field_is_REFUSED_rather_than_quietly_ignored(
    client: TestClient,
) -> None:
    """A caller still sending the old field must be told, not silently given a different answer.

    ``AssetModel`` forbids extras, so this is a 422. Ignoring it would be worse than the
    original defect: an integration would keep sending consent and keep believing it applied.
    """
    resp = _review(client, audience_subject_id=SUBJECT_GRANTED, granted_consents=["marketing"])
    assert resp.status_code == 422, resp.text
    assert "granted_consents" in resp.text


# --------------------------------------------------------------------------- #
# 2. The four read states
# --------------------------------------------------------------------------- #
def test_a_subject_with_a_grant_on_file_clears_the_market_consent_rule(
    client: TestClient,
) -> None:
    resp = _review(client, audience_subject_id=SUBJECT_GRANTED)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert _consent_finding(payload)["status"] == "pass"
    assert payload["outcome"] == "compliant"
    source = payload["consent_source"]
    assert source["subject_id"] == SUBJECT_GRANTED
    assert source["records_read"] == 1
    assert source["granted_purposes"] == ["marketing"]
    assert source["reason"] == ""


@pytest.mark.parametrize(
    ("subject", "expected_records", "expected_reason"),
    [
        # A record IS on file and grants nothing. The store was read; the answer is no.
        (SUBJECT_WITHDRAWN, 1, ""),
        # Asserted by an operator with nothing to show, so a checker has not confirmed it.
        (SUBJECT_PENDING, 1, ""),
        # Never heard of. THE refusal case: an absent record is not implied consent.
        (SUBJECT_ABSENT, 0, "the consent store holds no record for this subject"),
        # Nobody was named, so nothing was read. Distinct from the line above on purpose.
        ("", 0, "no audience subject named on the asset"),
    ],
)
def test_a_subject_the_store_does_not_vouch_for_fails_the_consent_rule(
    client: TestClient, subject: str, expected_records: int, expected_reason: str
) -> None:
    resp = _review(client, audience_subject_id=subject)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    finding = _consent_finding(payload)
    assert finding["status"] == "fail", (
        f"subject {subject!r} has no grant on file and the review passed its consent rule"
    )
    assert payload["outcome"] == "non_compliant"
    assert payload["requires_human_review"] is True
    source = payload["consent_source"]
    assert source["granted_purposes"] == []
    assert source["records_read"] == expected_records
    assert source["reason"] == expected_reason
    # The copy itself is clean, so consent is demonstrably the only thing that failed.
    assert [f["rule_id"] for f in payload["findings"] if f["status"] == "fail"] == [SG_CONSENT_RULE]


# --------------------------------------------------------------------------- #
# 3. The tenant boundary
# --------------------------------------------------------------------------- #
def test_another_brands_persona_reads_none_of_this_brands_records(client: TestClient) -> None:
    """The same asset and the same subject, a different verified tenant, the opposite verdict.

    ``subj-000101`` holds a real grant under ``demo-brand``. Asked as ``other-brand``, the
    store is filtered in SQL and answers with nothing, so the review fails. The reverse pairing
    is asserted too, because a read that returned everything to everyone would pass the first
    half of this test on its own.
    """
    mine = _review(client, audience_subject_id=SUBJECT_GRANTED).json()
    assert _consent_finding(mine)["status"] == "pass"

    theirs = _review(
        client, headers=OTHER_TENANT_PERSONA, audience_subject_id=SUBJECT_GRANTED
    ).json()
    assert _consent_finding(theirs)["status"] == "fail"
    assert theirs["consent_source"]["records_read"] == 0
    assert theirs["consent_source"]["reason"].startswith("the consent store holds no record")

    # And the other brand's OWN subject is readable by the other brand and not by this one.
    own = _review(
        client, headers=OTHER_TENANT_PERSONA, audience_subject_id=SUBJECT_OTHER_TENANT
    ).json()
    assert _consent_finding(own)["status"] == "pass"
    borrowed = _review(client, audience_subject_id=SUBJECT_OTHER_TENANT).json()
    assert _consent_finding(borrowed)["status"] == "fail"


# --------------------------------------------------------------------------- #
# 4. A mapped machine caller reaches the read
# --------------------------------------------------------------------------- #
_IAP_AUDIENCE = "/projects/1234567890/global/backendServices/42"
_MACHINE = "portal-e2e@fictional-project.iam.gserviceaccount.com"


def _iap_token() -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    return f"{header}.e30.c2ln"


def _machine_principal(monkeypatch: pytest.MonkeyPatch) -> Principal:
    """The principal the SHIPPED IAP adapter resolves for a mapped machine caller.

    The cryptography is stubbed exactly as the claim-half suite stubs it; everything else is
    the real adapter, including the reviewed map it reads, so this is the tenancy a deployed
    machine call actually resolves to rather than one this test made up.
    """
    from marketing_compliance_gate.adapters.gcp.iap_identity import IapIdentityAdapter
    from marketing_compliance_gate.domain.identity import RequestContext

    monkeypatch.setenv("MKT_GOV_IAP_MACHINE_TENANTS_JSON", json.dumps({_MACHINE: "demo-brand"}))
    monkeypatch.delenv("MKT_GOV_IAP_TENANT_DOMAINS_JSON", raising=False)
    claims = {
        "iss": IAP_ISSUER,
        "aud": _IAP_AUDIENCE,
        "sub": f"serviceAccount:{_MACHINE}",
        "email": _MACHINE,
        "exp": 4102444800,
    }
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = _IAP_AUDIENCE
    adapter._audience_configured_empty = False
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims))
    return adapter.resolve(RequestContext(headers={IAP_ASSERTION_HEADER: _iap_token()}))


def test_a_mapped_machine_caller_resolves_a_tenant_and_reaches_the_consent_read(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployment's programmatic identity can run a review whose consent is really read.

    The sibling ``compliance-advisory`` deployment refused every MACHINE caller because it
    resolved tenancy from a human's hosted domain and read no machine-subject map. A machine
    caller here is tenanted by its exact account address, and this drives the review service
    with that resolved principal's tenant: the same call the portal's end-to-end check makes.
    """
    principal = _machine_principal(monkeypatch)
    assert principal.tenant == "demo-brand", (
        "a mapped machine caller resolved no tenant, so every consent read it makes would "
        "come back empty and every review it ran would fail its consent rules"
    )

    service = deps.make_review_service(container)
    from marketing_compliance_gate.domain.models import (
        AssetType,
        Market,
        MarketingAsset,
        ReviewOutcome,
        ReviewRequest,
        Vertical,
    )

    asset = MarketingAsset(
        id="machine-caller-probe",
        asset_type=AssetType.CREATIVE,
        title="SG savings promo (FICTIONAL, clean copy)",
        body=_CLEAN_BODY,
        market=Market.SG,
        vertical=Vertical.BANKING,
        fields=dict(_CLEAN_FIELDS),
        audience_subject_id=SUBJECT_GRANTED,
    )
    review = service.review(
        ReviewRequest(asset=asset), actor=principal.subject, tenant=principal.tenant
    )
    assert review.consent_source.granted_purposes == ("marketing",)
    assert review.outcome is ReviewOutcome.COMPLIANT


def test_an_unmapped_machine_caller_reads_nothing_rather_than_borrowing_a_tenant(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine nobody wrote down gets no tenant, so it reads no records. Fail-closed.

    The counterpart to the test above, and the reason the map is keyed on the exact account
    rather than its domain: every service account in a project shares one domain, so a
    domain-keyed map would hand an unrelated machine this tenant's consent records.
    """
    monkeypatch.setenv("MKT_GOV_IAP_MACHINE_TENANTS_JSON", json.dumps({_MACHINE: "demo-brand"}))
    from marketing_compliance_gate.adapters.gcp.iap_identity import IapIdentityAdapter
    from marketing_compliance_gate.domain.identity import RequestContext

    sibling = "some-other-runner@fictional-project.iam.gserviceaccount.com"
    claims = {
        "iss": IAP_ISSUER,
        "aud": _IAP_AUDIENCE,
        "sub": f"serviceAccount:{sibling}",
        "email": sibling,
        "exp": 4102444800,
    }
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = _IAP_AUDIENCE
    adapter._audience_configured_empty = False
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims))
    principal = adapter.resolve(RequestContext(headers={IAP_ASSERTION_HEADER: _iap_token()}))
    assert principal.tenant == ""

    service = deps.make_review_service(container)
    from marketing_compliance_gate.domain.models import (
        AssetType,
        Market,
        MarketingAsset,
        ReviewOutcome,
        ReviewRequest,
        Vertical,
    )

    asset = MarketingAsset(
        id="unmapped-machine-probe",
        asset_type=AssetType.CREATIVE,
        title="SG savings promo (FICTIONAL, clean copy)",
        body=_CLEAN_BODY,
        market=Market.SG,
        vertical=Vertical.BANKING,
        fields=dict(_CLEAN_FIELDS),
        audience_subject_id=SUBJECT_GRANTED,
    )
    review = service.review(
        ReviewRequest(asset=asset), actor=principal.subject, tenant=principal.tenant
    )
    assert review.consent_source.granted_purposes == ()
    assert "no verified tenant" in review.consent_source.reason
    assert review.outcome is ReviewOutcome.NON_COMPLIANT


# --------------------------------------------------------------------------- #
# 5. The audit trail, and no raw subject id in it
# --------------------------------------------------------------------------- #
def test_the_audit_event_names_the_consent_read_and_pseudonymises_the_subject(
    client: TestClient, container: Container
) -> None:
    resp = _review(client, audience_subject_id=SUBJECT_GRANTED)
    assert resp.status_code == 200, resp.text

    events = [e for e in container.audit.read_all() if e["action"] == "review"]
    assert events, "the review wrote no audit event"
    metadata = events[-1]["metadata"]
    assert metadata["consent_records_read"] == "1"
    assert metadata["consent_granted_purposes"] == "marketing"
    assert metadata["consent_not_read_because"] == ""
    assert metadata["consent_subject_ref"] == subject_ref("demo-brand", SUBJECT_GRANTED)
    assert metadata["consent_subject_ref"].startswith("subject-sha256:")

    # The pseudonym is tenant-scoped, so the same subject id under two tenants is two refs.
    assert subject_ref("demo-brand", SUBJECT_GRANTED) != subject_ref("other-brand", SUBJECT_GRANTED)

    # No durable surface carries the raw id. Serialised whole, because a leak in a nested
    # value is still a leak and a per-key assertion would not see one.
    serialised = json.dumps(events[-1], sort_keys=True, default=str)
    assert SUBJECT_GRANTED not in serialised, (
        "the raw subject id reached the audit sink; consent records are personal data"
    )


def test_a_refused_read_records_WHY_rather_than_an_indistinguishable_blank(
    client: TestClient, container: Container
) -> None:
    """ "No record on file" and "nobody asked" must not audit identically.

    Both grant nothing, and an auditor reading only the outcome cannot tell a subject whose
    consent was checked and found absent from a review that never checked. The event says which.
    """
    _review(client, audience_subject_id=SUBJECT_ABSENT)
    absent = [e for e in container.audit.read_all() if e["action"] == "review"][-1]["metadata"]
    _review(client, audience_subject_id="")
    unasked = [e for e in container.audit.read_all() if e["action"] == "review"][-1]["metadata"]

    assert absent["consent_not_read_because"] != unasked["consent_not_read_because"]
    assert "holds no record" in absent["consent_not_read_because"]
    assert "no audience subject named" in unasked["consent_not_read_because"]
    # The unasked review names no subject at all, so there is nothing to pseudonymise.
    assert unasked["consent_subject_ref"] == ""
    assert absent["consent_subject_ref"].startswith("subject-sha256:")
