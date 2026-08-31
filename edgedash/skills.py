"""Skill canonicalisation for EdgeDash.

Deterministic only — no LLM, no network. Same input always produces same output.
Normalizes raw skill strings to canonical forms using a user-editable alias map.

Per steering rule 18 (idempotency) and rule 1 (standard library first):
uses only built-in string operations. No third-party deps.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edgedash.config import Config


def canonical(raw: str, aliases: dict[str, str]) -> str:
    """Normalize a raw skill string to canonical form.

    Pure function. Deterministic. No side effects.

    Steps:
    1. Lowercase
    2. Strip leading/trailing whitespace
    3. Drop parenthetical qualifiers: "kubernetes (eks)" -> "kubernetes"
    4. Remove surrounding punctuation (quotes, asterisks, brackets, etc.)
    5. Collapse internal whitespace to single spaces
    6. Apply alias map (case-insensitive lookup, returns canonical value)

    Args:
        raw: Raw skill string from extraction.
        aliases: Dict mapping raw skill names (lowercase) to canonical names.
                Example: {"k8s": "kubernetes", "postgres": "postgresql"}

    Returns:
        Canonical skill name in lowercase. Empty string if input is empty/whitespace.

    Examples:
        canonical("Kubernetes (EKS)", {}) -> "kubernetes"
        canonical("  PostgreSQL  ", {"postgres": "postgresql"}) -> "postgresql"
        canonical("k8s", {"k8s": "kubernetes"}) -> "kubernetes"
        canonical("", {}) -> ""
    """
    if not raw or not isinstance(raw, str):
        return ""

    # Step 1: lowercase
    text = raw.lower()

    # Step 2: strip leading/trailing whitespace
    text = text.strip()

    # Step 3: drop parenthetical qualifiers BEFORE stripping punctuation
    # Match anything in parentheses and remove it, then strip again
    text = re.sub(r'\s*\([^)]*\)\s*', '', text).strip()

    # Step 4: remove surrounding punctuation (quotes, asterisks, brackets, etc.)
    text = text.strip('"\'`*[](){}')

    # Step 5: collapse internal whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Return empty string if nothing left
    if not text:
        return ""

    # Step 6: apply alias map
    return aliases.get(text, text)


# ---------------------------------------------------------------------------
# Audit CLI
# ---------------------------------------------------------------------------

def _extract_skills_from_descriptions(db_path: str) -> list[str]:
    """Fallback: extract skill-like words from raw listing descriptions."""
    import re
    
    # Common tech skills to look for (simple keyword matching)
    common_skills = [
        "python", "sql", "tableau", "power bi", "excel", "pandas", "numpy",
        "javascript", "java", "c++", "c#", "ruby", "go", "rust", "scala",
        "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        "aws", "gcp", "azure", "kubernetes", "docker", "terraform",
        "git", "jenkins", "jira", "confluence",
        "machine learning", "deep learning", "tensorflow", "pytorch",
        "statistics", "analytics", "data visualization", "reporting",
        "etl", "dbt", "airflow", "snowflake", "databricks",
        "hadoop", "spark", "kafka", "rabbitmq",
    ]
    
    conn = __import__("sqlite3").connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    
    cur.execute("SELECT description FROM listings WHERE description IS NOT NULL")
    rows = cur.fetchall()
    conn.close()
    
    found_skills = []
    for row in rows:
        desc = (row["description"] or "").lower()
        for skill in common_skills:
            if skill in desc:
                found_skills.append(skill)
    
    return found_skills


def audit(config: "Config", db_path: str) -> None:
    """Audit extraction cache to find missing aliases.

    Reads all extracted required_skills from the database and prints:
    - 40 most common raw skill strings with counts
    - Their canonical forms
    - Raw strings seen exactly once (likely typos/junk)

    Falls back to scanning raw listing descriptions if no extraction cache exists.

    Args:
        config: Config object with skill_aliases.
        db_path: Path to SQLite database.
    """
    from edgedash import storage

    try:
        conn = __import__("sqlite3").connect(db_path)
        conn.row_factory = __import__("sqlite3").Row
        cur = conn.cursor()

        # Try extraction cache first
        cur.execute("""
            SELECT extraction FROM extraction_cache
        """)

        rows = cur.fetchall()
        conn.close()

        all_skills = []
        source_label = "Extraction cache"
        
        if rows:
            # Collect all skills from extractions
            import json

            for row in rows:
                try:
                    extraction = json.loads(row["extraction"])
                    all_skills.extend(extraction.get("required_skills", []))
                except (json.JSONDecodeError, KeyError):
                    pass
            
            if not all_skills:
                print("No skills found in extraction cache.")
                return
        else:
            # Fallback: scan raw descriptions for skill keywords
            print("No extraction cache found. Scanning raw listings for skill keywords...")
            print()
            all_skills = _extract_skills_from_descriptions(db_path)
            source_label = "Raw listings (preview)"
            
            if not all_skills:
                print("No skills found in listing descriptions.")
                return

        # Count frequencies
        skill_counts = Counter(all_skills)

        # Get top 40
        top_40 = skill_counts.most_common(40)

        print("\n" + "=" * 80)
        print(f"SKILL AUDIT — Top 40 Most Common Skills (from {source_label})")
        print("=" * 80 + "\n")

        for skill, count in top_40:
            canonical_form = canonical(skill, config.skill_aliases)
            status = "✓ ALIASED" if canonical_form != skill else "  (no alias)"
            print(f"{count:3d}x  {skill:35s}  →  {canonical_form:30s}  {status}")

        # Find singleton skills (seen exactly once)
        singletons = [skill for skill, count in skill_counts.items() if count == 1]

        print("\n" + "=" * 80)
        print(f"SUSPECT ENTRIES — Skills seen exactly once ({len(singletons)} total)")
        print("These are often typos, abbreviations, or full sentences mistakenly captured.")
        print("=" * 80 + "\n")

        for skill in sorted(singletons)[:50]:  # Show first 50
            print(f"  {skill}")

        if len(singletons) > 50:
            print(f"\n  ... and {len(singletons) - 50} more")

        print("\n" + "=" * 80)
        print("To improve: Edit skill_aliases in config.yaml, then re-run --audit")
        print("=" * 80)

    except Exception as exc:
        print(f"Error during audit: {exc}")


# ---------------------------------------------------------------------------
# Tests (can be run with: python -m pytest edgedash/skills.py -v)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # Check for --audit flag
    if len(sys.argv) > 1 and sys.argv[1] == "--audit":
        try:
            from edgedash.config import load_config

            config = load_config("config.yaml")
            audit(config, config.db_path)
        except Exception as exc:
            print(f"Audit failed: {exc}", file=sys.stderr)
            sys.exit(1)

    else:
        # Run inline tests
        # Quick manual tests
        test_aliases = {
            "k8s": "kubernetes",
            "postgres": "postgresql",
            "js": "javascript",
            "node": "node",
            "node.js": "node",
            "nodejs": "node",
            "gcp": "gcp",
            "google cloud": "gcp",
            "machine learning": "machine learning",
            "ci/cd": "ci/cd",
            "cicd": "ci/cd",
        }

        tests = [
            # (input, expected)
            ("Kubernetes", "kubernetes"),
            ("kubernetes", "kubernetes"),
            ("KUBERNETES (EKS)", "kubernetes"),
            ("  kubernetes  ", "kubernetes"),
            ("kubernetes (eks)", "kubernetes"),
            ('"kubernetes"', "kubernetes"),
            ("'PostgreSQL'", "postgresql"),
            ("postgres", "postgresql"),
            ("PostgreSQL", "postgresql"),
            ("  PostgreSQL  ", "postgresql"),
            ("k8s", "kubernetes"),
            ("K8S", "kubernetes"),
            ("js", "javascript"),
            ("node.js", "node"),
            ("nodejs", "node"),
            ("Node.JS", "node"),
            ("CI/CD", "ci/cd"),
            ("cicd", "ci/cd"),
            ("machine learning", "machine learning"),
            ("MACHINE LEARNING", "machine learning"),
            ("Python", "python"),
            ("python 3.11", "python 3.11"),
            ("  skill  with   spaces  ", "skill with spaces"),
            ("", ""),
            ("   ", ""),
            ("(unknown)", ""),
            (None, ""),
        ]

        print("Running skill canonicalisation tests...\n")
        passed = 0
        failed = 0

        for raw_input, expected in tests:
            result = canonical(raw_input, test_aliases)
            status = "✓" if result == expected else "✗"

            if result == expected:
                passed += 1
            else:
                failed += 1

            print(f"{status} canonical({repr(raw_input):40s}) -> {repr(result):30s} (expected {repr(expected)})")

        print(f"\n{passed} passed, {failed} failed")
