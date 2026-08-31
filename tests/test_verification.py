"""Tests for EdgeDash verification checks."""

from datetime import datetime, timezone
import pytest

from edgedash.verification import (
    check_score_spread,
    check_extraction_sanity,
    check_gap_sample_size,
    check_freshness,
    run_all_checks,
)


class MockConfig:
    """Mock config with verification thresholds."""
    min_score_spread = 10
    min_score_stdev = 5.0
    max_empty_extraction_pct = 20.0
    max_skills_per_listing = 20
    min_gap_sample = 3
    max_data_age_days = 3


class MockGap:
    """Mock Gap object for testing."""
    def __init__(self, listings_blocked):
        self.listings_blocked = listings_blocked


def test_check_score_spread_fewer_than_5():
    """Fewer than 5 scores should pass trivially."""
    config = MockConfig()
    result = check_score_spread([50, 60, 70], config)
    assert result.passed
    assert "Fewer than 5 scores" in result.message
    assert result.observed == 3


def test_check_score_spread_passing():
    """Scores with good spread and stdev should pass."""
    config = MockConfig()
    # spread = 40, mean = 70.0, stdev = 14.14
    result = check_score_spread([50, 60, 70, 80, 90], config)
    assert result.passed
    assert result.observed["spread"] == 40
    assert result.observed["stdev"] > 5.0


def test_check_score_spread_fails_low_spread():
    """Scores with spread < min_score_spread should fail."""
    config = MockConfig()
    # spread = 4, mean = 52.0, stdev = 1.41
    result = check_score_spread([50, 51, 52, 53, 54], config)
    assert not result.passed
    assert "spread" in result.message


def test_check_score_spread_fails_low_stdev():
    """Scores with sufficient spread but low stdev should fail."""
    config = MockConfig()
    # spread = 10, mean = 52.0, stdev = 4.0
    result = check_score_spread([50, 50, 50, 50, 60], config)
    assert not result.passed
    assert "standard deviation" in result.message


def test_check_extraction_sanity_empty():
    """Empty list of facts should pass trivially."""
    config = MockConfig()
    result = check_extraction_sanity([], config)
    assert result.passed
    assert "No listings to check" in result.message


def test_check_extraction_sanity_passing():
    """Sanity check with low empty percentage and normal skill count should pass."""
    config = MockConfig()
    facts = [
        {"required_skills": ["python", "sql"]},
        {"required_skills": ["tableau"]},
        {"required_skills": []},  # 1/4 empty = 25% empty? Wait, threshold is 20%. Let's do 1/5 empty = 20%.
        {"required_skills": ["excel"]},
        {"required_skills": ["python"]},
    ]
    result = check_extraction_sanity(facts, config)
    assert result.passed
    assert result.observed["empty_pct"] == 20.0
    assert result.observed["max_skills_found"] == 2


def test_check_extraction_sanity_fails_high_empty_pct():
    """Fails if empty required_skills pct exceeds threshold."""
    config = MockConfig()
    facts = [
        {"required_skills": []},
        {"required_skills": []},
        {"required_skills": ["python"]},
    ]  # 2/3 empty = 66.7% empty > 20%
    result = check_extraction_sanity(facts, config)
    assert not result.passed
    assert "Empty extraction rate" in result.message


def test_check_extraction_sanity_fails_too_many_skills():
    """Fails if a single listing has more skills than limit."""
    config = MockConfig()
    facts = [
        {"required_skills": [f"skill_{i}" for i in range(25)]},
        {"required_skills": ["python"]},
    ]
    result = check_extraction_sanity(facts, config)
    assert not result.passed
    assert "exceeds limit of" in result.message


def test_check_gap_sample_size_empty():
    """Empty gaps should pass trivially."""
    config = MockConfig()
    result = check_gap_sample_size([], config)
    assert result.passed
    assert "No gaps computed" in result.message


def test_check_gap_sample_size_passing_object():
    """Gaps as objects with >= min_gap_sample should pass."""
    config = MockConfig()
    gaps = [MockGap(listings_blocked=5), MockGap(listings_blocked=2)]
    result = check_gap_sample_size(gaps, config)
    assert result.passed
    assert result.observed == 5


def test_check_gap_sample_size_passing_dict():
    """Gaps as dicts with >= min_gap_sample should pass."""
    config = MockConfig()
    gaps = [{"listings_blocked": 3}, {"listings_blocked": 1}]
    result = check_gap_sample_size(gaps, config)
    assert result.passed
    assert result.observed == 3


def test_check_gap_sample_size_fails_low_sample():
    """Fails if top-ranked gap has listings_blocked < min_gap_sample."""
    config = MockConfig()
    gaps = [{"listings_blocked": 2}, {"listings_blocked": 1}]
    result = check_gap_sample_size(gaps, config)
    assert not result.passed
    assert "less than threshold" in result.message


def test_check_freshness_missing_timestamp():
    """Fails if fetch timestamp is None."""
    config = MockConfig()
    now = datetime(2026, 8, 26, 21, 0, 0, tzinfo=timezone.utc)
    result = check_freshness(None, config, now)
    assert not result.passed
    assert "No fetch timestamp available" in result.message


def test_check_freshness_passing():
    """Passes if age is <= max_data_age_days."""
    config = MockConfig()
    now = datetime(2026, 8, 26, 21, 0, 0, tzinfo=timezone.utc)
    latest_fetch_at = "2026-08-25T21:00:00+00:00"  # 1 day old
    result = check_freshness(latest_fetch_at, config, now)
    assert result.passed
    assert result.observed == 1.0


def test_check_freshness_fails_too_old():
    """Fails if age exceeds max_data_age_days."""
    config = MockConfig()
    now = datetime(2026, 8, 26, 21, 0, 0, tzinfo=timezone.utc)
    latest_fetch_at = "2026-08-20T21:00:00+00:00"  # 6 days old
    result = check_freshness(latest_fetch_at, config, now)
    assert not result.passed
    assert result.observed == 6.0


def test_run_all_checks_passing():
    """Verifies Verdict is passed when all checks pass."""
    config = MockConfig()
    now = datetime(2026, 8, 26, 21, 0, 0, tzinfo=timezone.utc)
    scores = [50, 60, 70, 80, 90]
    facts = [{"required_skills": ["python"]}]
    gaps = [{"listings_blocked": 4}]
    latest_fetch_at = "2026-08-26T20:00:00+00:00"

    verdict = run_all_checks(scores, facts, gaps, latest_fetch_at, config, now)
    assert verdict.passed
    assert len(verdict.failed_checks) == 0


def test_run_all_checks_failing():
    """Verifies Verdict fails when some checks fail."""
    config = MockConfig()
    now = datetime(2026, 8, 26, 21, 0, 0, tzinfo=timezone.utc)
    scores = [50, 50, 50, 50, 50]  # fails score spread
    facts = [{"required_skills": ["python"]}]
    gaps = [{"listings_blocked": 1}]  # fails gap sample
    latest_fetch_at = "2026-08-26T20:00:00+00:00"

    verdict = run_all_checks(scores, facts, gaps, latest_fetch_at, config, now)
    assert not verdict.passed
    assert len(verdict.failed_checks) == 2
    failed_names = [r.name for r in verdict.failed_checks]
    assert "check_score_spread" in failed_names
    assert "check_gap_sample_size" in failed_names
