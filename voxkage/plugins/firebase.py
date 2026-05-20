"""VoxKage Firebase MCP Plugin — Manage projects and apps."""

from voxkage.plugins.base import VoxKagePlugin


class FirebasePlugin(VoxKagePlugin):
    name = "firebase"
    display_name = "Firebase MCP"
    description = "Manage Firebase projects, list apps, and view configs. Requires local Firebase CLI login."
    required_env_vars = ["FIREBASE_MCP_ENABLED"]

    def setup_interactive(self) -> bool:
        print("  Firebase MCP utilizes your local Firebase CLI credentials.")
        print()
        print("  Steps:")
        print("    1. Install Firebase CLI locally if not already installed: npm install -g firebase-tools")
        print("    2. Run 'firebase login' in your terminal and complete authentication.")
        print()
        
        confirm = self._prompt("Enable Firebase MCP plugin? (y/n)")
        if confirm.lower() != 'y':
            return False

        self._write_env_var("FIREBASE_MCP_ENABLED", "true")
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        return {
            "firebase-mcp-server": {
                "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
                "command": "npx",
                "args": ["-y", "firebase-tools@latest", "mcp"],
            }
        }
