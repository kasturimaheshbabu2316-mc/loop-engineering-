"""Tests for EdgeDash Query Tool Registry (Rules 2, 41, 46)."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

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
    _clamp_int,
)
from edgedash import storage


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a fully populated SQLite test database with realistic test data."""
    db_path = tmp_path / "test_edgedash.db"
    storage.init_db(db_path)

    now = datetime.now(timezone.utc)
    t_past_15 = (now - timedelta(days=15)).isoformat()
    t_past_5 = (now - timedelta(days=5)).isoformat()
    t_past_3 = (now - timedelta(days=3)).isoformat()
    t_past_2 = (now - timedelta(days=2)).isoformat()
    t_future = (now + timedelta(days=1)).isoformat()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    desc1 = "Python, SQL, AWS, Kubernetes"
    desc2 = "Python, FastAPI, Docker"
    desc3 = "SQL, dbt, Snowflake"

    import hashlib
    hash1 = hashlib.sha256(desc1.encode("utf-8")).hexdigest()[:16]
    hash2 = hashlib.sha256(desc2.encode("utf-8")).hexdigest()[:16]
    hash3 = hashlib.sha256(desc3.encode("utf-8")).hexdigest()[:16]

    # 1. Insert listings
    listings = [
        ("id1", "Lead Data Engineer", "Acme Corp", "Remote", "http://job1", desc1, "source1", "2026-08-30", t_past_3, 95, "Strong match on core data engineering"),
        ("id2", "Senior Python Dev", "Acme Corp", "Berlin", "http://job2", desc2, "source1", "2026-08-28", t_past_5, 88, "High fit with backend Python"),
        ("id3", "Analytics Engineer", "Beta Tech", "Remote", "http://job3", desc3, "source1", "2026-08-20", t_past_15, 75, "Good fit with dbt gap"),
        ("id4", "Junior Analyst", "Gamma Inc", "Munich", "http://job4", "Excel, Tableau", "source1", "2026-08-10", t_past_15, None, None), # unscored
        ("id5", "Unverified Job", "Future Corp", "Remote", "http://job5", "Python, Rust", "source1", "2026-09-02", t_future, 99, "Fetched after verifier"),
    ]
    cur.executemany(
        """
        INSERT INTO listings (id, title, company, location, url, description, source, posted_at, fetched_at, fit_score, fit_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        listings,
    )

    # 2. Insert extraction cache
    extractions = [
        (hash1, json.dumps({"required_skills": ["python", "sql", "aws", "kubernetes"], "nice_to_have": ["dbt"]}), t_past_3),
        (hash2, json.dumps({"required_skills": ["python", "docker"], "nice_to_have": ["fastapi"]}), t_past_5),
        (hash3, json.dumps({"required_skills": ["sql", "dbt"], "nice_to_have": ["snowflake"]}), t_past_15),
    ]
    cur.executemany(
        """
        INSERT INTO extraction_cache (description_hash, extraction, created_at)
        VALUES (?, ?, ?)
        """,
        extractions,
    )

    # 3. Insert skill_gaps snapshots (2 runs for trend testing)
    run1_time = (now - timedelta(weeks=2)).isoformat()
    run2_time = (now - timedelta(days=3)).isoformat()

    gaps_data = [
        # Run 1 (2 weeks ago)
        ("dbt", 10, run1_time, 1, run1_time, 5, 4.25, 85.0, 95, json.dumps(["id3"])),
        ("kubernetes", 8, run1_time, 1, run1_time, 4, 3.50, 87.5, 95, json.dumps(["id1"])),
        ("legacy_skill", 4, run1_time, 1, run1_time, 2, 1.80, 90.0, 90, json.dumps(["id2"])),

        # Run 2 (3 days ago)
        ("dbt", 15, run2_time, 2, run2_time, 8, 6.80, 85.0, 95, json.dumps(["id3"])),
        ("kubernetes", 12, run2_time, 2, run2_time, 6, 5.70, 95.0, 95, json.dumps(["id1"])),
        ("new_gap", 3, run2_time, 2, run2_time, 3, 2.50, 83.3, 88, json.dumps(["id2"])),
    ]
    cur.executemany(
        """
        INSERT INTO skill_gaps (skill, frequency, last_seen, run_id, computed_at, listings_blocked, opportunity_cost, mean_score, top_score, example_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        gaps_data,
    )

    # 4. Insert cycle_log with a passing Verifier cycle
    cur.execute(
        """
        INSERT INTO cycle_log (agent, started_at, finished_at, records_touched, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Verifier", t_past_2, t_past_2, 4, "ok", "verdict=pass notes=All verification checks passed."),
    )

    conn.commit()
    conn.close()
    return db_path


class TestToolRegistry:
    """Test registry dictionary and @tool decorator."""

    def test_tools_registered(self):
        """All 7 tools must be registered in TOOLS."""
        expected = {
            "companies_hiring",
            "best_matches",
            "top_gaps",
            "gap_detail",
            "trend",
            "listing_count",
            "skill_demand",
        }
        assert expected.issubset(set(TOOLS.keys()))

    def test_tool_metadata_shape(self):
        """Each registered Tool has name, non-empty description, and valid schema."""
        for name, t in TOOLS.items():
            assert isinstance(t, Tool)
            assert t.name == name
            assert len(t.description) > 15  # Unambiguous description
            assert isinstance(t.parameters, dict)
            assert t.parameters.get("type") == "object"
            assert "properties" in t.parameters
            # Test serialization
            schema = t.to_dict()
            assert schema["name"] == name
            assert schema["description"] == t.description

    def test_custom_tool_decorator(self):
        """Test registering a custom tool with @tool decorator."""
        @tool(
            name="custom_test_tool",
            description="A test tool description for router testing.",
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        def my_fn(x: int = 1):
            return {"results": [{"x": x}], "summary": f"x is {x}"}

        assert "custom_test_tool" in TOOLS
        res = my_fn(x=42)
        assert res["results"] == [{"x": 42}]
        assert res["summary"] == "x is 42"


class TestParameterClamping:
    """Test parameter validation and clamping per Rule 41."""

    def test_clamp_int_within_range(self):
        assert _clamp_int(5, min_val=1, max_val=25, default=10) == 5

    def test_clamp_int_lower_bound(self):
        assert _clamp_int(-10, min_val=1, max_val=25, default=10) == 1
        assert _clamp_int(0, min_val=1, max_val=25, default=10) == 1

    def test_clamp_int_upper_bound(self):
        assert _clamp_int(100, min_val=1, max_val=25, default=10) == 25
        assert _clamp_int(999, min_val=1, max_val=90, default=7) == 90
        assert _clamp_int(50, min_val=1, max_val=12, default=3) == 12

    def test_clamp_int_invalid_inputs(self):
        assert _clamp_int(None, min_val=1, max_val=25, default=10) == 10
        assert _clamp_int("not_a_number", min_val=1, max_val=25, default=10) == 10
        assert _clamp_int("15", min_val=1, max_val=25, default=10) == 15


class TestQueryTools:
    """Test query tools functionality, return shapes, and boundary behaviors."""

    def test_companies_hiring(self, test_db: Path):
        """Test companies_hiring returns right shape and respects clamping."""
        res = companies_hiring(days=7, db_path=test_db)
        assert isinstance(res, dict)
        assert "results" in res and "summary" in res
        assert isinstance(res["results"], list)
        assert isinstance(res["summary"], str)

        # Check results content
        companies = {r["company"]: r["count"] for r in res["results"]}
        assert "Acme Corp" in companies
        assert "Beta Tech" not in companies  # posted 15 days ago

        # Test lower bound clamping (days=0 -> clamped to 1)
        res_low = companies_hiring(days=0, db_path=test_db)
        assert "1 days" in res_low["summary"]

        # Test upper bound clamping (days=500 -> clamped to 90)
        res_high = companies_hiring(days=500, db_path=test_db)
        assert "90 days" in res_high["summary"]

    def test_best_matches(self, test_db: Path):
        """Test best_matches returns sorted high scores and respects clamping."""
        res = best_matches(n=10, db_path=test_db)
        assert isinstance(res["results"], list)
        assert len(res["results"]) > 0

        first = res["results"][0]
        assert "score" in first
        assert "title" in first
        assert "company" in first
        assert "reason" in first
        assert first["score"] == 95  # Highest verified score

        # Verify ordering DESC by score
        scores = [r["score"] for r in res["results"]]
        assert scores == sorted(scores, reverse=True)

        # Clamping bounds
        res_min = best_matches(n=-5, db_path=test_db)
        assert len(res_min["results"]) <= 1

        res_max = best_matches(n=100, db_path=test_db)
        assert len(res_max["results"]) <= 25

    def test_top_gaps(self, test_db: Path):
        """Test top_gaps returns gaps ordered by opportunity cost."""
        res = top_gaps(n=5, db_path=test_db)
        assert isinstance(res["results"], list)
        assert len(res["results"]) > 0

        first = res["results"][0]
        assert "skill" in first
        assert "listings_blocked" in first
        assert "opportunity_cost" in first
        assert first["skill"] == "dbt"  # highest cost in run 2

        # Clamping bounds
        assert len(top_gaps(n=0, db_path=test_db)["results"]) <= 1
        assert len(top_gaps(n=50, db_path=test_db)["results"]) <= 25

    def test_gap_detail_known_skill(self, test_db: Path):
        """Test gap_detail returns example listings for known skill."""
        res = gap_detail("kubernetes", db_path=test_db)
        assert isinstance(res["results"], list)
        assert len(res["results"]) > 0
        assert "kubernetes" in res["summary"]

        first_listing = res["results"][0]
        assert first_listing["id"] == "id1"
        assert first_listing["company"] == "Acme Corp"

    def test_gap_detail_canonicalisation(self, test_db: Path):
        """Test gap_detail canonicalises input with aliases."""
        aliases = {"k8s": "kubernetes"}
        res = gap_detail("  K8s (EKS)  ", aliases=aliases, db_path=test_db)
        assert len(res["results"]) > 0
        assert "kubernetes" in res["summary"]

    def test_gap_detail_unknown_skill_returns_empty_not_raises(self, test_db: Path):
        """Unknown skill must return empty list without raising."""
        res = gap_detail("totally_unknown_skill_xyz", db_path=test_db)
        assert res["results"] == []
        assert "No gap" in res["summary"]

    def test_trend(self, test_db: Path):
        """Test trend returns delta and pct_change across snapshots."""
        res = trend(weeks=3, db_path=test_db)
        assert isinstance(res["results"], list)
        assert len(res["results"]) > 0
        assert "Skill gap trends across 2 snapshots" in res["summary"]

        skills_dict = {item["skill"]: item for item in res["results"]}
        # dbt increased from 4.25 to 6.80
        assert skills_dict["dbt"]["status"] == "INCREASING"
        assert skills_dict["dbt"]["delta"] > 0
        # legacy_skill dropped in run 2
        assert skills_dict["legacy_skill"]["status"] == "DROPPED"
        # new_gap appeared in run 2
        assert skills_dict["new_gap"]["status"] == "NEW"

        # Clamping bounds
        res_min = trend(weeks=0, db_path=test_db)
        assert "1 weeks" in res_min["summary"]
        res_max = trend(weeks=100, db_path=test_db)
        assert "12 weeks" in res_max["summary"]

    def test_listing_count(self, test_db: Path):
        """Test listing_count returns totals and newest date."""
        res = listing_count(db_path=test_db)
        assert isinstance(res["results"], list)
        assert len(res["results"]) == 1

        totals = res["results"][0]
        assert "total_listings" in totals
        assert "scored_listings" in totals
        assert "unscored_listings" in totals
        assert "newest_listing_date" in totals
        assert totals["total_listings"] >= 4
        assert totals["unscored_listings"] >= 1

    def test_skill_demand_known_skill(self, test_db: Path):
        """Test skill_demand counts required vs nice_to_have."""
        res = skill_demand("python", db_path=test_db)
        assert len(res["results"]) == 1
        item = res["results"][0]
        assert item["skill"] == "python"
        assert item["required_count"] >= 2
        assert item["total_mentions"] >= 2
        assert "python" in res["summary"]

        # Nice to have check for dbt
        res_dbt = skill_demand("dbt", db_path=test_db)
        item_dbt = res_dbt["results"][0]
        assert item_dbt["required_count"] >= 1
        assert item_dbt["nice_to_have_count"] >= 1

    def test_skill_demand_unknown_skill_returns_empty_not_raises(self, test_db: Path):
        """Unknown skill must return empty list without raising."""
        res = skill_demand("non_existent_skill_404", db_path=test_db)
        assert res["results"] == []
        assert "not found" in res["summary"]


class TestSteeringRules:
    """Test compliance with architecture steering rules."""

    def test_rule_2_no_sqlite3_import_in_tools(self):
        """Rule 2: All reads through storage. No direct sqlite3 import in query.tools."""
        tools_path = Path("edgedash/query/tools.py")
        with open(tools_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "import sqlite3" not in content
        assert "from sqlite3" not in content

    def test_rule_46_last_passing_cycle_isolation(self, test_db: Path):
        """Rule 46: Unverified listing fetched after passing cycle verifier time is excluded."""
        # Listing "id5" was fetched in future (after the passing verifier time)
        res = best_matches(n=10, db_path=test_db)
        returned_ids = [r["id"] for r in res["results"]]
        assert "id5" not in returned_ids
