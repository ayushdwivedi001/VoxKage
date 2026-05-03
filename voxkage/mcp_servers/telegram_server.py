"""
MCP Server: Telegram Bridge
Handles BOTH outbound (sending messages from VoxKage → Telegram)
and inbound awareness (checking messages from the user's phone).

Key design decisions:
  - telegram_ask_save is NON-BLOCKING: sends question and returns QUESTION_SENT immediately.
    Use telegram_check_reply() on subsequent turns to get the user's yes/no answer.
  - telegram_get_pending_messages tracks a persistent offset so messages are never repeated.
  - telegram_send_file auto-routes .jpg/.jpeg/.png/.webp to sendPhoto (inline) vs sendDocument.

Standalone — run directly:
    python mcp_servers/telegram_server.py
"""

import os
import sys
import json
import logging
from pathlib import Path

# ── Persistent state dir (cross-platform, used for offset + pending reply) ─────
_VOXKAGE_DIR = Path(os.path.expanduser("~")) / ".voxkage"
_VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
_OFFSET_FILE = _VOXKAGE_DIR / "telegram_offset.json"
_PENDING_REPLY_FILE = _VOXKAGE_DIR / "telegram_pending_reply.json"

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from _env import load_voxkage_env
load_voxkage_env()

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


# Image extensions that should be sent as photos (renders inline in Telegram chat)
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _send_document(file_path: str, caption: str = "") -> bool:
    """Low-level file send via Telegram Bot API.
    Automatically routes images to sendPhoto for inline display.
    All other file types use sendDocument.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    ext = os.path.splitext(file_path)[1].lower()
    endpoint = "sendPhoto" if ext in _PHOTO_EXTS else "sendDocument"
    field_name = "photo" if endpoint == "sendPhoto" else "document"
    try:
        import requests
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{endpoint}",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={field_name: f},
                timeout=30,
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[Telegram] File send error: {e}")
        return False


def _read_offset() -> int:
    """Read the last processed update_id from disk. Returns 0 if none stored."""
    try:
        if _OFFSET_FILE.exists():
            data = json.loads(_OFFSET_FILE.read_text(encoding="utf-8"))
            return int(data.get("last_update_id", 0))
    except Exception:
        pass
    return 0


def _write_offset(update_id: int):
    """Persist the last processed update_id to disk."""
    try:
        _OFFSET_FILE.write_text(
            json.dumps({"last_update_id": update_id}), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[Telegram] Could not write offset: {e}")


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

    *** NON-BLOCKING — returns immediately after sending the question. ***
    Do NOT wait for a reply in this call. The MCP server would freeze for 30s
    if this polled synchronously — all other tools would be unreachable.

    Workflow:
      1. Call telegram_ask_save(content_description) → sends question, returns QUESTION_SENT
      2. On your NEXT turn, call telegram_check_reply() → returns YES_SAVE, NO_SKIP, or WAITING
      3. If WAITING, call telegram_check_reply() again on subsequent turns.

    Use ONLY when you have generated a substantial result (>300 chars).
    Returns: 'QUESTION_SENT', or 'NO_SKIP' if Telegram is not configured.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return "NO_SKIP"
    question = (
        f"I just generated: *{content_description}*.\n\n"
        f"Would you like me to send this to Telegram? Reply *YES* or *NO*."
    )
    # Record current offset so check_reply only looks at messages AFTER this question
    current_offset = _read_offset()
    try:
        _PENDING_REPLY_FILE.write_text(
            json.dumps({"waiting": True, "baseline_offset": current_offset}),
            encoding="utf-8",
        )
    except Exception:
        pass
    _send(question)
    return (
        "QUESTION_SENT: Yes/No question sent to Telegram. "
        "Call telegram_check_reply() on your next turn to get the user's answer."
    )


@mcp.tool()
def telegram_check_reply() -> str:
    """
    Check if the user has replied to a previous telegram_ask_save() question.
    Call this once per turn AFTER calling telegram_ask_save().

    Returns:
      'YES_SAVE'  — user replied yes, proceed to send content
      'NO_SKIP'   — user replied no, skip sending
      'WAITING'   — no reply yet, check again next turn
      'NO_PENDING' — no question was sent (telegram_ask_save not called)
    """
    if not TELEGRAM_TOKEN:
        return "NO_PENDING"
    try:
        if not _PENDING_REPLY_FILE.exists():
            return "NO_PENDING"
        state = json.loads(_PENDING_REPLY_FILE.read_text(encoding="utf-8"))
        if not state.get("waiting"):
            return "NO_PENDING"
        baseline = state.get("baseline_offset", 0)
    except Exception:
        return "NO_PENDING"

    try:
        import requests
        # Only fetch messages AFTER our question was sent
        params = {"limit": 10, "timeout": 0, "offset": baseline + 1}
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params=params,
            timeout=8,
        )
        updates = resp.json().get("result", [])
        last_id = baseline
        found_reply = False
        reply_result = "WAITING"

        for u in updates:
            last_id = max(last_id, u["update_id"])
            msg = u.get("message", {}).get("text", "").strip().upper()
            if not found_reply:
                if msg in ("YES", "Y"):
                    _PENDING_REPLY_FILE.write_text(json.dumps({"waiting": False}), encoding="utf-8")
                    found_reply = True
                    reply_result = "YES_SAVE"
                elif msg in ("NO", "N"):
                    _PENDING_REPLY_FILE.write_text(json.dumps({"waiting": False}), encoding="utf-8")
                    found_reply = True
                    reply_result = "NO_SKIP"
        
        # Always update the global offset if we saw messages, 
        # and also update the baseline for next check_reply if we haven't found our answer yet.
        if last_id > baseline:
            _write_offset(last_id)
            if not found_reply:
                try:
                    state["baseline_offset"] = last_id
                    _PENDING_REPLY_FILE.write_text(json.dumps(state), encoding="utf-8")
                except Exception:
                    pass
        
        return reply_result
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def telegram_get_pending_messages() -> str:
    """
    Fetches pending/unread messages from the connected Telegram user.
    Use when the user asks: 'do I have any telegram messages?', 
    'read my telegram messages', 'check telegram'.
    """
    if not TELEGRAM_TOKEN:
        return "❌ Telegram not configured. No bot token found."
    
    current_offset = _read_offset()
    try:
        import requests
        params = {"limit": 10, "timeout": 0, "offset": current_offset + 1}
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params=params,
            timeout=8,
        )
        updates = resp.json().get("result", [])
        if not updates:
            return "No new messages."
        
        output = []
        last_id = current_offset
        for u in updates:
            last_id = max(last_id, u["update_id"])
            # Extract sender and text
            msg_obj = u.get("message", {})
            sender = msg_obj.get("from", {}).get("first_name", "Unknown")
            text = msg_obj.get("text", "")
            if text:
                output.append(f"From {sender}: {text}")
        
        if last_id > current_offset:
            _write_offset(last_id)
            
        if output:
            return "📩 *New Telegram Messages:*\n\n" + "\n".join(output)
        return "No new text messages (updates received but no text found)."
    except Exception as e:
        logger.error(f"[Telegram] Get updates error: {e}")
        return f"❌ Failed to fetch messages: {e}"


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





if __name__ == "__main__":
    mcp.run()
