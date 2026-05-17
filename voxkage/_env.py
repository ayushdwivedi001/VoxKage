"""
voxkage/_env.py — Shared .env loader for all MCP servers and modules.

Loads environment variables from the user's ~/.voxkage/.env file,
with a fallback to the repo root .env for dev mode.

override=True is intentional: the user's ~/.voxkage/.env MUST win over
any stale tokens already in the shell environment. This prevents the
'token from old session' bug where a new bot token is ignored.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


_loaded = False


def load_voxkage_env(force: bool = False) -> None:
    """
    Load .env from the user's VoxKage data directory.
    Falls back to repo-root .env in dev mode.

    override=True ensures ~/.voxkage/.env always wins over any stale
    shell-level env vars (prevents token name mismatch bugs on reinstalls).

    Args:
        force: If True, re-load even if already loaded. Use after writing
               new tokens via plugin setup so they take effect immediately.
    """
    global _loaded
    if _loaded and not force:
        return
    _loaded = True

    # Priority 1: ~/.voxkage/.env
    user_env = Path.home() / ".voxkage" / ".env"
    if user_env.exists():
        load_dotenv(str(user_env), override=True)
        return

    # Priority 2: Repo root .env (dev mode — .env next to pyproject.toml)
    # Walk up from this file's location to find the repo root
    current = Path(__file__).parent
    for _ in range(5):  # max 5 levels up
        candidate = current / ".env"
        if candidate.exists():
            load_dotenv(str(candidate), override=True)
            return
        current = current.parent
