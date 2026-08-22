"""Local rule-provider adapter (RuleProviderPort) — SQLite FTS5 rule KB.

The ``local`` profile's stand-in for **Gemini API File Search** over the per-market,
per-vertical compliance rule KB (whose GCP adapter is File Search, and whose platform
adapter is the shared A2 Enterprise KB): a ``sqlite3`` database with an **FTS5** virtual
table over the seeded fictional rules, queried with BM25 for the ``search`` lookup. It is
SDK-free, deterministic and seedable, so the same rules ground the offline CLI run and the
unit tests. There is no Google emulator for File Search, so this path is unconditional.

``rule_set`` returns the full, fully-typed :class:`RuleSet` for a (market, vertical) so the
deterministic :class:`RuleEngine` can evaluate it. ``search`` returns the subset whose text
matches a query string (a KB-style lookup the LLM/agent can call as a governed tool). The
rules themselves are config + seed; adding a market/vertical is a seed change, not code.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from ...config import Settings
from ...domain.models import (
    CheckType,
    Citation,
    Market,
    Rule,
    RuleKind,
    RuleSet,
    Severity,
    SourceType,
    Vertical,
)
from ._seed import ALL_RULES

_DEFAULT_DB_DIR = Path.home() / ".marketing_compliance_gate"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "rules.db"

# FTS5 query syntax is strict; keep only word characters so a free-text query never trips
# an "fts5: syntax error", and OR the terms for recall.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class LocalRuleProviderAdapter:
    """Serve + search the seeded compliance rule sets from a local SQLite FTS5 store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = getattr(getattr(settings, "local", None), "db_path", "") or str(_DEFAULT_DB_PATH)
        self._db_path = db_path
        # check_same_thread=False + an RLock: deps.get_container is process-wide while the
        # sync API endpoints run in Starlette's worker threadpool, so search() is called
        # from worker threads other than the one that opened the connection. The RLock
        # serialises access and is re-entrant so seed -> _insert does not deadlock.
        self._lock = threading.RLock()
        self._conn = self._connect(db_path)
        self._init_schema()
        if self._is_empty():
            self.seed(ALL_RULES)

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
                CREATE VIRTUAL TABLE IF NOT EXISTS rules USING fts5(
                    text,
                    rule_id UNINDEXED,
                    description UNINDEXED,
                    kind UNINDEXED,
                    check_type UNINDEXED,
                    market UNINDEXED,
                    vertical UNINDEXED,
                    severity UNINDEXED,
                    patterns UNINDEXED,
                    field_name UNINDEXED,
                    limit_val UNINDEXED,
                    consent_purpose UNINDEXED,
                    remediation UNINDEXED,
                    cite_title UNINDEXED,
                    cite_url UNINDEXED,
                    cite_published UNINDEXED,
                    cite_snippet UNINDEXED
                )
                """
            )
            self._conn.commit()

    def _is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM rules").fetchone()
        return int(row["n"]) == 0

    # ------------------------------------------------------------------ #
    # Seeding
    # ------------------------------------------------------------------ #
    def seed(self, rules: tuple[Rule, ...] | list[Rule]) -> int:
        """Replace the index contents with ``rules`` (deterministic test/CLI seed)."""
        with self._lock:
            self._conn.execute("DELETE FROM rules")
            return self._insert(list(rules))

    def _insert(self, rules: list[Rule]) -> int:
        rows = []
        for r in rules:
            c = r.citation
            text = f"{r.description} {' '.join(r.patterns)} {r.remediation}".strip()
            rows.append(
                (
                    text,
                    r.id,
                    r.description,
                    r.kind.value,
                    r.check.value,
                    r.market.value,
                    r.vertical.value,
                    r.severity.value,
                    "␟".join(r.patterns),
                    r.field,
                    "" if r.limit is None else repr(r.limit),
                    r.consent_purpose,
                    r.remediation,
                    "" if c is None else c.title,
                    "" if c is None else c.url,
                    "" if (c is None or c.published_date is None) else c.published_date,
                    "" if c is None else c.snippet,
                )
            )
        with self._lock:
            self._conn.executemany(
                "INSERT INTO rules "
                "(text, rule_id, description, kind, check_type, market, vertical, severity, "
                "patterns, field_name, limit_val, consent_purpose, remediation, cite_title, "
                "cite_url, cite_published, cite_snippet) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    # ------------------------------------------------------------------ #
    # RuleProviderPort
    # ------------------------------------------------------------------ #
    def rule_set(self, market: Market, vertical: Vertical) -> RuleSet:
        """Return every rule in force for ``(market, vertical)``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM rules WHERE market = ? AND vertical = ? ORDER BY rule_id",
                (market.value, vertical.value),
            ).fetchall()
        return RuleSet(
            market=market,
            vertical=vertical,
            rules=tuple(self._row_to_rule(row) for row in rows),
        )

    def search(self, market: Market, vertical: Vertical, text: str) -> RuleSet:
        """Return the matching subset of a (market, vertical)'s rules (BM25 ranked)."""
        match = self._build_match(text)
        with self._lock:
            if not match:
                rows = self._conn.execute(
                    "SELECT * FROM rules WHERE market = ? AND vertical = ? ORDER BY rule_id",
                    (market.value, vertical.value),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM rules WHERE rules MATCH ? AND market = ? AND vertical = ? "
                    "ORDER BY rank",
                    (match, market.value, vertical.value),
                ).fetchall()
        return RuleSet(
            market=market,
            vertical=vertical,
            rules=tuple(self._row_to_rule(row) for row in rows),
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_match(text: str) -> str:
        tokens = _TOKEN_RE.findall(text or "")
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens)

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> Rule:
        patterns = tuple(p for p in (row["patterns"] or "").split("␟") if p)
        limit_raw = row["limit_val"]
        limit = float(limit_raw) if limit_raw not in (None, "") else None
        citation = Citation(
            source_id=row["rule_id"],
            source_type=SourceType.REGULATION,
            title=row["cite_title"] or row["rule_id"],
            url=row["cite_url"] or "",
            page=1,
            published_date=row["cite_published"] or None,
            snippet=row["cite_snippet"] or "",
            score=1.0,
        )
        return Rule(
            id=row["rule_id"],
            kind=RuleKind(row["kind"]),
            check=CheckType(row["check_type"]),
            description=row["description"] or row["rule_id"],
            market=Market(row["market"]),
            vertical=Vertical(row["vertical"]),
            severity=Severity(row["severity"]),
            patterns=patterns,
            field=row["field_name"] or "",
            limit=limit,
            consent_purpose=row["consent_purpose"] or "",
            remediation=row["remediation"] or "",
            citation=citation,
        )
