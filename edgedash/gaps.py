"""CLI for viewing skill gaps.

Usage:
    python -m edgedash.gaps          # Use default db (edgedash.db)
    python -m edgedash.gaps my.db    # Custom database path
"""

from __future__ import annotations

import sqlite3
import json
import sys


def print_gaps_table(db_path: str = "edgedash.db") -> None:
    """Print latest gap snapshot as readable table."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get latest run (check both old and new schema)
    cur.execute("""
        SELECT MAX(run_id) as latest_run
        FROM skill_gaps
        WHERE run_id IS NOT NULL
    """)

    latest_run_row = cur.fetchone()
    latest_run = latest_run_row["latest_run"] if latest_run_row else None

    if not latest_run:
        # Try old schema (just frequency column)
        cur.execute("""
            SELECT MAX(frequency) as latest_freq
            FROM skill_gaps
            WHERE run_id IS NULL
        """)
        old_run = cur.fetchone()
        if old_run and old_run["latest_freq"]:
            print("Note: Using legacy skill_gaps data (run_id not available)")
            cur.execute("""
                SELECT skill, frequency as listings_blocked, last_seen as computed_at
                FROM skill_gaps
                WHERE run_id IS NULL
                ORDER BY frequency DESC
                LIMIT 10
            """)
            gaps = cur.fetchall()
            conn.close()

            if not gaps:
                print("No gap analysis found. Run the cycle with GapAnalyzer first.")
                return

            print()
            print(f"╔{'═' * 78}╗")
            print(f"║  SKILL GAPS — Latest Run")
            print(f"╠{'═' * 78}╣")
            print(f"║ {'Rank':<5} {'Skill':<25} {'Blocked':<10} {'Confidence':<15}║")
            print(f"╟{'─' * 78}╢")

            for i, gap in enumerate(gaps, 1):
                confidence = "⚠ LOW" if gap["listings_blocked"] < 3 else "✓ OK"
                print(f"║ {i:<5} {gap['skill']:<25} {gap['listings_blocked']:<10} {confidence:<15}║")

            print(f"╚{'═' * 78}╝")
            return

        print("No gap analysis runs found. Run the cycle with GapAnalyzer first.")
        return

    # Get gaps from latest run with new schema
    cur.execute("""
        SELECT skill, listings_blocked, opportunity_cost, mean_score,
               top_score, example_ids, computed_at
        FROM skill_gaps
        WHERE run_id = ?
        ORDER BY opportunity_cost DESC
        LIMIT 10
    """, (latest_run,))

    gaps = cur.fetchall()
    conn.close()

    if not gaps:
        print(f"No gaps found in run #{latest_run}.")
        return

    # Print header
    print()
    print(f"╔{'═' * 78}╗")
    print(f"║  SKILL GAPS — Run #{latest_run} — {gaps[0]['computed_at'][:19]}")
    print(f"╠{'═' * 78}╣")
    print(f"║ {'Rank':<5} {'Skill':<18} {'Blk':<5} {'Cost':<8} {'Mean':<6} {'Top':<5} {'Confidence':<12}║")
    print(f"╟{'─' * 78}╢")

    for i, gap in enumerate(gaps, 1):
        confidence = "⚠ LOW" if gap["listings_blocked"] < 3 else "✓ OK"
        
        # Cost bar visualization
        cost = gap["opportunity_cost"] or 0
        bar_len = min(int(cost * 2), 25)
        bar = "█" * bar_len + "░" * (25 - bar_len)

        print(f"║ {i:<5} {gap['skill']:<18} {gap['listings_blocked']:<5} "
              f"{cost:<8.2f} {gap['mean_score']:<6.1f} {gap['top_score']:<5} {confidence:<12}║")
        print(f"║       {bar:<73}║")

    print(f"╚{'═' * 78}╝")
    print()
    print("  Cost = sum(listing_score / 100) — higher = more valuable to acquire")
    print("  Blocked = # of high-scoring listings requiring this skill")
    print("  Low confidence = computed from <3 listings")
    print()


def print_trend_table(db_path: str = "edgedash.db") -> None:
    """Print skill gap trends across all snapshots (read-only)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all unique runs ordered by computed_at
    cur.execute("""
        SELECT DISTINCT run_id, computed_at
        FROM skill_gaps
        WHERE run_id IS NOT NULL
        ORDER BY computed_at ASC
    """)
    runs = cur.fetchall()

    if len(runs) == 0:
        print("No gap analysis runs found. Run the cycle with GapAnalyzer first.")
        conn.close()
        return

    if len(runs) == 1:
        print()
        print("═" * 78)
        print("  TREND REPORT — INSUFFICIENT DATA")
        print("═" * 78)
        print()
        print(f"  Only 1 snapshot found: {runs[0]['computed_at'][:19]}")
        print()
        print("  Need at least 2 snapshots to show a trend.")
        print("  Run the cycle for 1+ more days to see how your gaps are evolving.")
        print()
        print("═" * 78)
        conn.close()
        return

    earliest_run = runs[0]["run_id"]
    latest_run = runs[-1]["run_id"]
    earliest_date = runs[0]["computed_at"][:19]
    latest_date = runs[-1]["computed_at"][:19]

    # Get top 10 from latest run
    cur.execute("""
        SELECT skill, opportunity_cost
        FROM skill_gaps
        WHERE run_id = ?
        ORDER BY opportunity_cost DESC
        LIMIT 10
    """, (latest_run,))
    latest_top_10 = {row["skill"]: row["opportunity_cost"] for row in cur.fetchall()}

    # Get top 10 from earliest run
    cur.execute("""
        SELECT skill, opportunity_cost
        FROM skill_gaps
        WHERE run_id = ?
        ORDER BY opportunity_cost DESC
        LIMIT 10
    """, (earliest_run,))
    earliest_top_10 = {row["skill"]: row["opportunity_cost"] for row in cur.fetchall()}

    conn.close()

    # Identify NEW and DROPPED skills
    new_skills = set(latest_top_10.keys()) - set(earliest_top_10.keys())
    dropped_skills = set(earliest_top_10.keys()) - set(latest_top_10.keys())

    # Print header
    print()
    print("═" * 78)
    print(f"  SKILL GAP TRENDS — Comparing {earliest_date} → {latest_date}")
    print("═" * 78)
    print()
    print(f"  {'Skill':<22} {'Earliest':<10} {'Latest':<10} {'Δ':<8} {'% Change':<10} {'Status':<12}")
    print("  " + "─" * 76)

    for i, skill in enumerate(latest_top_10.keys(), 1):
        latest_cost = latest_top_10[skill]
        earliest_cost = earliest_top_10.get(skill)

        if earliest_cost is None:
            status = "★ NEW"
            change_str = "—"
            pct_str = "—"
        else:
            change = latest_cost - earliest_cost
            pct = ((latest_cost - earliest_cost) / earliest_cost * 100) if earliest_cost > 0 else 0
            change_str = f"{change:+.2f}"
            pct_str = f"{pct:+.0f}%"
            status = ""

        print(f"  {i:<2} {skill:<20} "
              f"{earliest_cost if earliest_cost else '—':<10} "
              f"{latest_cost:<10.2f} "
              f"{change_str:<8} "
              f"{pct_str:<10} "
              f"{status:<12}")

    if dropped_skills:
        print()
        print("  DROPPED OUT of top 10 since earliest snapshot:")
        for skill in sorted(dropped_skills):
            old_cost = earliest_top_10[skill]
            print(f"    • {skill} (was {old_cost:.2f})")

    print()
    print("═" * 78)
    print()
    print("  Opportunity Cost = sum(listing_score / 100) — higher = more valuable to acquire")
    print("  ★ NEW = skill appeared in top 10 since earliest snapshot")
    print("  DROPPED = skill was in top 10 earlier but not in latest")
    print()


def migrate_skill_gaps(db_path: str) -> None:
    """Add missing columns to skill_gaps table if they don't exist."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Get existing columns
    cur.execute("PRAGMA table_info(skill_gaps)")
    existing_cols = {row[1] for row in cur.fetchall()}
    
    # Add missing columns
    migrations = {
        "run_id": "INTEGER",
        "computed_at": "TEXT",
        "listings_blocked": "INTEGER",
        "opportunity_cost": "REAL", 
        "mean_score": "REAL",
        "top_score": "INTEGER",
        "example_ids": "TEXT",
    }
    
    for col, col_type in migrations.items():
        if col not in existing_cols:
            try:
                cur.execute(f"ALTER TABLE skill_gaps ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # Already exists in another session
    
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EdgeDash Gap Analysis CLI")
    parser.add_argument("db", nargs="?", default="edgedash.db", help="Database path")
    parser.add_argument("--trend", action="store_true", help="Show trend across all snapshots")

    args = parser.parse_args()

    migrate_skill_gaps(args.db)

    if args.trend:
        print_trend_table(args.db)
    else:
        print_gaps_table(args.db)