"""VoxKage Netlify MCP Plugin — Deploy and manage sites."""

import os
from voxkage.plugins.base import VoxKagePlugin


class NetlifyPlugin(VoxKagePlugin):
    name = "netlify"
    display_name = "Netlify MCP"
    description = "Deploy sites, manage DNS, and query Netlify resources."
    required_env_vars = ["NETLIFY_PERSONAL_ACCESS_TOKEN"]

    def setup_interactive(self) -> bool:
        print("  Netlify MCP requires a Personal Access Token.")
        print()
        print("  Steps:")
        print("    1. Log in to Netlify (https://app.netlify.com)")
        print("    2. Navigate to User Settings -> Applications -> Personal access tokens.")
        print("    3. Click 'New access token', give it a name, and copy the token.")
        print()

        token = self._prompt("Netlify Personal Access Token")
        if not token:
            return False

        self._write_env_var("NETLIFY_PERSONAL_ACCESS_TOKEN", token)
        return True

    def get_mcp_server_config(self) -> dict | None:
        if not self.is_configured():
            return None

        return {
            "netlify": {
                "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
                "command": "npx",
                "args": ["-y", "@netlify/mcp"],
                "env": {
                    "NETLIFY_PERSONAL_ACCESS_TOKEN": os.environ.get("NETLIFY_PERSONAL_ACCESS_TOKEN", "")
                }
            }
        }
