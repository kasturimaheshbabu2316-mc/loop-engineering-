"""GapAnalyzer — identifies skill gaps from scored listings.

Deterministic — no LLM calls. Analyzes already-scored listings to find
skills the user is missing that block high-scoring roles.

Per steering rules:
- Rule 18: Only analyze scored listings (WHERE fit_score IS NOT NULL)
- Rule 19: Reason is generated from score components by code
- Rule 25: Write timestamped snapshot to skill_gaps table
- Rule 26: Include up to 5 example listing IDs, highest score first
- Rule 27: Flag gaps computed from <3 listings as "low confidence"
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config
from edgedash import skills
from edgedash import storage


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Gap:
    """A skill gap analysis result."""
    skill: str
    listings_blocked: int
    opportunity_cost: float
    mean_score: float
    top_score: int
    example_ids: list[str]
    also_nice_to_have: int
    is_low_confidence: bool


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

def _load_scored_listings_with_extractions(
    db_path: str,
) -> list[dict]:
    """Load all scored listings with their extraction data."""
    from edgedash.storage import _connect

    conn = _connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()

    # Get scored listings (rule 18: WHERE fit_score IS NOT NULL)
    cur.execute("""
        SELECT id, title, fit_score, description
        FROM listings
        WHERE fit_score IS NOT NULL
        ORDER BY fit_score DESC
    """)

    listings = [dict(row) for row in cur.fetchall()]

    # Get extraction cache data
    cur.execute("""
        SELECT description_hash, extraction
        FROM extraction_cache
    """)

    extraction_map = {}
    for row in cur.fetchall():
        try:
            extraction_map[row["description_hash"]] = json.loads(row["extraction"])
        except (json.JSONDecodeError, KeyError):
            pass

    conn.close()

    # Match listings to their extractions via description hash
    import hashlib
    result = []
    for listing in listings:
        desc = listing.get("description", "")
        desc_hash = hashlib.sha256(desc.encode("utf-8")).hexdigest()[:16] if desc else ""
        extraction = extraction_map.get(desc_hash, {})
        listing["extraction"] = extraction
        result.append(listing)

    return result


def _compute_gaps(
    listings: list[dict],
    user_skills: list[str],
    skill_aliases: dict[str, str],
) -> list[Gap]:
    """Compute skill gaps from scored listings.

    Args:
        listings: List of scored listings with extraction data.
        user_skills: User's current skills (from config).
        skill_aliases: Skill alias map (from config).

    Returns:
        List of Gap objects sorted by opportunity_cost descending.
    """
    # Canonicalize user skills once
    user_skills_canonical = {
        skills.canonical(s, skill_aliases) for s in user_skills
    }

    # Track gaps per skill
    gap_data: dict[str, dict] = defaultdict(lambda: {
        "listings": [],
        "nice_to_have_count": 0,
    })

    for listing in listings:
        score = listing.get("fit_score", 0) or 0
        extraction = listing.get("extraction", {})
        listing_id = listing.get("id", "")

        # Check required_skills
        required = extraction.get("required_skills", [])
        for raw_skill in required:
            canonical_skill = skills.canonical(raw_skill, skill_aliases)
            if not canonical_skill:
                continue

            if canonical_skill not in user_skills_canonical:
                # Missing required skill = gap
                gap_data[canonical_skill]["listings"].append({
                    "id": listing_id,
                    "score": score,
                })

        # Check nice_to_have separately (rule 26)
        nice_to_have = extraction.get("nice_to_have", [])
        for raw_skill in nice_to_have:
            canonical_skill = skills.canonical(raw_skill, skill_aliases)
            if not canonical_skill:
                continue

            if canonical_skill not in user_skills_canonical:
                # Track separately, don't mix with required
                gap_data[canonical_skill]["nice_to_have_count"] += 1

    # Compute Gap objects
    gaps = []
    for skill, data in gap_data.items():
        blocked_listings = data["listings"]
        listings_blocked = len(blocked_listings)

        if listings_blocked == 0:
            continue

        # Sort by score descending
        sorted_listings = sorted(blocked_listings, key=lambda x: x["score"], reverse=True)

        # Opportunity cost: sum of (score / 100)
        opportunity_cost = sum(l["score"] / 100 for l in sorted_listings)

        # Mean score
        mean_score = sum(l["score"] for l in sorted_listings) / listings_blocked

        # Top score
        top_score = sorted_listings[0]["score"] if sorted_listings else 0

        # Example IDs (top 5)
        example_ids = [l["id"] for l in sorted_listings[:5]]

        # Low confidence flag (rule 27)
        is_low_confidence = listings_blocked < 3

        gaps.append(Gap(
            skill=skill,
            listings_blocked=listings_blocked,
            opportunity_cost=opportunity_cost,
            mean_score=mean_score,
            top_score=top_score,
            example_ids=example_ids,
            also_nice_to_have=data["nice_to_have_count"],
            is_low_confidence=is_low_confidence,
        ))

    # Rank by opportunity_cost descending (rule 24)
    gaps.sort(key=lambda g: g.opportunity_cost, reverse=True)

    return gaps


def _write_snapshot(
    db_path: str,
    gaps: list[Gap],
    run_id: int,
) -> None:
    """Write gap snapshot to skill_gaps table (rule 25)."""
    from datetime import datetime, timezone
    from edgedash.storage import _connect

    computed_at = datetime.now(timezone.utc).isoformat()

    # Ensure table exists with new columns
    conn = _connect(db_path)
    try:
        cur = conn.cursor()

        # Add new columns if they don't exist (migration-safe)
        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN run_id INTEGER
            """)
        except __import__("sqlite3").OperationalError:
            pass  # Column already exists

        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN computed_at TEXT
            """)
        except __import__("sqlite3").OperationalError:
            pass

        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN listings_blocked INTEGER
            """)
        except __import__("sqlite3").OperationalError:
            pass

        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN opportunity_cost REAL
            """)
        except __import__("sqlite3").OperationalError:
            pass

        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN mean_score REAL
            """)
        except __import__("sqlite3").OperationalError:
            pass

        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN top_score INTEGER
            """)
        except __import__("sqlite3").OperationalError:
            pass

        try:
            cur.execute("""
                ALTER TABLE skill_gaps ADD COLUMN example_ids TEXT
            """)
        except __import__("sqlite3").OperationalError:
            pass

        # Write new snapshot rows (never overwrite previous runs)
        for gap in gaps:
            cur.execute("""
                INSERT INTO skill_gaps
                    (skill, frequency, last_seen, run_id, computed_at,
                     listings_blocked, opportunity_cost, mean_score, top_score, example_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                gap.skill,
                gap.listings_blocked,  # Reuse frequency column for listings_blocked
                computed_at,
                run_id,
                computed_at,
                gap.listings_blocked,
                gap.opportunity_cost,
                gap.mean_score,
                gap.top_score,
                json.dumps(gap.example_ids),
            ))

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------

class GapAnalyzer:
    """Analyzes skill gaps from scored listings.

    Stop condition: all scored listings have been analyzed.
    """

    name: str = "GapAnalyzer"

    def run(
        self,
        config: Config,
        db_path: str,
        goal: str,
        stop_conditions: dict,
    ) -> AgentResult:
        """Run gap analysis.

        Args:
            config: User configuration with my_skills and skill_aliases.
            db_path: Path to SQLite database.
            goal: What to accomplish (from plan).
            stop_conditions: Limits to respect (max_seconds - currently unused).

        Returns:
            AgentResult with gap analysis summary.
        """
        # GapAnalyzer currently ignores stop_conditions (no iteration limit)
        # Load scored listings (rule 18: only scored)
        listings = _load_scored_listings_with_extractions(db_path)

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="No scored listings found. Run Scorer first.",
            )

        # Compute gaps
        gaps = _compute_gaps(listings, config.my_skills, config.skill_aliases)

        # Get run_id for snapshot
        from edgedash.storage import _connect
        conn = _connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT MAX(id) FROM cycle_log")
        run_id = (cur.fetchone()[0] or 0) + 1
        conn.close()

        # Write snapshot (rule 25)
        _write_snapshot(db_path, gaps, run_id)

        # Build notes (rule 19: human-readable reason)
        top_10 = gaps[:10]
        top_skill = top_10[0] if top_10 else None

        if top_skill:
            notes = (
                f"{len(gaps)} gaps · top: {top_skill.skill} "
                f"({top_skill.listings_blocked} listings, cost {top_skill.opportunity_cost:.1f}) · "
                f"{len(listings)} listings analysed"
            )
        else:
            notes = f"0 gaps · {len(listings)} listings analysed"

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=len(listings),
            notes=notes,
        )


# ---------------------------------------------------------------------------
# CLI for viewing gaps
# ---------------------------------------------------------------------------

def print_gaps_table(db_path: str = "edgedash.db") -> None:
    """Print latest gap snapshot as readable table."""
    from datetime import datetime

    conn = __import__("sqlite3").connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()

    # Get latest run
    cur.execute("""
        SELECT MAX(run_id) as latest_run
        FROM skill_gaps
        WHERE run_id IS NOT NULL
    """)

    latest_run = cur.fetchone()["latest_run"]

    if not latest_run:
        print("No gap analysis runs found. Run the cycle with GapAnalyzer first.")
        return

    # Get gaps from latest run
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
        print(f"No gaps found in run {latest_run}.")
        return

    # Print header
    print()
    print(f"╔{'═' * 78}╗")
    print(f"║  SKILL GAPS — Run #{latest_run} — {gaps[0]['computed_at'][:19]}")
    print(f"╠{'═' * 78}╣")
    print(f"║ {'Rank':<5} {'Skill':<20} {'Blocked':<8} {'Cost':<8} {'Mean':<6} {'Top':<5} {'Confidence':<12}║")
    print(f"╟{'─' * 78}╢")

    for i, gap in enumerate(gaps, 1):
        confidence = "⚠ LOW" if gap["listings_blocked"] < 3 else "✓ OK"
        bar_len = min(int(gap["opportunity_cost"] * 2), 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)

        print(f"║ {i:<5} {gap['skill']:<20} {gap['listings_blocked']:<8} "
              f"{gap['opportunity_cost']:<8.2f} {gap['mean_score']:<6.1f} "
              f"{gap['top_score']:<5} {confidence:<12}║")
        print(f"║       {'Cost bar: ' + bar:<66}║")

    print(f"╚{'═' * 78}╝")
    print()
    print("Cost = sum(listing_score / 100) — higher = more valuable to acquire")
    print("Blocked = # of high-scoring listings requiring this skill")


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "edgedash.db"
    print_gaps_table(db)