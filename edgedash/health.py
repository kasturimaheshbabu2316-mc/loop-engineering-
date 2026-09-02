"""System health reporting module for EdgeDash.

Provides read-only health checks for deployed systems:
1. Database connectivity
2. Listing freshness (newest listing <= 3 days old)
3. Cycle recency (successful cycle within 48 hours)
4. Verification stability (last 3 cycles did not all fail verification)

Exits non-zero on CLI if any check fails.
Rule 50: Safe execution for dashboard rendering without exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from edgedash.config import load_config
from edgedash.env import load_env
from edgedash import storage


@dataclass
class HealthCheckResult:
    """Outcome of an individual health check."""
    name: str
    passed: bool
    observed: str
    message: str


@dataclass
class HealthReport:
    """Aggregated health report across all system checks."""
    is_healthy: bool
    checks: list[HealthCheckResult]
    now: datetime
    db_path: str


def check_database(db_path: str | Path) -> HealthCheckResult:
    """Check database reachability."""
    ok, details = storage.ping_db(db_path)
    if ok:
        return HealthCheckResult(
            name="Database reachability",
            passed=True,
            observed=f"Connected ({details})",
            message=f"Database is reachable and responding ({details}).",
        )
    return HealthCheckResult(
        name="Database reachability",
        passed=False,
        observed=f"Unreachable ({details})",
        message=f"Database connection failed: {details}",
    )


def check_listing_freshness(
    db_path: str | Path, now: datetime, max_age_days: float = 3.0
) -> HealthCheckResult:
    """Check if the newest listing is within max_age_days."""
    newest_ts = storage.get_newest_listing_time(db_path)
    if not newest_ts:
        return HealthCheckResult(
            name="Listing freshness",
            passed=False,
            observed="No listings found in database",
            message="No job listings present in the database.",
        )
    try:
        dt = datetime.fromisoformat(newest_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age_days = (now - dt).total_seconds() / 86400.0
        passed = age_days <= max_age_days
        observed = f"{age_days:.2f} days old (latest: {newest_ts[:19]})"
        msg = f"Newest listing age: {age_days:.2f}d (threshold: {max_age_days}d)."
        return HealthCheckResult("Listing freshness", passed, observed, msg)
    except Exception as exc:
        return HealthCheckResult("Listing freshness", False, f"Invalid timestamp ({exc})", str(exc))


def check_cycle_recency(
    db_path: str | Path, now: datetime, max_age_hours: float = 48.0
) -> HealthCheckResult:
    """Check if a successful cycle ran within max_age_hours."""
    last_cycle_ts = storage.get_last_successful_cycle_time(db_path)
    if not last_cycle_ts:
        return HealthCheckResult(
            name="Cycle recency",
            passed=False,
            observed="No successful cycle found in cycle_log",
            message="No successful cycle recorded in cycle_log.",
        )
    try:
        dt = datetime.fromisoformat(last_cycle_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age_hours = (now - dt).total_seconds() / 3600.0
        passed = age_hours <= max_age_hours
        observed = f"{age_hours:.2f} hours ago (finished: {last_cycle_ts[:19]})"
        msg = f"Last successful cycle: {age_hours:.2f}h ago (threshold: {max_age_hours}h)."
        return HealthCheckResult("Cycle recency", passed, observed, msg)
    except Exception as exc:
        return HealthCheckResult("Cycle recency", False, f"Invalid timestamp ({exc})", str(exc))


def check_verification_stability(db_path: str | Path) -> HealthCheckResult:
    """Check if the last 3 verification cycles all failed."""
    runs = storage.get_recent_verifier_results(db_path, limit=3)
    if len(runs) < 3:
        verdicts = [_verdict_from_run(r) for r in runs]
        return HealthCheckResult(
            name="Verification stability",
            passed=True,
            observed=f"Recent verdicts: {verdicts} (< 3 total runs recorded)",
            message="Fewer than 3 verification cycles recorded; passing.",
        )
    verdicts = [_verdict_from_run(r) for r in runs]
    all_failed = all(v == "fail" for v in verdicts)
    passed = not all_failed
    observed = f"Last 3 verdicts: {verdicts}"
    msg = "Last 3 cycles all failed verification!" if all_failed else f"Verification stable: {verdicts}"
    return HealthCheckResult("Verification stability", passed, observed, msg)


def _verdict_from_run(run: dict[str, Any]) -> str:
    status = (run.get("status") or "").lower()
    notes = (run.get("notes") or "").lower()
    if status == "ok" or "pass" in notes:
        return "pass"
    return "fail"


def run_health_checks(db_path: str | Path, now: datetime | None = None) -> HealthReport:
    """Execute all system health checks."""
    if now is None:
        now = datetime.now(timezone.utc)

    db_check = check_database(db_path)
    if not db_check.passed:
        checks = [
            db_check,
            HealthCheckResult("Listing freshness", False, "Database unreachable", "Skipped"),
            HealthCheckResult("Cycle recency", False, "Database unreachable", "Skipped"),
            HealthCheckResult("Verification stability", False, "Database unreachable", "Skipped"),
        ]
        return HealthReport(is_healthy=False, checks=checks, now=now, db_path=str(db_path))

    checks = [
        db_check,
        check_listing_freshness(db_path, now),
        check_cycle_recency(db_path, now),
        check_verification_stability(db_path),
    ]
    is_healthy = all(c.passed for c in checks)
    return HealthReport(is_healthy=is_healthy, checks=checks, now=now, db_path=str(db_path))


def get_dashboard_health_summary(
    db_path: str | Path, now: datetime | None = None
) -> dict[str, str]:
    """Retrieve safe status indicator for dashboard under Rule 50."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    try:
        ok, details = storage.ping_db(db_path)
        if not ok:
            return {"status": "red", "label": "Offline", "description": "Database unreachable"}

        # Check last 3 verifier runs for red status
        runs = storage.get_recent_verifier_results(db_path, limit=3)
        if len(runs) >= 3:
            verdicts = [_verdict_from_run(r) for r in runs]
            if all(v == "fail" for v in verdicts):
                return {"status": "red", "label": "Degraded", "description": "Last 3 verification cycles failed"}

        # Check cycle recency within 24h for green status
        last_pass_ts = storage.get_last_passing_verifier_time(db_path) or storage.get_last_successful_cycle_time(db_path)
        if not last_pass_ts:
            return {"status": "amber", "label": "No Data", "description": "No verified cycle recorded yet"}

        dt = datetime.fromisoformat(last_pass_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = (now - dt).total_seconds() / 3600.0

        if age_hours <= 24.0:
            return {
                "status": "green",
                "label": "Live Data",
                "description": f"Verified {age_hours:.1f}h ago ({last_pass_ts[:10]})",
            }
        else:
            age_days = age_hours / 24.0
            time_str = f"{age_hours:.1f}h" if age_hours < 48 else f"{age_days:.1f}d"
            return {
                "status": "amber",
                "label": "Stale Data",
                "description": f"Last verified {time_str} ago ({last_pass_ts[:10]})",
            }
    except Exception as exc:
        return {"status": "amber", "label": "Unknown", "description": f"Telemetry unavailable: {exc}"}


def main() -> None:
    """CLI entry point for python -m edgedash.health."""
    load_env()
    db_path = None
    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser(description="EdgeDash System Health Reporter")
        parser.add_argument("--db-path", default=None, help="Path to database")
        args, _ = parser.parse_known_args()
        db_path = args.db_path

    if not db_path:
        try:
            cfg = load_config("config.yaml")
            db_path = cfg.db_path
        except Exception:
            db_path = "edgedash.db"

    report = run_health_checks(db_path)

    for c in report.checks:
        tag = "PASS" if c.passed else "FAIL"
        print(f"[{tag}] {c.name}: {c.observed}")

    if not report.is_healthy:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
