import json
import os
import sys
from pathlib import Path

APP_NAME = "VoxKage"

def get_resource_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_appdata_dir() -> Path:
    p = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p

def get_config_path() -> Path:
    return get_appdata_dir() / "config.json"

def default_config() -> dict:
    return {
        "autostart": False,
        "spotify_client_id": os.getenv("SPOTIFY_CLIENT_ID", ""),
        "spotify_client_secret": os.getenv("SPOTIFY_CLIENT_SECRET", "")
    }

def load_config() -> dict:
    cfg_path = get_config_path()
    if not cfg_path.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg
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
