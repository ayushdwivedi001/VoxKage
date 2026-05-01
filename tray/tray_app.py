"""
tray/tray_app.py — VoxKage System Tray
=======================================
Launches VoxKage globally via the `voxkage` PowerShell function.
The `voxkage` function (in $PROFILE) loads the global ~/.gemini/GEMINI.md
and ~/.gemini/settings.json — giving full personality + all MCP tools
from any directory.

Menu:
  ▶ Open VoxKage          → Opens PowerShell with `voxkage` in project dir
  ✕ Exit                  → Quit tray

Also starts the Telegram inbound listener daemon on boot.
"""

import os
import sys
import subprocess
import threading

# ── Path setup ────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Qt imports ────────────────────────────────────────────────────────────────
os.environ["QT_GQL_NO_DPI_AWARENESS"] = "1"

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt

# ── Project paths ─────────────────────────────────────────────────────────────
_VENV_PYTHON = os.path.join(_ROOT, "venv", "Scripts", "python.exe")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable
_CONFIG = os.path.join(_ROOT, "config.json")
_ICON = os.path.join(_ROOT, "icons", "icon.png")
_TG_LISTENER = os.path.join(_ROOT, "tg_bridge", "inbound_listener.py")

# VoxKage launch: open PowerShell in project root and run the global voxkage function
# The `voxkage` function is defined in $PROFILE and handles all flags + config loading
_GEMINI_CMD = (
    f'start "VoxKage" powershell -NoExit -Command "cd \'{_ROOT}\' ; voxkage"'
)

def open_voxkage(icon=None, item=None):
    """Open CMD with Gemini CLI running in the project directory."""
    subprocess.Popen(_GEMINI_CMD, shell=True, cwd=_ROOT)


def quit_app(tray_icon):
    """Stop the tray and all daemons."""
    tray_icon.hide()
    os._exit(0)


def start_telegram_listener():
    """Launch the Telegram inbound listener as a background daemon process."""
    if not os.path.exists(_TG_LISTENER):
        return
    try:
        subprocess.Popen(
            [_PYTHON, _TG_LISTENER],
            cwd=_ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print("[Tray] Telegram inbound listener started.")
    except Exception as e:
        print(f"[Tray] Failed to start Telegram listener: {e}")


def setup_tray():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    icon = QIcon(_ICON) if os.path.exists(_ICON) else QIcon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("VoxKage")

    menu = QMenu()

    act_open = QAction("▶  Open VoxKage")
    act_open.triggered.connect(open_voxkage)
    menu.addAction(act_open)

    menu.addSeparator()

    act_exit = QAction("✕  Exit")
    act_exit.triggered.connect(lambda: quit_app(tray))
    menu.addAction(act_exit)

    tray.setContextMenu(menu)
    tray.show()

    # Start Telegram listener daemon
    threading.Thread(target=start_telegram_listener, daemon=True).start()

    print("[VoxKage Tray] Running. Right-click the tray icon to open.")
    sys.exit(app.exec())


if __name__ == "__main__":
    setup_tray()
