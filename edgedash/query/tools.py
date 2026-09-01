"""Query tool registry for EdgeDash.

Deterministic only — NO LLM calls anywhere in this module.
All queries are parameterized, read-only, and execute through edgedash.storage (Rule 2).
All queries read from the last passing cycle per Rule 46.
All inputs are validated and clamped per Rule 41.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from edgedash import skills, storage


# ---------------------------------------------------------------------------
# Tool Registry & Decorator
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    """Registered query tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., dict[str, Any]]

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.fn(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Format tool metadata for the router model schema."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# Global registry of all available query tools
TOOLS: dict[str, Tool] = {}


def tool(
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Register a function in the TOOLS registry with a JSON-schema parameter spec.

    Args:
        name: Name of the tool. Defaults to function name.
        description: Specific and unambiguous description for the router model.
        parameters: JSON-schema-style dictionary defining tool parameters.
    """
    def decorator(fn: Callable[..., Any]) -> Tool:
        tool_name = name or fn.__name__
        tool_obj = Tool(
            name=tool_name,
            description=description.strip(),
            parameters=parameters or {"type": "object", "properties": {}, "required": []},
            fn=fn,
        )
        TOOLS[tool_name] = tool_obj
        return tool_obj

    return decorator


# ---------------------------------------------------------------------------
# Validation & Clamping Helpers (Rule 41)
# ---------------------------------------------------------------------------

def _clamp_int(val: Any, min_val: int, max_val: int, default: int) -> int:
    """Safely coerce and clamp an integer to [min_val, max_val] (Rule 41)."""
    try:
        if val is None:
            return default
        num = int(val)
        return max(min_val, min(max_val, num))
    except (ValueError, TypeError):
        return default


def _canonicalize_and_validate_skill(
    skill: Any,
    db_path: str | Path,
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Canonicalize skill and ensure it exists in the database (Rule 41).

    Never interpolates untrusted strings directly into queries.
    Returns the canonical skill string if present in DB, or None if unknown/absent.
    """
    if not skill or not isinstance(skill, str):
        return None

    canonical_skill = skills.canonical(skill, aliases or {})
    if not canonical_skill:
        return None

    known_skills = storage.get_known_skills(db_path)
    # Check if either the canonical skill or raw skill is recognized in the database
    if canonical_skill in known_skills or skill.strip().lower() in known_skills:
        return canonical_skill

    return None


# ---------------------------------------------------------------------------
# Seven Query Tools (Rules 2, 41, 46)
# ---------------------------------------------------------------------------

@tool(
    name="companies_hiring",
    description=(
        "Find companies actively posting job listings within the last N days, "
        "including listing counts per company. Use when asked which companies "
        "are hiring, most active employers, or recent postings by company."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days in the past to search for postings (clamped between 1 and 90, default 7).",
                "default": 7,
            }
        },
        "required": [],
    },
)
def companies_hiring(
    days: int = 7,
    *,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """Companies with listings posted in the last N days, with counts."""
    clamped_days = _clamp_int(days, min_val=1, max_val=90, default=7)
    verifier_time = storage.get_last_passing_verifier_time(db_path)

    companies, total_listings = storage.get_companies_hiring(
        db_path,
        days=clamped_days,
        verifier_time=verifier_time,
    )

    return {
        "results": companies,
        "summary": f"{total_listings} listings from {len(companies)} companies posted in the last {clamped_days} days",
    }


@tool(
    name="best_matches",
    description=(
        "Retrieve the highest-scoring job listings matching your profile, including "
        "fit score, job title, company name, and reason for the match. Use when asked "
        "for top job matches, highest fit roles, or best job recommendations."
    ),
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of top matching listings to return (clamped between 1 and 25, default 10).",
                "default": 10,
            }
        },
        "required": [],
    },
)
def best_matches(
    n: int = 10,
    *,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """Highest-scoring listings with score, title, company, reason."""
    clamped_n = _clamp_int(n, min_val=1, max_val=25, default=10)
    verifier_time = storage.get_last_passing_verifier_time(db_path)

    matches, total_scored = storage.get_best_matches(
        db_path,
        n=clamped_n,
        verifier_time=verifier_time,
    )

    return {
        "results": matches,
        "summary": f"Top {len(matches)} highest-scoring listings (out of {total_scored} scored listings)",
    }


@tool(
    name="top_gaps",
    description=(
        "Retrieve the highest-impact skill gaps ranked by opportunity cost, along "
        "with the number of job listings blocked by each gap. Use when asked what skills "
        "to learn, highest priority gaps, or what skills are holding back matches."
    ),
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of top skill gaps to return (clamped between 1 and 25, default 5).",
                "default": 5,
            }
        },
        "required": [],
    },
)
def top_gaps(
    n: int = 5,
    *,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """Top skill gaps by opportunity cost, with listings_blocked."""
    clamped_n = _clamp_int(n, min_val=1, max_val=25, default=5)
    verifier_time = storage.get_last_passing_verifier_time(db_path)

    gaps = storage.get_verified_skill_gaps(
        db_path,
        verifier_time=verifier_time,
        limit=clamped_n,
    )

    return {
        "results": gaps,
        "summary": f"Top {len(gaps)} skill gaps by opportunity cost from the latest verified snapshot",
    }


@tool(
    name="gap_detail",
    description=(
        "Retrieve the specific job listings blocked by a single named skill gap "
        "(rule 26 drill-down). Use when asked about a specific skill gap, why a skill "
        "was identified as a gap, or to see example jobs requiring that skill."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The specific skill name to inspect (e.g. 'python', 'dbt', 'kubernetes').",
            }
        },
        "required": ["skill"],
    },
)
def gap_detail(
    skill: str,
    *,
    aliases: dict[str, str] | None = None,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """The listings blocked by one named skill — Rule 26 drill-down."""
    canonical_skill = _canonicalize_and_validate_skill(skill, db_path, aliases)
    if not canonical_skill:
        return {
            "results": [],
            "summary": f"No gap detail or listings found for skill '{skill}'",
        }

    verifier_time = storage.get_last_passing_verifier_time(db_path)
    gap_data, listings = storage.get_gap_detail(
        db_path,
        skill=canonical_skill,
        verifier_time=verifier_time,
    )

    if not gap_data:
        return {
            "results": [],
            "summary": f"No gap snapshot found for skill '{canonical_skill}'",
        }

    cost = gap_data.get("opportunity_cost") or 0.0
    blocked = gap_data.get("listings_blocked") or 0

    return {
        "results": listings,
        "summary": f"{blocked} listings blocked by '{canonical_skill}' (showing {len(listings)} example listings, opportunity cost {cost:.2f})",
    }


@tool(
    name="trend",
    description=(
        "Track how skill gap opportunity costs and blocked listing counts have changed "
        "over the past N weeks across pipeline snapshots. Use when asked about skill gap "
        "trends, how skill demand is evolving over time, or newly emerging gaps."
    ),
    parameters={
        "type": "object",
        "properties": {
            "weeks": {
                "type": "integer",
                "description": "Number of weeks of historical snapshots to compare (clamped between 1 and 12, default 3).",
                "default": 3,
            }
        },
        "required": [],
    },
)
def trend(
    weeks: int = 3,
    *,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """Gap opportunity_cost change over N weeks from the snapshots."""
    clamped_weeks = _clamp_int(weeks, min_val=1, max_val=12, default=3)
    verifier_time = storage.get_last_passing_verifier_time(db_path)

    trend_items, num_snapshots, earliest_date, latest_date = storage.get_gap_trend(
        db_path,
        weeks=clamped_weeks,
        verifier_time=verifier_time,
    )

    if num_snapshots < 2:
        return {
            "results": trend_items,
            "summary": f"Found {num_snapshots} snapshot over the last {clamped_weeks} weeks (need 2+ snapshots for trend comparison)",
        }

    earliest_str = earliest_date[:10] if earliest_date else "start"
    latest_str = latest_date[:10] if latest_date else "now"

    return {
        "results": trend_items,
        "summary": f"Skill gap trends across {num_snapshots} snapshots over the last {clamped_weeks} weeks ({earliest_str} -> {latest_str})",
    }


@tool(
    name="listing_count",
    description=(
        "Get overall totals of job listings: total listings, scored listings, unscored "
        "listings, and newest listing date. Use when asked how many jobs are in the database, "
        "pipeline processing volume, or data collection status."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def listing_count(
    *,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """Totals: listings, scored, unscored, newest listing date."""
    verifier_time = storage.get_last_passing_verifier_time(db_path)
    totals = storage.get_listing_counts_summary(db_path, verifier_time=verifier_time)

    total = totals.get("total_listings", 0)
    scored = totals.get("scored_listings", 0)
    unscored = totals.get("unscored_listings", 0)
    newest = totals.get("newest_listing_date") or "N/A"

    return {
        "results": [totals],
        "summary": f"Total {total} listings ({scored} scored, {unscored} unscored), newest listing date {newest}",
    }


@tool(
    name="skill_demand",
    description=(
        "Calculate how often a specific skill appears as required versus nice-to-have "
        "across job postings. Use when asked how in-demand a skill is, how frequently "
        "employers ask for it, or whether a skill is mandatory versus optional."
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The specific skill name to check demand for (e.g. 'python', 'aws', 'sql').",
            }
        },
        "required": ["skill"],
    },
)
def skill_demand(
    skill: str,
    *,
    aliases: dict[str, str] | None = None,
    db_path: str | Path = "edgedash.db",
) -> dict[str, Any]:
    """How often one skill appears in required vs nice_to_have."""
    canonical_skill = _canonicalize_and_validate_skill(skill, db_path, aliases)
    if not canonical_skill:
        return {
            "results": [],
            "summary": f"Skill '{skill}' not found in database extractions",
        }

    verifier_time = storage.get_last_passing_verifier_time(db_path)
    data = storage.get_skill_demand(
        db_path,
        skill=canonical_skill,
        aliases=aliases,
        verifier_time=verifier_time,
    )

    req = data.get("required_count", 0)
    nth = data.get("nice_to_have_count", 0)
    pct = data.get("pct_of_listings", 0.0)

    return {
        "results": [data],
        "summary": f"Skill '{canonical_skill}' appears in {req} required and {nth} nice-to-have postings ({pct}% of listings)",
    }
