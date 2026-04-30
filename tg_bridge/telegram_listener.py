import os
import sys
import time
import asyncio
import logging
import threading
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_UI_COMMAND_FILE = str(_ROOT / ".ui_command")
_ENV_FILE = str(_ROOT / ".env")

# ─── Shutdown event shared with main.py ───────────────────────────────────────
_stop_event = threading.Event()
_bot_app = None          # telegram.ext.Application instance
_authorized_chat_id = None  # Loaded from .env at startup

# ─── Telegram Session Context ─────────────────────────────────────────────────
# This is the key to bidirectional routing. When a message arrives from Telegram,
# we set _active_session so main.py knows to send the response BACK to Telegram.
_session_lock = threading.Lock()
_active_session: dict | None = None   # {"chat_id": int, "source": "telegram"}

# Prefix injected into .ui_command so main.py can identify Telegram messages
_TG_PREFIX = "[TELEGRAM_MSG]"


def set_telegram_session(chat_id: int):
    """Mark that the current input came from Telegram. main.py reads this."""
    global _active_session
    with _session_lock:
        _active_session = {"chat_id": chat_id, "source": "telegram"}


def clear_telegram_session():
    """Clear the session after main.py has handled the response."""
    global _active_session
    with _session_lock:
        _active_session = None


def get_active_telegram_session() -> dict | None:
    """Returns the active session dict if the current input came from Telegram."""
    with _session_lock:
        return _active_session.copy() if _active_session else None


def _load_env():
    """Load .env into os.environ (non-destructive, doesn't overwrite existing)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_ENV_FILE, override=False)
    except Exception:
        pass


def _get_token() -> str | None:
    _load_env()
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None


def _get_chat_id() -> int | None:
    _load_env()
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return int(raw) if raw else None


def _save_chat_id_to_env(chat_id: int):
    """Append TELEGRAM_CHAT_ID to the .env file (called once on first contact)."""
    global _authorized_chat_id
    _authorized_chat_id = chat_id
    
    env_path = Path(_ENV_FILE)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("TELEGRAM_CHAT_ID"):
            lines[i] = f"TELEGRAM_CHAT_ID={chat_id}"
            updated = True
            break
    if not updated:
        lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["TELEGRAM_CHAT_ID"] = str(chat_id)
    logger.info(f"[Telegram] Chat ID {chat_id} auto-saved to .env")


def _write_ui_command(text: str):
    """Write a command to the .ui_command file for main.py to pick up."""
    # Instantly abort any ongoing TTS/LLM task so VoxKage immediately responds to Telegram
    try:
        from voice.voice_manager import manager
        manager.stop_audio()
    except Exception:
        pass
        
    try:
        with open(_UI_COMMAND_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        logger.error(f"[Telegram] Failed to write ui_command: {e}")


async def _reply_with_retry(update, text: str):
    """
    Robustly sends a reply text via the update object with retry logic.
    Catches network errors so the main handler doesn't abort.
    """
    for attempt in range(1, 4):
        try:
            await update.message.reply_text(text)
            return
        except Exception as e:
            if attempt == 3:
                logger.warning(f"[Telegram] Failed to send acknowledgment after 3 attempts: {e}")
            else:
                import asyncio
                await asyncio.sleep(1)

async def _handle_message(update, context):
    """Handle incoming text messages from Telegram."""
    global _authorized_chat_id

    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if not text:
        return

    # ── Auto-register chat ID on first contact ────────────────────────────────
    if _authorized_chat_id is None:
        _save_chat_id_to_env(chat_id)
        await _reply_with_retry(update, 
            "✅ VoxKage: Chat linked! I'll relay your messages to the assistant. "
            "You can now send me commands or questions."
        )
        logger.info(f"[Telegram] First contact from chat_id={chat_id}. Auto-registered.")
        return

    # ── Security: ignore unauthorized senders ─────────────────────────────────
    if chat_id != _authorized_chat_id:
        logger.warning(f"[Telegram] Unauthorized message from chat_id={chat_id}. Ignored.")
        return

    logger.info(f"[Telegram] Received message: {text[:80]!r}")

    # Acknowledge receipt immediately (with retries, so we don't abort on network errors)
    await _reply_with_retry(update, "⏳ Processing your request…")

    # Mark this input as coming from Telegram — main.py will route response back
    set_telegram_session(chat_id)

    # Write with prefix so main.py can show [📱 Telegram] in the HUD
    _write_ui_command(f"{_TG_PREFIX}{text}")



async def _handle_document(update, context):
    """Handle file/document uploads from Telegram."""
    global _authorized_chat_id

    chat_id = update.effective_chat.id
    if _authorized_chat_id is None or chat_id != _authorized_chat_id:
        return

    doc = update.message.document
    photo = update.message.photo

    try:
        await _reply_with_retry(update, "📂 File received. VoxKage is analyzing it…")
        
        if doc:
            file = await context.bot.get_file(doc.file_id)
            suffix = Path(doc.file_name or "upload.bin").suffix or ".bin"
            filename = doc.file_name or f"telegram_upload{suffix}"
        elif photo:
            # Get highest resolution
            file = await context.bot.get_file(photo[-1].file_id)
            suffix = ".jpg"
            filename = "telegram_photo.jpg"
        else:
            await update.message.reply_text("❌ Unsupported file type.")
            return

        # Download to temp dir
        tmp_dir = Path(tempfile.gettempdir()) / "voxkage_telegram"
        tmp_dir.mkdir(exist_ok=True)
        dest = str(tmp_dir / filename)
        await file.download_to_drive(dest)
        
        logger.info(f"[Telegram] File downloaded to: {dest}")
        
        # Mark session as Telegram so main.py routes the response back
        set_telegram_session(chat_id)

        # Inject into VoxKage's input pipeline as a file injection command
        caption = (update.message.caption or "").strip()
        if caption:
            instruction = f"[TELEGRAM_MSG][UI_FILE_INJECTION] {caption} file at: {dest}"
        else:
            instruction = f"[TELEGRAM_MSG][UI_FILE_INJECTION] summarize or analyze the file at: {dest}"
        
        _write_ui_command(instruction)

    except Exception as e:
        logger.error(f"[Telegram] File handling error: {e}")
        await update.message.reply_text(f"❌ Error processing file: {str(e)[:100]}")


async def _handle_error(update, context):
    """Log Telegram bot errors non-fatally."""
    logger.warning(f"[Telegram] Bot error: {context.error}")


async def _send_startup_message(app):
    """Send a startup greeting to the authorized chat."""
    chat_id = _get_chat_id()
    if not chat_id:
        return
    try:
        from datetime import datetime
        now = datetime.now().strftime("%I:%M %p")
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"🟢 *VoxKage is online* — {now}\nI'm listening. Send me a command or question!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"[Telegram] Could not send startup message: {e}")


async def _send_shutdown_message(app):
    """Send a shutdown notice to the authorized chat."""
    chat_id = _authorized_chat_id or _get_chat_id()
    if not chat_id:
        return
    try:
        await app.bot.send_message(
            chat_id=chat_id,
            text="🔴 *VoxKage is going offline.*\nI won't be able to respond until you restart me.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"[Telegram] Could not send shutdown message: {e}")


def _run_bot_thread():
    """Entry point for the daemon thread — runs the asyncio event loop."""
    global _authorized_chat_id, _bot_app
    
    token = _get_token()
    if not token:
        logger.info("[Telegram] No TELEGRAM_BOT_TOKEN in .env — Telegram bridge disabled.")
        return
    
    _authorized_chat_id = _get_chat_id()
    
    logger.info("[Telegram] Starting bot polling loop…")
    
    from telegram.ext import ApplicationBuilder, MessageHandler, filters
    
    _first_startup = True
    while not _stop_event.is_set():
        try:
            # Build the application
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            app = ApplicationBuilder().token(token).build()
            _bot_app = app

            # Register handlers
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
            app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, _handle_document))
            app.add_error_handler(_handle_error)
            
            async def _main():
                nonlocal _first_startup
                await app.initialize()
                await app.start()
                
                # Send startup message only on the very first boot, not on silent reconnects
                if _first_startup:
                    await _send_startup_message(app)
                
                # Start polling
                await app.updater.start_polling(
                    drop_pending_updates=_first_startup,   # Only drop stale messages on very first boot
                    timeout=10,
                    read_timeout=15,
                    write_timeout=15,
                )
                
                _first_startup = False
                logger.info("[Telegram] Bot polling started. Waiting for stop signal…")
                
                # Block until VoxKage signals shutdown
                while not _stop_event.is_set():
                    await asyncio.sleep(0.5)
                
                # Graceful shutdown
                logger.info("[Telegram] Stop event received. Sending shutdown message…")
                await _send_shutdown_message(app)
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
                logger.info("[Telegram] Bot stopped cleanly.")

            loop.run_until_complete(_main())
            
        except Exception as e:
            if _stop_event.is_set():
                break
            logger.error(f"[Telegram] Bot thread initialization error: {e}. Retrying in 5 seconds...")
            import time
            time.sleep(5)
            # Loop will continue and try to build a new app/loop



def start_telegram_daemon() -> threading.Thread:
    """
    Starts the Telegram bot listener as a non-blocking daemon thread.
    Returns the thread object (caller does not need to manage it).
    """
    _stop_event.clear()
    t = threading.Thread(target=_run_bot_thread, name="TelegramDaemon", daemon=True)
    t.start()
    logger.info("[Telegram] Daemon thread launched.")
    return t


def stop_telegram_daemon():
    """
    Signal the Telegram daemon thread to shut down gracefully.
    Called from main.py before VoxKage exits.
    """
    logger.info("[Telegram] Signalling daemon to stop…")
    _stop_event.set()


def send_to_telegram(text: str) -> bool:
    """
    Send a message to the authorized Telegram chat synchronously.
    Retries up to 3 times on ConnectionResetError (Windows socket error 10054)
    which happens when Telegram closes an idle keep-alive connection.
    """
    token = _get_token()
    chat_id = _authorized_chat_id or _get_chat_id()
    
    if not token or not chat_id:
        logger.warning("[Telegram] Cannot send — token or chat_id missing.")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    # Force a fresh TCP connection — prevents Windows 10054 ConnectionResetError
    # that happens when the server drops an idle keep-alive connection.
    headers = {"Connection": "close"}

    import requests
    for attempt in range(1, 4):  # up to 3 attempts
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                logger.info(f"[Telegram] Message sent ({len(text)} chars).")
                return True
            else:
                logger.warning(f"[Telegram] Send failed: {resp.status_code} {resp.text[:100]}")
                return False
        except (ConnectionResetError, ConnectionError) as e:
            logger.warning(f"[Telegram] Connection reset on attempt {attempt}/3: {e}. Retrying…")
            if attempt == 3:
                logger.error("[Telegram] All retries exhausted. Message not sent.")
                return False
        except Exception as e:
            logger.error(f"[Telegram] send_to_telegram error: {e}")
            return False
    return False


def ask_telegram_yes_no(question: str, timeout_seconds: int = 30) -> bool | None:
    """
    Sends a Yes/No question to Telegram and waits up to `timeout_seconds` for a reply.
    Returns True (yes), False (no), or None (timeout / no reply).
    Used for the "Save to Telegram?" smart prompt.
    """
    token = _get_token()
    chat_id = _authorized_chat_id or _get_chat_id()
    
    if not token or not chat_id:
        return None
    
    # Force fresh TCP connections to prevent Windows 10054 ConnectionResetError
    _headers = {"Connection": "close"}
    
    try:
        import requests
        
        # Send with inline keyboard
        url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url_msg, json={
            "chat_id": chat_id,
            "text": f"❓ {question}",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Yes, send it", "callback_data": "tg_yes"},
                    {"text": "❌ No thanks", "callback_data": "tg_no"}
                ]]
            }
        }, headers=_headers, timeout=15)
        
        if resp.status_code != 200:
            return None
        
        # Poll for callback query response
        url_updates = f"https://api.telegram.org/bot{token}/getUpdates"
        deadline = time.time() + timeout_seconds
        offset = None
        
        while time.time() < deadline:
            params = {"timeout": 5, "allowed_updates": ["callback_query"]}
            if offset:
                params["offset"] = offset
            
            try:
                r = requests.get(url_updates, params=params, headers=_headers, timeout=10)
                updates = r.json().get("result", [])
                
                for upd in updates:
                    offset = upd["update_id"] + 1
                    cb = upd.get("callback_query", {})
                    if cb.get("data") in ("tg_yes", "tg_no"):
                        # Answer the callback so the button stops spinning
                        requests.post(
                            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                            json={"callback_query_id": cb["id"]},
                            headers=_headers,
                            timeout=5
                        )
                        return cb["data"] == "tg_yes"
            except Exception:
                pass
            
            time.sleep(1)
        
        return None  # Timeout
        
    except Exception as e:
        logger.error(f"[Telegram] ask_yes_no error: {e}")
        return None

