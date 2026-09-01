"""Storage module for EdgeDash.

The ONLY module allowed to import sqlite3. All other modules must go through
this interface. Swapping SQLite for Postgres in week 4 will be a one-file change.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


def _make_listing_id(source: str, url: str) -> str:
    """Generate a stable ID for a listing based on source + url.

    This ensures the same job from the same source is never counted twice.
    """
    raw = f"{source}|{url}"
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _connect(path: str | Path) -> sqlite3.Connection:
    """Create a database connection with proper settings for concurrency."""
    conn = sqlite3.connect(path, timeout=30.0)
    # Enable WAL mode for better concurrent read/write performance
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(path: str | Path) -> None:
    """Initialize the database with required tables.

    Creates tables if they don't exist. Safe to call multiple times.

    Args:
        path: Path to the SQLite database file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(path)
    cur = conn.cursor()

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

    # Create skill_gaps table with composite primary key (skill, run_id)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS skill_gaps (
            skill TEXT,
            frequency INTEGER NOT NULL DEFAULT 1,
            last_seen TEXT NOT NULL,
            run_id INTEGER,
            computed_at TEXT,
            listings_blocked INTEGER,
            opportunity_cost REAL,
            mean_score REAL,
            top_score INTEGER,
            example_ids TEXT,
            PRIMARY KEY (skill, run_id)
        )
    """)

    # Check if we need to migrate existing skill_gaps table from old schema (PRIMARY KEY (skill))
    cur.execute("PRAGMA table_info(skill_gaps)")
    columns = cur.fetchall()
    if columns:
        pk_cols = [col[1] for col in columns if col[5] > 0]
        if pk_cols == ["skill"]:
            cur.execute("ALTER TABLE skill_gaps RENAME TO skill_gaps_old")
            cur.execute("""
                CREATE TABLE skill_gaps (
                    skill TEXT,
                    frequency INTEGER NOT NULL DEFAULT 1,
                    last_seen TEXT NOT NULL,
                    run_id INTEGER,
                    computed_at TEXT,
                    listings_blocked INTEGER,
                    opportunity_cost REAL,
                    mean_score REAL,
                    top_score INTEGER,
                    example_ids TEXT,
                    PRIMARY KEY (skill, run_id)
                )
            """)
            # Copy whatever columns exist in the old table
            old_col_names = [col[1] for col in columns]
            cols_str = ", ".join(old_col_names)
            cur.execute(f"INSERT INTO skill_gaps ({cols_str}) SELECT {cols_str} FROM skill_gaps_old")
            cur.execute("DROP TABLE skill_gaps_old")

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

    # Per steering rule 18: extraction cache table (safe to add on existing db)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS extraction_cache (
            description_hash TEXT PRIMARY KEY,
            extraction TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Query pipeline log table (Rule 42)
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
    """Insert new listings, ignoring duplicates.

    Uses INSERT OR IGNORE on the primary key (id) for deduplication.
    The listing ID is a stable hash of source + url.

    Args:
        db_path: Path to the SQLite database file.
        rows: List of listing dicts with keys:
            source, url, title, company, location, description, posted_at, fetched_at

    Returns:
        Count of genuinely NEW rows inserted (not updated/ignored).
    """
    if not rows:
        return 0

    conn = _connect(db_path)
    cur = conn.cursor()

    # Count before insert
    cur.execute("SELECT COUNT(*) FROM listings")
    count_before = cur.fetchone()[0]

    for row in rows:
        listing_id = _make_listing_id(row["source"], row["url"])
        cur.execute(
            """
            INSERT OR IGNORE INTO listings
                (id, title, company, location, url, description, source, posted_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    # Count after insert
    cur.execute("SELECT COUNT(*) FROM listings")
    count_after = cur.fetchone()[0]

    conn.close()

    return count_after - count_before


def count_unscored(db_path: str | Path) -> int:
    """Return the count of listings without a fit_score.

    Args:
        db_path: Path to the SQLite database file.
    """
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL")
    count = cur.fetchone()[0]

    conn.close()
    return count


def last_fetch_time(db_path: str | Path) -> str | None:
    """Return the ISO timestamp of the most recent fetch, or None if no listings.

    Args:
        db_path: Path to the SQLite database file.
    """
    conn = _connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT MAX(fetched_at) FROM listings")
    result = cur.fetchone()[0]

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
    """Write a row to the cycle_log table.

    Every agent run MUST log a cycle row for observability.

    Args:
        db_path: Path to the SQLite database file.
        agent: Name of the agent (e.g. "Fetcher", "Scorer").
        started_at: ISO timestamp when the cycle started.
        finished_at: ISO timestamp when the cycle finished (None if still running).
        records_touched: Number of records processed.
        status: "running", "success", or "failed".
        notes: Any additional context (e.g. retry reason, error message).

    Returns:
        The rowid of the inserted log entry.
    """
    import time

    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = _connect(db_path)
            cur = conn.cursor()

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
            return rowid

        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                continue
            raise


def get_listings(
    db_path: str | Path,
    limit: int = 100,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """Retrieve listings from the database.

    Args:
        db_path: Path to the SQLite database file.
        limit: Maximum number of listings to return.
        min_score: Minimum fit_score filter (None for no filter).

    Returns:
        List of listing dicts with all columns.
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
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

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    return rows


def update_listing_score(
    db_path: str | Path,
    listing_id: str,
    fit_score: int,
    fit_reason: str | None = None,
) -> None:
    """Update the fit_score for a specific listing.

    Args:
        db_path: Path to the SQLite database file.
        listing_id: The listing's primary key.
        fit_score: Computed fit score (0-100).
        fit_reason: Human-readable explanation for the score.
    """
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
    """Retrieve listings that have not yet been scored.

    Args:
        db_path: Path to the SQLite database file.
        limit: Maximum number of listings to return.

    Returns:
        List of listing dicts with all columns where fit_score IS NULL.
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
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

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    return rows


def upsert_skill_gaps(
    db_path: str | Path,
    gaps: list[dict[str, Any]],
) -> int:
    """Upsert skill gaps into the database.

    Args:
        db_path: Path to the SQLite database file.
        gaps: List of gap dicts with keys: skill, frequency, last_seen.

    Returns:
        Number of gaps upserted.
    """
    if not gaps:
        return 0

    conn = _connect(db_path)
    cur = conn.cursor()

    now = datetime.utcnow().isoformat()

    for gap in gaps:
        skill = gap.get("skill")
        frequency = gap.get("frequency", 1)

        cur.execute(
            """
            INSERT INTO skill_gaps (skill, frequency, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(skill) DO UPDATE SET
                frequency = frequency + ?,
                last_seen = ?
            """,
            (skill, frequency, now, frequency, now),
        )

    conn.commit()
    conn.close()

    return len(gaps)


def get_skill_gaps(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve skill gaps ordered by frequency.

    Args:
        db_path: Path to the SQLite database file.
        limit: Maximum number of gaps to return.

    Returns:
        List of gap dicts with keys: skill, frequency, last_seen.
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT skill, frequency, last_seen FROM skill_gaps
        ORDER BY frequency DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    return rows


# ---------------------------------------------------------------------------
# Extraction cache (per steering rule 18)
# ---------------------------------------------------------------------------

def get_extraction_cache(
    db_path: str | Path, description_hash: str
) -> dict[str, Any] | None:
    """Retrieve cached extraction result by description hash.

    Args:
        db_path: Path to the SQLite database file.
        description_hash: Hash of job description (from _hash_description).

    Returns:
        Cached extraction dict, or None if not found.
    """
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
        return json.loads(row[0])
    return None


def upsert_extraction_cache(
    db_path: str | Path,
    description_hash: str,
    extraction: dict[str, Any],
) -> None:
    """Store extraction result in cache.

    Args:
        db_path: Path to the SQLite database file.
        description_hash: Hash of job description.
        extraction: Extraction result dict to cache.
    """
    conn = _connect(db_path)
    cur = conn.cursor()

    now = datetime.utcnow().isoformat()
    extraction_json = json.dumps(extraction)

    cur.execute(
        """
        INSERT OR REPLACE INTO extraction_cache
            (description_hash, extraction, created_at)
        VALUES (?, ?, ?)
        """,
        (description_hash, extraction_json, now),
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Dashboard queries (Rule 38)
# ---------------------------------------------------------------------------

def get_last_passing_verifier_time(db_path: str | Path) -> str | None:
    """Retrieve the finished_at timestamp of the last passing Verifier cycle."""
    conn = _connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT finished_at FROM cycle_log
            WHERE agent = 'Verifier' AND notes LIKE '%pass%'
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_newest_verifier_cycle(db_path: str | Path) -> dict[str, Any] | None:
    """Retrieve the newest Verifier cycle row from cycle_log."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT * FROM cycle_log
            WHERE agent = 'Verifier'
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_total_counts(db_path: str | Path) -> tuple[int, int]:
    """Return (total_listings, total_scored_listings)."""
    conn = _connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM listings")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL")
        scored = cur.fetchone()[0]
        return total, scored
    except sqlite3.OperationalError:
        return 0, 0
    finally:
        conn.close()


def get_verified_listings(db_path: str | Path, verifier_time: str | None, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve top scored listings fetched at or before the last passing verifier time."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
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
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_verified_skill_gaps(db_path: str | Path, verifier_time: str | None, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve top skill gaps computed at or before the last passing verifier time."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        if verifier_time:
            # Find the run_id that was active at or before verifier_time
            cur.execute(
                """
                SELECT MAX(run_id) FROM skill_gaps
                WHERE computed_at <= ?
                """,
                (verifier_time,),
            )
            run_id = cur.fetchone()[0]
        else:
            cur.execute("SELECT MAX(run_id) FROM skill_gaps")
            run_id = cur.fetchone()[0]

        if run_id is None:
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
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_activity_log(db_path: str | Path, limit: int = 30) -> list[dict[str, Any]]:
    """Retrieve activity log for the last 30 cycles."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        # Fetch the most recent Orchestrator cycles
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
            # Fetch any Verifier run associated with this orchestrator run
            # We look for a Verifier entry started between orch.started_at and 20 seconds after finished_at
            cur.execute(
                """
                SELECT * FROM cycle_log
                WHERE agent = 'Verifier' AND started_at >= ? AND started_at <= datetime(?, '+20 seconds')
                ORDER BY id DESC LIMIT 1
                """,
                (orch["started_at"], orch["finished_at"]),
            )
            ver_row = cur.fetchone()
            ver = dict(ver_row) if ver_row else None
            
            cycles.append({
                "orchestrator": orch,
                "verifier": ver,
            })
            
        return cycles
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query Tools Storage Layer (Rules 2, 41, 46)
# ---------------------------------------------------------------------------

def get_companies_hiring(
    db_path: str | Path,
    days: int = 7,
    verifier_time: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve companies with listings posted in the last N days and their counts.

    Args:
        db_path: Path to the SQLite database file.
        days: Number of days to look back.
        verifier_time: Timestamp of last passing verifier cycle (Rule 46).

    Returns:
        tuple of (list of company dicts with keys 'company' and 'count', total listings count).
    """
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
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
            total_listings = cur.fetchone()[0]
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
            total_listings = cur.fetchone()[0]

        return companies, total_listings
    except sqlite3.OperationalError:
        return [], 0
    finally:
        conn.close()


def get_best_matches(
    db_path: str | Path,
    n: int = 10,
    verifier_time: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve top N highest-scoring listings with score, title, company, reason.

    Args:
        db_path: Path to the SQLite database file.
        n: Number of listings to return.
        verifier_time: Timestamp of last passing verifier cycle (Rule 46).

    Returns:
        tuple of (list of matched listing dicts, total scored listings count).
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
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
            total_scored = cur.fetchone()[0]
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
            total_scored = cur.fetchone()[0]

        return matches, total_scored
    except sqlite3.OperationalError:
        return [], 0
    finally:
        conn.close()


def get_known_skills(db_path: str | Path) -> set[str]:
    """Retrieve all unique skills present in the database (skill_gaps and extraction_cache).

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Set of lowercase raw and canonical skill strings found in the DB.
    """
    conn = _connect(db_path)
    cur = conn.cursor()
    known = set()
    try:
        # From skill_gaps table
        cur.execute("SELECT DISTINCT skill FROM skill_gaps WHERE skill IS NOT NULL")
        for row in cur.fetchall():
            if row[0]:
                known.add(row[0].strip().lower())

        # From extraction_cache
        cur.execute("SELECT extraction FROM extraction_cache")
        for row in cur.fetchall():
            try:
                data = json.loads(row[0])
                for s in data.get("required_skills", []):
                    if s:
                        known.add(str(s).strip().lower())
                for s in data.get("nice_to_have", []):
                    if s:
                        known.add(str(s).strip().lower())
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return known
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def get_gap_detail(
    db_path: str | Path,
    skill: str,
    verifier_time: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Retrieve gap detail for a named skill and its blocked example listings.

    Args:
        db_path: Path to the SQLite database file.
        skill: Canonical skill name.
        verifier_time: Timestamp of last passing verifier cycle (Rule 46).

    Returns:
        tuple of (gap dict or None, list of blocked example listing dicts).
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        if verifier_time:
            cur.execute(
                """
                SELECT MAX(run_id) FROM skill_gaps
                WHERE computed_at <= ?
                """,
                (verifier_time,),
            )
            run_id = cur.fetchone()[0]
        else:
            cur.execute("SELECT MAX(run_id) FROM skill_gaps")
            run_id = cur.fetchone()[0]

        if run_id is None:
            cur.execute(
                """
                SELECT skill, listings_blocked, opportunity_cost, mean_score, top_score, example_ids
                FROM skill_gaps
                WHERE lower(skill) = lower(?)
                ORDER BY rowid DESC LIMIT 1
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

        return gap_data, listings
    except sqlite3.OperationalError:
        return None, []
    finally:
        conn.close()


def get_gap_trend(
    db_path: str | Path,
    weeks: int = 3,
    verifier_time: str | None = None,
) -> tuple[list[dict[str, Any]], int, str | None, str | None]:
    """Retrieve skill gap trend over N weeks from snapshots.

    Args:
        db_path: Path to the SQLite database file.
        weeks: Number of weeks to look back.
        verifier_time: Timestamp of last passing verifier cycle (Rule 46).

    Returns:
        tuple of (trend_items, num_snapshots, earliest_date, latest_date).
    """
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
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
            latest_cost = latest_item["opportunity_cost"] or 0.0
            earliest_item = earliest_rows.get(skill)

            if earliest_item is None or earliest_run == latest_run:
                status = "NEW" if earliest_run != latest_run else "STABLE"
                delta = 0.0 if earliest_run == latest_run else latest_cost
                pct_change = 0.0 if earliest_run == latest_run else 100.0
                earliest_cost = latest_cost if earliest_run == latest_run else 0.0
            else:
                earliest_cost = earliest_item["opportunity_cost"] or 0.0
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
                "listings_blocked": latest_item["listings_blocked"],
            })

        if earliest_run != latest_run:
            for skill, earliest_item in earliest_rows.items():
                if skill not in latest_rows:
                    earliest_cost = earliest_item["opportunity_cost"] or 0.0
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
        return trend_items, len(runs), earliest_date, latest_date
    except sqlite3.OperationalError:
        return [], 0, None, None
    finally:
        conn.close()


def get_listing_counts_summary(
    db_path: str | Path,
    verifier_time: str | None = None,
) -> dict[str, Any]:
    """Retrieve totals: total listings, scored, unscored, newest listing date.

    Args:
        db_path: Path to the SQLite database file.
        verifier_time: Timestamp of last passing verifier cycle (Rule 46).

    Returns:
        dict with total_listings, scored_listings, unscored_listings, newest_listing_date.
    """
    conn = _connect(db_path)
    cur = conn.cursor()
    try:
        if verifier_time:
            cur.execute("SELECT COUNT(*) FROM listings WHERE fetched_at <= ?", (verifier_time,))
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL AND fetched_at <= ?", (verifier_time,))
            scored = cur.fetchone()[0]
            cur.execute("SELECT MAX(COALESCE(posted_at, fetched_at)) FROM listings WHERE fetched_at <= ?", (verifier_time,))
            newest_date = cur.fetchone()[0]
        else:
            cur.execute("SELECT COUNT(*) FROM listings")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL")
            scored = cur.fetchone()[0]
            cur.execute("SELECT MAX(COALESCE(posted_at, fetched_at)) FROM listings")
            newest_date = cur.fetchone()[0]

        unscored = total - scored
        return {
            "total_listings": total,
            "scored_listings": scored,
            "unscored_listings": unscored,
            "newest_listing_date": newest_date,
        }
    except sqlite3.OperationalError:
        return {
            "total_listings": 0,
            "scored_listings": 0,
            "unscored_listings": 0,
            "newest_listing_date": None,
        }
    finally:
        conn.close()


def get_skill_demand(
    db_path: str | Path,
    skill: str,
    aliases: dict[str, str] | None = None,
    verifier_time: str | None = None,
) -> dict[str, Any]:
    """Compute demand for a skill (required vs nice_to_have frequency).

    Args:
        db_path: Path to the SQLite database file.
        skill: Skill name to check.
        aliases: Skill aliases mapping.
        verifier_time: Timestamp of last passing verifier cycle (Rule 46).

    Returns:
        dict with skill, required_count, nice_to_have_count, total_mentions, pct_of_listings.
    """
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

    conn = _connect(db_path)
    cur = conn.cursor()
    try:
        if verifier_time:
            cur.execute(
                """
                SELECT description FROM listings
                WHERE fetched_at <= ? AND description IS NOT NULL
                """,
                (verifier_time,),
            )
            import hashlib
            hashes = set()
            for row in cur.fetchall():
                if row[0]:
                    h = hashlib.sha256(row[0].encode("utf-8")).hexdigest()[:16]
                    hashes.add(h)

            cur.execute("SELECT description_hash, extraction FROM extraction_cache")
            extractions = []
            for row in cur.fetchall():
                if row[0] in hashes:
                    try:
                        extractions.append(json.loads(row[1]))
                    except Exception:
                        pass

            if not extractions and not hashes:
                cur.execute("SELECT extraction FROM extraction_cache")
                for row in cur.fetchall():
                    try:
                        extractions.append(json.loads(row[0]))
                    except Exception:
                        pass
        else:
            cur.execute("SELECT extraction FROM extraction_cache")
            extractions = []
            for row in cur.fetchall():
                try:
                    extractions.append(json.loads(row[0]))
                except Exception:
                    pass

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
    except sqlite3.OperationalError:
        return {
            "skill": target_canonical,
            "required_count": 0,
            "nice_to_have_count": 0,
            "total_mentions": 0,
            "total_listings": 0,
            "pct_of_listings": 0.0,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query Pipeline Logging (Rules 42, 45)
# ---------------------------------------------------------------------------

def log_query(
    db_path: str | Path,
    question: str,
    tool_chosen: str | None,
    params: dict[str, Any] | str,
    answerable: bool,
    duration_ms: float,
    answer_text: str = "",
) -> int:
    """Log a user question and pipeline execution to query_log table.

    Args:
        db_path: Path to the SQLite database file.
        question: The natural language question asked.
        tool_chosen: Name of tool executed, or None if unanswerable.
        params: Parameters passed to the tool.
        answerable: True if answered by a tool, False if no tool matched.
        duration_ms: Total pipeline duration in milliseconds.
        answer_text: Phrased answer text.

    Returns:
        The row ID of the inserted log record.
    """
    conn = _connect(db_path)
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    params_str = params if isinstance(params, str) else json.dumps(params)
    try:
        cur.execute(
            """
            INSERT INTO query_log (question, tool_chosen, params, answerable, duration_ms, created_at, answer_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (question, tool_chosen, params_str, 1 if answerable else 0, duration_ms, now, answer_text),
        )
        row_id = cur.lastrowid
        conn.commit()
        return row_id
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def get_recent_queries(db_path: str | Path, limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent queries from query_log.

    Args:
        db_path: Path to the SQLite database file.
        limit: Number of records to return.

    Returns:
        List of dicts representing recent query records.
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, question, tool_chosen, params, answerable, duration_ms, created_at, answer_text
            FROM query_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()



