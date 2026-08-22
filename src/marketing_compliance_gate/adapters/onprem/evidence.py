"""On-prem placeholder for ``EvidenceStorePort`` — the sovereign migration target.

A reversibility (no-lock-in) placeholder: in the managed profile this port binds to the
Firestore substantiation-evidence adapter; switching ``profile`` to ``onprem`` rebinds it
here. The adapter constructs cleanly with **no external dependencies** and structurally
satisfies the same Protocol, so the contract tests prove interface parity.

Every method raises rather than returning empty. An evidence store that answered "no
evidence" when it is simply unimplemented would turn every green claim UNSUBSTANTIATED
silently, or worse, be read as "nothing to check": porting on-premise must supply a real
store, wired to the client's document management system, with the SAME tenant semantics
(``list_for_asset`` filters server-side; ``get`` is raw and the domain authorizes).
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import SubstantiationEvidence

_MESSAGE = (
    "On-prem EvidenceStorePort adapter is a migration placeholder; implement against your "
    "on-premise substantiation-evidence store. Core domain logic is unchanged."
)


class OnPremEvidenceStoreAdapter:
    """Placeholder substantiation-evidence adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_for_asset(self, tenant: str, asset_id: str) -> tuple[SubstantiationEvidence, ...]:
        raise NotImplementedError(_MESSAGE)

    def get(self, evidence_id: str) -> SubstantiationEvidence | None:
        raise NotImplementedError(_MESSAGE)

    def put(self, evidence: SubstantiationEvidence) -> str:
        raise NotImplementedError(_MESSAGE)
