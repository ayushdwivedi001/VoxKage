"""VoxKage Spotify Plugin — Music search and playback control."""

from voxkage.plugins.base import VoxKagePlugin


class SpotifyPlugin(VoxKagePlugin):
    name = "spotify"
    display_name = "Spotify"
    description = "Search and play music via Spotify. Control playback from VoxKage."
    required_env_vars = ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"]
    mcp_server_name = "voxkage-media"
    mcp_server_script = "mcp_servers/media_server.py"

    def setup_interactive(self) -> bool:
        print("  To set up Spotify, you need API credentials from Spotify Developer Dashboard.")
        print()
        print("  Steps:")
        print("    1. Go to https://developer.spotify.com/dashboard")
        print("    2. Create a new application")
        print("    3. Click 'Settings' and add this EXACT Redirect URI:")
        print("       http://127.0.0.1:8080")
        print("    4. Save, then copy the Client ID and Client Secret")
        print()

        client_id = self._prompt("Spotify Client ID")
        if not client_id:
            return False

        client_secret = self._prompt("Spotify Client Secret", secret=True)
        if not client_secret:
            return False

        self._write_env_var("SPOTIFY_CLIENT_ID", client_id)
        self._write_env_var("SPOTIFY_CLIENT_SECRET", client_secret)
        return True
