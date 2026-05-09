import pyautogui
import os
from datetime import datetime

def take_screenshot():
    folder = os.path.expandvars(r"%USERPROFILE%\Pictures\Screenshots")
    os.makedirs(folder, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filepath = os.path.join(folder, f"screenshot_{timestamp}.png")

    screenshot = pyautogui.screenshot()
    screenshot.save(filepath)
    return filepath
