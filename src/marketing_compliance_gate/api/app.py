"""FastAPI app — thin HTTP boundary over the domain services.

Owns no business logic: it translates a request into a domain call and serialises the cited
result with ``to_jsonable``. Heavy / cloud imports stay lazy so importing this module under
the local profile needs no Google Cloud SDK.

Identity is server-verified: every artifact route resolves a :class:`Principal` from the
inbound request (a seeded dev persona in local mode, or the IAP-injected assertion in secure
mode) and uses ``principal.actor`` as the audit actor. The client cannot supply an
``actor``. The embedding-surface controls (env-driven per-tenant CORS + a CSP
``frame-ancestors`` header) let the UI embed same-origin into a host app.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from hex_service_kit import cors_allowlist
from hex_service_kit.netdefaults import ConfiguredEmptyError, read_env_setting
from hex_service_kit.web import add_loopback_exposure_guard, make_require_service_caller

from ..config import Settings, end_user_auth_kind
from ..domain.consent import (
    ChannelPreference,
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentStatus,
    SuppressionEntry,
    SuppressionReason,
    SuppressionScope,
)
from ..domain.errors import (
    ConsentRecordNotFoundError,
    ConsentWriteRejectedError,
    EvidenceNotFoundError,
    GreenClaimPackError,
    GuardrailBlockedError,
    RuleSetEmptyError,
    TenantAccessDeniedError,
)
from ..domain.identity import IdentityError
from ..domain.models import AssetType, Market, MarketingAsset, ReviewRequest, Vertical, utcnow
from ..domain.serialization import to_jsonable
from ..ports.identity import VERIFIED
from .deps import (
    get_container,
    make_consent_service,
    make_review_service,
    make_substantiation_service,
)
from .schemas import (
    AgentCardModel,
    ChannelPreferenceModel,
    ConsentConfirmationModel,
    ConsentDecisionRequestModel,
    ConsentRecordModel,
    HealthModel,
    ReviewRequestModel,
    ServiceConsentDecisionModel,
    ServiceSendEventModel,
    SubstantiationRequestModel,
    SuppressionEntryModel,
)
from .security import CurrentPrincipal

_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Embedding-surface controls. In secure/embedded mode the agent is served same-origin via
# the parent app's reverse-proxy (no CORS needed); for the cross-origin / standalone dev
# case, MKT_GOV_CORS_ORIGINS is an explicit per-tenant allowlist (never "*").
# MKT_GOV_FRAME_ANCESTORS is the CSP frame-ancestors allowlist of parent origins permitted
# to iframe the agent UI.
_FRAME_ANCESTORS_ENV = "MKT_GOV_FRAME_ANCESTORS"
_CORS_ORIGINS_ENV = "MKT_GOV_CORS_ORIGINS"
_DEFAULT_FRAME_ANCESTORS = "'self'"

# The ONE deliberate opt-out from the loopback exposure bound below. It is a RELAXATION, so the
# commons compares the raw value against exactly "1": unset, set-and-empty, "0", "true" and
# " 1 " all leave the guard ON, and no other variable can switch it off.
_INSECURE_DEMO_ENV = "MKT_GOV_ALLOW_INSECURE_DEMO"


#: Entries that are a wildcard by BEHAVIOUR rather than by spelling, so the asterisk test below
#: cannot see them. ``null`` is the one that matters: a SANDBOXED iframe presents the origin
#: ``null``, so allowing it hands framing and credentialed cross-origin rights to any page able
#: to open one. ``'*'`` is what a quoted Terraform variable or a YAML string renders, and ``*.*``
#: is a host pattern matching every name with a dot in it. The same set is refused on the
#: document half, in ``ui/lib/csp.mjs``.
_WILDCARD_TOKENS = frozenset({"*", "'*'", "null", "*.*"})


def _refuse_wildcard(values: Sequence[str], env_name: str) -> None:
    """Refuse a resolved origin policy that names a wildcard, at boot rather than per request.

    Both allowlists were resolved carefully in three states and then handed on verbatim, with
    the "never ``*``" rule living only in the comment above and in the runbook. A comment does
    not fail a build: ``frame-ancestors *`` lets ANY page frame the console, and ``*`` in the
    CORS allowlist grants every origin on the internet the trust the allowlist exists to
    restrict, on responses that carry credentials.

    Any token CONTAINING ``*`` is refused, not only a bare one. ``https://*.client.example``
    is a real CSP host-source wildcard covering every subdomain, including whichever one an
    attacker manages to register, and an allowlist is only worth having when each entry names
    an origin somebody decided to trust.

    The character test is necessary and not sufficient, so :data:`_WILDCARD_TOKENS` covers the
    spellings that carry no asterisk and behave as one anyway. A real origin never contains the
    character and is never one of those tokens, so this refuses nothing a deployment could
    correctly hold.
    """
    offending = [value for value in values if "*" in value or value in _WILDCARD_TOKENS]
    if offending:
        raise ValueError(
            f"{env_name} resolved to {offending}: the origin policy must never contain a "
            "wildcard. Name the exact parent origins that may frame or call this service, or "
            f"unset {env_name} to keep the restrictive default."
        )


def _frame_ancestors() -> str:
    """Resolve the CSP ``frame-ancestors`` allowlist in THREE states, never two.

    ``os.environ.get(name, "'self'")`` only distinguishes absent from present, so a variable
    an operator set to empty (a Terraform variable that renders to nothing, a Cloud Run env
    var declared with no value) reached the middleware verbatim and produced
    ``Content-Security-Policy: frame-ancestors `` with an EMPTY directive. Browsers discard a
    valueless directive as a parse error, and the ``== "'self'"`` branch below was skipped too,
    so ``X-Frame-Options`` was not emitted as the legacy fallback either: the clickjacking
    control vanished without a trace in the one deployment shape that looks configured.

    * unset: no intent was expressed, so the documented restrictive default stands.
    * set and empty: an intent WAS expressed and it names nothing. Refused, not silently
      widened. This resolver runs at import, so the refusal is a BOOT refusal: the process
      never comes up serving responses that carry no framing policy at all.
    * set with a value: used as given, once :func:`_refuse_wildcard` has established that it
      names origins rather than everybody.
    """
    setting = read_env_setting(_FRAME_ANCESTORS_ENV)
    if setting.is_configured_empty:
        raise ConfiguredEmptyError(
            f"{_FRAME_ANCESTORS_ENV} is set but empty. An empty CSP frame-ancestors directive "
            "is discarded by browsers, which would leave the agent with no clickjacking "
            f"protection at all. Unset {_FRAME_ANCESTORS_ENV} to keep the "
            f"{_DEFAULT_FRAME_ANCESTORS} default, or name the parent origins that may frame it."
        )
    resolved = setting.value or _DEFAULT_FRAME_ANCESTORS
    _refuse_wildcard(resolved.split(), _FRAME_ANCESTORS_ENV)
    return resolved


_FRAME_ANCESTORS = _frame_ancestors()


def _cors_origins() -> list[str]:
    """Explicit allowlist, never "*"; the localhost dev fallback applies ONLY under a
    DELIBERATE local profile (shared hex-service-kit rule).

    The argument is the exposure profile, not the raw one: a run that never chose a profile
    presents ``unconfigured``, which is no origin's allowlist, so an unset variable cannot
    hand cross-origin trust to arbitrary local processes on a user's machine.

    The commons resolver documents that it never returns ``*``, and it never invents one, but
    it does return what the variable says: a tenant that sets the allowlist to ``*`` gets
    ``["*"]`` back. :func:`_refuse_wildcard` is what turns that documented rule into a refusal.

    It runs on the CONFIGURED value first, and that ordering is the point rather than an
    accident. The commons resolver now refuses a wildcard itself, raising
    ``InsecureCorsError``, so whichever of the two runs first is the one that decides which
    message an operator reads. This repo owns the rule: it names the variable, it says how to
    get back to the restrictive default, and its union covers the behavioural tokens as well
    as the asterisk. Running it first keeps it the single authority and leaves the commons an
    unreachable backstop on the configured path. The trailing call still guards the RESOLVED
    list, which under the unset default is a value the operator never wrote.
    """
    setting = read_env_setting(_CORS_ORIGINS_ENV)
    if setting.has_value:
        _refuse_wildcard(
            [origin.strip() for origin in setting.value.split(",") if origin.strip()],
            _CORS_ORIGINS_ENV,
        )
    origins = cors_allowlist(
        get_container().settings.exposure_profile,
        origins_env=_CORS_ORIGINS_ENV,
        dev_origins=tuple(_DEV_ORIGINS),
    )
    _refuse_wildcard(origins, _CORS_ORIGINS_ENV)
    return origins


app = FastAPI(
    title="D6 Marketing Compliance and Brand Governance",
    version="0.1.0",
    description=(
        "Cited marketing-compliance reviews from a deterministic claim / permission / brand "
        "/ consent rule engine, the marketing maker-checker gate, generic across banking and "
        "online retail and the JP/AU/SG markets."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Dev-Persona"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next: Any) -> Any:
    """Emit embedding-surface headers: CSP frame-ancestors (who may iframe the agent).

    ``_FRAME_ANCESTORS`` is guaranteed non-empty by :func:`_frame_ancestors`, so the directive
    emitted here always carries a value a browser will honour.
    """
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors {_FRAME_ANCESTORS}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if get_container().settings.exposure_profile in {"gcp", "platform", "onprem"}:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if _FRAME_ANCESTORS == _DEFAULT_FRAME_ANCESTORS:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


# A request arrives with nothing authenticating the END USER unless BOTH of these hold, and the
# guard below bounds every case where either fails:
#
#   1. a profile was chosen. Absent that, nobody selected an identity scheme: the seeded-persona
#      adapter refuses to construct and every artifact route answers 401, but /healthz, the
#      agent card and /v1/personas would still answer a stranger, and a deployment in that state
#      has no business being reachable at all. It is also the one case where a settings file
#      that bound a verifying adapter must NOT buy the relaxation: unset is not consent,
#      whatever the binding says;
#   2. the identity adapter the active binding names DECLARES that it verifies the end user
#      (``ports/identity.py``). Seeded personas arrive on the ``X-Dev-Persona`` header the
#      caller wrote and the on-premises placeholder resolves nobody at all; neither
#      authenticates anyone, so neither may switch this off. Reading the BINDING rather than
#      the profile string also answers correctly for a deployment that rebound identity in
#      ``config/settings.yaml`` to its own IdP adapter.
#
# Note what is NOT in this expression: MKT6_S2S_TOKEN. A service credential is evidence about a
# calling SERVICE and says nothing about the end-user routes, so setting one must not, and
# cannot, disable their bound. The S2S routes are bounded by `_authenticate_service_caller`,
# which is where a service credential belongs.
#
# Resolved from ONE settings object, so the two halves of the question cannot answer about
# different resolutions of the profile.
_POSTURE_SETTINGS = get_container().settings
_END_USER_AUTHENTICATED = (
    _POSTURE_SETTINGS.profile_explicit and end_user_auth_kind(_POSTURE_SETTINGS) == VERIFIED
)

# Registered LAST, so it is the OUTERMOST middleware: an off-loopback caller is refused before
# CORS, before the security-header middleware above and before any route or dependency runs.
#
# DO NOT DELETE THIS AND RELY ON A BIND-HOST CHECK IN AN ENTRY POINT. A start-up bound is a
# property of the ONE entry point that calls it, and the shipped entry points do not: this
# repo's Dockerfile ends with
#
#     CMD exec uvicorn marketing_compliance_gate.api.app:app --host 0.0.0.0 --port ${PORT}
#
# and ``make run-api`` hands the same ``marketing_compliance_gate.api.app:app`` object to uvicorn.
# Both reach this module and neither reaches any ``main()``, so the bound has to live on the
# APP OBJECT to hold in a shipped process. Without it, a LAN peer got 200 on /v1/personas with
# the full seeded-persona list and could then act as any of them, including the approver, by
# echoing the id back in ``X-Dev-Persona``.
add_loopback_exposure_guard(
    app,
    unauthenticated=not _END_USER_AUTHENTICATED,
    insecure_demo_env=_INSECURE_DEMO_ENV,
    # The EXPOSURE profile, so a run nobody configured names itself 'unconfigured' in the
    # refusal rather than borrowing the name of a profile an operator never chose.
    posture=_POSTURE_SETTINGS.exposure_profile,
)


@app.get("/healthz", response_model=HealthModel)
def healthz() -> HealthModel:
    settings = Settings.load()
    return HealthModel(
        status="ok",
        profile=settings.profile,
        market=settings.market,
        vertical=settings.vertical,
    )


@app.get(
    "/.well-known/agent-card.json",
    response_model=AgentCardModel,
    tags=["governance"],
)
def agent_card() -> AgentCardModel:
    """Serve the A2A AgentCard for this agent (Hrz3 discovery, rule R4).

    Pure and identity-agnostic: the card advertises the agent's governed skills so a peer
    agent or the registry sees one capability surface. Built from ``agent.agent_card`` with no
    ADK import.
    """
    from ..agent.agent_card import build_agent_card

    return AgentCardModel.from_domain(build_agent_card(get_container().settings))


@app.get("/v1/personas")
def personas() -> list[dict[str, str]]:
    """List seeded dev personas for the local persona picker (empty outside local profile).

    Local mode runs with no IdP; the UI uses this to let a demo/test pick an identity
    (and thus exercise per-user authorization) via the ``X-Dev-Persona`` header. Secure
    profiles resolve identity from the IAP assertion, so this returns an empty list, and so
    does a run that never chose a profile: the persona adapter refuses to construct there,
    which is an empty picker rather than a 500.
    """
    try:
        identity = get_container().identity
    except IdentityError:
        return []
    lister = getattr(identity, "personas", None)
    if lister is None:
        return []
    return [dict(p) for p in lister()]


def _to_asset(body: ReviewRequestModel | SubstantiationRequestModel) -> MarketingAsset:
    a = body.asset
    return MarketingAsset(
        id=a.id,
        asset_type=AssetType(a.asset_type),
        title=a.title,
        body=a.body,
        market=Market(a.market),
        vertical=Vertical(a.vertical),
        fields=dict(a.fields),
        granted_consents=tuple(a.granted_consents),
        submitted_by=a.submitted_by,
    )


@app.post("/v1/review")
def review(body: ReviewRequestModel, principal: CurrentPrincipal) -> dict:
    try:
        request = ReviewRequest(asset=_to_asset(body), actor=principal.actor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = make_review_service().review(
            request, actor=principal.actor, tenant=principal.tenant
        )
    except GuardrailBlockedError as exc:
        raise HTTPException(status_code=400, detail=f"guardrail blocked: {exc}") from exc
    except RuleSetEmptyError as exc:
        raise HTTPException(status_code=404, detail=f"no rule set: {exc}") from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(result)


@app.post("/v1/substantiation", tags=["green-claims"])
def substantiation(body: SubstantiationRequestModel, principal: CurrentPrincipal) -> dict:
    """Assess the asset's green claims against the VERIFIED principal's tenant evidence.

    The tenant is never taken from the request: it is the one the IdentityPort resolved, so
    a caller cannot read another brand's substantiation evidence by asking nicely.
    """
    try:
        asset = _to_asset(body)
        as_of = date.fromisoformat(body.as_of) if body.as_of.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = make_substantiation_service().assess(asset, principal, as_of=as_of)
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GuardrailBlockedError as exc:
        raise HTTPException(status_code=400, detail=f"guardrail blocked: {exc}") from exc
    except GreenClaimPackError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(result)


@app.get("/v1/evidence", tags=["green-claims"])
def list_evidence(asset_id: str, principal: CurrentPrincipal) -> list[dict]:
    """List the substantiation evidence the principal's OWN tenant holds for an asset."""
    try:
        records = make_substantiation_service().evidence_for_asset(asset_id, principal)
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return [to_jsonable(record) for record in records]


@app.get("/v1/evidence/{evidence_id}", tags=["green-claims"])
def get_evidence(evidence_id: str, principal: CurrentPrincipal) -> dict:
    """Read one evidence record; a cross-tenant read is DENIED with 403, not hidden as 404.

    Fail-closed object-level authorization: the record's tenant is compared to the verified
    principal's tenant in the domain service, the denial is audited, and the status code says
    plainly that the request was refused rather than pretending the record does not exist.
    """
    try:
        record = make_substantiation_service().evidence(evidence_id, principal)
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except EvidenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(record)


# --------------------------------------------------------------------------- #
# The consent and preference store
#
# Every route below reads its tenant from the VERIFIED principal, never from the request, so
# a caller cannot ask about or write to another brand's data subject. The decision itself is
# pure code with no model in the path: see domain/consent.py.
# --------------------------------------------------------------------------- #
def _instant(raw: str) -> datetime | None:
    """Parse an optional ISO-8601 instant, normalising a naive value to UTC.

    A naive value compared against a timezone-aware stored window raises, so it is normalised
    at the boundary rather than allowed to reach the engine.
    """
    text = raw.strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@app.post("/v1/consent/decision", tags=["consent"])
def consent_decision(body: ConsentDecisionRequestModel, principal: CurrentPrincipal) -> dict:
    """Decide whether this tenant may contact a data subject, for a purpose, on a channel.

    Deterministic and fail-closed: an unknown consent state is not consent. The response
    carries the decision id, every reason (denying and informational), and the citations of
    the market consent rules that were applied.
    """
    try:
        channel = ConsentChannel(body.channel)
        market = Market(body.market)
        vertical = Vertical(body.vertical)
        as_of = _instant(body.as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        decision = make_consent_service().decide(
            body.subject_id,
            body.purpose,
            channel,
            principal,
            market=market,
            vertical=vertical,
            as_of=as_of,
        )
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(decision)


@app.get("/v1/consent/subjects/{subject_id}", tags=["consent"])
def consent_snapshot(subject_id: str, principal: CurrentPrincipal) -> dict:
    """Everything the principal's OWN tenant holds for one subject, in one read."""
    try:
        snapshot = make_consent_service().snapshot(subject_id, principal)
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(snapshot)


@app.post("/v1/consent/records", tags=["consent"])
def put_consent_record(body: ConsentRecordModel, principal: CurrentPrincipal) -> dict:
    """Store one consent record under the verified tenant.

    A grant the caller cannot evidence is stored pending a checker's confirmation and routed
    to Hrz7; it grants nothing until confirmed. Withdrawals apply immediately.
    """
    try:
        record = ConsentRecord(
            id=body.id,
            tenant="",  # replaced with the verified tenant by the service
            subject_id=body.subject_id,
            purpose=body.purpose,
            status=ConsentStatus(body.status),
            basis=ConsentBasis(body.basis),
            effective_from=_instant(body.effective_from),
            expires_at=_instant(body.expires_at),
            captured_at=utcnow(),
            source=body.source,
            evidence_ref=body.evidence_ref,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        receipt = make_consent_service().record(record, principal)
    except ConsentWriteRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(receipt)


@app.get("/v1/consent/records/{record_id}", tags=["consent"])
def get_consent_record(record_id: str, principal: CurrentPrincipal) -> dict:
    """Read one consent record; a cross-tenant read is DENIED with 403, not hidden as 404."""
    try:
        record = make_consent_service().record_by_id(record_id, principal)
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConsentRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(record)


@app.post("/v1/consent/records/{record_id}/confirm", tags=["consent"])
def confirm_consent_record(
    record_id: str, body: ConsentConfirmationModel, principal: CurrentPrincipal
) -> dict:
    """The checker half: confirm or refuse a grant that landed pending review (rule R8)."""
    try:
        receipt = make_consent_service().confirm(
            record_id, principal, approved=body.approved, rationale=body.rationale
        )
    except ConsentWriteRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConsentRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(receipt)


@app.post("/v1/consent/preferences", tags=["consent"])
def put_channel_preference(body: ChannelPreferenceModel, principal: CurrentPrincipal) -> dict:
    """Store one channel preference. Absence of a preference is never read as an opt-in."""
    try:
        preference = ChannelPreference(
            id=body.id,
            tenant="",
            subject_id=body.subject_id,
            channel=ConsentChannel(body.channel),
            opted_in=body.opted_in,
            updated_at=utcnow(),
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        preference_id = make_consent_service().set_preference(preference, principal)
    except ConsentWriteRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {"preference_id": preference_id}


# --------------------------------------------------------------------------- #
# The service-to-service consent intake (what `consent-preference-kit` talks to)
#
# The routes above authenticate an END USER and take the tenant from the verified principal.
# These two authenticate the CALLING SERVICE instead, which is the only way a proactive
# outreach system with no user in the loop can ask the question at all, and they therefore
# accept the tenant in the body. That is the same trust model Hrz7's own service intake uses:
# the calling service is the trust anchor, and per-hop OAuth2 token exchange (on-behalf-of) is
# the deferred next layer. The scheme is chosen by the EXPOSURE profile, so a run that never
# chose a profile gets no scheme and is refused rather than falling into the loopback
# zero-secret opening.
# --------------------------------------------------------------------------- #
_authenticate_service_caller = make_require_service_caller(
    lambda request: get_container().settings.exposure_profile,
    token_env="MKT6_S2S_TOKEN",
    allowed_callers_env="MKT6_S2S_ALLOWED_CALLERS",
    audience_env="MKT6_S2S_AUDIENCE",
)


def require_service_caller(request: Request) -> None:
    """Authenticate the calling service, fail-closed (the commons picks the scheme)."""
    _authenticate_service_caller(request)


def _service_principal(tenant: str) -> Any:
    """The principal a trusted service call acts as: its asserted tenant, a service subject.

    Built here rather than in the domain so the service layer keeps ONE authorization rule
    (everything is gated on ``principal.tenant``) whichever transport asked the question.
    """
    from ..domain.identity import Principal

    return Principal(subject="s2s:consent-client", tenant=tenant.strip(), source="s2s")


@app.post(
    "/v1/service/consent/decision",
    dependencies=[Depends(require_service_caller)],
    tags=["consent"],
)
def service_consent_decision(body: ServiceConsentDecisionModel) -> dict:
    """Decide consent for a trusted calling service (the S2S half of the consent store)."""
    try:
        channel = ConsentChannel(body.channel)
        market = Market(body.market)
        vertical = Vertical(body.vertical)
        as_of = _instant(body.as_of)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        decision = make_consent_service().decide(
            body.subject_id,
            body.purpose,
            channel,
            _service_principal(body.tenant),
            market=market,
            vertical=vertical,
            as_of=as_of,
        )
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return to_jsonable(decision)


@app.post(
    "/v1/service/consent/sends",
    dependencies=[Depends(require_service_caller)],
    tags=["consent"],
)
def service_record_send(body: ServiceSendEventModel) -> dict:
    """Record a contact a trusted calling service made, so the frequency cap counts it."""
    from ..domain.consent import SendEvent

    try:
        send = SendEvent(
            id=body.id,
            tenant="",  # replaced with the asserted tenant by the service
            subject_id=body.subject_id,
            channel=ConsentChannel(body.channel),
            purpose=body.purpose,
            decision_id=body.decision_id,
            sent_at=_instant(body.sent_at) or utcnow(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        send_id = make_consent_service().note_send(send, _service_principal(body.tenant))
    except ConsentWriteRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {"send_id": send_id}


@app.post("/v1/consent/suppressions", tags=["consent"])
def put_suppression(body: SuppressionEntryModel, principal: CurrentPrincipal) -> dict:
    """Store one suppression entry. It takes effect immediately: it only ever denies."""
    try:
        entry = SuppressionEntry(
            id=body.id,
            tenant="",
            subject_id=body.subject_id,
            scope=SuppressionScope(body.scope),
            reason=SuppressionReason(body.reason),
            channel=ConsentChannel(body.channel) if body.channel.strip() else None,
            purpose=body.purpose,
            effective_from=utcnow(),
            expires_at=_instant(body.expires_at),
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        suppression_id = make_consent_service().suppress(entry, principal)
    except ConsentWriteRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TenantAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {"suppression_id": suppression_id}
