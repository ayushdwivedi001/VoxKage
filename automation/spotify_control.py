import os
import json
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import subprocess
import time
from config_loader import get_appdata_dir, load_config
from automation.web_agent import _dispatch_task

logger = logging.getLogger(__name__)

# Constants
SPOTIFY_TOKEN_CACHE = os.path.join(get_appdata_dir(), "spotify_token.json")

# Fetch from config first, but fallback to environment variables
_cfg = load_config()
SPOTIPY_CLIENT_ID = _cfg.get("spotify_client_id") or os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = _cfg.get("spotify_client_secret") or os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8080")

GLOBAL_SPOTIFY_OPTIONS = []

USER_PLAYLISTS = {
    "true end???": "spotify:playlist:5EA1FkKuxcYzS1PWcJkPkR",
    "scenarios": "spotify:playlist:3m7dvMD5F40r7nXmD5WkuZ"
}

def is_spotify_app_installed() -> bool:
    """Robustly detect if the Spotify Desktop App is installed on Windows."""
    # 1. Check APPDATA path (most common for standard install)
    appdata_path = os.path.join(os.environ.get('APPDATA', ''), 'Spotify', 'Spotify.exe')
    if os.path.exists(appdata_path):
        return True
        
    # 2. Check local appdata standard
    localappdata_path = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Spotify', 'Spotify.exe')
    if os.path.exists(localappdata_path):
        return True
        
    # 3. Check Microsoft Store execution alias (Very common)
    store_alias_path = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WindowsApps', 'Spotify.exe')
    if os.path.exists(store_alias_path):
        return True
        
    # 4. Check WindowsApps directory directly (requires permissions usually)
    windows_apps_dir = os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'WindowsApps')
    if os.path.exists(windows_apps_dir):
        try:
            for folder in os.listdir(windows_apps_dir):
                if 'Spotify' in folder and os.path.exists(os.path.join(windows_apps_dir, folder, 'Spotify.exe')):
                    return True
        except PermissionError:
            # We might not have access to read WindowsApps, ignore
            pass
            
    return False

def _get_spotify_client():
    """Builds the Spotipy client if credentials are available."""
    if not SPOTIPY_CLIENT_ID or not SPOTIPY_CLIENT_SECRET:
        return None
        
    scope = "user-modify-playback-state user-read-playback-state"
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=scope,
        cache_path=SPOTIFY_TOKEN_CACHE,
        open_browser=True # Opens browser for initial login if no cached token
    )
    return spotipy.Spotify(auth_manager=sp_oauth)

def open_spotify_app_with_uri(uri: str):
    """Launch Spotify desktop app to a specific URI."""
    try:
        os.startfile(uri)
        time.sleep(2)  # Give app a moment to open
        return True
    except Exception as e:
        logger.error(f"Failed to open Spotify URI {uri}: {e}")
        return False

def play_spotify_app(uri: str = None) -> str:
    """Uses Spotify Web API to play a track/playlist if installed and keys available."""
    sp = _get_spotify_client()
    if sp:
        try:
            # If we have Web API, we can just trigger play
            params = {}
            if uri:
                if 'playlist' in uri:
                    params['context_uri'] = uri
                else:
                    params['uris'] = [uri]
            sp.start_playback(**params)
            return "Playing on Spotify via API."
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 404 and "NO_ACTIVE_DEVICE" in str(e).upper():
                logger.warning("No active device found in Spotify API. Forcing app to wake up...")
                if uri and is_spotify_app_installed():
                    open_spotify_app_with_uri(uri)
                    time.sleep(3) # Give app time to register device
                    try:
                        # Grab the first available device since we just woke it up
                        devices = sp.devices().get('devices', [])
                        if devices:
                             # Ideally play on computer
                             target_device = devices[0]['id']
                             for d in devices:
                                 if d.get('type', '').lower() == 'computer':
                                     target_device = d['id']
                                     break
                             sp.start_playback(device_id=target_device, **params)
                             return "Woke up Spotify app and started playback via targeted device API."

                        # Fallback if no device registered
                        sp.start_playback(**params)
                        return "Woke up Spotify app and started playback via API."
                    except Exception as play_e:
                        logger.error(f"Failed second attempt to play: {play_e}")
                        try:
                            import pyautogui
                            pyautogui.press('playpause')
                            return "Woke up Spotify app and triggered generic playpause keystroke."
                        except:
                            pass
            else:
                logger.warning(f"Spotify API play failed: {e}. Falling back to URI launch.")
        except Exception as e:
            logger.warning(f"Spotify API play failed completely: {e}. Falling back to URI launch.")
    
    # Fallback to local URI execution if possible
    if uri and is_spotify_app_installed():
        open_spotify_app_with_uri(uri)
        try:
            import pyautogui
            pyautogui.press('playpause')
        except:
            pass
        return "Opened Spotify playlist locally and attempted to press Play."
        
    return "Failed to trigger playback on app."


def search_spotify_app(query: str, limit: int = 5):
    """Use Spotify Web API to search for tracks."""
    global GLOBAL_SPOTIFY_OPTIONS
    sp = _get_spotify_client()
    if not sp:
        return []
    
    try:
        results = sp.search(q=query, limit=limit, type='track')
        tracks = results.get('tracks', {}).get('items', [])
        options = []
        GLOBAL_SPOTIFY_OPTIONS.clear()
        for i, t in enumerate(tracks):
            opt = {
                "number": i + 1,
                "title": f"{t['name']} by {t['artists'][0]['name']}",
                "uri": t['uri']
            }
            options.append(opt)
            GLOBAL_SPOTIFY_OPTIONS.append(opt)
        return options
    except Exception as e:
        logger.error(f"Spotify search failed: {e}")
        return []

def control_spotify_app(action: str) -> str:
    """Pause, Resume, Next, Prev via Web API."""
    sp = _get_spotify_client()
    if not sp:
        return "Spotify API not configured."
        
    try:
        if action == "pause" or action == "stop":
            sp.pause_playback()
            return "Spotify paused."
        elif action == "play" or action == "resume":
            sp.start_playback()
            return "Spotify resumed."
        elif action == "next":
            sp.next_track()
            return "Skipped to next track on Spotify."
        elif action == "prev" or action == "previous":
            sp.previous_track()
            return "Skipped to previous track on Spotify."
        elif action == "status":
            playback = sp.current_playback()
            if playback is None or not playback.get('is_playing'):
                return "Spotify is currently not playing anything or the device is inactive. Note to self: Goal Met. Tell the user no music is playing."
            item = playback.get('item', {})
            name = item.get('name', 'Unknown Song')
            artist = item.get('artists', [{}])[0].get('name', 'Unknown Artist')
            return f"Currently playing on Spotify: '{name}' by {artist}. Note to self: If the user asked you about the artist, you may now use search_web to look them up. Otherwise, Goal Met: tell the user exactly what is playing."
        else:
            return f"Unknown action: {action}"
    except Exception as e:
        logger.error(f"Spotify control failed: {e}")
        return f"Could not control Spotify: {str(e)}"

# --- BROWSER AUTOMATION FALLBACK ---

def play_spotify_selection_api(number) -> str:
    """Uses Web API to play the Nth selection from our last search."""
    global GLOBAL_SPOTIFY_OPTIONS
    try:
        number = int(number)
    except:
        return "Invalid selection number format."
        
    if number < 1 or number > len(GLOBAL_SPOTIFY_OPTIONS):
        return "Invalid selection number."
    
    uri = GLOBAL_SPOTIFY_OPTIONS[number - 1]["uri"]
    return play_spotify_app(uri)

def browse_spotify_search(query: str) -> str:
    """Fallback: open open.spotify.com via Playwright and search."""
    try:
        # We integrate this directly into the existing _dispatch_task or use a custom one.
        # Actually _pw_worker in web_agent handles complex steps. Let's add 'search_spotify_web' to pw worker.
        return _dispatch_task("search_spotify_web", {"query": query})
    except Exception as e:
        return f"Browser fallback failed: {e}"

def browse_spotify_play(number: int) -> str:
    """Fallback: play the Nth result on open.spotify.com."""
    try:
        return _dispatch_task("play_spotify_web", {"number": number})
    except Exception as e:
        return f"Browser play failed: {e}"

def control_spotify_web(action: str) -> str:
    try:
        return _dispatch_task("control_spotify_web", {"action": action})
    except Exception as e:
        return str(e)
