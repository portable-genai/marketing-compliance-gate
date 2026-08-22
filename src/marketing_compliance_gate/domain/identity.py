"""Identity value objects for server-side, verified principals, re-exported from the commons.

The agent never trusts a client-asserted ``actor`` or ACL. A :class:`Principal` is
resolved server-side by an :class:`~marketing_compliance_gate.ports.identity.IdentityPort`
adapter (local dev persona, GCP IAP-verified assertion, or an on-prem client IdP) from the
inbound transport context, and becomes the audit actor plus the entitlement principals. Pure
stdlib: nothing here imports a web framework or a cloud SDK.

None of these types is declared here. They are the ``hex-service-kit`` types, re-exported so
this module stays the one import site the domain and the adapters read identity from. A hand
copy here, byte-identical to the commons and to fifteen other repos' copies, would be a shared
value type that is not actually shared: the moment one copy gains a field the fleet no longer
agrees on what a verified principal is.

``Principal.actor`` is the audit actor, and non-repudiation of that actor is what MAS TRM
and CPS 234 are read against here. That obligation is this repo's, not the commons'; the
type it is discharged with is the fleet's.
"""

from __future__ import annotations

from hex_service_kit.identity import (
    ANONYMOUS,
    IdentityError,
    Principal,
    RequestContext,
)

__all__ = [
    "ANONYMOUS",
    "IdentityError",
    "Principal",
    "RequestContext",
]
