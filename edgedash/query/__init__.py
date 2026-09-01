"""Query tool registry package for EdgeDash."""

from edgedash.query.ask import Answer, ask
from edgedash.query.tools import (
    TOOLS,
    Tool,
    best_matches,
    companies_hiring,
    gap_detail,
    listing_count,
    skill_demand,
    tool,
    top_gaps,
    trend,
)

__all__ = [
    "TOOLS",
    "Tool",
    "tool",
    "ask",
    "Answer",
    "companies_hiring",
    "best_matches",
    "top_gaps",
    "gap_detail",
    "trend",
    "listing_count",
    "skill_demand",
]

