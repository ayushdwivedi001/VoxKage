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


# ── Install Command ───────────────────────────────────────────────────────────

_INSTALL_GROUPS = {
    "rag":       ("voxkage[rag]",       None,                                  "RAG Memory"),
    "vision":    ("voxkage[vision]",     None,                                  "Vision & OCR"),
    "browser":   ("voxkage[browser]",    "playwright install chromium",          "Browser Engine"),
    "docs_plus": ("voxkage[docs_plus]",  None,                                  "PDF Conversion"),
    "tray":      ("voxkage[tray]",       None,                                  "System Tray"),
    "full":      ("voxkage[full]",       "playwright install chromium",          "Full Suite"),
}

def cmd_install(group: str):
    """Install an optional VoxKage capability pack."""
    if group not in _INSTALL_GROUPS:
        print(f"\n  {_c(255,80,80)}✗  Unknown pack: '{group}'{RST}")
        print(f"  Available packs:")
        for name, (_, _, label) in _INSTALL_GROUPS.items():
            print(f"    {_c(56,189,248)}{name:12s}{RST} — {label}")
        print()
        return

    pip_spec, post_cmd, label = _INSTALL_GROUPS[group]
    print(f"\n  {_c(56,189,248)}↓{RST}  Installing {label} ({pip_spec})...\n")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pip_spec],
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"\n  {_c(255,80,80)}✗  Installation failed.{RST} Try manually: pip install {pip_spec}")
        return

    if post_cmd:
        print(f"\n  {_c(56,189,248)}↓{RST}  Running post-install: {post_cmd}")
        subprocess.run(post_cmd.split(), capture_output=False)

    print(f"\n  {_c(34,197,94)}✓  {label} is ready.{RST} Restart voxkage to use it.\n")


# ── Status Command ────────────────────────────────────────────────────────────

def cmd_status():
    """Show VoxKage system health and installed packs."""
    print()
    print(f"  {_c(14,165,233)}✦  VoxKage v{__version__} — System Status{RST}")
    print(f"  {_c(71,85,105)}{'─' * 50}{RST}")
    print()

    # Core
    gemini = find_gemini_cli()
    gemini_ok = False
    try:
        result = subprocess.run(
            [gemini, "--version"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        gemini_ok = result.returncode == 0
    except Exception:
        pass

    _ok = f"{_c(34,197,94)}✓{RST}"
    _no = f"{_c(255,80,80)}✗{RST}"

    print(f"  CORE")
    print(f"    {_ok if gemini_ok else _no}  Gemini CLI          {'found' if gemini_ok else 'not found — run: npm i -g @google/gemini-cli'}")
    print(f"    {_ok}  Brain directory     {voxkage_dir()}")

    # Count active MCP servers from settings
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
        model = cfg.get("main_model", "unknown")
        sub_model = cfg.get("subagent_model", "unknown")
        print(f"    {_ok}  Main model          {model}")
        print(f"    {_ok}  Sub-agent model     {sub_model}")
    except Exception:
        pass
    print()

    # Capability Packs
    print(f"  CAPABILITY PACKS")
    print(f"    {_ok}  Core AI + OS Control       (always on)")

    pack_checks = [
        ("RAG Memory",       "chromadb",              "rag"),
        ("Vision & OCR",     "cv2",                   "vision"),
        ("Browser Engine",   "playwright",            "browser"),
        ("PDF Conversion",   "fitz",                  "docs_plus"),
        ("System Tray",      "PySide6",               "tray"),
    ]
    for label, module, group in pack_checks:
        try:
            __import__(module)
            print(f"    {_ok}  {label:24s} installed")
        except ImportError:
            print(f"    {_no}  {label:24s} pip install voxkage[{group}]")
    print()

    # Integrations
    print(f"  INTEGRATIONS")
    integrations = [
        ("Telegram", "TELEGRAM_BOT_TOKEN"),
        ("Gmail",    "GOOGLE_APPLICATION_CREDENTIALS"),
        ("Spotify",  "SPOTIFY_CLIENT_ID"),
        ("GitHub",   "GITHUB_PAT"),
    ]
    from voxkage._env import load_voxkage_env
    load_voxkage_env()
    for name, env_var in integrations:
        configured = bool(os.environ.get(env_var, "").strip())
        if name == "Gmail":
            # Gmail uses a token file
            gmail_token = Path(os.path.expanduser("~")) / ".voxkage" / "gmail_token.json"
            configured = gmail_token.exists()
        if configured:
            print(f"    {_ok}  {name:20s} Connected")
        else:
            print(f"    {_no}  {name:20s} voxkage plugins add {name.lower()}")

    # Community Plugins
    print()
    print(f"  COMMUNITY PLUGINS")
    try:
        import importlib.metadata
        eps = list(importlib.metadata.entry_points(group="voxkage.plugins"))
        community = [ep for ep in eps if ep.name not in ("telegram", "gmail", "spotify", "github")]
        if community:
            for ep in community:
                print(f"    {_ok}  {ep.name}")
        else:
            print(f"    (none installed)              voxkage plugins search <query>")
    except Exception:
        print(f"    (none installed)")
    print()


# ── Init Command ──────────────────────────────────────────────────────────────

def cmd_init():
    """First-time setup wizard — v1.0.0 interactive flow."""
    print()
    print(f"  {_c(14,165,233)}┌{'─' * 60}┐{RST}")
    print(f"  {_c(14,165,233)}│{RST}  {_c(34,211,238)}✦  VoxKage v{__version__} — First-Time Setup{RST}{' ' * 22}{_c(14,165,233)}│{RST}")
    print(f"  {_c(14,165,233)}│{RST}  {_c(71,85,105)}{'─' * 56}{RST}  {_c(14,165,233)}│{RST}")
    print(f"  {_c(14,165,233)}│{RST}  VoxKage supercharges your Gemini CLI into a living OS AI.{_c(14,165,233)}│{RST}")
    print(f"  {_c(14,165,233)}│{RST}  This takes about 2 minutes.{' ' * 30}{_c(14,165,233)}│{RST}")
    print(f"  {_c(14,165,233)}└{'─' * 60}┘{RST}")
    print()

    if not is_supported_platform():
        print(f"  {_c(255,80,80)}✗  VoxKage currently supports Windows and macOS only.{RST}")
        sys.exit(1)

    plat = "Windows" if is_windows() else "macOS"
    print(f"  {_c(34,197,94)}✓{RST}  Platform: {plat}")

    # Create directories
    voxkage_dir()
    data_dir()
    task_logs_dir()
    gemini_dir()
    print(f"  {_c(34,197,94)}✓{RST}  Data directory: {voxkage_dir()}")

    # Create .env if missing
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

    # Create config if missing
    if not config_path().exists():
        default_cfg = {
            "main_model": "gemini-2.5-flash",
            "subagent_model": "gemini-2.5-flash",
            "autostart": False,
            "safe_mode": True,
        }
        config_path().write_text(json.dumps(default_cfg, indent=2), encoding="utf-8")
        print(f"  {_c(34,197,94)}✓{RST}  Created default config")
    else:
        print(f"  {_c(34,197,94)}✓{RST}  Config already exists")

    # Check Gemini CLI
    gemini_ok = False
    gemini = find_gemini_cli()
    try:
        result = subprocess.run(
            [gemini, "--version"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        gemini_ok = result.returncode == 0
    except Exception:
        pass

    if gemini_ok:
        print(f"  {_c(34,197,94)}✓{RST}  Gemini CLI found: {gemini}")
    else:
        print(f"  {_c(255,180,84)}⚠  Gemini CLI not found.{RST}")
        print(f"     Install: {_c(56,189,248)}npm install -g @google/gemini-cli{RST}")
        print(f"     Then authenticate: {_c(56,189,248)}gemini{RST}")

    print()
    print(f"  {_c(71,85,105)}Note: Gemini authentication is managed by the Gemini CLI.{RST}")
    print(f"  {_c(71,85,105)}Run `gemini` if you haven't set that up yet.{RST}")
    print()

    # ── [1/3] Capability Packs ────────────────────────────────────────────────
    print(f"  {_c(34,211,238)}[1/3] Capability Packs{RST}")
    print()
    print(f"  VoxKage core is already installed and ready. It includes:")
    print(f"    {_c(34,197,94)}✓{RST}  Agentic memory (SOUL system, problem/solution logs)")
    print(f"    {_c(34,197,94)}✓{RST}  ACE coding engine (step-by-step planning)")
    print(f"    {_c(34,197,94)}✓{RST}  Full OS control (open, edit, move, delete any file)")
    print(f"    {_c(34,197,94)}✓{RST}  System health, process management, date/time")
    print(f"    {_c(34,197,94)}✓{RST}  Office documents (Word, Excel, PowerPoint)")
    print(f"    {_c(34,197,94)}✓{RST}  Plugin credentials (Telegram, Gmail, Spotify, GitHub)")
    print(f"    {_c(34,197,94)}✓{RST}  Live internet access via agentic web search")
    print()
    print(f"  Optional packs add heavier capabilities:")
    print()
    print(f"  {_c(56,189,248)}[B]{RST} RAG Memory       Semantic codebase + document search  (~450 MB)")
    print(f"  {_c(56,189,248)}[C]{RST} Browser Engine   Full web automation in Chrome         (~500 MB)")
    print(f"  {_c(56,189,248)}[D]{RST} Vision           Screenshot analysis, OCR              (~250 MB)")
    print(f"  {_c(56,189,248)}[E]{RST} PDF Conversion   Convert between PDF ↔ Word ↔ other   (~80 MB)")
    print(f"  {_c(56,189,248)}[F]{RST} System Tray      VoxKage lives in your taskbar         (~200 MB)")
    print(f"  {_c(56,189,248)}[G]{RST} Install Everything (Full Suite — ~1.1 GB)")
    print(f"  {_c(56,189,248)}[S]{RST} Skip — install later with `voxkage install <pack>`")
    print()

    pack_map = {
        "b": "rag", "c": "browser", "d": "vision",
        "e": "docs_plus", "f": "tray", "g": "full",
    }

    try:
        choice = input(f"  Choose packs to install (e.g. B,C or G or S): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "s"

    if choice != "s" and choice:
        for letter in choice.replace(",", " ").split():
            letter = letter.strip()
            if letter in pack_map:
                cmd_install(pack_map[letter])
    print()

    # ── [2/3] Integrations ────────────────────────────────────────────────────
    print(f"  {_c(34,211,238)}[2/3] Integrations (Optional){RST}")
    print(f"  Connect your services to unlock VoxKage's full powers:")
    print()
    print(f"  [ ] Telegram — Bidirectional phone ↔ VoxKage bridge")
    print(f"  [ ] Gmail    — Read, compose, send emails hands-free")
    print(f"  [ ] Spotify  — Control music by text")
    print(f"  [ ] GitHub   — Clone, commit, push repositories")
    print()

    try:
        configure = input(f"  Configure now? (y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        configure = "n"

    if configure == "y":
        from voxkage.plugins.registry import list_plugins, add_plugin
        for plugin_name in ["telegram", "gmail", "spotify", "github"]:
            try:
                setup_yn = input(f"  Set up {plugin_name}? (y/N): ").strip().lower()
                if setup_yn == "y":
                    add_plugin(plugin_name)
            except (EOFError, KeyboardInterrupt):
                pass
    print()

    # ── [3/3] Auto-start ──────────────────────────────────────────────────────
    print(f"  {_c(34,211,238)}[3/3] Auto-start on Boot{RST}")
    print(f"  Start VoxKage system tray automatically on Windows login.")
    print(f"  Keeps the Telegram bridge alive in the background.")
    print()

    try:
        autostart = input(f"  Enable autostart? (y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        autostart = "n"

    if autostart == "y":
        try:
            from voxkage.autostart import enable_autostart
            enable_autostart()
            print(f"  {_c(34,197,94)}✓{RST}  Autostart enabled")
            # Update config
            try:
                cfg = json.loads(config_path().read_text(encoding="utf-8"))
                cfg["autostart"] = True
                config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            except Exception:
                pass
        except Exception as e:
            print(f"  {_c(255,180,84)}⚠{RST}  Autostart setup failed: {e}")

    # Generate settings & GEMINI.md
    _generate_settings_json()
    print(f"  {_c(34,197,94)}✓{RST}  Generated MCP settings")

    _generate_gemini_md()
    print(f"  {_c(34,197,94)}✓{RST}  Generated agent instructions")

    _install_platform_deps()

    print()
    print(f"  {_c(71,85,105)}{'─' * 60}{RST}")
    print(f"  {_c(34,197,94)}✓  VoxKage is ready. Type `voxkage` to start your OS AI.{RST}")
    print(f"  {_c(71,85,105)}{'─' * 60}{RST}")
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
        ("voxkage-coding",    "mcp_servers/coding_server.py"),
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


def _ensure_telegram_watcher_running():
    """
    Start the Telegram Watcher background process if not already running.

    Singleton-aware:
      - If a watcher is already alive (from a previous VoxKage session), do nothing.
      - If the lock file contains a dead PID, clean it up and spawn a fresh watcher.
      - Only starts if TELEGRAM_BOT_TOKEN is configured.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return  # Telegram not configured — skip silently

    voxkage_dir_path = Path(os.path.expanduser("~")) / ".voxkage"
    lock_file = voxkage_dir_path / "telegram_watcher.lock"

    # Check if a watcher is already alive
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            # Windows-safe process existence check
            try:
                import psutil
                if psutil.pid_exists(pid):
                    return  # Watcher is alive — do not spawn another
            except ImportError:
                try:
                    os.kill(pid, 0)
                    return  # Watcher is alive
                except (OSError, SystemError, ValueError):
                    pass  # Dead PID — proceed to clean up and spawn
        except (ValueError, OSError):
            pass  # Corrupt lock file — will be overwritten by the new watcher

    # Locate the watcher script
    watcher_script = Path(__file__).parent / "telegram_watcher.py"
    if not watcher_script.exists():
        return  # Watcher not installed

    try:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable

        if is_windows():
            subprocess.Popen(
                [pythonw, str(watcher_script)],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [sys.executable, str(watcher_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception:
        pass  # Non-critical — VoxKage still works, just no auto-injection


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
    _ensure_telegram_watcher_running()

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
        epilog=(
            "Quick start:\n"
            "  voxkage                  Launch VoxKage interactive session\n"
            "  voxkage init             First-time setup wizard\n"
            "  voxkage status           Show system health & installed packs\n"
            "  voxkage install <pack>   Install a capability pack (rag, vision, browser, docs_plus, tray, full)\n"
            "  voxkage plugins          List or configure integrations\n"
            "  voxkage tray             Launch system tray icon\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", "-V", action="version",
        version=f"VoxKage v{__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init", help="Run first-time setup wizard")
    subparsers.add_parser("tray", help="Launch/restore system tray icon")
    subparsers.add_parser("status", help="Show system health & installed packs")

    install_parser = subparsers.add_parser("install", help="Install a capability pack")
    install_parser.add_argument("pack", help="Pack name: rag, vision, browser, docs_plus, tray, full")

    plugins_parser = subparsers.add_parser("plugins", help="List or configure plugins")
    plugins_parser.add_argument("plugin_action", nargs="?", default=None, help="Action: 'add'")
    plugins_parser.add_argument("plugin_name", nargs="?", default=None, help="Plugin name")

    args, remaining = parser.parse_known_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "tray":
        cmd_tray()
    elif args.command == "status":
        cmd_status()
    elif args.command == "install":
        cmd_install(args.pack)
    elif args.command == "plugins":
        cmd_plugins(args)
    else:
        cmd_launch(remaining if remaining else None)


if __name__ == "__main__":
    main()