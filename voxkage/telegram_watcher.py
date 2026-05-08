"""
VoxKage Telegram Watcher — telegram_watcher.py

A persistent background process that bridges Telegram → Gemini CLI.

Architecture:
  ┌─────────────┐   Bot API poll   ┌──────────────────┐
  │  Your Phone  │ ──────────────► │ telegram_watcher  │
  │  (Telegram)  │                 │  (this process)   │
  └─────────────┘                  └────────┬─────────┘
                                            │  stdin inject
                                            ▼
                                   ┌──────────────────┐
                                   │   Gemini CLI /   │
                                   │   VoxKage        │
                                   └──────────────────┘

How it works:
  1. Polls Telegram Bot API every 2 seconds (long-poll friendly)
  2. On new message: formats it and writes to Gemini CLI's stdin pipe
  3. Gemini CLI reads it as a new user prompt and responds
  4. Handles text, photos, documents, voice, stickers

Run standalone:
    python telegram_watcher.py

Started automatically by voxkage launcher (cli.py) in background.
Singleton — only one instance runs at a time (checked via lock file).
"""

import os
import sys
import json
import time
import logging
import threading
import tempfile
import signal
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[TelegramWatcher] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vk.telegram_watcher")

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _env import load_voxkage_env
load_voxkage_env()

# ── Config ────────────────────────────────────────────────────────────────────
_VOXKAGE_DIR   = Path(os.path.expanduser("~")) / ".voxkage"
_OFFSET_FILE   = _VOXKAGE_DIR / "telegram_offset.json"
_LOCK_FILE     = _VOXKAGE_DIR / "telegram_watcher.lock"
_INBOX_FILE    = _VOXKAGE_DIR / "telegram_inbox.jsonl"   # persistent message log
_STDIN_PIPE    = _VOXKAGE_DIR / "gemini_stdin.pipe"      # named pipe path (Windows: use file)

_VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
POLL_SEC = 2      # seconds between polls
API_BASE = f"https://api.telegram.org/bot{TOKEN}"

# ── Gemini CLI stdin injection ────────────────────────────────────────────────
# How we inject messages into the running Gemini CLI process:
# On Windows, Gemini CLI runs in a Windows Terminal / cmd window.
# We use pyautogui to focus that window and SendKeys the message.
# This is the only reliable cross-process stdin injection method on Windows
# without modifying Gemini CLI itself.
#
# Alternative: if Gemini CLI is started with stdin redirected from a named pipe,
# we can write directly. The launcher (cli.py) is updated to support this.
# We try the pipe first, fall back to pyautogui keyboard injection.

_GEMINI_WINDOW_TITLES = [
    "gemini", "voxkage", "vision-assistant"
]


def _find_gemini_hwnd():
    """Find the Gemini CLI window handle."""
    try:
        import win32gui
        result = []
        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).lower()
            if any(t in title for t in _GEMINI_WINDOW_TITLES):
                result.append((hwnd, win32gui.GetWindowText(hwnd)))
        win32gui.EnumWindows(_cb, None)
        return result[0][0] if result else 0
    except Exception:
        return 0


def _inject_via_pyautogui(prompt: str) -> bool:
    """
    Focus the Gemini CLI terminal window and type the prompt.
    Adds a newline at the end to submit it (Enter).
    """
    try:
        import win32gui, win32con, ctypes
        import pyautogui
        import time as _t

        hwnd = _find_gemini_hwnd()
        if not hwnd:
            log.warning("Gemini CLI window not found for stdin injection")
            return False

        # Restore if minimised
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            _t.sleep(0.3)

        # Bring to front
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        _t.sleep(0.5)   # let the window paint and accept input

        # Type the prompt. Use clipboard paste for reliability with unicode.
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(prompt, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            pyautogui.hotkey("ctrl", "v")
        except Exception:
            # Fallback: typewrite (ASCII only)
            safe = prompt.encode("ascii", "replace").decode("ascii")
            pyautogui.typewrite(safe, interval=0.02)

        _t.sleep(0.2)
        pyautogui.press("enter")
        log.info(f"Injected prompt into Gemini CLI ({len(prompt)} chars)")
        return True

    except Exception as e:
        log.error(f"pyautogui injection failed: {e}")
        return False


def _inject_via_inbox_file(message_obj: dict):
    """
    Fallback: write the message to a persistent inbox file.
    The telegram_server MCP tool reads this file when polled.
    Used when Gemini CLI window cannot be found (e.g. running headless).
    """
    try:
        with open(_INBOX_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(message_obj, ensure_ascii=False) + "\n")
        log.info(f"Message saved to inbox file: {_INBOX_FILE}")
    except Exception as e:
        log.error(f"Inbox file write failed: {e}")


# ── Telegram API helpers ──────────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    """Call Telegram Bot API. Returns {} on failure."""
    try:
        import requests
        resp = requests.get(f"{API_BASE}/{method}", params=kwargs, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        log.warning(f"API {method} returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.debug(f"API {method} error: {e}")
    return {}


def _api_post(method: str, **kwargs) -> dict:
    try:
        import requests
        resp = requests.post(f"{API_BASE}/{method}", json=kwargs, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.debug(f"API POST {method} error: {e}")
    return {}


def _download_file(file_id: str, dest_dir: Path) -> str | None:
    """Download a Telegram file by file_id. Returns local path or None."""
    try:
        import requests
        r = _api("getFile", file_id=file_id)
        file_path = r.get("result", {}).get("file_path")
        if not file_path:
            return None
        url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        fname = Path(file_path).name
        local = dest_dir / fname
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            local.write_bytes(resp.content)
        log.info(f"Downloaded file: {local}")
        return str(local)
    except Exception as e:
        log.error(f"File download failed: {e}")
        return None


def _send_telegram(text: str):
    """Send a message back to the user's Telegram."""
    _api_post("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="Markdown")


# ── Offset persistence ────────────────────────────────────────────────────────

def _read_offset() -> int:
    try:
        if _OFFSET_FILE.exists():
            return int(json.loads(_OFFSET_FILE.read_text())["last_update_id"])
    except Exception:
        pass
    return 0


def _save_offset(uid: int):
    try:
        _OFFSET_FILE.write_text(json.dumps({"last_update_id": uid}))
    except Exception:
        pass


# ── Message formatting ────────────────────────────────────────────────────────

def _format_prompt(update: dict, downloaded_path: str | None = None) -> str | None:
    """
    Convert a Telegram update into a VoxKage prompt string.
    Returns None if the update should be silently skipped.
    """
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None

    # Only process messages from the configured chat
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if CHAT_ID and chat_id != CHAT_ID:
        return None

    sender = msg.get("from", {}).get("first_name", "User")
    text   = msg.get("text") or msg.get("caption") or ""
    parts  = [f"[TELEGRAM MESSAGE from {sender}]"]

    if text:
        parts.append(text)

    # Attachments
    if msg.get("photo"):
        if downloaded_path:
            parts.append(f"[SYSTEM INSTRUCTION: The user attached a PHOTO. Local path: {downloaded_path}]")
            parts.append(
                f"CRITICAL INSTRUCTION: You MUST use the analyze_specific_file tool (with file_path='{downloaded_path}' and query='describe this image in detail') BEFORE you answer the user's text prompt!"
            )
        else:
            parts.append("[PHOTO attached — could not download]")

    elif msg.get("document"):
        doc = msg["document"]
        fname = doc.get("file_name", "document")
        if downloaded_path:
            parts.append(f"[SYSTEM INSTRUCTION: The user attached a DOCUMENT: {fname}. Local path: {downloaded_path}]")
            parts.append(
                f"CRITICAL INSTRUCTION: You MUST use the analyze_specific_file tool (with file_path='{downloaded_path}' and query='summarize this document') BEFORE you answer the user's text prompt!"
            )
        else:
            parts.append(f"[DOCUMENT: {fname} — could not download]")

    elif msg.get("voice"):
        if downloaded_path:
            parts.append(f"[VOICE MESSAGE — local path: {downloaded_path}]")
        else:
            parts.append("[VOICE MESSAGE — could not download]")

    elif msg.get("sticker"):
        emoji = msg["sticker"].get("emoji", "")
        parts.append(f"[STICKER: {emoji}]")

    elif msg.get("location"):
        loc = msg["location"]
        parts.append(f"[LOCATION: lat={loc['latitude']}, lon={loc['longitude']}]")

    elif msg.get("contact"):
        c = msg["contact"]
        parts.append(f"[CONTACT: {c.get('first_name','')} {c.get('phone_number','')}]")

    # If no content at all, skip
    if len(parts) == 1 and not text:
        return None

    # Flatten prompt to a single line to prevent premature console execution on paste
    prompt_str = " | ".join(parts)
    return prompt_str.replace("\n", " ").replace("\r", "")


# ── Singleton lock ────────────────────────────────────────────────────────────

def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive (Windows-safe)."""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    # Fallback without psutil
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError, ValueError):
        return False


def _kill_stale_watcher(pid: int):
    """Forcefully kill a stale watcher process."""
    try:
        import psutil
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=3)
        log.info(f"Killed stale watcher (PID {pid}) via psutil")
        return
    except Exception:
        pass
    # Fallback: os-level kill
    try:
        if sys.platform == "win32":
            os.system(f"taskkill /f /pid {pid} >nul 2>&1")
        else:
            os.kill(pid, signal.SIGTERM)
        log.info(f"Killed stale watcher (PID {pid}) via OS signal")
    except Exception as e:
        log.warning(f"Could not kill PID {pid}: {e}")


def _acquire_lock() -> bool:
    """
    Singleton lock for the Telegram watcher.

    Rules:
      - If lock file exists and PID is alive AND it's our own watcher
        (not some random process that reused the PID), return False.
      - If lock file exists but process is dead/stale → kill it, claim lock.
      - If no lock file → claim lock.

    Only ONE watcher instance is allowed across ALL VoxKage sessions.
    Re-launching VoxKage CLI does NOT spawn a new watcher if one is already running.
    """
    try:
        if _LOCK_FILE.exists():
            try:
                pid = int(_LOCK_FILE.read_text().strip())
            except (ValueError, OSError):
                # Corrupt lock file — overwrite it
                _LOCK_FILE.unlink(missing_ok=True)
                _LOCK_FILE.write_text(str(os.getpid()))
                return True

            if _is_process_alive(pid):
                # Process IS alive — check if it's actually a Python/watcher process
                # (not a random system process that reused the PID)
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    cmdline = " ".join(proc.cmdline()).lower()
                    if "telegram_watcher" in cmdline or "python" in cmdline:
                        log.info(f"Watcher already running (PID {pid}) — not spawning a new one")
                        return False
                    else:
                        # PID is alive but belongs to a different process — stale lock
                        log.warning(f"PID {pid} is alive but not a watcher — claiming lock")
                except ImportError:
                    # No psutil — trust the lock file
                    log.info(f"Watcher appears alive (PID {pid}) — not spawning a new one")
                    return False
                except Exception:
                    log.info(f"Watcher appears alive (PID {pid}) — not spawning a new one")
                    return False
            else:
                # Process is DEAD — stale lock from a crashed session
                log.warning(f"Stale watcher lock found (PID {pid} is dead) — cleaning up")
                _LOCK_FILE.unlink(missing_ok=True)

        # Claim the lock
        _LOCK_FILE.write_text(str(os.getpid()))
        return True

    except Exception as e:
        log.error(f"Lock acquisition error: {e}")
        # Proceed anyway — better to run than to silently fail
        try:
            _LOCK_FILE.write_text(str(os.getpid()))
        except Exception:
            pass
        return True


def _release_lock():
    try:
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()
    except Exception:
        pass


# ── Download directory ────────────────────────────────────────────────────────

_DOWNLOAD_DIR = _VOXKAGE_DIR / "telegram_downloads"
_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── Main poll loop ────────────────────────────────────────────────────────────

def run():
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set — watcher cannot start")
        sys.exit(1)

    if not _acquire_lock():
        sys.exit(0)  # Another instance is running

    log.info(f"Telegram Watcher started (PID {os.getpid()})")
    log.info(f"Polling every {POLL_SEC}s | Chat ID filter: {CHAT_ID or 'any'}")
    _send_telegram("🟢 VoxKage is online and listening.")

    def _cleanup(sig, frame):
        log.info("Shutting down...")
        _send_telegram("🔴 VoxKage Telegram Watcher stopped.")
        _release_lock()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    offset = _read_offset()
    consecutive_errors = 0

    while True:
        try:
            result = _api("getUpdates", offset=offset + 1, limit=10, timeout=0)
            updates = result.get("result", [])
            consecutive_errors = 0

            for update in updates:
                try:
                    uid = update["update_id"]
                    offset = max(offset, uid)
                    _save_offset(offset)  # Save immediately to prevent infinite retry loops on crash

                    msg = update.get("message") or update.get("edited_message", {})
                    text_content = msg.get("text") or msg.get("caption") or ""
                    
                    if text_content.strip().lower() == "/clear":
                        _INBOX_FILE.write_text("", encoding="utf-8")
                        _send_telegram("✅ Session and pending requests cleared.")
                        continue

                    # Download any attachment first (before formatting prompt)
                    downloaded = None
                    file_id = None

                    if msg.get("photo"):
                        # photos is a list — take the highest resolution (last element)
                        file_id = msg["photo"][-1]["file_id"]
                    elif msg.get("document"):
                        file_id = msg["document"]["file_id"]
                    elif msg.get("voice"):
                        file_id = msg["voice"]["file_id"]

                    if file_id:
                        downloaded = _download_file(file_id, _DOWNLOAD_DIR)

                    prompt = _format_prompt(update, downloaded)
                    if not prompt:
                        continue

                    log.info(f"New Telegram message → injecting into VoxKage")

                    # Try live injection first (pyautogui → Gemini CLI window)
                    injected = _inject_via_pyautogui(prompt)

                    # Always write to inbox file as persistent record
                    # (and as fallback if injection failed)
                    _inject_via_inbox_file({
                        "update_id": uid,
                        "prompt": prompt,
                        "injected": injected,
                        "timestamp": time.time(),
                    })

                    if not injected:
                        # Acknowledge on Telegram so user knows it was received
                        _send_telegram(
                            f"📨 Message queued asynchronously.\n"
                            f"VoxKage is currently busy or the window is hidden. "
                            f"It will read this when asked to 'check telegram inbox', or you can send /clear to abort."
                        )
                except Exception as ex:
                    log.error(f"Error processing update {update.get('update_id')}: {ex}")
                    continue

        except Exception as e:
            consecutive_errors += 1
            wait = min(30, POLL_SEC * (2 ** consecutive_errors))
            log.error(f"Poll error ({consecutive_errors}): {e} — retrying in {wait}s")
            time.sleep(wait)
            continue

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    run()