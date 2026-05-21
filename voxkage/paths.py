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


# ── Antigravity CLI config ───────────────────────────────────────────────────

def gemini_dir() -> Path:
    """~/.voxkage/.gemini/"""
    d = voxkage_dir() / ".gemini"
    d.mkdir(parents=True, exist_ok=True)
    return d

def settings_json_path() -> Path:
    return gemini_dir() / "settings.json"

def mcp_config_json_path() -> Path:
    return home_dir() / ".gemini" / "config" / "mcp_config.json"

def gemini_md_path() -> Path:
    return gemini_dir() / "GEMINI.md"


# ── Assets ───────────────────────────────────────────────────────────────────

def icon_path(name: str = "icon.png") -> Path:
    return package_dir() / "icons" / name

def template_path(name: str) -> Path:
    """Find a bundled template file.
    Checks templates/ first (canonical), then data/ as fallback."""
    in_templates = package_dir() / "templates" / name
    if in_templates.exists():
        return in_templates
    return package_dir() / "data" / name


# ── Python & tool executables ────────────────────────────────────────────────

def python_exe() -> str:
    """Current Python interpreter path."""
    return sys.executable

def find_gemini_cli() -> str:
    """Find the legacy command line executable (kept for reference)."""
    found = shutil.which("gemini") or shutil.which("gemini.cmd")
    if found:
        return found
    if is_windows():
        npm_global = home_dir() / "AppData" / "Roaming" / "npm"
        for c in [npm_global / "gemini.cmd", Path(r"C:\Program Files\nodejs\gemini.cmd")]:
            if c.exists():
                return str(c)
    return "gemini"


def find_agy_cli() -> str:
    """Find the Antigravity CLI (agy) executable across platforms."""
    # 1. Check PATH first
    found = shutil.which("agy") or shutil.which("agy.exe")
    if found:
        return found

    # 2. Platform-specific locations
    candidates: list[Path] = []
    if is_windows():
        candidates = [
            home_dir() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
            Path(r"C:\Program Files\agy\bin\agy.exe"),
            Path(r"C:\Program Files (x86)\agy\bin\agy.exe"),
            home_dir() / "AppData" / "Roaming" / "agy" / "bin" / "agy.exe",
        ]
    elif is_mac():
        candidates = [
            Path("/usr/local/bin/agy"),
            Path("/opt/homebrew/bin/agy"),
            home_dir() / ".local" / "bin" / "agy",
        ]

    for c in candidates:
        if c.exists():
            return str(c)

    return "agy"  # fallback — hope it's in PATH


def find_npm() -> str | None:
    """Find npm executable. Returns None if not installed."""
    found = shutil.which("npm") or shutil.which("npm.cmd")
    return found


def agy_mcp_dir() -> Path:
    """~/.gemini/antigravity-cli/mcp/ — where agy reads its MCP server tool schemas."""
    d = home_dir() / ".gemini" / "antigravity-cli" / "mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_opencode_cli() -> str:
    """Find the OpenCode CLI executable across platforms."""
    # 1. Check PATH first
    found = shutil.which("opencode") or shutil.which("opencode.exe") or shutil.which("opencode.cmd")
    if found:
        return found

    # 2. Platform-specific candidate locations
    candidates: list[Path] = []
    if is_windows():
        candidates = [
            home_dir() / "AppData" / "Roaming" / "npm" / "opencode.cmd",
            home_dir() / "AppData" / "Roaming" / "npm" / "opencode",
            home_dir() / ".local" / "bin" / "opencode",
            Path(r"C:\Program Files\opencode\opencode.exe"),
        ]
    elif is_mac():
        candidates = [
            Path("/usr/local/bin/opencode"),
            Path("/opt/homebrew/bin/opencode"),
            home_dir() / ".local" / "bin" / "opencode",
        ]
    else:
        candidates = [
            home_dir() / ".local" / "bin" / "opencode",
            Path("/usr/local/bin/opencode"),
            Path("/usr/bin/opencode"),
        ]

    for c in candidates:
        if c.exists():
            return str(c)

    return "opencode"  # fallback — hope it's in PATH


def opencode_config_path() -> Path:
    """Path to OpenCode's global config: %USERPROFILE%\\.config\\opencode\\opencode.json (Win) or ~/.config/opencode/opencode.json."""
    p = home_dir() / ".config" / "opencode" / "opencode.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def opencode_agents_md_path() -> Path:
    """Path to OpenCode's global instruction file: ~/.config/opencode/AGENTS.md."""
    return home_dir() / ".config" / "opencode" / "AGENTS.md"


