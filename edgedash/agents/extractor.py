"""Extraction agent for EdgeDash — extracts facts from job listings via LLM.

Per steering rule 16: the LLM extracts structured facts only. No scoring,
no fit evaluation, no candidate knowledge. The model reads a document.

Per steering rule 18: extraction results are cached by description hash to
avoid repeated API calls for the same job text.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from edgedash import storage
from edgedash.llm import complete_json, LLMError
from edgedash.config import Config


# ---------------------------------------------------------------------------
# Schema for extraction (per steering rule 16: no scoring fields)
# ---------------------------------------------------------------------------

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "required_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Skills the role requires (lowercase)",
        },
        "nice_to_have": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Preferred skills, not required (lowercase)",
        },
        "seniority": {
            "type": "string",
            "enum": ["junior", "mid", "senior", "lead", "unknown"],
            "description": "Seniority level from role title and reqs",
        },
        "years_required": {
            "type": ["integer", "null"],
            "description": "Years required; null if not stated",
        },
        "remote_ok": {
            "type": ["boolean", "null"],
            "description": "Remote acceptable (true), not (false), or unstated (null)",
        },
    },
    "required": [
        "required_skills",
        "nice_to_have",
        "seniority",
        "years_required",
        "remote_ok",
    ],
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _hash_description(text: str | None) -> str:
    """Generate a stable hash of job description text."""
    if not text:
        text = ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_cached_extraction(
    db_path: str, description_hash: str
) -> dict[str, Any] | None:
    """Load cached extraction from storage, or None if not cached."""
    try:
        return storage.get_extraction_cache(db_path, description_hash)
    except Exception:
        # Cache table may not exist yet or other error; skip cache
        return None


def _store_cached_extraction(
    db_path: str, description_hash: str, extraction: dict[str, Any]
) -> None:
    """Store extraction result in cache."""
    try:
        storage.upsert_extraction_cache(db_path, description_hash, extraction)
    except Exception:
        # Cache storage failed; log but don't crash
        pass


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(
    listing: dict[str, Any],
    db_path: str,
    config: Config,
) -> dict[str, Any]:
    """Extract structured facts from a job listing via LLM.

    Per steering rule 18: caches by description hash to avoid repeated
    API calls for the same job text.

    Args:
        listing: Job listing dict with keys: title, company, description, etc.
        db_path: Path to SQLite database (used for cache).
        config: Config object with LLM provider/model.

    Returns:
        Extraction dict matching EXTRACTION_SCHEMA with keys:
        - required_skills: list[str] (lowercase)
        - nice_to_have: list[str] (lowercase)
        - seniority: one of "junior" | "mid" | "senior" | "lead" | "unknown"
        - years_required: int | null
        - remote_ok: bool | null

    Raises:
        LLMError: If the model call fails or response validation fails.
    """
    description = listing.get("description", "")
    description_hash = _hash_description(description)

    # Per steering rule 18: check cache FIRST
    cached = _load_cached_extraction(db_path, description_hash)
    if cached is not None:
        return cached

    # Cache miss: call LLM
    prompt = _build_prompt(listing)

    result = complete_json(
        prompt=prompt,
        schema=EXTRACTION_SCHEMA,
        config=config,
        max_retries=1,
    )

    # Normalise skill names to lowercase
    result["required_skills"] = [s.lower().strip() for s in result.get("required_skills", [])]
    result["nice_to_have"] = [s.lower().strip() for s in result.get("nice_to_have", [])]

    # Store in cache
    _store_cached_extraction(db_path, description_hash, result)

    return result


def _build_prompt(listing: dict[str, Any]) -> str:
    """Build the extraction prompt per steering rule 16.

    Rule 16: Ask ONLY for what the listing says. Do not infer, guess, or
    evaluate. Do not mention profile, skills, or candidate. The model reads
    a document, nothing more.
    """
    title = listing.get("title", "")
    company = listing.get("company", "")
    description = listing.get("description", "")

    prompt = f"""You are analyzing a job listing document. Extract ONLY factual information that is explicitly stated in the listing. Do not infer, do not guess, do not evaluate.

Rules:
- If the listing does not state something explicitly, use null or an empty list.
- Normalize all skill names to lowercase (e.g., "Postgres" -> "postgres", "Python" -> "python").
- For seniority: use the enum values only (junior/mid/senior/lead/unknown). If unclear, use "unknown".
- For years_required: extract only if the listing explicitly states "X years of experience" or similar. Otherwise use null.
- For remote_ok: use true only if remote/WFH is explicitly mentioned as acceptable. Use false if explicitly not allowed. Use null if not mentioned.
- Do not reference a reader, candidate, or applicant. You are reading a job listing document.
- Do not evaluate fit, suitability, or any aspect of matching to a person.

Job listing:

Title: {title}
Company: {company}

Description:
{description}
"""
    return prompt
