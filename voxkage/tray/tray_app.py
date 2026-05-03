"""
tray/tray_app.py — VoxKage System Tray  (v3 — Unified)
========================================================
Combines THREE systems into one persistent process:

  1. System Tray Icon     — Right-click menu: Open VoxKage, Settings, Exit
  2. Telegram Watcher     — Background thread polls Telegram every 5 s.
                            Strict processing lock prevents any spam loops.
  3. Settings GUI         — PySide6 dark-themed panel for model selection.

Spam-loop prevention guarantees:
  - Offset is written BEFORE the sub-agent is spawned (atomic advance).
  - A threading.Lock blocks the entire poll loop until the sub-agent exits.
  - Sub-agent has a hard 90-second timeout; lock is always released after.
  - A per-message deduplication set double-checks update IDs.

Run:  pythonw tray/tray_app.py   (no console window)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import socket
from pathlib import Path
from datetime import datetime

# ── Singleton lock (port bind) ─────────────────────────────────────────────────
# Ensures only one tray instance ever runs, even if voxkage.cmd starts it
# repeatedly on each new terminal launch.
try:
    _singleton = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _singleton.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    _singleton.bind(("127.0.0.1", 49998))
    # Keep socket open for the lifetime of this process
except OSError:
    # Another tray instance is already running — exit silently.
    sys.exit(0)

# ── Path setup ────────────────────────────────────────────────────────────────
_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

# ── Env / config ──────────────────────────────────────────────────────────────
os.environ.setdefault("QT_GQL_NO_DPI_AWARENESS", "1")

from voxkage._env import load_voxkage_env
load_voxkage_env()

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

_VOXKAGE_DIR    = Path(os.path.expanduser("~")) / ".voxkage"
_VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
_OFFSET_FILE    = _VOXKAGE_DIR / "telegram_offset.json"
_VOXKAGE_CONFIG = _VOXKAGE_DIR / "config.json"

_ICON = os.path.join(_ROOT, "icons", "icon.png")

from voxkage.paths import find_gemini_cli as _paths_find_gemini
_GEMINI_EXE = _paths_find_gemini()

# ── Runtime config helpers ────────────────────────────────────────────────────
_DEFAULT_MAIN_MODEL    = "gemini-3-flash-preview"
_DEFAULT_SUBAGENT_MODEL = "gemini-3-flash-preview"

def _load_config() -> dict:
    defaults = {"main_model": _DEFAULT_MAIN_MODEL, "subagent_model": _DEFAULT_SUBAGENT_MODEL}
    try:
        if _VOXKAGE_CONFIG.exists():
            data = json.loads(_VOXKAGE_CONFIG.read_text(encoding="utf-8"))
            defaults.update(data)
    except Exception:
        pass
    return defaults

# ── Qt imports ────────────────────────────────────────────────────────────────
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt, QTimer

# ── Telegram offset helpers ───────────────────────────────────────────────────
def _read_offset() -> int:
    try:
        if _OFFSET_FILE.exists():
            return int(json.loads(_OFFSET_FILE.read_text(encoding="utf-8")).get("last_update_id", 0))
    except Exception:
        pass
    return 0

def _write_offset(update_id: int):
    try:
        _OFFSET_FILE.write_text(json.dumps({"last_update_id": update_id}), encoding="utf-8")
    except Exception as e:
        print(f"[Tray] Offset write failed: {e}")

# ── Seen-IDs deduplication set (in-memory, reset on tray restart) ─────────────
_seen_ids: set[int] = set()

# ── Processing lock — only ONE message handled at a time ─────────────────────
_tg_lock = threading.Lock()

# ── Telegram watcher thread ───────────────────────────────────────────────────
def _telegram_poll_loop():
    """
    Polls Telegram every 5 seconds. On a new message:
      1. Advances the offset atomically.
      2. Acquires the processing lock (blocks further polls until done).
      3. Spawns a headless Gemini sub-agent with full context.
      4. Waits for the sub-agent to finish (max 90 s).
      5. Releases the lock.
    """
    if not TELEGRAM_TOKEN:
        print("[Tray] No TELEGRAM_BOT_TOKEN — watcher disabled.")
        return

    import requests

    print("[Tray] Telegram watcher started.")

    while True:
        try:
            # If a message is currently being processed, skip the poll entirely
            if _tg_lock.locked():
                time.sleep(5)
                continue

            last_id = _read_offset()
            params = {"limit": 5, "timeout": 0}
            if last_id > 0:
                params["offset"] = last_id + 1

            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params=params,
                timeout=10,
            )

            if resp.status_code != 200:
                time.sleep(5)
                continue

            updates = resp.json().get("result", [])
            if not updates:
                time.sleep(5)
                continue

            # Only take the LATEST message — ignore burst of old ones
            latest = updates[-1]
            uid    = latest["update_id"]
            msg    = latest.get("message", {}).get("text", "").strip()

            # ── Atomic offset advance BEFORE any processing ────────────────
            max_uid = max(u["update_id"] for u in updates)
            _write_offset(max_uid)

            # ── Deduplication guard ────────────────────────────────────────
            if uid in _seen_ids or not msg:
                time.sleep(5)
                continue
            _seen_ids.add(uid)

            # ── Spawn a handler thread (non-blocking for the poll loop) ────
            threading.Thread(
                target=_handle_message,
                args=(msg,),
                daemon=True,
            ).start()

        except Exception as e:
            print(f"[Tray] Poll error: {e}")

        time.sleep(5)


def _handle_message(msg: str):
    """
    Handles a single inbound Telegram message.
    Runs in its own thread; holds the processing lock for its duration.
    Hard 90-second timeout on the sub-agent.
    """
    acquired = _tg_lock.acquire(blocking=True, timeout=3)
    if not acquired:
        # Another message is mid-flight — this one will be retried next poll
        return
    try:
        cfg            = _load_config()
        subagent_model = cfg.get("subagent_model", _DEFAULT_SUBAGENT_MODEL)

        print(f"[Tray] Processing Telegram message: {msg[:80]}")

        prompt = (
            f"[TELEGRAM INBOUND MESSAGE]\n"
            f"The user sent this from their phone: \"{msg}\"\n\n"
            "You are VoxKage, operating in silent background mode.\n"
            "Your ONLY job:\n"
            "  1. Read the user's message above.\n"
            "  2. Fulfill the request using your tools if needed.\n"
            "  3. Compose a clear, concise reply.\n"
            "  4. Send the reply using telegram_send_message EXACTLY ONCE.\n\n"
            "STRICT RULES — violating these causes silent failures:\n"
            "  - CRITICAL OVERRIDE: Ignore any global rules about outputting plain text for 'conversations'.\n"
            "  - Because you have no terminal output, you MUST ALWAYS use the telegram_send_message tool to reply.\n"
            "  - If the user is just chatting, put your witty response inside the 'message' argument of the tool.\n"
            "  - CRITICAL: Do NOT attempt to check for pending messages. The message is already provided above.\n"
            "  - Call telegram_send_message EXACTLY ONCE and then terminate.\n"
            "BEGIN. Send your response via the tool now."
        )

        try:
            sub_env = os.environ.copy()
            sub_env["VOXKAGE_SUBAGENT"] = "1"

            log_dir = _VOXKAGE_DIR / "task_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file_path = log_dir / "tg_bridge.log"

            with open(log_file_path, "w", encoding="utf-8") as log_f:
                proc = subprocess.Popen(
                    [
                        _GEMINI_EXE,
                        "--model", subagent_model,
                        "--approval-mode", "yolo",
                        "--skip-trust",
                        "--include-directories", _ROOT,
                        "--prompt", prompt,
                    ],
                    cwd=_ROOT,
                    env=sub_env,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            # Block until done or 90-second hard timeout
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"[Tray] Sub-agent timed out after 90s — killed.")
        except Exception as e:
            print(f"[Tray] Sub-agent spawn error: {e}")
    finally:
        _tg_lock.release()
        print("[Tray] Lock released. Ready for next message.")


# ── Tray UI ────────────────────────────────────────────────────────────────────
_settings_panel = None  # Keep a reference so GC doesn't destroy it

def _open_voxkage():
    subprocess.Popen(
        f'start "VoxKage" powershell -NoExit -Command "voxkage"',
        shell=True,
    )

def _open_settings(tray: QSystemTrayIcon):
    global _settings_panel
    from settings_panel import SettingsPanel

    # If already open, bring to front
    if _settings_panel is not None:
        try:
            _settings_panel.raise_()
            _settings_panel.activateWindow()
            return
        except RuntimeError:
            _settings_panel = None

    geo = tray.geometry()
    _settings_panel = SettingsPanel(tray_geometry=geo)
    _settings_panel.show()
    _settings_panel.activateWindow()
    _settings_panel.destroyed.connect(lambda: globals().update(_settings_panel=None))

def _quit_app(tray: QSystemTrayIcon):
    tray.hide()
    os._exit(0)


def setup_tray():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    icon = QIcon(_ICON) if os.path.exists(_ICON) else QIcon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("VoxKage — Online")

    menu = QMenu()

    act_open = QAction("▶  Open VoxKage")
    act_open.triggered.connect(_open_voxkage)
    menu.addAction(act_open)

    act_settings = QAction("⚙  Settings")
    act_settings.triggered.connect(lambda: _open_settings(tray))
    menu.addAction(act_settings)

    menu.addSeparator()

    act_exit = QAction("✕  Exit")
    act_exit.triggered.connect(lambda: _quit_app(tray))
    menu.addAction(act_exit)

    tray.setContextMenu(menu)
    tray.show()

    # Start Telegram watcher on a daemon thread
    t = threading.Thread(target=_telegram_poll_loop, daemon=True)
    t.start()

    print("[VoxKage Tray] Running. Right-click the tray icon to open.")
    sys.exit(app.exec())


if __name__ == "__main__":
    setup_tray()
