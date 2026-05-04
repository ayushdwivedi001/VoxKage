"""
MCP Server: System Control & App Launcher
Wraps automation/system_control.py and automation/app_launcher.py

Standalone — run directly:
    python mcp_servers/system_server.py
"""

import os
import sys
import asyncio
from datetime import datetime
import threading

# ── Path setup (must run before any local imports) ────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from _env import load_voxkage_env
load_voxkage_env()

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-system")

# ── Single worker thread + async queue ───────────────────────────────────────
# All GUI/COM tools are funnelled through one dedicated thread.
# This means parallel Gemini tool calls are automatically serialised —
# the second call waits in the queue instead of racing for the COM lock.
_WORKER_QUEUE: asyncio.Queue | None = None
_WORKER_THREAD: threading.Thread | None = None
_WORKER_LOOP: asyncio.AbstractEventLoop | None = None
_SETTLE_DELAY = 2.0   # seconds to wait after each GUI action (increased for stability)

def _ensure_worker():
    """Lazily spin up the dedicated COM worker thread and its event loop."""
    global _WORKER_QUEUE, _WORKER_THREAD, _WORKER_LOOP

    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return

    import pythoncom

    ready = threading.Event()

    def _thread_main():
        global _WORKER_LOOP, _WORKER_QUEUE
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        loop = asyncio.new_event_loop()
        _WORKER_LOOP = loop
        _WORKER_QUEUE = asyncio.Queue()
        ready.set()
        try:
            loop.run_forever()
        finally:
            pythoncom.CoUninitialize()

    _WORKER_THREAD = threading.Thread(target=_thread_main, daemon=True, name="vk-com-worker")
    _WORKER_THREAD.start()
    ready.wait(timeout=5)


async def _run_on_worker(fn) -> str:
    """
    Schedule a callable on the COM worker thread and await its result.
    Automatically adds a post-action settle delay so Windows processes
    each GUI event before the next tool call fires.
    """
    _ensure_worker()

    future: asyncio.Future = asyncio.get_event_loop().create_future()

    async def _task():
        try:
            result = fn()
            future.get_loop().call_soon_threadsafe(future.set_result, result)
        except Exception as exc:
            future.get_loop().call_soon_threadsafe(future.set_exception, exc)

    # Schedule on the worker's own event loop
    _WORKER_LOOP.call_soon_threadsafe(
        lambda: asyncio.ensure_future(_task(), loop=_WORKER_LOOP)
    )

    result = await future
    # Settle delay — gives Windows time to finish processing each GUI action
    await asyncio.sleep(_SETTLE_DELAY)
    return result


# ── Lazy imports (avoid import-time side effects) ─────────────────────────────
def _get_system():
    from automation.system_control import (
        set_volume, set_brightness, toggle_wifi, toggle_bluetooth,
        close_app, switch_to_app, toggle_hotspot, toggle_night_light, open_intel_dsa
    )
    return set_volume, set_brightness, toggle_wifi, toggle_bluetooth, close_app, switch_to_app, toggle_hotspot, toggle_night_light, open_intel_dsa

def _get_launcher():
    from automation.app_launcher import execute_special_command, open_app
    return execute_special_command, open_app


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_current_datetime() -> str:
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
async def system_control(action: str, level: int = None) -> str:
    """
    Controls PC hardware and power states.

    action options:
      set_volume     — requires level (0–100)
      set_brightness — requires level (0–100)
      wifi_on / wifi_off
      bluetooth_on / bluetooth_off
      hotspot_on / hotspot_off
      night_light_on / night_light_off
      intel_dsa
      shutdown / restart / sleep / hibernate / lock

    IMPORTANT: Call this tool one at a time. Do not queue multiple system_control
    calls simultaneously — each call must complete before the next begins.
    """
    def _run():
        set_volume, set_brightness, toggle_wifi, toggle_bluetooth, close_app, switch_to_app, toggle_hotspot, toggle_night_light, open_intel_dsa = _get_system()
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
        elif action == "hotspot_on":
            return toggle_hotspot(True)
        elif action == "hotspot_off":
            return toggle_hotspot(False)
        elif action == "night_light_on":
            return toggle_night_light(True)
        elif action == "night_light_off":
            return toggle_night_light(False)
        elif action == "intel_dsa":
            return open_intel_dsa()
        elif action in ("shutdown", "restart", "sleep", "hibernate", "lock"):
            return execute_special_command(action)
        else:
            return (
                f"Unknown action: {action!r}. Valid: set_volume, set_brightness, "
                "wifi_on, wifi_off, bluetooth_on, bluetooth_off, hotspot_on, hotspot_off, "
                "night_light_on, night_light_off, intel_dsa, shutdown, restart, sleep, hibernate, lock"
            )

    return await _run_on_worker(_run)


@mcp.tool()
async def open_application(app_name: str) -> str:
    """
    Launches an application or folder on the user's PC.

    Launches an application, folder, or website on the user's PC dynamically.
    No hardcoded configurations are needed. Provide the exact executable name,
    a valid Windows shortcut name, an absolute file path, or a website URL.

    IMPORTANT: Wait for this tool to return before calling switch_to_application
    or any other GUI tool — the app needs time to fully open first.
    """
    def _run():
        _, open_app = _get_launcher()
        return open_app(app_name)

    return await _run_on_worker(_run)


@mcp.tool()
async def close_application(target: str) -> str:
    """
    Safely closes a file, window, application, or folder process.
    target: The name of the file (e.g. "VoxKage app related information.txt"), 
            the application name (e.g. "chrome"), process name (e.g. "notepad.exe"),
            or exact folder path.

    This tool safely uses WM_CLOSE window messages to shut down specific file tabs 
    in VS Code/Notepad without killing the whole app, and uses COM objects to safely 
    close File Explorer folders without killing the Windows shell.

    IMPORTANT: Call one at a time — do not queue alongside other GUI tools.
    """
    def _run():
        from automation.system_control import safe_close_target
        return safe_close_target(target)

    return await _run_on_worker(_run)


@mcp.tool()
async def switch_to_application(window_name: str) -> str:
    """
    Switches focus to an already-open application window.
    Uses fuzzy matching on the window title.
    Example: switch_to_application("chrome"), switch_to_application("vs code")

    IMPORTANT: Only call this after open_application has fully returned —
    switching to a window that hasn't finished opening will fail.
    """
    def _run():
        set_volume, set_brightness, toggle_wifi, toggle_bluetooth, close_app, switch_to_app, toggle_hotspot, toggle_night_light, open_intel_dsa = _get_system()
        return switch_to_app(window_name)

    return await _run_on_worker(_run)


@mcp.tool()
async def run_shell_command(command: str) -> str:
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
    def _run():
        import subprocess as _sp

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
            pwsh_path = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            result = _sp.run(
                [pwsh_path, "-NoProfile", "-Command", command],
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.path.expanduser("~"),
                env=os.environ.copy(),
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

    # Shell commands don't need the GUI COM thread — run directly
    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    mcp.run()