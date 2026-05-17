"""
MCP Server: System Control & App Launcher  (v2 — 50+ tools)
"""

import os, sys, asyncio, threading
from datetime import datetime

# Optional: pycaw for precise audio control (Windows Core Audio API)
try:
    from ctypes import cast as _ctypes_cast, POINTER as _POINTER
    from comtypes import CLSCTX_ALL as _CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities as _AudioUtilities, IAudioEndpointVolume as _IAudioEndpointVolume
    _PYCAW_AVAILABLE = True
except Exception:
    _PYCAW_AVAILABLE = False

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-system")

# ── Single COM worker thread ───────────────────────────────────────────────────
_WORKER_QUEUE = None
_WORKER_THREAD = None
_WORKER_LOOP = None
_SETTLE_DELAY = 0.8  # reduced from 2.0s — most GUI actions don't need 2s

def _ensure_worker():
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
    _ensure_worker()
    future = asyncio.get_event_loop().create_future()
    async def _task():
        try:
            result = fn()
            future.get_loop().call_soon_threadsafe(future.set_result, result)
        except Exception as exc:
            future.get_loop().call_soon_threadsafe(future.set_exception, exc)
    _WORKER_LOOP.call_soon_threadsafe(lambda: asyncio.ensure_future(_task(), loop=_WORKER_LOOP))
    result = await future
    await asyncio.sleep(_SETTLE_DELAY)
    return result

# ── Lazy import helper ─────────────────────────────────────────────────────────
def _sc():
    import voxkage.automation.system_control as m
    return m

def _launcher():
    from voxkage.automation.app_launcher import execute_special_command, open_app
    return execute_special_command, open_app

# ══════════════════════════════════════════════════════════════════════════════
# DATETIME
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_current_datetime() -> str:
    """Returns the current local date and time.
    ALWAYS call this when user asks: what time is it, what date, what day, what year."""
    now = datetime.now()
    return (
        f"Current date & time: {now.strftime('%A, %d %B %Y — %I:%M:%S %p')}\n"
        f"24h: {now.strftime('%H:%M:%S')} | Timezone: IST (UTC+5:30)"
    )

# ══════════════════════════════════════════════════════════════════════════════
# VOLUME & AUDIO
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def set_volume(level: int) -> str:
    """Set system volume to an exact level.
    Parameters: level = 0 to 100 (e.g. 50 for 50%)"""
    return await _run_on_worker(lambda: _sc().set_volume(level))

@mcp.tool()
async def get_volume() -> str:
    """Get the current system volume level and whether it is muted."""
    return await asyncio.to_thread(_sc().get_volume)

@mcp.tool()
async def toggle_mute(mute: bool) -> str:
    """Mute or unmute system audio without changing the volume level.
    Parameters: mute = True to mute, False to unmute"""
    def _run():
        if _PYCAW_AVAILABLE:
            try:
                devices = _AudioUtilities.GetSpeakers()
                interface = devices.Activate(_IAudioEndpointVolume._iid_, _CLSCTX_ALL, None)
                vol = _ctypes_cast(interface, _POINTER(_IAudioEndpointVolume))
                vol.SetMute(1 if mute else 0, None)
                return f"Audio {'muted' if mute else 'unmuted'}."
            except Exception:
                pass
        import pyautogui
        pyautogui.press("volumemute")
        return f"Audio {'muted' if mute else 'unmuted'} (keypress fallback)."
    return await _run_on_worker(_run)

@mcp.tool()
async def set_audio_output_device(device_name: str) -> str:
    """Switch audio output to a different device (speakers, headphones, HDMI, etc.).
    Parameters: device_name = partial name of the device (e.g. 'headphones', 'speakers', 'HDMI')"""
    return await asyncio.to_thread(_sc().set_audio_output, device_name)

@mcp.tool()
async def mute_microphone(mute: bool) -> str:
    """Mute or unmute the microphone.
    Parameters: mute = True to mute mic, False to unmute"""
    return await asyncio.to_thread(_sc().mute_microphone, mute)

# ══════════════════════════════════════════════════════════════════════════════
# BRIGHTNESS & DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def set_brightness(level: int) -> str:
    """Set monitor brightness to an exact level.
    Parameters: level = 0 to 100. Works on built-in laptop displays via WMI.
    For external monitors uses DDC/CI (requires nircmd)."""
    return await asyncio.to_thread(_sc().set_brightness, level)

@mcp.tool()
async def get_brightness() -> str:
    """Get the current monitor brightness level (laptop displays only)."""
    return await asyncio.to_thread(_sc().get_brightness)

@mcp.tool()
async def toggle_night_light(enable: bool) -> str:
    """Toggle Windows Night Light (blue light filter) via registry — no UI needed.
    Parameters: enable = True to turn on, False to turn off"""
    return await asyncio.to_thread(_sc().toggle_night_light, enable)

@mcp.tool()
async def toggle_dark_mode(dark: bool) -> str:
    """Switch Windows between Dark and Light mode (apps + system).
    Parameters: dark = True for dark mode, False for light mode"""
    return await asyncio.to_thread(_sc().toggle_dark_mode, dark)

# ══════════════════════════════════════════════════════════════════════════════
# POWER & SYSTEM STATE
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def power_action(action: str) -> str:
    """Perform a power state action on the PC.
    Parameters:
      action: 'shutdown' | 'restart' | 'sleep' | 'hibernate' | 'lock'"""
    def _run():
        execute_special_command, _ = _launcher()
        return execute_special_command(action)
    return await _run_on_worker(_run)

@mcp.tool()
async def schedule_shutdown(minutes: int) -> str:
    """Schedule an automatic shutdown after N minutes.
    Parameters: minutes = number of minutes before shutdown (e.g. 30)"""
    return await asyncio.to_thread(_sc().schedule_shutdown, minutes)

@mcp.tool()
async def cancel_scheduled_shutdown() -> str:
    """Cancel any pending shutdown or restart timer that was previously scheduled."""
    return await asyncio.to_thread(_sc().cancel_scheduled_shutdown)

@mcp.tool()
async def set_power_plan(mode: str) -> str:
    """Set the Windows power plan.
    Parameters:
      mode: 'performance' | 'balanced' | 'saver'"""
    return await asyncio.to_thread(_sc().set_power_profile, mode)

@mcp.tool()
async def get_battery_status() -> str:
    """Get battery percentage, charging state, and estimated time remaining.
    Returns 'no battery' message on desktop PCs."""
    return await asyncio.to_thread(_sc().get_battery_status)

# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM INFO & MONITORING
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_system_info() -> str:
    """Get a full system snapshot: OS, CPU, RAM, GPU, disk usage, and uptime — all in one call."""
    return await asyncio.to_thread(_sc().get_system_info)

@mcp.tool()
async def get_disk_usage() -> str:
    """Get disk usage for all drives showing free space, total size, and percent used."""
    return await asyncio.to_thread(_sc().get_disk_usage)

@mcp.tool()
async def get_system_uptime() -> str:
    """Get how long the PC has been running since last boot."""
    return await asyncio.to_thread(_sc().system_uptime)

@mcp.tool()
async def get_running_processes(sort_by: str = "cpu", top_n: int = 15) -> str:
    """List the top processes consuming the most CPU or RAM.
    Parameters:
      sort_by: 'cpu' or 'ram'
      top_n:   number of results to show (default 15)"""
    return await asyncio.to_thread(_sc().get_running_processes, sort_by, top_n)

@mcp.tool()
async def kill_process(name_or_pid: str) -> str:
    """Kill a running process by name or PID. Protected processes (System, explorer) are blocked.
    Parameters: name_or_pid = process name (e.g. 'chrome') or PID number"""
    return await asyncio.to_thread(_sc().kill_process_by_name, name_or_pid)

@mcp.tool()
async def suspend_process(name: str) -> str:
    """Pause (suspend) a process without killing it. Useful to free CPU temporarily.
    Parameters: name = process name (e.g. 'chrome')"""
    return await asyncio.to_thread(_sc().suspend_process, name)

@mcp.tool()
async def resume_process(name: str) -> str:
    """Resume a previously suspended (paused) process.
    Parameters: name = process name"""
    return await asyncio.to_thread(_sc().resume_process, name)

@mcp.tool()
async def boost_process_priority(name: str) -> str:
    """Set a process to High CPU priority for better performance.
    Parameters: name = process name (e.g. 'game.exe', 'blender')"""
    return await asyncio.to_thread(_sc().boost_process, name)

@mcp.tool()
async def get_startup_programs() -> str:
    """List all programs configured to launch at Windows startup."""
    return await asyncio.to_thread(_sc().get_startup_programs)

# ══════════════════════════════════════════════════════════════════════════════
# CONNECTIVITY
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def toggle_wifi(enable: bool) -> str:
    """Turn Wi-Fi on or off.
    Parameters: enable = True to turn on, False to turn off"""
    return await asyncio.to_thread(_sc().toggle_wifi, enable)

@mcp.tool()
async def toggle_bluetooth(enable: bool) -> str:
    """Turn Bluetooth on or off using WinRT (no admin required).
    Parameters: enable = True to turn on, False to turn off"""
    return await asyncio.to_thread(_sc().toggle_bluetooth, enable)

@mcp.tool()
async def toggle_hotspot(enable: bool) -> str:
    """Turn Mobile Hotspot on or off.
    Parameters: enable = True to turn on, False to turn off"""
    return await asyncio.to_thread(_sc().toggle_hotspot, enable)

@mcp.tool()
async def toggle_airplane_mode(enable: bool) -> str:
    """Enable or disable Airplane Mode (kills all wireless radios).
    Parameters: enable = True to enable airplane mode, False to disable"""
    return await asyncio.to_thread(_sc().toggle_airplane_mode, enable)

@mcp.tool()
async def get_network_status() -> str:
    """Get current network info: IP address, gateway, DNS servers, and public IP."""
    return await asyncio.to_thread(_sc().get_network_status)

@mcp.tool()
async def get_wifi_networks() -> str:
    """Scan and list all available Wi-Fi networks with signal strength."""
    return await asyncio.to_thread(_sc().get_wifi_networks)

@mcp.tool()
async def connect_wifi(ssid: str, password: str = "") -> str:
    """Connect to a Wi-Fi network.
    Parameters:
      ssid:     network name
      password: Wi-Fi password (leave empty for open networks)"""
    return await asyncio.to_thread(_sc().connect_wifi, ssid, password)

@mcp.tool()
async def ping_host(host: str = "8.8.8.8", count: int = 4) -> str:
    """Ping a host to check latency and packet loss.
    Parameters:
      host:  IP or domain (default 8.8.8.8 = Google DNS)
      count: number of ping packets to send"""
    return await asyncio.to_thread(_sc().ping_host, host, count)

@mcp.tool()
async def get_open_ports() -> str:
    """List all open listening TCP ports and which process is using each port."""
    return await asyncio.to_thread(_sc().get_open_ports)

@mcp.tool()
async def run_network_speed_test() -> str:
    """Run a network speed test and return download/upload speeds and ping.
    Requires speedtest-cli: pip install speedtest-cli"""
    return await asyncio.to_thread(_sc().run_speed_test)

@mcp.tool()
async def flush_dns() -> str:
    """Flush the DNS cache to fix domain resolution issues."""
    return await asyncio.to_thread(_sc().flush_dns)

# ══════════════════════════════════════════════════════════════════════════════
# APP & WINDOW MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def open_application(app_name: str) -> str:
    """Launch any application, folder, file, or URL.
    Parameters: app_name = app name, file path, or URL (e.g. 'chrome', 'notepad', 'C:/file.txt')"""
    def _run():
        _, open_app = _launcher()
        return open_app(app_name)
    return await _run_on_worker(_run)

@mcp.tool()
async def close_application(target: str) -> str:
    """Close an application, window, or process by name.
    Parameters: target = app name or process (e.g. 'chrome', 'notepad.exe')"""
    def _run():
        from voxkage.automation.system_control import safe_close_target
        return safe_close_target(target)
    return await _run_on_worker(_run)

@mcp.tool()
async def switch_to_application(window_name: str) -> str:
    """Switch focus to an already-open window using fuzzy title matching.
    Parameters: window_name = partial window title (e.g. 'chrome', 'vs code')"""
    def _run():
        return _sc().switch_to_app(window_name)
    return await _run_on_worker(_run)

@mcp.tool()
async def list_open_windows() -> str:
    """List all currently open and visible application windows."""
    return await asyncio.to_thread(_sc().list_open_windows)

@mcp.tool()
async def minimize_window(title: str) -> str:
    """Minimize a window by partial title match.
    Parameters: title = part of the window title (e.g. 'chrome', 'notepad')"""
    return await _run_on_worker(lambda: _sc().minimize_window(title))

@mcp.tool()
async def maximize_window(title: str) -> str:
    """Maximize a window by partial title match.
    Parameters: title = part of the window title"""
    return await _run_on_worker(lambda: _sc().maximize_window(title))

@mcp.tool()
async def tile_windows(layout: str = "side_by_side") -> str:
    """Arrange open windows in a layout.
    Parameters:
      layout: 'side_by_side' | 'stack' | 'quad'"""
    return await _run_on_worker(lambda: _sc().tile_windows(layout))

@mcp.tool()
async def take_screenshot(save_path: str = "") -> str:
    """Take a full-screen screenshot and save it as a PNG.
    Parameters: save_path = where to save (default: Desktop with timestamp)"""
    return await asyncio.to_thread(_sc().take_screenshot, save_path)

@mcp.tool()
async def get_installed_apps(search: str = "") -> str:
    """List all installed applications. Optionally filter by name.
    Parameters: search = optional name filter (e.g. 'python', 'adobe')"""
    return await asyncio.to_thread(_sc().get_installed_apps, search)

# ══════════════════════════════════════════════════════════════════════════════
# CLIPBOARD & KEYBOARD
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_clipboard() -> str:
    """Read the current text content of the clipboard."""
    return await asyncio.to_thread(_sc().get_clipboard_content)

@mcp.tool()
async def set_clipboard(text: str) -> str:
    """Write text to the clipboard so the user can paste it anywhere.
    Parameters: text = content to copy to clipboard"""
    return await asyncio.to_thread(_sc().set_clipboard_content, text)

@mcp.tool()
async def type_text(text: str, delay_ms: int = 30) -> str:
    """Type text into the currently focused window (simulates keyboard input).
    Parameters:
      text:     the text to type
      delay_ms: milliseconds between keystrokes (default 30)"""
    return await _run_on_worker(lambda: _sc().type_text_input(text, delay_ms))

@mcp.tool()
async def press_hotkey(keys: str) -> str:
    """Press a keyboard shortcut.
    Parameters: keys = key combination with + separator (e.g. 'ctrl+c', 'alt+f4', 'win+d', 'ctrl+alt+t')"""
    return await _run_on_worker(lambda: _sc().press_keyboard_hotkey(keys))

# ══════════════════════════════════════════════════════════════════════════════
# MAINTENANCE & CLEANUP
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def clear_temp_files() -> str:
    """Delete temporary files from %TEMP% to free up disk space."""
    return await asyncio.to_thread(_sc().clear_temp_files)

@mcp.tool()
async def get_largest_files(directory: str, count: int = 10) -> str:
    """Find the largest files in a directory tree to identify space hogs.
    Parameters:
      directory: folder path (e.g. 'C:/Users/AYUSH/Downloads' or '~/Downloads')
      count:     number of results (default 10)"""
    return await asyncio.to_thread(_sc().get_largest_files, directory, count)

@mcp.tool()
async def get_folder_size(path: str) -> str:
    """Calculate the total size of a folder and how many files it contains.
    Parameters: path = folder path"""
    return await asyncio.to_thread(_sc().get_folder_size, path)

@mcp.tool()
async def toggle_hidden_files(show: bool) -> str:
    """Show or hide hidden files and folders in Windows Explorer.
    Parameters: show = True to make hidden files visible, False to hide them"""
    return await asyncio.to_thread(_sc().toggle_hidden_files, show)

@mcp.tool()
async def toggle_focus_mode(enable: bool) -> str:
    """Enable or disable Focus Mode (silences all toast notifications).
    Parameters: enable = True to silence notifications, False to restore them"""
    return await asyncio.to_thread(_sc().toggle_focus_mode, enable)

@mcp.tool()
async def update_all_software() -> str:
    """Trigger silent background updates for all installed apps via winget."""
    return await asyncio.to_thread(_sc().update_all_software)

# ══════════════════════════════════════════════════════════════════════════════
# SHELL COMMAND (power user escape hatch)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def run_shell_command(command: str) -> str:
    """Execute a PowerShell command and return its output.
    Use for tasks faster via CLI than GUI:
      - Check installed extensions: 'code --list-extensions'
      - List files: 'dir C:\\Users\\AYUSH\\Desktop'
      - Check processes: 'tasklist | findstr chrome'
      - winget queries: 'winget list --name python'
    SAFETY: Dangerous commands (format, del *, reg delete) are blocked by Shield Protocol."""
    def _run():
        import subprocess as _sp
        try:
            from voxkage.shield import shield_gate_command
            block = shield_gate_command(command)
            if block:
                return block
        except ImportError:
            cmd_lower = command.lower()
            BLOCKED = ["format ", "rd /s", "rmdir /s", "del /f /s", "del *",
                       "reg delete", "bcdedit", "diskpart", "net user", "net localgroup"]
            for b in BLOCKED:
                if b in cmd_lower:
                    return f"BLOCKED: '{b}' is a dangerous command."
        try:
            pwsh = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            r = _sp.run([pwsh, "-NoProfile", "-Command", command],
                       shell=False, capture_output=True, text=True, timeout=30,
                       cwd=os.path.expanduser("~"), env=os.environ.copy())
            out = r.stdout.strip()
            err = r.stderr.strip()
            if r.returncode == 0:
                return out or "(Command succeeded with no output)"
            return f"Exit {r.returncode}\n{out}\n{err}"
        except _sp.TimeoutExpired:
            return "Command timed out after 30 seconds."
        except Exception as e:
            return f"Shell command failed: {e}"
    return await asyncio.to_thread(_run)


# ══════════════════════════════════════════════════════════════════════════════
# BACKWARDS COMPAT — keep old system_control mega-tool so nothing breaks
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def system_control(action: str, level: int = None) -> str:
    """[LEGACY] Hardware and power state control via action string.
    Prefer the individual atomic tools (set_volume, set_brightness, etc.) for reliability.
    action: set_volume, set_brightness, wifi_on/off, bluetooth_on/off,
            hotspot_on/off, night_light_on/off, shutdown, restart, sleep, hibernate, lock"""
    def _run():
        m = _sc()
        execute_special_command, _ = _launcher()
        if action == "set_volume":
            return m.set_volume(level) if level is not None else "Error: level required"
        elif action == "set_brightness":
            return m.set_brightness(level) if level is not None else "Error: level required"
        elif action == "wifi_on":   return m.toggle_wifi(True)
        elif action == "wifi_off":  return m.toggle_wifi(False)
        elif action == "bluetooth_on":  return m.toggle_bluetooth(True)
        elif action == "bluetooth_off": return m.toggle_bluetooth(False)
        elif action == "hotspot_on":    return m.toggle_hotspot(True)
        elif action == "hotspot_off":   return m.toggle_hotspot(False)
        elif action == "night_light_on":  return m.toggle_night_light(True)
        elif action == "night_light_off": return m.toggle_night_light(False)
        elif action in ("shutdown","restart","sleep","hibernate","lock"):
            return execute_special_command(action)
        return f"Unknown action: {action!r}"
    return await _run_on_worker(_run)


if __name__ == "__main__":
    mcp.run()
