"""On-prem placeholder for ``ConsentStorePort`` — the sovereign migration target.

A reversibility (no-lock-in) placeholder: in the managed profile this port binds to the
Firestore consent and preference adapter; switching ``profile`` to ``onprem`` rebinds it
here. The adapter constructs cleanly with **no external dependencies** and structurally
satisfies the same Protocol, so the contract tests prove interface parity.

Every method raises rather than returning empty. An empty snapshot is a perfectly valid
answer meaning "this subject granted nothing", and the engine correctly denies on it, so an
unimplemented store that returned one would look like it was working: every decision would
come back DENIED for the right-looking reason while no withdrawal, suppression or cap was
ever actually being recorded. Porting on-premise must supply a real store, wired to the
client's own preference platform, with the SAME tenant semantics (``snapshot`` filters
server-side; ``get_record`` is raw and the domain authorizes).
"""

from __future__ import annotations

from datetime import datetime

from ...config import Settings
from ...domain.consent import (
    ChannelPreference,
    ConsentChannel,
    ConsentRecord,
    ConsentSnapshot,
    FrequencyCap,
    SendEvent,
    SuppressionEntry,
)

_MESSAGE = (
    "On-prem ConsentStorePort adapter is a migration placeholder; implement against your "
    "on-premise consent and preference platform. Core domain logic is unchanged."
)


class OnPremConsentStoreAdapter:
    """Placeholder consent and preference adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def snapshot(self, tenant: str, subject_id: str) -> ConsentSnapshot:
        raise NotImplementedError(_MESSAGE)

    def get_record(self, record_id: str) -> ConsentRecord | None:
        raise NotImplementedError(_MESSAGE)

    def put_record(self, record: ConsentRecord) -> str:
        raise NotImplementedError(_MESSAGE)

    def put_preference(self, preference: ChannelPreference) -> str:
        raise NotImplementedError(_MESSAGE)

    def put_suppression(self, entry: SuppressionEntry) -> str:
        raise NotImplementedError(_MESSAGE)

    def put_cap(self, cap: FrequencyCap) -> str:
        raise NotImplementedError(_MESSAGE)

    def record_send(self, send: SendEvent) -> str:
        raise NotImplementedError(_MESSAGE)

    def count_sends(
        self, tenant: str, subject_id: str, channel: ConsentChannel, since: datetime
    ) -> int:
        raise NotImplementedError(_MESSAGE)
