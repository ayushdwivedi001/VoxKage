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

from voxkage._env import load_voxkage_env
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
# Priority order:
#   1. Named Pipe IPC (\\.\pipe\voxkage_ipc) — zero UI interference
#   2. pyautogui clipboard injection — legacy fallback (Windows only)
#   3. Inbox file (telegram_inbox.jsonl) — universal fallback
#
# The tray_app.py already has its own sub-agent spawning path, so this
# watcher is primarily used when the tray is NOT running.

def _inject_via_ipc(prompt: str) -> bool:
    """
    Try injecting via Named Pipe IPC.
    Returns True ONLY if the Named Pipe server was listening and accepted the
    message — NOT if it fell through to the inbox-file fallback.
    """
    if sys.platform != "win32":
        return False  # Named Pipes are Windows-only
    try:
        import win32file
        import pywintypes
        import json, time

        payload = json.dumps({
            "source": "telegram",
            "text": prompt,
            "timestamp": time.time(),
        }, ensure_ascii=False)

        handle = win32file.CreateFile(
            r"\\.\pipe\voxkage_ipc",
            win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None,
        )
        try:
            win32file.WriteFile(handle, (payload + "\n").encode("utf-8"))
            log.info("[IPC] Message delivered via Named Pipe")
            return True
        finally:
            win32file.CloseHandle(handle)

    except ImportError:
        log.debug("[IPC] win32file not available")
        return False
    except Exception as e:
        # ERROR_FILE_NOT_FOUND (winerror 2) = pipe server not running — expected
        log.debug(f"[IPC] Named Pipe not available: {e}")
        return False


# ── Headless Gemini processor (when VoxKage terminal is not open) ─────────────

def _find_gemini_exe() -> str | None:
    """Find the gemini CLI executable."""
    import shutil
    cmd = shutil.which("gemini")
    if cmd:
        return cmd
    # Windows: npm global installs as .cmd wrapper
    for name in ("gemini.cmd", "gemini"):
        p = Path.home() / "AppData" / "Roaming" / "npm" / name
        if p.exists():
            return str(p)
    return None


def _process_via_headless_gemini(prompt: str) -> bool:
    """
    When the VoxKage terminal is NOT running, spawn a headless Gemini/VoxKage
    subprocess to process the Telegram message and send the answer back.

    Architecture:
        telegram_watcher  →  gemini CLI (headless, VOXKAGE_ACTIVE=1)
                                  ↓ MCP tools (all 18 servers loaded)
                                  ↓ telegram_send_message  →  User's phone

    The prompt is wrapped with a REMOTE MODE instruction that forces VoxKage
    to call telegram_send_message at the end of every task.
    """
    import subprocess

    gemini = _find_gemini_exe()
    if not gemini:
        log.warning("[Headless] gemini CLI not found in PATH — cannot process headlessly")
        return False

    # ~/.voxkage/.gemini contains GEMINI.md + settings.json with all 18 MCP servers
    vk_gemini_home = str(_VOXKAGE_DIR / ".gemini")

    # Inject the REMOTE MODE system instruction so VoxKage knows to send to Telegram
    full_prompt = (
        f"{prompt}\n"
        f"[SYSTEM REMOTE MODE: You are running as a headless background agent for "
        f"a Telegram user. After completing this task, you MUST call the "
        f"telegram_send_message tool with your complete response so the user sees "
        f"the result on their phone. Keep the Telegram message under 4000 chars.]"
    )

    env = os.environ.copy()
    env["VOXKAGE_ACTIVE"] = "1"

    log.info("[Headless] VoxKage terminal not found — processing Telegram message headlessly")
    _send_telegram("⚙️ Received. Processing headlessly — reply coming shortly...")

    try:
        # On Windows, .CMD files MUST be invoked with shell=True.
        # We pipe the prompt via stdin instead of a positional arg so that
        # special characters like [TELEGRAM MESSAGE] aren't mangled by cmd.exe.
        use_shell = sys.platform == "win32" and gemini.lower().endswith(".cmd")
        proc = subprocess.Popen(
            [gemini, "-m", "gemini-2.5-flash",
             "--include-directories", vk_gemini_home,
             "--skip-trust"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            shell=use_shell,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            _, stderr = proc.communicate(input=full_prompt + "\n", timeout=180)  # 3-minute ceiling
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.error("[Headless] Gemini timed out after 180s")
            _send_telegram(
                "⏰ Your request timed out (3 min limit).\n"
                "For complex tasks, please open VoxKage on your PC."
            )
            return True  # We attempted it

        if proc.returncode != 0 and stderr:
            log.warning(f"[Headless] Gemini stderr: {stderr[:300]}")

        log.info(f"[Headless] Subprocess finished (rc={proc.returncode})")
        return True

    except Exception as e:
        log.error(f"[Headless] Failed to spawn Gemini subprocess: {e}")
        _send_telegram(f"❌ Error processing your request headlessly: {str(e)[:200]}")
        return False


_GEMINI_WINDOW_TITLES = [
    "gemini", "voxkage", "vision-assistant"
]


def _find_gemini_hwnd():
    """
    Find the terminal window running VoxKage / Gemini CLI.

    Strategy A: match by window title keywords (fast).
    Strategy B: find node.exe running the gemini CLI via psutil,
                then find the console window attached to that process
                (handles Windows Terminal where tab titles aren't always
                 exposed as hwnd titles).
    """
    try:
        import win32gui, win32process

        # Strategy A — title keyword match
        title_hits = []
        def _cb_title(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).lower()
            if any(t in title for t in _GEMINI_WINDOW_TITLES):
                title_hits.append(hwnd)
        win32gui.EnumWindows(_cb_title, None)
        if title_hits:
            log.debug(f"[inject] Found window by title: {win32gui.GetWindowText(title_hits[0])}")
            return title_hits[0]

        # Strategy B — find node.exe process running gemini, get its console hwnd
        try:
            import psutil
            gemini_pids = set()
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (proc.info["name"] or "").lower()
                    cmdline = " ".join(proc.info["cmdline"] or []).lower()
                    if "node" in name and "gemini" in cmdline:
                        gemini_pids.add(proc.pid)
                        # Also include parent process (terminal emulator)
                        try:
                            parent = proc.parent()
                            if parent:
                                gemini_pids.add(parent.pid)
                        except Exception:
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if gemini_pids:
                proc_hits = []
                def _cb_pid(hwnd, _):
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if pid in gemini_pids:
                            proc_hits.append(hwnd)
                    except Exception:
                        pass
                win32gui.EnumWindows(_cb_pid, None)
                if proc_hits:
                    log.debug(f"[inject] Found window via process tree (node.exe gemini)")
                    return proc_hits[0]
        except ImportError:
            pass  # psutil not installed — Strategy B unavailable

    except Exception as e:
        log.debug(f"[inject] hwnd search error: {e}")
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

                    # Immediate acknowledgement — user sees something is happening
                    _send_telegram("🤔 Thinking...")

                    # ── Injection chain ────────────────────────────────────
                    # Priority 1: Named Pipe IPC (if IPC server thread is running)
                    injected = _inject_via_ipc(prompt)

                    # Priority 2: pyautogui keyboard injection (VoxKage window is open)
                    if not injected:
                        injected = _inject_via_pyautogui(prompt)

                    # Priority 3: Headless Gemini subprocess (VoxKage is closed)
                    # This is the REMOTE CONTROL path — full MCP toolset, sends reply via Telegram
                    if not injected:
                        injected = _process_via_headless_gemini(prompt)

                    # Always write to inbox as a persistent log record
                    _inject_via_inbox_file({
                        "update_id": uid,
                        "prompt": prompt,
                        "injected": injected,
                        "method": (
                            "ipc" if injected and _inject_via_ipc.__name__ else
                            "pyautogui" if injected else
                            "headless" if injected else
                            "failed"
                        ),
                        "timestamp": time.time(),
                    })

                    if not injected:
                        _send_telegram(
                            "❌ VoxKage could not process your request.\n"
                            "The terminal is closed and the headless processor is unavailable.\n"
                            "Please open VoxKage on your PC and try again."
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