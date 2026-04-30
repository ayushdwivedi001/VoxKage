"""
MCP Server: Telegram Bridge
Handles BOTH outbound (sending messages from VoxKage → Telegram)
and inbound awareness (checking messages from the user's phone).

Standalone — run directly:
    python mcp_servers/telegram_server.py
"""

import os
import sys
import logging

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-telegram")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def _send(text: str) -> bool:
    """Low-level send via Telegram Bot API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[Telegram] Send error: {e}")
        return False


def _send_document(file_path: str, caption: str = "") -> bool:
    """Low-level file send via Telegram Bot API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        import requests
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": f},
                timeout=30,
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[Telegram] File send error: {e}")
        return False


@mcp.tool()
def telegram_send_message(message: str) -> str:
    """
    Sends a plain text message to the user's Telegram phone.
    Use for: quick notifications, reminders, short facts, confirmations, answers.
    """
    if not TELEGRAM_TOKEN:
        return "❌ Telegram not configured. No bot token found."
    if not TELEGRAM_CHAT_ID:
        return "⚠️ Telegram: no chat ID yet. Ask user to send any message to the bot first."
    ok = _send(message)
    return "✅ Message sent to Telegram." if ok else "❌ Failed to send Telegram message."


@mcp.tool()
def telegram_send_report(title: str, content: str) -> str:
    """
    Sends a formatted report to Telegram with a header.
    Use for: research results, file summaries, long analyses, daily briefings.
    Telegram has a 4096 character limit — content is truncated if needed.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "❌ Telegram not configured."
    formatted = f"📋 *{title}*\n\n{content}"
    if len(formatted) > 4000:
        formatted = formatted[:3990] + "\n\n_…[truncated]_"
    ok = _send(formatted)
    return "✅ Report sent to Telegram." if ok else "❌ Failed to send report."


@mcp.tool()
def telegram_send_file(file_path: str, caption: str = "") -> str:
    """
    Sends a local file (PDF, image, text, CSV, etc.) to the user's Telegram.
    file_path must be an absolute path to a file that exists on this PC.
    Use when user says: 'send this file to telegram', 'forward this PDF to my phone'.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "❌ Telegram not configured."
    if not os.path.isfile(file_path):
        return f"❌ File not found: {file_path}"
    caption = caption or f"📎 {os.path.basename(file_path)}"
    ok = _send_document(file_path, caption)
    return f"✅ '{os.path.basename(file_path)}' sent to Telegram." if ok else "❌ File send failed."


@mcp.tool()
def telegram_ask_save(content_description: str) -> str:
    """
    Asks the user via Telegram if they want to save the current information.
    Sends a Yes/No question and waits up to 30 seconds for a reply.
    Returns: 'YES_SAVE', 'NO_SKIP', or 'TIMEOUT'
    Use ONLY when you have generated a substantial result (>300 chars).
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "NO_SKIP"
    question = (
        f"I just generated: *{content_description}*.\n\n"
        f"Would you like me to send this to Telegram? Reply YES or NO."
    )
    _send(question)
    # Poll for 30 seconds for a reply
    import requests, time
    deadline = time.time() + 30
    last_update_id = None
    while time.time() < deadline:
        try:
            params = {"timeout": 5}
            if last_update_id:
                params["offset"] = last_update_id + 1
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params=params,
                timeout=10,
            )
            updates = resp.json().get("result", [])
            for u in updates:
                last_update_id = u["update_id"]
                msg = u.get("message", {}).get("text", "").strip().upper()
                if msg in ("YES", "Y"):
                    return "YES_SAVE"
                if msg in ("NO", "N"):
                    return "NO_SKIP"
        except Exception:
            pass
        time.sleep(2)
    return "TIMEOUT"


@mcp.tool()
def telegram_get_status() -> str:
    """
    Returns the current status of the Telegram integration.
    Use when user asks: 'Is Telegram connected?', 'telegram status'.
    """
    if not TELEGRAM_TOKEN:
        return "❌ Telegram not configured. No bot token in .env."
    if not TELEGRAM_CHAT_ID:
        return "⚠️ Token found but no chat ID. Send any message to the bot to link your account."
    return f"✅ Telegram active. Linked to chat ID: {TELEGRAM_CHAT_ID}."


@mcp.tool()
def telegram_get_pending_messages() -> str:
    """
    Checks if the user has sent any new messages via Telegram.
    Returns the latest unread message text, or 'NO_MESSAGES' if none.
    Use this proactively if you want to check for new Telegram input.
    """
    if not TELEGRAM_TOKEN:
        return "NO_MESSAGES"
    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"limit": 5, "timeout": 0},
            timeout=10,
        )
        updates = resp.json().get("result", [])
        if not updates:
            return "NO_MESSAGES"
        # Return latest message text
        for u in reversed(updates):
            msg = u.get("message", {}).get("text", "").strip()
            if msg:
                return f"PENDING: {msg}"
        return "NO_MESSAGES"
    except Exception as e:
        return f"ERROR: {e}"


if __name__ == "__main__":
    mcp.run()
