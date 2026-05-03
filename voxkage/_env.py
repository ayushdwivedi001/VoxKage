"""
voxkage/_env.py — Shared .env loader for all MCP servers and modules.

Loads environment variables from the user's ~/.voxkage/.env file,
with a fallback to the repo root .env for dev mode.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


_loaded = False


def load_voxkage_env() -> None:
    """
    Load .env from the user's VoxKage data directory.
    Falls back to repo-root .env in dev mode.
    Called once — subsequent calls are no-ops.
    """
    global _loaded
    if _loaded:
        return
    _loaded = True

    # Priority 1: ~/.voxkage/.env
    user_env = Path.home() / ".voxkage" / ".env"
    if user_env.exists():
        load_dotenv(str(user_env), override=False)
        return

    # Priority 2: Repo root .env (dev mode — .env next to pyproject.toml)
    # Walk up from this file's location to find the repo root
    current = Path(__file__).parent
    for _ in range(5):  # max 5 levels up
        candidate = current / ".env"
        if candidate.exists():
            load_dotenv(str(candidate), override=False)
            return
        current = current.parent
