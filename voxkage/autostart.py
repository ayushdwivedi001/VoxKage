# autostart.py
import os
import sys
import winreg
from config_loader import get_config_path, APP_NAME

EXE_NAME = os.path.basename(sys.executable)  # when packaged will be VoxKage.exe
# Path to exe to run at login:
def get_run_value():
    # For portable dev uses (python script) you might want to call python with script path.
    # In production installed exe, sys.executable is fine.
    return f'"{sys.executable}"'

def enable_autostart():
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as reg:
        winreg.SetValueEx(reg, APP_NAME, 0, winreg.REG_SZ, get_run_value())

def disable_autostart():
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as reg:
            winreg.DeleteValue(reg, APP_NAME)
    except FileNotFoundError:
        pass

def is_autostart_enabled():
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as reg:
            val, _ = winreg.QueryValueEx(reg, APP_NAME)
            return bool(val)
    except Exception:
        return False
