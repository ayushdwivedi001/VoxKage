"""VoxKage Gmail Plugin — OAuth-based email management."""

from voxkage.plugins.base import VoxKagePlugin


class GmailPlugin(VoxKagePlugin):
    name = "gmail"
    display_name = "Gmail"
    description = "Read, send, and manage email via Gmail API with OAuth authentication."
    required_env_vars = []  # Uses OAuth file, not env vars
    mcp_server_name = "voxkage-email"
    mcp_server_script = "mcp_servers/email_server.py"

    def is_configured(self) -> bool:
        """Gmail uses OAuth credentials.json file, not env vars."""
        from voxkage.paths import data_dir
        cred_file = data_dir() / "credentials.json"
        return cred_file.exists()

    def setup_interactive(self) -> bool:
        from voxkage.paths import data_dir

        print("  To set up Gmail, you need OAuth credentials from Google Cloud Console.")
        print()
        print("  Steps:")
        print("    1. Go to https://console.cloud.google.com/apis/credentials")
        print("    2. Create a project (or use an existing one)")
        print("    3. Enable the Gmail API")
        print("    4. Create OAuth 2.0 Client ID (Desktop application)")
        print("    5. Download the credentials.json file")
        print()

        cred_path = self._prompt("Path to your credentials.json file")
        if not cred_path:
            return False

        import shutil
        from pathlib import Path
        src = Path(cred_path.strip('"').strip("'"))
        if not src.exists():
            print(f"  ✗  File not found: {src}")
            return False

        dst = data_dir() / "credentials.json"
        shutil.copy2(src, dst)
        print(f"  → Credentials saved to {dst}")
        print("  → On first use, VoxKage will open a browser for OAuth authorization.")
        return True
