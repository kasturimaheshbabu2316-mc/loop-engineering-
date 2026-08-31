"""Tests for EdgeDash planning module."""

import pytest
from datetime import datetime, timezone

from edgedash.state import SystemState
from edgedash.planning import build_plan, Task, Plan


# Minimal config for tests
class MockConfig:
    db_path = "edgedash.db"
    fetch_interval_hours = 6
    max_pages = 5
    max_listings = 50
    scorer_batch_size = 25
    scorer_timeout_seconds = 120
    gap_analyzer_timeout_seconds = 60


def make_state(
    hours_since_fetch: float | None = None,
    unscored_count: int = 0,
    gaps_stale: bool = False,
    gaps_computed_at: str | None = None,
) -> SystemState:
    """Helper to create SystemState with defaults."""
    now = datetime.now(timezone.utc)
    return SystemState(
        last_fetch_at=now.isoformat() if hours_since_fetch is not None else None,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at or now.isoformat(),
        gaps_stale=gaps_stale,
        latest_score_at=now.isoformat(),
        last_cycle_verdict="pass",
        last_cycle_at=now.isoformat(),
    )


class TestBuildPlan:
    """Test build_plan decision rules."""

    def test_all_stale_all_three_run(self):
        """Everything stale: fetch (never), score (50), analyse (stale)."""
        state = make_state(
            hours_since_fetch=None,  # never fetched
            unscored_count=50,
            gaps_stale=True,
        )
        plan = build_plan(state, MockConfig())

        assert len(plan.tasks) == 3
        assert plan.tasks[0].agent_name == "MockFetcher"
        assert plan.tasks[0].reason == "never fetched"
        assert plan.tasks[1].agent_name == "Scorer"
        assert "unscored_count=50" in plan.tasks[1].reason
        assert plan.tasks[2].agent_name == "GapAnalyzer"
        assert "gaps_stale=True" in plan.tasks[2].reason

    def test_nothing_to_do_all_skipped(self):
        """Nothing stale: fetch recent, no unscored, gaps fresh."""
        state = make_state(
            hours_since_fetch=2.0,  # recent
            unscored_count=0,  # all scored
            gaps_stale=False,  # fresh
        )
        plan = build_plan(state, MockConfig())

        assert len(plan.tasks) == 3
        # All should be skipped
        assert "skipped" in plan.tasks[0].reason.lower()
        assert "skipped" in plan.tasks[1].reason.lower()
        assert "skipped" in plan.tasks[2].reason.lower()

    def test_only_unscored(self):
        """Fetch recent, unscored exist, gaps fresh - only scorer runs."""
        state = make_state(
            hours_since_fetch=1.0,  # recent
            unscored_count=30,
            gaps_stale=False,
        )
        plan = build_plan(state, MockConfig())

        # Fetcher skipped (recent)
        assert "skipped" in plan.tasks[0].reason.lower()
        # Scorer runs (has work)
        assert "unscored_count=30" in plan.tasks[1].reason
        # GapAnalyzer skipped (fresh)
        assert "skipped" in plan.tasks[2].reason.lower()

    def test_gaps_stale_only(self):
        """Fetch recent, no unscored, gaps stale - only GapAnalyzer runs."""
        state = make_state(
            hours_since_fetch=1.0,
            unscored_count=0,
            gaps_stale=True,
        )
        plan = build_plan(state, MockConfig())

        assert "skipped" in plan.tasks[0].reason.lower()  # Fetcher
        assert "skipped" in plan.tasks[1].reason.lower()  # Scorer
        assert plan.tasks[2].agent_name == "GapAnalyzer"
        assert "gaps_stale=True" in plan.tasks[2].reason

    def test_fetch_stale_but_nothing_else(self):
        """Fetch stale, no unscored, gaps fresh - only fetcher runs."""
        state = make_state(
            hours_since_fetch=10.0,  # stale (>6 hours)
            unscored_count=0,
            gaps_stale=False,
        )
        plan = build_plan(state, MockConfig())

        assert plan.tasks[0].agent_name == "MockFetcher"
        assert "hours_since_fetch=10.0" in plan.tasks[0].reason
        assert "skipped" in plan.tasks[1].reason.lower()  # Scorer
        assert "skipped" in plan.tasks[2].reason.lower()  # GapAnalyzer


class TestPlanRender:
    """Test Plan.render() output format."""

    def test_render_shows_stop_conditions(self):
        """Render includes stop conditions from config."""
        state = make_state(
            hours_since_fetch=None,  # will fetch
            unscored_count=50,  # will score
            gaps_stale=True,  # will analyse
        )
        plan = build_plan(state, MockConfig())
        output = plan.render()

        assert "max_pages=5" in output
        assert "max_listings=50" in output
        assert "max_items=25" in output
        assert "max_seconds=120" in output

    def test_render_shows_reasons(self):
        """Render includes human-readable reasons."""
        state = make_state(
            hours_since_fetch=10.0,
            unscored_count=0,
            gaps_stale=False,
        )
        plan = build_plan(state, MockConfig())
        output = plan.render()

        assert "hours_since_fetch" in output
        assert "unscored_count" in output

    def test_render_nothing_to_do(self):
        """Render shows nothing to do when all skipped."""
        state = make_state(
            hours_since_fetch=2.0,
            unscored_count=0,
            gaps_stale=False,
        )
        plan = build_plan(state, MockConfig())
        output = plan.render()

        assert "(nothing to do)" in output


class TestTaskStructure:
    """Test Task dataclass structure."""

    def test_task_has_all_fields(self):
        """Each task has agent_name, goal, stop_conditions, reason."""
        state = make_state(hours_since_fetch=None, unscored_count=1)
        plan = build_plan(state, MockConfig())

        for task in plan.tasks:
            assert task.agent_name
            assert task.goal
            assert task.stop_conditions
            assert task.reason

    def test_stop_conditions_from_config(self):
        """Stop conditions values come from config."""
        state = make_state(hours_since_fetch=None, unscored_count=1)
        plan = build_plan(state, MockConfig())

        fetcher_task = plan.tasks[0]
        assert fetcher_task.stop_conditions["max_pages"] == 5
        assert fetcher_task.stop_conditions["max_listings"] == 50

        scorer_task = plan.tasks[1]
        assert scorer_task.stop_conditions["max_items"] == 25
        assert scorer_task.stop_conditions["max_seconds"] == 120