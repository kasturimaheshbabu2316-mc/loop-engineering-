"""Tests for EdgeDash Two-Call Query Pipeline (Rules 42-45)."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from edgedash.config import Config
from edgedash.query.ask import Answer, ask, _build_routing_prompt, _build_phrasing_prompt
from edgedash.query.tools import TOOLS
from edgedash import storage


@pytest.fixture
def mock_config() -> Config:
    """Mock configuration object."""
    return Config(
        target_role="Data Engineer",
        target_city="Berlin",
        keywords=["Python", "SQL"],
        my_skills=["Python", "SQL"],
        db_path="edgedash.db",
        llm_provider="gemini",
        llm_model="gemini-flash-latest",
    )


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Create a populated test database with sample verified listings and gaps."""
    db_path = tmp_path / "query_test.db"
    storage.init_db(db_path)

    now = datetime.now(timezone.utc)
    t_past = (now - timedelta(days=2)).isoformat()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Listings
    cur.execute(
        """
        INSERT INTO listings (id, title, company, location, url, description, source, posted_at, fetched_at, fit_score, fit_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("id1", "Senior Data Engineer", "Acme Corp", "Remote", "http://job1", "Python, SQL", "test", "2026-08-30", t_past, 95, "Great fit for Python/SQL"),
    )

    # Extraction cache
    import hashlib
    h = hashlib.sha256("Python, SQL".encode("utf-8")).hexdigest()[:16]
    cur.execute(
        """
        INSERT INTO extraction_cache (description_hash, extraction, created_at)
        VALUES (?, ?, ?)
        """,
        (h, json.dumps({"required_skills": ["python", "sql"], "nice_to_have": ["dbt"]}), t_past),
    )

    # Skill gaps
    cur.execute(
        """
        INSERT INTO skill_gaps (skill, frequency, last_seen, run_id, computed_at, listings_blocked, opportunity_cost, mean_score, top_score, example_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("dbt", 5, t_past, 1, t_past, 5, 4.25, 85.0, 95, json.dumps(["id1"])),
    )

    # Passing verifier cycle
    cur.execute(
        """
        INSERT INTO cycle_log (agent, started_at, finished_at, records_touched, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Verifier", t_past, t_past, 1, "ok", "verdict=pass notes=All verification checks passed."),
    )

    conn.commit()
    conn.close()
    return db_path


class TestPromptGeneration:
    """Test routing and phrasing prompt templates."""

    def test_routing_prompt_contains_all_tools_and_rule_45(self):
        """Routing prompt must list all TOOLS and state Rule 45 explicitly."""
        prompt = _build_routing_prompt("Which companies are hiring?")
        assert "Which companies are hiring?" in prompt
        assert "AVAILABLE TOOLS:" in prompt
        for name in TOOLS:
            assert name in prompt
        # Check rule 45 instructions
        assert "Do NOT attempt to pick the closest tool" in prompt
        assert '"tool": null' in prompt

    def test_phrasing_prompt_contains_rule_43(self):
        """Phrasing prompt must contain Rule 43 constraints against hallucinations."""
        prompt = _build_phrasing_prompt(
            question="What is the top gap?",
            summary="Top 1 gap found",
            rows=[{"skill": "dbt", "opportunity_cost": 4.25}],
        )
        assert "What is the top gap?" in prompt
        assert "Top 1 gap found" in prompt
        assert "dbt" in prompt
        assert "Use ONLY facts and numbers present in the data rows" in prompt
        assert "Do NOT estimate, extrapolate, speculate" in prompt


class TestTwoCallQueryPipeline:
    """Test full execution of ask() across routing, tool execution, and phrasing."""

    def test_successful_query_flow(self, populated_db: Path, mock_config: Config):
        """Test answerable question goes through ROUTE -> EXECUTE -> PHRASE."""
        route_mock_res = {
            "tool": "companies_hiring",
            "params": {"days": 7},
            "confidence": "high",
        }
        phrase_mock_res = {
            "text": "Acme Corp is actively hiring with 1 job listing posted in the last 7 days.",
        }

        with patch("edgedash.llm.complete_json") as mock_complete:
            mock_complete.side_effect = [route_mock_res, phrase_mock_res]

            ans = ask(
                "Which companies are hiring in the last week?",
                config=mock_config,
                db_path=populated_db,
            )

            assert isinstance(ans, Answer)
            assert ans.tool_used == "companies_hiring"
            assert ans.params == {"days": 7}
            assert len(ans.rows) >= 1
            assert ans.rows[0]["company"] == "Acme Corp"
            assert ans.text == "Acme Corp is actively hiring with 1 job listing posted in the last 7 days."
            assert mock_complete.call_count == 2

    def test_unanswerable_question_returns_fixed_message_zero_phrase_call(self, populated_db: Path, mock_config: Config):
        """Rule 45: If tool is null, return fixed message listing tools, with NO phrasing LLM call."""
        route_mock_res = {
            "tool": None,
            "params": {},
            "confidence": "low",
        }

        with patch("edgedash.llm.complete_json") as mock_complete:
            mock_complete.return_value = route_mock_res

            ans = ask(
                "What is the capital of France?",
                config=mock_config,
                db_path=populated_db,
            )

            assert isinstance(ans, Answer)
            assert ans.tool_used is None
            assert ans.rows == []
            assert ans.params == {}
            assert "cannot answer that question" in ans.text
            assert "companies_hiring" in ans.text
            assert "best_matches" in ans.text
            assert "top_gaps" in ans.text
            # Strictly ONE LLM call made (Route only, no Phrasing)
            assert mock_complete.call_count == 1

    def test_hallucinated_tool_raises_hard_error(self, populated_db: Path, mock_config: Config):
        """Hallucinated tool name not in TOOLS must raise ValueError."""
        route_mock_res = {
            "tool": "non_existent_magic_tool",
            "params": {},
            "confidence": "high",
        }

        with patch("edgedash.llm.complete_json") as mock_complete:
            mock_complete.return_value = route_mock_res

            with pytest.raises(ValueError, match="unknown tool 'non_existent_magic_tool'"):
                ask("Run secret analysis", config=mock_config, db_path=populated_db)

    def test_empty_question_handling(self, populated_db: Path, mock_config: Config):
        """Empty question returns friendly prompt without calling LLM."""
        with patch("edgedash.llm.complete_json") as mock_complete:
            ans = ask("   ", config=mock_config, db_path=populated_db)
            assert "Please ask a question" in ans.text
            assert mock_complete.call_count == 0

    def test_query_logging_to_database(self, populated_db: Path, mock_config: Config):
        """Verify query execution is logged to query_log table."""
        route_mock_res = {
            "tool": "top_gaps",
            "params": {"n": 5},
            "confidence": "high",
        }
        phrase_mock_res = {
            "text": "Your top skill gap is dbt with 5 blocked listings and an opportunity cost of 4.25.",
        }

        with patch("edgedash.llm.complete_json") as mock_complete:
            mock_complete.side_effect = [route_mock_res, phrase_mock_res]

            ans = ask("What are my top skill gaps?", config=mock_config, db_path=populated_db)
            assert ans.tool_used == "top_gaps"

        # Check query_log in DB
        queries = storage.get_recent_queries(populated_db, limit=5)
        assert len(queries) >= 1
        latest = queries[0]
        assert latest["question"] == "What are my top skill gaps?"
        assert latest["tool_chosen"] == "top_gaps"
        assert latest["answerable"] == 1
        assert latest["duration_ms"] > 0
        assert "dbt" in latest["answer_text"]

    def test_unanswerable_query_logging(self, populated_db: Path, mock_config: Config):
        """Verify unanswerable queries are logged with answerable=0."""
        route_mock_res = {
            "tool": None,
            "params": {},
            "confidence": "low",
        }

        with patch("edgedash.llm.complete_json") as mock_complete:
            mock_complete.return_value = route_mock_res
            ask("Tell me a bedtime story.", config=mock_config, db_path=populated_db)

        queries = storage.get_recent_queries(populated_db, limit=5)
        latest = queries[0]
        assert latest["question"] == "Tell me a bedtime story."
        assert latest["tool_chosen"] is None
        assert latest["answerable"] == 0


class TestSteeringRules:
    """Test compliance with architecture steering rules."""

    def test_rule_2_no_sqlite3_import_in_ask(self):
        """Rule 2: All reads/writes through storage. No direct sqlite3 import in query.ask."""
        ask_path = Path("edgedash/query/ask.py")
        with open(ask_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "import sqlite3" not in content
        assert "from sqlite3" not in content
