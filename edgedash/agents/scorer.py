"""Scorer agent for EdgeDash.

Reads unscored listings, extracts facts via LLM (one call per unique
description), computes a deterministic fit score, and writes results back
to storage.

Per steering rules:
- Rule 16: model extracts facts only; all arithmetic is here, never in LLM
- Rule 17: LLMError per listing is caught and logged; cycle continues
- Rule 18: only listings WHERE fit_score IS NULL are processed
- Rule 19: fit_reason is generated from score components by this code
- Rule 20: score distribution logged to cycle_log after every run
- Rule 21: batch size capped at config.scorer_batch_size (default 25)
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.agents.extractor import extract
from edgedash.config import Config
from edgedash.llm import LLMError
from edgedash import skills as skills_module


# ---------------------------------------------------------------------------
# Scoring weights (the model NEVER sees these)
# ---------------------------------------------------------------------------

# Component weights — must sum to 100
_W_SKILLS_REQUIRED = 55   # required skill overlap is the dominant signal
_W_SKILLS_NICE     = 15   # nice-to-have overlap is a bonus
_W_SENIORITY       = 20   # seniority match
_W_EXPERIENCE      = 10   # years-of-experience match

# Seniority match table: (listing_seniority, user_experience_years) -> score 0-100
_SENIORITY_SCORES: dict[str, dict[str, int]] = {
    # How well does each user experience band match each seniority level?
    # Bands: "junior" (<2 yrs), "mid" (2-5), "senior" (5-9), "lead" (9+)
    "junior":  {"junior": 100, "mid": 60, "senior": 20, "lead": 10},
    "mid":     {"junior": 50,  "mid": 100, "senior": 70, "lead": 40},
    "senior":  {"junior": 20,  "mid": 60,  "senior": 100, "lead": 80},
    "lead":    {"junior": 10,  "mid": 30,  "senior": 70,  "lead": 100},
    "unknown": {"junior": 70,  "mid": 70,  "senior": 70,  "lead": 70},
}


def _experience_band(years: int) -> str:
    """Bucket years of experience into a seniority band."""
    if years < 2:
        return "junior"
    if years < 5:
        return "mid"
    if years < 9:
        return "senior"
    return "lead"


def _skill_overlap(
    user_skills: list[str],
    listing_skills: list[str],
    aliases: dict[str, str],
) -> float:
    """Return fraction of listing_skills the user has (0.0 – 1.0)."""
    if not listing_skills:
        return 1.0  # No requirements stated → full credit

    user_canonical = {skills_module.canonical(s, aliases) for s in user_skills}
    required_canonical = [skills_module.canonical(s, aliases) for s in listing_skills]
    required_canonical = [s for s in required_canonical if s]  # drop empty

    if not required_canonical:
        return 1.0

    matched = sum(1 for s in required_canonical if s in user_canonical)
    return matched / len(required_canonical)


def _years_match(user_years: int, required_years: int | None) -> float:
    """Score how well user years match required years (0.0 – 1.0)."""
    if required_years is None:
        return 1.0  # Not stated → full credit

    if user_years >= required_years:
        return 1.0

    # Partial credit: proportional, minimum 0.1
    return max(0.1, user_years / required_years)


def compute_score(
    extraction: dict[str, Any],
    config: Config,
) -> tuple[int, str]:
    """Compute deterministic fit score from extraction and user profile.

    Per rule 16: this function owns ALL scoring arithmetic.
    Per rule 19: reason is generated from score components by this code.

    Args:
        extraction: Output of extractor.extract() — structured facts only.
        config: User profile with my_skills and experience_years.

    Returns:
        Tuple of (score: int 0-100, reason: str).
    """
    aliases = config.skill_aliases

    # Component 1: required skill overlap
    required_overlap = _skill_overlap(
        config.my_skills,
        extraction.get("required_skills", []),
        aliases,
    )
    c_required = required_overlap * _W_SKILLS_REQUIRED

    # Component 2: nice-to-have overlap
    nice_overlap = _skill_overlap(
        config.my_skills,
        extraction.get("nice_to_have", []),
        aliases,
    )
    c_nice = nice_overlap * _W_SKILLS_NICE

    # Component 3: seniority match
    seniority = extraction.get("seniority", "unknown") or "unknown"
    user_band = _experience_band(config.experience_years)
    seniority_row = _SENIORITY_SCORES.get(seniority, _SENIORITY_SCORES["unknown"])
    c_seniority = seniority_row.get(user_band, 70) / 100 * _W_SENIORITY

    # Component 4: years-of-experience match
    years_frac = _years_match(config.experience_years, extraction.get("years_required"))
    c_experience = years_frac * _W_EXPERIENCE

    raw_score = c_required + c_nice + c_seniority + c_experience
    score = max(0, min(100, round(raw_score)))

    # Build human-readable reason from components (rule 19)
    req_count = len(extraction.get("required_skills", []))
    nice_count = len(extraction.get("nice_to_have", []))
    matched_req = round(required_overlap * req_count)
    matched_nice = round(nice_overlap * nice_count)
    years_req = extraction.get("years_required")
    years_str = f"{config.experience_years}/{years_req}yr" if years_req else "years not stated"

    reason = (
        f"skills {matched_req}/{req_count} required"
        + (f", {matched_nice}/{nice_count} nice-to-have" if nice_count else "")
        + f"; seniority={seniority} (user={user_band})"
        + f"; exp={years_str}"
        + f" → {score}/100"
    )

    return score, reason


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Scorer:
    """Scores unscored listings by extracting facts then computing fit.

    Stop condition: all unscored listings in the current batch are processed.
    """

    name: str = "Scorer"

    def run(
        self,
        config: Config,
        db_path: str,
        goal: str,
        stop_conditions: dict,
    ) -> AgentResult:
        """Score unscored listings, respecting stop_conditions.

        Args:
            config: User configuration.
            db_path: Path to SQLite database.
            goal: What to accomplish (from plan).
            stop_conditions: Limits to respect (max_items, max_seconds).

        Returns:
            AgentResult with scored count and distribution stats.
        """
        # Use stop_conditions from orchestrator, fallback to config
        batch_size = stop_conditions.get("max_items", getattr(config, "scorer_batch_size", 25))
        listings = storage.get_unscored_listings(db_path, limit=batch_size)

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="No unscored listings.",
            )

        scored: list[int] = []
        failed = 0
        skipped = 0

        for listing in listings:
            listing_id = listing["id"]
            try:
                extraction = extract(listing, db_path, config)
                score, reason = compute_score(extraction, config)
                storage.update_listing_score(db_path, listing_id, score, reason)
                scored.append(score)

            except LLMError as exc:
                # Rule 17: per-listing failure must not crash the cycle
                failed += 1
                storage.log_cycle(
                    db_path=db_path,
                    agent=self.name,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    records_touched=0,
                    status="failed",
                    notes=f"LLM error for listing {listing_id}: {exc}",
                )

            except Exception as exc:
                failed += 1
                storage.log_cycle(
                    db_path=db_path,
                    agent=self.name,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    records_touched=0,
                    status="failed",
                    notes=f"Unexpected error for listing {listing_id}: {exc}",
                )

        # Build distribution notes (rule 20)
        notes = _distribution_notes(scored, failed, len(listings))

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=len(scored),
            notes=notes,
        )


def _distribution_notes(scored: list[int], failed: int, total: int) -> str:
    """Build score distribution summary for cycle_log (rule 20)."""
    if not scored:
        return f"0 scored, {failed} failed of {total} attempted"

    mn = min(scored)
    mx = max(scored)
    mean = statistics.mean(scored)
    spread = mx - mn

    suspect = " ⚠ SUSPECT RUN (spread ≤10)" if spread <= 10 else ""
    failed_str = f", {failed} failed" if failed else ""

    return (
        f"{len(scored)} scored{failed_str} · "
        f"min={mn} max={mx} mean={mean:.1f} spread={spread}"
        + suspect
    )
