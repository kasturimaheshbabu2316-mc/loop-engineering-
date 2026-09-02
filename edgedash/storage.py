"""Storage module for EdgeDash.

Supports PostgreSQL when DATABASE_URL is configured in the environment,
with automatic fallback to local SQLite for offline development.
Rule 2: All database access goes through this single storage module.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from edgedash.env import load_env

load_env()

logger = logging.getLogger(__name__)

# Try importing PostgreSQL driver
try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    _PSYCOPG2_AVAILABLE = False

import sqlite3

# Determine active backend
def get_database_url() -> str | None:
    """Read DATABASE_URL from environment."""
    url = os.environ.get("DATABASE_URL")
    if url and url.strip():
        return url.strip()
    return None

_ACTIVE_BACKEND = "postgres" if get_database_url() else "sqlite"
if _ACTIVE_BACKEND == "postgres":
    print("[storage] Active backend: PostgreSQL (DATABASE_URL configured)", file=sys.stderr)
else:
    print("[storage] Active backend: SQLite (offline development fallback)", file=sys.stderr)


def _make_listing_id(source: str, url: str) -> str:
    """Generate a stable ID for a listing based on source + url.

    This ensures the same job from the same source is never counted twice.
    """
    raw = f"{source}|{url}"
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


class _DBConnection:
    """Unified wrapper around SQLite and PostgreSQL connections."""

    def __init__(self, raw_conn: Any, is_postgres: bool):
        self._conn = raw_conn
        self.is_postgres = is_postgres
        self.row_factory = None

    def cursor(self) -> _DBCursor:
        if self.is_postgres:
            return _DBCursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor), True)
        else:
            cur = self._conn.cursor()
            return _DBCursor(cur, False)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def execute(self, sql: str, params: tuple | list = ()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur


class _DBCursor:
    """Unified wrapper around SQLite and PostgreSQL cursors."""

    def __init__(self, raw_cur: Any, is_postgres: bool):
        self._cur = raw_cur
        self.is_postgres = is_postgres
        self.lastrowid = getattr(raw_cur, "lastrowid", None)

    def execute(self, sql: str, params: tuple | list = ()) -> _DBCursor:
        if self.is_postgres:
            # Convert ? placeholders to %s for PostgreSQL
            pg_sql = sql.replace("?", "%s")
            self._cur.execute(pg_sql, params)
            try:
                self.lastrowid = getattr(self._cur, "lastrowid", None)
            except Exception:
                pass
        else:
            self._cur.execute(sql, params)
            self.lastrowid = getattr(self._cur, "lastrowid", None)
        return self

    def fetchone(self) -> Any:
        row = self._cur.fetchone()
        if row is None:
            return None
        if self.is_postgres:
            # If row is a dict or RealDictRow, support indexing and dict conversion
            return dict(row)
        return row

    def fetchall(self) -> list[Any]:
        rows = self._cur.fetchall()
        if self.is_postgres:
            return [dict(r) for r in rows]
        return rows

    def close(self) -> None:
        self._cur.close()


def _connect(path: str | Path) -> _DBConnection:
    """Create a database connection based on active backend."""
    db_url = get_database_url()
    if db_url:
        if not _PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "DATABASE_URL is set but 'psycopg2' is not installed. "
                "Install psycopg2-binary to connect to PostgreSQL."
            )
        # Handle postgres:// to postgresql:// dialect prefix if needed
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        raw_conn = psycopg2.connect(db_url)
        return _DBConnection(raw_conn, is_postgres=True)
    else:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_conn = sqlite3.connect(path, timeout=30.0)
        raw_conn.row_factory = sqlite3.Row
        raw_conn.execute("PRAGMA journal_mode=WAL")
        raw_conn.execute("PRAGMA busy_timeout=30000")
        return _DBConnection(raw_conn, is_postgres=False)


def init_db(path: str | Path) -> None:
    """Initialize the database with required tables.

    Creates tables if they don't exist. Safe to call multiple times.

    Args:
        path: Path to SQLite DB (or ignored if DATABASE_URL is set).
    """
    conn = _connect(path)
    cur = conn.cursor()

    if conn.is_postgres:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id VARCHAR(64) PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                url TEXT NOT NULL,
                description TEXT,
                source VARCHAR(64) NOT NULL,
                posted_at TEXT,
                fetched_at TEXT NOT NULL,
                fit_score INTEGER,
                fit_reason TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS skill_gaps (
                skill TEXT,
                frequency INTEGER NOT NULL DEFAULT 1,
                last_seen TEXT NOT NULL,
                run_id INTEGER NOT NULL DEFAULT 0,
                computed_at TEXT,
                listings_blocked INTEGER,
                opportunity_cost REAL,
                mean_score REAL,
                top_score INTEGER,
                example_ids TEXT,
                PRIMARY KEY (skill, run_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cycle_log (
                id SERIAL PRIMARY KEY,
                agent TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                records_touched INTEGER,
                status TEXT,
                notes TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS extraction_cache (
                description_hash VARCHAR(64) PRIMARY KEY,
                extraction TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                tool_chosen TEXT,
                params TEXT,
                answerable INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                created_at TEXT NOT NULL,
                answer_text TEXT
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                url TEXT NOT NULL,
                description TEXT,
                source TEXT NOT NULL,
                posted_at TEXT,
                fetched_at TEXT NOT NULL,
                fit_score INTEGER,
                fit_reason TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS skill_gaps (
                skill TEXT,
                frequency INTEGER NOT NULL DEFAULT 1,
                last_seen TEXT NOT NULL,
                run_id INTEGER NOT NULL DEFAULT 0,
                computed_at TEXT,
                listings_blocked INTEGER,
                opportunity_cost REAL,
                mean_score REAL,
                top_score INTEGER,
                example_ids TEXT,
                PRIMARY KEY (skill, run_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cycle_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                records_touched INTEGER,
                status TEXT,
                notes TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS extraction_cache (
                description_hash TEXT PRIMARY KEY,
                extraction TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                tool_chosen TEXT,
                params TEXT,
                answerable INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                created_at TEXT NOT NULL,
                answer_text TEXT
            )
        """)

    conn.commit()
    conn.close()


def upsert_listings(db_path: str | Path, rows: list[dict[str, Any]]) -> int:
    """Insert new listings, ignoring duplicates."""
    if not rows:
        return 0

    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM listings")
    first_row = cur.fetchone()
    count_before = first_row["count"] if isinstance(first_row, dict) else first_row[0]

    for row in rows:
        listing_id = _make_listing_id(row["source"], row["url"])
        cur.execute(
            """
            INSERT INTO listings
                (id, title, company, location, url, description, source, posted_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                listing_id,
                row.get("title"),
                row.get("company"),
                row.get("location"),
                row.get("url"),
                row.get("description"),
                row.get("source"),
                row.get("posted_at"),
                row.get("fetched_at"),
            ),
        )

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM listings")
    second_row = cur.fetchone()
    count_after = second_row["count"] if isinstance(second_row, dict) else second_row[0]

    conn.close()
    return count_after - count_before


def count_unscored(db_path: str | Path) -> int:
    """Return the count of listings without a fit_score."""
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL")
    row = cur.fetchone()
    count = row["count"] if isinstance(row, dict) else row[0]

    conn.close()
    return count


def last_fetch_time(db_path: str | Path) -> str | None:
    """Return the ISO timestamp of the most recent fetch, or None if no listings."""
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT MAX(fetched_at) FROM listings")
    row = cur.fetchone()
    result = row["max"] if isinstance(row, dict) and "max" in row else (row[0] if row else None)

    conn.close()
    return result


def log_cycle(
    db_path: str | Path,
    agent: str,
    started_at: str,
    finished_at: str | None = None,
    records_touched: int = 0,
    status: str = "running",
    notes: str | None = None,
) -> int:
    """Write a row to the cycle_log table."""
    conn = _connect(db_path)
    cur = conn.cursor()

    if conn.is_postgres:
        cur.execute(
            """
            INSERT INTO cycle_log (agent, started_at, finished_at, records_touched, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (agent, started_at, finished_at, records_touched, status, notes),
        )
        row = cur.fetchone()
        rowid = row["id"] if isinstance(row, dict) else row[0]
    else:
        cur.execute(
            """
            INSERT INTO cycle_log (agent, started_at, finished_at, records_touched, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (agent, started_at, finished_at, records_touched, status, notes),
        )
        rowid = cur.lastrowid

    conn.commit()
    conn.close()
    return rowid or 0


def get_listings(
    db_path: str | Path,
    limit: int = 100,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """Retrieve listings from the database."""
    conn = _connect(db_path)
    cur = conn.cursor()

    if min_score is not None:
        cur.execute(
            """
            SELECT * FROM listings
            WHERE fit_score IS NOT NULL AND fit_score >= ?
            ORDER BY fit_score DESC, fetched_at DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
    else:
        cur.execute(
            """
            SELECT * FROM listings
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_listing_score(
    db_path: str | Path,
    listing_id: str,
    fit_score: int,
    fit_reason: str | None = None,
) -> None:
    """Update the fit_score for a specific listing."""
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE listings
        SET fit_score = ?, fit_reason = ?
        WHERE id = ?
        """,
        (fit_score, fit_reason, listing_id),
    )

    conn.commit()
    conn.close()


def get_unscored_listings(db_path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    """Retrieve listings that have not yet been scored."""
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT * FROM listings
        WHERE fit_score IS NULL
        ORDER BY fetched_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def upsert_skill_gaps(
    db_path: str | Path,
    gaps: list[dict[str, Any]],
) -> int:
    """Upsert skill gaps into the database."""
    if not gaps:
        return 0

    conn = _connect(db_path)
    cur = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()

    for gap in gaps:
        skill = gap.get("skill")
        frequency = gap.get("frequency", 1)
        run_id = gap.get("run_id", 0) or 0
        computed_at = gap.get("computed_at", now)
        blocked = gap.get("listings_blocked", 0)
        opp_cost = gap.get("opportunity_cost", 0.0)
        mean_score = gap.get("mean_score", 0.0)
        top_score = gap.get("top_score", 0)
        example_ids = gap.get("example_ids", "[]")

        cur.execute(
            """
            INSERT INTO skill_gaps (skill, frequency, last_seen, run_id, computed_at, listings_blocked, opportunity_cost, mean_score, top_score, example_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (skill, run_id) DO UPDATE SET
                frequency = skill_gaps.frequency + EXCLUDED.frequency,
                last_seen = EXCLUDED.last_seen,
                computed_at = EXCLUDED.computed_at,
                listings_blocked = EXCLUDED.listings_blocked,
                opportunity_cost = EXCLUDED.opportunity_cost,
                mean_score = EXCLUDED.mean_score,
                top_score = EXCLUDED.top_score,
                example_ids = EXCLUDED.example_ids
            """,
            (skill, frequency, now, run_id, computed_at, blocked, opp_cost, mean_score, top_score, example_ids),
        )

    conn.commit()
    conn.close()
    return len(gaps)


def get_skill_gaps(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve skill gaps ordered by frequency."""
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT skill, frequency, last_seen FROM skill_gaps
        ORDER BY frequency DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_extraction_cache(
    db_path: str | Path, description_hash: str
) -> dict[str, Any] | None:
    """Retrieve cached extraction result by description hash."""
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT extraction FROM extraction_cache
        WHERE description_hash = ?
        """,
        (description_hash,),
    )

    row = cur.fetchone()
    conn.close()

    if row:
        val = row["extraction"] if isinstance(row, dict) else row[0]
        return json.loads(val)
    return None


def upsert_extraction_cache(
    db_path: str | Path,
    description_hash: str,
    extraction: dict[str, Any],
) -> None:
    """Store extraction result in cache."""
    conn = _connect(db_path)
    cur = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()
    extraction_json = json.dumps(extraction)

    cur.execute(
        """
        INSERT INTO extraction_cache (description_hash, extraction, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT (description_hash) DO UPDATE SET
            extraction = EXCLUDED.extraction,
            created_at = EXCLUDED.created_at
        """,
        (description_hash, extraction_json, now),
    )

    conn.commit()
    conn.close()


def ping_db(db_path: str | Path) -> tuple[bool, str]:
    """Test connectivity to the database backend. Safe and read-only."""
    try:
        if not get_database_url() and not Path(db_path).exists():
            return False, f"Database file not found: '{db_path}'"
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        is_pg = conn.is_postgres
        conn.close()
        backend_name = "PostgreSQL" if is_pg else f"SQLite ({Path(db_path).name})"
        return True, backend_name
    except Exception as exc:
        return False, str(exc)


def get_newest_listing_time(db_path: str | Path) -> str | None:
    """Return the timestamp of the newest listing by fetched_at."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT MAX(fetched_at) FROM listings")
        row = cur.fetchone()
        conn.close()
        if row:
            return row["max"] if isinstance(row, dict) and "max" in row else (row[0] if row else None)
        return None
    except Exception:
        return None


def get_last_successful_cycle_time(db_path: str | Path) -> str | None:
    """Return the timestamp of the most recent successful cycle in cycle_log."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT finished_at FROM cycle_log
            WHERE (agent = 'Orchestrator' AND status IN ('complete', 'nothing_to_do'))
               OR (agent = 'Verifier' AND (status = 'ok' OR notes LIKE '%pass%'))
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return row["finished_at"] if isinstance(row, dict) and "finished_at" in row else (row[0] if row else None)
        return None
    except Exception:
        return None


def get_recent_verifier_results(db_path: str | Path, limit: int = 3) -> list[dict[str, Any]]:
    """Retrieve the most recent Verifier cycle runs from cycle_log."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT status, notes, finished_at FROM cycle_log
            WHERE agent = 'Verifier'
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_last_passing_verifier_time(db_path: str | Path) -> str | None:
    """Retrieve the finished_at timestamp of the last passing Verifier cycle."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT finished_at FROM cycle_log
            WHERE agent = 'Verifier' AND notes LIKE '%pass%'
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return row["finished_at"] if isinstance(row, dict) else row[0]
        return None
    except Exception:
        return None


def get_newest_verifier_cycle(db_path: str | Path) -> dict[str, Any] | None:
    """Retrieve the newest Verifier cycle row from cycle_log."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM cycle_log
            WHERE agent = 'Verifier'
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def get_total_counts(db_path: str | Path) -> tuple[int, int]:
    """Return (total_listings, total_scored_listings)."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM listings")
        r1 = cur.fetchone()
        total = r1["count"] if isinstance(r1, dict) else r1[0]

        cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL")
        r2 = cur.fetchone()
        scored = r2["count"] if isinstance(r2, dict) else r2[0]

        conn.close()
        return total, scored
    except Exception:
        return 0, 0


def get_verified_listings(db_path: str | Path, verifier_time: str | None, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve top scored listings fetched at or before the last passing verifier time."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        if verifier_time:
            cur.execute(
                """
                SELECT * FROM listings
                WHERE fit_score IS NOT NULL AND fetched_at <= ?
                ORDER BY fit_score DESC, fetched_at DESC
                LIMIT ?
                """,
                (verifier_time, limit),
            )
        else:
            cur.execute(
                """
                SELECT * FROM listings
                WHERE fit_score IS NOT NULL
                ORDER BY fit_score DESC, fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


def get_verified_skill_gaps(db_path: str | Path, verifier_time: str | None, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve top skill gaps computed at or before the last passing verifier time."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        if verifier_time:
            cur.execute(
                """
                SELECT MAX(run_id) FROM skill_gaps
                WHERE computed_at <= ?
                """,
                (verifier_time,),
            )
            r = cur.fetchone()
            run_id = r["max"] if isinstance(r, dict) and "max" in r else (r[0] if r else None)
        else:
            cur.execute("SELECT MAX(run_id) FROM skill_gaps")
            r = cur.fetchone()
            run_id = r["max"] if isinstance(r, dict) and "max" in r else (r[0] if r else None)

        if run_id is None:
            conn.close()
            return []

        cur.execute(
            """
            SELECT skill, listings_blocked, opportunity_cost, mean_score, top_score FROM skill_gaps
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC, skill ASC
            LIMIT ?
            """,
            (run_id, limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


def get_activity_log(db_path: str | Path, limit: int = 30) -> list[dict[str, Any]]:
    """Retrieve activity log for the last 30 cycles."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM cycle_log
            WHERE agent = 'Orchestrator'
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        orch_runs = [dict(row) for row in cur.fetchall()]

        cycles = []
        for orch in orch_runs:
            s_time = orch.get("started_at")
            f_time = orch.get("finished_at")
            
            # Compute window end in python for clean cross-DB compatibility
            window_end = f_time
            if f_time:
                try:
                    dt = datetime.fromisoformat(f_time.replace("Z", "+00:00"))
                    window_end = (dt + timedelta(seconds=20)).isoformat()
                except Exception:
                    pass

            cur.execute(
                """
                SELECT * FROM cycle_log
                WHERE agent = 'Verifier' AND started_at >= ? AND started_at <= ?
                ORDER BY id DESC LIMIT 1
                """,
                (s_time, window_end),
            )
            ver_row = cur.fetchone()
            ver = dict(ver_row) if ver_row else None

            cycles.append({
                "orchestrator": orch,
                "verifier": ver,
            })

        conn.close()
        return cycles
    except Exception:
        return []


def get_companies_hiring(
    db_path: str | Path,
    days: int = 7,
    verifier_time: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve companies with listings posted in the last N days and their counts."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        if verifier_time:
            cur.execute(
                """
                SELECT company, COUNT(*) as count
                FROM listings
                WHERE company IS NOT NULL AND company != ''
                  AND (posted_at >= ? OR (posted_at IS NULL AND fetched_at >= ?))
                  AND fetched_at <= ?
                GROUP BY company
                ORDER BY count DESC, company ASC
                """,
                (cutoff, cutoff, verifier_time),
            )
            companies = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM listings
                WHERE (posted_at >= ? OR (posted_at IS NULL AND fetched_at >= ?))
                  AND fetched_at <= ?
                """,
                (cutoff, cutoff, verifier_time),
            )
            r = cur.fetchone()
            total_listings = r["count"] if isinstance(r, dict) else r[0]
        else:
            cur.execute(
                """
                SELECT company, COUNT(*) as count
                FROM listings
                WHERE company IS NOT NULL AND company != ''
                  AND (posted_at >= ? OR (posted_at IS NULL AND fetched_at >= ?))
                GROUP BY company
                ORDER BY count DESC, company ASC
                """,
                (cutoff, cutoff),
            )
            companies = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM listings
                WHERE (posted_at >= ? OR (posted_at IS NULL AND fetched_at >= ?))
                """,
                (cutoff, cutoff),
            )
            r = cur.fetchone()
            total_listings = r["count"] if isinstance(r, dict) else r[0]

        conn.close()
        return companies, total_listings
    except Exception:
        return [], 0


def get_best_matches(
    db_path: str | Path,
    n: int = 10,
    verifier_time: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve top N highest-scoring listings with score, title, company, reason."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        if verifier_time:
            cur.execute(
                """
                SELECT id, title, company, fit_score as score, fit_reason as reason, url, location
                FROM listings
                WHERE fit_score IS NOT NULL AND fetched_at <= ?
                ORDER BY fit_score DESC, fetched_at DESC
                LIMIT ?
                """,
                (verifier_time, n),
            )
            matches = [dict(row) for row in cur.fetchall()]

            cur.execute(
                "SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL AND fetched_at <= ?",
                (verifier_time,),
            )
            r = cur.fetchone()
            total_scored = r["count"] if isinstance(r, dict) else r[0]
        else:
            cur.execute(
                """
                SELECT id, title, company, fit_score as score, fit_reason as reason, url, location
                FROM listings
                WHERE fit_score IS NOT NULL
                ORDER BY fit_score DESC, fetched_at DESC
                LIMIT ?
                """,
                (n,),
            )
            matches = [dict(row) for row in cur.fetchall()]

            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL")
            r = cur.fetchone()
            total_scored = r["count"] if isinstance(r, dict) else r[0]

        conn.close()
        return matches, total_scored
    except Exception:
        return [], 0


def get_known_skills(db_path: str | Path) -> set[str]:
    """Retrieve all unique skills present in the database."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        known = set()

        cur.execute("SELECT DISTINCT skill FROM skill_gaps WHERE skill IS NOT NULL")
        for row in cur.fetchall():
            val = row["skill"] if isinstance(row, dict) else row[0]
            if val:
                known.add(val.strip().lower())

        cur.execute("SELECT extraction FROM extraction_cache")
        for row in cur.fetchall():
            val = row["extraction"] if isinstance(row, dict) else row[0]
            try:
                data = json.loads(val)
                for s in data.get("required_skills", []):
                    if s:
                        known.add(str(s).strip().lower())
                for s in data.get("nice_to_have", []):
                    if s:
                        known.add(str(s).strip().lower())
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        conn.close()
        return known
    except Exception:
        return set()


def get_gap_detail(
    db_path: str | Path,
    skill: str,
    verifier_time: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Retrieve gap detail for a named skill and its blocked example listings."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()

        if verifier_time:
            cur.execute(
                """
                SELECT MAX(run_id) FROM skill_gaps
                WHERE computed_at <= ?
                """,
                (verifier_time,),
            )
            r = cur.fetchone()
            run_id = r["max"] if isinstance(r, dict) and "max" in r else (r[0] if r else None)
        else:
            cur.execute("SELECT MAX(run_id) FROM skill_gaps")
            r = cur.fetchone()
            run_id = r["max"] if isinstance(r, dict) and "max" in r else (r[0] if r else None)

        if run_id is None:
            cur.execute(
                """
                SELECT skill, listings_blocked, opportunity_cost, mean_score, top_score, example_ids
                FROM skill_gaps
                WHERE lower(skill) = lower(?)
                ORDER BY frequency DESC LIMIT 1
                """,
                (skill,),
            )
        else:
            cur.execute(
                """
                SELECT skill, listings_blocked, opportunity_cost, mean_score, top_score, example_ids
                FROM skill_gaps
                WHERE lower(skill) = lower(?) AND run_id = ?
                """,
                (skill, run_id),
            )

        gap_row = cur.fetchone()
        if not gap_row:
            conn.close()
            return None, []

        gap_data = dict(gap_row)
        example_ids = []
        if gap_data.get("example_ids"):
            try:
                example_ids = json.loads(gap_data["example_ids"])
            except (json.JSONDecodeError, TypeError):
                pass

        listings = []
        if example_ids:
            placeholders = ",".join("?" for _ in example_ids)
            cur.execute(
                f"""
                SELECT id, title, company, fit_score as score, fit_reason as reason, url, location
                FROM listings
                WHERE id IN ({placeholders})
                ORDER BY fit_score DESC
                """,
                example_ids,
            )
            listings = [dict(row) for row in cur.fetchall()]

        conn.close()
        return gap_data, listings
    except Exception:
        return None, []


def get_gap_trend(
    db_path: str | Path,
    weeks: int = 3,
    verifier_time: str | None = None,
) -> tuple[list[dict[str, Any]], int, str | None, str | None]:
    """Retrieve skill gap trend over N weeks from snapshots."""
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()

    try:
        conn = _connect(db_path)
        cur = conn.cursor()

        if verifier_time:
            cur.execute(
                """
                SELECT DISTINCT run_id, computed_at
                FROM skill_gaps
                WHERE run_id IS NOT NULL AND computed_at >= ? AND computed_at <= ?
                ORDER BY computed_at ASC
                """,
                (cutoff, verifier_time),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT run_id, computed_at
                FROM skill_gaps
                WHERE run_id IS NOT NULL AND computed_at >= ?
                ORDER BY computed_at ASC
                """,
                (cutoff,),
            )
        runs = [dict(row) for row in cur.fetchall()]

        if not runs:
            if verifier_time:
                cur.execute(
                    """
                    SELECT DISTINCT run_id, computed_at
                    FROM skill_gaps
                    WHERE run_id IS NOT NULL AND computed_at <= ?
                    ORDER BY computed_at ASC
                    """,
                    (verifier_time,),
                )
            else:
                cur.execute(
                    """
                    SELECT DISTINCT run_id, computed_at
                    FROM skill_gaps
                    WHERE run_id IS NOT NULL
                    ORDER BY computed_at ASC
                    """
                )
            runs = [dict(row) for row in cur.fetchall()]

        if not runs:
            conn.close()
            return [], 0, None, None

        earliest_run = runs[0]["run_id"]
        latest_run = runs[-1]["run_id"]
        earliest_date = runs[0]["computed_at"]
        latest_date = runs[-1]["computed_at"]

        cur.execute(
            """
            SELECT skill, opportunity_cost, listings_blocked
            FROM skill_gaps
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            """,
            (latest_run,),
        )
        latest_rows = {row["skill"]: dict(row) for row in cur.fetchall()}

        cur.execute(
            """
            SELECT skill, opportunity_cost, listings_blocked
            FROM skill_gaps
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            """,
            (earliest_run,),
        )
        earliest_rows = {row["skill"]: dict(row) for row in cur.fetchall()}

        trend_items = []
        for skill, latest_item in latest_rows.items():
            latest_cost = latest_item.get("opportunity_cost") or 0.0
            earliest_item = earliest_rows.get(skill)

            if earliest_item is None or earliest_run == latest_run:
                status = "NEW" if earliest_run != latest_run else "STABLE"
                delta = 0.0 if earliest_run == latest_run else latest_cost
                pct_change = 0.0 if earliest_run == latest_run else 100.0
                earliest_cost = latest_cost if earliest_run == latest_run else 0.0
            else:
                earliest_cost = earliest_item.get("opportunity_cost") or 0.0
                delta = latest_cost - earliest_cost
                pct_change = ((latest_cost - earliest_cost) / earliest_cost * 100.0) if earliest_cost > 0 else 0.0
                if delta > 0.01:
                    status = "INCREASING"
                elif delta < -0.01:
                    status = "DECREASING"
                else:
                    status = "STABLE"

            trend_items.append({
                "skill": skill,
                "latest_cost": round(latest_cost, 2),
                "earliest_cost": round(earliest_cost, 2),
                "delta": round(delta, 2),
                "pct_change": round(pct_change, 1),
                "status": status,
                "listings_blocked": latest_item.get("listings_blocked", 0),
            })

        if earliest_run != latest_run:
            for skill, earliest_item in earliest_rows.items():
                if skill not in latest_rows:
                    earliest_cost = earliest_item.get("opportunity_cost") or 0.0
                    trend_items.append({
                        "skill": skill,
                        "latest_cost": 0.0,
                        "earliest_cost": round(earliest_cost, 2),
                        "delta": round(-earliest_cost, 2),
                        "pct_change": -100.0,
                        "status": "DROPPED",
                        "listings_blocked": 0,
                    })

        trend_items.sort(key=lambda x: (x["latest_cost"], x["earliest_cost"]), reverse=True)
        conn.close()
        return trend_items, len(runs), earliest_date, latest_date
    except Exception:
        return [], 0, None, None


def get_listing_counts_summary(
    db_path: str | Path,
    verifier_time: str | None = None,
) -> dict[str, Any]:
    """Retrieve totals: total listings, scored, unscored, newest listing date."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()

        if verifier_time:
            cur.execute("SELECT COUNT(*) FROM listings WHERE fetched_at <= ?", (verifier_time,))
            r1 = cur.fetchone()
            total = r1["count"] if isinstance(r1, dict) else r1[0]

            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL AND fetched_at <= ?", (verifier_time,))
            r2 = cur.fetchone()
            scored = r2["count"] if isinstance(r2, dict) else r2[0]

            cur.execute("SELECT MAX(COALESCE(posted_at, fetched_at)) FROM listings WHERE fetched_at <= ?", (verifier_time,))
            r3 = cur.fetchone()
            newest_date = r3["max"] if isinstance(r3, dict) and "max" in r3 else (r3[0] if r3 else None)
        else:
            cur.execute("SELECT COUNT(*) FROM listings")
            r1 = cur.fetchone()
            total = r1["count"] if isinstance(r1, dict) else r1[0]

            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL")
            r2 = cur.fetchone()
            scored = r2["count"] if isinstance(r2, dict) else r2[0]

            cur.execute("SELECT MAX(COALESCE(posted_at, fetched_at)) FROM listings")
            r3 = cur.fetchone()
            newest_date = r3["max"] if isinstance(r3, dict) and "max" in r3 else (r3[0] if r3 else None)

        unscored = total - scored
        conn.close()
        return {
            "total_listings": total,
            "scored_listings": scored,
            "unscored_listings": unscored,
            "newest_listing_date": newest_date,
        }
    except Exception:
        return {
            "total_listings": 0,
            "scored_listings": 0,
            "unscored_listings": 0,
            "newest_listing_date": None,
        }


def get_skill_demand(
    db_path: str | Path,
    skill: str,
    aliases: dict[str, str] | None = None,
    verifier_time: str | None = None,
) -> dict[str, Any]:
    """Compute demand for a skill (required vs nice_to_have frequency)."""
    from edgedash import skills

    target_canonical = skills.canonical(skill, aliases or {})
    if not target_canonical:
        return {
            "skill": skill,
            "required_count": 0,
            "nice_to_have_count": 0,
            "total_mentions": 0,
            "total_listings": 0,
            "pct_of_listings": 0.0,
        }

    try:
        conn = _connect(db_path)
        cur = conn.cursor()

        if verifier_time:
            cur.execute(
                """
                SELECT description FROM listings
                WHERE fetched_at <= ? AND description IS NOT NULL
                """,
                (verifier_time,),
            )
            hashes = set()
            for row in cur.fetchall():
                val = row["description"] if isinstance(row, dict) else row[0]
                if val:
                    h = sha256(val.encode("utf-8")).hexdigest()[:16]
                    hashes.add(h)

            cur.execute("SELECT description_hash, extraction FROM extraction_cache")
            extractions = []
            for row in cur.fetchall():
                d_hash = row["description_hash"] if isinstance(row, dict) else row[0]
                ext_str = row["extraction"] if isinstance(row, dict) else row[1]
                if d_hash in hashes:
                    try:
                        extractions.append(json.loads(ext_str))
                    except Exception:
                        pass

            if not extractions and not hashes:
                cur.execute("SELECT extraction FROM extraction_cache")
                for row in cur.fetchall():
                    ext_str = row["extraction"] if isinstance(row, dict) else row[0]
                    try:
                        extractions.append(json.loads(ext_str))
                    except Exception:
                        pass
        else:
            cur.execute("SELECT extraction FROM extraction_cache")
            extractions = []
            for row in cur.fetchall():
                ext_str = row["extraction"] if isinstance(row, dict) else row[0]
                try:
                    extractions.append(json.loads(ext_str))
                except Exception:
                    pass

        conn.close()

        required_count = 0
        nice_to_have_count = 0
        total_listings = len(extractions)

        for ext in extractions:
            req_skills = [skills.canonical(s, aliases or {}) for s in ext.get("required_skills", []) if s]
            nth_skills = [skills.canonical(s, aliases or {}) for s in ext.get("nice_to_have", []) if s]

            if target_canonical in req_skills:
                required_count += 1
            if target_canonical in nth_skills:
                nice_to_have_count += 1

        total_mentions = required_count + nice_to_have_count
        pct = round((total_mentions / total_listings * 100), 1) if total_listings > 0 else 0.0

        return {
            "skill": target_canonical,
            "required_count": required_count,
            "nice_to_have_count": nice_to_have_count,
            "total_mentions": total_mentions,
            "total_listings": total_listings,
            "pct_of_listings": pct,
        }
    except Exception:
        return {
            "skill": target_canonical,
            "required_count": 0,
            "nice_to_have_count": 0,
            "total_mentions": 0,
            "total_listings": 0,
            "pct_of_listings": 0.0,
        }


def log_query(
    db_path: str | Path,
    question: str,
    tool_chosen: str | None,
    params: dict[str, Any] | str,
    answerable: bool,
    duration_ms: float,
    answer_text: str = "",
) -> int:
    """Log a user question and pipeline execution to query_log table."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        params_str = params if isinstance(params, str) else json.dumps(params)

        if conn.is_postgres:
            cur.execute(
                """
                INSERT INTO query_log (question, tool_chosen, params, answerable, duration_ms, created_at, answer_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (question, tool_chosen, params_str, 1 if answerable else 0, duration_ms, now, answer_text),
            )
            r = cur.fetchone()
            row_id = r["id"] if isinstance(r, dict) else r[0]
        else:
            cur.execute(
                """
                INSERT INTO query_log (question, tool_chosen, params, answerable, duration_ms, created_at, answer_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (question, tool_chosen, params_str, 1 if answerable else 0, duration_ms, now, answer_text),
            )
            row_id = cur.lastrowid

        conn.commit()
        conn.close()
        return row_id or 0
    except Exception:
        return 0


def get_recent_queries(db_path: str | Path, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent queries from query_log."""
    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, question, tool_chosen, params, answerable, duration_ms, created_at, answer_text
            FROM query_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Migration & Check CLI Utilities
# ---------------------------------------------------------------------------

def migrate_db(db_path: str | Path = "edgedash.db") -> None:
    """Create every table on an empty database. Safe to run repeatedly."""
    backend_name = "PostgreSQL" if get_database_url() else "SQLite"
    print(f"Running migration on active backend: {backend_name}...")
    init_db(db_path)
    print("[OK] Database migration completed successfully. All tables verified.")


def check_db(db_path: str | Path = "edgedash.db") -> None:
    """Print backend information, connectivity status, and table row counts."""
    db_url = get_database_url()
    backend_name = "PostgreSQL" if db_url else "SQLite"
    print(f"Active backend: {backend_name}")

    if db_url:
        # Mask sensitive password in URL
        try:
            from urllib.parse import urlparse
            parsed = urlparse(db_url)
            netloc = parsed.hostname or "localhost"
            if parsed.port:
                netloc += f":{parsed.port}"
            masked_url = f"{parsed.scheme}://{parsed.username or 'user'}:****@{netloc}{parsed.path}"
            print(f"Connection URI: {masked_url}")
        except Exception:
            print("Connection URI: [Configured via DATABASE_URL]")
    else:
        print(f"SQLite DB Path: {Path(db_path).resolve()}")

    try:
        conn = _connect(db_path)
        cur = conn.cursor()
        print("Connection status: Connected successfully [OK]")

        tables = ["listings", "skill_gaps", "cycle_log", "extraction_cache", "query_log"]
        print("\nRow counts per table:")
        for tbl in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                r = cur.fetchone()
                cnt = r["count"] if isinstance(r, dict) else r[0]
                print(f"  - {tbl:20s}: {cnt} rows")
            except Exception as e:
                print(f"  - {tbl:20s}: [Table not found or error: {e}]")
        conn.close()
    except Exception as exc:
        print(f"Connection status: FAILED to connect: {exc}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EdgeDash Database Management CLI")
    parser.add_argument("--migrate", action="store_true", help="Create tables on the active database")
    parser.add_argument("--check", action="store_true", help="Check database connectivity and row counts")
    parser.add_argument("--db-path", default="edgedash.db", help="Path to SQLite database file if offline")

    args = parser.parse_args()

    if args.migrate:
        migrate_db(args.db_path)
    elif args.check:
        check_db(args.db_path)
    else:
        parser.print_help()
