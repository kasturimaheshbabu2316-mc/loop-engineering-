"""Planning module for EdgeDash.

Deterministic planning decisions based on system state.
No I/O, no LLM - pure functions of (state, config).
"""

from dataclasses import dataclass
from typing import Any

from edgedash.state import SystemState


@dataclass
class Task:
    """A single task to execute in the cycle.

    agent_name: Name of the agent (Fetcher, Scorer, GapAnalyzer)
    goal: What the task aims to accomplish
    stop_conditions: Dict with max_* limits from config
    reason: Human-readable reason for including/skipping this task
    """

    agent_name: str
    goal: str
    stop_conditions: dict[str, Any]
    reason: str


@dataclass
class Plan:
    """An ordered list of tasks to execute in this cycle."""

    tasks: list[Task]

    def render(self) -> str:
        """Render plan as compact printable output.

        One line per agent showing goal, stop conditions, and reason.
        Skipped agents appear with their reason.
        """
        lines = ["  Plan", "  ───"]

        # Check if ALL tasks are skipped (nothing to do)
        all_skipped = all("skipped" in task.reason.lower() for task in self.tasks)
        if all_skipped:
            return "\n".join(lines) + "\n    (nothing to do)"

        for task in self.tasks:
            # Format stop conditions
            if task.stop_conditions:
                stops = ", ".join(f"{k}={v}" for k, v in task.stop_conditions.items())
                stops_str = f" ({stops})"
            else:
                stops_str = ""

            # Truncate reason if too long
            reason = task.reason
            if len(reason) > 50:
                reason = reason[:47] + "..."

            lines.append(f"  {task.agent_name:<15} {task.goal}{stops_str}")
            lines.append(f"    → {reason}")

        return "\n".join(lines)


def build_plan(state: SystemState, config: Any) -> Plan:
    """Build execution plan from current state and configuration.

    Pure function - no I/O, deterministic based on inputs.

    Decision rules (thresholds from config):
    - fetch    if hours_since_fetch >= fetch_interval_hours (default 6)
    - score    if unscored_count > 0
    - analyse  if gaps_stale, or gaps_computed_at is null

    Args:
        state: Current system state from read_state()
        config: User configuration with thresholds

    Returns:
        Plan with ordered tasks including skipped ones with reasons
    """
    tasks: list[Task] = []

    # Get thresholds from config
    fetch_interval_hours = getattr(config, "fetch_interval_hours", 6)
    max_pages = getattr(config, "max_pages", 5)
    max_listings = getattr(config, "max_listings", 50)
    max_score_items = getattr(config, "scorer_batch_size", 25)
    max_score_seconds = getattr(config, "scorer_timeout_seconds", 120)
    max_analyse_seconds = getattr(config, "gap_analyzer_timeout_seconds", 60)

    # ----- FETCH -----
    # Run fetcher if: never fetched (hours_since_fetch is None) OR stale
    should_fetch = state.hours_since_fetch is None or state.hours_since_fetch >= fetch_interval_hours
    
    if should_fetch:
        reason = "never fetched" if state.hours_since_fetch is None else f"hours_since_fetch={state.hours_since_fetch:.1f} >= {fetch_interval_hours}"
        tasks.append(Task(
            agent_name="MockFetcher",  # matches AGENT_REGISTRY
            goal="Fetch new job listings",
            stop_conditions={"max_pages": max_pages, "max_listings": max_listings},
            reason=reason,
        ))
    else:
        tasks.append(Task(
            agent_name="MockFetcher",  # matches AGENT_REGISTRY
            goal="Fetch new job listings",
            stop_conditions={"max_pages": max_pages, "max_listings": max_listings},
            reason=f"skipped: hours_since_fetch={state.hours_since_fetch:.1f} < {fetch_interval_hours}",
        ))

    # ----- SCORE -----
    if state.unscored_count > 0:
        tasks.append(Task(
            agent_name="Scorer",
            goal="Score unscored listings",
            stop_conditions={"max_items": max_score_items, "max_seconds": max_score_seconds},
            reason=f"unscored_count={state.unscored_count}",
        ))
    else:
        tasks.append(Task(
            agent_name="Scorer",
            goal="Score unscored listings",
            stop_conditions={"max_items": max_score_items, "max_seconds": max_score_seconds},
            reason=f"skipped: unscored_count={state.unscored_count}",
        ))

    # ----- ANALYSE -----
    should_analyse = state.gaps_stale or state.gaps_computed_at is None

    if should_analyse:
        reason = "gaps_stale=True" if state.gaps_stale else "gaps_computed_at is null"
        tasks.append(Task(
            agent_name="GapAnalyzer",
            goal="Analyse skill gaps from scored listings",
            stop_conditions={"max_seconds": max_analyse_seconds},
            reason=reason,
        ))
    else:
        tasks.append(Task(
            agent_name="GapAnalyzer",
            goal="Analyse skill gaps from scored listings",
            stop_conditions={"max_seconds": max_analyse_seconds},
            reason=f"skipped: gaps_stale=False, gaps_computed_at={state.gaps_computed_at}",
        ))

    return Plan(tasks=tasks)