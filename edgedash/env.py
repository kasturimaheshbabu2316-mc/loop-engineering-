"""Environment variable loader for EdgeDash.

Per steering rule 4: secrets are loaded in exactly one place.
No other module reads os.environ directly.

Call load_env() once at application startup (run_cycle.py).
After that, os.getenv() works for any module that needs a key.
"""

from __future__ import annotations

from pathlib import Path


def load_env(dotenv_path: str | Path | None = None) -> None:
    """Load environment variables from .env file.

    Args:
        dotenv_path: Path to .env file. Defaults to .env in repo root.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        # python-dotenv not installed; rely on environment variables already set
        return

    if dotenv_path is None:
        dotenv_path = Path(".env")

    load_dotenv(dotenv_path=Path(dotenv_path), override=False)
