"""
MCP Server: System Control & App Launcher
Wraps automation/system_control.py and automation/app_launcher.py
"""

from mcp.server.fastmcp import FastMCP
import os
import sys

# Ensure we can import from the parent directory (VoxKage root)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from automation.system_control import (
    set_volume, set_brightness, toggle_wifi, toggle_bluetooth, change_wallpaper_from_folder
)
from automation.app_launcher import execute_special_command, open_app

mcp = FastMCP("voxkage-system")

@mcp.tool()
def system_control(action: str, level: int = None) -> str:
    """
    Controls PC hardware and power states.
    Actions: set_volume, set_brightness, wifi_on, wifi_off, bluetooth_on, bluetooth_off,
             wallpaper, shutdown, restart, sleep, hibernate, lock
    """
    if action == "set_volume" and level is not None:
        return set_volume(level)
    elif action == "set_brightness" and level is not None:
        return set_brightness(level)
    elif action == "wifi_on":
        return toggle_wifi(True)
    elif action == "wifi_off":
        return toggle_wifi(False)
    elif action == "bluetooth_on":
        return toggle_bluetooth(True)
    elif action == "bluetooth_off":
        return toggle_bluetooth(False)
    elif action == "wallpaper":
        return change_wallpaper_from_folder()
    elif action in ["shutdown", "restart", "sleep", "hibernate", "lock"]:
        return execute_special_command(action)
    return f"Unknown system control action: {action}"

@mcp.tool()
def open_application(app_name: str) -> str:
    """
    Launches an application installed on the PC.
    Provide the common name of the app (e.g., 'chrome', 'notepad', 'vscode').
    """
    return open_app(app_name)

if __name__ == "__main__":
    mcp.run()
