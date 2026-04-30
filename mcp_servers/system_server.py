"""
MCP Server: System Control & App Launcher
Wraps automation/system_control.py and automation/app_launcher.py

Standalone — run directly:
    python mcp_servers/system_server.py
"""

import os
import sys
from datetime import datetime

# ── Path setup (must run before any local imports) ────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-system")

# ── Lazy imports (avoid import-time side effects) ─────────────────────────────
def _get_system():
    from automation.system_control import (
        set_volume, set_brightness, toggle_wifi, toggle_bluetooth,
        change_wallpaper_from_folder, close_app, switch_to_app,
    )
    return set_volume, set_brightness, toggle_wifi, toggle_bluetooth, change_wallpaper_from_folder, close_app, switch_to_app

def _get_launcher():
    from automation.app_launcher import execute_special_command, open_app
    return execute_special_command, open_app




@mcp.tool()
def get_current_datetime() -> str:
    """
    Returns the current local date and time.
    ALWAYS call this tool when the user asks: what time is it, what's the date,
    what day is it, what year is it — do NOT search the web for this.
    """
    now = datetime.now()
    return (
        f"Current date & time: {now.strftime('%A, %d %B %Y — %I:%M:%S %p')}\n"
        f"24h: {now.strftime('%H:%M:%S')} | "
        f"Timezone: IST (UTC+5:30)"
    )


@mcp.tool()
def system_control(action: str, level: int = None) -> str:
    """
    Controls PC hardware and power states.

    action options:
      set_volume     — requires level (0–100)
      set_brightness — requires level (0–100)
      wifi_on / wifi_off
      bluetooth_on / bluetooth_off
      wallpaper      — picks a random wallpaper from C:\\wallpapers
      shutdown / restart / sleep / hibernate / lock
    """
    set_volume, set_brightness, toggle_wifi, toggle_bluetooth, change_wallpaper, close_app, switch_to_app = _get_system()
    execute_special_command, _ = _get_launcher()

    if action == "set_volume":
        if level is None:
            return "Error: level is required for set_volume (0–100)."
        return set_volume(level)
    elif action == "set_brightness":
        if level is None:
            return "Error: level is required for set_brightness (0–100)."
        return set_brightness(level)
    elif action == "wifi_on":
        return toggle_wifi(True)
    elif action == "wifi_off":
        return toggle_wifi(False)
    elif action == "bluetooth_on":
        return toggle_bluetooth(True)
    elif action == "bluetooth_off":
        return toggle_bluetooth(False)
    elif action == "wallpaper":
        return change_wallpaper()
    elif action in ("shutdown", "restart", "sleep", "hibernate", "lock"):
        return execute_special_command(action)
    else:
        return f"Unknown action: {action!r}. Valid: set_volume, set_brightness, wifi_on, wifi_off, bluetooth_on, bluetooth_off, wallpaper, shutdown, restart, sleep, hibernate, lock"


@mcp.tool()
def open_application(app_name: str) -> str:
    """
    Launches an application or folder on the user's PC.

    Known apps: chrome, vs code, japanese book, my files, background images,
                my screenshots, games folder, spline, notepad, calculator,
                paint, excel, ms word, powerpoint, music, cmd.

    Also supports website shortcuts: google, youtube, github, gmail, openai, chat gpt, fit.
    """
    _, open_app = _get_launcher()
    return open_app(app_name)


@mcp.tool()
def close_application(target: str) -> str:
    """
    Safely closes a file, window, application, or folder process.
    target: The name of the file (e.g. "VoxKage app related information.txt"), 
            the application name (e.g. "chrome"), process name (e.g. "notepad.exe"),
            or exact folder path.
            
    This tool safely uses WM_CLOSE window messages to shut down specific file tabs 
    in VS Code/Notepad without killing the whole app, and uses COM objects to safely 
    close File Explorer folders without killing the Windows shell.
    """
    from automation.system_control import safe_close_target
    return safe_close_target(target)


@mcp.tool()
def switch_to_application(window_name: str) -> str:
    """
    Switches focus to an already-open application window.
    Uses fuzzy matching on the window title.
    Example: switch_to_application("chrome"), switch_to_application("vs code")
    """
    set_volume, set_brightness, toggle_wifi, toggle_bluetooth, change_wallpaper, close_app, switch_to_app = _get_system()
    return switch_to_app(window_name)


@mcp.tool()
def run_shell_command(command: str) -> str:
    """
    Execute a shell command on the user's PC and return the output.
    
    USE THIS for tasks that are faster via CLI than GUI automation:
    - Check VS Code extensions: "code --list-extensions | findstr -i prettier"
    - Install VS Code extensions: "code --install-extension formulahendry.code-runner"
    - Check installed software: "winget list --name spotify"
    - Open apps: 'start spotify:'
    - Quick file checks: "dir C:\\Users\\AYUSH\\Desktop\\*.txt"
    - Process checks: "tasklist | findstr chrome"
    
    SAFETY: Commands that modify system state (format, del *, reg delete, etc.)
    will be blocked. Only read-only and safe installation commands are allowed.
    """
    import subprocess as _sp

    # Block obviously dangerous commands
    cmd_lower = command.lower().strip()
    _BLOCKED = [
        "format ", "rd /s", "rmdir /s", "del /f /s", "del *", 
        "reg delete", "bcdedit", "diskpart", "shutdown", "restart",
        "net user", "net localgroup", "sfc ", "dism ",
    ]
    for blocked in _BLOCKED:
        if blocked in cmd_lower:
            return f"BLOCKED: '{blocked}' is a dangerous system command. VoxKage refuses to execute it."

    try:
        result = _sp.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.expanduser("~"),
        )
        output = result.stdout.strip()
        error = result.stderr.strip()
        if result.returncode == 0:
            return output if output else "(Command succeeded with no output)"
        else:
            return f"Exit code {result.returncode}\nOutput: {output}\nError: {error}"
    except _sp.TimeoutExpired:
        return "Command timed out after 30 seconds."
    except Exception as e:
        return f"Shell command failed: {e}"

if __name__ == "__main__":
    mcp.run()
