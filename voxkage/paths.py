"""
voxkage/paths.py — Single source of truth for ALL VoxKage paths.

Every file in the project imports from here instead of hardcoding paths.
Resolves dynamically based on user's OS and environment.

Directory layout (per-user):
    ~/.voxkage/                   # User data root
    ├── config.json               # Runtime config
    ├── .env                      # API tokens & secrets
    ├── .gemini/
    │   ├── settings.json         # MCP server config (auto-generated)
    │   └── GEMINI.md             # Agent instructions (auto-generated)
    ├── data/
    │   ├── voxkage_memory/       # SOUL memories (qdrant + history.db)
    │   ├── rag_store/            # ChromaDB RAG index
    │   ├── tool_index.lance/     # Tool RAG index
    │   ├── gmail_token.json      # OAuth tokens
    │   └── credentials.json      # Gmail OAuth credentials
    ├── task_logs/                # Background task logs
    └── telegram_offset.json      # Telegram poll offset
"""

import os
import sys
import shutil
import platform
from pathlib import Path


# ── Platform detection ────────────────────────────────────────────────────────

def is_windows() -> bool:
    return platform.system() == "Windows"

def is_mac() -> bool:
    return platform.system() == "Darwin"

def is_linux() -> bool:
    return platform.system() == "Linux"

def is_supported_platform() -> bool:
    return is_windows() or is_mac()


# ── Core directories ─────────────────────────────────────────────────────────

def home_dir() -> Path:
    """User's home directory (~)."""
    return Path.home()

def voxkage_dir() -> Path:
    """~/.voxkage/ — user data root. Created on first access."""
    d = home_dir() / ".voxkage"
    d.mkdir(parents=True, exist_ok=True)
    return d

def package_dir() -> Path:
    """Where the installed voxkage package code lives (read-only for users)."""
    return Path(__file__).parent

def data_dir() -> Path:
    """~/.voxkage/data/ — memories, RAG, credentials."""
    d = voxkage_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d

def brain_dir() -> Path:
    """~/.voxkage/data/brain/ — coding plans and state."""
    d = data_dir() / "brain"
    d.mkdir(parents=True, exist_ok=True)
    return d

def browser_dir() -> Path:
    """~/.voxkage/data/browser/ — isolated chrome profile."""
    d = data_dir() / "browser"
    d.mkdir(parents=True, exist_ok=True)
    return d

def rag_dir() -> Path:
    """~/.voxkage/data/rag/ — chromadb store."""
    d = data_dir() / "rag"
    d.mkdir(parents=True, exist_ok=True)
    return d

def output_dir() -> Path:
    """~/.voxkage/output/ — fallback directory for saving files."""
    d = voxkage_dir() / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d

def task_logs_dir() -> Path:
    """~/.voxkage/task_logs/"""
    d = voxkage_dir() / "task_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Config & secrets ─────────────────────────────────────────────────────────

def config_path() -> Path:
    """~/.voxkage/config.json"""
    return voxkage_dir() / "config.json"

def env_path() -> Path:
    """~/.voxkage/.env — user secrets."""
    return voxkage_dir() / ".env"

def env_path_resolved() -> Path:
    """
    Returns the .env path to load.
    Priority: ~/.voxkage/.env → <repo_root>/.env (dev mode fallback)
    """
    user_env = env_path()
    if user_env.exists():
        return user_env
    # Dev mode: .env next to pyproject.toml (repo root)
    dev_env = package_dir().parent / ".env"
    if dev_env.exists():
        return dev_env
    return user_env  # default even if doesn't exist yet


# ── Gemini CLI config ────────────────────────────────────────────────────────

def gemini_dir() -> Path:
    """~/.voxkage/.gemini/"""
    d = voxkage_dir() / ".gemini"
    d.mkdir(parents=True, exist_ok=True)
    return d

def settings_json_path() -> Path:
    return gemini_dir() / "settings.json"

def gemini_md_path() -> Path:
    return gemini_dir() / "GEMINI.md"


# ── Assets ───────────────────────────────────────────────────────────────────

def icon_path(name: str = "icon.png") -> Path:
    return package_dir() / "icons" / name

def template_path(name: str) -> Path:
    return package_dir() / "templates" / name


# ── Python & tool executables ────────────────────────────────────────────────

def python_exe() -> str:
    """Current Python interpreter path."""
    return sys.executable

def find_gemini_cli() -> str:
    """Find the gemini CLI executable across platforms."""
    # 1. Check PATH first (works if npm global bin is in PATH)
    found = shutil.which("gemini") or shutil.which("gemini.cmd")
    if found:
        return found

    # 2. Platform-specific npm global locations
    candidates: list[Path] = []
    if is_windows():
        npm_global = home_dir() / "AppData" / "Roaming" / "npm"
        candidates = [
            npm_global / "gemini.cmd",
            Path(r"C:\Program Files\nodejs\gemini.cmd"),
            Path(r"C:\Program Files (x86)\nodejs\gemini.cmd"),
        ]
    elif is_mac():
        candidates = [
            Path("/usr/local/bin/gemini"),
            Path("/opt/homebrew/bin/gemini"),
            home_dir() / ".npm-global" / "bin" / "gemini",
        ]
        # Check nvm versions
        nvm_dir = home_dir() / ".nvm" / "versions" / "node"
        if nvm_dir.exists():
            for node_ver in sorted(nvm_dir.iterdir(), reverse=True):
                candidates.append(node_ver / "bin" / "gemini")

    for c in candidates:
        if c.exists():
            return str(c)

    return "gemini"  # fallback — hope it's in PATH

def find_npm() -> str | None:
    """Find npm executable. Returns None if not installed."""
    found = shutil.which("npm") or shutil.which("npm.cmd")
    return found
