"""Deterministic pipeline verification checks for EdgeDash.

Each check is a pure function that takes the data it needs and returns a
CheckResult. Thresholds are obtained from configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class CheckResult:
    """Result of a single verification check."""
    name: str
    passed: bool
    observed: Any
    threshold: Any
    message: str


@dataclass
class Verdict:
    """Aggregated verdict of all verification checks."""
    passed: bool
    failed_checks: list[CheckResult]
    summary: str


def check_score_spread(scores: list[int], config: Any) -> CheckResult:
    """FAILS if max - min < min_score_spread, or if stdev is below min_score_stdev.
    Passes trivially if fewer than 5 scores.
    """
    min_score_spread = getattr(config, "min_score_spread", 10)
    min_score_stdev = getattr(config, "min_score_stdev", 5.0)

    if len(scores) < 5:
        return CheckResult(
            name="check_score_spread",
            passed=True,
            observed=len(scores),
            threshold=5,
            message=f"Fewer than 5 scores ({len(scores)}) - passed trivially.",
        )

    max_s = max(scores)
    min_s = min(scores)
    spread = max_s - min_s

    mean = sum(scores) / len(scores)
    variance = sum((x - mean) ** 2 for x in scores) / len(scores)
    stdev = variance ** 0.5

    if spread < min_score_spread:
        return CheckResult(
            name="check_score_spread",
            passed=False,
            observed={"spread": spread, "stdev": stdev},
            threshold={"min_score_spread": min_score_spread, "min_score_stdev": min_score_stdev},
            message=f"Score spread {spread} is less than threshold {min_score_spread}.",
        )

    if stdev < min_score_stdev:
        return CheckResult(
            name="check_score_spread",
            passed=False,
            observed={"spread": spread, "stdev": stdev},
            threshold={"min_score_spread": min_score_spread, "min_score_stdev": min_score_stdev},
            message=f"Score standard deviation {stdev:.2f} is less than threshold {min_score_stdev}.",
        )

    return CheckResult(
        name="check_score_spread",
        passed=True,
        observed={"spread": spread, "stdev": stdev},
        threshold={"min_score_spread": min_score_spread, "min_score_stdev": min_score_stdev},
        message=f"Score spread ({spread}) and stdev ({stdev:.2f}) are within acceptable limits.",
    )


def check_extraction_sanity(facts_list: list[dict[str, Any]], config: Any) -> CheckResult:
    """FAILS if more than max_empty_extraction_pct of listings have an empty required_skills list,
    or if any listing has more than max_skills_per_listing.
    """
    max_empty_extraction_pct = getattr(config, "max_empty_extraction_pct", 20.0)
    max_skills_per_listing = getattr(config, "max_skills_per_listing", 20)

    if not facts_list:
        return CheckResult(
            name="check_extraction_sanity",
            passed=True,
            observed=0,
            threshold=max_empty_extraction_pct,
            message="No listings to check - passed trivially.",
        )

    empty_count = 0
    max_skills_found = 0

    for fact in facts_list:
        req_skills = fact.get("required_skills", [])
        if not req_skills:
            empty_count += 1
        else:
            max_skills_found = max(max_skills_found, len(req_skills))

    empty_pct = (empty_count / len(facts_list)) * 100.0

    if empty_pct > max_empty_extraction_pct:
        return CheckResult(
            name="check_extraction_sanity",
            passed=False,
            observed={"empty_pct": empty_pct, "max_skills_found": max_skills_found},
            threshold={"max_empty_pct": max_empty_extraction_pct, "max_skills_per_listing": max_skills_per_listing},
            message=f"Empty extraction rate of {empty_pct:.1f}% exceeds threshold of {max_empty_extraction_pct}%.",
        )

    if max_skills_found > max_skills_per_listing:
        return CheckResult(
            name="check_extraction_sanity",
            passed=False,
            observed={"empty_pct": empty_pct, "max_skills_found": max_skills_found},
            threshold={"max_empty_pct": max_empty_extraction_pct, "max_skills_per_listing": max_skills_per_listing},
            message=f"Listing has {max_skills_found} skills, which exceeds limit of {max_skills_per_listing}.",
        )

    return CheckResult(
        name="check_extraction_sanity",
        passed=True,
        observed={"empty_pct": empty_pct, "max_skills_found": max_skills_found},
        threshold={"max_empty_pct": max_empty_extraction_pct, "max_skills_per_listing": max_skills_per_listing},
        message=f"Extraction sanity checks passed. Empty rate: {empty_pct:.1f}%, Max skills: {max_skills_found}.",
    )


def check_gap_sample_size(gaps: list[Any], config: Any) -> CheckResult:
    """FAILS if the top-ranked gap was computed from fewer than min_gap_sample listings."""
    min_gap_sample = getattr(config, "min_gap_sample", 3)

    if not gaps:
        return CheckResult(
            name="check_gap_sample_size",
            passed=True,
            observed=0,
            threshold=min_gap_sample,
            message="No gaps computed - passed trivially.",
        )

    top_gap = gaps[0]

    # Dynamically extract count from Gap object, dictionary, or row-like object
    count = None
    if hasattr(top_gap, "listings_blocked"):
        count = getattr(top_gap, "listings_blocked")
    elif hasattr(top_gap, "frequency"):
        count = getattr(top_gap, "frequency")
    elif isinstance(top_gap, dict) or (hasattr(top_gap, "__getitem__") and not isinstance(top_gap, str)):
        try:
            count = top_gap["listings_blocked"]
        except (KeyError, TypeError):
            try:
                count = top_gap["frequency"]
            except (KeyError, TypeError):
                pass

    if count is None:
        raise ValueError("Gap object has no listings_blocked or frequency field")

    if count < min_gap_sample:
        return CheckResult(
            name="check_gap_sample_size",
            passed=False,
            observed=count,
            threshold=min_gap_sample,
            message=f"Top-ranked gap has count {count}, which is less than threshold {min_gap_sample}.",
        )

    return CheckResult(
        name="check_gap_sample_size",
        passed=True,
        observed=count,
        threshold=min_gap_sample,
        message=f"Top-ranked gap has sufficient sample size of {count}.",
    )


def check_freshness(latest_fetch_at: str | None, config: Any, now: datetime) -> CheckResult:
    """FAILS if the newest listing is older than max_data_age_days."""
    max_data_age_days = getattr(config, "max_data_age_days", 3)

    if latest_fetch_at is None:
        return CheckResult(
            name="check_freshness",
            passed=False,
            observed=None,
            threshold=max_data_age_days,
            message="No fetch timestamp available (never fetched).",
        )

    # Parse timestamp
    dt = datetime.fromisoformat(latest_fetch_at.replace("Z", "+00:00"))
    
    # Standardize to timezone-aware UTC comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_days = (now - dt).total_seconds() / 86400.0

    if age_days > max_data_age_days:
        return CheckResult(
            name="check_freshness",
            passed=False,
            observed=age_days,
            threshold=max_data_age_days,
            message=f"Newest listing is {age_days:.2f} days old, exceeding limit of {max_data_age_days} days.",
        )

    return CheckResult(
        name="check_freshness",
        passed=True,
        observed=age_days,
        threshold=max_data_age_days,
        message=f"Newest listing age is {age_days:.2f} days, which is within the limit.",
    )


def run_all_checks(
    scores: list[int],
    facts_list: list[dict[str, Any]],
    gaps: list[Any],
    latest_fetch_at: str | None,
    config: Any,
    now: datetime,
) -> Verdict:
    """Run all verification checks. Passes only if all checks pass."""
    results = [
        check_score_spread(scores, config),
        check_extraction_sanity(facts_list, config),
        check_gap_sample_size(gaps, config),
        check_freshness(latest_fetch_at, config, now),
    ]

    failed_checks = [r for r in results if not r.passed]
    passed = len(failed_checks) == 0

    if passed:
        summary = "All verification checks passed."
    else:
        failed_names = ", ".join(r.name for r in failed_checks)
        summary = f"Verification failed: {len(failed_checks)} check(s) failed ({failed_names})."

    return Verdict(passed=passed, failed_checks=failed_checks, summary=summary)
