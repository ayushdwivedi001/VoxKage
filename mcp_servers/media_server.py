"""
MCP Server: Media Control (Spotify & YouTube)
"""

from mcp.server.fastmcp import FastMCP
import os
import sys
import random

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from automation.web_agent import search_media_options as yt_search, play_media_selection as yt_play, control_media_web, _dispatch_task
from automation.spotify_control import (
    search_spotify_app, play_spotify_app, control_spotify_app, USER_PLAYLISTS,
    browse_spotify_search, browse_spotify_play, control_spotify_web,
    is_spotify_app_installed, play_spotify_selection_api
)

mcp = FastMCP("voxkage-media")

@mcp.tool()
def search_media_options(platform: str, query: str) -> str:
    """Searches YouTube for videos."""
    return yt_search(platform, query)

@mcp.tool()
def play_media_selection(number: int) -> str:
    """Plays a specific YouTube video from search results."""
    return yt_play(number)

@mcp.tool()
def search_spotify(query: str) -> str:
    """Searches Spotify for tracks."""
    if is_spotify_app_installed():
        opts = search_spotify_app(query)
        if opts:
            log_text = "Found Spotify Tracks:\n" + "\n".join([f"{r['number']}: {r['title']}" for r in opts])
            try:
                from voice.voice_manager import log_to_hud
                log_to_hud("VoxKage", log_text)
            except:
                pass
            return (
                f"Found {len(opts)} tracks on Spotify. Read these options to the user clearly.\n"
                f"OPTIONS: {opts}"
            )
    return browse_spotify_search(query)

@mcp.tool()
def play_spotify_selection(number: int) -> str:
    """Plays a Spotify track from search results."""
    if is_spotify_app_installed():
        res = play_spotify_selection_api(number)
        if "Failed" not in res:
            return res
    return browse_spotify_play(number)

@mcp.tool()
def play_user_playlist(playlist_name: str) -> str:
    """Plays a saved Spotify playlist."""
    pname = playlist_name.lower()
    if "true end" in pname:
        uri = USER_PLAYLISTS.get("true end???", "")
    elif "scenario" in pname:
        uri = USER_PLAYLISTS.get("scenarios", "")
    else:
        uri = random.choice(list(USER_PLAYLISTS.values()))
        
    if is_spotify_app_installed():
        return play_spotify_app(uri) + " Note to self: Goal Met. Tell the user the playlist is started."
    else:
        web_url = uri.replace("spotify:playlist:", "https://open.spotify.com/playlist/")
        return _dispatch_task("play_spotify_web_url", {"url": web_url}) + " Note to self: Goal Met. Tell the user the playlist is started."

@mcp.tool()
def media_control(action: str, target: str = "auto") -> str:
    """Pauses, plays, stops, or skips media."""
    if target == "youtube":
        return control_media_web(action)
    elif target == "spotify":
        if is_spotify_app_installed():
            return control_spotify_app(action)
        return control_spotify_web(action)
    else:
        r1 = control_media_web(action)
        r2 = control_spotify_app(action) if is_spotify_app_installed() else control_spotify_web(action)
        return f"Auto control attempted. YouTube: {r1}. Spotify: {r2}."

if __name__ == "__main__":
    mcp.run()
