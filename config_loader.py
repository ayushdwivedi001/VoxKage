import json
import os
import sys
from pathlib import Path

APP_NAME = "VoxKage"

def get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller onefile/onedir."""
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_appdata_dir() -> Path:
    # Use %APPDATA%\VoxKage
    p = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_config_path() -> Path:
    return get_appdata_dir() / "config.json"

def default_config() -> dict:
    # Minimal safe default config for a fresh install
    return {
        "wake_word": "voxkage",
        "voice_password": "",
        "protected_commands": [],
        "custom_commands": {},
        "app_launch_commands": {
            "notepad": "notepad",
            "calculator": "calc",
            "cmd": "start cmd",
            "files": "explorer",
            "pictures": r'start "" "%USERPROFILE%\\Pictures"',
            "downloads": r'start "" "%USERPROFILE%\\Downloads"',
            "desktop": r'start "" "%USERPROFILE%\\Desktop"'
        },
        "system_commands": {
            "shutdown": "shutdown /s /t 1",
            "restart": "shutdown /r /t 1",
            "go to sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
        },
        "website_commands": {
            "google": "https://www.google.com",
            "youtube": "https://www.youtube.com",
            "chat gpt": "https://chat.openai.com"
        },
        "autostart": False,
        "voice_replies": True,
        "voice_mode": "voice_chat"  # "voice_chat" or "chat_only"
    }

def load_config() -> dict:
    cfg_path = get_config_path()

    # Migration: if a config.json exists next to the executing script (dev mode), copy it if user config absent
    local_cfg_path = Path(__file__).parent / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # corrupted: back it up and recreate
            try:
                bk = cfg_path.with_suffix(".corrupt.json")
                cfg_path.rename(bk)
            except Exception:
                pass

    # if no user config file, check for local dev config to copy (only if user config missing)
    if local_cfg_path.exists() and not cfg_path.exists():
        try:
            cfg_path.write_text(local_cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not cfg_path.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg

    # fallback: try to read one more time
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        cfg = default_config()
        save_config(cfg)
        return cfg

def save_config(cfg: dict):
    cfg_path = get_config_path()
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)