from config_loader import load_config
from .faster_listen import listen
from .intent_engine import detect_intent
import os
import re
from difflib import get_close_matches
from automation.screenshot import take_screenshot
from voice.voice_manager import speak
from automation.app_launcher    import open_app, execute_special_command
from automation.system_control  import (
    close_app, switch_to_app,
    set_volume, set_brightness,
    toggle_wifi, toggle_bluetooth, close_explorer_folder, close_folder_window_by_path, switch_to_window_by_title_or_path
)
from automation.browser_control import (
    open_website, search_youtube, search_google
)

def word_to_number(word):
    word = word.lower()
    numbers = {
        "first": 1, "second": 2, "third": 3,
        "fourth": 4, "fifth": 5, "sixth": 6,
        "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10
    }
    return numbers.get(word)

def clean_text(text):
    # Lowercase, strip spaces, and remove punctuation
    return re.sub(r'[^a-z0-9 ]', '', text.lower().strip())

def verify_voice_password():
    speak("Please say the access password")

    try:
        said = listen()
        CONFIG = load_config()  # ✅ load config dynamically here
        stored_password = CONFIG.get("voice_password", "")

        # Clean both before comparing
        if clean_text(said) == clean_text(stored_password):
            speak("Password verified")
            return True
        else:
            speak("Access denied")
            return False
    except Exception as e:
        speak("Could not verify password.")
        print("Password verification error:", e)
        return False
    
def _lookup_with_fuzzy(name, mapping):
    """Exact match first, then fuzzy by difflib."""
    if name in mapping:
        return name, mapping[name]
    candidates = get_close_matches(name, list(mapping.keys()), n=1, cutoff=0.78)
    if candidates:
        k = candidates[0]
        return k, mapping[k]
    return None, None

def process_command(command):
    CONFIG = load_config()
    PROTECTED_COMMANDS = CONFIG.get("protected_commands", [])
    cmd = command.lower()

    # Protected gating by substring
    for protected in PROTECTED_COMMANDS:
        if protected and protected in cmd:
            if verify_voice_password():
                speak(f"Access granted for {protected}")
                break
            else:
                return

    intent_data = detect_intent(cmd)
    intent = intent_data.get("intent")

    # Volume / Brightness
    if intent == "set_volume":
        lvl = intent_data.get("level")
        if lvl is not None:
            speak(f"Setting volume to {lvl}%")
            return set_volume(lvl)

    if intent == "set_brightness":
        lvl = intent_data.get("level")
        if lvl is not None:
            speak(f"Setting brightness to {lvl}%")
            return set_brightness(lvl)

    # Wi-Fi / Bluetooth
    if intent == "wifi_on":  return toggle_wifi(True)
    if intent == "wifi_off": return toggle_wifi(False)
    if intent == "bluetooth_on":  return toggle_bluetooth(True)
    if intent == "bluetooth_off": return toggle_bluetooth(False)

    # Open
    if intent == "open":
        name = (intent_data.get("target") or "").strip()
        if not name:
            speak("I didn't catch what to open.")
            return

        CONFIG = load_config()
        APP = CONFIG.get("app_launch_commands", {})
        WEB = CONFIG.get("website_commands", {})
        SYS = CONFIG.get("system_commands", {})

        # Try app/system/website with fuzzy
        key, action = _lookup_with_fuzzy(name, APP)
        if key:
            speak(f"Launching {key}")
            res = open_app(key)
            speak(res)
            return

        key, action = _lookup_with_fuzzy(name, SYS)
        if key:
            speak(f"Executing system command: {key}")
            res = execute_special_command(key)
            speak(res)
            return

        key, action = _lookup_with_fuzzy(name, WEB)
        if key:
            speak(f"Opening {key}")
            return open_website(key)

        # Removed fallback so that unknown 'open' targets fall through to LLM

    # Switch
    if intent == "switch":
        name = (intent_data.get("target") or "").strip().lower()
        if not name:
            speak("I didn't catch which app to switch to.")
            return

        CONFIG = load_config()
        ALL = {}
        for section in ["custom_commands","app_launch_commands","system_commands","website_commands"]:
            ALL.update(CONFIG.get(section, {}))

        key, action = _lookup_with_fuzzy(name, ALL)
        if key:
            action_l = action.strip().lower()

            if action_l.endswith(".exe"):
                exe_name = os.path.splitext(os.path.basename(action_l))[0]
                speak(f"Switching to {exe_name}")
                result = switch_to_window_by_title_or_path(exe_name)
                speak(result)
                return

            if action_l.startswith('start ""') and '"' in action_l:
                parts = action_l.split('"')
                if len(parts) >= 3:
                    folder_path = parts[2].strip()
                    folder_name = os.path.basename(folder_path)
                    speak(f"Switching to {folder_name}")
                    result = switch_to_window_by_title_or_path(folder_name)
                    speak(result)
                    return

            if action_l.startswith(("https://","http://")):
                domain = action_l.split("//")[-1].split("/")[0].split('.')[0]
                speak(f"Switching to {domain}")
                result = switch_to_window_by_title_or_path(domain)
                speak(result)
                return

            speak(f"Switching to {key}")
            result = switch_to_window_by_title_or_path(key)
            speak(result)
            return
        # If no key found, silently fall through to LLM

    # Close
    if intent == "close":
        name = (intent_data.get("target") or "").strip().lower()
        if not name:
            speak("I didn't catch what to close.")
            return

        CONFIG = load_config()
        ALL = {}
        for section in ["custom_commands","app_launch_commands","system_commands","website_commands"]:
            ALL.update(CONFIG.get(section, {}))

        key, action = _lookup_with_fuzzy(name, ALL)
        if key:
            action_s = action.strip()

            if action_s.endswith(".exe"):
                exe_name = os.path.basename(action_s)
                speak(f"Closing {key}")
                return close_app(exe_name)

            if action_s.startswith('start ""') and '"' in action_s:
                parts = action_s.split('"')
                if len(parts) >= 3:
                    folder_path = parts[2].strip()
                    folder_name = os.path.basename(folder_path)
                    speak(f"Closing folder {folder_name}")
                    result = close_folder_window_by_path(folder_path)
                    speak(result)
                    return

            if action_s.startswith("start "):
                app = action_s.split("start", 1)[1].strip().split(" ")[0]
                exe_map = {
                    "excel": "EXCEL.EXE","word": "WINWORD.EXE","powerpoint": "POWERPNT.EXE",
                    "cmd": "cmd.exe","chrome": "chrome.exe","explorer": "explorer.exe",
                    "notepad": "notepad.exe","code": "Code.exe",
                }
                exe = exe_map.get(app)
                if exe:
                    speak(f"Closing {app}")
                    return close_app(exe)
                else:
                    speak(f"Sorry, I couldn't map '{app}' to a known application.")
                    return

            if action_s.startswith(("https://","http://")) or "." in action_s:
                speak("Websites cannot be closed. You can close the browser manually if needed.")
                return

            speak(f"Trying to close {key}")
            return close_app(key + ".exe")
        # If no key found, silently fall through to LLM

    # Wallpaper
    if intent == "wallpaper":
        from automation.system_control import change_wallpaper_from_folder
        speak("Changing your wallpaper.")
        res = change_wallpaper_from_folder()
        speak(res)
        return

    # Power
    if intent in ("shutdown","restart","sleep"):
        speak(f"Executing {intent}.")
        res = execute_special_command(intent)
        if res:
            speak(res)
        return

    # Exit (legacy)
    if "exit" in cmd or "quit assistant" in cmd:
        return "exit"

    # --- Phase 4: LLM Command Bridge / Fallback ---
    # Attempt to use the LLM to either respond conversationally or map to a tool call
    from llm.llm_client import ask_llm_sync
    import logging

    try:
        response = ask_llm_sync(cmd)
        if response:
            # We don't call speak(response) here anymore.
            # llm_client.py handles its own TTS natively 
            # (via Piper background streaming and direct tool logging).
            pass
    except Exception as e:
        logging.error(f"Error in LLM fallback: {e}")
        speak("Sorry, I am having trouble connecting to the AI.")
