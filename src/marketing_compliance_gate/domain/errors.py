"""Domain exceptions for the Marketing Compliance and Brand Governance system (D6).

Pure-Python exception hierarchy raised by the orchestration services. The domain layer
never imports Google Cloud, ADK, or any framework: these errors let callers (API, CLI,
the agent runtime adapter) react to domain-level failures without coupling to any vendor
SDK error type.
"""

from __future__ import annotations


class ComplianceGovError(Exception):
    """Base class for all domain-level errors raised by D6 services."""


class GuardrailBlockedError(ComplianceGovError):
    """Raised when the A1 guardrail blocks an input or output.

    A blocked unsafe request must never yield a partial review: the orchestrator raises
    this rather than reviewing screened-out content.
    """


class RuleSetEmptyError(ComplianceGovError):
    """Raised when no rules are configured for the asset's (market, vertical).

    A compliance review must be grounded in a rule set; an empty rule set is a hard
    error so a review is never reported as "compliant" merely because no rule ran.
    """


class GreenClaimPackError(ComplianceGovError):
    """Raised when the jurisdiction green-claim rule pack is missing or malformed.

    Fail-closed: the substantiation gate is only as good as its pack, so an unreadable or
    invalid pack is a hard error rather than a silently empty rule set that would let every
    green claim through unchecked.
    """


class TenantAccessDeniedError(ComplianceGovError):
    """Raised when a principal reads substantiation evidence outside its own tenant.

    Object-level authorization is fail-closed and server-verified: the check is made
    against the VERIFIED principal's tenant, never a client-supplied value, and it is a
    denial (HTTP 403), not a not-found, so the boundary is explicit in the audit trail.
    """


class EvidenceNotFoundError(ComplianceGovError):
    """Raised when no substantiation evidence exists for the requested id (HTTP 404)."""


class ConsentRecordNotFoundError(ComplianceGovError):
    """Raised when no consent record exists for the requested id (HTTP 404)."""


class ConsentWriteRejectedError(ComplianceGovError):
    """Raised when a consent write is refused before it reaches the store (HTTP 422).

    Fail-closed at the write boundary: a record that names no subject or no purpose, or a
    suppression entry whose scope names nothing it could suppress, would land as data the
    engine can never resolve. Refusing it is safer than storing a row that silently
    participates in no decision.
    """


class UnsupportedMarketError(ComplianceGovError):
    """Raised when a requested market has no configured residency region / profile."""


class UnsupportedVerticalError(ComplianceGovError):
    """Raised when a requested vertical has no configured rule set / taxonomy."""
