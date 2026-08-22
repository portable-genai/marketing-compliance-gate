"""Every domain span carries structural attributes only, never request content.

A trace backend is not the WORM audit trail: it has no redaction stage, no retention policy
written against a regulator's requirement, and a far wider read audience than the audit
store. This repo opens spans from three domain services (asset review, green-claims
substantiation, consent), so the value of tracing depends on every one of those spans
carrying structural attributes only (which market, which vertical, whose tenant), never an
asset's body, a data subject's identifier or anything else a marketer or customer supplied.

The container's local tracer only logs span names, so a leaked attribute would be invisible
to the rest of the suite; this module swaps in a tracer that records the attributes too and
drives the three REAL request paths (review, assess, decide/snapshot/record) with planted,
obviously fictional identifiers in the content so a leak would actually show.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from marketing_compliance_gate.config import Container
from marketing_compliance_gate.domain.consent import (
    ConsentChannel,
    ConsentRecord,
    ConsentStatus,
)
from marketing_compliance_gate.domain.consent_service import ConsentService
from marketing_compliance_gate.domain.identity import Principal
from marketing_compliance_gate.domain.models import (
    AssetType,
    Market,
    MarketingAsset,
    ReviewRequest,
    Vertical,
)
from marketing_compliance_gate.domain.services import ReviewService
from marketing_compliance_gate.domain.substantiation import SubstantiationService
from marketing_compliance_gate.green_pack import pack_for

#: The full attribute allowlist per span name. A new attribute is a deliberate decision
#: made here, not a drive-by keyword argument.
_ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "review.build": frozenset({"market", "vertical"}),
    "substantiation.assess": frozenset({"market", "vertical"}),
    "consent.decide": frozenset({"tenant", "market"}),
    "consent.snapshot": frozenset({"tenant"}),
    "consent.record": frozenset({"tenant"}),
}

#: Planted content markers (all obviously fictional). If any span attribute value carried
#: request content, one of these would surface in the recorded attributes.
_PLANTED_IDENTIFIER = "S1234567D"
_PLANTED_BODY = (
    "A balanced message for customer "
    f"{_PLANTED_IDENTIFIER} (PLANTED, FICTIONAL) with no guarantees."
)
_PLANTED_SUBJECT = "subj-000101"

_PRINCIPAL = Principal(subject="officer@example.com", tenant="demo-brand", source="test")


class _RecordingTracer:
    """Records every span name AND its attributes, unlike the name-only local tracer."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append((name, dict(attributes)))
        yield


def _asset() -> MarketingAsset:
    return MarketingAsset(
        id="asset-span-0001",
        asset_type=AssetType.CREATIVE,
        title="Span guard asset (FICTIONAL)",
        body=_PLANTED_BODY,
        market=Market.SG,
        vertical=Vertical.BANKING,
        fields={},
        granted_consents=(),
    )


def _review_spans(container: Container) -> _RecordingTracer:
    tracer = _RecordingTracer()
    ReviewService(
        rule_provider=container.rule_provider,
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=tracer,
        audit=container.audit,
    ).review(ReviewRequest(asset=_asset()), actor="span-test-bot (FICTIONAL)")
    return tracer


def _substantiation_spans(container: Container) -> _RecordingTracer:
    tracer = _RecordingTracer()
    SubstantiationService(
        evidence_store=container.evidence_store,
        pack=pack_for(container.settings),
        llm=container.llm,
        guardrail=container.guardrail,
        tracer=tracer,
        audit=container.audit,
    ).assess(_asset(), _PRINCIPAL)
    return tracer


def _consent_spans(container: Container) -> _RecordingTracer:
    """Drive all three consent paths: decide, snapshot, and a write with its receipt."""
    tracer = _RecordingTracer()
    service = ConsentService(
        consent_store=container.consent_store,
        rule_provider=container.rule_provider,
        tracer=tracer,
        audit=container.audit,
    )
    # Time-relative on purpose: the seeded consent windows run for years, and a fixed date
    # here is how the consent suite went stale once already.
    now = datetime.now(UTC)
    service.decide(
        _PLANTED_SUBJECT,
        "marketing",
        ConsentChannel.EMAIL,
        _PRINCIPAL,
        market=Market.SG,
        vertical=Vertical.BANKING,
        as_of=now,
    )
    service.snapshot(_PLANTED_SUBJECT, _PRINCIPAL)
    service.record(
        ConsentRecord(
            id="cr-span-guard-0001",
            tenant=_PRINCIPAL.tenant,
            subject_id=_PLANTED_SUBJECT,
            purpose="marketing",
            status=ConsentStatus.WITHDRAWN,
            captured_at=now,
        ),
        _PRINCIPAL,
    )
    return tracer


def _all_spans(container: Container) -> list[tuple[str, dict[str, str]]]:
    return (
        _review_spans(container).spans
        + _substantiation_spans(container).spans
        + _consent_spans(container).spans
    )


def test_each_request_path_opens_its_named_spans(local_container: Container) -> None:
    assert [n for n, _ in _review_spans(local_container).spans] == ["review.build"]
    assert [n for n, _ in _substantiation_spans(local_container).spans] == ["substantiation.assess"]
    consent_names = [n for n, _ in _consent_spans(local_container).spans]
    assert consent_names.count("consent.decide") == 1
    assert consent_names.count("consent.record") == 1
    assert "consent.snapshot" in consent_names
    assert set(consent_names) <= {"consent.decide", "consent.snapshot", "consent.record"}


def test_every_span_attribute_set_is_a_fixed_allowlist(local_container: Container) -> None:
    """No span may start attaching content to explain itself, whatever the verdict."""
    for name, attributes in _all_spans(local_container):
        assert name in _ALLOWED_ATTRIBUTES, f"unexpected span {name!r}"
        assert set(attributes) == _ALLOWED_ATTRIBUTES[name], name


def test_no_span_attribute_value_carries_planted_content(local_container: Container) -> None:
    """The asset body and the consent subject carry planted markers, so a leak would show."""
    emitted = " ".join(
        value for _, attributes in _all_spans(local_container) for value in attributes.values()
    )
    assert _PLANTED_IDENTIFIER not in emitted
    assert _PLANTED_IDENTIFIER.lower() not in emitted.lower()
    assert _PLANTED_SUBJECT not in emitted
    assert _PLANTED_BODY not in emitted


def test_the_allowed_attributes_are_structural(local_container: Container) -> None:
    """The allowed values answer "which market, which vertical, whose tenant", nothing more."""
    for name, attributes in _all_spans(local_container):
        if "market" in attributes:
            assert attributes["market"] == Market.SG.value, name
        if "vertical" in attributes:
            assert attributes["vertical"] == Vertical.BANKING.value, name
        if "tenant" in attributes:
            assert attributes["tenant"] == _PRINCIPAL.tenant, name
