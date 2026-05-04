"""
voxkage/cli.py — Cross-platform CLI entry point for VoxKage.

Replaces the Windows-only voxkage.cmd with a pure Python equivalent
that works identically on Windows and macOS.

Commands:
    voxkage              Launch VoxKage (ASCII banner + Gemini CLI session)
    voxkage init         First-time setup wizard
    voxkage tray         Launch/restore system tray icon
    voxkage plugins      List all plugins and their status
    voxkage plugins add <name>   Configure a specific plugin
    voxkage --help       Show usage guide
    voxkage --version    Show installed version
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Ensure Windows console can output Unicode (for ASCII art and plugins)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        os.system("chcp 65001 >nul 2>&1")

from voxkage import __version__
from voxkage.paths import (
    is_windows, is_mac, is_supported_platform,
    voxkage_dir, package_dir, config_path, env_path,
    gemini_dir, settings_json_path, gemini_md_path,
    data_dir, task_logs_dir, find_gemini_cli, find_npm,
    python_exe, icon_path, template_path,
)


# ── ANSI color helpers ────────────────────────────────────────────────────────

def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if is_windows():
        return os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") or True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(r: int, g: int, b: int) -> str:
    """Generate ANSI 24-bit foreground color code."""
    if not _supports_color():
        return ""
    return f"\033[38;2;{r};{g};{b}m"


RST = "\033[0m" if _supports_color() else ""


# ── Theme palettes ────────────────────────────────────────────────────────────
# Each palette is keyed to the exact string Gemini CLI stores in settings.json.
# logo: 6 colors — rows 1-5 of the ASCII art + the dim bottom row.
# All colors sampled directly from each theme's actual syntax palette.

THEME_PALETTES = {
    # ── Default Dark — VoxKage signature sky-blue gradient ────────────────
    "Default Dark": dict(
        logo=[
            _c(14, 165, 233),   # row 1  — sky-500
            _c(34, 211, 238),   # row 2  — cyan-400
            _c(103, 232, 249),  # row 3  — cyan-300
            _c(147, 197, 253),  # row 4  — blue-200
            _c(186, 230, 253),  # row 5  — sky-200
            _c(30, 58, 138),    # row 6 dim — blue-900
        ],
        tag=_c(2, 132, 199),
        val=_c(56, 189, 248),
        meta=_c(51, 65, 85),
        prompt=_c(3, 105, 161),
        green=_c(34, 197, 94),
        notice=_c(71, 85, 105),
    ),

    # ── ANSI Dark — pure terminal palette, electric cyan to blue ──────────
    "ANSI Dark": dict(
        logo=[
            _c(0, 229, 255),    # row 1  — bright cyan
            _c(0, 207, 207),    # row 2  — teal
            _c(0, 180, 255),    # row 3  — sky blue
            _c(0, 144, 255),    # row 4  — bright blue
            _c(0, 110, 255),    # row 5  — blue
            _c(0, 51, 128),     # row 6 dim
        ],
        tag=_c(0, 207, 207),
        val=_c(0, 229, 255),
        meta=_c(68, 68, 68),
        prompt=_c(0, 144, 255),
        green=_c(0, 230, 118),
        notice=_c(136, 136, 136),
    ),

    # ── Atom One Dark — purple » blue » cyan » green » orange ─────────────
    "Atom One Dark": dict(
        logo=[
            _c(97, 175, 239),   # row 1  — blue (functions)
            _c(86, 182, 194),   # row 2  — cyan (types)
            _c(198, 120, 221),  # row 3  — purple (keywords)
            _c(224, 108, 117),  # row 4  — red (errors → accent)
            _c(229, 192, 123),  # row 5  — yellow (strings)
            _c(75, 82, 99),     # row 6 dim
        ],
        tag=_c(198, 120, 221),
        val=_c(97, 175, 239),
        meta=_c(75, 82, 99),
        prompt=_c(229, 192, 123),
        green=_c(152, 195, 121),
        notice=_c(99, 109, 131),
    ),

    # ── Ayu Dark — amber » sky gradient with teal accents ─────────────────
    "Ayu Dark": dict(
        logo=[
            _c(57, 186, 230),   # row 1  — tag blue
            _c(89, 194, 255),   # row 2  — function blue
            _c(115, 208, 255),  # row 3  — sky
            _c(255, 180, 84),   # row 4  — keyword amber
            _c(255, 213, 128),  # row 5  — string gold
            _c(45, 54, 64),     # row 6 dim
        ],
        tag=_c(255, 180, 84),
        val=_c(89, 194, 255),
        meta=_c(61, 79, 94),
        prompt=_c(57, 186, 230),
        green=_c(170, 217, 76),
        notice=_c(92, 103, 115),
    ),

    # ── Dracula Dark — iconic pink » purple » cyan » green ────────────────
    "Dracula Dark": dict(
        logo=[
            _c(255, 121, 198),  # row 1  — pink (keywords)
            _c(189, 147, 249),  # row 2  — purple (functions)
            _c(139, 233, 253),  # row 3  — cyan (types)
            _c(80, 250, 123),   # row 4  — green (strings)
            _c(241, 250, 140),  # row 5  — yellow (escape chars)
            _c(98, 114, 164),   # row 6 dim — comment
        ],
        tag=_c(139, 233, 253),
        val=_c(189, 147, 249),
        meta=_c(68, 71, 90),
        prompt=_c(255, 184, 108),
        green=_c(80, 250, 123),
        notice=_c(98, 114, 164),
    ),

    # ── GitHub Dark — blue monochrome with gold accents ───────────────────
    "GitHub Dark": dict(
        logo=[
            _c(121, 192, 255),  # row 1  — keyword blue
            _c(88, 166, 255),   # row 2  — function blue
            _c(56, 139, 253),   # row 3  — blue-dark
            _c(31, 111, 235),   # row 4  — blue-darker
            _c(56, 139, 253),   # row 5  — cycle back up
            _c(28, 42, 58),     # row 6 dim
        ],
        tag=_c(210, 153, 34),
        val=_c(121, 192, 255),
        meta=_c(48, 54, 61),
        prompt=_c(210, 153, 34),
        green=_c(63, 185, 80),
        notice=_c(110, 118, 129),
    ),

    # ── GitHub Dark Colorblind — blue + amber, no green dependency ────────
    "GitHub Dark Colorblind Dark": dict(
        logo=[
            _c(121, 192, 255),  # row 1
            _c(88, 166, 255),   # row 2
            _c(227, 179, 65),   # row 3  — amber (colorblind-safe accent)
            _c(255, 166, 87),   # row 4  — orange
            _c(210, 168, 255),  # row 5  — lavender
            _c(28, 42, 58),     # row 6 dim
        ],
        tag=_c(227, 179, 65),
        val=_c(121, 192, 255),
        meta=_c(48, 54, 61),
        prompt=_c(255, 166, 87),
        green=_c(227, 179, 65),
        notice=_c(110, 118, 129),
    ),

    # ── Holiday Dark — festive red » orange » gold » green ────────────────
    "Holiday Dark": dict(
        logo=[
            _c(255, 77, 77),    # row 1  — red
            _c(255, 140, 0),    # row 2  — orange
            _c(255, 215, 0),    # row 3  — gold
            _c(127, 255, 0),    # row 4  — chartreuse
            _c(0, 250, 154),    # row 5  — spring green
            _c(26, 26, 58),     # row 6 dim
        ],
        tag=_c(255, 215, 0),
        val=_c(127, 255, 0),
        meta=_c(42, 42, 74),
        prompt=_c(255, 140, 0),
        green=_c(0, 250, 154),
        notice=_c(106, 106, 138),
    ),

    # ── Shades Of Purple Dark — gold » pink » mint » cyan » lavender ──────
    "Shades Of Purple Dark": dict(
        logo=[
            _c(250, 208, 0),    # row 1  — gold (keywords)
            _c(255, 98, 140),   # row 2  — hot pink (operators)
            _c(165, 255, 144),  # row 3  — mint (strings)
            _c(158, 255, 255),  # row 4  — ice cyan (types)
            _c(251, 148, 255),  # row 5  — lavender (functions)
            _c(61, 59, 110),    # row 6 dim
        ],
        tag=_c(250, 208, 0),
        val=_c(165, 255, 144),
        meta=_c(61, 59, 110),
        prompt=_c(255, 98, 140),
        green=_c(57, 217, 0),
        notice=_c(123, 120, 197),
    ),

    # ── Solarized Dark — blue » cyan » green » yellow » orange ───────────
    "Solarized Dark": dict(
        logo=[
            _c(38, 139, 210),   # row 1  — blue
            _c(42, 161, 152),   # row 2  — cyan
            _c(133, 153, 0),    # row 3  — green
            _c(181, 137, 0),    # row 4  — yellow
            _c(203, 75, 22),    # row 5  — orange
            _c(7, 54, 66),      # row 6 dim — base03
        ],
        tag=_c(42, 161, 152),
        val=_c(38, 139, 210),
        meta=_c(88, 110, 117),
        prompt=_c(181, 137, 0),
        green=_c(133, 153, 0),
        notice=_c(101, 123, 131),
    ),

    # ── Tokyo Night Dark — soft blue » purple » green » gold » red ────────
    "Tokyo Night Dark": dict(
        logo=[
            _c(122, 162, 247),  # row 1  — blue (functions)
            _c(187, 154, 247),  # row 2  — purple (keywords)
            _c(158, 206, 106),  # row 3  — green (strings)
            _c(224, 175, 104),  # row 4  — gold (constants)
            _c(247, 118, 142),  # row 5  — red (errors → accent)
            _c(59, 66, 97),     # row 6 dim — comment
        ],
        tag=_c(187, 154, 247),
        val=_c(122, 162, 247),
        meta=_c(59, 66, 97),
        prompt=_c(224, 175, 104),
        green=_c(158, 206, 106),
        notice=_c(86, 95, 137),
    ),
}

# Light themes share one palette — dark ink on implied white/light bg
_LIGHT_PALETTE = dict(
    logo=[
        _c(9, 105, 218),    # row 1  — GitHub blue
        _c(5, 80, 174),     # row 2  — darker blue
        _c(26, 127, 55),    # row 3  — green
        _c(130, 80, 223),   # row 4  — purple
        _c(207, 34, 46),    # row 5  — red
        _c(192, 200, 210),  # row 6 dim
    ],
    tag=_c(9, 105, 218),
    val=_c(5, 80, 174),
    meta=_c(140, 149, 159),
    prompt=_c(9, 105, 218),
    green=_c(26, 127, 55),
    notice=_c(87, 96, 106),
)

# Light theme name substrings → use _LIGHT_PALETTE
_LIGHT_KEYWORDS = ("light", "xcode", "google code")


def _get_palette(theme: str) -> dict:
    """Return the best-matching palette for a given Gemini CLI theme name."""
    # Exact match first
    if theme in THEME_PALETTES:
        return THEME_PALETTES[theme]
    # Light theme fallback
    tl = theme.lower()
    if any(k in tl for k in _LIGHT_KEYWORDS):
        return _LIGHT_PALETTE
    # Fuzzy match on known dark themes
    for key in THEME_PALETTES:
        if key.lower().replace(" dark", "") in tl or tl in key.lower():
            return THEME_PALETTES[key]
    # Default
    return THEME_PALETTES["Default Dark"]


# ── ASCII Banner ──────────────────────────────────────────────────────────────

def print_banner():
    """Print the VoxKage ASCII art banner with theme-aware colors."""
    # Enable ANSI on Windows
    if is_windows():
        os.system("chcp 65001 >nul 2>&1")
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    # Read theme from ~/.gemini/settings.json
    theme = "Default Dark"
    try:
        gemini_settings = Path.home() / ".gemini" / "settings.json"
        if gemini_settings.exists():
            data = json.loads(gemini_settings.read_text(encoding="utf-8"))
            # Gemini CLI stores theme at top-level "theme" key
            theme = data.get("theme", data.get("ui", {}).get("theme", theme))
    except Exception:
        pass

    p = _get_palette(theme)
    L = p["logo"]

    # Load model from VoxKage config
    model = "gemini-2.5-flash"
    try:
        cfg_path = config_path()
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            model = cfg.get("main_model", model)
    except Exception:
        pass

    cwd = os.getcwd()

    art = (
        f"\n"
        f" {L[0]} ██╗   ██╗ ██████╗ ██╗  ██╗██╗  ██╗ █████╗  ██████╗ ███████╗{RST}\n"
        f" {L[1]} ██║   ██║██╔═══██╗╚██╗██╔╝██║ ██╔╝██╔══██╗██╔════╝ ██╔════╝{RST}\n"
        f" {L[2]} ██║   ██║██║   ██║ ╚███╔╝ █████╔╝ ███████║██║  ███╗█████╗  {RST}\n"
        f" {L[3]} ╚██╗ ██╔╝██║   ██║ ██╔██╗ ██╔═██╗ ██╔══██║██║   ██║██╔══╝  {RST}\n"
        f" {L[4]}  ╚████╔╝ ╚██████╔╝██╔╝ ██╗██║  ██╗██║  ██║╚██████╔╝███████╗{RST}\n"
        f" {L[5]}   ╚═══╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝{RST}\n"
        f"\n"
        f" {p['meta']} ──────────────────────────────────────────────────────────────{RST}\n"
        f"  {p['tag']}◈{RST}  {p['val']}OS Agentic AI{RST}    {p['meta']}│{RST}    {p['tag']}◈{RST}  {p['val']}Autonomous  ·  Persistent  ·  Aware{RST}\n"
        f" {p['meta']} ──────────────────────────────────────────────────────────────{RST}\n"
        f"\n"
        f" {p['meta']} session  {p['prompt']}›{RST}  {p['val']}{cwd}{RST}\n"
        f" {p['meta']} theme    {p['prompt']}›{RST}  {p['val']}{theme}{RST}\n"
        f" {p['meta']} engine   {p['prompt']}›{RST}  {p['val']}{model}{RST}\n"
        f" {p['meta']} status   {p['prompt']}›{RST}  {p['green']}● ready{RST}\n"
        f"\n"
        f" {p['meta']} ──────────────────────────────────────────────────────────────{RST}\n"
        f" {p['notice']}  VoxKage uses Gemini CLI as its inference backend. All intelligence,{RST}\n"
        f" {p['notice']}  memory, and agentic behavior is orchestrated by VoxKage core.{RST}\n"
        f" {p['meta']} ──────────────────────────────────────────────────────────────{RST}\n"
        f"\n"
    )

    try:
        sys.stdout.buffer.write(art.encode("utf-8") + b"\n")
        sys.stdout.flush()
    except AttributeError:
        print(art.encode("utf-8", "replace").decode("utf-8"), flush=True)


# ── Init Command ──────────────────────────────────────────────────────────────

def cmd_init():
    """First-time setup wizard."""
    print()
    print(f"  {_c(14,165,233)}VoxKage Setup{RST}")
    print(f"  {_c(71,85,105)}─────────────────────────────────────{RST}")
    print()

    if not is_supported_platform():
        print(f"  {_c(255,80,80)}✗  VoxKage currently supports Windows and macOS only.{RST}")
        sys.exit(1)

    plat = "Windows" if is_windows() else "macOS"
    print(f"  {_c(34,197,94)}✓{RST}  Platform: {plat}")

    voxkage_dir()
    data_dir()
    task_logs_dir()
    gemini_dir()
    print(f"  {_c(34,197,94)}✓{RST}  Data directory: {voxkage_dir()}")

    if not env_path().exists():
        tpl = template_path(".env.example")
        if tpl.exists():
            shutil.copy2(tpl, env_path())
        else:
            env_path().write_text(
                "# VoxKage Environment Variables\n"
                "# Add your API tokens here.\n\n"
                "# TELEGRAM_BOT_TOKEN=\n"
                "# TELEGRAM_CHAT_ID=\n"
                "# SPOTIFY_CLIENT_ID=\n"
                "# SPOTIFY_CLIENT_SECRET=\n"
                "# GITHUB_PAT=\n",
                encoding="utf-8",
            )
        print(f"  {_c(34,197,94)}✓{RST}  Created .env template")
    else:
        print(f"  {_c(34,197,94)}✓{RST}  .env already exists")

    if not config_path().exists():
        default_cfg = {
            "main_model": "gemini-2.5-flash",
            "subagent_model": "gemini-2.5-flash",
            "autostart": False,
            "voice_replies": False,
        }
        config_path().write_text(json.dumps(default_cfg, indent=2), encoding="utf-8")
        print(f"  {_c(34,197,94)}✓{RST}  Created default config")
    else:
        print(f"  {_c(34,197,94)}✓{RST}  Config already exists")

    npm = find_npm()
    if not npm:
        print()
        print(f"  {_c(255,180,84)}⚠  npm not found.{RST} VoxKage needs Node.js for the Gemini CLI backend.")
        print(f"     Install Node.js from: {_c(56,189,248)}https://nodejs.org/{RST}")
        print(f"     Then re-run: {_c(56,189,248)}voxkage init{RST}")
        print()
        return
    print(f"  {_c(34,197,94)}✓{RST}  npm found: {npm}")

    gemini = find_gemini_cli()
    gemini_works = False
    try:
        result = subprocess.run(
            [gemini, "--version"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        if result.returncode == 0:
            gemini_works = True
    except Exception:
        pass

    if gemini_works:
        print(f"  {_c(34,197,94)}✓{RST}  Gemini CLI found: {gemini}")
    else:
        print(f"  {_c(56,189,248)}↓{RST}  Installing Gemini CLI...")
        try:
            npm_cmd = [npm, "install", "-g", "@anthropic-ai/gemini-cli"]
            if is_windows():
                npm_cmd = [npm + ".cmd" if not npm.endswith(".cmd") else npm,
                           "install", "-g", "@google/gemini-cli"]
            subprocess.run(npm_cmd, check=True, timeout=120)
            print(f"  {_c(34,197,94)}✓{RST}  Gemini CLI installed")
        except Exception as e:
            print(f"  {_c(255,80,80)}✗{RST}  Failed to install Gemini CLI: {e}")
            print(f"     Try manually: {_c(56,189,248)}npm install -g @google/gemini-cli{RST}")

    print(f"  {_c(56,189,248)}↓{RST}  Setting up VoxKage browser (Playwright Chromium)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, timeout=300, capture_output=True,
        )
        print(f"  {_c(34,197,94)}✓{RST}  Playwright Chromium ready")
    except Exception as e:
        print(f"  {_c(255,180,84)}⚠{RST}  Playwright setup issue: {e}")
        print(f"     Try manually: {_c(56,189,248)}python -m playwright install chromium{RST}")

    _generate_settings_json()
    print(f"  {_c(34,197,94)}✓{RST}  Generated MCP settings")

    _generate_gemini_md()
    print(f"  {_c(34,197,94)}✓{RST}  Generated agent instructions")

    _install_platform_deps()

    print()
    print(f"  {_c(71,85,105)}─────────────────────────────────────{RST}")
    print(f"  {_c(34,197,94)}Setup complete!{RST}")
    print()
    print(f"  Next steps:")
    print(f"    {_c(56,189,248)}voxkage{RST}          — Launch VoxKage")
    print(f"    {_c(56,189,248)}voxkage plugins{RST}  — Configure integrations (Telegram, Gmail, etc.)")
    print(f"    {_c(56,189,248)}voxkage tray{RST}     — Start system tray icon")
    print()


def _install_platform_deps():
    extras = "windows" if is_windows() else "macos" if is_mac() else None
    if not extras:
        return
    print(f"  {_c(56,189,248)}↓{RST}  Installing {extras} platform packages...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", f"voxkage[{extras}]", "--quiet"],
            check=True, timeout=120, capture_output=True,
        )
        print(f"  {_c(34,197,94)}✓{RST}  Platform packages installed")
    except Exception:
        print(f"  {_c(255,180,84)}⚠{RST}  Some platform packages failed — non-critical")


def _sanitize_settings(settings: dict) -> dict:
    tools = settings.get("tools", {})
    if isinstance(tools, dict) and "core" in tools:
        del tools["core"]
    return settings


def _generate_settings_json():
    py = python_exe()
    pkg = str(package_dir())

    core_servers = [
        ("voxkage-system",    "mcp_servers/system_server.py"),
        ("voxkage-browser",   "mcp_servers/browser_server.py"),
        ("voxkage-media",     "mcp_servers/media_server.py"),
        ("voxkage-download",  "mcp_servers/download_server.py"),
        ("voxkage-oscontrol", "mcp_servers/os_control_server.py"),
        ("voxkage-file",      "mcp_servers/file_server.py"),
        ("voxkage-fileops",   "mcp_servers/file_ops_server.py"),
        ("voxkage-gui",       "mcp_servers/gui_server.py"),
        ("voxkage-health",    "mcp_servers/health_server.py"),
        ("voxkage-notify",    "mcp_servers/notify_server.py"),
        ("voxkage-memory",    "mcp_servers/memory_server.py"),
        ("voxkage-tasks",     "mcp_servers/task_server.py"),
        ("voxkage-rag",       "mcp_servers/rag_server.py"),
        ("voxkage-devserver", "mcp_servers/devserver_server.py"),
    ]

    servers = {}
    for name, script in core_servers:
        servers[name] = {
            "command": py,
            "args": [os.path.join(pkg, script)],
            "cwd": str(voxkage_dir()),
            "env": {"VOXKAGE_HOME": str(voxkage_dir())},
            "trust": True,
        }

    try:
        from voxkage.plugins.registry import get_configured_plugin_servers
        plugin_servers = get_configured_plugin_servers()
        servers.update(plugin_servers)
    except Exception:
        pass

    global_gemini = Path.home() / ".gemini" / "settings.json"
    global_gemini.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if global_gemini.exists():
        try:
            settings = json.loads(global_gemini.read_text(encoding="utf-8"))
        except Exception:
            pass

    if "mcpServers" not in settings:
        settings["mcpServers"] = {}
    settings["mcpServers"].update(servers)
    settings = _sanitize_settings(settings)

    global_gemini.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _patch_local_settings(servers)


def _generate_gemini_md():
    tpl_path = template_path("GEMINI.md.template")
    if not tpl_path.exists():
        repo_gemini = package_dir().parent / "GEMINI.md"
        if repo_gemini.exists():
            content = repo_gemini.read_text(encoding="utf-8")
        else:
            content = "# VoxKage Agent Instructions\n\nNo template found.\n"
    else:
        content = tpl_path.read_text(encoding="utf-8")

    content = content.replace("{{VOXKAGE_HOME}}", str(voxkage_dir()))
    content = content.replace("{{USER_HOME}}", str(Path.home()))
    content = content.replace("{{PLATFORM}}", "windows" if is_windows() else "darwin")
    content = content.replace("{{PACKAGE_DIR}}", str(package_dir()))

    gemini_md_path().write_text(content, encoding="utf-8")


# ── Tray Command ──────────────────────────────────────────────────────────────

def cmd_tray():
    tray_module = "voxkage.tray.tray_app"
    if is_windows():
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        subprocess.Popen(
            [pythonw, "-m", tray_module],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
    else:
        subprocess.Popen(
            [sys.executable, "-m", tray_module],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(f"  {_c(34,197,94)}✓{RST}  System tray launched")


# ── Plugins Command ───────────────────────────────────────────────────────────

def cmd_plugins(args):
    from voxkage.plugins.registry import list_plugins, add_plugin
    if args.plugin_action == "add" and args.plugin_name:
        add_plugin(args.plugin_name)
    else:
        list_plugins()


# ── Launch Command (default) ─────────────────────────────────────────────────

def _patch_local_settings(servers: dict | None = None):
    local_gemini = Path(os.getcwd()) / ".gemini" / "settings.json"
    if not local_gemini.exists():
        return

    try:
        local_settings = json.loads(local_gemini.read_text(encoding="utf-8"))
    except Exception:
        return

    changed = False

    tools = local_settings.get("tools", {})
    if isinstance(tools, dict) and "core" in tools:
        del tools["core"]
        if "disableLLMCorrection" not in tools:
            tools["disableLLMCorrection"] = False
        changed = True

    if servers:
        if "mcpServers" not in local_settings:
            local_settings["mcpServers"] = {}
        local_settings["mcpServers"].update(servers)
        changed = True

    for name, cfg in local_settings.get("mcpServers", {}).items():
        if name.startswith("voxkage-"):
            env = cfg.get("env", {})
            if not env or "VOXKAGE_HOME" not in env:
                cfg["env"] = {"VOXKAGE_HOME": str(voxkage_dir())}
                changed = True

    if changed:
        local_gemini.write_text(
            json.dumps(local_settings, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def cmd_launch(extra_args: list[str] | None = None):
    if not config_path().exists():
        print(f"  {_c(56,189,248)}First run detected — running setup...{RST}")
        print()
        cmd_init()

    try:
        _generate_settings_json()
    except Exception:
        pass

    _ensure_tray_running()

    print_banner()

    model = "gemini-2.5-flash"
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
        model = cfg.get("main_model", model)
    except Exception:
        pass

    gemini = find_gemini_cli()
    vk_home = str(voxkage_dir())

    cmd = [
        gemini,
        "-m", model,
        "--include-directories", vk_home,
        "--skip-trust",
    ]

    if extra_args:
        cmd.extend(extra_args)

    try:
        proc = subprocess.run(cmd, cwd=os.getcwd())
        sys.exit(proc.returncode)
    except FileNotFoundError:
        print(f"\n  {_c(255,80,80)}✗  Gemini CLI not found.{RST}")
        print(f"     Run {_c(56,189,248)}voxkage init{RST} to install it.\n")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


def _ensure_tray_running():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1)
        s.connect(("127.0.0.1", 49998))
        s.close()
    except (ConnectionRefusedError, OSError, socket.timeout):
        try:
            cmd_tray()
        except Exception:
            pass


# ── Main Entry Point ─────────────────────────────────────────────────────────

def main():
    if not is_supported_platform():
        print("\n  VoxKage currently supports Windows and macOS only.\n")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="voxkage",
        description="VoxKage — OS-level Agentic AI Assistant",
        epilog="Run 'voxkage' without arguments to launch an interactive session.",
    )
    parser.add_argument(
        "--version", "-V", action="version",
        version=f"VoxKage v{__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init", help="Run first-time setup wizard")
    subparsers.add_parser("tray", help="Launch/restore system tray icon")

    plugins_parser = subparsers.add_parser("plugins", help="List or configure plugins")
    plugins_parser.add_argument("plugin_action", nargs="?", default=None, help="Action: 'add'")
    plugins_parser.add_argument("plugin_name", nargs="?", default=None, help="Plugin name")

    args, remaining = parser.parse_known_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "tray":
        cmd_tray()
    elif args.command == "plugins":
        cmd_plugins(args)
    else:
        cmd_launch(remaining if remaining else None)


if __name__ == "__main__":
    main()