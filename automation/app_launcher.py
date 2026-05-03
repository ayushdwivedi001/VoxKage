import os
import subprocess

def open_app(app_name):
    try:
        # Let Windows resolve the command/shortcut
        os.system(f'start "" "{app_name}"')
        return f"Attempted to open {app_name}"
    except Exception as e:
        return f"Failed to open {app_name}: {e}"

def execute_special_command(action):
    action = action.lower()
    if action == "shutdown":
        os.system("shutdown /s /t 1")
    elif action == "restart":
        os.system("shutdown /r /t 1")
    elif action == "sleep":
        os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
    elif action == "hibernate":
        os.system("shutdown /h")
    elif action == "lock":
        os.system("rundll32.exe user32.dll,LockWorkStation")
    else:
        return f"Special command '{action}' not recognized"
    return f"Executed {action}"
