"""Two-call query pipeline for EdgeDash (Rules 42-45).

ROUTE (Call 1) -> EXECUTE (Tool from registry) -> PHRASE (Call 2).
Pure determinism on execution, strict schema enforcement, and zero extrapolation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from edgedash import llm, storage
from edgedash.config import Config
from edgedash.env import load_env
from edgedash.query.tools import TOOLS

load_env()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Answer:
    """Answer object returned by the query pipeline."""

    text: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    tool_used: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {
            "type": ["string", "null"],
            "description": "The exact name of the selected tool, or null if no tool matches.",
        },
        "params": {
            "type": "object",
            "description": "Extracted parameter dictionary for the chosen tool.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "low"],
            "description": "Confidence level in tool selection.",
        },
    },
    "required": ["tool", "params", "confidence"],
}

PHRASER_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "2-3 sentence factual and direct answer based only on the provided rows and summary.",
        }
    },
    "required": ["text"],
}


# ---------------------------------------------------------------------------
# Prompt Builders
# ---------------------------------------------------------------------------

def _build_tools_manifest() -> str:
    """Generate plain text description of all tools and their parameter specs."""
    lines = []
    for name, t in TOOLS.items():
        props = t.parameters.get("properties", {})
        props_str = json.dumps(props) if props else "{}"
        lines.append(f"- {name}: {t.description}\n  Parameters: {props_str}")
    return "\n\n".join(lines)


def _build_routing_prompt(question: str) -> str:
    """Generate the routing prompt (Step 1). Contains ONLY TOOLS registry and question."""
    manifest = _build_tools_manifest()
    return f"""You are a precise query router for a job market intelligence database.
Your task is to choose the exact tool from the registry below that answers the user's question, and extract its parameters.

AVAILABLE TOOLS:
{manifest}

ROUTING INSTRUCTIONS (Rule 45):
1. If the question matches one of the available tools, return that tool's exact name in "tool", extract its parameters in "params", and set "confidence" to "high" (or "low" if ambiguous).
2. CRITICAL RULE (Rule 45): If the user's question CANNOT be directly answered by any available tool, you MUST return "tool": null and "params": {{}}. Do NOT attempt to pick the closest tool. Do NOT guess or invent tools.

USER QUESTION:
{question}"""


def _build_unanswerable_text() -> str:
    """Generate fixed message listing available tool descriptions when tool is null."""
    lines = [
        "I cannot answer that question with the available query tools.",
        "",
        "Here are the questions I can answer based on your verified data:",
    ]
    for name, tool_obj in TOOLS.items():
        lines.append(f"• **{name}**: {tool_obj.description}")
    return "\n".join(lines)


def _build_phrasing_prompt(question: str, summary: str, rows: list[dict[str, Any]]) -> str:
    """Generate the phrasing prompt (Step 3). Enforces Rule 43 (no outside extrapolation)."""
    rows_json = json.dumps(rows, indent=2, default=str)
    return f"""You are answering a user question about job market data.
Write a clear, concise 2-3 sentence answer based ONLY on the data rows and summary provided below.

QUESTION:
{question}

DATA SUMMARY:
{summary}

DATA ROWS:
{rows_json}

RULES (Rule 43):
1. Use ONLY facts and numbers present in the data rows and summary above.
2. Do NOT estimate, extrapolate, speculate, or add outside context or real-world assumptions.
3. If the data rows are empty or no records are found, state clearly that the data does not contain an answer.
4. Keep the response to 2-3 concise, factual sentences."""


# ---------------------------------------------------------------------------
# Public Query Pipeline
# ---------------------------------------------------------------------------

def ask(
    question: str,
    *,
    config: Config | None = None,
    db_path: str | Path = "edgedash.db",
) -> Answer:
    """Execute the two-call query pipeline per Rules 42-45.

    Args:
        question: User natural language question.
        config: EdgeDash Config object (for LLM provider and model settings).
        db_path: Path to the SQLite database file.

    Returns:
        Answer object with .text, .rows, .tool_used, and .params.
    """
    start_time = time.perf_counter()
    question = question.strip() if question else ""
    if not question:
        return Answer(
            text="Please ask a question about your job market data.",
            rows=[],
            tool_used=None,
            params={},
        )

    # -----------------------------------------------------------------------
    # 1. ROUTE (Rule 42 / Rule 45)
    # -----------------------------------------------------------------------
    route_prompt = _build_routing_prompt(question)
    route_res = llm.complete_json(route_prompt, ROUTER_SCHEMA, config=config)

    tool_name = route_res.get("tool")
    params = route_res.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    # -----------------------------------------------------------------------
    # 2. Check for null / unanswerable (Rule 45)
    # -----------------------------------------------------------------------
    if tool_name is None:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        text = _build_unanswerable_text()
        storage.log_query(
            db_path=db_path,
            question=question,
            tool_chosen=None,
            params={},
            answerable=False,
            duration_ms=duration_ms,
            answer_text=text,
        )
        return Answer(
            text=text,
            rows=[],
            tool_used=None,
            params={},
        )

    # Hard error on unknown / hallucinated tool name
    if tool_name not in TOOLS:
        raise ValueError(f"Router selected unknown tool '{tool_name}' not present in TOOLS registry.")

    # -----------------------------------------------------------------------
    # 3. EXECUTE tool (Rule 41 validation & clamping inside tools)
    # -----------------------------------------------------------------------
    tool_obj = TOOLS[tool_name]
    tool_output = tool_obj(**params, db_path=db_path)
    rows = tool_output.get("results", [])
    summary = tool_output.get("summary", "")

    # -----------------------------------------------------------------------
    # 4. PHRASE (Rule 43)
    # -----------------------------------------------------------------------
    phrase_prompt = _build_phrasing_prompt(question, summary, rows)
    phrase_res = llm.complete_json(phrase_prompt, PHRASER_SCHEMA, config=config)
    answer_text = phrase_res.get("text", summary)

    duration_ms = (time.perf_counter() - start_time) * 1000.0
    storage.log_query(
        db_path=db_path,
        question=question,
        tool_chosen=tool_name,
        params=params,
        answerable=True,
        duration_ms=duration_ms,
        answer_text=answer_text,
    )

    return Answer(
        text=answer_text,
        rows=rows,
        tool_used=tool_name,
        params=params,
    )
