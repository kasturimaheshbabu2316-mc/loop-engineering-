"""State inspection for EdgeDash.

Reads current system state from storage for planning decisions.
Deterministic - no LLM calls, no I/O except through storage module.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from edgedash import storage


@dataclass
class SystemState:
    """Current state of the EdgeDash system, suitable for planning decisions.

    All timestamps are ISO strings in UTC.
    """

    last_fetch_at: str | None  # ISO timestamp
    hours_since_fetch: float | None  # None if never fetched
    unscored_count: int

    gaps_computed_at: str | None  # ISO timestamp of latest snapshot
    gaps_stale: bool  # True if any score is newer than gap snapshot
    latest_score_at: str | None  # ISO timestamp of most recent score

    last_cycle_verdict: str | None  # "pass" or "fail" from verifier
    last_cycle_at: str | None  # ISO timestamp


def read_state(config: Any, now: datetime) -> SystemState:
    """Read current system state from storage for planning decisions.

    Args:
        config: User configuration (provides db_path).
        now: Current timestamp (parameterized for testability).

    Returns:
        SystemState with all fields populated from storage queries.
    """
    db_path = config.db_path

    # last_fetch_at - max(fetched_at) from listings
    last_fetch_at = storage.last_fetch_time(db_path)

    if last_fetch_at:
        fetched_dt = datetime.fromisoformat(last_fetch_at.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        hours_since_fetch = (now - fetched_dt).total_seconds() / 3600
    else:
        hours_since_fetch = None

    # unscored_count
    unscored_count = storage.count_unscored(db_path)

    # gaps_computed_at - max(computed_at) from skill_gaps
    gaps_computed_at = _get_latest_gaps_timestamp(db_path)

    # latest_score_at - max(fit_score_updated_at) from listings
    latest_score_at = _get_latest_score_timestamp(db_path)

    # gaps_stale: True if any score is newer than gap snapshot
    if gaps_computed_at and latest_score_at:
        gaps_dt = datetime.fromisoformat(gaps_computed_at.replace("Z", "+00:00"))
        score_dt = datetime.fromisoformat(latest_score_at.replace("Z", "+00:00"))
        gaps_stale = score_dt > gaps_dt
    else:
        gaps_stale = gaps_computed_at is None  # stale if never computed

    # last_cycle_verdict, last_cycle_at from cycle_log
    last_cycle_verdict, last_cycle_at = _get_last_cycle_result(db_path)

    return SystemState(
        last_fetch_at=last_fetch_at,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        latest_score_at=latest_score_at,
        last_cycle_verdict=last_cycle_verdict,
        last_cycle_at=last_cycle_at,
    )


def _get_latest_gaps_timestamp(db_path: str) -> str | None:
    """Get timestamp of latest skill_gaps snapshot."""
    import sqlite3

    conn = storage._connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT MAX(computed_at) FROM skill_gaps")
    result = cur.fetchone()[0]
    conn.close()
    return result


def _get_latest_score_timestamp(db_path: str) -> str | None:
    """Get timestamp of most recent score update (using fetched_at as proxy)."""
    conn = storage._connect(db_path)
    cur = conn.cursor()
    # Use fetched_at as proxy - when listing was fetched is when score will be computed
    cur.execute("SELECT MAX(fetched_at) FROM listings WHERE fit_score IS NOT NULL")
    result = cur.fetchone()[0]
    conn.close()
    return result


def _get_last_cycle_result(db_path: str) -> tuple[str | None, str | None]:
    """Get verdict and timestamp of most recent cycle.

    Returns:
        Tuple of (verdict, timestamp). Verdict is 'pass' or 'fail' from verifier.
    """
    conn = storage._connect(db_path)
    cur = conn.cursor()
    # Look for verifier results in cycle_log
    cur.execute("""
        SELECT notes, finished_at
        FROM cycle_log
        WHERE agent = 'Verifier'
        ORDER BY id DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()

    if row:
        notes = row[0] or ""
        if "pass" in notes.lower():
            verdict = "pass"
        elif "fail" in notes.lower():
            verdict = "fail"
        else:
            verdict = None
        return verdict, row[1]
    return None, None