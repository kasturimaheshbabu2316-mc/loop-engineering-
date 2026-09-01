"""Configuration loader for EdgeDash.

All user-specific values live here. No secrets, no hardcoded profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Standard library first — PyYAML is the one dependency worth adding
# because hand-parsing YAML is brittle and TOML doesn't handle lists as cleanly.
# Install with: pip install pyyaml
import yaml


@dataclass
class Config:
    """User profile and runtime settings for EdgeDash.

    Attributes:
        target_role: Job title to search for.
        target_city: Location filter for listings.
        keywords: Terms to match in job descriptions.
        my_skills: Skills the user currently has.
        experience_years: Years of relevant experience.
        db_path: Path to the SQLite database file.
        min_fit_score: Minimum score (0-100) for a listing to be surfaced.
        sources: List of source names to enable (e.g. ["arbeitnow"]).
        use_mock_fetcher: If True, use MockFetcher instead of real Fetcher (offline dev).
        llm_provider: LLM provider name ("gemini" or "ollama").
        llm_model: Model identifier for the chosen provider.
        skill_aliases: Dict mapping raw skill names to canonical forms.
    """

    target_role: str
    target_city: str
    keywords: list[str] = field(default_factory=list)
    my_skills: list[str] = field(default_factory=list)
    experience_years: int = 0
    db_path: str = "edgedash.db"
    min_fit_score: int = 50
    sources: list[str] = field(default_factory=lambda: ["arbeitnow"])
    use_mock_fetcher: bool = False
    llm_provider: str = "gemini"
    llm_model: str = "gemini-flash-latest"
    skill_aliases: dict[str, str] = field(default_factory=dict)
    min_score_spread: int = 10
    min_score_stdev: float = 5.0
    max_empty_extraction_pct: float = 20.0
    max_skills_per_listing: int = 20
    min_gap_sample: int = 3
    max_data_age_days: int = 3


def _coerce_list(value: Any) -> list[str]:
    """Ensure the value is a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a YAML file.

    Args:
        path: Path to config.yaml. Defaults to "config.yaml" in repo root.

    Returns:
        Config object with user profile and settings.

    Raises:
        FileNotFoundError: If config.yaml does not exist.
        ValueError: If required fields are missing.
    """
    if path is None:
        path = Path("config.yaml")
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path.resolve()}\n"
            "Create config.yaml with target_role, target_city, and other fields."
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Required fields — fail fast with clear message
    if "target_role" not in raw:
        raise ValueError("Missing required field in config: target_role")
    if "target_city" not in raw:
        raise ValueError("Missing required field in config: target_city")

    return Config(
        target_role=str(raw["target_role"]),
        target_city=str(raw["target_city"]),
        keywords=_coerce_list(raw.get("keywords")),
        my_skills=_coerce_list(raw.get("my_skills")),
        experience_years=int(raw.get("experience_years", 0)),
        db_path=str(raw.get("db_path", "edgedash.db")),
        min_fit_score=int(raw.get("min_fit_score", 50)),
        sources=_coerce_list(raw.get("sources", ["arbeitnow"])),
        use_mock_fetcher=bool(raw.get("use_mock_fetcher", False)),
        llm_provider=str(raw.get("llm_provider", "gemini")),
        llm_model=str(raw.get("llm_model", "gemini-1.5-flash")),
        skill_aliases=dict(raw.get("skill_aliases", {})),
        min_score_spread=int(raw.get("min_score_spread", 10)),
        min_score_stdev=float(raw.get("min_score_stdev", 5.0)),
        max_empty_extraction_pct=float(raw.get("max_empty_extraction_pct", 20.0)),
        max_skills_per_listing=int(raw.get("max_skills_per_listing", 20)),
        min_gap_sample=int(raw.get("min_gap_sample", 3)),
        max_data_age_days=int(raw.get("max_data_age_days", 3)),
    )


def save_config(config: Config, path: str | Path | None = None) -> None:
    """Save user configuration back to YAML file.

    Args:
        config: Config object to persist.
        path: Path to config.yaml. Defaults to "config.yaml".
    """
    if path is None:
        path = Path("config.yaml")
    else:
        path = Path(path)

    raw: dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception:
            raw = {}

    raw["target_role"] = config.target_role
    raw["target_city"] = config.target_city
    raw["keywords"] = config.keywords
    raw["my_skills"] = config.my_skills
    raw["experience_years"] = config.experience_years
    raw["db_path"] = config.db_path
    raw["min_fit_score"] = config.min_fit_score
    raw["sources"] = config.sources
    raw["use_mock_fetcher"] = config.use_mock_fetcher
    raw["llm_provider"] = config.llm_provider
    raw["llm_model"] = config.llm_model
    if config.skill_aliases:
        raw["skill_aliases"] = config.skill_aliases

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False, default_flow_style=False)

