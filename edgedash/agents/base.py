"""Base agent contract for EdgeDash.

Every agent must implement the Agent protocol. Results are returned as
AgentResult dataclasses so the orchestrator can handle them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from edgedash.config import Config


@dataclass
class AgentResult:
    """Outcome of a single agent run.

    Attributes:
        agent: Name of the agent that produced this result.
        status: "ok" if the run completed successfully, "failed" otherwise.
        records_touched: Number of records read, written, or updated.
        notes: Optional free-text detail (error message, skip reason, etc.).
    """

    agent: str
    status: str          # "ok" | "failed"
    records_touched: int
    notes: str | None = None


@runtime_checkable
class Agent(Protocol):
    """Protocol that every EdgeDash agent must satisfy.

    An agent has exactly one goal and one stop condition.
    The orchestrator calls run() and receives an AgentResult.
    Agents must not import from each other; all coordination goes
    through the orchestrator and the storage interface.
    """

    name: str

    def run(
        self,
        config: "Config",
        db_path: str,
        goal: str,
        stop_conditions: dict,
    ) -> AgentResult:
        """Execute the agent's single responsibility.

        Args:
            config: Loaded user configuration.
            db_path: Path to the SQLite database (passed through from config).
            goal: What the agent should accomplish (from plan).
            stop_conditions: Limits the agent must respect (max_items, max_seconds, etc.).

        Returns:
            AgentResult describing what happened.
        """
        ...