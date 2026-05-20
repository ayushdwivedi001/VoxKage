"""VoxKage Sequential Thinking MCP Plugin — Advanced reasoning and problem solving."""

from voxkage.plugins.base import VoxKagePlugin


class SequentialThinkingPlugin(VoxKagePlugin):
    name = "sequential-thinking"
    display_name = "Sequential Thinking MCP"
    description = "Enables advanced multi-step sequential reasoning tools for the AI agent."
    required_env_vars = ["SEQUENTIAL_THINKING_ENABLED"]

    def setup_interactive(self) -> bool:
        print("  Sequential Thinking MCP equips your agent with structured,")
        print("  multi-step reasoning systems to solve highly complex tasks.")
        print()

        confirm = self._prompt("Enable Sequential Thinking MCP plugin? (y/n)")
        if confirm.lower() != 'y':
            return False

        self._write_env_var("SEQUENTIAL_THINKING_ENABLED", "true")
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        return {
            "sequential-thinking": {
                "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]
            }
        }
