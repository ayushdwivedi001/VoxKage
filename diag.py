"""
VoxKage — System Diagnostics
=============================
Tests:
  1. Telegram credentials (token + chat_id)
  2. Named Pipe IPC (tray/CLI communication)
  3. Telegram Watcher singleton status
  4. Inbox file state

Usage:
    python diag.py
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE    = Path.home() / ".voxkage" / ".env"
VOXKAGE_DIR = Path.home() / ".voxkage"
LOCK_FILE   = VOXKAGE_DIR / "telegram_watcher.lock"
INBOX_FILE  = VOXKAGE_DIR / "telegram_inbox.jsonl"
PIPE_NAME   = r"\\.\pipe\voxkage_ipc"

load_dotenv(str(ENV_FILE))
token   = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
chat_id = (os.environ.get("TELEGRAM_CHAT_ID")   or "").strip()

PASS = "  ✅"
FAIL = "  ❌"
WARN = "  ⚠️ "
INFO = "  ℹ️ "

def hr(c="─"): print(c * 58)

# ─────────────────────────────────────────────────────────────
# TEST 1: Telegram Credentials
# ─────────────────────────────────────────────────────────────
print()
hr("═")
print("  [1/4] TELEGRAM CREDENTIALS")
hr("═")

if not token:
    print(FAIL, "TELEGRAM_BOT_TOKEN is not set")
    sys.exit(1)

bot_own_id = token.split(":")[0]
print(INFO, f"Token     : {token[:12]}...  (Bot ID prefix: {bot_own_id})")
print(INFO, f"Chat ID   : {chat_id or '(not set)'}")

# Validate token
try:
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
    if r.get("ok"):
        bot = r["result"]
        print(PASS, f"Token valid → @{bot.get('username')} (ID: {bot.get('id')})")
    else:
        print(FAIL, f"Token rejected: {r.get('description')}")
        sys.exit(1)
except Exception as e:
    print(FAIL, f"Network error: {e}")
    sys.exit(1)

# Chat ID sanity check
if not chat_id:
    print(WARN, "TELEGRAM_CHAT_ID is not set — run: python fix_chat_id.py")
elif chat_id == bot_own_id:
    print(FAIL, f"Chat ID ({chat_id}) equals the Bot's own ID — this is wrong!")
    print("      Run: python fix_chat_id.py  to discover your real Chat ID")
else:
    # Try sending a message to confirm
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "🔍 VoxKage diagnostics ping"},
            timeout=10,
        ).json()
        if r.get("ok"):
            print(PASS, f"sendMessage to {chat_id} → OK (check your Telegram)")
        else:
            print(FAIL, f"sendMessage failed: {r.get('description')}")
            print(INFO, "  → If 'chat not found': send /start to your bot first")
    except Exception as e:
        print(FAIL, f"sendMessage error: {e}")

# ─────────────────────────────────────────────────────────────
# TEST 2: Named Pipe IPC
# ─────────────────────────────────────────────────────────────
print()
hr("═")
print("  [2/4] NAMED PIPE IPC  (\\\\.\\ pipe\\voxkage_ipc)")
hr("═")

if sys.platform != "win32":
    print(WARN, "Not Windows — Named Pipes not available. Using inbox file fallback.")
else:
    try:
        import win32file
        import pywintypes

        # Attempt to open the pipe (will fail with error 2 if server not running)
        try:
            handle = win32file.CreateFile(
                PIPE_NAME,
                win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None,
            )
            # If we got here, the server is listening
            test_payload = json.dumps({
                "source": "diagnostics",
                "text":   "[DIAG] IPC pipe test — ignore this message",
                "timestamp": time.time(),
            }) + "\n"
            win32file.WriteFile(handle, test_payload.encode("utf-8"))
            win32file.CloseHandle(handle)
            print(PASS, "Named Pipe server is RUNNING and accepted a test message")
            print(INFO, "  VoxKage CLI / Tray is active and listening on the IPC pipe")
        except pywintypes.error as e:
            if e.winerror == 2:  # ERROR_FILE_NOT_FOUND
                print(WARN, "Named Pipe server is NOT running (winerror 2 = no server)")
                print(INFO, "  This is normal when VoxKage CLI / Tray is not open.")
                print(INFO, "  The Telegram Watcher will fall back to headless Gemini mode.")
            elif e.winerror == 231:  # ERROR_PIPE_BUSY
                print(WARN, "Pipe is busy (another client is connected) — server is running")
            else:
                print(WARN, f"Pipe error (winerror {e.winerror}): {e}")

    except ImportError:
        print(WARN, "win32file (pywin32) not installed — IPC unavailable")
        print(INFO, "  Fix: pip install pywin32")

# ─────────────────────────────────────────────────────────────
# TEST 3: Telegram Watcher Singleton
# ─────────────────────────────────────────────────────────────
print()
hr("═")
print("  [3/4] TELEGRAM WATCHER STATUS")
hr("═")

if not LOCK_FILE.exists():
    print(WARN, f"Lock file not found: {LOCK_FILE}")
    print(INFO, "  The watcher has never been started, or cleaned up after last run.")
    print(INFO, "  Start it with: python voxkage/telegram_watcher.py")
else:
    try:
        pid = int(LOCK_FILE.read_text().strip())
        print(INFO, f"Lock file found — recorded PID: {pid}")

        try:
            import psutil
            if psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                cmdline = " ".join(proc.cmdline())
                if "telegram_watcher" in cmdline.lower() or "python" in cmdline.lower():
                    print(PASS, f"Watcher is RUNNING (PID {pid})")
                    print(INFO, f"  Command: {cmdline[:80]}...")
                else:
                    print(WARN, f"PID {pid} is alive but not the watcher: {cmdline[:60]}")
                    print(INFO, "  Stale lock file — safe to delete and restart watcher")
            else:
                print(FAIL, f"PID {pid} is NOT running — stale lock file")
                print(INFO, "  The watcher crashed or was killed. Lock file is stale.")
                print(INFO, "  The watcher will clean this up automatically on next start.")
        except ImportError:
            print(INFO, "  psutil not available — cannot verify process")

    except ValueError:
        print(WARN, "Lock file is corrupt (not a valid PID)")

# ─────────────────────────────────────────────────────────────
# TEST 4: Inbox File
# ─────────────────────────────────────────────────────────────
print()
hr("═")
print("  [4/4] INBOX FILE  (telegram_inbox.jsonl)")
hr("═")

if not INBOX_FILE.exists():
    print(INFO, f"Inbox file does not exist yet: {INBOX_FILE}")
    print(INFO, "  It will be created when the first Telegram message is received.")
else:
    size = INBOX_FILE.stat().st_size
    print(INFO, f"Inbox file: {INBOX_FILE}  ({size} bytes)")

    lines = INBOX_FILE.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    valid = [l for l in lines if l.strip()]
    print(INFO, f"Total entries: {len(valid)}")

    if valid:
        print(INFO, "Last 3 entries:")
        for line in valid[-3:]:
            try:
                entry = json.loads(line)
                ts = time.strftime("%H:%M:%S", time.localtime(entry.get("timestamp", 0)))
                print(f"       [{ts}] {entry.get('prompt', '')[:60]}")
            except Exception:
                print(f"       (raw) {line[:60]}")

# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
print()
hr("═")
print("  SUMMARY & NEXT STEPS")
hr("═")
print()
print("  If Chat ID issue was found:")
print("    python fix_chat_id.py            ← auto-discovers and fixes Chat ID")
print()
print("  To start the Telegram Watcher:")
print("    python voxkage/telegram_watcher.py")
print()
print("  To start full VoxKage (watcher auto-starts):")
print("    voxkage")
print()
hr("═")
