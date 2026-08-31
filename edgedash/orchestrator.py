"""Orchestrator for EdgeDash.

State-driven pipeline orchestrator. Reads state, builds a plan, executes
tasks in order, handles failures gracefully, and writes one cycle summary.

Rules 28-33 govern this module:
- 28: State-driven decisions (no hardcoded sequences)
- 29: Stop conditions are bounds, not goals
- 30: Task = (agent_name, goal, stop_conditions, reason)
- 31: Print plan before executing (transparency)
- 32: Failures don't kill the cycle (partial completion)
- 33: One cycle summary row with outcome
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass

from edgedash import storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.config import Config
from edgedash.state import read_state
from edgedash.planning import build_plan, Plan


# ---------------------------------------------------------------------------
# Agent registry
# To add an agent: add one entry here, add decision rule in build_plan.
# ---------------------------------------------------------------------------

AGENT_REGISTRY: list[Agent] = [
    MockFetcher(),
    Scorer(),
    GapAnalyzer(),
]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_SEP = "─" * 60
_THIN = "·" * 60


def _hr(char: str = "─") -> None:
    print(char * 60)


def _header(title: str) -> None:
    print()
    _hr()
    print(f"  {title}")
    _hr()


def _row(label: str, value: str, width: int = 24) -> None:
    print(f"  {label:<{width}} {value}")


# ---------------------------------------------------------------------------
# Cycle summary (rule 33)
# ---------------------------------------------------------------------------

@dataclass
class CycleSummary:
    """Single summary row for the cycle (rule 33).

    outcome: "complete" | "partial" | "nothing_to_do"
    """

    plan_rendered: str
    ran: list[str]              # agents that ran
    skipped: list[str]          # agents that were skipped with reasons
    durations: dict[str, float] # agent_name -> elapsed seconds
    outcome: str
    total_records: int
    error: str | None = None


def _determine_outcome(plan: Plan, results: list[AgentResult]) -> str:
    """Determine cycle outcome (rule 33).

    - "complete": at least one agent ran successfully, no failures
    - "partial": at least one agent ran, some failed
    - "nothing_to_do": all agents skipped (this is SUCCESS, exit 0)
    """
    ran = [r for r in results if r.status not in ("skipped", "pending")]
    failed = [r for r in results if r.status == "failed"]

    if not ran:
        return "nothing_to_do"
    elif failed:
        return "partial"
    else:
        return "complete"


# ---------------------------------------------------------------------------
# Core cycle (rules 28-33)
# ---------------------------------------------------------------------------

def run_cycle(config: Config) -> str:
    """Execute one state-driven EdgeDash pipeline cycle.

    Steps (rules 28-33):
        1. Init database.
        2. Read state via read_state().
        3. Build plan via build_plan().
        4. PRINT rendered plan (rule 31).
        5. Execute tasks in order, passing goal and stop_conditions.
        6. Wrap each task in try/except (rule 32).
        7. Write ONE cycle summary (rule 33).

    Args:
        config: Loaded user configuration.

    Returns:
        Cycle outcome: "complete" | "partial" | "nothing_to_do"
    """
    cycle_start = datetime.now(timezone.utc)
    db = config.db_path

    # ── 1. Init ──────────────────────────────────────────────────────────────
    storage.init_db(db)

    # ── 2. Read state (rule 28) ──────────────────────────────────────────────
    state = read_state(config, cycle_start)

    _header("EdgeDash  ·  Cycle starting")
    print(f"  {'Time':<24} {cycle_start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  {'Profile':<24} {config.target_role} · {config.target_city}")
    print(f"  {'Database':<24} {db}")
    print()
    print("  STATE READ FROM DB")
    _hr("·")
    _row("Last fetch", state.last_fetch_at or "never")
    _row("Hours since fetch", f"{state.hours_since_fetch:.1f}" if state.hours_since_fetch is not None else "never")
    _row("Unscored rows", str(state.unscored_count))
    _row("Gaps stale", str(state.gaps_stale))
    if state.gaps_computed_at:
        _row("Gaps computed", state.gaps_computed_at[:19])

    # ── 3. Build plan (rules 28, 30) ─────────────────────────────────────────
    plan = build_plan(state, config)

    # ── 4. Print plan (rule 31) ──────────────────────────────────────────────
    _header("Plan")
    print(plan.render())

    # ── 5. Execute tasks (rules 29, 32) ──────────────────────────────────────
    _header("Agent runs")

    results: list[AgentResult] = []
    durations: dict[str, float] = {}

    # Build map of agent_name -> agent instance
    agent_by_name = {agent.name: agent for agent in AGENT_REGISTRY}

    for task in plan.tasks:
        agent = agent_by_name.get(task.agent_name)

        if not agent:
            # Unknown agent - log and skip
            results.append(AgentResult(
                agent=task.agent_name,
                status="failed",
                records_touched=0,
                notes=f"Unknown agent: {task.agent_name}",
            ))
            continue

        # Check if skipped
        if "skipped" in task.reason.lower():
            print(f"\n  ○ {task.agent_name} … skipped")
            print(f"    → {task.reason}")
            results.append(AgentResult(
                agent=task.agent_name,
                status="skipped",
                records_touched=0,
                notes=task.reason,
            ))
            durations[task.agent_name] = 0.0
            continue

        # Run the agent (rule 32: wrap in try/except)
        agent_start = datetime.now(timezone.utc)
        print(f"\n  ▸ Running {task.agent_name} …")
        print(f"    goal: {task.goal}")
        if task.stop_conditions:
            stops = ", ".join(f"{k}={v}" for k, v in task.stop_conditions.items())
            print(f"    limits: {stops}")

        try:
            result = agent.run(config, db, task.goal, task.stop_conditions)
        except Exception as exc:
            # Rule 32: log failure, continue with remaining tasks
            result = AgentResult(
                agent=task.agent_name,
                status="failed",
                records_touched=0,
                notes=str(exc),
            )

        agent_end = datetime.now(timezone.utc)
        elapsed = (agent_end - agent_start).total_seconds()
        durations[task.agent_name] = elapsed

        # Log to cycle_log (hard rule #5)
        storage.log_cycle(
            db_path=db,
            agent=result.agent,
            started_at=agent_start.isoformat(),
            finished_at=agent_end.isoformat(),
            records_touched=result.records_touched,
            status=result.status,
            notes=result.notes,
        )

        status_icon = "✓" if result.status == "ok" else "✗"
        print(f"    {status_icon} status={result.status}  "
              f"records={result.records_touched}  "
              f"elapsed={elapsed:.2f}s")
        if result.notes:
            print(f"      note: {result.notes}")

        results.append(result)

    # ── 6. Determine outcome (rule 33) ───────────────────────────────────────
    outcome = _determine_outcome(plan, results)

    # Build summary
    ran = [r.agent for r in results if r.status not in ("skipped", "pending")]
    skipped = [f"{r.agent}: {r.notes}" for r in results if r.status == "skipped"]
    total_records = sum(r.records_touched for r in results)

    summary = CycleSummary(
        plan_rendered=plan.render(),
        ran=ran,
        skipped=skipped,
        durations=durations,
        outcome=outcome,
        total_records=total_records,
    )

    # ── 7. Print cycle summary (rule 33) ─────────────────────────────────────
    _header("Cycle summary")

    col = (20, 10, 10, 40)
    print(f"  {'Agent':<{col[0]}} {'Status':<{col[1]}} {'Records':>{col[2]}}  {'Notes'}")
    _hr("·")
    for r in results:
        note_short = (r.notes or "")[:40]
        print(f"  {r.agent:<{col[0]}} {r.status:<{col[1]}} {r.records_touched:>{col[2]}}  {note_short}")

    _hr("·")
    print(f"  {'TOTAL':<{col[0]}} {outcome:<{col[1]}} {total_records:>{col[2]}}")
    print()
    _row("Cycle duration", f"{sum(durations.values()):.2f}s")
    _row("Outcome", outcome)

    if outcome == "partial":
        failed = [r.agent for r in results if r.status == "failed"]
        _row("Failed agents", ", ".join(failed) if failed else "none")

    # Post-cycle db state
    print()
    print("  POST-CYCLE DB STATE")
    _hr("·")
    _row("Unscored listings", str(storage.count_unscored(db)))
    _row("Last fetch", storage.last_fetch_time(db) or "—")

    _hr()
    print()

    # ── 8. Run Verifier (Rule 24, verification checks) ──────────────────────
    verifier_start = datetime.now(timezone.utc)
    
    # Gather data for verification checks
    import json
    
    scores = []
    facts_list = []
    
    conn = storage._connect(db)
    cur = conn.cursor()
    try:
        cur.execute("SELECT fit_score FROM listings WHERE fit_score IS NOT NULL")
        scores = [row[0] for row in cur.fetchall()]
        
        cur.execute("SELECT extraction FROM extraction_cache")
        for row in cur.fetchall():
            try:
                facts_list.append(json.loads(row[0]))
            except Exception:
                pass
    finally:
        conn.close()
        
    gaps = storage.get_skill_gaps(db)
    latest_fetch_at = storage.last_fetch_time(db)
    
    from edgedash.verification import run_all_checks
    verdict = run_all_checks(
        scores=scores,
        facts_list=facts_list,
        gaps=gaps,
        latest_fetch_at=latest_fetch_at,
        config=config,
        now=verifier_start,
    )
    
    print(f"\n  ▸ Running Verifier …")
    status_icon = "✓" if verdict.passed else "✗"
    print(f"    {status_icon} status={'pass' if verdict.passed else 'fail'} summary={verdict.summary}")
    
    # Log verifier result (Rule 5 / Verifier integration)
    storage.log_cycle(
        db_path=db,
        agent="Verifier",
        started_at=verifier_start.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        records_touched=len(scores),
        status="ok" if verdict.passed else "failed",
        notes=f"verdict={'pass' if verdict.passed else 'fail'} notes={verdict.summary}",
    )

    # ── 9. Write cycle summary row (rule 33) ─────────────────────────────────
    summary_notes = f"outcome={outcome}"
    if summary.ran:
        summary_notes += f" ran={','.join(summary.ran)}"
    if summary.skipped:
        summary_notes += f" skipped={len(summary.skipped)}"

    storage.log_cycle(
        db_path=db,
        agent="Orchestrator",
        started_at=cycle_start.isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        records_touched=total_records,
        status=outcome,
        notes=summary_notes,
    )

    return outcome

