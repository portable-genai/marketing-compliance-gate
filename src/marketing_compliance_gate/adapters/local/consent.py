"""Local ConsentStorePort adapter — SDK-free SQLite consent and preference store.

The ``local`` profile's stand-in for the managed store (Firestore on GCP): five ``sqlite3``
tables (records, channel preferences, suppressions, frequency caps, recorded sends) seeded
with obviously fictional subjects across two tenants, so the tenant boundary and every branch
of the deterministic decision are demoable and testable offline, with no Google Cloud SDK and
no network.

It mirrors the substantiation-evidence adapter next door on purpose, down to the connection
handling and the split of responsibilities:

* :meth:`snapshot` filters on ``tenant`` IN SQL, so a read can never span tenants even if a
  caller passes the wrong subject id, and
* :meth:`get_record` is deliberately an UNFILTERED fetch by id, because the domain service is
  where the fail-closed comparison against the verified principal's tenant happens and
  returns a 403 denial (see ``ports/consent.py`` and ``domain/consent_service.py``). That
  split is what makes the cross-tenant denial test meaningful: the store hands over the
  record, and the domain refuses to serve it.

Datetimes are stored as ISO-8601 strings and read back timezone-aware. An empty string is a
null, so "no expiry" and "expired at the epoch" can never be confused.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

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
from ._consent_seed import SEED_CAPS, SEED_PREFERENCES, SEED_RECORDS, SEED_SUPPRESSIONS

_DEFAULT_DB_DIR = Path.home() / ".marketing_compliance_gate"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "consent.db"


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _parse(value: str | None) -> datetime | None:
    """Parse a stored ISO timestamp, or ``None``. Naive values are read as UTC.

    A naive datetime compared against a timezone-aware ``as_of`` raises, which would turn a
    stray legacy row into a 500 on every decision for that subject. Normalising here keeps
    the engine's comparisons total.
    """
    text = (value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class LocalConsentStoreAdapter:
    """Serve tenant-scoped consent, preference, cap and suppression state from SQLite."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = getattr(getattr(settings, "local", None), "consent_path", "") or str(
            _DEFAULT_DB_PATH
        )
        self._db_path = db_path
        # check_same_thread=False + an RLock: the container is process-wide while the sync
        # API endpoints run in Starlette's worker threadpool (same rationale as the rule and
        # evidence stores).
        self._lock = threading.RLock()
        self._conn = self._connect(db_path)
        self._init_schema()
        if self._is_empty():
            self.seed()

    # ------------------------------------------------------------------ #
    # Connection / schema
    # ------------------------------------------------------------------ #
    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        if db_path not in (":memory:", "") and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS consent_records (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    basis TEXT NOT NULL DEFAULT 'explicit_opt_in',
                    effective_from TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    evidence_ref TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_preferences (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    opted_in INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS suppressions (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT '',
                    purpose TEXT NOT NULL DEFAULT '',
                    effective_from TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS frequency_caps (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    max_messages INTEGER NOT NULL DEFAULT 0,
                    window_hours INTEGER NOT NULL DEFAULT 0,
                    purpose TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS send_events (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    decision_id TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            for statement in (
                "CREATE INDEX IF NOT EXISTS cr_tenant_subject "
                "ON consent_records (tenant, subject_id)",
                "CREATE INDEX IF NOT EXISTS cp_tenant_subject "
                "ON channel_preferences (tenant, subject_id)",
                "CREATE INDEX IF NOT EXISTS sup_tenant_subject "
                "ON suppressions (tenant, subject_id)",
                "CREATE INDEX IF NOT EXISTS fc_tenant ON frequency_caps (tenant)",
                "CREATE INDEX IF NOT EXISTS se_tenant_subject_channel "
                "ON send_events (tenant, subject_id, channel)",
            ):
                self._conn.execute(statement)
            self._conn.commit()

    def _is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM consent_records").fetchone()
        return int(row["n"]) == 0

    # ------------------------------------------------------------------ #
    # Seeding
    # ------------------------------------------------------------------ #
    def seed(
        self,
        records: tuple[ConsentRecord, ...] | None = None,
        preferences: tuple[ChannelPreference, ...] | None = None,
        suppressions: tuple[SuppressionEntry, ...] | None = None,
        caps: tuple[FrequencyCap, ...] | None = None,
    ) -> int:
        """Replace the store contents (deterministic test / demo seed). Returns record count."""
        records = SEED_RECORDS if records is None else records
        preferences = SEED_PREFERENCES if preferences is None else preferences
        suppressions = SEED_SUPPRESSIONS if suppressions is None else suppressions
        caps = SEED_CAPS if caps is None else caps
        with self._lock:
            for table in (
                "consent_records",
                "channel_preferences",
                "suppressions",
                "frequency_caps",
                "send_events",
            ):
                self._conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed literal names
            for record in records:
                self.put_record(record)
            for preference in preferences:
                self.put_preference(preference)
            for entry in suppressions:
                self.put_suppression(entry)
            for cap in caps:
                self.put_cap(cap)
        return len(records)

    # ------------------------------------------------------------------ #
    # ConsentStorePort — reads
    # ------------------------------------------------------------------ #
    def snapshot(self, tenant: str, subject_id: str) -> ConsentSnapshot:
        """Everything held for (tenant, subject); the tenant filter is in every query."""
        if not tenant or not subject_id:
            # Fail closed: an unresolved tenant or subject reads nothing rather than
            # everything. The engine then denies, because an empty snapshot grants nothing.
            return ConsentSnapshot(tenant=tenant, subject_id=subject_id)
        with self._lock:
            record_rows = self._conn.execute(
                "SELECT * FROM consent_records WHERE tenant = ? AND subject_id = ? ORDER BY id",
                (tenant, subject_id),
            ).fetchall()
            preference_rows = self._conn.execute(
                "SELECT * FROM channel_preferences WHERE tenant = ? AND subject_id = ? ORDER BY id",
                (tenant, subject_id),
            ).fetchall()
            suppression_rows = self._conn.execute(
                "SELECT * FROM suppressions WHERE tenant = ? AND subject_id = ? ORDER BY id",
                (tenant, subject_id),
            ).fetchall()
            cap_rows = self._conn.execute(
                "SELECT * FROM frequency_caps WHERE tenant = ? ORDER BY id",
                (tenant,),
            ).fetchall()
        return ConsentSnapshot(
            tenant=tenant,
            subject_id=subject_id,
            records=tuple(self._to_record(row) for row in record_rows),
            preferences=tuple(self._to_preference(row) for row in preference_rows),
            suppressions=tuple(self._to_suppression(row) for row in suppression_rows),
            caps=tuple(self._to_cap(row) for row in cap_rows),
        )

    def get_record(self, record_id: str) -> ConsentRecord | None:
        """Raw fetch by id: the DOMAIN authorizes the tenant, never this adapter."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM consent_records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._to_record(row) if row is not None else None

    def count_sends(
        self, tenant: str, subject_id: str, channel: ConsentChannel, since: datetime
    ) -> int:
        if not tenant or not subject_id:
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*) AS n FROM send_events WHERE tenant = ? AND subject_id = ? "
                "AND channel = ? AND sent_at >= ?",
                (tenant, subject_id, channel.value, since.isoformat()),
            ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # ConsentStorePort — writes
    # ------------------------------------------------------------------ #
    def put_record(self, record: ConsentRecord) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO consent_records "
                "(id, tenant, subject_id, purpose, status, basis, effective_from, expires_at, "
                "captured_at, source, evidence_ref, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.tenant,
                    record.subject_id,
                    record.purpose,
                    record.status.value,
                    record.basis.value,
                    _iso(record.effective_from),
                    _iso(record.expires_at),
                    _iso(record.captured_at),
                    record.source,
                    record.evidence_ref,
                    record.note,
                ),
            )
            self._conn.commit()
        return record.id

    def put_preference(self, preference: ChannelPreference) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO channel_preferences "
                "(id, tenant, subject_id, channel, opted_in, updated_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    preference.id,
                    preference.tenant,
                    preference.subject_id,
                    preference.channel.value,
                    1 if preference.opted_in else 0,
                    _iso(preference.updated_at),
                    preference.source,
                ),
            )
            self._conn.commit()
        return preference.id

    def put_suppression(self, entry: SuppressionEntry) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO suppressions "
                "(id, tenant, subject_id, scope, reason, channel, purpose, effective_from, "
                "expires_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id,
                    entry.tenant,
                    entry.subject_id,
                    entry.scope.value,
                    entry.reason.value,
                    entry.channel.value if entry.channel is not None else "",
                    entry.purpose,
                    _iso(entry.effective_from),
                    _iso(entry.expires_at),
                    entry.note,
                ),
            )
            self._conn.commit()
        return entry.id

    def put_cap(self, cap: FrequencyCap) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO frequency_caps "
                "(id, tenant, channel, max_messages, window_hours, purpose) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cap.id,
                    cap.tenant,
                    cap.channel.value,
                    int(cap.max_messages),
                    int(cap.window_hours),
                    cap.purpose,
                ),
            )
            self._conn.commit()
        return cap.id

    def record_send(self, send: SendEvent) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO send_events "
                "(id, tenant, subject_id, channel, purpose, decision_id, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    send.id,
                    send.tenant,
                    send.subject_id,
                    send.channel.value,
                    send.purpose,
                    send.decision_id,
                    _iso(send.sent_at),
                ),
            )
            self._conn.commit()
        return send.id

    # ------------------------------------------------------------------ #
    # Mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_record(row: sqlite3.Row) -> ConsentRecord:
        captured = _parse(row["captured_at"])
        return ConsentRecord(
            id=row["id"],
            tenant=row["tenant"],
            subject_id=row["subject_id"],
            purpose=row["purpose"],
            status=ConsentStatus(row["status"]),
            basis=ConsentBasis(row["basis"] or ConsentBasis.EXPLICIT_OPT_IN.value),
            effective_from=_parse(row["effective_from"]),
            expires_at=_parse(row["expires_at"]),
            captured_at=captured if captured is not None else datetime(1970, 1, 1, tzinfo=UTC),
            source=row["source"] or "",
            evidence_ref=row["evidence_ref"] or "",
            note=row["note"] or "",
        )

    @staticmethod
    def _to_preference(row: sqlite3.Row) -> ChannelPreference:
        updated = _parse(row["updated_at"])
        return ChannelPreference(
            id=row["id"],
            tenant=row["tenant"],
            subject_id=row["subject_id"],
            channel=ConsentChannel(row["channel"]),
            opted_in=bool(row["opted_in"]),
            updated_at=updated if updated is not None else datetime(1970, 1, 1, tzinfo=UTC),
            source=row["source"] or "",
        )

    @staticmethod
    def _to_suppression(row: sqlite3.Row) -> SuppressionEntry:
        channel = (row["channel"] or "").strip()
        return SuppressionEntry(
            id=row["id"],
            tenant=row["tenant"],
            subject_id=row["subject_id"],
            scope=SuppressionScope(row["scope"]),
            reason=SuppressionReason(row["reason"]),
            channel=ConsentChannel(channel) if channel else None,
            purpose=row["purpose"] or "",
            effective_from=_parse(row["effective_from"]),
            expires_at=_parse(row["expires_at"]),
            note=row["note"] or "",
        )

    @staticmethod
    def _to_cap(row: sqlite3.Row) -> FrequencyCap:
        return FrequencyCap(
            id=row["id"],
            tenant=row["tenant"],
            channel=ConsentChannel(row["channel"]),
            max_messages=int(row["max_messages"]),
            window_hours=int(row["window_hours"]),
            purpose=row["purpose"] or "",
        )
