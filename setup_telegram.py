#!/usr/bin/env python3
"""
VoxKage — Telegram Bot Setup & Diagnostic Script
=================================================
Run this ONCE to:
  1. Validate your Bot API Token format and connectivity
  2. Discover your Telegram Chat ID automatically
  3. Write credentials securely to ~/.voxkage/.env
  4. Verify the full integration end-to-end

Usage:
    python setup_telegram.py
    python setup_telegram.py --token <YOUR_TOKEN>  # non-interactive
"""

import os
import sys
import re
import json
import time
import argparse
import getpass
from pathlib import Path

# ── Dependency guard ──────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print("❌  'requests' is not installed.")
    print("    Fix: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv, set_key, dotenv_values
except ImportError:
    print("❌  'python-dotenv' is not installed.")
    print("    Fix: pip install python-dotenv")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
VOXKAGE_DIR = Path.home() / ".voxkage"
ENV_FILE     = VOXKAGE_DIR / ".env"
TOKEN_REGEX  = re.compile(r"^\d{8,12}:[A-Za-z0-9_\-]{35,}$")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _hr(char="─", width=60):
    print(char * width)

def _ok(msg):   print(f"  ✅  {msg}")
def _warn(msg): print(f"  ⚠️   {msg}")
def _err(msg):  print(f"  ❌  {msg}")
def _info(msg): print(f"  ℹ️   {msg}")


def _ensure_voxkage_dir():
    VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
    # Create a skeleton .env if it doesn't exist
    if not ENV_FILE.exists():
        ENV_FILE.touch(mode=0o600)
        _info(f"Created {ENV_FILE}")


def _validate_token_format(token: str) -> tuple[bool, str]:
    """
    A Telegram Bot Token has the format:  <bot_id>:<secret>
    e.g.  7123456789:AAHxxxxxxxxxxxxxxxx-xxxxxxxxxxxx

    Returns (is_valid, diagnosis_message).
    """
    token = token.strip()

    if not token:
        return False, "Token is empty."

    if ":" not in token:
        return False, (
            "Token has no colon separator. "
            "The format must be  <numbers>:<alphanumeric_string>  — "
            "make sure you copied the FULL token from @BotFather."
        )

    parts = token.split(":", 1)
    bot_id, secret = parts

    if not bot_id.isdigit():
        return False, (
            f"The part before ':' should be digits only, got: '{bot_id}'. "
            "Double-check you didn't accidentally paste a partial token."
        )

    if len(bot_id) < 8 or len(bot_id) > 12:
        return False, (
            f"Bot ID '{bot_id}' is {len(bot_id)} digits long — "
            "expected 8-12 digits. Token may be truncated or incorrect."
        )

    if len(secret) < 35:
        return False, (
            f"Secret part is only {len(secret)} chars — expected ≥35. "
            "Token appears truncated."
        )

    if not TOKEN_REGEX.match(token):
        return False, (
            "Token contains unexpected characters. "
            "Only letters, digits, underscores and hyphens are allowed in the secret part."
        )

    return True, f"Format OK  (Bot ID: {bot_id})"


def _call_api(token: str, method: str, **params) -> dict | None:
    """Call Telegram Bot API. Returns parsed JSON or None on error."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"ok": False, "_network_error": True,
                "description": "Could not connect to api.telegram.org — check your internet connection."}
    except requests.exceptions.Timeout:
        return {"ok": False, "_network_error": True,
                "description": "Request timed out after 15 s."}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def _post_api(token: str, method: str, **params) -> dict | None:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.post(url, json=params, timeout=15)
        return resp.json()
    except Exception as e:
        return {"ok": False, "description": str(e)}


# ── Step 1: Token validation ──────────────────────────────────────────────────

def step_validate_token(token: str) -> str:
    """Validate format, then validate against the live API. Returns cleaned token."""
    print()
    _hr()
    print("  STEP 1 — Validate Bot Token")
    _hr()

    token = token.strip()

    # ── Format check ──
    valid, diagnosis = _validate_token_format(token)
    if valid:
        _ok(f"Token format valid. {diagnosis}")
    else:
        _err(f"Token format INVALID: {diagnosis}")
        print()
        print("  Common causes:")
        print("   • You only copied the numeric Bot ID, not the full token")
        print("   • The token was split across two lines when copied")
        print("   • The token contains invisible characters (try re-copying)")
        print()
        fix = input("  Paste the corrected token (or press Enter to abort): ").strip()
        if not fix:
            sys.exit(1)
        token = fix
        valid, diagnosis = _validate_token_format(token)
        if not valid:
            _err(f"Still invalid: {diagnosis}")
            sys.exit(1)
        _ok(f"Token format valid. {diagnosis}")

    # ── Live API check ──
    print()
    _info("Verifying token with Telegram API (getMe)...")
    result = _call_api(token, "getMe")

    if result is None:
        _err("Received no response from Telegram.")
        sys.exit(1)

    if result.get("_network_error"):
        _err(f"Network error: {result['description']}")
        print()
        print("  Troubleshooting:")
        print("   • Check your internet connection")
        print("   • If behind a proxy/VPN, Telegram may be blocked in your region")
        print("   • Try: curl https://api.telegram.org")
        sys.exit(1)

    if not result.get("ok"):
        err_code = result.get("error_code", "?")
        err_desc = result.get("description", "Unknown error")
        _err(f"Telegram API rejected the token (code {err_code}): {err_desc}")
        print()
        if err_code == 401:
            print("  ➜  Error 401 = Unauthorized. The token is invalid or has been revoked.")
            print("     Go to @BotFather → /mybots → select your bot → API Token → Revoke/Regenerate.")
        elif err_code == 404:
            print("  ➜  Error 404 = Bot not found. Make sure you copied the FULL token.")
        sys.exit(1)

    bot = result["result"]
    _ok(f"Token accepted by Telegram!")
    _ok(f"Bot name  : @{bot.get('username', '?')}")
    _ok(f"Bot ID    : {bot.get('id', '?')}")
    _ok(f"Display   : {bot.get('first_name', '?')}")
    return token


# ── Step 2: Chat ID discovery ─────────────────────────────────────────────────

def step_get_chat_id(token: str, existing_chat_id: str = "") -> str:
    """
    Return the user's Telegram Chat ID.
    If already set in env, confirm it. Otherwise auto-discover via polling.
    """
    print()
    _hr()
    print("  STEP 2 — Discover Your Chat ID")
    _hr()

    if existing_chat_id:
        _info(f"TELEGRAM_CHAT_ID already set in .env: {existing_chat_id}")
        confirm = input("  Use this Chat ID? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            return existing_chat_id
        print("  → Will discover a new Chat ID instead.")
        print()

    bot_username = _call_api(token, "getMe").get("result", {}).get("username", "your bot")

    print(f"  Open Telegram on your phone and send any message to @{bot_username}")
    print("  (e.g. just type  /start  or  hello)")
    print()
    input("  Press Enter AFTER you've sent the message... ")

    print()
    _info("Polling for your message (up to 30 s)...")

    deadline = time.time() + 30
    while time.time() < deadline:
        result = _call_api(token, "getUpdates", limit=10, timeout=5)
        updates = (result or {}).get("result", [])

        if updates:
            # Find the most recent message
            latest = max(updates, key=lambda u: u.get("update_id", 0))
            msg = latest.get("message") or latest.get("edited_message") or {}
            chat = msg.get("chat", {})
            chat_id = str(chat.get("id", ""))
            first_name = chat.get("first_name", "")
            username = chat.get("username", "")

            if chat_id:
                _ok(f"Chat ID found: {chat_id}")
                if first_name:
                    _ok(f"User name   : {first_name} (@{username})")
                return chat_id

        time.sleep(2)

    _warn("Could not auto-detect Chat ID within 30 s.")
    _info("You can find your Chat ID by messaging @userinfobot on Telegram.")
    manual = input("  Enter your Chat ID manually (or press Enter to skip): ").strip()
    return manual


# ── Step 3: Write .env securely ───────────────────────────────────────────────

def step_write_env(token: str, chat_id: str):
    print()
    _hr()
    print("  STEP 3 — Save Credentials to ~/.voxkage/.env")
    _hr()

    _ensure_voxkage_dir()

    # Use python-dotenv's set_key to safely update individual keys
    # without overwriting unrelated entries (Spotify, GitHub, etc.)
    set_key(str(ENV_FILE), "TELEGRAM_BOT_TOKEN", token, quote_mode="never")
    _ok(f"TELEGRAM_BOT_TOKEN written to {ENV_FILE}")

    if chat_id:
        set_key(str(ENV_FILE), "TELEGRAM_CHAT_ID", chat_id, quote_mode="never")
        _ok(f"TELEGRAM_CHAT_ID    written to {ENV_FILE}")
    else:
        _info("TELEGRAM_CHAT_ID skipped (will be auto-detected on first message).")

    # Secure the file (owner-read-only on non-Windows)
    if sys.platform != "win32":
        ENV_FILE.chmod(0o600)
        _ok("File permissions set to 600 (owner-only read/write).")

    _info("The .env file is already in .gitignore — your credentials are safe.")


# ── Step 4: End-to-end verification ──────────────────────────────────────────

def step_verify(token: str, chat_id: str):
    print()
    _hr()
    print("  STEP 4 — End-to-End Verification")
    _hr()

    if not chat_id:
        _warn("No Chat ID available — skipping live message test.")
        return

    test_msg = (
        "✅ VoxKage Telegram integration verified!\n\n"
        "Your bot is correctly configured and can reach you.\n"
        "You're all set to use VoxKage remotely. 🎉"
    )
    _info("Sending a test message to your Telegram...")
    result = _post_api(token, "sendMessage", chat_id=chat_id, text=test_msg)

    if result and result.get("ok"):
        _ok("Test message sent successfully! Check your Telegram.")
    else:
        desc = (result or {}).get("description", "Unknown error")
        _warn(f"Could not send test message: {desc}")
        _info("The token is valid — this may be a Chat ID issue.")
        _info("Make sure you've sent at least one message TO the bot first.")


# ── Step 5: Summary ───────────────────────────────────────────────────────────

def step_summary(token: str, chat_id: str):
    print()
    _hr("═")
    print("  SETUP COMPLETE")
    _hr("═")
    print()
    print("  Your credentials are stored in:")
    print(f"    {ENV_FILE}")
    print()
    print("  Environment variables set:")
    print(f"    TELEGRAM_BOT_TOKEN = {token[:10]}...{token[-6:]}  (masked for display)")
    if chat_id:
        print(f"    TELEGRAM_CHAT_ID   = {chat_id}")
    print()
    print("  Next steps:")
    print("    1. Start VoxKage:  voxkage  (or: python -m voxkage)")
    print("    2. The Telegram watcher starts automatically in the background.")
    print("    3. Send any message to your bot on Telegram — VoxKage will respond.")
    print()
    print("  To restart the watcher manually:")
    print("    python voxkage/telegram_watcher.py")
    print()
    _hr("═")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="VoxKage Telegram Bot Setup & Diagnostic"
    )
    parser.add_argument("--token",   help="Bot API token (non-interactive mode)")
    parser.add_argument("--chat-id", help="Chat ID to skip auto-discovery")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the live message verification step")
    args = parser.parse_args()

    print()
    _hr("═")
    print("  VoxKage — Telegram Bot Setup")
    _hr("═")
    print()
    print("  This script will:")
    print("   1. Validate your Bot API Token (format + live API)")
    print("   2. Discover your Telegram Chat ID")
    print("   3. Save credentials securely to ~/.voxkage/.env")
    print("   4. Send a verification message to confirm everything works")
    print()
    _info(f"Credentials will be stored in: {ENV_FILE}")

    # Load existing .env so we can show current values
    _ensure_voxkage_dir()
    load_dotenv(str(ENV_FILE), override=False)
    existing_token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    existing_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    # ── Acquire token ──────────────────────────────────────────────────────────
    if args.token:
        raw_token = args.token
    elif existing_token:
        _info(f"Found existing token in .env: {existing_token[:10]}...{existing_token[-4:]}")
        use_existing = input("  Use existing token? [Y/n]: ").strip().lower()
        if use_existing in ("", "y", "yes"):
            raw_token = existing_token
        else:
            raw_token = getpass.getpass("  Paste your new Bot Token (input hidden): ")
    else:
        print()
        print("  Get your token from @BotFather on Telegram:")
        print("    1. Open Telegram → search @BotFather")
        print("    2. Send /newbot  and follow the prompts")
        print("    3. Copy the token it gives you (format: 1234567890:ABC...)")
        print()
        raw_token = getpass.getpass("  Paste your Bot Token (input hidden): ")

    # ── Run steps ─────────────────────────────────────────────────────────────
    token = step_validate_token(raw_token)

    chat_id = args.chat_id or ""
    chat_id = step_get_chat_id(token, existing_chat_id if not args.chat_id else args.chat_id)

    step_write_env(token, chat_id)

    if not args.no_verify:
        step_verify(token, chat_id)

    step_summary(token, chat_id)


if __name__ == "__main__":
    main()
