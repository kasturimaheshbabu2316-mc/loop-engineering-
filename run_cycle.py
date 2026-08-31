"""EdgeDash entry point.

Run one full pipeline cycle:
    python run_cycle.py

Reads config.yaml from the current directory.
"""

from edgedash.config import load_config
from edgedash.env import load_env
from edgedash.orchestrator import run_cycle


def main() -> None:
    load_env()          # load .env once, before anything reads os.environ
    config = load_config("config.yaml")
    run_cycle(config)


if __name__ == "__main__":
    main()
