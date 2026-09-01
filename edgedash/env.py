"""Environment variable loader for EdgeDash.

Per steering rule 4: secrets are loaded in exactly one place.
No other module reads os.environ directly.

Call load_env() once at application startup (run_cycle.py).
After that, os.getenv() works for any module that needs a key.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(dotenv_path: str | Path | None = None) -> None:
    """Load environment variables from .env file.

    Args:
        dotenv_path: Path to .env file. Defaults to .env in repo root.
    """
    if dotenv_path is None:
        dotenv_path = Path(".env")
    else:
        dotenv_path = Path(dotenv_path)

    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=dotenv_path, override=True)
        return
    except ImportError:
        pass

    # Standard library fallback (Rule 1: stdlib first)
    if dotenv_path.exists():
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    os.environ[k] = v

