"""
voxkage/cli.py — Cross-platform CLI entry point for VoxKage.

Replaces the Windows-only voxkage.cmd with a pure Python equivalent
that works identically on Windows and macOS.

Commands:
    voxkage              Launch VoxKage (ASCII banner + Antigravity CLI session)
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
    data_dir, task_logs_dir, find_agy_cli, find_npm,
    find_opencode_cli, opencode_config_path, opencode_agents_md_path,
    python_exe, icon_path, template_path, output_dir,
    agy_mcp_dir,
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
# Each palette is keyed to a theme name string.
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
    """Return the best-matching palette for a given Antigravity CLI theme name."""
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

def _strip_ansi(s: str) -> str:
    """Strip ANSI escape codes to get true visible character width."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _center_ansi_line(line: str, width: int) -> str:
    """Center a line that contains ANSI codes (visible width ≠ byte length)."""
    visible_len = len(_strip_ansi(line))
    pad = max(0, (width - visible_len) // 2)
    return " " * pad + line


def print_banner():
    """Print the VoxKage ASCII art banner — dynamically centered, no ANSI clears.

    IMPORTANT: Do NOT use any ANSI escape sequences (\x1b[2J, \x1b[3J, \x1b[H)
    or os.system("cls") here. Antigravity CLI uses React-Ink which takes full ownership
    of the terminal after this function returns. Any ANSI VT state we inject here
    will corrupt Ink's internal cursor position tracking, causing the terminal to
    go completely black on resize. Pure newlines are safe because they don't alter
    VT parser state — they just advance the cursor downward.
    """
    # ── Enable ANSI colors on Windows (both CMD and PowerShell) ───────────────
    if is_windows():
        os.system("chcp 65001 >nul 2>&1")
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    # ── Get terminal dimensions ───────────────────────────────────────────────
    try:
        term_width = os.get_terminal_size().columns
        term_height = os.get_terminal_size().lines
    except OSError:
        term_width, term_height = 80, 24

    # ── Read theme ─────────────────────────────────────────────────────────────
    theme = "Default Dark"
    try:
        if config_path().exists():
            cfg_data = json.loads(config_path().read_text(encoding="utf-8"))
            theme = cfg_data.get("theme", theme)
        else:
            agy_settings = Path.home() / ".gemini" / "settings.json"
            if agy_settings.exists():
                data = json.loads(agy_settings.read_text(encoding="utf-8"))
                theme = data.get("theme", data.get("ui", {}).get("theme", theme))
    except Exception:
        pass

    p = _get_palette(theme)
    L = p["logo"]

    # ── Raw art lines (no leading space — centering is computed dynamically) ──
    art_lines_raw = [
        f"{L[0]}██╗   ██╗ ██████╗ ██╗  ██╗██╗  ██╗ █████╗  ██████╗ ███████╗{RST}",
        f"{L[1]}██║   ██║██╔═══██╗╚██╗██╔╝██║ ██╔╝██╔══██╗██╔════╝ ██╔════╝{RST}",
        f"{L[2]}██║   ██║██║   ██║ ╚███╔╝ █████╔╝ ███████║██║  ███╗█████╗  {RST}",
        f"{L[3]}╚██╗ ██╔╝██║   ██║ ██╔██╗ ██╔═██╗ ██╔══██║██║   ██║██╔══╝  {RST}",
        f"{L[4]} ╚████╔╝ ╚██████╔╝██╔╝ ██╗██║  ██╗██║  ██║╚██████╔╝███████╗{RST}",
        f"{L[5]}  ╚═══╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝{RST}",
    ]

    # ── Build horizontally centered output ────────────────────────────────────
    # CRITICAL: Do NOT add vertical padding (newlines before art).
    # React-Ink tracks its height using relative cursor moves (\x1b[NA).
    # If we push the cursor down with newlines, React-Ink's "move up N rows"
    # calculation on resize goes out of bounds → Windows Terminal shows a blank
    # screen. Keep it to just a couple of breathing-room newlines at most.
    centered_art = []
    for line in art_lines_raw:
        centered_art.append(_center_ansi_line(line, term_width))

    # 2 blank lines before art, 1 after — minimal and resize-safe
    output = "\n\n" + "\n".join(centered_art) + "\n"

    try:
        sys.stdout.buffer.write(output.encode("utf-8"))
        sys.stdout.buffer.flush()
    except AttributeError:
        sys.stdout.write(output)
        sys.stdout.flush()




# ── Install Command ───────────────────────────────────────────────────────────

_INSTALL_GROUPS = {
    "rag":       ("voxkage[rag]",       None,                                  "RAG Memory"),
    "vision":    ("voxkage[vision]",     None,                                  "Vision & OCR"),
    "browser":   ("voxkage[browser]",    "playwright install chromium",          "Browser Engine"),
    "docs_plus": ("voxkage[docs_plus]",  None,                                  "PDF Conversion"),
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
    
    # Smart resolver for local repository clones / editable mode
    from pathlib import Path
    pkg_root = Path(__file__).parent.parent
    pyproject_path = pkg_root / "pyproject.toml"

    if pyproject_path.exists():
        # We are in a local git clone/workspace! Install from the local folder with extras.
        # Use editable -e flag to preserve the editable installation state
        pip_args = [sys.executable, "-m", "pip", "install", "-e", f"{pkg_root.resolve()}[{group}]"]
        print(f"\n  {_c(56,189,248)}↓{RST}  Installing {label} from local workspace clone ({pkg_root.resolve()}[{group}])...\n")
    else:
        # Standard PyPI install
        pip_args = [sys.executable, "-m", "pip", "install", pip_spec]
        print(f"\n  {_c(56,189,248)}↓{RST}  Installing {label} ({pip_spec})...\n")

    result = subprocess.run(
        pip_args,
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"\n  {_c(255,80,80)}✗  Installation failed.{RST}")
        return

    if post_cmd:
        print(f"\n  {_c(56,189,248)}↓{RST}  Running post-install: {post_cmd}")
        if post_cmd.startswith("playwright "):
            # Run playwright module safely using environment's python executable
            cmd_args = [sys.executable, "-m", "playwright"] + post_cmd.split()[1:]
        else:
            cmd_args = post_cmd.split()
        subprocess.run(cmd_args, capture_output=False)

    print(f"\n  {_c(34,197,94)}✓  {label} is ready.{RST} Restart voxkage to use it.\n")


# ── Status Command ────────────────────────────────────────────────────────────

def cmd_status():
    """Show VoxKage system health and installed packs."""
    print()
    print(f"  {_c(14,165,233)}✦  VoxKage v{__version__} — System Status{RST}")
    print(f"  {_c(71,85,105)}{'─' * 50}{RST}")
    print()

    # Core — detect active engine from config
    _status_cfg: dict = {}
    try:
        _status_cfg = json.loads(config_path().read_text(encoding="utf-8")) if config_path().exists() else {}
    except Exception:
        pass
    _active_engine = _status_cfg.get("interface_engine", "antigravity")

    _ok = f"{_c(34,197,94)}✓{RST}"
    _no = f"{_c(255,80,80)}✗{RST}"

    if _active_engine == "opencode":
        _oc_exe = find_opencode_cli()
        _oc_ok = False
        _oc_ver = ""
        try:
            _oc_res = subprocess.run(
                [_oc_exe, "--version"], capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
            )
            _oc_ok = _oc_res.returncode == 0
            _oc_ver = _oc_res.stdout.strip() or _oc_res.stderr.strip()
        except Exception:
            pass
        print(f"  CORE")
        print(f"    {_ok if _oc_ok else _no}  Interface Engine     {'OpenCode CLI — ' + _oc_ver if _oc_ok else 'OpenCode CLI — not found (npm install -g @opencode/cli)'}")
        print(f"    {_ok}  Brain directory      {voxkage_dir()}")
    else:
        agy = find_agy_cli()
        agy_ok = False
        agy_ver = ""
        try:
            result = subprocess.run(
                [agy, "--version"], capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
            )
            agy_ok = result.returncode == 0
            agy_ver = result.stdout.strip()
        except Exception:
            pass
        print(f"  CORE")
        print(f"    {_ok if agy_ok else _no}  Interface Engine     {'Antigravity CLI (agy) — ' + agy_ver if agy_ok else 'Antigravity CLI — not found'}")
        print(f"    {_ok}  Brain directory      {voxkage_dir()}")

    print()

    # Capability Packs
    print(f"  CAPABILITY PACKS")
    print(f"    {_ok}  Core AI + OS Control       (always on)")

    pack_checks = [
        ("RAG Memory",       "chromadb",              "rag"),
        ("Vision & OCR",     "cv2",                   "vision"),
        ("Browser Engine",   "playwright",            "browser"),
        ("PDF Conversion",   "fitz",                  "docs_plus"),
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
    from voxkage.plugins.registry import _load_all_plugins
    plugins = _load_all_plugins()

    builtin_names = {
        "telegram", "gmail", "spotify", "github", "firebase",
        "netlify", "supabase", "clickhouse", "sequential-thinking"
    }

    builtins = [p for p in plugins if p.name in builtin_names]
    community = [p for p in plugins if p.name not in builtin_names]

    for p in builtins:
        configured = p.is_configured()
        if configured:
            print(f"    {_ok}  {p.display_name:24s} Connected")
        else:
            print(f"    {_no}  {p.display_name:24s} voxkage plugins add {p.name}")

    # Community Plugins
    print()
    print(f"  COMMUNITY PLUGINS")
    if community:
        for p in community:
            status = "Connected" if p.is_configured() else f"voxkage plugins add {p.name}"
            marker = _ok if p.is_configured() else _no
            print(f"    {marker}  {p.display_name:24s} {status}")
    else:
        print(f"    (none installed)              voxkage plugins search <query>")
    print()


# ── Init Command ──────────────────────────────────────────────────────────────

def cmd_init():
    """First-time setup wizard — v1.0.0 interactive flow."""
    print()
    print(f"  {_c(14,165,233)}┌{'─' * 60}┐{RST}")
    print(f"  {_c(14,165,233)}│{RST}  {_c(34,211,238)}✦  VoxKage v{__version__} — First-Time Setup{RST}{' ' * 22}{_c(14,165,233)}│{RST}")
    print(f"  {_c(14,165,233)}│{RST}  {_c(71,85,105)}{'─' * 56}{RST}  {_c(14,165,233)}│{RST}")
    print(f"  {_c(14,165,233)}│{RST}  VoxKage supercharges your Antigravity CLI into a living OS AI.{_c(14,165,233)}│{RST}")
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

    # Check Antigravity CLI
    agy_ok = False
    agy = find_agy_cli()
    try:
        result = subprocess.run(
            [agy, "--version"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
        )
        agy_ok = result.returncode == 0
    except Exception:
        pass

    if agy_ok:
        print(f"  {_c(34,197,94)}✓{RST}  Antigravity CLI found: {agy}")
    else:
        print(f"  {_c(255,180,84)}⚠  Antigravity CLI not found.{RST}")
        print(f"     Install from: {_c(56,189,248)}https://antigravity.dev{RST}")

    print()
    print(f"  {_c(71,85,105)}Note: Authentication is managed by the Antigravity CLI.{RST}")
    print(f"  {_c(71,85,105)}Run `agy` if you haven't set that up yet.{RST}")
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

    # Scan which optional packs are already installed
    import importlib as _il
    def _has(mod):
        try:
            _il.import_module(mod)
            return True
        except ImportError:
            return False

    pack_defs = [
        # (letter, pack_key, label, sentinel_module, size_note)
        ("B", "rag",       "RAG Memory     ", "chromadb",             "~450 MB"),
        ("C", "vision",    "Vision & OCR   ", "cv2",                  "~250 MB"),
        ("D", "docs_plus", "PDF Conversion ", "docx2pdf",             " ~80 MB"),
    ]

    _ok = f"{_c(34,197,94)}✓{RST}"
    _no = f"{_c(255,80,80)}✗{RST}"

    missing_packs = []
    letter_map    = {}

    all_done = True
    for letter, key, label, sentinel, size in pack_defs:
        installed = _has(sentinel)
        if installed:
            print(f"    {_ok}  [{letter}] {label}  {_c(71,85,105)}already installed{RST}")
        else:
            all_done = False
            missing_packs.append((letter, key, label, size))
            letter_map[letter.lower()] = key

    if all_done:
        print(f"  {_c(34,197,94)}✓  All capability packs are installed.{RST}")
        print()
    else:
        print()
        print(f"  Optional packs not yet installed:")
        print()
        for letter, key, label, size in missing_packs:
            print(f"  {_c(56,189,248)}[{letter}]{RST} {label}  ({size})")
        # Full suite only if at least 2 missing
        if len(missing_packs) >= 2:
            remaining_keys = [k for _, k, _, _ in missing_packs]
            print(f"  {_c(56,189,248)}[G]{RST} Install all missing ({', '.join(remaining_keys)})")
            letter_map["g"] = "__missing__"
        print(f"  {_c(56,189,248)}[S]{RST} Skip — install later with `voxkage install <pack>`")
        print()

        try:
            choice = input(f"  Choose packs to install (e.g. B,C or G or S): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "s"

        if choice and choice != "s":
            for letter in choice.replace(",", " ").split():
                letter = letter.strip()
                if letter == "g":
                    for _, key, _, _ in missing_packs:
                        cmd_install(key)
                elif letter in letter_map:
                    cmd_install(letter_map[letter])
    print()

    # ── [2/3] Integrations ────────────────────────────────────────────────────
    print(f"  {_c(34,211,238)}[2/3] Integrations (Optional){RST}")
    print(f"  Connect your services to unlock VoxKage's full powers:")
    print()

    # Live scan — show tick state from actual .env
    from voxkage.plugins.registry import _load_all_plugins
    all_plugins = _load_all_plugins()
    configured_count = sum(1 for p in all_plugins if p.is_configured())

    for p in all_plugins:
        if p.is_configured():
            tick = f"{_c(34,197,94)}[✓]{RST}"
            label = f"{p.display_name:8s} — {_c(71,85,105)}{p.description}{RST}"
        else:
            tick = f"{_c(71,85,105)}[ ]{RST}"
            label = f"{p.display_name:8s} — {_c(71,85,105)}{p.description}{RST}"
        print(f"  {tick} {label}")
    print()

    if configured_count == len(all_plugins):
        prompt_label = "Reconfigure any? (y/N)"
    elif configured_count > 0:
        prompt_label = f"Configure missing ones? (y/N)"
    else:
        prompt_label = "Configure now? (y/N)"

    try:
        configure = input(f"  {prompt_label}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        configure = "n"

    if configure == "y":
        from voxkage.plugins.registry import add_plugin
        for p in all_plugins:
            already = p.is_configured()
            label = f"{_c(34,197,94)}(already configured){RST}" if already else ""
            try:
                setup_yn = input(
                    f"  {'Reconfigure' if already else 'Set up'} {p.display_name}? (y/N): "
                ).strip().lower()
                if setup_yn == "y":
                    add_plugin(p.name)
            except (EOFError, KeyboardInterrupt):
                pass
    print()

    # ── [3/3] Auto-start ──────────────────────────────────────────────────────
    print(f"  {_c(34,211,238)}[3/3] Auto-start on Boot{RST}")
    print(f"  Keeps the VoxKage tray and Telegram bridge alive after every reboot.")
    print()

    try:
        _autostart_currently_on = False
        if sys.platform == "win32":
            from voxkage.autostart import is_autostart_enabled
            _autostart_currently_on = is_autostart_enabled()
    except Exception:
        _autostart_currently_on = False

    if _autostart_currently_on:
        print(f"  {_c(34,197,94)}[✓]{RST} Autostart is {_c(34,197,94)}already enabled{RST} — VoxKage tray starts on login.")
        print()
        try:
            toggle = input(f"  Disable autostart? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            toggle = "n"
        if toggle == "y":
            try:
                from voxkage.autostart import disable_autostart
                disable_autostart()
                print(f"  {_c(255,180,84)}○{RST}  Autostart disabled.")
                try:
                    cfg = json.loads(config_path().read_text(encoding="utf-8"))
                    cfg["autostart"] = False
                    config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                except Exception:
                    pass
            except Exception as e:
                print(f"  {_c(255,80,80)}✗{RST}  Failed to disable autostart: {e}")
        else:
            print(f"  {_c(34,197,94)}✓{RST}  Autostart unchanged — still enabled.")
    else:
        print(f"  {_c(71,85,105)}[ ]{RST} Autostart is {_c(71,85,105)}not enabled{RST}.")
        print()
        try:
            autostart = input(f"  Enable autostart? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            autostart = "n"
        if autostart == "y":
            try:
                from voxkage.autostart import enable_autostart
                enable_autostart()
                print(f"  {_c(34,197,94)}✓{RST}  Autostart enabled — VoxKage tray will start on next login.")
                try:
                    cfg = json.loads(config_path().read_text(encoding="utf-8"))
                    cfg["autostart"] = True
                    config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                except Exception:
                    pass
            except Exception as e:
                print(f"  {_c(255,180,84)}⚠{RST}  Autostart setup failed: {e}")

    # Scaffold agy MCP servers
    _scaffold_agy_mcp_servers()
    print(f"  {_c(34,197,94)}✓{RST}  Registered MCP servers in Antigravity")

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


def _scaffold_agy_mcp_servers():
    """
    Register all VoxKage MCP servers with Antigravity CLI.

    agy reads MCP servers from ~/.gemini/antigravity-cli/mcp/<server-name>/
    Each server folder contains:
      - _server.json  — launch config (command, args, env, cwd)
      - <tool>.json   — one schema file per @mcp.tool() decorated function
    """
    import json as _json
    import ast as _ast
    py = python_exe()
    pkg = str(package_dir())
    mcp_base = agy_mcp_dir()  # ~/.gemini/antigravity-cli/mcp/

    core_servers = [
        ("voxkage-cognitive-core", "mcp_servers/cognitive_core_server.py"),
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
        ("voxkage-session",   "mcp_servers/session_server.py"),
    ]

    # Add configured plugin servers (telegram, github, spotify, gmail)
    try:
        from voxkage.plugins.registry import get_configured_plugin_servers
        plugin_cfgs = get_configured_plugin_servers()
        for name, cfg in plugin_cfgs.items():
            script = cfg.get("args", [None])[0]
            if script:
                script_rel = os.path.relpath(script, pkg) if os.path.isabs(script) else script
                core_servers.append((name, script_rel))
    except Exception:
        pass

    vk_home = str(voxkage_dir())

    for server_name, script_rel in core_servers:
        script_abs = os.path.join(pkg, script_rel)
        if not os.path.exists(script_abs):
            continue

        server_dir = mcp_base / server_name
        server_dir.mkdir(parents=True, exist_ok=True)

        # _server.json is no longer used by Antigravity CLI; we register servers directly in mcp_config.json via _generate_settings_json

        # Extract per-tool JSON schemas via AST (no subprocess required)
        try:
            src = Path(script_abs).read_text(encoding="utf-8", errors="ignore")
            tree = _ast.parse(src)

            for node in _ast.walk(tree):
                if not isinstance(node, _ast.FunctionDef):
                    continue

                # Only @mcp.tool() decorated functions
                is_mcp_tool = any(
                    (isinstance(d, _ast.Call) and
                     isinstance(d.func, _ast.Attribute) and
                     d.func.attr == "tool")
                    or
                    (isinstance(d, _ast.Attribute) and d.attr == "tool")
                    for d in node.decorator_list
                )
                if not is_mcp_tool:
                    continue

                tool_name = node.name

                # Docstring → description
                description = ""
                if (node.body and isinstance(node.body[0], _ast.Expr) and
                        isinstance(node.body[0].value, _ast.Constant)):
                    description = node.body[0].value.s.strip()
                if not description:
                    description = f"VoxKage tool: {tool_name}"

                # Build parameter schema from function signature
                fn_args = node.args
                properties = {}
                required = []
                n_args = len(fn_args.args)
                n_defaults = len(fn_args.defaults)
                default_offset = n_args - n_defaults

                for i, arg in enumerate(fn_args.args):
                    if arg.arg in ("self", "return"):
                        continue
                    param_type = "string"
                    if arg.annotation:
                        try:
                            ann = _ast.unparse(arg.annotation)
                        except AttributeError:
                            ann = ""
                        if "int" in ann:
                            param_type = "integer"
                        elif "float" in ann:
                            param_type = "number"
                        elif "bool" in ann:
                            param_type = "boolean"
                        elif "list" in ann.lower():
                            param_type = "array"

                    properties[arg.arg] = {"type": param_type, "description": arg.arg}
                    if i < default_offset:
                        required.append(arg.arg)

                schema = {
                    "name": tool_name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                    }
                }
                if required:
                    schema["parameters"]["required"] = required

                (server_dir / f"{tool_name}.json").write_text(
                    _json.dumps(schema, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception:
            pass  # Non-critical: agy can still launch the server

    # Finally, write all servers to the central mcp_config.json for Antigravity CLI
    _generate_settings_json()


# Keep _generate_settings_json for internal use (coding_server, rag_server may reference it)

def _generate_settings_json():
    py = python_exe()
    pkg = str(package_dir())

    core_servers = [
        ("voxkage-cognitive-core", "mcp_servers/cognitive_core_server.py"),
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
        ("voxkage-session",   "mcp_servers/session_server.py"),
    ]

    servers = {}
    for name, script in core_servers:
        servers[name] = {
            "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
            "command": py,
            "args": [os.path.join(pkg, script)],
            "cwd": str(voxkage_dir()),
            "env": {"VOXKAGE_HOME": str(voxkage_dir())},
            "trust": True,
        }

    try:
        from voxkage.plugins.registry import get_configured_plugin_servers
        plugin_servers = get_configured_plugin_servers()
        for k, v in plugin_servers.items():
            # Handle remote serverUrl entries — convert to local mcp-remote proxy
            server_url = v.pop("serverUrl", None)
            if server_url:
                v["command"] = "npx"
                v["args"] = ["-y", "mcp-remote", server_url]
            if "$typeName" not in v:
                v["$typeName"] = "exa.cascade_plugins_pb.CascadePluginCommandTemplate"
        servers.update(plugin_servers)
    except Exception as e:
        print("ERROR in registry:", e)
        import traceback
        traceback.print_exc()

    from voxkage.paths import mcp_config_json_path
    vk_settings = mcp_config_json_path()
    vk_settings.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if vk_settings.exists():
        try:
            settings = json.loads(vk_settings.read_text(encoding="utf-8"))
        except Exception:
            pass

    settings["mcpServers"] = servers
    settings = _sanitize_settings(settings)

    vk_settings.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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
    content = content.replace("{{OUTPUT_DIR}}", str(output_dir()))

    # ── INJECT SOUL MEMORY ────────────────────────────────────────────────────
    soul_text = "*No user profile data available yet. Profile will grow as we interact.*"
    profile_path = voxkage_dir() / "user_profile.json"
    if profile_path.exists():
        try:
            import json
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            lines = []
            
            if profile.get("identity"):
                for k, v in profile["identity"].items():
                    lines.append(f"- **Identity ({k})**: {v}")
            
            if profile.get("preferences"):
                for k, v in profile["preferences"].items():
                    lines.append(f"- **Prefers ({k})**: {v}")
                    
            if profile.get("habits"):
                for h in profile["habits"]:
                    lines.append(f"- **Habit**: {h}")

            if lines:
                soul_text = "\n".join(lines)
        except Exception as e:
            soul_text = f"*Error loading soul memory: {e}*"

    # Inject background consolidated history if it exists
    consolidation_path = voxkage_dir() / "cognitive" / "soul_consolidation.md"
    if consolidation_path.exists():
        try:
            consol_content = consolidation_path.read_text(encoding="utf-8").strip()
            if consol_content:
                soul_text += "\n\n" + consol_content
        except Exception:
            pass

    content = content.replace("{{USER_SOUL_INJECTION}}", soul_text)
    # ──────────────────────────────────────────────────────────────────────────

    gemini_md_path().write_text(content, encoding="utf-8")


# ── Tray Command ──────────────────────────────────────────────────────────────

def cmd_tray():
    import subprocess
    tray_script = package_dir() / "tray" / "tray_app.py"
    if is_windows():
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        subprocess.Popen(
            [pythonw, str(tray_script)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
    else:
        subprocess.Popen(
            [sys.executable, str(tray_script)],
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




def _ensure_telegram_watcher_running():
    """
    Start the Telegram Watcher background process if not already running.

    Singleton-aware:
      - If a watcher is already alive (from a previous VoxKage session), do nothing.
      - If the lock file contains a dead PID, clean it up and spawn a fresh watcher.
      - Only starts if TELEGRAM_BOT_TOKEN is configured and telegram_watcher_enabled is True.
    """
    try:
        if config_path().exists():
            cfg = json.loads(config_path().read_text(encoding="utf-8"))
            if not cfg.get("telegram_watcher_enabled", True):
                return
    except Exception:
        pass

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


# ── GEMINI.md injection (system prompt for agy) ──────────────────────────────

# Sentinel kept for any old patched bundles that may still exist — harmless
SENTINEL = "// __VOXKAGE_PATCHED_v5__"

_VOXKAGE_RAW_ART = [
    "\u2588\u2588\u2557   \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2557  \u2588\u2588\u2557\u2588\u2588\u2557  \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557",
    "\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2550\u2588\u2588\u2557\u255a\u2588\u2588\u2557\u2588\u2588\u2554\u255d\u2588\u2588\u2551 \u2588\u2588\u2554\u255d\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d \u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d",
    "\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2551   \u2588\u2588\u2551 \u255a\u2588\u2588\u2588\u2554\u255d \u2588\u2588\u2588\u2588\u2588\u2554\u255d \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551\u2588\u2588\u2551  \u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2557  ",
    "\u255a\u2588\u2588\u2557 \u2588\u2588\u2554\u255d\u2588\u2588\u2551   \u2588\u2588\u2551 \u2588\u2588\u2554\u2588\u2588\u2557 \u2588\u2588\u2554\u2550\u2588\u2588\u2557 \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u255d  ",
    " \u255a\u2588\u2588\u2588\u2588\u2554\u255d \u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2554\u255d \u2588\u2588\u2557\u2588\u2588\u2551  \u2588\u2588\u2557\u2588\u2588\u2551  \u2588\u2588\u2551\u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557",
    "  \u255a\u2550\u2550\u2550\u255d   \u255a\u2550\u2550\u2550\u2550\u2550\u255d \u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u255d \u255a\u2550\u2550\u2550\u2550\u2550\u255d \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u255d",
]




def _js_stmt_end(content, start):
    """Return the index just past the closing ';' of the JS statement beginning at start.
    Uses paren/brace/bracket counting so it works for both concise arrow functions
    (no braces) and function-body arrow functions.  Returns -1 on failure."""
    i = start
    # Skip to the first '(' or '{' that opens the expression
    while i < len(content) and content[i] not in ('(', '{', '['):
        # jump over comments
        if content[i:i+2] == '//':
            nl = content.find('\n', i)
            i = nl + 1 if nl != -1 else len(content)
            continue
        if content[i:i+2] == '/*':
            ec = content.find('*/', i+2)
            i = ec + 2 if ec != -1 else len(content)
            continue
        i += 1
    depth = 0
    in_str = None
    while i < len(content):
        ch = content[i]
        if in_str:
            if ch == '\\' and in_str != '`':
                i += 2; continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'", '`'):
            in_str = ch
        elif ch in ('(', '{', '['):
            depth += 1
        elif ch in (')', '}', ']'):
            depth -= 1
            if depth == 0:
                i += 1
                # skip whitespace
                while i < len(content) and content[i] in (' ', '\t', '\n', '\r'):
                    i += 1
                if i < len(content) and content[i] == ';':
                    return i + 1
                # Handle chained calls: (0, jsxs)(Box_default, {...})
                # JSX compiled output uses this 2-part pattern — when depth hits 0
                # after the first pair and is immediately followed by '(' or '[',
                # keep counting rather than returning prematurely.
                if i < len(content) and content[i] in ('(', '['):
                    depth += 1
                    i += 1   # consume opening bracket; loop continues
                    continue
                return i
        i += 1
    return -1


def _patch_gemini_bundle():
    """[DEPRECATED] Kept as a no-op stub for compatibility. agy does not use React-Ink bundles."""
    pass


def _patch_gemini_settings():
    """[DEPRECATED] Kept as a no-op stub for compatibility. agy does not require setting tweaks."""
    pass


def _inject_global_settings():
    """[DEPRECATED] Kept as a no-op stub. VoxKage now uses agy's MCP directory
    structure (~/.gemini/antigravity-cli/mcp/) via _scaffold_agy_mcp_servers()."""
    pass


def _cleanup_global_settings():
    """[DEPRECATED] Kept as a no-op stub. No global settings to clean up with agy."""
    pass


def _inject_global_gemini_md():
    """
    Copy VoxKage's GEMINI.md into ~/.gemini/GEMINI.md so agy reads it
    as the user-level system prompt.

    agy reads GEMINI.md from ~/.gemini/GEMINI.md — same path as the
    legacy platform. We back up any existing file so _cleanup can restore it.
    """
    import shutil as _shutil
    src = gemini_md_path()          # ~/.voxkage/.gemini/GEMINI.md
    dst = Path.home() / ".gemini" / "GEMINI.md"
    bak = Path.home() / ".gemini" / "GEMINI.md.pre_voxkage"

    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        return

    # Back up the user's original GEMINI.md (if any and not already backed up)
    if dst.exists() and not bak.exists():
        _shutil.copy2(dst, bak)

    _shutil.copy2(src, dst)


def _cleanup_global_gemini_md():
    """
    Restore ~/.gemini/GEMINI.md to whatever it was before VoxKage ran.
    If the user had no GEMINI.md before, the file is removed.
    """
    dst = Path.home() / ".gemini" / "GEMINI.md"
    bak = Path.home() / ".gemini" / "GEMINI.md.pre_voxkage"

    try:
        if bak.exists():
            import shutil as _shutil
            _shutil.copy2(bak, dst)
            bak.unlink()
        elif dst.exists():
            dst.unlink()  # We created it, remove it cleanly
    except Exception:
        pass


# ── OpenCode Engine Helpers ────────────────────────────────────────────────────

def _scaffold_opencode_mcp():
    """
    Write all VoxKage MCP servers into OpenCode's global config file.

    OpenCode reads MCP servers from ~/.config/opencode/opencode.json under the
    "mcp" key. We read the existing file (if any), inject all VoxKage core +
    plugin servers under "mcp", and write back — preserving every other key
    the user may have set (models, providers, API keys, themes etc.).

    OpenCode MCP entry format:
        "server-name": {
            "type": "local",
            "command": ["python.exe", "/path/to/server.py"],
            "env": {"VOXKAGE_HOME": "..."},
            "enabled": true
        }
    """
    py = python_exe()
    pkg = str(package_dir())
    vk_home = str(voxkage_dir())

    core_servers = [
        ("voxkage-cognitive-core", "mcp_servers/cognitive_core_server.py"),
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
        ("voxkage-session",   "mcp_servers/session_server.py"),
    ]

    # Collect all configured plugin servers dynamically (same source as agy)
    all_servers: dict = {}
    for server_name, script_rel in core_servers:
        script_abs = os.path.join(pkg, script_rel)
        if not os.path.exists(script_abs):
            continue
        all_servers[server_name] = {
            "type": "local",
            "command": [py, script_abs],
            "env": {"VOXKAGE_HOME": vk_home},
            "enabled": True,
        }

    # Add every configured plugin server (Telegram, Gmail, GitHub, Spotify,
    # Firebase, Netlify, Supabase, Chrome DevTools, ClickHouse, Sequential
    # Thinking — whatever is registered at runtime).
    try:
        from voxkage.plugins.registry import get_configured_plugin_servers
        plugin_cfgs = get_configured_plugin_servers()
        for name, cfg in plugin_cfgs.items():
            server_url = cfg.get("serverUrl")
            if server_url:
                # Remote MCP server — wrap with mcp-remote proxy
                env_vars = cfg.get("env", {"VOXKAGE_HOME": vk_home})
                all_servers[name] = {
                    "type": "local",
                    "command": ["npx", "-y", "mcp-remote", server_url],
                    "env": env_vars,
                    "enabled": True,
                }
            else:
                # Plugin entries use agy format — extract command/args for opencode
                cmd_exe = cfg.get("command", py)
                args = cfg.get("args", [])
                env_vars = cfg.get("env", {"VOXKAGE_HOME": vk_home})
                # Build opencode-style command array: [executable, *args]
                full_cmd = [cmd_exe] + args if args else [cmd_exe]
                all_servers[name] = {
                    "type": "local",
                    "command": full_cmd,
                    "env": env_vars,
                    "enabled": True,
                }
    except Exception:
        pass

    # Read existing opencode.json (preserve all user keys)
    cfg_path = opencode_config_path()
    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    # Inject ONLY the "mcp" key — everything else is untouched
    existing["mcp"] = all_servers

    cfg_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _inject_agents_md():
    """
    Copy VoxKage's generated GEMINI.md into OpenCode's global AGENTS.md.

    OpenCode reads ~/.config/opencode/AGENTS.md as its global system
    instruction file (equivalent role to GEMINI.md in the agy engine).
    We back up any pre-existing AGENTS.md so _cleanup can restore it.
    """
    import shutil as _shutil
    src = gemini_md_path()          # ~/.voxkage/.gemini/GEMINI.md (already rendered)
    dst = opencode_agents_md_path() # ~/.config/opencode/AGENTS.md
    bak = dst.parent / "AGENTS.md.pre_voxkage"

    dst.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        return

    # Back up existing AGENTS.md once (don't overwrite an existing backup)
    if dst.exists() and not bak.exists():
        _shutil.copy2(dst, bak)

    _shutil.copy2(src, dst)


def _cleanup_agents_md():
    """
    Restore ~/.config/opencode/AGENTS.md to its pre-VoxKage state on exit.
    If the user had no AGENTS.md before, the file is removed.
    NOTE: opencode.json MCP entries are intentionally NOT cleaned — they should
    persist so /mcp always works even when OpenCode is launched independently.
    """
    dst = opencode_agents_md_path()
    bak = dst.parent / "AGENTS.md.pre_voxkage"

    try:
        if bak.exists():
            import shutil as _shutil
            _shutil.copy2(bak, dst)
            bak.unlink()
        elif dst.exists():
            dst.unlink()
    except Exception:
        pass


def cmd_launch(extra_args: list[str] | None = None):
    # Set window title
    if is_windows():
        os.system("title VoxKage")
    else:
        os.system("echo -e \"\\033]0;VoxKage\\a\"")

    if not config_path().exists():
        print(f"  {_c(56,189,248)}First run detected — running setup...{RST}")
        print()
        cmd_init()

    # ── Read engine preference ─────────────────────────────────────────────────
    _engine = "antigravity"
    try:
        _cfg_data = json.loads(config_path().read_text(encoding="utf-8")) if config_path().exists() else {}
        _engine = _cfg_data.get("interface_engine", "antigravity")
    except Exception:
        pass

    if _engine == "opencode":
        # ── OpenCode engine path ───────────────────────────────────────────────
        # Scaffold ALL MCP servers (core + every configured plugin) into
        # ~/.config/opencode/opencode.json under the "mcp" key.
        try:
            _scaffold_opencode_mcp()
        except Exception:
            pass

        # Regenerate soul memory and inject as AGENTS.md for OpenCode.
        try:
            _generate_gemini_md()
            _inject_agents_md()
        except Exception:
            pass

        _cli_exe = find_opencode_cli()
        _not_found_msg = (
            f"OpenCode CLI not found.\n"
            f"     Install with: {_c(56,189,248)}npm install -g @opencode/cli{RST}\n"
            f"     Then relaunch: {_c(56,189,248)}voxkage{RST}"
        )
        _cleanup_fn = _cleanup_agents_md

    else:
        # ── Antigravity engine path (default — existing behaviour unchanged) ───
        # Refresh MCP registrations in ~/.gemini/config/mcp_config.json.
        try:
            _scaffold_agy_mcp_servers()
        except Exception:
            pass

        # Regenerate GEMINI.md with latest soul memory and inject into agy.
        try:
            _generate_gemini_md()
            _inject_global_gemini_md()
        except Exception:
            pass

        _cli_exe = find_agy_cli()
        _not_found_msg = (
            f"Antigravity CLI not found.\n"
            f"     Install agy first, then run {_c(56,189,248)}voxkage init{RST} to configure."
        )
        _cleanup_fn = _cleanup_global_gemini_md

    _run_first_time_setup()                  # one-time: playwright install chromium
    _ensure_tray_running()
    _ensure_telegram_watcher_running()       # start Telegram background watcher
    _start_ipc_server()                      # Telegram → VoxKage terminal bridge

    # ── Print VoxKage ASCII banner, then launch chosen engine ─────────────────
    # VoxKage branding appears first — identical regardless of engine selected.
    if is_windows():
        os.system("cls")
    else:
        os.system("clear")

    print_banner()

    cmd = [_cli_exe]
    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    env["VOXKAGE_ACTIVE"] = "1"

    try:
        proc = subprocess.run(cmd, cwd=os.getcwd(), env=env)
    except FileNotFoundError:
        print(f"\n  {_c(255,80,80)}✗  {_not_found_msg}{RST}\n")
        _cleanup_fn()
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_fn()

    sys.exit(proc.returncode if 'proc' in dir() else 0)


def _start_ipc_server():
    """
    Start the Named Pipe IPC server in a daemon thread.

    When the Telegram watcher delivers a message via \\.\pipe\voxkage_ipc,
    the on_message callback is fired. It uses pyautogui to type the prompt
    into the VoxKage terminal window — exactly as if the user typed it —
    so VoxKage processes it visibly in front of the user.

    The server dies automatically when the VoxKage process exits (daemon).
    """
    def _on_telegram_message(source: str, text: str):
        """
        Called by IPCServer when a Telegram message arrives.
        Types the message into the VoxKage terminal window via pyautogui.
        """
        import time as _t
        try:
            import pyautogui
            import win32gui, win32con
            import ctypes

            # Find the VoxKage terminal window (title set by cmd_launch)
            hwnd = 0
            def _find_vk(h, _):
                nonlocal hwnd
                if win32gui.IsWindowVisible(h):
                    title = win32gui.GetWindowText(h).lower()
                    if "voxkage" in title:
                        hwnd = h
            win32gui.EnumWindows(_find_vk, None)

            if hwnd:
                # Restore if minimised
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    _t.sleep(0.3)
                # Bring to foreground
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
                _t.sleep(0.6)  # let window paint and accept input

            # Paste via clipboard for reliable unicode support
            try:
                import win32clipboard
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
                pyautogui.hotkey("ctrl", "v")
            except Exception:
                # Fallback: typewrite (ASCII safe)
                safe = text.encode("ascii", "replace").decode("ascii")
                pyautogui.typewrite(safe, interval=0.015)

            _t.sleep(0.2)
            pyautogui.press("enter")

        except ImportError:
            pass  # pyautogui / win32 not available — IPC delivered but couldn't inject
        except Exception as e:
            pass  # Non-critical — VoxKage still works

    try:
        from voxkage.ipc import IPCServer
        server = IPCServer(on_message=_on_telegram_message)
        server.start()
    except Exception:
        pass  # IPC unavailable — watcher will fall back to pyautogui directly


def _run_first_time_setup():
    """
    One-time post-install wizard. Runs on first `voxkage` launch after install.
    Handles steps that `pip install` cannot do on its own:

      1. playwright install chromium  — downloads the Chromium binary needed by
                                        browser_server.py / search_web tool.

    Marks completion with a stamp file at ~/.voxkage/.setup_done so it NEVER
    runs again on subsequent launches (instant startup for all future sessions).
    """
    stamp = _VOXKAGE_DIR / ".setup_done"
    if stamp.exists():
        return  # Already done — skip immediately

    print(f"\n  {_c(56,189,248)}✨{RST}  First-time VoxKage setup (this only happens once)...")

    # ── Step 1: playwright install chromium ───────────────────────────────────
    print(f"  {_c(71,85,105)}  → Installing Chromium browser for web automation...{RST}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            print(f"  {_c(34,197,94)}✓{RST}  Chromium ready.")
        else:
            # Non-fatal — browser tools won't work but everything else will
            print(f"  {_c(255,180,84)}⚠{RST}  Chromium install failed (search_web may not work).")
            print(f"  {_c(71,85,105)}  Fix: run `playwright install chromium` manually.{RST}")
    except Exception as e:
        print(f"  {_c(255,180,84)}⚠{RST}  Playwright setup skipped: {e}")

    # ── Mark setup as complete ─────────────────────────────────────────────────
    try:
        _VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
        stamp.write_text(__import__('voxkage').__version__, encoding="utf-8")
        print(f"  {_c(34,197,94)}✓{RST}  Setup complete. Starting VoxKage...\n")
    except Exception:
        pass  # Non-critical — will re-run next launch but that's fine


# ── VoxKage state dir (used by _run_first_time_setup) ─────────────────────────
_VOXKAGE_DIR = Path.home() / ".voxkage"


def _ensure_tray_running():
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(0.1)
        s.connect(("127.0.0.1", 49998))
        s.close()
        return  # Tray already running — do nothing
    except (_socket.timeout, ConnectionRefusedError, OSError):
        pass  # Not running yet — launch it

    tray_script = package_dir() / "tray" / "tray_app.py"
    if not tray_script.exists():
        return

    try:
        if is_windows():
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable

            # Capture stderr to a temp log so we can detect crashes
            log_path = voxkage_dir() / "tray_launch.log"
            with open(log_path, "w") as log_f:
                proc = subprocess.Popen(
                    [pythonw, str(tray_script)],
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                    stdout=log_f,
                    stderr=log_f,
                )
            # Give it 1.5s to start and bind its singleton socket
            import time as _time
            _time.sleep(1.5)

            # Confirm it actually started by trying the socket again
            try:
                s2 = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s2.settimeout(0.5)
                s2.connect(("127.0.0.1", 49998))
                s2.close()
            except Exception:
                # Failed — read the log for the real error
                try:
                    err = log_path.read_text(encoding="utf-8", errors="replace").strip()
                    if err:
                        print(f"  {_c(255,180,84)}⚠{RST}  Tray failed to start: {err[:200]}")
                except Exception:
                    pass
        else:
            subprocess.Popen(
                [sys.executable, str(tray_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception as e:
        pass  # Non-critical — VoxKage still works without the tray



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
            "  voxkage install <pack>   Install a capability pack (browser, rag, vision, docs_plus, full)\n"
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
    install_parser.add_argument("pack", help="Pack name: browser, rag, vision, docs_plus, full")

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