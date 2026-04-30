import os
# ── Offline model flags (Whisper + sentence-transformers already cached) ────────
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from config_loader import load_config
CONFIG = load_config()
WAKE_WORD = CONFIG.get("wake_word", "vision").lower()

import sys
from voice.faster_listen import listen
from voice.commands import process_command
from voice.voice_manager import speak, manager

def _generate_startup_greeting() -> str:
    """Generate a time-aware, Jarvis-style random startup greeting."""
    import random
    from datetime import datetime
    now = datetime.now()
    hour = now.hour
    day_name = now.strftime("%A")
    date_str = now.strftime("%B %d")

    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 21:
        period = "evening"
    else:
        period = "night"

    greetings = [
        f"Good {period}, sir. VoxKage is online and fully operational. How may I assist you today?",
        f"Good {period}, sir. All systems are running smoothly. What shall we tackle today?",
        f"Welcome back, sir. It's {day_name}, {date_str}. I'm ready whenever you are.",
        f"Good {period}. I've been waiting, sir. What's on the agenda?",
        f"Online and at your service, sir. Good {period}. What can I do for you?",
        f"Good {period}, sir. VoxKage standing by. Say the word and I'll get right on it.",
        f"Systems nominal, sir. Good {period} — it's {date_str}. What do you need?",
    ]

    late_night_extras = [
        "Still at it this late, sir? I admire the dedication. Ready when you are.",
        "Burning the midnight oil, sir? VoxKage is right here with you. What do you need?",
    ]

    if hour >= 22 or hour < 5:
        greetings = late_night_extras + greetings

    return random.choice(greetings)

def start_assistant():
    from llm.llm_client import clear_session_memory
    clear_session_memory()

    # ── Pre-warm the Tool RAG index (runs once at startup, ~2s) ─────────────────
    try:
        from llm.tool_rag import ensure_index_fresh
        ensure_index_fresh()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Tool RAG warm-up failed (non-fatal): {_e}")

    # ── Pre-warm the Semantic Router (builds route embeddings once) ──────────────
    try:
        from voice.semantic_router import warmup as router_warmup
        router_warmup()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Semantic Router warm-up failed (non-fatal): {_e}")

    # ── Launch Telegram Bridge (non-blocking daemon thread) ──────────────────────
    try:
        from tg_bridge.telegram_listener import start_telegram_daemon
        start_telegram_daemon()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Telegram bridge failed to start (non-fatal): {_e}")

    # ── Pre-boot Gemini REPL (boot happens in background, ~14s) ─────────────────
    try:
        from llm.constants import ENGINE
        if ENGINE == "gemini_cli":
            from llm.gemini_repl import boot_repl_sync
            boot_repl_sync()  # Non-blocking — boots in daemon thread
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Gemini REPL pre-boot failed (non-fatal): {_e}")

    speak(_generate_startup_greeting())

    while True:
        manager.wait_to_finish()
        
        cfg = load_config()
        voice_mode = cfg.get("voice_mode", "voice_chat")
        
        if voice_mode == "chat_only":
            import time
            time.sleep(0.5)
            if os.path.exists(".ui_command"):
                try:
                    with open(".ui_command", "r", encoding="utf-8") as f:
                        command = f.read().strip()
                    
                    # Clear the command file immediately so we don't re-process it on restart
                    if command:
                        with open(".ui_command", "w", encoding="utf-8") as f:
                            f.write("")
                            
                        from voice.voice_manager import log_to_hud

                        # ── Detect input source ───────────────────────────────────────────
                        _TG_PREFIX = "[TELEGRAM_MSG]"
                        is_from_telegram = command.startswith(_TG_PREFIX)
                        if is_from_telegram:
                            # Strip the prefix to get the clean command text
                            command = command[len(_TG_PREFIX):]

                        # ── Detect file injection ─────────────────────────────────────────
                        is_silent_injection = "[UI_FILE_INJECTION]" in command

                        # ── Exit check ────────────────────────────────────────────────────
                        if command == "exit" or "quit assistant" in command:
                            speak("Goodbye, sir.")
                            try:
                                from tg_bridge.telegram_listener import stop_telegram_daemon
                                stop_telegram_daemon()
                            except Exception:
                                pass
                            break

                        if is_silent_injection:
                            # Route silently to LLM — no HUD log, no tool calls
                            from llm.llm_client import ask_llm_sync
                            try:
                                if is_from_telegram:
                                    from voice.voice_manager import suppress_tts, restore_tts
                                    suppress_tts()
                                response = ask_llm_sync(command)
                                # If this file injection came from Telegram, forward the response
                                if is_from_telegram and response:
                                    try:
                                        from tg_bridge.telegram_listener import send_to_telegram, clear_telegram_session
                                        send_to_telegram(response)
                                        clear_telegram_session()
                                    except Exception:
                                        pass
                            except Exception as e:
                                print("Error in file injection LLM call:", e)
                            finally:
                                if is_from_telegram:
                                    from voice.voice_manager import restore_tts
                                    restore_tts()

                        elif is_from_telegram:
                            # ── TELEGRAM INPUT → silent LLM → send response back to Telegram ──
                            print(f"[Telegram] User - {command}")
                            log_to_hud("User", f"📱 [Telegram] {command}")

                            from voice.voice_manager import suppress_tts, restore_tts
                            suppress_tts()
                            try:
                                from llm.llm_client import ask_llm_sync
                                from tg_bridge.telegram_listener import send_to_telegram, clear_telegram_session

                                response = ask_llm_sync(command)

                                if response:
                                    send_to_telegram(response)
                                    log_to_hud("VoxKage", f"📱 [Sent to Telegram] {response[:120]}…" if len(response) > 120 else f"📱 [Sent to Telegram] {response}")

                            except Exception as e:
                                print(f"Error in Telegram-origin LLM call: {e}")
                            finally:
                                restore_tts()
                                clear_telegram_session()

                        else:
                            # ── Normal dashboard input ────────────────────────────────────
                            print(f"User - {command}")
                            log_to_hud("User", command)
                            result = process_command(command)
                            if result == "exit":
                                speak("Goodbye, sir.")
                                try:
                                    from tg_bridge.telegram_listener import stop_telegram_daemon
                                    stop_telegram_daemon()
                                except Exception:
                                    pass
                                break

                except Exception as e:
                    print("Error reading UI command:", e)
            continue
        
        command = listen()
        if command:
            from voice.voice_manager import log_to_hud
            
            is_silent_injection = False
            if "[UI_FILE_INJECTION]" in command:
                is_silent_injection = True
            
            print(f"User - {command}")
            
            if not is_silent_injection:
                log_to_hud("User", command)
            
            if "exit" == command or "quit assistant" in command:
                speak("Goodbye!")
                try:
                    from tg_bridge.telegram_listener import stop_telegram_daemon
                    stop_telegram_daemon()
                except Exception:
                    pass
                break
            else:
                if is_silent_injection:
                    from llm.llm_client import ask_llm_sync
                    try:
                        response = ask_llm_sync(command)
                        if response:
                            speak(response)
                    except Exception as e:
                        print("Error in direct LLM invocation:", e)
                else:
                    result = process_command(command)
                    if result == "exit":
                        speak("Goodbye!")
                        try:
                            from tg_bridge.telegram_listener import stop_telegram_daemon
                            stop_telegram_daemon()
                        except Exception:
                            pass
                        break

if __name__ == "__main__":
    if "--settings" in sys.argv:
        from settings_gui import launch_settings_gui
        launch_settings_gui()
    else:
        from tray.tray_app import setup_tray
        setup_tray()
