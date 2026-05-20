"""
voxkage/plugins/registry.py — Plugin discovery, listing, and configuration.
"""

import os
import sys

# Ensure Windows console can output Unicode
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        os.system("chcp 65001 >nul 2>&1")

from voxkage.paths import env_path, package_dir, voxkage_dir


def _c(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"

RST = "\033[0m"
GREEN = _c(34, 197, 94)
RED = _c(255, 80, 80)
BLUE = _c(56, 189, 248)
DIM = _c(100, 116, 139)
META = _c(71, 85, 105)


def _load_all_plugins():
    """
    Load all registered plugins — built-in + community (entry-point discovery).

    Community plugins can register via pyproject.toml:
        [project.entry-points."voxkage.plugins"]
        my_plugin = "my_package.plugin:MyPlugin"
    """
    from voxkage.plugins.telegram import TelegramPlugin
    from voxkage.plugins.gmail import GmailPlugin
    from voxkage.plugins.spotify import SpotifyPlugin
    from voxkage.plugins.github import GitHubPlugin
    from voxkage.plugins.firebase import FirebasePlugin
    from voxkage.plugins.netlify import NetlifyPlugin
    from voxkage.plugins.supabase import SupabasePlugin
    from voxkage.plugins.devtools import ChromeDevtoolsPlugin
    from voxkage.plugins.clickhouse import ClickhousePlugin
    from voxkage.plugins.sequential_thinking import SequentialThinkingPlugin

    builtin = [
        TelegramPlugin(),
        GmailPlugin(),
        SpotifyPlugin(),
        GitHubPlugin(),
        FirebasePlugin(),
        NetlifyPlugin(),
        SupabasePlugin(),
        ChromeDevtoolsPlugin(),
        ClickhousePlugin(),
        SequentialThinkingPlugin(),
    ]
    _BUILTIN_NAMES = {
        "telegram",
        "gmail",
        "spotify",
        "github",
        "firebase",
        "netlify",
        "supabase",
        "chrome-devtools",
        "clickhouse",
        "sequential-thinking",
    }

    # Discover community plugins via importlib.metadata
    community = []
    try:
        import importlib.metadata
        eps = importlib.metadata.entry_points(group="voxkage.plugins")
        for ep in eps:
            if ep.name in _BUILTIN_NAMES:
                continue  # Skip built-in plugins (already loaded above)
            try:
                plugin_cls = ep.load()
                plugin_instance = plugin_cls()
                community.append(plugin_instance)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"[VoxKage] Failed to load community plugin '{ep.name}': {e}"
                )
    except Exception:
        pass

    return builtin + community


def list_plugins():
    """Print all plugins and their configuration status."""
    plugins = _load_all_plugins()

    print()
    print(f"  {BLUE}VoxKage Plugins{RST}")
    print(f"  {META}─────────────────────────────────────────────{RST}")
    print()

    for p in plugins:
        status = f"{GREEN}✓ Configured{RST}" if p.is_configured() else f"{RED}✗ Not configured{RST}"
        print(f"  {BLUE}{p.display_name}{RST}  {DIM}({p.name}){RST}")
        print(f"    {p.description}")
        print(f"    Status: {status}")
        if not p.is_configured():
            needs = ", ".join(p.required_env_vars)
            print(f"    Needs:  {DIM}{needs}{RST}")
            print(f"    Setup:  {BLUE}voxkage plugins add {p.name}{RST}")
        print()

    print(f"  {META}─────────────────────────────────────────────{RST}")
    print(f"  {DIM}Browser automation (Playwright) is a core feature — always active.{RST}")
    print()


def add_plugin(name: str):
    """Run interactive setup for a specific plugin."""
    plugins = _load_all_plugins()
    plugin = next((p for p in plugins if p.name == name), None)

    if not plugin:
        print(f"\n  {RED}✗  Unknown plugin: {name}{RST}")
        print(f"  Available: {', '.join(p.name for p in plugins)}\n")
        return

    if plugin.is_configured():
        print(f"\n  {GREEN}✓  {plugin.display_name} is already configured.{RST}")
        reconfigure = input("  Reconfigure? (y/N): ").strip().lower()
        if reconfigure != "y":
            return

    print(f"\n  {BLUE}Setting up {plugin.display_name}...{RST}\n")
    success = plugin.setup_interactive()

    if success:
        print(f"\n  {GREEN}✓  {plugin.display_name} configured!{RST}")
        print(f"  {DIM}Restart VoxKage to activate this plugin.{RST}\n")
        # Regenerate settings.json to include the new server
        try:
            from voxkage.cli import _generate_settings_json
            _generate_settings_json()
        except Exception:
            pass
    else:
        print(f"\n  {RED}✗  Setup cancelled or failed.{RST}\n")


def get_configured_plugin_servers() -> dict:
    """Return MCP server entries for all configured plugins."""
    servers = {}
    for plugin in _load_all_plugins():
        cfg = plugin.get_mcp_server_config()
        if cfg:
            servers.update(cfg)
    return servers
