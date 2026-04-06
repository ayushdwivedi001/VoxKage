from config_loader import load_config
CONFIG = load_config()
WAKE_WORD = CONFIG.get("wake_word", "vision").lower()
import sys
import os
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
                    if command:
                        from voice.voice_manager import log_to_hud
                        
                        if command == "exit" or "quit assistant" in command:
                            speak("Goodbye!")
                            with open(".ui_command", "w", encoding="utf-8") as f:
                                f.write("")
                            break
                        
                        print(f"User - {command}")
                        log_to_hud("User", command)
                        result = process_command(command)
                        if result == "exit":
                            speak("Goodbye!")
                            break
                    with open(".ui_command", "w", encoding="utf-8") as f:
                        f.write("")
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
                        break

if __name__ == "__main__":
    if "--settings" in sys.argv:
        from settings_gui import launch_settings_gui
        launch_settings_gui()
    else:
        from tray.tray_app import setup_tray
        setup_tray()