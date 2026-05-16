"""
VoxKage — Telegram Bot Connection Test
=======================================
Quick smoke test to verify your token and chat_id are correctly configured.
Reads credentials from ~/.voxkage/.env (the canonical VoxKage secrets file).

Usage:
    python test_telegram.py
"""
import os
import sys
import requests
from dotenv import load_dotenv

# ── Load credentials from the canonical VoxKage .env ─────────────────────────
env_path = os.path.expanduser("~/.voxkage/.env")
load_dotenv(env_path)

token   = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
chat_id = (os.environ.get("TELEGRAM_CHAT_ID")   or "").strip()

# ── Preflight checks ──────────────────────────────────────────────────────────
if not token:
    print("❌  TELEGRAM_BOT_TOKEN is not set.")
    print(f"    Edit {env_path} and add:  TELEGRAM_BOT_TOKEN=<your_token>")
    print("    Or run: python setup_telegram.py")
    sys.exit(1)

if ":" not in token:
    print("❌  TELEGRAM_BOT_TOKEN looks malformed (no ':' separator).")
    print("    A valid token looks like: 7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    print("    Re-copy your token from @BotFather on Telegram.")
    sys.exit(1)

print(f"Token   : {token[:10]}...{token[-4:]}  (masked)")
print(f"Chat ID : {chat_id or '(not set — will be auto-detected)'}")
print()

# ── Step 1: Verify token with getMe ──────────────────────────────────────────
print("[ 1/3 ] Calling getMe ...")
try:
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getMe",
        timeout=10,
    )
    data = r.json()
    if data.get("ok"):
        bot = data["result"]
        print(f"  ✅  Bot verified: @{bot.get('username')} (ID: {bot.get('id')})")
    else:
        code = data.get("error_code", "?")
        desc = data.get("description", "unknown")
        print(f"  ❌  getMe failed (code {code}): {desc}")
        if code == 401:
            print("      → Token is invalid/revoked. Get a new one from @BotFather.")
        sys.exit(1)
except requests.exceptions.ConnectionError:
    print("  ❌  Network error — check your internet connection.")
    sys.exit(1)
except requests.exceptions.Timeout:
    print("  ❌  Request timed out.")
    sys.exit(1)

# ── Step 2: Send a test message (only if chat_id is known) ───────────────────
print()
print("[ 2/3 ] Sending test message ...")
if not chat_id:
    print("  ⚠️   TELEGRAM_CHAT_ID not set — skipping sendMessage test.")
    print("       Run: python setup_telegram.py  to discover your Chat ID.")
else:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ VoxKage test message — connection OK!"},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            print("  ✅  Message sent! Check your Telegram.")
        else:
            code = data.get("error_code", "?")
            desc = data.get("description", "unknown")
            print(f"  ❌  sendMessage failed (code {code}): {desc}")
            if code == 400 and "chat not found" in desc.lower():
                print("      → Chat ID is wrong or you haven't messaged the bot yet.")
                print("        Open Telegram, find your bot, and send /start first.")
    except Exception as e:
        print(f"  ❌  Error: {e}")

# ── Step 3: Summary ───────────────────────────────────────────────────────────
print()
print("[ 3/3 ] Done.")
print()
print("  If everything above shows ✅, your Telegram bot is correctly configured.")
print("  Start VoxKage normally ('voxkage') and the watcher will launch automatically.")
print("  To run the full interactive setup: python setup_telegram.py")
