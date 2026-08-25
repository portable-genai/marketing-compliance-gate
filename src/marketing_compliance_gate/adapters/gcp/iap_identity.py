"""GCP IdentityPort adapter: verify the Identity-Aware Proxy (IAP) signed assertion.

In secure mode the deployment is fronted by Cloud IAP (Cloud Run behind an HTTPS load
balancer + IAP), which authenticates the user against the configured IdP (Workspace, or an
external client IdP via Workforce Identity Federation) and injects a signed JWT in the
``x-goog-iap-jwt-assertion`` header. This adapter VERIFIES that assertion (signature,
audience, issuer, expiry) and derives the :class:`Principal` server-side, so authentication
is configured ON the GCP service rather than hand-rolled in the app. The Google SDK imports
are lazy (mirroring the other gcp adapters) so the SDK-free local/onprem profiles never
import them, and the verified assertion is never logged.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit.assertion import require_claims, require_pinned_algorithm
from hex_service_kit.federation import IAP_ASSERTION_HEADER, IAP_ISSUER, IAP_KEYS_URL
from hex_service_kit.identity import IdentityError as AssertionRefused

from ...config import Settings
from ...domain.identity import IdentityError, Principal, RequestContext
from ...envread import read_env_setting
from ...ports.identity import VERIFIED, EndUserAuthUnavailableError

# This repository's names for the kit's transport facts. They are REBOUND, not re-declared:
# the header name, the issuer and the key-set URL are the same three strings in every
# repository that verifies an IAP assertion, and while each kept its own copy the population
# could drift without anything noticing. Rebinding makes a divergence between this adapter and
# the reviewed set impossible rather than merely unlikely.
#
#: ``verify_token`` does not check the issuer at all (``verify_oauth2_token`` is the wrapper
#: that does), so this adapter checks it itself against the kit's value.
_ASSERTION_HEADER = IAP_ASSERTION_HEADER
_IAP_KEYS_URL = IAP_KEYS_URL
_IAP_ISSUER = IAP_ISSUER

#: The claims this deployment requires before it reads any of them. ``email`` is here because it
#: is the subject the audit record attributes to; the previous ``email or sub`` reader accepted
#: an assertion carrying only one of them and could not tell an absent claim from an empty one.
_REQUIRED_CLAIMS = ("iss", "sub", "email", "exp")


_VERIFIER_UNAVAILABLE = (
    "the IAP assertion verifier is not installed, so this deployment can authenticate nobody. "
    "Install the managed extra (pip install -r requirements-gcp.lock, or '.[gcp]') so "
    "google-auth is importable, or run a profile whose identity adapter needs no cloud SDK."
)


class IapAudienceUnconfiguredError(EndUserAuthUnavailableError):
    """No audience is configured, so nobody can be authenticated on this deployment.

    503 rather than 401: a caller who presented a perfectly good IAP assertion would be refused
    in exactly the same way, so inviting them to authenticate would be a lie. The message names
    the variable, because the fix is in the deployment and not in the request.
    """

    http_status = 503


class IapVerifierUnavailableError(EndUserAuthUnavailableError):
    """google-auth is not importable, so no assertion can be checked at all.

    Also 503, and for the same reason. This exists so the missing-SDK case is a refusal with a
    reason instead of the bare 500 an unwrapped ModuleNotFoundError produced: an uncredentialed
    caller got an empty error page and the operator got nothing to read.
    """

    http_status = 503


class IapIdentityAdapter:
    """Verify the IAP-injected JWT assertion and derive a Principal (secure mode)."""

    #: The assertion's signature, audience, issuer and expiry are checked server-side before
    #: any claim is believed, so this adapter DOES authenticate the end user.
    end_user_auth = VERIFIED

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Expected audience: the IAP-protected resource. For an HTTPS LB + IAP it is
        # "/projects/<NUM>/global/backendServices/<ID>"; for App Engine/Cloud Run IAP it is
        # "/projects/<NUM>/apps/<ID>". Configure via MKT_GOV_IAP_AUDIENCE; required in secure mode.
        #
        # Read as THREE states, not two. Reading ``.value`` alone collapses unset and
        # set-and-empty onto the same empty string. Both states refuse identically and always
        # did, so nothing here widens or narrows what is accepted; what was lost was the
        # ability to tell an operator which mistake they made, and an operator told 'not
        # configured' about a variable that was present and blank goes looking for the wrong
        # thing.
        _audience_setting = read_env_setting("MKT_GOV_IAP_AUDIENCE")
        self._audience = _audience_setting.value
        self._audience_configured_empty = _audience_setting.is_configured_empty

    def resolve(self, ctx: RequestContext) -> Principal:
        # The policy is checked BEFORE the credential is even read. An unset audience makes
        # every request a refusal that costs no verification work and needs no Google SDK
        # installed, and it can never reach ``verify_token`` empty, where an absent audience
        # is documented to SKIP the ``aud`` check and would authenticate any IAP-signed token.
        if not self._audience:
            raise IapAudienceUnconfiguredError(
                "MKT_GOV_IAP_AUDIENCE is set to an empty value, which names nothing; cannot "
                "verify IAP assertion. Unset it to leave the setting absent, or give it "
                "the IAP-protected resource."
                if self._audience_configured_empty
                else "MKT_GOV_IAP_AUDIENCE is not configured; cannot verify IAP assertion"
            )
        # Stripped, so a header a proxy rendered blank is ABSENT rather than an assertion:
        # a whitespace-only value is truthy, so it skipped this refusal and was refused
        # further down by the algorithm pin instead, which reports a malformed token for
        # what is actually a missing one.
        assertion = ctx.header(_ASSERTION_HEADER).strip()
        if not assertion:
            raise IdentityError("missing IAP assertion header; request did not pass through IAP")
        # The algorithm is judged BEFORE the verifier is handed the token, with no cryptography
        # and no cloud SDK, so the refusal is exercised by the offline gate rather than living
        # inside a library the gate does not install. `alg: none` is an unsigned assertion and
        # the HS* family would let a public key be used as an HMAC secret.
        self._refuse_unpinned_algorithm(assertion)
        claims = self._verify(assertion)
        # `verify_token` checks the signature, the audience and the expiry. It does NOT check the
        # issuer, so a Google-signed token from another issuer that satisfied the other two would
        # have been accepted here on the strength of a docstring that said otherwise.
        self._refuse_unpinned_claims(claims)
        subject = str(claims["email"]).strip()
        # Tenant from the hosted-domain claim; entitlement principals are derived
        # server-side (here, the verified subject; production maps Cloud Identity groups).
        tenant = str(claims.get("hd") or "").strip()
        principals: tuple[str, ...] = (f"user:{subject}",)
        return Principal(
            subject=subject,
            principals=principals,
            tenant=tenant,
            assurance="iap",
            source="gcp-iap",
        )

    def _refuse_unpinned_algorithm(self, assertion: str) -> None:
        """Refuse an assertion signed with an algorithm this deployment does not accept.

        The kit raises its own ``IdentityError``, which is NOT this repository's, so it is
        re-raised as the local one. Without that, the refusal would escape ``get_principal``
        and FastAPI would answer a bare 500 to a caller who should have been told 401.
        """
        try:
            require_pinned_algorithm(assertion)
        except AssertionRefused as exc:
            raise IdentityError(str(exc)) from exc

    def _refuse_unpinned_claims(self, claims: dict[str, Any]) -> None:
        """Refuse a verified assertion missing a required claim or naming the wrong party."""
        try:
            require_claims(
                claims,
                issuer=_IAP_ISSUER,
                audience=self._audience,
                required=_REQUIRED_CLAIMS,
            )
        except AssertionRefused as exc:
            raise IdentityError(str(exc)) from exc

    def _verify(self, assertion: str) -> dict[str, Any]:
        try:
            # Lazy import keeps the SDK-free profiles import-clean (mirrors the other gcp
            # adapters). Inside the try because an uninstalled verifier must refuse with a
            # reason and a status: unwrapped, the ModuleNotFoundError escaped resolve and
            # get_principal entirely and FastAPI answered a bare 500 on every request.
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token
        except ImportError as exc:
            raise IapVerifierUnavailableError(_VERIFIER_UNAVAILABLE) from exc

        try:
            claims: dict[str, Any] = id_token.verify_token(
                assertion,
                google_requests.Request(),
                audience=self._audience,
                certs_url=_IAP_KEYS_URL,
            )
        except Exception as exc:  # noqa: BLE001 - any verification failure must become a 401
            raise IdentityError(f"IAP assertion verification failed: {exc}") from exc
        return claims
