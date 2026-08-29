"""Service-to-service (S2S) transport hardening shared by the platform adapters.

The ``platform`` profile's adapters are thin HTTP clients to the sibling
horizontal-platform and de-risking services. Base URLs must be ``https://`` outside
loopback (caught at adapter construction); when ``S2S_TOKEN`` is set every request
carries it as an ``Authorization: Bearer`` header, and ``S2S_SIGNING_KEY``
optionally propagates a verified end-user actor as an HMAC-signed ``X-Cg-Actor`` /
``X-Cg-Actor-Sig`` pair.

**Sourced from the shared ``hex-service-kit`` commons.** This module
passes this repo's env-var and header names to :mod:`hex_service_kit.s2s`, so a fix to
the S2S transport rule is a version bump of the package rather than an N-repo edit.
"""

from __future__ import annotations

from hex_service_kit.s2s import client_headers, validate_base_url

#: Env var holding the bearer credential for S2S calls (UNSET = no header attached; set and
#: empty raises, because an emptied credential is an expressed intent, not the unset default).
TOKEN_ENV = "S2S_TOKEN"
#: Env var holding the HMAC key for signing the propagated end-user actor.
SIGNING_KEY_ENV = "S2S_SIGNING_KEY"
_ACTOR_HEADER = "X-Cg-Actor"
_ACTOR_SIG_HEADER = "X-Cg-Actor-Sig"

__all__ = ["SIGNING_KEY_ENV", "TOKEN_ENV", "headers", "validate_base_url"]


def headers(actor: str = "") -> dict[str, str]:
    """Auth headers for one S2S request (bearer token + optional signed actor)."""
    return client_headers(
        actor,
        token_env=TOKEN_ENV,
        signing_key_env=SIGNING_KEY_ENV,
        actor_header=_ACTOR_HEADER,
        actor_sig_header=_ACTOR_SIG_HEADER,
    )
