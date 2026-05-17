"""
tray/tray_app.py — VoxKage System Tray  (v5 — Icon Only)
=========================================================
Responsibility: system tray icon + right-click menu. Nothing else.

ALL Telegram polling is handled exclusively by telegram_watcher.py which is
started as a separate process by cmd_launch() via _ensure_telegram_watcher_running().

Having the tray also poll Telegram caused every message to be injected TWICE
(once by tray, once by watcher). That race condition is fixed here by doing
exactly one thing: showing the icon.

Singleton: binds TCP 127.0.0.1:49998 — only one instance ever runs.
Run:  pythonw tray/tray_app.py   (no console window)
"""

from __future__ import annotations

import os
import subprocess
import sys
import socket
from pathlib import Path

# ── Singleton lock (port bind) ─────────────────────────────────────────────────
try:
    _singleton = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _singleton.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    _singleton.bind(("127.0.0.1", 49998))
    _singleton.listen(1)
except OSError:
    sys.exit(0)  # Another tray instance already running

# ── Path setup ────────────────────────────────────────────────────────────────
_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Env / config ──────────────────────────────────────────────────────────────
from voxkage._env import load_voxkage_env
load_voxkage_env()

# ── pystray + Pillow ──────────────────────────────────────────────────────────
try:
    import pystray
    from PIL import Image as _PILImage
    _PYSTRAY_OK = True
except ImportError:
    _PYSTRAY_OK = False

# ── Icon path ─────────────────────────────────────────────────────────────────
try:
    from voxkage.paths import icon_path as _icon_path_fn
    _ICON_PATH = str(_icon_path_fn())
except Exception:
    _ICON_PATH = os.path.join(_DIR, "..", "icons", "icon.png")


# ── Tray actions ───────────────────────────────────────────────────────────────

def _open_voxkage(icon=None, item=None):
    """Open a new VoxKage terminal session."""
    subprocess.Popen(
        'start "VoxKage" powershell -NoExit -Command "voxkage"',
        shell=True,
    )


def _open_settings(icon=None, item=None):
    """Open the native tkinter settings panel in an isolated process."""
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(
            [sys.executable, "-m", "voxkage.tray.settings_window"],
            creationflags=flags
        )
    except Exception as e:
        print(f"[Tray] Settings error: {e}")


def _quit_app(icon=None, item=None):
    print("[Tray] Exiting...")
    
    # Close the settings window if it is currently open
    if sys.platform == "win32":
        try:
            import win32gui
            import win32con
            hwnd = win32gui.FindWindow(None, "VoxKage Settings")
            if hwnd:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
            
    if icon is not None:
        icon.stop()
    os._exit(0)


# ── Icon loader ────────────────────────────────────────────────────────────────

def _load_icon():
    try:
        img = _PILImage.open(_ICON_PATH).convert("RGBA")
        img = img.resize((64, 64), _PILImage.LANCZOS)
        return img
    except Exception:
        # Fallback: solid cyan square
        try:
            return _PILImage.new("RGBA", (64, 64), (0, 188, 212, 255))
        except Exception:
            return None


# ── Main ───────────────────────────────────────────────────────────────────────

def setup_tray():
    if not _PYSTRAY_OK:
        print(
            "\n  [VoxKage] System Tray requires pystray + Pillow.\n"
            "  Install with: pipx inject voxkage pystray Pillow\n"
        )
        # Block forever so the singleton socket stays open
        import time
        while True:
            time.sleep(60)

    img = _load_icon()
    if img is None:
        import time
        while True:
            time.sleep(60)

    menu = pystray.Menu(
        pystray.MenuItem("▶  Open VoxKage", _open_voxkage, default=True),
        pystray.MenuItem("⚙  Settings",     _open_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("✕  Exit",          _quit_app),
    )

    icon = pystray.Icon(
        name="VoxKage",
        icon=img,
        title="VoxKage — Online",
        menu=menu,
    )

    print("[VoxKage Tray] Running. Telegram is handled by telegram_watcher.py.")
    icon.run()


if __name__ == "__main__":
    setup_tray()
