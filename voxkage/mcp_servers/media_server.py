"""
MCP Server: Media Control (Spotify & YouTube)

Standalone — run directly:
    python mcp_servers/media_server.py
"""

import os
import sys
import random
import logging
import threading
import ctypes
import time


# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from _env import load_voxkage_env
load_voxkage_env()

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-media")
logger = logging.getLogger(__name__)

# ── Auto-focus: bring VoxKage terminal back after browser opens ───────────────
def _focus_voxkage_after_delay(delay: float = 4.0):
    """
    Waits `delay` seconds then brings the VoxKage CMD window to the foreground.
    Called in a daemon thread so it never blocks the MCP tool response.
    Searches for a window titled 'VoxKage' (set by the tray launcher via start "VoxKage" cmd).
    """
    def _do_focus():
        time.sleep(delay)
        target_hwnd = [0]

        def _enum_cb(hwnd, _):
            try:
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if "VoxKage" in title and ctypes.windll.user32.IsWindowVisible(hwnd):
                    target_hwnd[0] = hwnd
                    return False  # stop enumeration
            except Exception:
                pass
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)

        hwnd = target_hwnd[0]
        if hwnd:
            # Restore if minimised, then set foreground
            ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            logger.info(f"[AutoFocus] Brought VoxKage window (hwnd={hwnd}) to front.")
        else:
            logger.debug("[AutoFocus] VoxKage window not found — already in front or renamed.")

    t = threading.Thread(target=_do_focus, daemon=True)
    t.start()




def _yt():
    from voxkage.automation.web_agent import (
        search_media_options as yt_search,
        play_media_selection as yt_play,
        control_media_web,
        _dispatch_task,
    )
    return yt_search, yt_play, control_media_web, _dispatch_task


def _spotify():
    from voxkage.automation.spotify_control import (
        search_spotify_app, play_spotify_app, control_spotify_app,
        USER_PLAYLISTS, browse_spotify_search, browse_spotify_play,
        control_spotify_web, is_spotify_app_installed,
        play_spotify_selection_api,
    )
    return (
        search_spotify_app, play_spotify_app, control_spotify_app,
        USER_PLAYLISTS, browse_spotify_search, browse_spotify_play,
        control_spotify_web, is_spotify_app_installed,
        play_spotify_selection_api,
    )



# ── YouTube search cache (shared between search and play within this process) ──
_yt_last_results: list = []



def _youtube_search_http(query: str) -> list:
    """
    Scrapes YouTube search results via HTTP — no browser, no Playwright.
    Uses a multi-pattern fallback chain to handle all YouTube response variants.
    ~1-2 seconds total.
    """
    import re
    import urllib.parse
    import requests as _req

    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={encoded}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = _req.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    raw = resp.text

    # Extract all video IDs (deduplicated list preserves order)
    ids = re.findall(r'"videoId":"([^"]{11})"', raw)

    # Pattern 1: standard runs-style title (most common format)
    titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+?)"\}', raw)

    # Pattern 2: simpleText title (alternate YouTube format)
    if len(titles) < 3:
        titles = re.findall(r'"title":\{"simpleText":"([^"]+?)"', raw)

    # Pattern 3: accessibility label fallback
    if len(titles) < 3:
        titles = re.findall(r'"label":"([^"]+?)"', raw)

    seen, results = set(), []
    for vid_id, title in zip(ids, titles):
        if vid_id in seen:
            continue
        seen.add(vid_id)
        # Skip internal YouTube IDs that aren't real videos
        if any(skip in title.lower() for skip in ["watch later", "share", "add to queue"]):
            continue
        results.append({
            "number": len(results) + 1,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={vid_id}",
        })
        if len(results) >= 5:
            break

    return results




@mcp.tool()
def search_media_options(platform: str, query: str) -> str:
    """
    Searches YouTube for videos matching a query.
    platform: "youtube" (only supported platform)
    query: what to search for

    Returns a numbered list of up to 5 results.
    Follow up with play_media_selection(number) to open the video in the browser.
    """
    global _yt_last_results

    if "youtube" not in platform.lower():
        return f"Platform '{platform}' is not supported. Only 'youtube' is available."

    try:
        results = _youtube_search_http(query)
    except Exception as e:
        return f"YouTube search failed: {e}"

    if not results:
        return "No YouTube results found for that query."

    _yt_last_results = results
    lines = "\n".join([f"{r['number']}: {r['title']}" for r in results])
    return f"Found {len(results)} YouTube results for '{query}':\n{lines}\n\nWhich number would you like to play?"




@mcp.tool()
def play_media_selection(number: int) -> str:
    """
    Opens a specific YouTube result in the browser.
    number: the result number from the last search_media_options call (1–5)
    Call search_media_options first, then call this to play the chosen video.
    """
    global _yt_last_results

    # Use the local HTTP cache first (instant, no Playwright boot)
    if _yt_last_results:
        idx = number - 1
        if 0 <= idx < len(_yt_last_results):
            video = _yt_last_results[idx]
            url = video.get("url", "")
            title = video.get("title", "video")
            if url:
                # Open in browser via subprocess (Windows start command)
                import subprocess
                subprocess.Popen(f'start "" "{url}"', shell=True)
                # Auto-bring terminal back to front after browser opens
                _focus_voxkage_after_delay(delay=4.0)
                return f"Opening '{title}' in your browser."
        return f"Invalid number {number}. I have {len(_yt_last_results)} results (1–{len(_yt_last_results)})."

    # Fallback: use Playwright if no cache (e.g. fresh session after restart)
    try:
        _, yt_play, *_ = _yt()
        return yt_play(number)
    except Exception as e:
        return f"No YouTube search results in memory. Please search first, then play a number."



@mcp.tool()
def search_spotify(query: str) -> str:
    """
    Searches Spotify for a track, artist, or album.
    Returns a numbered list. Follow up with play_spotify_selection(number).
    """
    (
        search_spotify_app, _, _, _, browse_spotify_search, *_,
        is_installed, _,
    ) = _spotify()
    if is_installed():
        opts = search_spotify_app(query)
        if opts:
            result_lines = "\n".join([f"{r['number']}: {r['title']}" for r in opts])
            return f"Found {len(opts)} Spotify tracks:\n{result_lines}"
    return browse_spotify_search(query)


@mcp.tool()
def play_spotify_selection(number: int) -> str:
    """
    Plays a specific track from the last search_spotify call.
    number: the track number to play
    """
    (
        _, _, _, _, _, browse_spotify_play,
        _, is_installed, play_selection_api,
    ) = _spotify()
    if is_installed():
        res = play_selection_api(number)
        if "Failed" not in res:
            return res
    return browse_spotify_play(number)


@mcp.tool()
def play_user_playlist(playlist_name: str = "random") -> str:
    """
    Plays one of the user's saved Spotify playlists.
    Use playlist_name='random' to pick a random playlist.
    Known playlists: 'true end???', 'scenarios'
    """
    (
        _, play_spotify_app, _, USER_PLAYLISTS, _, _,
        _, is_installed, _,
    ) = _spotify()
    _, _, _, _dispatch_task = _yt()

    pname = playlist_name.lower()
    if "true end" in pname:
        uri = USER_PLAYLISTS.get("true end???", "")
    elif "scenario" in pname:
        uri = USER_PLAYLISTS.get("scenarios", "")
    else:
        uri = random.choice(list(USER_PLAYLISTS.values())) if USER_PLAYLISTS else ""

    if not uri:
        return "No playlist URI found. Check automation/spotify_control.py USER_PLAYLISTS."

    if is_installed():
        result = play_spotify_app(uri) + " Playlist started."
        _focus_voxkage_after_delay(delay=3.0)
        return result
    else:
        web_url = uri.replace("spotify:playlist:", "https://open.spotify.com/playlist/")
        result = _dispatch_task("play_spotify_web_url", {"url": web_url}) + " Playlist started."
        _focus_voxkage_after_delay(delay=4.0)
        return result


@mcp.tool()
def media_control(action: str, target: str = "auto") -> str:
    """
    Controls media playback.
    action: play, pause, stop, next, previous, skip
    target: 'youtube', 'spotify', or 'auto' (tries both)
    """
    _, _, control_media_web, _ = _yt()
    (
        _, _, control_spotify_app, _, _, _,
        control_spotify_web, is_installed, _,
    ) = _spotify()

    if target == "youtube":
        return control_media_web(action)
    elif target == "spotify":
        if is_installed():
            return control_spotify_app(action)
        return control_spotify_web(action)
    else:
        r1 = control_media_web(action)
        r2 = control_spotify_app(action) if is_installed() else control_spotify_web(action)
        return f"YouTube: {r1} | Spotify: {r2}"


if __name__ == "__main__":
    mcp.run()
