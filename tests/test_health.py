"""Unit tests for EdgeDash health reporting module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from edgedash import health, storage
from edgedash.health import (
    check_database,
    check_listing_freshness,
    check_cycle_recency,
    check_verification_stability,
    run_health_checks,
    get_dashboard_health_summary,
)


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a temporary initialized SQLite database."""
    db_file = tmp_path / "test_edgedash.db"
    storage.init_db(db_file)
    return db_file


def _insert_dummy_listing(db_path: Path, fetched_at: str) -> None:
    """Helper to insert a test listing with a custom fetched_at timestamp."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO listings (id, source, url, title, company, description, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"test_{fetched_at}",
            "mock",
            f"https://example.com/job/{fetched_at}",
            "Test Engineer",
            "Acme Corp",
            "Python Engineer needed",
            fetched_at,
        ),
    )
    conn.commit()
    conn.close()


def _insert_cycle_log(
    db_path: Path,
    agent: str,
    status: str,
    notes: str = "",
    finished_at: str | None = None,
) -> None:
    """Helper to insert a cycle_log row."""
    if finished_at is None:
        finished_at = datetime.now(timezone.utc).isoformat()
    started_at = finished_at

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO cycle_log (agent, started_at, finished_at, records_touched, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (agent, started_at, finished_at, 1, status, notes),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Test Database Reachability Check
# ---------------------------------------------------------------------------
def test_check_database_success(test_db: Path) -> None:
    result = check_database(test_db)
    assert result.passed is True
    assert "Connected" in result.observed
    assert result.name == "Database reachability"


def test_check_database_failure(tmp_path: Path) -> None:
    # A path that cannot be accessed or is an invalid directory
    bad_db = tmp_path / "non_existent_folder" / "sub" / "invalid.db"
    result = check_database(bad_db)
    assert result.passed is False
    assert "Unreachable" in result.observed


# ---------------------------------------------------------------------------
# Test Listing Freshness Check (Threshold: 3 days)
# ---------------------------------------------------------------------------
def test_check_listing_freshness_empty_db(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    result = check_listing_freshness(test_db, now=now)
    assert result.passed is False
    assert "No listings found" in result.observed


def test_check_listing_freshness_pass(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    # Listing fetched 1 day ago
    recent_ts = (now - timedelta(days=1)).isoformat()
    _insert_dummy_listing(test_db, recent_ts)

    result = check_listing_freshness(test_db, now=now)
    assert result.passed is True
    assert "1.00 days old" in result.observed


def test_check_listing_freshness_fail_stale(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    # Listing fetched 4 days ago (> 3.0 days)
    stale_ts = (now - timedelta(days=4)).isoformat()
    _insert_dummy_listing(test_db, stale_ts)

    result = check_listing_freshness(test_db, now=now)
    assert result.passed is False
    assert "4.00 days old" in result.observed


# ---------------------------------------------------------------------------
# Test Cycle Recency Check (Threshold: 48 hours)
# ---------------------------------------------------------------------------
def test_check_cycle_recency_no_cycles(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    result = check_cycle_recency(test_db, now=now)
    assert result.passed is False
    assert "No successful cycle" in result.observed


def test_check_cycle_recency_pass(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    # Cycle finished 10 hours ago
    cycle_ts = (now - timedelta(hours=10)).isoformat()
    _insert_cycle_log(test_db, agent="Orchestrator", status="complete", finished_at=cycle_ts)

    result = check_cycle_recency(test_db, now=now)
    assert result.passed is True
    assert "10.00 hours ago" in result.observed


def test_check_cycle_recency_fail_stale(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    # Cycle finished 50 hours ago (> 48h)
    cycle_ts = (now - timedelta(hours=50)).isoformat()
    _insert_cycle_log(test_db, agent="Orchestrator", status="complete", finished_at=cycle_ts)

    result = check_cycle_recency(test_db, now=now)
    assert result.passed is False
    assert "50.00 hours ago" in result.observed


# ---------------------------------------------------------------------------
# Test Verification Stability Check (Fails if last 3 all failed)
# ---------------------------------------------------------------------------
def test_check_verification_stability_fewer_than_3_runs(test_db: Path) -> None:
    _insert_cycle_log(test_db, agent="Verifier", status="ok", notes="verdict=pass")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")

    result = check_verification_stability(test_db)
    assert result.passed is True
    assert "< 3 total runs recorded" in result.observed


def test_check_verification_stability_mixed_runs(test_db: Path) -> None:
    _insert_cycle_log(test_db, agent="Verifier", status="ok", notes="verdict=pass")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")

    result = check_verification_stability(test_db)
    assert result.passed is True
    assert result.observed == "Last 3 verdicts: ['fail', 'fail', 'pass']"


def test_check_verification_stability_all_failed(test_db: Path) -> None:
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")

    result = check_verification_stability(test_db)
    assert result.passed is False
    assert result.observed == "Last 3 verdicts: ['fail', 'fail', 'fail']"


# ---------------------------------------------------------------------------
# Test Aggregated run_health_checks
# ---------------------------------------------------------------------------
def test_run_health_checks_healthy_system(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    recent_ts = (now - timedelta(hours=2)).isoformat()

    _insert_dummy_listing(test_db, recent_ts)
    _insert_cycle_log(test_db, agent="Orchestrator", status="complete", finished_at=recent_ts)
    _insert_cycle_log(test_db, agent="Verifier", status="ok", notes="verdict=pass", finished_at=recent_ts)

    report = run_health_checks(test_db, now=now)
    assert report.is_healthy is True
    assert len(report.checks) == 4
    assert all(c.passed for c in report.checks)


def test_run_health_checks_unhealthy_system(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    stale_ts = (now - timedelta(days=5)).isoformat()

    _insert_dummy_listing(test_db, stale_ts)
    _insert_cycle_log(test_db, agent="Orchestrator", status="complete", finished_at=stale_ts)

    report = run_health_checks(test_db, now=now)
    assert report.is_healthy is False


# ---------------------------------------------------------------------------
# Test Dashboard Health Summary (Requirements 3 & 4 / Rule 50)
# ---------------------------------------------------------------------------
def test_get_dashboard_health_summary_live_green(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    pass_ts = (now - timedelta(hours=4)).isoformat()
    _insert_cycle_log(test_db, agent="Verifier", status="ok", notes="verdict=pass", finished_at=pass_ts)

    summary = get_dashboard_health_summary(test_db, now=now)
    assert summary["status"] == "green"
    assert summary["label"] == "Live Data"
    assert "4.0h ago" in summary["description"]


def test_get_dashboard_health_summary_stale_amber(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    pass_ts = (now - timedelta(hours=30)).isoformat()
    _insert_cycle_log(test_db, agent="Verifier", status="ok", notes="verdict=pass", finished_at=pass_ts)

    summary = get_dashboard_health_summary(test_db, now=now)
    assert summary["status"] == "amber"
    assert summary["label"] == "Stale Data"
    assert "30.0h ago" in summary["description"]


def test_get_dashboard_health_summary_degraded_red(test_db: Path) -> None:
    now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")
    _insert_cycle_log(test_db, agent="Verifier", status="failed", notes="verdict=fail")

    summary = get_dashboard_health_summary(test_db, now=now)
    assert summary["status"] == "red"
    assert summary["label"] == "Degraded"
    assert "Last 3 verification cycles failed" in summary["description"]


def test_get_dashboard_health_summary_unreachable_red(tmp_path: Path) -> None:
    bad_db = tmp_path / "missing" / "db.sqlite"
    summary = get_dashboard_health_summary(bad_db)
    assert summary["status"] == "red"
    assert summary["label"] == "Offline"


def test_get_dashboard_health_summary_never_crashes() -> None:
    # Invalid type or object that raises exception
    summary = get_dashboard_health_summary(None)  # type: ignore[arg-type]
    assert summary["status"] in ("red", "amber")


# ---------------------------------------------------------------------------
# Test CLI Execution
# ---------------------------------------------------------------------------
def test_cli_execution_healthy(test_db: Path) -> None:
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(hours=1)).isoformat()
    _insert_dummy_listing(test_db, recent_ts)
    _insert_cycle_log(test_db, agent="Orchestrator", status="complete", finished_at=recent_ts)
    _insert_cycle_log(test_db, agent="Verifier", status="ok", notes="verdict=pass", finished_at=recent_ts)

    proc = subprocess.run(
        [sys.executable, "-m", "edgedash.health", "--db-path", str(test_db)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "[PASS] Database reachability:" in proc.stdout
    assert "[PASS] Listing freshness:" in proc.stdout
    assert "[PASS] Cycle recency:" in proc.stdout
    assert "[PASS] Verification stability:" in proc.stdout


def test_cli_execution_unhealthy(test_db: Path) -> None:
    # Empty DB has no listings and no cycles -> unhealthy
    proc = subprocess.run(
        [sys.executable, "-m", "edgedash.health", "--db-path", str(test_db)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "[PASS] Database reachability:" in proc.stdout
    assert "[FAIL] Listing freshness:" in proc.stdout
    assert "[FAIL] Cycle recency:" in proc.stdout
