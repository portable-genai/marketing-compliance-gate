"""EvidenceStorePort — the tenant-scoped store of green-claim substantiation evidence.

The green-claims gate is only as good as the evidence a brand actually holds: the emissions
inventory behind a carbon-neutral claim, the accredited test report behind a biodegradable
claim, the fund's ESG disclosure behind a sustainability label. This port is the hexagon
boundary to wherever that evidence lives. The GCP adapter is Firestore in the market's
residency region, the ``local`` adapter is an SDK-free SQLite store seeded with obviously
fictional records, and the ``onprem`` adapter is the fail-fast migration placeholder for the
client's own document store.

Authorization contract (fail-closed, server-verified)
-----------------------------------------------------
Evidence is tenant-owned data, so the two read methods differ deliberately:

* :meth:`list_for_asset` takes the tenant and MUST filter on it in the store, so a query can
  never span tenants, and
* :meth:`get` is a raw fetch by id that does NOT filter: the caller
  (:class:`~marketing_compliance_gate.domain.substantiation.SubstantiationService`) compares the
  record's tenant to the VERIFIED principal's tenant and denies with
  ``TenantAccessDeniedError`` (HTTP 403). Keeping the check in the domain means every driving
  adapter (API, CLI, agent) inherits it, and an adapter cannot become the only place the
  boundary is enforced.

Never pass a client-supplied tenant into either method: the tenant comes from the
:class:`~marketing_compliance_gate.domain.identity.Principal` the IdentityPort verified.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import SubstantiationEvidence


@runtime_checkable
class EvidenceStorePort(Protocol):
    def list_for_asset(self, tenant: str, asset_id: str) -> tuple[SubstantiationEvidence, ...]:
        """Return the evidence ``tenant`` holds for ``asset_id`` (store-side tenant filter)."""
        ...

    def get(self, evidence_id: str) -> SubstantiationEvidence | None:
        """Return one evidence record by id, or ``None``; the DOMAIN authorizes the tenant."""
        ...

    def put(self, evidence: SubstantiationEvidence) -> str:
        """Upsert one evidence record and return its id."""
        ...
