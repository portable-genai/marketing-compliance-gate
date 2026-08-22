"""IdentityPort — resolve a verified Principal from inbound transport context.

The hexagon boundary for authentication. The API layer hands the adapter a
:class:`RequestContext` (the request headers) and gets back a verified
:class:`Principal`, or an :class:`IdentityError`. The active profile picks the adapter:

* ``local`` resolves a seeded dev persona (no IdP/AD/LDAP) so demos and tests run offline,
* ``gcp`` (and ``platform``) verifies the Identity-Aware-Proxy-injected signed assertion
  (auth configured on the GCP service), and
* ``onprem`` is the placeholder for the client's own enterprise IdP (OIDC/SAML).

This keeps the per-user identity decision swappable by configuration, exactly like every
other port, and is the single seam where the client-asserted actor/ACL is replaced by a
server-verified one.

The Protocol itself is NOT written out here: it comes from ``hex_service_kit.identity``
beside the :class:`Principal` and :class:`RequestContext` it is typed in, and is re-exported
so this repo keeps one import site for the boundary. A Protocol copied into N repositories
is N Protocols, and only one of them gets fixed when a defect is found.
"""

from __future__ import annotations

from hex_service_kit.identity import IdentityPort

# --------------------------------------------------------------------------- #
# What an identity adapter DECLARES about the end-user authentication it provides.
#
# The exposure guard registered on the app object has one question to answer before it may
# relax: are this service's END-USER routes authenticated? Nothing else in the configuration
# answers it.
#
# * The PROFILE names an adapter family, not an authentication scheme. A deliberate ``local``
#   and an inherited one bind the same seeded personas, and a client's own IdP adapter can be
#   bound under ``onprem`` in ``config/settings.yaml`` without the profile string changing.
# * A SERVICE-TO-SERVICE credential authenticates a calling SERVICE. It authenticates no end
#   user, so its presence is not evidence that anything a browser reaches is protected, and it
#   takes no part in this answer at any depth.
#
# The adapter bound to the identity port is the only thing that knows, so it says so here.
# --------------------------------------------------------------------------- #

#: The adapter verifies a server-side assertion; the client cannot assert who it is.
VERIFIED = "verified"
#: The adapter believes a header the client wrote. Useful offline, not authentication.
CLIENT_ASSERTED = "client-asserted"
#: The adapter resolves nobody: a placeholder for an identity provider not yet bound.
UNIMPLEMENTED = "unimplemented"

#: Every declaration this service understands. Anything else is read as CLIENT_ASSERTED.
END_USER_AUTH_KINDS: frozenset[str] = frozenset({VERIFIED, CLIENT_ASSERTED, UNIMPLEMENTED})

#: The class attribute an identity adapter sets to one of the values above. A CLASS attribute,
#: not an instance one, because the posture has to be readable WITHOUT constructing the
#: adapter: the seeded-persona adapter refuses to construct under an inherited profile, and a
#: posture obtainable only by constructing something disappears exactly when it matters most.
END_USER_AUTH_ATTR = "end_user_auth"


def declared_end_user_auth(adapter: object) -> str:
    """What ``adapter`` (a class or an instance) declares, defaulting to CLIENT_ASSERTED.

    An adapter that declares NOTHING is read as :data:`CLIENT_ASSERTED`, never
    :data:`VERIFIED`. Silence is not a claim to verify anything, and a guard that read silence
    as "authenticated" would switch itself off for every adapter somebody forgot to annotate.
    An unrecognised value lands in the same place, so a typo cannot read as a verification
    claim.
    """
    declared = getattr(adapter, END_USER_AUTH_ATTR, None)
    if isinstance(declared, str) and declared in END_USER_AUTH_KINDS:
        return declared
    return CLIENT_ASSERTED


__all__ = [
    "CLIENT_ASSERTED",
    "END_USER_AUTH_ATTR",
    "END_USER_AUTH_KINDS",
    "UNIMPLEMENTED",
    "VERIFIED",
    "IdentityPort",
    "declared_end_user_auth",
]
