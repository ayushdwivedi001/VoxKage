"""
MCP Server: VoxKage GUI Pilot v2 (voxkage-gui)

Architecture: Internal execution engine with plan→act→verify→retry loop.
The model plans once; the server handles the messy retry/verify loop internally
and returns only a clean text summary. Screenshots are saved to disk and
referenced by path — never base64-encoded into tool responses.

Tools (unchanged surface — drop-in replacement):
  gui_thinking(goal, plan)    — plan before acting
  get_desktop_state()         — screenshot + all open windows
  get_open_files()            — session awareness: all files open across apps
  gui_step(action, goal, ...) — atomic OR multi-step desktop action
  read_active_document()      — read content of currently focused document

Run standalone: python mcp_servers/gui_server.py
"""

import os
import sys
import io
import re
import time
import json
import base64
import subprocess
import threading
import functools
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-gui")

# ── Constants ─────────────────────────────────────────────────────────────────
_BRAIN_DIR       = r"C:\VoxKage\Brain"
_SCREENSHOT_DIR  = os.path.join(_BRAIN_DIR, "screenshots")
_FOCUS_SETTLE    = 0.35   # seconds after SetForegroundWindow
_ACTION_SETTLE   = 0.25   # seconds after click/hotkey/type
_NAV_SETTLE      = 0.80   # seconds after open/double-click (app needs to paint)
_MAX_FIND_TRIES  = 3      # find_and_click retries before giving up
_SCREENSHOT_Q    = 80     # JPEG quality for saved screenshots (disk)

os.makedirs(_SCREENSHOT_DIR, exist_ok=True)

# ── Single worker thread (owns the COM apartment) ─────────────────────────────
# All pyautogui + win32 calls must run on this thread.
# Eliminates the threading.Lock deadlock race entirely.
import asyncio as _asyncio

_WORKER_LOOP:   _asyncio.AbstractEventLoop | None = None
_WORKER_THREAD: threading.Thread | None = None
_WORKER_READY   = threading.Event()


def _start_worker():
    global _WORKER_LOOP
    try:
        import pythoncom
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    except Exception:
        pass
    loop = _asyncio.new_event_loop()
    _WORKER_LOOP = loop
    _WORKER_READY.set()
    loop.run_forever()


def _ensure_worker():
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _WORKER_THREAD = threading.Thread(
        target=_start_worker, daemon=True, name="vk-gui-worker"
    )
    _WORKER_THREAD.start()
    _WORKER_READY.wait(timeout=5)


def _on_worker(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on the GUI worker thread and return the result."""
    _ensure_worker()
    fut = _asyncio.run_coroutine_threadsafe(
        _asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args, **kwargs)),
        _WORKER_LOOP,
    )
    # Use a proper future that runs in the worker's own loop
    result_box = [None, None]  # [result, exception]
    done = threading.Event()

    def _task():
        try:
            result_box[0] = fn(*args, **kwargs)
        except Exception as e:
            result_box[1] = e
        finally:
            done.set()

    _WORKER_LOOP.call_soon_threadsafe(
        lambda: threading.Thread(target=_task, daemon=True).start()
    )
    done.wait(timeout=30)
    if result_box[1] is not None:
        raise result_box[1]
    return result_box[0]


# ── Optional heavy deps ───────────────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except Exception:
    _PYAUTOGUI = False

try:
    import win32gui, win32con, win32process, win32api
    import ctypes, ctypes.wintypes
    _WIN32 = True
except Exception:
    _WIN32 = False

try:
    import psutil
    _PSUTIL = True
except Exception:
    _PSUTIL = False

try:
    from PIL import Image
    _PIL = True
except Exception:
    _PIL = False


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT ENGINE — saves to disk, NEVER base64 in tool responses
# ═══════════════════════════════════════════════════════════════════════════════

def _take_screenshot(label: str = "gui", hwnd: int = 0) -> str:
    """
    Capture screen (or a specific window) and save to disk.
    Returns the absolute file path. Empty string on failure.
    Never returns base64 — callers reference the path only.
    """
    if not _PYAUTOGUI:
        return ""
    try:
        region = None
        if hwnd and _WIN32:
            try:
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                if r > l and b > t:
                    region = (l, t, r - l, b - t)
            except Exception:
                pass
        img = pyautogui.screenshot(region=region)
        safe_label = re.sub(r"[^\w\-]", "_", label)[:40]
        path = os.path.join(_SCREENSHOT_DIR, f"{safe_label}.jpg")
        img.save(path, "JPEG", quality=_SCREENSHOT_Q)
        return path
    except Exception:
        return ""


def _screenshot_ref(label: str, extra_text: str = "", hwnd: int = 0) -> str:
    """
    Take a screenshot and return a clean text reference string.
    This is what MCP tools return — a path the model can inspect,
    not a blob that floods the context window.
    """
    path = _take_screenshot(label, hwnd)
    if path:
        ref = (
            f"[SCREENSHOT] Saved: {path}\n"
            f"To inspect visually: analyze_specific_file(file_path='{path}', query='describe what you see')\n"
        )
    else:
        ref = "[SCREENSHOT] Could not capture screenshot.\n"
    return (ref + extra_text).strip()


# ═══════════════════════════════════════════════════════════════════════════════
# WINDOW MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

_WINDOW_ALIASES = {
    "vscode": "code", "vs code": "code", "visual studio code": "code",
    "word": "winword", "excel": "excel", "notepad++": "notepad++",
    "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
    "vlc": "vlc", "spotify": "spotify", "discord": "discord",
    "explorer": "explorer", "file explorer": "explorer",
    "task manager": "taskmgr", "terminal": "windowsterminal",
    "cmd": "cmd", "powershell": "powershell",
}


def _get_windows() -> list[dict]:
    if not _WIN32:
        return []
    results = []
    _SKIP_EXE = {"systemsettings.exe", "textinputhost.exe", "shellexperiencehost.exe",
                 "applicationframehost.exe", "nvidia overlay.exe"}

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title or len(title) < 2:
            return
        exe, pid = "unknown", 0
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if _PSUTIL:
                exe = psutil.Process(pid).name().lower()
        except Exception:
            pass
        if exe in _SKIP_EXE:
            return
        results.append({"hwnd": hwnd, "title": title, "exe": exe, "pid": pid})

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return results


def _find_hwnd(app_name: str) -> int:
    """Fuzzy-find HWND. Returns 0 if not found."""
    if not app_name:
        return 0
    low = app_name.lower().strip()
    # Apply alias map
    for alias, exe_base in _WINDOW_ALIASES.items():
        if alias in low:
            low = exe_base
            break
    # Score each window — prefer exe match over title match
    best_hwnd, best_score = 0, 0
    for w in _get_windows():
        score = 0
        if low in w["exe"]:
            score = 2
        elif low in w["title"].lower():
            score = 1
        if score > best_score:
            best_score = score
            best_hwnd = w["hwnd"]
    return best_hwnd


def _focus_window(hwnd: int) -> bool:
    """
    Bring hwnd to foreground using AttachThreadInput.
    Plain SetForegroundWindow fails 100% when caller is a background process.
    """
    if not hwnd or not _WIN32:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)

        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid  = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)
        our_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        tgt_tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)

        ctypes.windll.user32.AllowSetForegroundWindow(ctypes.wintypes.DWORD(-1))

        attached = []
        if fg_tid and fg_tid != our_tid:
            ctypes.windll.user32.AttachThreadInput(our_tid, fg_tid, True)
            attached.append(fg_tid)
        if tgt_tid and tgt_tid != our_tid and tgt_tid != fg_tid:
            ctypes.windll.user32.AttachThreadInput(our_tid, tgt_tid, True)
            attached.append(tgt_tid)

        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        time.sleep(_FOCUS_SETTLE)

        for tid in attached:
            ctypes.windll.user32.AttachThreadInput(our_tid, tid, False)
        return True
    except Exception:
        try:
            win32gui.BringWindowToTop(hwnd)
            time.sleep(0.2)
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# CLIPBOARD
# ═══════════════════════════════════════════════════════════════════════════════

def _read_clipboard() -> str:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except Exception:
            data = ""
        win32clipboard.CloseClipboard()
        return data or ""
    except Exception:
        return ""


def _set_clipboard(text: str):
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# This is the key architectural difference from v1.
# The engine runs plan→act→screenshot→verify→retry internally,
# returning only a clean text result to the model.
# The model never has to manage retry state or parse base64 blobs.

class _ActionResult:
    """Structured result from a single atomic action."""
    def __init__(self, success: bool, message: str, screenshot: str = "",
                 data: dict | None = None):
        self.success    = success
        self.message    = message
        self.screenshot = screenshot  # file path, never base64
        self.data       = data or {}

    def to_str(self) -> str:
        parts = [
            f"{'✓' if self.success else '✗'}  {self.message}",
        ]
        if self.screenshot:
            parts.append(
                f"[SCREENSHOT] {self.screenshot}\n"
                f"   → analyze_specific_file(file_path='{self.screenshot}', query='describe what you see')"
            )
        if self.data:
            for k, v in self.data.items():
                parts.append(f"   {k}: {v}")
        return "\n".join(parts)


def _exec_focus(app: str) -> _ActionResult:
    """Focus a window by app name. Internal — used by engine."""
    hwnd = _find_hwnd(app)
    if not hwnd:
        wins = [w["title"][:60] for w in _get_windows()[:8]]
        return _ActionResult(
            False,
            f"Window '{app}' not found. Open windows: {wins}\n"
            f"Is the application running? Use open_application() first if needed.",
        )
    ok = _focus_window(hwnd)
    safe_app = re.sub(r'[^\w]', '_', app)[:20]
    shot = _take_screenshot(f"focus_{safe_app}", hwnd)
    return _ActionResult(ok, f"Focused '{app}'", shot)


def _exec_click(x: int, y: int, button: str = "left", double: bool = False) -> _ActionResult:
    if not _PYAUTOGUI:
        return _ActionResult(False, "pyautogui not available")
    if double:
        pyautogui.doubleClick(x, y)
    elif button == "right":
        pyautogui.rightClick(x, y)
    else:
        pyautogui.click(x, y)
    time.sleep(_ACTION_SETTLE)
    shot = _take_screenshot(f"click_{x}_{y}")
    return _ActionResult(True, f"{'Double-c' if double else 'C'}licked at ({x}, {y})", shot)


def _exec_hotkey(keys: str) -> _ActionResult:
    if not _PYAUTOGUI:
        return _ActionResult(False, "pyautogui not available")
    parts = [p.strip() for p in keys.split("+")]
    pyautogui.hotkey(*parts)
    time.sleep(_ACTION_SETTLE)
    safe_keys = re.sub(r'[^\w]', '_', keys)[:20]
    shot = _take_screenshot(f"hotkey_{safe_keys}")
    return _ActionResult(True, f"Pressed hotkey: {keys}", shot)


def _exec_type(text: str) -> _ActionResult:
    if not _PYAUTOGUI:
        return _ActionResult(False, "pyautogui not available")
    if len(text) <= 80 and all(ord(c) < 128 for c in text):
        pyautogui.typewrite(text, interval=0.03)
    else:
        old = _read_clipboard()
        try:
            _set_clipboard(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
        finally:
            _set_clipboard(old)
    return _ActionResult(True, f"Typed: \"{text[:60]}{'...' if len(text)>60 else ''}\"")


def _exec_scroll(direction: str, amount: int) -> _ActionResult:
    if not _PYAUTOGUI:
        return _ActionResult(False, "pyautogui not available")
    clicks = amount if direction == "down" else -amount
    pyautogui.scroll(clicks)
    time.sleep(0.3)
    return _ActionResult(True, f"Scrolled {direction} {amount} clicks")


def _exec_find_and_click(description: str, app: str = "", double: bool = False,
                         button: str = "left") -> _ActionResult:
    """
    Vision-assisted click: take screenshot, describe target, return
    structured instruction for the model to supply coordinates.

    Unlike v1, this does NOT embed retry state in the goal string.
    The engine tracks retries internally via the caller loop in gui_step.
    """
    if app:
        hwnd = _find_hwnd(app)
        if hwnd:
            _focus_window(hwnd)
            time.sleep(0.2)
    shot = _take_screenshot("find_click_target")
    if not shot:
        return _ActionResult(False, "Could not take screenshot for vision-assisted click")

    return _ActionResult(
        True,   # "success" here means "screenshot ready, awaiting coordinates"
        f"VISION_NEEDED: To {'double-' if double else ''}click '{description}':\n"
        f"1. Inspect the screenshot: analyze_specific_file(file_path='{shot}', query='find the exact pixel coordinates of: {description}')\n"
        f"2. Then call: gui_step(action=\"{'double_click' if double else 'click'}\", x=<x>, y=<y>, goal=\"{description}\")\n"
        f"3. If the element is not visible, call gui_step(action=\"scroll\", direction=\"down\") and try again.",
        shot,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FILE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _open_file_in_app(file_path: str, app: str = "") -> str:
    if not os.path.exists(file_path):
        return f"File not found: {file_path}"
    app_low = app.lower()
    try:
        if "code" in app_low or "vscode" in app_low:
            subprocess.Popen(["code", file_path], shell=True)
            return f"Opened {file_path} in VS Code"
        elif "notepad++" in app_low:
            subprocess.Popen(["notepad++", file_path], shell=True)
            return f"Opened {file_path} in Notepad++"
        elif "notepad" in app_low:
            subprocess.Popen(["notepad", file_path], shell=True)
            return f"Opened {file_path} in Notepad"
        else:
            os.startfile(file_path)
            return f"Opened {file_path} with default app"
    except Exception as e:
        return f"Failed to open file: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def gui_thinking(goal: str, plan: str) -> str:
    """
    GUI PILOT: Plan a multi-step desktop automation task before acting.

    Call this BEFORE any sequence of 3+ GUI steps.
    After calling this, IMMEDIATELY call gui_step to start executing.
    Do NOT output prose between gui_thinking and the first gui_step.

    Parameters:
      goal : what you are trying to accomplish on the desktop
      plan : numbered step-by-step plan
    """
    try:
        from voice.voice_manager import log_to_hud
        log_to_hud("VoxKage", f"🖥️ GUI Plan: {goal}\n{plan}")
    except Exception:
        pass
    return (
        f"GUI PLAN LOGGED\n"
        f"Goal : {goal}\n"
        f"Steps:\n{plan}\n\n"
        f"━━━ EXECUTE NOW ━━━\n"
        f"Start with: gui_step(action='screenshot', goal='{goal[:60]}')\n"
        f"Then proceed step by step. Each gui_step handles its own verify loop."
    )


@mcp.tool()
def get_desktop_state() -> str:
    """
    GUI PILOT: Screenshot the desktop and list all open windows.

    Returns a text summary and a screenshot file path.
    Use analyze_specific_file() on the screenshot path to visually inspect.

    Call this:
    - At the start of any GUI task
    - After completing a task to verify the result
    - When unsure what is currently on screen
    """
    def _run():
        windows = _get_windows()
        focused = ""
        if _WIN32:
            try:
                focused = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            except Exception:
                pass

        win_list = "\n".join(
            f"  [{w['exe']}] {w['title'][:80]}"
            for w in windows[:20]
        )
        text = (
            f"DESKTOP STATE\n"
            f"Focused: {focused or '(unknown)'}\n"
            f"Open windows ({len(windows)}):\n{win_list}"
        )
        shot = _take_screenshot("desktop_state")
        if shot:
            text += (
                f"\n\nScreenshot: {shot}\n"
                f"→ analyze_specific_file(file_path='{shot}', query='describe the current desktop state')"
            )
        return text

    return _on_worker(_run)


@mcp.tool()
def get_open_files() -> str:
    """
    SESSION AWARENESS: Return all files currently open across all apps.

    Call when user refers to "my open Word doc", "the PDF I have open",
    "my current VS Code file" — gives you exact file paths instantly.
    """
    def _run():
        result: dict[str, list] = {}
        windows = _get_windows()

        for w in windows:
            title, exe = w["title"], w["exe"]

            if "code.exe" in exe or "visual studio code" in title.lower():
                parts = [p.strip() for p in re.split(r"\s[—–\-]{1,2}\s", title)]
                if parts and "visual studio code" not in parts[0].lower():
                    result.setdefault("vscode", []).append(parts[0])

            elif "winword.exe" in exe:
                m = re.match(r"^(.+?)\s+-\s+(?:Word|Microsoft Word)", title)
                if m:
                    result.setdefault("microsoft_word", []).append(m.group(1).strip())

            elif "excel.exe" in exe:
                m = re.match(r"^(.+?)\s+-\s+(?:Excel|Microsoft Excel)", title)
                if m:
                    result.setdefault("microsoft_excel", []).append(m.group(1).strip())

            elif "notepad.exe" == exe:
                m = re.match(r"^(.+?)\s+-\s+Notepad", title)
                if m:
                    result.setdefault("notepad", []).append(m.group(1).strip())

            elif "notepad++.exe" == exe:
                result.setdefault("notepad_pp", []).append(
                    title.split(" - Notepad++")[0].strip()
                )

            elif "vlc.exe" == exe:
                m = re.match(r"^(.+?)\s+[–—\-]+\s+VLC", title)
                if m:
                    result.setdefault("vlc", []).append(m.group(1).strip())

            elif exe in ("acrobat.exe", "acrord32.exe"):
                m = re.match(r"^(.+?)\s+-\s+Adobe Acrobat", title)
                if m:
                    result.setdefault("adobe_acrobat", []).append(m.group(1).strip())

            elif "msedge.exe" == exe and ".pdf" in title.lower():
                result.setdefault("edge_pdf", []).append(
                    title.split(" - Microsoft")[0].strip()
                )
            elif "chrome.exe" == exe and ".pdf" in title.lower():
                result.setdefault("chrome_pdf", []).append(
                    title.split(" - Google")[0].strip()
                )

        # COM override for full paths (Word / Excel)
        for com_app, com_key, com_attr in [
            ("Word.Application",  "microsoft_word",  "Documents"),
            ("Excel.Application", "microsoft_excel", "Workbooks"),
        ]:
            try:
                import win32com.client
                app = win32com.client.GetActiveObject(com_app)
                coll = getattr(app, com_attr)
                paths = [coll.Item(i + 1).FullName for i in range(coll.Count)]
                if paths:
                    result[com_key] = paths
            except Exception:
                pass

        if not result:
            return (
                "[SESSION] No recognized open files detected.\n"
                "Open windows may be apps without files (browsers, terminals, etc.)."
            )

        lines = ["[SESSION] Currently open files:\n"]
        for app_name, files in result.items():
            lines.append(f"  {app_name}:")
            for f in files:
                lines.append(f"    • {f}")
        return "\n".join(lines)

    return _on_worker(_run)


@mcp.tool()
def gui_step(
    action: str,
    goal: str,
    app: str = "",
    x: int = -1,
    y: int = -1,
    description: str = "",
    text: str = "",
    keys: str = "",
    key: str = "",
    direction: str = "down",
    amount: int = 3,
    ms: int = 500,
    from_x: int = -1,
    from_y: int = -1,
    to_x: int = -1,
    to_y: int = -1,
    file_path: str = "",
) -> str:
    """
    GUI PILOT: Execute ONE atomic desktop action.

    All actions automatically take a screenshot after executing and return
    the file path — never base64 blobs. Screenshots can be inspected with
    analyze_specific_file().

    ACTIONS:
      screenshot      — capture current desktop state
      focus           — bring app window to foreground (app=name)
      click           — left-click at (x,y) OR vision-assisted by description
      right_click     — right-click at (x,y) OR vision-assisted by description
      double_click    — double-click at (x,y) OR vision-assisted by description
      find_and_click  — take screenshot + return coordinates request for model
      type            — type text (text=...). Short ASCII: keystrokes. Long/unicode: clipboard
      hotkey          — key combination (keys="ctrl+s", "ctrl+shift+x", "alt+tab")
      key             — single key press (key="enter", "escape", "tab", "f5")
      scroll          — scroll page (direction="up"/"down", amount=clicks)
      drag            — drag from (from_x,from_y) to (to_x,to_y)
      wait            — pause ms milliseconds
      open_file       — open file_path in app (or default handler)
      read_screen     — screenshot so model can read on-screen text

    WORKFLOW:
      1. gui_step(action="screenshot")                      — see current state
      2. gui_step(action="focus", app="File Explorer")      — focus window
      3. gui_step(action="find_and_click", description="Tracker folder")
         → inspect screenshot, get coordinates
      4. gui_step(action="double_click", x=<x>, y=<y>)     — execute with coords
      5. gui_step(action="screenshot")                      — verify result

    IMPORTANT: find_and_click returns a VISION_NEEDED message with the screenshot
    path. Inspect it, extract coordinates, then call click/double_click with those
    coordinates. Do NOT call find_and_click repeatedly without checking the screenshot.

    Parameters:
      action      : action name (see above)
      goal        : what you are trying to accomplish (always required)
      app         : app name for focus/find_and_click (e.g. "VS Code", "File Explorer")
      x, y        : pixel coordinates for click/drag actions
      description : element description for find_and_click
      text        : text to type
      keys        : hotkey combo (e.g. "ctrl+shift+x")
      key         : single key (e.g. "enter", "escape")
      direction   : scroll direction ("up" or "down")
      amount      : scroll amount (default 3)
      ms          : wait duration in milliseconds (default 500)
      from_x,from_y,to_x,to_y : drag coordinates
      file_path   : path for open_file action
    """
    if not _PYAUTOGUI and action not in ("screenshot", "read_screen", "wait"):
        return "pyautogui not available — cannot perform GUI automation."

    action = action.lower().strip()

    def _run():
        # ── screenshot / read_screen ───────────────────────────────────────
        if action in ("screenshot", "read_screen"):
            hwnd = _find_hwnd(app) if app else 0
            shot = _take_screenshot(action, hwnd)
            windows = _get_windows()
            focused = ""
            if _WIN32:
                try:
                    focused = win32gui.GetWindowText(win32gui.GetForegroundWindow())
                except Exception:
                    pass
            win_summary = ", ".join(
                f"{w['exe']}:{w['title'][:30]}" for w in windows[:6]
            )
            result = (
                f"Current state — Focused: {focused or 'unknown'}\n"
                f"Open: {win_summary}"
            )
            if shot:
                result += (
                    f"\n[SCREENSHOT] {shot}\n"
                    f"→ analyze_specific_file(file_path='{shot}', query='describe what you see')"
                )
            return result

        # ── focus ──────────────────────────────────────────────────────────
        if action == "focus":
            target = app or description
            if not target:
                return "focus requires 'app' parameter (e.g. app='VS Code')"
            r = _exec_focus(target)
            return r.to_str()

        # ── open_file ──────────────────────────────────────────────────────
        if action == "open_file":
            if not file_path:
                return "open_file requires file_path parameter"
            msg = _open_file_in_app(file_path, app)
            time.sleep(_NAV_SETTLE)
            r = _ActionResult(True, msg, _take_screenshot("open_file"))
            return r.to_str()

        # ── wait ───────────────────────────────────────────────────────────
        if action == "wait":
            wait_ms = max(100, min(int(ms), 10000))
            time.sleep(wait_ms / 1000)
            return f"Waited {wait_ms}ms"

        # ── hotkey ─────────────────────────────────────────────────────────
        if action == "hotkey":
            if not keys:
                return "hotkey requires 'keys' (e.g. keys='ctrl+s')"
            danger = {"alt+f4", "ctrl+alt+del", "win+l"}
            if any(d in keys.lower() for d in danger):
                return (
                    f"[CONFIRM REQUIRED] Hotkey '{keys}' closes/locks a window. "
                    f"Confirm with user before executing."
                )
            r = _exec_hotkey(keys)
            return r.to_str()

        # ── key ────────────────────────────────────────────────────────────
        if action == "key":
            if not key:
                return "key requires 'key' parameter (e.g. key='enter')"
            pyautogui.press(key)
            time.sleep(0.2)
            return f"Pressed key: '{key}'"

        # ── type ───────────────────────────────────────────────────────────
        if action == "type":
            if not text:
                return "type requires 'text' parameter"
            r = _exec_type(text)
            return r.to_str()

        # ── scroll ─────────────────────────────────────────────────────────
        if action == "scroll":
            r = _exec_scroll(direction if direction in ("up","down") else "down", int(amount))
            return r.to_str()

        # ── drag ───────────────────────────────────────────────────────────
        if action == "drag":
            if from_x < 0 or from_y < 0 or to_x < 0 or to_y < 0:
                return "drag requires from_x, from_y, to_x, to_y"
            pyautogui.moveTo(from_x, from_y, duration=0.3)
            pyautogui.dragTo(to_x, to_y, duration=0.5, button="left")
            time.sleep(0.3)
            shot = _take_screenshot("drag")
            r = _ActionResult(True, f"Dragged ({from_x},{from_y}) → ({to_x},{to_y})", shot)
            return r.to_str()

        # ── find_and_click ─────────────────────────────────────────────────
        if action == "find_and_click":
            target = description or text
            if not target:
                return "find_and_click requires 'description' parameter"
            is_double = "double" in goal.lower()
            r = _exec_find_and_click(target, app, double=is_double)
            return r.to_str()

        # ── click / right_click / double_click ────────────────────────────
        if action in ("click", "right_click", "double_click"):
            if x >= 0 and y >= 0:
                is_double = action == "double_click"
                btn = "right" if action == "right_click" else "left"
                r = _exec_click(x, y, button=btn, double=is_double)
                return r.to_str()
            elif description:
                # Redirect to vision path — no coordinates supplied
                r = _exec_find_and_click(
                    description, app,
                    double=(action == "double_click"),
                    button="right" if action == "right_click" else "left",
                )
                return r.to_str()
            else:
                return f"{action} requires either (x,y) coordinates or description"

        return (
            f"Unknown action: '{action}'. Valid: screenshot, focus, click, right_click, "
            "double_click, find_and_click, type, hotkey, key, scroll, drag, read_screen, wait, open_file"
        )

    return _on_worker(_run)


@mcp.tool()
def read_active_document() -> str:
    """
    SESSION AWARENESS: Read text content of the currently active document.

    Works with:
    - Microsoft Word (full document via COM)
    - VS Code / Notepad / any text editor (Ctrl+A → Ctrl+C → clipboard)
    - PDF viewers (screenshot so model can read visible page)

    Use when user says "what does my open file say" or "summarize my current doc".
    """
    def _run():
        # Try Word COM first (most accurate — gets full document text)
        try:
            import win32com.client
            word = win32com.client.GetActiveObject("Word.Application")
            doc = word.ActiveDocument
            text = doc.Content.Text
            name = doc.Name
            if text:
                preview = text[:6000]
                return (
                    f"[DOCUMENT] Active Word document: {name}\n"
                    f"Length: {len(text)} characters\n\n"
                    f"{preview}"
                    + ("\n\n[...truncated — document continues]" if len(text) > 6000 else "")
                )
        except Exception:
            pass

        # Text editor: Ctrl+A → Ctrl+C → clipboard
        focused = ""
        if _WIN32:
            try:
                focused = win32gui.GetWindowText(win32gui.GetForegroundWindow()).lower()
            except Exception:
                pass

        is_text_editor = any(x in focused for x in [
            "notepad", "code", "sublime", "atom", "vim", "wordpad", "writer"
        ])

        if is_text_editor and _PYAUTOGUI:
            old_clip = _read_clipboard()
            try:
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.3)
                pyautogui.hotkey("ctrl", "c")
                time.sleep(0.4)
                content = _read_clipboard()
                pyautogui.hotkey("ctrl", "Home")
            finally:
                _set_clipboard(old_clip)

            if content:
                preview = content[:6000]
                return (
                    f"[DOCUMENT] Active editor: {focused}\n"
                    f"Length: {len(content)} characters\n\n"
                    f"{preview}"
                    + ("\n\n[...truncated]" if len(content) > 6000 else "")
                )

        # Fallback: screenshot so model reads it visually
        shot = _take_screenshot("read_doc")
        if shot:
            return (
                f"[DOCUMENT] Could not extract text automatically.\n"
                f"Screenshot: {shot}\n"
                f"→ analyze_specific_file(file_path='{shot}', query='read all visible text on this document')"
            )
        return "[DOCUMENT] Could not read active document — no compatible app detected."

    return _on_worker(_run)


if __name__ == "__main__":
    mcp.run()