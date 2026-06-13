"""VoxKage Google Colab MCP Plugin — Control and run Colab notebooks."""

from voxkage.plugins.base import VoxKagePlugin
from voxkage.paths import voxkage_dir


class ColabPlugin(VoxKagePlugin):
    name = "colab"
    display_name = "Google Colab"
    description = "Manage notebooks in Google Drive, write and execute Python code in Google Colab runtimes (with GPU/TPU)."
    required_env_vars = ["VOXKAGE_COLAB_ENABLED"]

    def is_package_installed(self) -> bool:
        try:
            import mcp_colab_server
            return True
        except ImportError:
            return False

    def setup_interactive(self) -> bool:
        if not self.is_package_installed():
            print("  Installing google-colab-mcp package...")
            import subprocess
            import sys
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "google-colab-mcp"], check=True)
                print("  ✓ google-colab-mcp installed successfully.")
            except Exception as e:
                print(f"  ✗ Failed to install google-colab-mcp: {e}")
                return False

        print("  Google Colab MCP requires OAuth client credentials from Google Cloud Console.")
        print()
        print("  Steps:")
        print("    1. Go to https://console.cloud.google.com/apis/credentials")
        print("    2. Create a project and enable Google Drive API.")
        print("    3. Create OAuth 2.0 Client ID (Desktop Application).")
        print("    4. Download the client credentials JSON file.")
        print()

        cred_path = self._prompt("Path to your credentials.json file")
        if not cred_path:
            return False

        from pathlib import Path
        import shutil
        import json

        src = Path(cred_path.strip('"').strip("'"))
        if not src.exists():
            print(f"  ✗ File not found: {src}")
            return False

        # Ensure .mcp-colab directory exists
        colab_dir = Path.home() / ".mcp-colab"
        colab_dir.mkdir(parents=True, exist_ok=True)

        dst = colab_dir / "credentials.json"
        shutil.copy2(src, dst)
        print(f"  → Credentials copied to {dst}")

        # Scaffold server_config.json with custom non-conflicting ports to avoid port 8080 collisions
        config_path = colab_dir / "server_config.json"
        
        default_config = {
            "server": {
                "host": "localhost",
                "port": 8089,
                "debug": True
            },
            "selenium": {
                "browser": "chrome",
                "headless": False,
                "timeout": 30,
                "implicit_wait": 10,
                "page_load_timeout": 30
            },
            "colab": {
                "base_url": "https://colab.research.google.com",
                "execution_timeout": 300,
                "max_retries": 3,
                "retry_delay": 5
            },
            "google_api": {
                "scopes": [
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/drive.file"
                ],
                "credentials_file": str(dst),
                "token_file": str(colab_dir / "token.json"),
                "oauth_port": 8568,
                "redirect_uri": "http://localhost:8568/"
            },
            "logging": {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "file": str(colab_dir / "logs" / "colab_mcp.log")
            }
        }

        # Write configuration
        config_path.write_text(json.dumps(default_config, indent=2), encoding="utf-8")
        print(f"  → Configuration scaffolded to {config_path} (pre-configured to port 8568 to avoid conflicts)")

        self._write_env_var("VOXKAGE_COLAB_ENABLED", "true")
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        import sys
        return {
            "voxkage-colab": {
                "command": sys.executable,
                "args": ["-m", "mcp_colab_server.server"],
                "env": {
                    "VOXKAGE_HOME": str(voxkage_dir()),
                }
            }
        }
