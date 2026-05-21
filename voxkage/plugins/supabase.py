"""VoxKage Supabase MCP Plugin — Manage databases and edge functions."""

import os

from voxkage.plugins.base import VoxKagePlugin
from voxkage._env import load_voxkage_env


class SupabasePlugin(VoxKagePlugin):
    name = "supabase"
    display_name = "Supabase MCP"
    description = "Manage Supabase projects, databases, and Edge functions."
    required_env_vars = ["SUPABASE_MCP_ENABLED", "SUPABASE_ACCESS_TOKEN"]

    def setup_interactive(self) -> bool:
        print("  Supabase MCP connects directly to the Supabase remote MCP server.")
        print()
        print("  This connects you to database management, migrations, branches,")
        print("  and Edge function deployment tools.")
        print()

        confirm = self._prompt("Enable Supabase MCP plugin? (y/n)")
        if confirm.lower() != 'y':
            return False

        token = self._prompt("Enter your Supabase access token (from supabase.com/dashboard/account/tokens)")
        if not token:
            print("  Token is required. Setup cancelled.")
            return False

        self._write_env_var("SUPABASE_MCP_ENABLED", "true")
        self._write_env_var("SUPABASE_ACCESS_TOKEN", token)
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        load_voxkage_env()
        token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")

        env = {"VOXKAGE_HOME": os.environ.get("VOXKAGE_HOME", "")}
        if token:
            env["SUPABASE_ACCESS_TOKEN"] = token

        return {
            "supabase": {
                "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
                "command": "npx",
                "args": ["-y", "mcp-remote", "https://mcp.supabase.com/mcp"],
                "env": env,
            }
        }
