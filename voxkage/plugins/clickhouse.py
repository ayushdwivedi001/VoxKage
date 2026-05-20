"""VoxKage ClickHouse MCP Plugin — Query and analyze ClickHouse databases."""

from voxkage.plugins.base import VoxKagePlugin


class ClickhousePlugin(VoxKagePlugin):
    name = "clickhouse"
    display_name = "ClickHouse MCP"
    description = "Query ClickHouse cloud databases and view cost metrics."
    required_env_vars = ["CLICKHOUSE_MCP_ENABLED"]

    def setup_interactive(self) -> bool:
        print("  ClickHouse MCP connects to remote ClickHouse instances.")
        print()

        confirm = self._prompt("Enable ClickHouse MCP plugin? (y/n)")
        if confirm.lower() != 'y':
            return False

        self._write_env_var("CLICKHOUSE_MCP_ENABLED", "true")
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        return {
            "clickhouse": {
                "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
                "command": "npx",
                "args": ["-y", "mcp-remote", "https://mcp.clickhouse.cloud/mcp"]
            }
        }
