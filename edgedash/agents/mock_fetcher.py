"""MockFetcher — returns fake job listings without any network calls.

Used during development to exercise the full pipeline (storage, dedup,
orchestrator logging) before the real Fetcher is wired in.

Dedup contract: listings 0-3 have stable IDs across every run.
Run twice against the same db and the second run must report 8 new rows,
not 12, proving INSERT OR IGNORE works correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config
from edgedash import storage


# ---------------------------------------------------------------------------
# Static listing templates
# The first 4 entries are intentionally stable (fixed source + url) so that
# the second run of the orchestrator will skip them as duplicates.
# ---------------------------------------------------------------------------

_STABLE: list[dict[str, Any]] = [
    {
        "source": "mock",
        "url": "https://jobs.example.com/stable/001",
        "title": "Data Analyst",
        "company": "Infosys Analytics",
        "location": "Bengaluru, Karnataka",
        "description": (
            "We are looking for a Data Analyst with strong SQL skills and "
            "experience in Tableau. You will build dashboards, write data "
            "pipelines in Python, and present insights to stakeholders."
        ),
        "posted_at": "2026-08-10",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/stable/002",
        "title": "Senior Data Analyst",
        "company": "Flipkart",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Senior role requiring advanced SQL, Python (pandas, numpy), "
            "and experience with Power BI. 4+ years preferred. You will own "
            "end-to-end reporting for the supply-chain team."
        ),
        "posted_at": "2026-08-11",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/stable/003",
        "title": "Business Intelligence Analyst",
        "company": "Wipro Digital",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Build and maintain BI reports using Tableau and Excel. "
            "Collaborate with product and finance teams to surface KPIs. "
            "SQL and basic statistics required."
        ),
        "posted_at": "2026-08-09",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/stable/004",
        "title": "Data Analyst – Marketing",
        "company": "Swiggy",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Support growth analytics using Python and SQL. Own campaign "
            "performance dashboards in Power BI. Experience with A/B testing "
            "and statistical analysis is a strong plus."
        ),
        "posted_at": "2026-08-12",
    },
]

_VARIABLE: list[dict[str, Any]] = [
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/005",
        "title": "Junior Data Analyst",
        "company": "Razorpay",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Entry-level analyst role. Work with the payments data team to "
            "produce daily reports in Excel and SQL. Python scripting is a bonus."
        ),
        "posted_at": "2026-08-13",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/006",
        "title": "Data Analyst – Risk",
        "company": "HDFC Bank Tech",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Risk analytics team needs SQL-savvy analyst to build risk "
            "dashboards. Experience in banking data, Excel modelling, and "
            "Power BI preferred."
        ),
        "posted_at": "2026-08-13",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/007",
        "title": "Product Data Analyst",
        "company": "Zepto",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Work alongside product managers to define and track metrics. "
            "Strong SQL required. Python, Amplitude, and dbt exposure valued."
        ),
        "posted_at": "2026-08-14",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/008",
        "title": "Lead Data Analyst",
        "company": "PhonePe",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Lead a small team of analysts. Drive self-serve analytics via "
            "Tableau. Hands-on with Python for data wrangling; spark experience "
            "is a differentiator."
        ),
        "posted_at": "2026-08-14",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/009",
        "title": "Data Analyst – Operations",
        "company": "Amazon India",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Support operations analytics. SQL, Excel, and Python essential. "
            "QuickSight and Redshift experience preferred. 2-4 years in an "
            "operations or logistics context a plus."
        ),
        "posted_at": "2026-08-15",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/010",
        "title": "Analytics Engineer",
        "company": "Meesho",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Bridge analyst and engineering roles. Build dbt models, write "
            "Python pipelines, and own the analytics data layer. SQL fluency "
            "and experience with Airflow or similar schedulers required."
        ),
        "posted_at": "2026-08-15",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/011",
        "title": "Data Analyst – Finance",
        "company": "Ola Financial Services",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Finance analytics role focused on P&L reporting and forecasting. "
            "Advanced Excel, SQL, and Python. Power BI dashboard ownership. "
            "CA or MBA finance background preferred."
        ),
        "posted_at": "2026-08-16",
    },
    {
        "source": "mock",
        "url": "https://jobs.example.com/run/{ts}/012",
        "title": "Customer Insights Analyst",
        "company": "BigBasket",
        "location": "Bengaluru, Karnataka",
        "description": (
            "Mine customer behaviour data to surface retention and churn "
            "signals. Python (pandas, scikit-learn basics), SQL, and "
            "Tableau required. Exposure to ML pipelines is a bonus."
        ),
        "posted_at": "2026-08-16",
    },
]


class MockFetcher:
    """Returns 12 fake job listings without any network calls.

    Stop condition: always completes after yielding all 12 listings.
    """

    name: str = "MockFetcher"

    def run(
        self,
        config: Config,
        db_path: str,
        goal: str,
        stop_conditions: dict,
    ) -> AgentResult:
        """Build and upsert mock listings.

        The 4 stable listings have fixed source+url, so their IDs are
        deterministic. Re-running against the same db proves dedup:
        only the 8 variable listings will be counted as new.

        Args:
            config: Loaded user configuration (role and city are injected
                    into listing metadata for realism).
            db_path: Path to the SQLite database.
            goal: What to accomplish (ignored for mock).
            stop_conditions: Limits (max_pages, max_listings - ignored for mock).

        Returns:
            AgentResult with the count of NEW rows inserted.
        """
        # Mock fetcher ignores stop_conditions - it always returns 12 listings
        now = datetime.now(timezone.utc).isoformat()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

        rows: list[dict] = []

        for template in _STABLE:
            row = dict(template)
            row["fetched_at"] = now
            rows.append(row)

        for template in _VARIABLE:
            row = dict(template)
            row["url"] = row["url"].format(ts=ts)
            row["fetched_at"] = now
            rows.append(row)

        new_count = storage.upsert_listings(db_path, rows)

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=new_count,
            notes=f"Offered 12 listings, {new_count} were new (rest deduplicated).",
        )