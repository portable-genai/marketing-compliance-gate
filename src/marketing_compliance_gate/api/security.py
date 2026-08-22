"""FastAPI security dependency: resolve a verified Principal (never client-asserted).

Builds a :class:`RequestContext` from the inbound request headers and asks the active
profile's :class:`IdentityPort` adapter to resolve a verified :class:`Principal`. The
request-body ``actor``/ACL are ignored entirely: the audit actor flows from here, closing
the spoofable-identity gap. A failure to resolve a verified principal is a 401.

The IdentityPort is the inner ring of the defense-in-depth PEP (edge IAP/Apigee ->
Hrz1 guardrail -> this per-backend check); this module is the per-backend ring.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ..domain.identity import IdentityError, Principal, RequestContext
from .deps import get_container


def get_principal(request: Request) -> Principal:
    """Resolve the verified end-user principal for this request, or raise 401.

    Binding the port is inside the try on purpose: an identity adapter that REFUSES TO
    CONSTRUCT (the seeded personas under a profile nobody chose) is an authentication
    failure, so it must answer 401 like any other unresolvable principal, not 500.
    """
    ctx = RequestContext(headers={k.lower(): v for k, v in request.headers.items()})
    try:
        identity = get_container().identity
        return identity.resolve(ctx)
    except IdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc


# Reusable typed dependency for route signatures.
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
