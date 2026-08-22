"""Firestore EvidenceStorePort adapter — the managed substantiation-evidence store.

Green-claim substantiation evidence (emissions inventories, offset retirement records,
accredited test reports, fund ESG disclosures) is tenant-owned reference data that a
compliance officer must be able to pull up months later, so on GCP it lives in Firestore in
the market's residency region rather than in the agent's memory.

Two things this adapter is deliberate about:

* **Residency.** The region is resolved and validated from the active market before the
  client is built (``_region.resolve_region``), so evidence never lands outside the JP / AU /
  SG boundary the deployment configured.
* **Tenant isolation.** ``list_for_asset`` composes a server-side ``where`` on BOTH the
  tenant and the asset, so the query itself cannot span tenants. ``get`` is an unfiltered
  fetch by document id because the DOMAIN performs the fail-closed comparison against the
  verified principal's tenant and answers 403; keeping that check in one place stops it from
  drifting between adapters.

All Google Cloud SDK imports are LAZY, so the local / on-prem / test profiles import this
module with no ``google-cloud-firestore`` installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.models import EvidenceKind, GreenClaimCategory, SubstantiationEvidence
from ._region import resolve_region

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.cloud import firestore

_COLLECTION = "mkt6_substantiation_evidence"


class FirestoreEvidenceStoreAdapter:
    """Read and write tenant-scoped substantiation evidence in Firestore."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._collection = _COLLECTION
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy, region-validated client construction
    # ------------------------------------------------------------------ #
    def _get_client(self) -> firestore.Client:  # pragma: no cover - needs the GCP SDK
        resolve_region(self._settings, market=self._settings.active_market)
        if self._client is None:
            from google.cloud import firestore  # noqa: PLC0415 - lazy: gcp profile only

            self._client = firestore.Client(project=self._settings.project_id)
        return self._client

    # ------------------------------------------------------------------ #
    # EvidenceStorePort
    # ------------------------------------------------------------------ #
    def list_for_asset(  # pragma: no cover - needs the GCP SDK
        self, tenant: str, asset_id: str
    ) -> tuple[SubstantiationEvidence, ...]:
        if not tenant:
            return ()  # fail closed: an unresolved tenant reads nothing
        query = (
            self._get_client()
            .collection(self._collection)
            .where("tenant", "==", tenant)
            .where("asset_id", "==", asset_id)
        )
        records = [self._to_evidence(doc.id, doc.to_dict() or {}) for doc in query.stream()]
        return tuple(sorted(records, key=lambda e: e.id))

    def get(  # pragma: no cover - needs the GCP SDK
        self, evidence_id: str
    ) -> SubstantiationEvidence | None:
        doc = self._get_client().collection(self._collection).document(evidence_id).get()
        if not doc.exists:
            return None
        return self._to_evidence(doc.id, doc.to_dict() or {})

    def put(self, evidence: SubstantiationEvidence) -> str:  # pragma: no cover - needs the SDK
        self._get_client().collection(self._collection).document(evidence.id).set(
            self._to_document(evidence)
        )
        return evidence.id

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_document(evidence: SubstantiationEvidence) -> dict[str, Any]:
        return {
            "tenant": evidence.tenant,
            "asset_id": evidence.asset_id,
            "kind": evidence.kind.value,
            "title": evidence.title,
            "categories": [c.value for c in evidence.categories],
            "issued_date": evidence.issued_date,
            "valid_until": evidence.valid_until,
            "issuer": evidence.issuer,
            "independently_verified": evidence.independently_verified,
            "reference": evidence.reference,
        }

    @staticmethod
    def _to_evidence(doc_id: str, data: dict[str, Any]) -> SubstantiationEvidence:
        return SubstantiationEvidence(
            id=doc_id,
            tenant=str(data.get("tenant", "")),
            asset_id=str(data.get("asset_id", "")),
            kind=EvidenceKind(str(data.get("kind", EvidenceKind.SUPPLIER_ATTESTATION.value))),
            title=str(data.get("title", "")),
            categories=tuple(GreenClaimCategory(str(c)) for c in (data.get("categories") or [])),
            issued_date=str(data.get("issued_date", "") or ""),
            valid_until=str(data.get("valid_until", "") or ""),
            issuer=str(data.get("issuer", "") or ""),
            independently_verified=bool(data.get("independently_verified", False)),
            reference=str(data.get("reference", "") or ""),
        )
