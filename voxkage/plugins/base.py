"""
voxkage/plugins/base.py — Base class for VoxKage plugins.

Each plugin represents a token/OAuth-gated integration (Telegram, Gmail, etc).
Plugins are NOT loaded or registered as MCP servers until the user provides credentials.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class VoxKagePlugin(ABC):
    """Base class for VoxKage integration plugins."""

    name: str = ""
    display_name: str = ""
    description: str = ""
    required_env_vars: list[str] = []
    mcp_server_name: str = ""
    mcp_server_script: str = ""  # relative to package_dir, e.g. "mcp_servers/telegram_server.py"

    def is_configured(self) -> bool:
        """Check if all required env vars are set in ~/.voxkage/.env."""
        import os
        from voxkage._env import load_voxkage_env
        load_voxkage_env()

        for var in self.required_env_vars:
            val = os.environ.get(var, "").strip()
            if not val or val.startswith("your_") or val == "":
                return False
        return True

    def is_package_installed(self) -> bool:
        """Check if the Python package for this plugin is importable."""
        return True  # All plugin packages ship with voxkage core

    @abstractmethod
    def setup_interactive(self) -> bool:
        """
        Run interactive CLI setup — ask user for tokens, write to .env.
        Returns True if setup was successful.
        """
        ...

    def get_mcp_server_config(self) -> dict | None:
        """
        Return the MCP server entry for settings.json.
        Returns None if not configured.
        """
        if not self.is_configured() or not self.mcp_server_script:
            return None

        import sys
        from voxkage.paths import package_dir, voxkage_dir
        import os

        return {
            self.mcp_server_name: {
                "command": sys.executable,
                "args": [os.path.join(str(package_dir()), self.mcp_server_script)],
                "cwd": str(package_dir().parent),  # repo root so relative imports work
                "env": {"VOXKAGE_HOME": str(voxkage_dir())},
                "trust": True,
            }
        }

    def _write_env_var(self, key: str, value: str):
        """Append or update a key=value pair in ~/.voxkage/.env."""
        from voxkage.paths import env_path
        env_file = env_path()
        lines = []
        found = False

        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{key}=") or line.strip().startswith(f"# {key}="):
                    lines[i] = f"{key}={value}"
                    found = True
                    break

        if not found:
            lines.append(f"{key}={value}")

        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Force-reload env so the new token is available in this process immediately.
        # This prevents the 'plugin configured but still not working' bug.
        try:
            from voxkage._env import load_voxkage_env
            load_voxkage_env(force=True)
        except Exception:
            pass

    def _prompt(self, prompt_text: str, secret: bool = False) -> str:
        """Get input from user, optionally hiding the input for secrets."""
        if secret:
            import getpass
            return getpass.getpass(f"  {prompt_text}: ").strip()
        return input(f"  {prompt_text}: ").strip()
