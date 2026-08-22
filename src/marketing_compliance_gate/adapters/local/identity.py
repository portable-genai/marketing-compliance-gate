"""Local IdentityPort adapter: seeded dev personas, NO IdP / AD / LDAP.

The SDK-free ``local`` profile must run with zero authentication so demos and tests work
fully offline. This adapter resolves a :class:`Principal` from a small set of seeded
personas, selected by the ``X-Dev-Persona`` request header (the UI's persona picker),
defaulting to the first persona when none is supplied. It lets you exercise per-user
authorization (different entitlement principals and tenants, including a cross-tenant
persona) without standing up any identity provider. It is bound ONLY under the local
profile; secure mode uses the IAP adapter, which verifies a real assertion.

These personas authenticate NOBODY, so this adapter refuses to construct unless the local
profile was chosen deliberately: the profile must actually be ``local`` AND (when the
settings came from the environment) ``MKT_GOV_PROFILE`` or the settings file must have named
it, rather than it being inherited from a fallback. A missing variable therefore fails closed
instead of serving a compliance-review API to any caller as a seeded brand approver.

The entitlement groups are D6's marketing-compliance domain (marketing reviewer / brand /
approver / audit), but the persona ids and the cross-tenant persona mirror the reference so
per-user, per-tenant authorization stays demoable offline.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.identity import IdentityError, Principal, RequestContext
from ...ports.identity import CLIENT_ASSERTED

_PERSONA_HEADER = "x-dev-persona"

# Seeded dev personas. Ordered; the first entry is the default when no persona is selected.
# The persona id is the suffix of ``source`` after the colon.
_PERSONAS: tuple[Principal, ...] = (
    Principal(
        subject="demo.reviewer@brand.example",
        principals=("group:mkt-reviewer", "group:brand"),
        tenant="demo-brand",
        assurance="local-demo",
        source="local-persona:analyst",
    ),
    Principal(
        subject="demo.approver@brand.example",
        principals=("group:mkt-reviewer", "group:brand", "group:mkt-approver"),
        tenant="demo-brand",
        assurance="local-demo",
        source="local-persona:approver",
    ),
    Principal(
        subject="demo.auditor@brand.example",
        principals=("group:audit",),
        tenant="demo-brand",
        assurance="local-demo",
        source="local-persona:auditor",
    ),
    Principal(
        subject="user@other-tenant.example",
        principals=("group:mkt-reviewer",),
        tenant="other-brand",
        assurance="local-demo",
        source="local-persona:other-tenant",
    ),
)


def _persona_id(principal: Principal) -> str:
    _, _, suffix = principal.source.partition(":")
    return suffix or principal.subject


class LocalPersonaProfileError(IdentityError):
    """Raised when seeded dev personas would be served under a non-deliberate local profile."""


class LocalPersonaIdentityAdapter:
    """Resolve a Principal from a seeded dev persona (local profile only, no auth)."""

    #: The persona arrives on the ``X-Dev-Persona`` header the CALLER wrote. That is a
    #: selection, not a credential, so this adapter authenticates nobody and may never buy
    #: the exposure guard's relaxation.
    end_user_auth = CLIENT_ASSERTED

    def __init__(self, settings: Settings) -> None:
        if settings.profile != "local":
            raise LocalPersonaProfileError(
                "seeded dev personas are local-profile only; refusing to serve them under "
                f"profile {settings.profile!r}"
            )
        if not settings.profile_explicit:
            raise LocalPersonaProfileError(
                "MKT_GOV_PROFILE is not set, so the local profile was inherited rather than "
                "chosen; the seeded dev personas authenticate nobody and are refused. Set "
                "MKT_GOV_PROFILE=local deliberately for a dev or demo run, or "
                "MKT_GOV_PROFILE=gcp for a real deployment."
            )
        self._settings = settings
        self._by_id: dict[str, Principal] = {_persona_id(p): p for p in _PERSONAS}
        self._default: Principal = _PERSONAS[0]

    def resolve(self, ctx: RequestContext) -> Principal:
        chosen = ctx.header(_PERSONA_HEADER).strip()
        if not chosen:
            return self._default
        persona = self._by_id.get(chosen.lower())
        if persona is None:
            raise IdentityError(
                f"unknown dev persona {chosen!r}; valid personas: {sorted(self._by_id)}"
            )
        return persona

    def personas(self) -> tuple[dict[str, str], ...]:
        """List the seeded personas for the local persona picker (id, subject, tenant)."""
        return tuple(
            {
                "id": _persona_id(p),
                "subject": p.subject,
                "tenant": p.tenant,
                "principals": ", ".join(p.principals),
            }
            for p in _PERSONAS
        )
