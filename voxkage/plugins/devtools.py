"""VoxKage Chrome DevTools MCP Plugin — Inspect and audit active web page tabs."""

from voxkage.plugins.base import VoxKagePlugin


class ChromeDevtoolsPlugin(VoxKagePlugin):
    name = "chrome-devtools"
    display_name = "Chrome DevTools MCP"
    description = "Inspect and control Chrome tabs, take snapshots, and analyze performance."
    required_env_vars = ["CHROME_DEVTOOLS_ENABLED"]

    def setup_interactive(self) -> bool:
        print("  Chrome DevTools MCP allows your agent to connect directly to")
        print("  running Google Chrome sessions for advanced auditing and inspection.")
        print()

        confirm = self._prompt("Enable Chrome DevTools MCP plugin? (y/n)")
        if confirm.lower() != 'y':
            return False

        self._write_env_var("CHROME_DEVTOOLS_ENABLED", "true")
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        return {
            "chrome-devtools-mcp": {
                "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
                "command": "npx",
                "args": ["-y", "chrome-devtools-mcp@latest"]
            }
        }
