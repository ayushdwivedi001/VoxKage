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
        # Windows Terminal, new PowerShell, and cmd with VT support
        return os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") or True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(r: int, g: int, b: int) -> str:
    """Generate ANSI 24-bit foreground color code."""
    if not _supports_color():
        return ""
    return f"\033[38;2;{r};{g};{b}m"


RST = "\033[0m" if _supports_color() else ""


# ── ASCII Banner ──────────────────────────────────────────────────────────────

def print_banner():
    """Print the VoxKage ASCII art banner with theme-aware colors."""
    # Enable ANSI on Windows
    if is_windows():
        os.system("chcp 65001 >nul 2>&1")
        # Enable VT processing
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    C1 = _c(14, 165, 233)
    C2 = _c(34, 211, 238)
    C3 = _c(103, 232, 249)
    C4 = _c(165, 243, 252)
    C5 = _c(186, 230, 253)
    DIM = _c(30, 58, 138)
    META = _c(71, 85, 105)
    TAG = _c(14, 116, 144)
    VAL = _c(56, 189, 248)
    PROMPT = _c(2, 132, 199)
    GREEN = _c(34, 197, 94)
    NOTICE = _c(100, 116, 139)

    # Try to load user theme from gemini settings
    gemini_settings = Path.home() / ".gemini" / "settings.json"
    theme = "Default Dark"
    try:
        if gemini_settings.exists():
            data = json.loads(gemini_settings.read_text(encoding="utf-8"))
            theme = data.get("theme", theme)
    except Exception:
        pass

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

    art = f"""
 {C1} ██╗   ██╗ ██████╗ ██╗  ██╗██╗  ██╗ █████╗  ██████╗ ███████╗{RST}
 {C2} ██║   ██║██╔═══██╗╚██╗██╔╝██║ ██╔╝██╔══██╗██╔════╝ ██╔════╝{RST}
 {C3} ██║   ██║██║   ██║ ╚███╔╝ █████╔╝ ███████║██║  ███╗█████╗  {RST}
 {C4} ╚██╗ ██╔╝██║   ██║ ██╔██╗ ██╔═██╗ ██╔══██║██║   ██║██╔══╝  {RST}
 {C5}  ╚████╔╝ ╚██████╔╝██╔╝ ██╗██║  ██╗██║  ██║╚██████╔╝███████╗{RST}
 {DIM}   ╚═══╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝{RST}

 {META} ──────────────────────────────────────────────────────────────{RST}
  {TAG}◈{RST}  {VAL}OS Agentic AI{RST}    {META}│{RST}    {TAG}◈{RST}  {VAL}Autonomous  ·  Persistent  ·  Aware{RST}
 {META} ──────────────────────────────────────────────────────────────{RST}

 {META} session  {PROMPT}›{RST}  {VAL}{cwd}{RST}
 {META} theme    {PROMPT}›{RST}  {VAL}{theme}{RST}
 {META} engine   {PROMPT}›{RST}  {VAL}{model}{RST}
 {META} status   {PROMPT}›{RST}  {GREEN}● ready{RST}

 {META} ──────────────────────────────────────────────────────────────{RST}
 {NOTICE}  VoxKage uses Gemini CLI as its inference backend. All intelligence,{RST}
 {NOTICE}  memory, and agentic behavior is orchestrated by VoxKage core.{RST}
 {META} ──────────────────────────────────────────────────────────────{RST}
"""
    try:
        sys.stdout.buffer.write(art.encode('utf-8') + b'\n')
        sys.stdout.flush()
    except AttributeError:
        print(art.encode('utf-8', 'replace').decode('utf-8'), flush=True)


# ── Init Command ──────────────────────────────────────────────────────────────

def cmd_init():
    """First-time setup wizard."""
    print()
    print(f"  {_c(14,165,233)}VoxKage Setup{RST}")
    print(f"  {_c(71,85,105)}─────────────────────────────────────{RST}")
    print()

    # 1. Platform check
    if not is_supported_platform():
        print(f"  {_c(255,80,80)}✗  VoxKage currently supports Windows and macOS only.{RST}")
        sys.exit(1)

    plat = "Windows" if is_windows() else "macOS"
    print(f"  {_c(34,197,94)}✓{RST}  Platform: {plat}")

    # 2. Create ~/.voxkage/ structure
    voxkage_dir()
    data_dir()
    task_logs_dir()
    gemini_dir()
    print(f"  {_c(34,197,94)}✓{RST}  Data directory: {voxkage_dir()}")

    # 3. Create .env if missing
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
                "# GITHUB_TOKEN=\n",
                encoding="utf-8",
            )
        print(f"  {_c(34,197,94)}✓{RST}  Created .env template")
    else:
        print(f"  {_c(34,197,94)}✓{RST}  .env already exists")

    # 4. Create default config.json if missing
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

    # 5. Check Node.js / npm
    npm = find_npm()
    if not npm:
        print()
        print(f"  {_c(255,180,84)}⚠  npm not found.{RST} VoxKage needs Node.js for the Gemini CLI backend.")
        print(f"     Install Node.js from: {_c(56,189,248)}https://nodejs.org/{RST}")
        print(f"     Then re-run: {_c(56,189,248)}voxkage init{RST}")
        print()
        return
    print(f"  {_c(34,197,94)}✓{RST}  npm found: {npm}")

    # 6. Check / install Gemini CLI
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

    # 7. Install Playwright Chromium
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

    # 8. Generate settings.json
    _generate_settings_json()
    print(f"  {_c(34,197,94)}✓{RST}  Generated MCP settings")

    # 9. Generate GEMINI.md
    _generate_gemini_md()
    print(f"  {_c(34,197,94)}✓{RST}  Generated agent instructions")

    # 10. Install platform-specific deps
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
    """Install platform-specific optional dependencies."""
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
    """Remove any settings that would prevent MCP tools from working.

    The critical one is tools.core — if set to a whitelist like
    ["run_shell_command"], Gemini CLI only exposes that core tool
    and silently hides all MCP tools from the model's function-calling
    schema.  The model then falls back to running MCP tool names as
    shell commands, which obviously fails.
    """
    tools = settings.get("tools", {})
    if isinstance(tools, dict) and "core" in tools:
        del tools["core"]
    return settings


def _generate_settings_json():
    """Generate MCP server settings and write to all locations Gemini CLI reads.

    Gemini CLI merges settings from two places (local overrides global):
      1. Global:  ~/.gemini/settings.json
      2. Local:   <cwd>/.gemini/settings.json

    We update BOTH so that the MCP servers are always available regardless
    of which directory the user launches VoxKage from.
    """
    py = python_exe()
    pkg = str(package_dir())

    # Core MCP servers (always included)
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

    # Plugin MCP servers (only if configured)
    try:
        from voxkage.plugins.registry import get_configured_plugin_servers
        plugin_servers = get_configured_plugin_servers()
        servers.update(plugin_servers)
    except Exception:
        pass

    # ── Write to global settings (~/.gemini/settings.json) ────────────────
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

    # ── Also patch any LOCAL .gemini/settings.json at the current CWD ─────
    # This prevents a stale local config from overriding our global settings
    # with a tools.core whitelist that blocks MCP tool exposure.
    _patch_local_settings(servers)


def _generate_gemini_md():
    """Generate GEMINI.md from the template, replacing placeholders."""
    tpl_path = template_path("GEMINI.md.template")
    if not tpl_path.exists():
        # Dev mode: copy the existing GEMINI.md from repo root
        repo_gemini = package_dir().parent / "GEMINI.md"
        if repo_gemini.exists():
            content = repo_gemini.read_text(encoding="utf-8")
        else:
            content = "# VoxKage Agent Instructions\n\nNo template found.\n"
    else:
        content = tpl_path.read_text(encoding="utf-8")

    # Replace placeholders
    content = content.replace("{{VOXKAGE_HOME}}", str(voxkage_dir()))
    content = content.replace("{{USER_HOME}}", str(Path.home()))
    content = content.replace("{{PLATFORM}}", "windows" if is_windows() else "darwin")
    content = content.replace("{{PACKAGE_DIR}}", str(package_dir()))

    gemini_md_path().write_text(content, encoding="utf-8")


# ── Tray Command ──────────────────────────────────────────────────────────────

def cmd_tray():
    """Launch the system tray icon in the background."""
    tray_module = "voxkage.tray.tray_app"
    if is_windows():
        # Use pythonw to avoid console window
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        subprocess.Popen(
            [pythonw, "-m", tray_module],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
    else:
        # macOS: launch as background process
        subprocess.Popen(
            [sys.executable, "-m", tray_module],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(f"  {_c(34,197,94)}✓{RST}  System tray launched")


# ── Plugins Command ───────────────────────────────────────────────────────────

def cmd_plugins(args):
    """List or configure plugins."""
    from voxkage.plugins.registry import list_plugins, add_plugin

    if args.plugin_action == "add" and args.plugin_name:
        add_plugin(args.plugin_name)
    else:
        list_plugins()


# ── Launch Command (default) ─────────────────────────────────────────────────

def _patch_local_settings(servers: dict | None = None):
    """Patch any local .gemini/settings.json at CWD to fix MCP config.

    Ensures:
      1. tools.core whitelist is removed (it blocks MCP tool exposure)
      2. MCP server entries are up-to-date with VOXKAGE_HOME env
    """
    local_gemini = Path(os.getcwd()) / ".gemini" / "settings.json"
    if not local_gemini.exists():
        return

    try:
        local_settings = json.loads(local_gemini.read_text(encoding="utf-8"))
    except Exception:
        return

    changed = False

    # Remove tools.core restriction
    tools = local_settings.get("tools", {})
    if isinstance(tools, dict) and "core" in tools:
        del tools["core"]
        if "disableLLMCorrection" not in tools:
            tools["disableLLMCorrection"] = False
        changed = True

    # Update MCP servers if provided
    if servers:
        if "mcpServers" not in local_settings:
            local_settings["mcpServers"] = {}
        local_settings["mcpServers"].update(servers)
        changed = True

    # Ensure all existing MCP server entries have VOXKAGE_HOME env
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
    """Print banner and launch Gemini CLI session."""
    # Auto-init if first run
    if not config_path().exists():
        print(f"  {_c(56,189,248)}First run detected — running setup...{RST}")
        print()
        cmd_init()

    # ── Ensure MCP settings are fresh and sanitized ──────────────────────
    # This is the critical step: regenerate settings.json on every launch
    # to ensure MCP servers are registered and tools.core is never set.
    try:
        _generate_settings_json()
    except Exception:
        pass  # Non-fatal — settings may already be correct

    # Auto-start tray in background (if not already running)
    _ensure_tray_running()

    print_banner()

    # Load model from config
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
    """Start the tray if not already running (singleton check via port)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1)
        s.connect(("127.0.0.1", 49998))
        s.close()
        # Port is bound — tray is running
    except (ConnectionRefusedError, OSError, socket.timeout):
        # Tray not running — start it silently
        try:
            cmd_tray()
        except Exception:
            pass  # Non-critical


# ── Main Entry Point ─────────────────────────────────────────────────────────

def main():
    """CLI entry point — registered as console_scripts in pyproject.toml."""
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

    # voxkage init
    subparsers.add_parser("init", help="Run first-time setup wizard")

    # voxkage tray
    subparsers.add_parser("tray", help="Launch/restore system tray icon")

    # voxkage plugins [add <name>]
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
        # Default: launch VoxKage session
        cmd_launch(remaining if remaining else None)


if __name__ == "__main__":
    main()
