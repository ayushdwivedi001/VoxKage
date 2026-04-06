# command_registry.py
from automation.app_launcher import APP_COMMANDS, SPECIAL_COMMANDS
from automation.browser_control import WEBSITE_COMMANDS
from config_loader import load_config

def get_all_commands():
    config = load_config()
    custom_cmds = config.get("custom_commands", {})

    # Built-in voice command patterns
    builtin_patterns = {
        "set volume to <value>": "Adjusts system volume",
        "set brightness to <value>": "Adjusts screen brightness",
        "turn wifi on/off": "Toggles Wi-Fi",
        "turn bluetooth on/off": "Toggles Bluetooth",
        "switch to <app>": "Switches to an open app window",
        "take screenshot": "Takes a screenshot and saves it",
        "change wallpaper": "Changes to a new wallpaper from the folder",
        "close <app>": "Closes specified app (e.g., Notepad, Chrome)",
        "search youtube for <query>": "Search YouTube for something",
        "search google for <query>": "Search Google for something"
    }

    combined = {}

    # Merge commands with type info
    for k, v in APP_COMMANDS.items():
        combined[k] = f"(App) → {v}"

    for k, v in WEBSITE_COMMANDS.items():
        combined[k] = f"(Website) → {v}"

    for k, v in SPECIAL_COMMANDS.items():
        combined[k] = f"(System Action) → {v}"

    for k, v in custom_cmds.items():
        combined[k] = f"(Custom) → {v}"

    for pattern, desc in builtin_patterns.items():
        combined[pattern] = f"(Built-in) → {desc}"

    return combined
