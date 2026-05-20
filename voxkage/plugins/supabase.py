"""VoxKage Supabase MCP Plugin — Manage databases and edge functions."""

from voxkage.plugins.base import VoxKagePlugin


class SupabasePlugin(VoxKagePlugin):
    name = "supabase"
    display_name = "Supabase MCP"
    description = "Manage Supabase projects, databases, and Edge functions."
    required_env_vars = ["SUPABASE_MCP_ENABLED"]

    def setup_interactive(self) -> bool:
        print("  Supabase MCP connects directly to the Supabase remote MCP server.")
        print()
        print("  This connects you to database management, migrations, branches,")
        print("  and Edge function deployment tools.")
        print()

        confirm = self._prompt("Enable Supabase MCP plugin? (y/n)")
        if confirm.lower() != 'y':
            return False

        self._write_env_var("SUPABASE_MCP_ENABLED", "true")
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        return {
            "supabase": {
                "serverUrl": "https://mcp.supabase.com/mcp"
            }
        }
