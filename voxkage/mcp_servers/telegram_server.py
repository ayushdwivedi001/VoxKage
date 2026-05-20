"""
MCP Server: Telegram Bridge (voxkage-telegram)
Handles outbound (VoxKage → Telegram) and inbox awareness.

The inbound path is now handled by telegram_watcher.py which runs as a
persistent background process and injects messages directly into the VoxKage terminal.
This MCP server handles:
  - Sending messages/files/reports outbound
  - Reading the inbox file (fallback when watcher injection fails)
  - ask/check reply flow for confirmation prompts

Standalone — run directly:
    python mcp_servers/telegram_server.py
"""

import os
import sys
import json
import logging
import time
from pathlib import Path

# ── Persistent state ──────────────────────────────────────────────────────────
_VOXKAGE_DIR         = Path(os.path.expanduser("~")) / ".voxkage"
_OFFSET_FILE         = _VOXKAGE_DIR / "telegram_offset.json"
_PENDING_REPLY_FILE  = _VOXKAGE_DIR / "telegram_pending_reply.json"
_INBOX_FILE          = _VOXKAGE_DIR / "telegram_inbox.jsonl"
_DOWNLOAD_DIR        = _VOXKAGE_DIR / "telegram_downloads"
_VOXKAGE_DIR.mkdir(parents=True, exist_ok=True)
_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-telegram")
logger = logging.getLogger(__name__)

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API_BASE = f"https://api.telegram.org/bot{TOKEN}"

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


# ── Low-level API ─────────────────────────────────────────────────────────────

def _post(method: str, **kwargs) -> bool:
    if not TOKEN or not CHAT_ID:
        return False
    try:
        import requests
        r = requests.post(f"{API_BASE}/{method}", json={"chat_id": CHAT_ID, **kwargs}, timeout=15)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"[Telegram] {method} error: {e}")
        return False


def _get(method: str, **kwargs) -> dict:
    try:
        import requests
        r = requests.get(f"{API_BASE}/{method}", params=kwargs, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"[Telegram] GET {method} error: {e}")
    return {}


def _send(text: str) -> bool:
    return _post("sendMessage", text=text, parse_mode="Markdown")


def _send_document(file_path: str, caption: str = "") -> bool:
    if not TOKEN or not CHAT_ID:
        return False
    ext      = os.path.splitext(file_path)[1].lower()
    endpoint = "sendPhoto" if ext in _PHOTO_EXTS else "sendDocument"
    field    = "photo" if endpoint == "sendPhoto" else "document"
    try:
        import requests
        with open(file_path, "rb") as f:
            r = requests.post(
                f"{API_BASE}/{endpoint}",
                data={"chat_id": CHAT_ID, "caption": caption},
                files={field: f},
                timeout=30,
            )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"[Telegram] File send error: {e}")
        return False


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


# ── Inbox file reader (watcher fallback) ──────────────────────────────────────

def _read_inbox(mark_read: bool = True) -> list[dict]:
    """
    Read messages from the inbox file that telegram_watcher.py wrote.
    Used as fallback when live injection failed (VoxKage terminal window not found).
    """
    if not _INBOX_FILE.exists():
        return []
    try:
        lines = _INBOX_FILE.read_text(encoding="utf-8").strip().splitlines()
        messages = []
        unread = []
        for line in lines:
            try:
                obj = json.loads(line)
                if not obj.get("read"):
                    unread.append(obj)
                messages.append(obj)
            except Exception:
                pass
        if mark_read and unread:
            # Mark all as read
            marked = []
            for obj in messages:
                obj["read"] = True
                marked.append(json.dumps(obj, ensure_ascii=False))
            _INBOX_FILE.write_text("\n".join(marked) + "\n", encoding="utf-8")
        return unread
    except Exception as e:
        logger.error(f"Inbox read error: {e}")
        return []


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def telegram_send_message(message: str) -> str:
    """
    Send a plain text message to the user's Telegram phone.
    Use for: notifications, reminders, short facts, confirmations, answers.
    """
    if not TOKEN:
        return "❌ Telegram not configured — no bot token."
    if not CHAT_ID:
        return "⚠️ Telegram: no chat ID. Ask user to send any message to the bot first."
    return "✅ Message sent." if _send(message) else "❌ Failed to send Telegram message."


@mcp.tool()
def telegram_send_report(title: str, content: str) -> str:
    """
    Send a formatted report to Telegram with a bold header.
    Use for: research results, summaries, analyses, daily briefings.
    Content is truncated at 4000 characters (Telegram limit).
    """
    if not TOKEN or not CHAT_ID:
        return "❌ Telegram not configured."
    body = f"📋 *{title}*\n\n{content}"
    if len(body) > 4000:
        body = body[:3990] + "\n\n_…[truncated]_"
    return "✅ Report sent." if _send(body) else "❌ Failed to send report."


@mcp.tool()
def telegram_send_file(file_path: str, caption: str = "") -> str:
    """
    Send a local file (PDF, image, text, CSV, etc.) to the user's Telegram.
    file_path must be an absolute path to an existing file on this PC.
    Images (.jpg/.png/.webp) are sent inline; all others as document attachments.
    """
    if not TOKEN or not CHAT_ID:
        return "❌ Telegram not configured."
    if not os.path.isfile(file_path):
        return f"❌ File not found: {file_path}"
    cap = caption or f"📎 {os.path.basename(file_path)}"
    name = os.path.basename(file_path)
    return f"✅ '{name}' sent." if _send_document(file_path, cap) else "❌ File send failed."


@mcp.tool()
def telegram_check_inbox() -> str:
    """
    Check for Telegram messages that arrived while the watcher could not inject
    them live into the VoxKage terminal (e.g. the terminal window was not found).

    Returns unread messages from the inbox file and marks them as read.
    Use when user says 'check telegram inbox' or after being notified that
    a message could not be injected.

    NOTE: Under normal operation, the telegram_watcher.py process injects
    messages directly into the VoxKage terminal — you will see them appear automatically
    without needing to call this tool. This tool is the manual fallback.
    """
    messages = _read_inbox(mark_read=True)
    if not messages:
        return "📭 No unread messages in inbox."

    parts = [f"📬 {len(messages)} unread message(s) from Telegram:\n"]
    for i, m in enumerate(messages, 1):
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("timestamp", 0)))
        parts.append(f"[{i}] {ts}\n{m.get('prompt', '(empty)')}")
    return "\n\n".join(parts)


@mcp.tool()
def telegram_ask_save(content_description: str) -> str:
    """
    Ask the user via Telegram if they want to save/receive the current result.

    NON-BLOCKING — sends the question and returns immediately.
    Follow up with telegram_check_reply() on the next turn.

    Workflow:
      1. telegram_ask_save("weather report for Mumbai")  → QUESTION_SENT
      2. Next turn: telegram_check_reply()               → YES_SAVE / NO_SKIP / WAITING
      3. If YES_SAVE: send the content with telegram_send_report() or telegram_send_file()
    """
    if not TOKEN or not CHAT_ID:
        return "NO_SKIP"
    baseline = _read_offset()
    try:
        _PENDING_REPLY_FILE.write_text(
            json.dumps({"waiting": True, "baseline_offset": baseline})
        )
    except Exception:
        pass
    question = (
        f"I just generated: *{content_description}*.\n\n"
        f"Would you like me to send this to Telegram? Reply *YES* or *NO*."
    )
    _send(question)
    return (
        "QUESTION_SENT: Question sent to Telegram. "
        "Call telegram_check_reply() next turn to get the answer."
    )


@mcp.tool()
def telegram_check_reply() -> str:
    """
    Check if the user has replied YES or NO to a telegram_ask_save() question.

    Returns:
      YES_SAVE   — user replied yes, proceed to send content
      NO_SKIP    — user replied no, skip sending
      WAITING    — no reply yet, call again next turn
      NO_PENDING — no question is pending
    """
    if not TOKEN:
        return "NO_PENDING"
    try:
        if not _PENDING_REPLY_FILE.exists():
            return "NO_PENDING"
        state = json.loads(_PENDING_REPLY_FILE.read_text())
        if not state.get("waiting"):
            return "NO_PENDING"
        baseline = state.get("baseline_offset", 0)
    except Exception:
        return "NO_PENDING"

    try:
        import requests
        resp = requests.get(
            f"{API_BASE}/getUpdates",
            params={"limit": 10, "timeout": 0, "offset": baseline + 1},
            timeout=8,
        )
        updates = resp.json().get("result", [])
        last_id = baseline

        for u in updates:
            last_id = max(last_id, u["update_id"])
            text = u.get("message", {}).get("text", "").strip().upper()
            if text in ("YES", "Y"):
                _PENDING_REPLY_FILE.write_text(json.dumps({"waiting": False}))
                _save_offset(last_id)
                return "YES_SAVE"
            elif text in ("NO", "N"):
                _PENDING_REPLY_FILE.write_text(json.dumps({"waiting": False}))
                _save_offset(last_id)
                return "NO_SKIP"

        # Advance baseline so next check doesn't re-read same messages
        if last_id > baseline:
            state["baseline_offset"] = last_id
            _PENDING_REPLY_FILE.write_text(json.dumps(state))
            _save_offset(last_id)

        return "WAITING"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def telegram_get_pending_messages() -> str:
    """
    Manually fetch any new Telegram messages via the Bot API.

    Under normal operation, telegram_watcher.py injects messages automatically.
    Use this only if the watcher is not running or as an explicit check.

    Also checks the inbox file for messages the watcher could not inject live.
    """
    if not TOKEN:
        return "❌ Telegram not configured."

    output = []

    # 1. Check inbox file (watcher fallback messages)
    inbox = _read_inbox(mark_read=True)
    if inbox:
        output.append(f"📬 {len(inbox)} message(s) from inbox file (watcher fallback):")
        for m in inbox:
            output.append(m.get("prompt", "(empty)"))

    # 2. Live poll from Telegram API
    offset = _read_offset()
    try:
        import requests
        resp = requests.get(
            f"{API_BASE}/getUpdates",
            params={"limit": 10, "timeout": 0, "offset": offset + 1},
            timeout=8,
        )
        updates = resp.json().get("result", [])
        last_id = offset
        for u in updates:
            last_id = max(last_id, u["update_id"])
            msg = u.get("message", {})
            sender = msg.get("from", {}).get("first_name", "Unknown")
            text = msg.get("text") or msg.get("caption") or ""
            if text:
                output.append(f"From {sender}: {text}")
        if updates:
            _save_offset(last_id)
    except Exception as e:
        output.append(f"API poll error: {e}")

    if not output:
        return "📭 No new messages."
    return "📩 *Telegram Messages:*\n\n" + "\n\n".join(output)


@mcp.tool()
def telegram_get_status() -> str:
    """
    Return the current Telegram integration status.
    Shows whether the watcher process is running and the connection is alive.
    """
    if not TOKEN:
        return "❌ Not configured — no TELEGRAM_BOT_TOKEN in .env"
    if not CHAT_ID:
        return "⚠️ Token found but no TELEGRAM_CHAT_ID. Send any message to the bot to link."

    # Check if unified tray (watcher) is running
    watcher_status = "unknown"
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1)
        s.connect(("127.0.0.1", 49998))
        s.close()
        watcher_status = "✅ running (System Tray / V3 Watcher)"
    except (ConnectionRefusedError, OSError, socket.timeout):
        watcher_status = "❌ not running — launch voxkage tray"

    inbox_count = len(_read_inbox(mark_read=False))
    return (
        f"✅ Telegram connected\n"
        f"  Chat ID  : {CHAT_ID}\n"
        f"  Watcher  : {watcher_status}\n"
        f"  Inbox    : {inbox_count} unread message(s) in fallback file"
    )


if __name__ == "__main__":
    mcp.run()