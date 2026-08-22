"""Firestore ConsentStorePort adapter — the managed consent and preference store.

A subject's consent records, channel preferences, suppression entries, frequency caps and
recorded sends are tenant-owned personal data that a compliance officer (or a regulator) must
be able to pull up months later, so on GCP they live in Firestore in the market's residency
region rather than in the agent's memory. Mirrors the substantiation-evidence adapter next
door, deliberately, so the two tenant-owned stores have one posture between them.

Two things this adapter is deliberate about:

* **Residency.** The region is resolved and validated from the active market before the
  client is built (``_region.resolve_region``), so consent records never land outside the
  JP / AU / SG boundary the deployment configured. Consent data is precisely the data a
  residency rule exists for.
* **Tenant isolation.** ``snapshot`` composes a server-side ``where`` on the tenant AND the
  subject, so the query itself cannot span tenants. ``get_record`` is an unfiltered fetch by
  document id because the DOMAIN performs the fail-closed comparison against the verified
  principal's tenant and answers 403; keeping that check in one place stops it from drifting
  between adapters.

All Google Cloud SDK imports are LAZY, so the local / on-prem / test profiles import this
module with no ``google-cloud-firestore`` installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.consent import (
    ChannelPreference,
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentSnapshot,
    ConsentStatus,
    FrequencyCap,
    SendEvent,
    SuppressionEntry,
    SuppressionReason,
    SuppressionScope,
)
from ._region import resolve_region

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.cloud import firestore

_RECORDS = "mkt6_consent_records"
_PREFERENCES = "mkt6_channel_preferences"
_SUPPRESSIONS = "mkt6_consent_suppressions"
_CAPS = "mkt6_frequency_caps"
_SENDS = "mkt6_consent_sends"


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _parse(value: Any) -> datetime | None:
    """Read a stored timestamp back, tolerating both ISO strings and native timestamps."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class FirestoreConsentStoreAdapter:
    """Read and write tenant-scoped consent and preference state in Firestore."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
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
    # ConsentStorePort — reads
    # ------------------------------------------------------------------ #
    def snapshot(  # pragma: no cover - needs the GCP SDK
        self, tenant: str, subject_id: str
    ) -> ConsentSnapshot:
        if not tenant or not subject_id:
            return ConsentSnapshot(tenant=tenant, subject_id=subject_id)
        client = self._get_client()

        def _subject(collection: str) -> list[Any]:
            query = (
                client.collection(collection)
                .where("tenant", "==", tenant)
                .where("subject_id", "==", subject_id)
            )
            return list(query.stream())

        records = [self._to_record(d.id, d.to_dict() or {}) for d in _subject(_RECORDS)]
        preferences = [self._to_preference(d.id, d.to_dict() or {}) for d in _subject(_PREFERENCES)]
        suppressions = [
            self._to_suppression(d.id, d.to_dict() or {}) for d in _subject(_SUPPRESSIONS)
        ]
        caps = [
            self._to_cap(d.id, d.to_dict() or {})
            for d in client.collection(_CAPS).where("tenant", "==", tenant).stream()
        ]
        return ConsentSnapshot(
            tenant=tenant,
            subject_id=subject_id,
            records=tuple(sorted(records, key=lambda r: r.id)),
            preferences=tuple(sorted(preferences, key=lambda p: p.id)),
            suppressions=tuple(sorted(suppressions, key=lambda s: s.id)),
            caps=tuple(sorted(caps, key=lambda c: c.id)),
        )

    def get_record(  # pragma: no cover - needs the GCP SDK
        self, record_id: str
    ) -> ConsentRecord | None:
        doc = self._get_client().collection(_RECORDS).document(record_id).get()
        if not doc.exists:
            return None
        return self._to_record(doc.id, doc.to_dict() or {})

    def count_sends(  # pragma: no cover - needs the GCP SDK
        self, tenant: str, subject_id: str, channel: ConsentChannel, since: datetime
    ) -> int:
        if not tenant or not subject_id:
            return 0
        query = (
            self._get_client()
            .collection(_SENDS)
            .where("tenant", "==", tenant)
            .where("subject_id", "==", subject_id)
            .where("channel", "==", channel.value)
            .where("sent_at", ">=", since.isoformat())
        )
        return sum(1 for _ in query.stream())

    # ------------------------------------------------------------------ #
    # ConsentStorePort — writes
    # ------------------------------------------------------------------ #
    def put_record(self, record: ConsentRecord) -> str:  # pragma: no cover - needs the SDK
        self._get_client().collection(_RECORDS).document(record.id).set(
            {
                "tenant": record.tenant,
                "subject_id": record.subject_id,
                "purpose": record.purpose,
                "status": record.status.value,
                "basis": record.basis.value,
                "effective_from": _iso(record.effective_from),
                "expires_at": _iso(record.expires_at),
                "captured_at": _iso(record.captured_at),
                "source": record.source,
                "evidence_ref": record.evidence_ref,
                "note": record.note,
            }
        )
        return record.id

    def put_preference(  # pragma: no cover - needs the SDK
        self, preference: ChannelPreference
    ) -> str:
        self._get_client().collection(_PREFERENCES).document(preference.id).set(
            {
                "tenant": preference.tenant,
                "subject_id": preference.subject_id,
                "channel": preference.channel.value,
                "opted_in": preference.opted_in,
                "updated_at": _iso(preference.updated_at),
                "source": preference.source,
            }
        )
        return preference.id

    def put_suppression(self, entry: SuppressionEntry) -> str:  # pragma: no cover - needs the SDK
        self._get_client().collection(_SUPPRESSIONS).document(entry.id).set(
            {
                "tenant": entry.tenant,
                "subject_id": entry.subject_id,
                "scope": entry.scope.value,
                "reason": entry.reason.value,
                "channel": entry.channel.value if entry.channel is not None else "",
                "purpose": entry.purpose,
                "effective_from": _iso(entry.effective_from),
                "expires_at": _iso(entry.expires_at),
                "note": entry.note,
            }
        )
        return entry.id

    def put_cap(self, cap: FrequencyCap) -> str:  # pragma: no cover - needs the SDK
        self._get_client().collection(_CAPS).document(cap.id).set(
            {
                "tenant": cap.tenant,
                "channel": cap.channel.value,
                "max_messages": int(cap.max_messages),
                "window_hours": int(cap.window_hours),
                "purpose": cap.purpose,
            }
        )
        return cap.id

    def record_send(self, send: SendEvent) -> str:  # pragma: no cover - needs the SDK
        self._get_client().collection(_SENDS).document(send.id).set(
            {
                "tenant": send.tenant,
                "subject_id": send.subject_id,
                "channel": send.channel.value,
                "purpose": send.purpose,
                "decision_id": send.decision_id,
                "sent_at": _iso(send.sent_at),
            }
        )
        return send.id

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_record(doc_id: str, data: dict[str, Any]) -> ConsentRecord:
        captured = _parse(data.get("captured_at"))
        return ConsentRecord(
            id=doc_id,
            tenant=str(data.get("tenant", "")),
            subject_id=str(data.get("subject_id", "")),
            purpose=str(data.get("purpose", "")),
            # An unrecognised status is read as UNKNOWN, which denies. A store row nobody
            # modelled must never be the thing that grants permission to contact a person.
            status=ConsentStatus(str(data.get("status", ConsentStatus.UNKNOWN.value))),
            basis=ConsentBasis(str(data.get("basis", ConsentBasis.EXPLICIT_OPT_IN.value))),
            effective_from=_parse(data.get("effective_from")),
            expires_at=_parse(data.get("expires_at")),
            captured_at=captured if captured is not None else datetime(1970, 1, 1, tzinfo=UTC),
            source=str(data.get("source", "") or ""),
            evidence_ref=str(data.get("evidence_ref", "") or ""),
            note=str(data.get("note", "") or ""),
        )

    @staticmethod
    def _to_preference(doc_id: str, data: dict[str, Any]) -> ChannelPreference:
        updated = _parse(data.get("updated_at"))
        return ChannelPreference(
            id=doc_id,
            tenant=str(data.get("tenant", "")),
            subject_id=str(data.get("subject_id", "")),
            channel=ConsentChannel(str(data.get("channel", ConsentChannel.EMAIL.value))),
            opted_in=bool(data.get("opted_in", False)),
            updated_at=updated if updated is not None else datetime(1970, 1, 1, tzinfo=UTC),
            source=str(data.get("source", "") or ""),
        )

    @staticmethod
    def _to_suppression(doc_id: str, data: dict[str, Any]) -> SuppressionEntry:
        channel = str(data.get("channel", "") or "").strip()
        return SuppressionEntry(
            id=doc_id,
            tenant=str(data.get("tenant", "")),
            subject_id=str(data.get("subject_id", "")),
            scope=SuppressionScope(str(data.get("scope", SuppressionScope.ALL.value))),
            reason=SuppressionReason(
                str(data.get("reason", SuppressionReason.SUBJECT_REQUEST.value))
            ),
            channel=ConsentChannel(channel) if channel else None,
            purpose=str(data.get("purpose", "") or ""),
            effective_from=_parse(data.get("effective_from")),
            expires_at=_parse(data.get("expires_at")),
            note=str(data.get("note", "") or ""),
        )

    @staticmethod
    def _to_cap(doc_id: str, data: dict[str, Any]) -> FrequencyCap:
        return FrequencyCap(
            id=doc_id,
            tenant=str(data.get("tenant", "")),
            channel=ConsentChannel(str(data.get("channel", ConsentChannel.EMAIL.value))),
            max_messages=int(data.get("max_messages", 0)),
            window_hours=int(data.get("window_hours", 0)),
            purpose=str(data.get("purpose", "") or ""),
        )
