"""The header an assertion arrives under when this service is embedded, and the honest refusal.

**The contradiction this module exists to resolve.** ``test_iap_claim_half.py`` already asserts
that a MAPPED machine caller resolves to the tenant it was given, and those tests pass. Reading
the adapter as it shipped, an embedded deployment would still answer 401 to exactly such a
caller. Both are true, and the reason is that every test in that file hands the adapter its
assertion like this:

    adapter.resolve(RequestContext(headers={IAP_ASSERTION_HEADER: _token()}))

``IAP_ASSERTION_HEADER`` is ``x-goog-iap-jwt-assertion``, the one header production never
delivers to an embedded application. ``x-goog-*`` is Google's reserved namespace and the
serverless frontend REMOVES those headers from a request entering a service, so an embedding host
behind IAP cannot forward what its own edge handed it: the host sets the reserved name, the
frontend drops it, and the service refuses "request did not pass through IAP" about a request
that passed through IAP one hop earlier. The broker sends the same value as
``x-portal-iap-assertion`` as well, precisely because that name is not reserved.

So the claim-half suite models the CLAIMS faithfully, service-account rows included, and models
the TRANSPORT wrongly. It is right about what it asserts and silent about the half that fails.

**Where this was found.** ``compliance-advisory`` and ``cio-advisory`` both answered 401 to
every authenticated caller the day they were deployed as embedded apps, root-caused to reading
only the reserved header name (org-metadata's open-backlog.md, "forty-six repositories read the
one assertion header an embedding host can never forward", opened 2026-09-12). This repository
was in that list and carried the identical defect; it is fixed here BEFORE this app's own first
embedding, not after a live 401 forced the question.
"""

from __future__ import annotations

import base64
import json as _json
from typing import Any

import pytest
from hex_service_kit import federation as kit_federation
from hex_service_kit.federation import IAP_ISSUER

from marketing_compliance_gate.adapters.gcp import iap_identity
from marketing_compliance_gate.adapters.gcp.iap_identity import IapIdentityAdapter
from marketing_compliance_gate.domain.identity import IdentityError, RequestContext

_AUDIENCE = "/projects/1234567890/global/backendServices/42"

#: A machine caller, in the shape a real IAP assertion carries it: an
#: ``@...iam.gserviceaccount.com`` address, NO ``hd`` claim at all (a machine belongs to no
#: hosted domain), and a ``sub`` of the provider's own shape.
_MACHINE = "journey-caller@demo-project.iam.gserviceaccount.com"
_MACHINE_SUB = "accounts.google.com:117000000000000000001"


def _token() -> str:
    """A structurally real compact JWS. Only the header is read, and nothing is signed."""
    header = (
        base64.urlsafe_b64encode(_json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{base64.urlsafe_b64encode(b'{}').decode().rstrip('=')}.c2ln"


def _machine_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": IAP_ISSUER,
        "aud": _AUDIENCE,
        "sub": _MACHINE_SUB,
        "email": _MACHINE,
        "exp": 4102444800,
    }
    claims.update(overrides)
    return claims


def _adapter(audience: str = _AUDIENCE) -> IapIdentityAdapter:
    """The adapter with only the deployment configuration ``resolve`` reads."""
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = audience
    adapter._audience_configured_empty = False
    return adapter


def _resolve(headers: dict[str, str], claims: dict[str, Any] | None = None) -> Any:
    """Run the shipped adapter over ``headers``, with only the cryptography stubbed.

    Stubbing ``_verify`` is what makes the half under test reachable with no network, no
    credential and no cloud SDK. It skips no check the adapter owns: the algorithm pin, the
    required claims, the issuer and the audience are all still evaluated here, and every refusal
    the verifier itself owns is exercised by the crypto suite instead.
    """
    adapter = _adapter()
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims or _machine_claims()))
    return adapter.resolve(RequestContext(headers=headers))


# --------------------------------------------------------------------------------------- #
# The transport. One assertion, two names, and only one of them survives the hop.
# --------------------------------------------------------------------------------------- #
def test_the_forwarded_header_name_is_the_commons_value_and_is_not_reserved() -> None:
    """Rebound from the kit, never re-declared, and outside the stripped namespace.

    Putting the fallback back inside ``x-goog-*`` would reintroduce the exact defect it fixes,
    silently, because the frontend strips the whole namespace rather than one name.
    """
    assert iap_identity._PORTAL_ASSERTION_HEADER == kit_federation.PORTAL_ASSERTION_HEADER
    assert iap_identity._PORTAL_ASSERTION_HEADER == "x-portal-iap-assertion"
    assert not iap_identity._PORTAL_ASSERTION_HEADER.startswith("x-goog-")


def test_the_mapped_machine_caller_resolves_when_the_host_forwarded_the_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline reproduction of the live 401 found in the sibling repos, and its fix here.

    This is the SAME assertion, the SAME claims and the SAME reviewed map as
    ``test_iap_reviewed_maps.py``'s machine-tenant case. The only difference is the header name,
    and the header name is the whole defect: under the forwarded name, which is the only one an
    embedded application ever sees, the adapter as it shipped refused with "missing IAP
    assertion header" and the map below was never read by anything.
    """
    monkeypatch.setenv(
        iap_identity._IAP_MACHINE_TENANTS_ENV, _json.dumps({_MACHINE: "reference-bank"})
    )
    monkeypatch.delenv(iap_identity._IAP_TENANT_DOMAINS_ENV, raising=False)

    principal = _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()})

    assert principal.tenant == "reference-bank"
    assert principal.subject == _MACHINE
    assert principal.source == "gcp-iap"


def test_a_human_is_refused_by_the_same_transport_defect_and_admitted_by_the_same_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect was never machine-only, and saying so is the point of this test.

    Behind the portal the reserved header is absent for every caller, so a human whose console
    only calls routes that take no principal would appear to work while every route that reads
    an identity failed.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    monkeypatch.setenv(
        iap_identity._IAP_TENANT_DOMAINS_ENV, _json.dumps({"bank.example": "reference-bank"})
    )

    human = _machine_claims(
        email="avery.stone@bank.example", hd="bank.example", sub="accounts.google.com:1"
    )
    assert _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()}, human).tenant == (
        "reference-bank"
    )


@pytest.mark.parametrize(
    "header",
    [kit_federation.IAP_ASSERTION_HEADER, kit_federation.PORTAL_ASSERTION_HEADER],
    ids=["edge-injected", "host-forwarded"],
)
def test_both_names_take_the_identical_verification_path(
    monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    """The fallback is TRANSPORT and not a second trust path.

    An assertion under either name is handed to the same verifier and judged by the same
    reviewed policy, so a caller gains nothing by choosing one. The assertion is a stub token
    and ``_verify`` is NOT stubbed here, so the only two acceptable outcomes are a refusal from
    the verifier and a refusal because the verifier is absent; a returned identity would mean
    the header bought a bypass, and a "missing IAP assertion header" would mean it was ignored.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    with pytest.raises((IdentityError, ModuleNotFoundError)) as caught:
        result = _adapter().resolve(RequestContext(headers={header: _token()}))
        raise AssertionError(f"an unverified assertion produced an identity: {result!r}")
    assert "missing IAP assertion header" not in str(caught.value)


def test_the_edge_injected_name_still_wins_when_both_are_present() -> None:
    """Precedence is about diagnosis, not trust: the direct edge's assertion needs no forwarding.

    Both are verified identically, so nothing turns on the order; pinning it keeps the common
    path the simple one when a service is reached both directly and through a host.
    """
    edge = _token()
    ctx = RequestContext(
        headers={
            kit_federation.IAP_ASSERTION_HEADER: edge,
            kit_federation.PORTAL_ASSERTION_HEADER: "forwarded-and-different",
        }
    )
    seen: list[str] = []
    adapter = _adapter()
    object.__setattr__(
        adapter, "_verify", lambda assertion: seen.append(assertion) or _machine_claims()
    )
    adapter.resolve(ctx)
    assert seen == [edge]


def test_neither_name_present_is_still_a_missing_assertion() -> None:
    """The fix must not swallow the ordinary case, and the refusal must name BOTH headers.

    An operator who reads only "missing IAP assertion header" looks at the load balancer. The
    one who reads which two names were examined looks at the hop that dropped one of them.
    """
    with pytest.raises(IdentityError) as caught:
        _adapter().resolve(RequestContext(headers={}))
    message = str(caught.value)
    assert "missing IAP assertion header" in message
    assert kit_federation.IAP_ASSERTION_HEADER in message
    assert kit_federation.PORTAL_ASSERTION_HEADER in message


@pytest.mark.parametrize("blank", ["   ", "\t", "\n"])
def test_a_whitespace_only_forwarded_header_is_an_absent_one(blank: str) -> None:
    """A blank value is truthy, so without stripping it would be refused as a malformed token."""
    with pytest.raises(IdentityError, match="missing IAP assertion header"):
        _adapter().resolve(RequestContext(headers={iap_identity._PORTAL_ASSERTION_HEADER: blank}))


def test_an_unmapped_machine_caller_is_still_refused_through_the_forwarded_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix widens transport, not policy: an unreviewed caller stays refused.

    ``FederationPolicy`` here defaults to no allowlist and no ``refuse_unmapped_tenant``, so an
    unmapped machine resolves to no tenant rather than being rejected outright; this pins that
    the forwarded header does not change WHAT is decided, only whether the decision is reached
    at all.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    monkeypatch.delenv(iap_identity._IAP_TENANT_DOMAINS_ENV, raising=False)
    principal = _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()})
    assert principal.subject == _MACHINE
    assert principal.tenant == ""
