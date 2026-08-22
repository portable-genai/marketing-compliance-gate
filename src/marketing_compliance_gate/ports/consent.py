"""ConsentStorePort — the tenant-scoped consent and preference store.

The catalog's consent and preference store lives here, inside Mkt6, because Mkt6 already
models consent (the ``CONSENT``-kind rule and its ``CONSENT_REQUIRED`` check) and is already
the mandatory dependency of the proactive-outreach system that consumes it. This port is the
hexagon boundary to wherever a subject's consent records, channel preferences, frequency
caps, suppression entries and recorded sends actually live.

The adapter family deliberately MIRRORS the substantiation
:mod:`~marketing_compliance_gate.ports.evidence` store, method for method and posture for
posture, because the two solve the same problem (tenant-owned records a compliance officer
must be able to pull up months later) and a second, differently-shaped store would be a
second set of mistakes:

* the GCP adapter is Firestore in the active market's residency region,
* the ``local`` adapter is an SDK-free SQLite store seeded with obviously fictional subjects
  for two tenants, so the tenant boundary is demoable and testable offline, and
* the ``onprem`` adapter is the fail-fast migration placeholder for the client's own
  preference platform.

Authorization contract (fail-closed, server-verified)
-----------------------------------------------------
Consent records are personal data, so the reads differ deliberately, exactly as they do on
the evidence store:

* :meth:`snapshot` takes the tenant and MUST filter on it in the store, so a read can never
  span tenants, and
* :meth:`get_record` is a raw fetch by id that does NOT filter: the caller
  (:class:`~marketing_compliance_gate.domain.consent_service.ConsentService`) compares the
  record's tenant to the VERIFIED principal's tenant and denies with
  ``TenantAccessDeniedError`` (HTTP 403). Keeping the check in the domain means every driving
  adapter inherits it and no adapter becomes the only place the boundary is enforced.

Never pass a client-supplied tenant into either method: the tenant comes from the
:class:`~marketing_compliance_gate.domain.identity.Principal` the IdentityPort verified.

Why a snapshot rather than four reads
-------------------------------------
:meth:`snapshot` returns records, preferences, suppressions and caps together. A decision
assembled from several reads taken at different instants is not replayable, and a withdrawal
landing between two of them would be missed. One read, one decision, one audit record.

An unimplemented adapter must RAISE, never return an empty snapshot. An empty snapshot is
indistinguishable from "this subject granted nothing", which the engine correctly reads as a
denial, but an on-prem port that silently answers "nothing on file" for every subject would
be a store that quietly stops recording withdrawals too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.consent import (
    ChannelPreference,
    ConsentChannel,
    ConsentRecord,
    ConsentSnapshot,
    FrequencyCap,
    SendEvent,
    SuppressionEntry,
)


@runtime_checkable
class ConsentStorePort(Protocol):
    def snapshot(self, tenant: str, subject_id: str) -> ConsentSnapshot:
        """Everything held for one (tenant, subject); the tenant filter is IN the store."""
        ...

    def get_record(self, record_id: str) -> ConsentRecord | None:
        """Return one consent record by id, or ``None``; the DOMAIN authorizes the tenant."""
        ...

    def put_record(self, record: ConsentRecord) -> str:
        """Upsert one consent record and return its id."""
        ...

    def put_preference(self, preference: ChannelPreference) -> str:
        """Upsert one channel preference and return its id."""
        ...

    def put_suppression(self, entry: SuppressionEntry) -> str:
        """Upsert one suppression entry and return its id."""
        ...

    def put_cap(self, cap: FrequencyCap) -> str:
        """Upsert one tenant frequency cap and return its id."""
        ...

    def record_send(self, send: SendEvent) -> str:
        """Record one contact, which is what the frequency cap counts. Returns its id."""
        ...

    def count_sends(
        self, tenant: str, subject_id: str, channel: ConsentChannel, since: datetime
    ) -> int:
        """Count this tenant's recorded sends to the subject on the channel since ``since``."""
        ...
