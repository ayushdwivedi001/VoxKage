"""VoxKage GitHub Plugin — Git operations, Actions, and repository management."""

from voxkage.plugins.base import VoxKagePlugin


class GitHubPlugin(VoxKagePlugin):
    name = "github"
    display_name = "GitHub"
    description = "Clone repos, manage PRs, check Actions, and automate Git workflows."
    required_env_vars = ["GITHUB_TOKEN"]
    mcp_server_name = "voxkage-github"
    mcp_server_script = "mcp_servers/github_server.py"

    def setup_interactive(self) -> bool:
        print("  To set up GitHub, you need a Personal Access Token (PAT).")
        print()
        print("  Steps:")
        print("    1. Go to https://github.com/settings/tokens")
        print("    2. Generate a new token (classic)")
        print("    3. Select scopes: repo, workflow, read:org")
        print("    4. Copy the generated token")
        print()

        token = self._prompt("GitHub Personal Access Token", secret=True)
        if not token:
            return False

        self._write_env_var("GITHUB_TOKEN", token)
        return True
