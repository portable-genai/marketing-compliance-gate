"""API request/response schemas (thin Pydantic models at the HTTP boundary)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..domain.models import AgentCard


class AssetModel(BaseModel):
    id: str = Field("asset-1", description="Asset id.")
    asset_type: str = Field("creative", description="campaign | creative | offer.")
    title: str = Field("", description="Asset title.")
    body: str = Field(..., description="Marketing copy to check.")
    market: str = Field("SG", description="Market: JP | AU | SG.")
    vertical: str = Field("banking", description="Vertical: banking | online_retail.")
    fields: dict[str, str] = Field(default_factory=dict)
    granted_consents: list[str] = Field(default_factory=list)
    submitted_by: str = ""


class ReviewRequestModel(BaseModel):
    # No ``actor`` field: the audit actor is the server-verified Principal (resolved from
    # the IAP assertion in secure mode, or a seeded dev persona in local mode), never a
    # client-supplied value. See api/security.py.
    asset: AssetModel


class SubstantiationRequestModel(BaseModel):
    """Request for the green-claims gate.

    Carries NO tenant and NO actor: both come from the server-verified Principal, so a
    client cannot ask for another tenant's substantiation evidence (see api/security.py and
    domain/substantiation.py). ``as_of`` makes the evidence ageing explicit and replayable:
    omit it to age against today, or pin it to reproduce a past assessment exactly.
    """

    asset: AssetModel
    as_of: str = Field(
        "",
        description="ISO date (YYYY-MM-DD) the evidence is aged against; empty means today.",
    )


class ConsentDecisionRequestModel(BaseModel):
    """Ask the consent and preference store whether a subject may be contacted.

    Carries NO tenant and NO actor: both come from the server-verified Principal, so a caller
    cannot ask about another brand's data subject (see api/security.py and
    domain/consent_service.py). ``as_of`` makes the evaluation instant explicit and
    replayable: omit it to decide against now, or pin it to reproduce a past decision exactly.
    """

    subject_id: str = Field(..., description="The data subject's stable id (never a name).")
    purpose: str = Field("marketing", description="The processing purpose being asked about.")
    channel: str = Field("email", description="email | sms | push | voice | chat | post.")
    market: str = Field("SG", description="Market: JP | AU | SG.")
    vertical: str = Field("banking", description="Vertical: banking | online_retail.")
    as_of: str = Field(
        "",
        description="ISO-8601 instant the decision is made against; empty means now.",
    )


class ConsentRecordModel(BaseModel):
    """One consent record to store. The tenant is the verified principal's, never this body.

    A grant with a self-evidencing ``basis`` plus a ``source`` and an ``evidence_ref`` is
    stored as granted. A grant without that proof is stored pending a checker's confirmation
    and grants nothing in the meantime.
    """

    id: str = Field(..., description="Record id (idempotency key).")
    subject_id: str = Field(..., description="The data subject's stable id.")
    purpose: str = Field("marketing", description="The processing purpose.")
    status: str = Field("granted", description="granted | withdrawn | pending_review | unknown.")
    basis: str = Field(
        "explicit_opt_in",
        description="explicit_opt_in | soft_opt_in | contractual | legitimate_interest.",
    )
    effective_from: str = Field("", description="ISO-8601 instant the grant starts.")
    expires_at: str = Field("", description="ISO-8601 instant the grant lapses.")
    source: str = Field("", description="Where the statement was captured.")
    evidence_ref: str = Field("", description="Locator for the captured proof.")
    note: str = Field("", description="Free-text note for the audit trail.")


class ConsentConfirmationModel(BaseModel):
    """A human checker's disposition of a grant that landed pending review."""

    approved: bool = Field(..., description="True promotes to granted; False withdraws it.")
    rationale: str = Field("", description="Why the checker decided as they did.")


class ChannelPreferenceModel(BaseModel):
    id: str = Field(..., description="Preference id (idempotency key).")
    subject_id: str = Field(..., description="The data subject's stable id.")
    channel: str = Field("email", description="email | sms | push | voice | chat | post.")
    opted_in: bool = Field(..., description="True is an opt-in; absence is never an opt-in.")
    source: str = Field("", description="Where the preference was captured.")


class SuppressionEntryModel(BaseModel):
    id: str = Field(..., description="Suppression id (idempotency key).")
    subject_id: str = Field(..., description="The data subject's stable id.")
    scope: str = Field("all", description="all | channel | purpose.")
    reason: str = Field(
        "subject_request",
        description=(
            "subject_request | complaint | hard_bounce | regulator_order | vulnerability | "
            "deceased."
        ),
    )
    channel: str = Field("", description="Required when scope is 'channel'.")
    purpose: str = Field("", description="Required when scope is 'purpose'.")
    expires_at: str = Field("", description="ISO-8601 instant it lapses; empty is permanent.")
    note: str = Field("", description="Free-text note for the audit trail.")


class ServiceConsentDecisionModel(BaseModel):
    """The S2S consent question, asked by a trusted CALLING SERVICE rather than a user.

    This is the only consent shape that carries a ``tenant``, and it carries one for the same
    reason Hrz7's service intake accepts an asserted maker and tenant: the caller is
    authenticated as a service, not as an end user, so there is no end-user principal to
    derive a tenant from. Per-hop OAuth2 token exchange (on-behalf-of) is the deferred next
    layer; until then the calling service is the trust anchor on this path. The end-user
    routes above take no tenant at all and never will.
    """

    tenant: str = Field(..., description="The tenant the calling service is acting for.")
    subject_id: str = Field(..., description="The data subject's stable id (never a name).")
    purpose: str = Field("marketing", description="The processing purpose being asked about.")
    channel: str = Field("email", description="email | sms | push | voice | chat | post.")
    market: str = Field("SG", description="Market: JP | AU | SG.")
    vertical: str = Field("banking", description="Vertical: banking | online_retail.")
    as_of: str = Field(
        "", description="ISO-8601 instant the decision is made against; empty means now."
    )


class ServiceSendEventModel(BaseModel):
    """One contact a calling service actually made, quoting the decision that permitted it.

    Recording the send is what makes a frequency cap real: the cap counts these rows. Quoting
    ``decision_id`` is what ties the message in the audit trail back to the exact stored state
    that allowed it.
    """

    tenant: str = Field(..., description="The tenant the calling service is acting for.")
    id: str = Field(..., description="Send id (idempotency key).")
    subject_id: str = Field(..., description="The data subject's stable id.")
    channel: str = Field("email", description="email | sms | push | voice | chat | post.")
    purpose: str = Field("marketing", description="The processing purpose.")
    decision_id: str = Field("", description="The ConsentDecision id that permitted this send.")
    sent_at: str = Field("", description="ISO-8601 instant of the send; empty means now.")


class HealthModel(BaseModel):
    status: str = "ok"
    profile: str
    market: str
    vertical: str


class AgentSkillModel(BaseModel):
    id: str
    name: str
    description: str


class AgentCardModel(BaseModel):
    """A2A AgentCard served at ``/.well-known/agent-card.json`` (Hrz3 discovery shape)."""

    name: str
    description: str
    url: str
    version: str
    skills: list[AgentSkillModel] = Field(default_factory=list)
    provider: str

    @classmethod
    def from_domain(cls, card: AgentCard) -> AgentCardModel:
        return cls(
            name=card.name,
            description=card.description,
            url=card.url,
            version=card.version,
            skills=[
                AgentSkillModel(id=s.id, name=s.name, description=s.description)
                for s in card.skills
            ],
            provider=card.provider,
        )
