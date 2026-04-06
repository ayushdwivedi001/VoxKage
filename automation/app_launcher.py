import os
from config_loader import load_config

CONFIG = load_config()

APP_COMMANDS = CONFIG.get("app_launch_commands", {})
SPECIAL_COMMANDS = CONFIG.get("system_commands", {})

def open_app(app_name):
    app_name = app_name.lower()
    command = APP_COMMANDS.get(app_name)
    if command:
        try:
            # Check if it is a shell-formatted string like: start "" "C:\..."
            raw_path = command
            if command.lower().startswith('start "" '):
                # Extract the path using split, or strip surrounding quotes
                parts = command.split('"', 3)
                if len(parts) >= 4:
                    raw_path = parts[3].strip('" ')
                
            # If the extracted path exists, execute it cleanly
            if os.path.exists(raw_path):
                try:
                    os.startfile(raw_path)
                except Exception as e:
                    try:
                        from voice.voice_manager import log_to_hud
                        log_to_hud("VoxKage", f"[System] Path error: {raw_path}")
                    except:
                        pass
                    return f"Failed to open {app_name}: Path error."
            else:
                # If path doesn't exist or isn't a file, try the raw system shell
                os.system(command)
            return f"Opening {app_name}"
        except Exception as e:
            return f"Failed to open {app_name}: {e}"
    else:
        return f"Application '{app_name}' is not supported"

def execute_special_command(action):
    action = action.lower()
    command = SPECIAL_COMMANDS.get(action)
    if command:
        try:
            raw_path = command
            if command.lower().startswith('start "" '):
                parts = command.split('"', 3)
                if len(parts) >= 4:
                    raw_path = parts[3].strip('" ')
                    
            if os.path.exists(raw_path):
                try:
                    os.startfile(raw_path)
                except Exception as e:
                    try:
                        from voice.voice_manager import log_to_hud
                        log_to_hud("VoxKage", f"[System] Path error: {raw_path}")
                    except:
                        pass
                    return f"Failed to execute {action}: Path error."
            else:
                os.system(command)
            return f"Executing {action} command"
        except Exception as e:
            return f"Failed to execute {action}: {e}"
    else:
        return f"Special command '{action}' not recognized"
