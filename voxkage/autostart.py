# autostart.py
"""
VoxKage autostart — registers/removes the tray daemon from Windows startup.

Registry key: HKCU\Software\Microsoft\Windows\CurrentVersion\Run\VoxKage
Value: path to `voxkage tray` (the pipx-installed entrypoint)

This ensures `voxkage tray` (not the raw python interpreter) starts on login,
which in turn starts the Telegram watcher and keeps the system tray alive.
"""
import os
import sys
import shutil
from voxkage.config_loader import APP_NAME

# ── Resolve the `voxkage` CLI entrypoint ──────────────────────────────────────
def _get_voxkage_exe() -> str:
    """
    Return the full path to the `voxkage` CLI binary.

    Tries (in order):
      1. shutil.which("voxkage")     — works for pipx-installed users
      2. The scripts dir next to sys.executable (venv installs)
      3. Falls back to `sys.executable -m voxkage` as a last resort
    """
    # Option 1 — pipx / system PATH
    exe = shutil.which("voxkage")
    if exe:
        return f'"{exe}" tray'

    # Option 2 — same Scripts dir as the current interpreter
    scripts = os.path.join(os.path.dirname(sys.executable), "Scripts")
    for name in ("voxkage.exe", "voxkage"):
        candidate = os.path.join(scripts, name)
        if os.path.exists(candidate):
            return f'"{candidate}" tray'

    # Option 3 — fallback: spawn via python -m voxkage tray
    return f'"{sys.executable}" -m voxkage tray'


def enable_autostart():
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value = _get_voxkage_exe()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as reg:
        winreg.SetValueEx(reg, APP_NAME, 0, winreg.REG_SZ, value)


def disable_autostart():
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as reg:
            winreg.DeleteValue(reg, APP_NAME)
    except FileNotFoundError:
        pass


def is_autostart_enabled() -> bool:
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as reg:
            val, _ = winreg.QueryValueEx(reg, APP_NAME)
            return bool(val)
    except Exception:
        return False
