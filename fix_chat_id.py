"""
VoxKage — Chat ID Auto-Fix
==========================
Your TELEGRAM_CHAT_ID is currently set to the Bot's own ID (same as the Bot ID
in your token). This is wrong — you cannot send messages to yourself as a bot.

This script:
1. Fetches recent updates from your bot
2. Shows you all chat IDs it has seen (your personal ID will be here)
3. Fixes ~/.voxkage/.env automatically
"""

import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv, set_key

ENV_FILE = Path.home() / ".voxkage" / ".env"
load_dotenv(str(ENV_FILE))

token   = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
old_cid = (os.environ.get("TELEGRAM_CHAT_ID")   or "").strip()

if not token:
    print("ERROR: TELEGRAM_BOT_TOKEN not set in", ENV_FILE)
    sys.exit(1)

# Extract the bot's own numeric ID from the token (everything before the ":")
bot_own_id = token.split(":")[0]
print(f"\nBot's own ID (from token): {bot_own_id}")
print(f"Current TELEGRAM_CHAT_ID : {old_cid}")

if old_cid == bot_own_id:
    print("\n⚠️  PROBLEM CONFIRMED: Your Chat ID equals your Bot ID.")
    print("   The bot cannot send messages to itself.")
    print("   Your Chat ID should be YOUR personal Telegram account ID.\n")
else:
    print("\n✅ Chat ID does not match Bot ID — may be a different issue.")

print("Fetching recent messages your bot has received (getUpdates)...")
r = requests.get(
    f"https://api.telegram.org/bot{token}/getUpdates",
    params={"limit": 20},
    timeout=15,
)
data = r.json()

if not data.get("ok"):
    print(f"ERROR from Telegram: {data.get('description')}")
    sys.exit(1)

updates = data.get("result", [])
if not updates:
    print("\n❌ No updates found — your bot hasn't received any messages yet.")
    print("   ACTION REQUIRED:")
    print("   1. Open Telegram on your phone")
    print("   2. Search for @Jarvish12Bot")
    print("   3. Tap START or send any message (e.g. 'hello')")
    print("   4. Re-run this script\n")
    sys.exit(1)

# Collect unique chat IDs that are NOT the bot itself
personal_ids = {}
for u in updates:
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat", {})
    frm  = msg.get("from", {})
    cid  = str(chat.get("id", ""))
    if cid and cid != bot_own_id:
        personal_ids[cid] = {
            "username": frm.get("username", ""),
            "first_name": frm.get("first_name", ""),
            "text": (msg.get("text") or "")[:40],
        }

if not personal_ids:
    print("\n❌ All updates are from the bot itself — no human messages found.")
    print("   Send a message to @Jarvish12Bot from YOUR Telegram account first,")
    print("   then re-run this script.")
    sys.exit(1)

print(f"\nFound {len(personal_ids)} unique human chat(s):\n")
ids_list = list(personal_ids.items())
for i, (cid, info) in enumerate(ids_list, 1):
    name = info["first_name"] or info["username"] or "Unknown"
    print(f"  [{i}] Chat ID: {cid}  |  Name: {name} (@{info['username']})  |  Last msg: '{info['text']}'")

print()
if len(ids_list) == 1:
    chosen_id = ids_list[0][0]
    print(f"Auto-selecting the only human Chat ID: {chosen_id}")
else:
    choice = input(f"Enter the number of your Chat ID [1-{len(ids_list)}]: ").strip()
    try:
        chosen_id = ids_list[int(choice) - 1][0]
    except (ValueError, IndexError):
        print("Invalid choice.")
        sys.exit(1)

# Write to .env
set_key(str(ENV_FILE), "TELEGRAM_CHAT_ID", chosen_id, quote_mode="never")
print(f"\n✅ TELEGRAM_CHAT_ID updated to: {chosen_id}")
print(f"   Saved in: {ENV_FILE}\n")

# Verify by sending a test message
print("Sending verification message...")
resp = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": chosen_id, "text": "✅ VoxKage Chat ID fixed! Bot is now correctly configured. 🎉"},
    timeout=10,
).json()

if resp.get("ok"):
    print("✅ Message sent! Check your Telegram — setup is complete.\n")
else:
    print(f"⚠️  Send failed: {resp.get('description')}")
    print("   Try sending /start to your bot first, then retry.\n")
