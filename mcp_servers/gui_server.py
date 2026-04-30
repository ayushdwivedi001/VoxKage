"""
MCP Server: VoxKage Local GUI Pilot (voxkage-gui)

Gives VoxKage vision-verified hands on the entire Windows desktop.
Same plan→step→screenshot→verify→retry loop as the browser agent.

Tools:
  gui_thinking(goal, plan)    — plan before acting (mirrors agent_thinking)
  get_desktop_state()         — screenshot + all open windows
  get_open_files()            — session awareness: all files open across apps
  gui_step(action, goal, ...) — atomic desktop action (click/type/hotkey/etc.)
  read_active_document()      — read content of currently focused document

Run standalone: python mcp_servers/gui_server.py
"""

import os, sys, io, re, time, base64, subprocess, json
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-gui")

# ── Optional heavy deps (graceful degradation) ────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.08
    _PYAUTOGUI = True
except Exception:
    _PYAUTOGUI = False

try:
    import win32gui, win32con, win32process, win32api
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

_BRAIN_DIR = r"C:\VoxKage\Brain"
os.makedirs(_BRAIN_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _screenshot(label: str = "desktop", hwnd: int = 0) -> tuple:
    """Returns (saved_path, base64_str). Empty strings on failure."""
    if not _PYAUTOGUI:
        return "", ""
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
        path = os.path.join(_BRAIN_DIR, f"gui_{label}.jpg")
        img.save(path, "JPEG", quality=85)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return path, b64
    except Exception as e:
        return "", ""


def _vision(label: str, text: str, hwnd: int = 0) -> dict:
    """Return __vision__ dict for the agentic loop's vision pipeline."""
    path, b64 = _screenshot(label, hwnd)
    return {
        "__vision__": True,
        "text":          text + (f"\n[Screenshot saved: {path}]" if path else ""),
        "screenshot_b64": b64,
    }


def _get_windows() -> list:
    if not _WIN32:
        return []
    results = []
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
        results.append({"hwnd": hwnd, "title": title, "exe": exe, "pid": pid})
    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return results


def _find_hwnd(app_name: str) -> int:
    """Fuzzy-find HWND for an app name."""
    if not app_name:
        return 0
    low = app_name.lower().strip()
    # Known aliases
    _ALIASES = {
        "vscode": "code", "vs code": "code", "visual studio code": "code",
        "word": "winword", "excel": "excel", "notepad++": "notepad++",
        "chrome": "chrome", "edge": "msedge", "firefox": "firefox",
        "vlc": "vlc", "spotify": "spotify", "discord": "discord",
        "explorer": "explorer", "task manager": "taskmgr",
    }
    for alias, exe_base in _ALIASES.items():
        if alias in low:
            low = exe_base
            break

    for w in _get_windows():
        if low in w["exe"] or low in w["title"].lower():
            return w["hwnd"]
    return 0


def _focus(hwnd: int) -> bool:
    if not hwnd or not _WIN32:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.35)
        return True
    except Exception:
        try:
            win32gui.BringWindowToTop(hwnd)
            time.sleep(0.2)
            return True
        except Exception:
            return False


def _read_clipboard() -> str:
    """Read current clipboard text via win32clipboard."""
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


def _open_file_in_app(file_path: str, app: str = "") -> str:
    """Open a file in a specified app or system default."""
    if not os.path.exists(file_path):
        return f"File not found: {file_path}"
    app_low = app.lower()
    try:
        if "code" in app_low or "vscode" in app_low or "vs code" in app_low:
            subprocess.Popen(["code", file_path], shell=True)
            return f"Opened {file_path} in VS Code."
        elif "notepad++" in app_low:
            subprocess.Popen(["notepad++", file_path], shell=True)
            return f"Opened {file_path} in Notepad++."
        elif "notepad" in app_low:
            subprocess.Popen(["notepad", file_path], shell=True)
            return f"Opened {file_path} in Notepad."
        elif "word" in app_low:
            os.startfile(file_path)
            return f"Opened {file_path} in Word."
        else:
            os.startfile(file_path)
            return f"Opened {file_path} with default app."
    except Exception as e:
        return f"Failed to open file: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def gui_thinking(goal: str, plan: str) -> str:
    """
    GUI PILOT: Plan a multi-step desktop automation task before acting.

    Call this BEFORE any sequence of 3+ GUI steps, just like agent_thinking
    for browser tasks. Forces structured planning, logged to HUD.

    Parameters:
      goal : what you are trying to accomplish on the desktop
      plan : numbered step-by-step plan (e.g. "1. Focus VS Code\\n2. Ctrl+Shift+X\\n3. Type Prettier\\n4. Click Install")

    After calling this, IMMEDIATELY call gui_step to start executing.
    """
    try:
        from voice.voice_manager import log_to_hud
        log_to_hud("VoxKage", f"🖥️ GUI Plan: {goal}\n{plan}")
    except Exception:
        pass
    return (
        f"GUI PLAN LOGGED:\nGoal: {goal}\n{plan}\n\n"
        f"━━━ START EXECUTING NOW ━━━\n"
        f"Call gui_step immediately. Do NOT output prose. Begin with step 1.\n"
        f"  → gui_step(action='screenshot', goal='{goal}') to see current state first\n"
        f"  → Then proceed with your plan step by step."
    )


@mcp.tool()
def get_desktop_state() -> dict:
    """
    GUI PILOT: Take a full desktop screenshot and list all open windows.

    Returns a __vision__ dict so Gemini can SEE the current desktop.
    Use this:
    - At the start of any GUI task to understand current state
    - After completing a task to verify the result
    - When you're unsure what's currently on screen
    """
    windows = _get_windows()
    focused_title = ""
    if _WIN32:
        try:
            focused_title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        except Exception:
            pass

    win_list = "\n".join(
        f"  [{w['exe']}] {w['title'][:80]}"
        for w in windows[:20]
        if w["exe"] not in ("systemsettings.exe", "textinputhost.exe", "shellexperiencehost.exe")
    )
    text = (
        f"DESKTOP STATE\n"
        f"Focused window: {focused_title}\n"
        f"Open windows ({len(windows)}):\n{win_list}\n\n"
        f"Screenshot attached — analyze it to understand current UI state."
    )
    return _vision("desktop_state", text)


@mcp.tool()
def get_open_files() -> str:
    """
    SESSION AWARENESS: Return all files currently open across all apps.

    Call this when user says things like:
    - "my open Word doc", "the PDF I have open", "my current VS Code file"
    - "look at my Japanese book PDF"
    This gives you the exact file path instantly without searching.

    Returns structured dict of app -> [file paths/names].
    """
    result = {}
    windows = _get_windows()

    for w in windows:
        title, exe = w["title"], w["exe"]

        if exe in ("code.exe",) or "visual studio code" in title.lower():
            parts = [p.strip() for p in re.split(r"\s[—–\-]{1,2}\s", title)]
            if parts and "visual studio code" not in parts[0].lower():
                result.setdefault("vscode", []).append(parts[0])

        elif exe == "winword.exe" or ("word" in exe and "exe" in exe):
            m = re.match(r"^(.+?)\s+-\s+(?:Word|Microsoft Word)", title)
            if m:
                result.setdefault("microsoft_word", []).append(m.group(1).strip())

        elif exe == "excel.exe":
            m = re.match(r"^(.+?)\s+-\s+(?:Excel|Microsoft Excel)", title)
            if m:
                result.setdefault("microsoft_excel", []).append(m.group(1).strip())

        elif exe == "notepad.exe":
            m = re.match(r"^(.+?)\s+-\s+Notepad", title)
            if m:
                result.setdefault("notepad", []).append(m.group(1).strip())

        elif exe == "notepad++.exe":
            result.setdefault("notepad_pp", []).append(title.split(" - Notepad++")[0].strip())

        elif exe == "vlc.exe":
            m = re.match(r"^(.+?)\s+[–—\-]+\s+VLC", title)
            if m:
                result.setdefault("vlc", []).append(m.group(1).strip())

        elif exe in ("acrobat.exe", "acrord32.exe"):
            m = re.match(r"^(.+?)\s+-\s+Adobe Acrobat", title)
            if m:
                result.setdefault("adobe_acrobat", []).append(m.group(1).strip())

        elif exe == "msedge.exe" and ".pdf" in title.lower():
            result.setdefault("edge_pdf", []).append(title.split(" - Microsoft")[0].strip())

        elif exe == "chrome.exe" and ".pdf" in title.lower():
            result.setdefault("chrome_pdf", []).append(title.split(" - Google")[0].strip())

    # COM override for Word/Excel (gets full file paths)
    for com_app, com_key, com_attr in [
        ("Word.Application", "microsoft_word", "Documents"),
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
        return "[SESSION] No recognized open files detected. Open windows may be apps without files (browsers, terminals, etc.)."

    lines = ["[SESSION] Currently open files:\n"]
    for app, files in result.items():
        lines.append(f"  {app}:")
        for f in files:
            lines.append(f"    • {f}")
    return "\n".join(lines)


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
) -> dict | str:
    """
    GUI PILOT: Execute ONE atomic desktop action. Always include goal.

    ACTIONS:
      screenshot      — take desktop screenshot (ALWAYS do this first to see current state)
      focus           — bring app window to foreground (app=app name)
      click           — left-click. Use x,y for coordinates OR description for vision-assisted find
      right_click     — right-click at x,y or by description
      double_click    — double-click at x,y or by description
      find_and_click  — PREFERRED: takes screenshot, asks Gemini vision to find element, then clicks
      type            — type text into current focus (text=what to type)
      hotkey          — press key combo (keys="ctrl+s", "ctrl+shift+x", "alt+f4", "win+d")
      key             — press single key (key="enter", "escape", "tab", "delete", "f5")
      scroll          — scroll (direction="up"/"down", amount=clicks)
      drag            — drag from (from_x,from_y) to (to_x,to_y)
      read_screen     — take screenshot so Gemini can read on-screen text
      wait            — pause ms milliseconds (ms=1000 = 1 second)
      open_file       — open file_path in app (or default handler if app="")

    WORKFLOW (always follow this):
      1. gui_step(action="screenshot") — see current state
      2. gui_step(action="focus", app="...") — focus correct window
      3. gui_step(action="find_and_click", description="...") — click elements by description
      4. After each click/hotkey: gui_step(action="screenshot") — verify result
      5. Retry with different approach if expected UI element not visible

    SAFETY: hotkey("alt+f4") and destructive actions will ask confirmation first.

    Parameters:
      action      : action name (see above)
      goal        : what you are trying to accomplish overall
      app         : app name for focus action (e.g. "VS Code", "Notepad", "Spotify")
      x, y        : pixel coordinates for click/drag actions
      description : element description for find_and_click (e.g. "Install button next to Prettier")
      text        : text to type
      keys        : hotkey combo (e.g. "ctrl+shift+x", "ctrl+s")
      key         : single key (e.g. "enter", "escape", "tab", "f5")
      direction   : scroll direction ("up" or "down")
      amount      : scroll clicks (default 3)
      ms          : wait milliseconds (default 500)
      from_x,from_y,to_x,to_y : drag coordinates
      file_path   : path for open_file action
    """
    if not _PYAUTOGUI:
        return "pyautogui not available — cannot perform GUI automation."

    action = action.lower().strip()

    # ── screenshot ─────────────────────────────────────────────────────────────
    if action == "screenshot":
        hwnd = _find_hwnd(app) if app else 0
        return _vision("screenshot", f"Current desktop/window state. Goal: {goal}", hwnd)

    # ── read_screen ────────────────────────────────────────────────────────────
    if action == "read_screen":
        hwnd = _find_hwnd(app) if app else 0
        return _vision("read_screen", f"Read the text visible on screen for: {goal}", hwnd)

    # ── focus ──────────────────────────────────────────────────────────────────
    if action == "focus":
        target = app or description
        if not target:
            return "focus action requires 'app' parameter (e.g. app='VS Code')"
        hwnd = _find_hwnd(target)
        if hwnd:
            _focus(hwnd)
            return _vision("focus", f"Focused '{target}'. Verify the window is now in front.", hwnd)
        else:
            # Try opening it
            result = _open_file_in_app("", target)
            time.sleep(1.5)
            return f"Window '{target}' not found — attempted to launch. {result}"

    # ── open_file ──────────────────────────────────────────────────────────────
    if action == "open_file":
        if not file_path:
            return "open_file requires file_path parameter."
        r = _open_file_in_app(file_path, app)
        time.sleep(1.2)
        return _vision("open_file", r)

    # ── wait ───────────────────────────────────────────────────────────────────
    if action == "wait":
        ms = max(100, min(ms, 10000))
        time.sleep(ms / 1000)
        return f"Waited {ms}ms. Continuing."

    # ── hotkey ─────────────────────────────────────────────────────────────────
    if action == "hotkey":
        if not keys:
            return "hotkey requires 'keys' parameter (e.g. keys='ctrl+s')"
        # Safety gate for destructive combos
        if any(k in keys.lower() for k in ["alt+f4", "ctrl+alt+del", "win+l"]):
            return f"[CONFIRM] Hotkey '{keys}' closes/locks a window. Confirm with user first."
        parts = [p.strip() for p in keys.split("+")]
        pyautogui.hotkey(*parts)
        time.sleep(0.4)
        return _vision("hotkey", f"Pressed {keys}. Screenshot shows result.")

    # ── key ────────────────────────────────────────────────────────────────────
    if action == "key":
        if not key:
            return "key action requires 'key' parameter (e.g. key='enter')"
        pyautogui.press(key)
        time.sleep(0.2)
        return f"Pressed '{key}'."

    # ── type ───────────────────────────────────────────────────────────────────
    if action == "type":
        if not text:
            return "type action requires 'text' parameter."
        # Use clipboard for reliable unicode typing
        old_clip = _read_clipboard()
        _set_clipboard(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        _set_clipboard(old_clip)
        return f"Typed: \"{text[:80]}{'...' if len(text)>80 else ''}\""

    # ── scroll ─────────────────────────────────────────────────────────────────
    if action == "scroll":
        clicks = amount if direction == "down" else -amount
        pyautogui.scroll(clicks)
        time.sleep(0.3)
        return f"Scrolled {direction} {amount} clicks."

    # ── drag ───────────────────────────────────────────────────────────────────
    if action == "drag":
        if from_x < 0 or from_y < 0 or to_x < 0 or to_y < 0:
            return "drag requires from_x, from_y, to_x, to_y."
        pyautogui.moveTo(from_x, from_y, duration=0.3)
        pyautogui.dragTo(to_x, to_y, duration=0.5, button="left")
        time.sleep(0.3)
        return _vision("drag", f"Dragged from ({from_x},{from_y}) to ({to_x},{to_y}).")

    # ── find_and_click (vision-assisted) ──────────────────────────────────────
    if action == "find_and_click":
        target = description or text
        if not target:
            return "find_and_click requires 'description' (e.g. description='Install button')"
        hwnd = _find_hwnd(app) if app else 0
        if hwnd:
            _focus(hwnd)
            time.sleep(0.2)
        return _vision(
            "find_and_click",
            f"FIND AND CLICK: Look at this screenshot and locate: \"{target}\"\n"
            f"Identify the EXACT pixel coordinates (x, y) of that element.\n"
            f"Then call: gui_step(action=\"click\", x=<x>, y=<y>, goal=\"{goal}\")\n"
            f"If the element is not visible, call gui_step(action=\"scroll\", direction=\"down\") and look again."
        )

    # ── click / right_click / double_click ────────────────────────────────────
    if action in ("click", "right_click", "double_click"):
        if x >= 0 and y >= 0:
            # Direct coordinate click
            if action == "click":
                pyautogui.click(x, y)
            elif action == "right_click":
                pyautogui.rightClick(x, y)
            else:
                pyautogui.doubleClick(x, y)
            time.sleep(0.4)
            return _vision(action, f"{action} at ({x},{y}). Screenshot shows result.")
        elif description:
            # Redirect to vision-assisted flow
            return gui_step(
                action="find_and_click",
                goal=goal,
                app=app,
                description=description,
            )
        else:
            return f"{action} requires either (x,y) coordinates or description."

    return f"Unknown action: '{action}'. Valid: screenshot, focus, click, right_click, double_click, find_and_click, type, hotkey, key, scroll, drag, read_screen, wait, open_file"


@mcp.tool()
def read_active_document() -> dict | str:
    """
    SESSION AWARENESS: Read the text content of the currently active/focused document.

    Works with:
    - Microsoft Word (full document via COM)
    - VS Code / Notepad / any text editor (Ctrl+A → Ctrl+C → clipboard)
    - PDF viewers: takes screenshot so Gemini can read the visible page

    Use this when user says "what does my open file say" or "summarize my current doc".
    Returns the document text (or a vision screenshot for PDFs).
    """
    if not _PYAUTOGUI:
        return "pyautogui not available."

    # Try Word COM first (most accurate)
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
                f"Content ({len(text)} chars):\n\n{preview}"
                + ("\n\n[...truncated — document continues]" if len(text) > 6000 else "")
            )
    except Exception:
        pass

    # For text editors: Ctrl+A → Ctrl+C → read clipboard
    focused = ""
    if _WIN32:
        try:
            focused = win32gui.GetWindowText(win32gui.GetForegroundWindow()).lower()
        except Exception:
            pass

    is_text_editor = any(x in focused for x in [
        "notepad", "code", "sublime", "atom", "vim", "nano", "wordpad", "writer"
    ])

    if is_text_editor:
        old_clip = _read_clipboard()
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.4)
        content = _read_clipboard()
        # Deselect
        pyautogui.hotkey("ctrl", "Home")
        # Restore clipboard
        _set_clipboard(old_clip)
        if content:
            preview = content[:6000]
            return (
                f"[DOCUMENT] Active editor: {focused}\n"
                f"Content ({len(content)} chars):\n\n{preview}"
                + ("\n\n[...truncated]" if len(content) > 6000 else "")
            )

    # Fallback: screenshot so Gemini reads it visually
    return _vision("read_doc", "Read the text visible on screen from the active document.")


if __name__ == "__main__":
    mcp.run()
