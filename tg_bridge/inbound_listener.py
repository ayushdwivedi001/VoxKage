"""
tg_bridge/inbound_listener.py — Standalone Telegram Inbound Daemon
===================================================================
Runs as a background process. Listens for incoming Telegram messages
and writes them to .tg_inbound in the project root so the Gemini CLI
can read them on the next interaction.

Launch: python tg_bridge/inbound_listener.py
The tray app starts this automatically as a daemon process.
"""

import os
import sys
import time
import json
import logging
import requests
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TelegramListener] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
INBOUND_FILE = os.path.join(_ROOT, ".tg_inbound")
POLL_INTERVAL = 3   # seconds between polls


def send_message(text: str) -> bool:
    """Send a message back to the user's Telegram."""
    if not TOKEN or not CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False


def get_updates(offset: int = None) -> list:
    """Fetch updates from Telegram with long-polling."""
    params = {"timeout": POLL_INTERVAL, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params=params,
            timeout=POLL_INTERVAL + 5,
        )
        return resp.json().get("result", [])
    except Exception as e:
        logger.warning(f"getUpdates error: {e}")
        return []


def write_inbound(text: str, sender: str = "Telegram"):
    """Write incoming message to .tg_inbound for Gemini CLI to pick up."""
    entry = {
        "sender": sender,
        "text": text,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        # Read existing entries
        entries = []
        if os.path.exists(INBOUND_FILE):
            with open(INBOUND_FILE, "r", encoding="utf-8") as f:
                try:
                    entries = json.load(f)
                except Exception:
                    entries = []
        entries.append(entry)
        # Keep only last 20
        entries = entries[-20:]
        with open(INBOUND_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        logger.info(f"Inbound message saved: {text[:60]!r}")
    except Exception as e:
        logger.error(f"Write inbound error: {e}")


def run():
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found in .env — exiting.")
        return

    logger.info(f"Listening for Telegram messages (chat_id={CHAT_ID or 'not set yet'})")
    send_message("🤖 VoxKage is online and listening.")

    last_update_id = None

    while True:
        try:
            updates = get_updates(offset=last_update_id)
            for update in updates:
                last_update_id = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                from_id = str(msg.get("from", {}).get("id", ""))

                if not text:
                    continue

                # Only accept messages from the configured chat ID
                if CHAT_ID and from_id != CHAT_ID:
                    logger.warning(f"Ignored message from unknown chat_id={from_id}")
                    continue

                logger.info(f"Received: {text!r}")
                write_inbound(text, sender="User (Telegram)")

                # Send acknowledgement back
                send_message(f"📨 Received: _{text}_\n\nProcessing…")

        except KeyboardInterrupt:
            logger.info("Listener stopped.")
            break
        except Exception as e:
            logger.error(f"Listener error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()
