"""Local EvidenceStorePort adapter — SDK-free SQLite substantiation-evidence store.

The ``local`` profile's stand-in for the managed evidence store (Firestore on GCP): a
``sqlite3`` table of :class:`SubstantiationEvidence` rows, seeded with obviously fictional
records for two tenants so the tenant boundary is demoable and testable offline, with no
Google Cloud SDK and no network.

Tenant isolation is enforced IN THE QUERY: :meth:`list_for_asset` filters on ``tenant`` in
SQL, so a listing can never span tenants even if a caller passes the wrong asset id.
:meth:`get` is deliberately an unfiltered fetch by id, because the domain service is where
the fail-closed comparison against the verified principal's tenant happens and returns a
403 denial (see ``ports/evidence.py`` and ``domain/substantiation.py``). That split is what
makes the cross-tenant denial test meaningful: the store hands over the record, and the
domain refuses to serve it.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ...config import Settings
from ...domain.models import EvidenceKind, GreenClaimCategory, SubstantiationEvidence
from ._seed import SEED_EVIDENCE

_DEFAULT_DB_DIR = Path.home() / ".marketing_compliance_gate"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "evidence.db"

_SEPARATOR = "␟"


class LocalEvidenceStoreAdapter:
    """Serve tenant-scoped substantiation evidence from a local SQLite store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = getattr(getattr(settings, "local", None), "evidence_path", "") or str(
            _DEFAULT_DB_PATH
        )
        self._db_path = db_path
        # check_same_thread=False + an RLock: the container is process-wide while the sync
        # API endpoints run in Starlette's worker threadpool (same rationale as the rule store).
        self._lock = threading.RLock()
        self._conn = self._connect(db_path)
        self._init_schema()
        if self._is_empty():
            self.seed(SEED_EVIDENCE)

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
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    categories TEXT NOT NULL DEFAULT '',
                    issued_date TEXT NOT NULL DEFAULT '',
                    valid_until TEXT NOT NULL DEFAULT '',
                    issuer TEXT NOT NULL DEFAULT '',
                    independently_verified INTEGER NOT NULL DEFAULT 0,
                    reference TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS evidence_tenant_asset ON evidence (tenant, asset_id)"
            )
            self._conn.commit()

    def _is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM evidence").fetchone()
        return int(row["n"]) == 0

    # ------------------------------------------------------------------ #
    # Seeding
    # ------------------------------------------------------------------ #
    def seed(
        self, records: tuple[SubstantiationEvidence, ...] | list[SubstantiationEvidence]
    ) -> int:
        """Replace the store contents with ``records`` (deterministic test / demo seed)."""
        with self._lock:
            self._conn.execute("DELETE FROM evidence")
            for record in records:
                self.put(record)
            return len(list(records))

    # ------------------------------------------------------------------ #
    # EvidenceStorePort
    # ------------------------------------------------------------------ #
    def list_for_asset(self, tenant: str, asset_id: str) -> tuple[SubstantiationEvidence, ...]:
        """Evidence held by ``tenant`` for ``asset_id``; the tenant filter is in the query."""
        if not tenant:
            # Fail closed: an unresolved tenant reads nothing rather than everything.
            return ()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM evidence WHERE tenant = ? AND asset_id = ? ORDER BY id",
                (tenant, asset_id),
            ).fetchall()
        return tuple(self._row_to_evidence(row) for row in rows)

    def get(self, evidence_id: str) -> SubstantiationEvidence | None:
        """Raw fetch by id: the DOMAIN authorizes the tenant, never this adapter."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        return self._row_to_evidence(row) if row is not None else None

    def put(self, evidence: SubstantiationEvidence) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO evidence "
                "(id, tenant, asset_id, kind, title, categories, issued_date, valid_until, "
                "issuer, independently_verified, reference) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.id,
                    evidence.tenant,
                    evidence.asset_id,
                    evidence.kind.value,
                    evidence.title,
                    _SEPARATOR.join(c.value for c in evidence.categories),
                    evidence.issued_date,
                    evidence.valid_until,
                    evidence.issuer,
                    1 if evidence.independently_verified else 0,
                    evidence.reference,
                ),
            )
            self._conn.commit()
        return evidence.id

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> SubstantiationEvidence:
        categories = tuple(
            GreenClaimCategory(c) for c in (row["categories"] or "").split(_SEPARATOR) if c
        )
        return SubstantiationEvidence(
            id=row["id"],
            tenant=row["tenant"],
            asset_id=row["asset_id"],
            kind=EvidenceKind(row["kind"]),
            title=row["title"] or "",
            categories=categories,
            issued_date=row["issued_date"] or "",
            valid_until=row["valid_until"] or "",
            issuer=row["issuer"] or "",
            independently_verified=bool(row["independently_verified"]),
            reference=row["reference"] or "",
        )
