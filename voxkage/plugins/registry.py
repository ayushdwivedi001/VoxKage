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


from voxkage.plugins.base import VoxKagePlugin

class CoreMcpServerPlugin(VoxKagePlugin):
    """Generic plugin class for Core VoxKage MCP servers."""
    def __init__(self, name: str, display_name: str, description: str, mcp_name: str, script: str, dep_modules: list, dep_packages: list):
        self.name = name
        self.display_name = display_name
        self.description = description
        self.mcp_server_name = mcp_name
        self.mcp_server_script = script
        self.dep_modules = dep_modules
        self.dep_packages = dep_packages
        self.required_env_vars = []

    def is_configured(self) -> bool:
        # Configured if all required modules are importable
        for mod in self.dep_modules:
            try:
                __import__(mod)
            except ImportError:
                return False
        return True

    def get_mcp_server_config(self) -> dict | None:
        # Core servers are registered via core_servers in cli.py, not as dynamic plugin configs
        return None

    def setup_interactive(self) -> bool:
        missing = []
        for mod, pkg in zip(self.dep_modules, self.dep_packages):
            try:
                __import__(mod)
            except ImportError:
                missing.append(pkg)

        if not missing:
            print(f"\n  ✓  All dependencies for {self.display_name} are already installed.")
            print(f"     {self.display_name} is active and ready.")
            return True

        print(f"\n  Missing dependencies for {self.display_name}: {', '.join(missing)}")
        try:
            confirm = input(f"  Would you like to install them now? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            confirm = "n"
            
        if confirm != "y":
            return False

        print(f"  Installing dependencies...")
        try:
            import subprocess
            import sys
            subprocess.run([sys.executable, "-m", "pip", "install"] + missing, check=True)
            print(f"  ✓  Successfully installed dependencies!")
            return True
        except Exception as e:
            print(f"  ✗  Failed to install dependencies: {e}")
            return False


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
    from voxkage.plugins.clickhouse import ClickhousePlugin
    from voxkage.plugins.sequential_thinking import SequentialThinkingPlugin
    from voxkage.plugins.colab import ColabPlugin

    builtin = [
        TelegramPlugin(),
        GmailPlugin(),
        SpotifyPlugin(),
        GitHubPlugin(),
        FirebasePlugin(),
        NetlifyPlugin(),
        SupabasePlugin(),
        ClickhousePlugin(),
        SequentialThinkingPlugin(),
        ColabPlugin(),
    ]

    core_plugins = [
        CoreMcpServerPlugin(
            "cognitive-core", "Cognitive Core",
            "Self-correction, self-critique, and self-evolution gates under RULE ZERO.",
            "voxkage-cognitive-core", "mcp_servers/cognitive_core_server.py",
            [], []
        ),
        CoreMcpServerPlugin(
            "websearch", "Web Search",
            "Headless, fast DuckDuckGo search and trafilatura article markdown fetching.",
            "voxkage-websearch", "mcp_servers/websearch_server.py",
            ["ddgs", "trafilatura", "lxml_html_clean", "aiohttp"], ["ddgs>=9.14.4", "trafilatura>=2.0.0", "lxml_html_clean>=0.1.0", "aiohttp"]
        ),
        CoreMcpServerPlugin(
            "browser", "Browser Automation",
            "Playwright-based Chromium browser control and DOM layout scraping.",
            "voxkage-browser", "mcp_servers/browser_server.py",
            ["playwright", "fitz"], ["playwright>=1.40", "PyMuPDF>=1.23"]
        ),
        CoreMcpServerPlugin(
            "rag", "RAG Memory",
            "Local semantic memory database using ChromaDB and sentence-transformers.",
            "voxkage-rag", "mcp_servers/rag_server.py",
            ["chromadb", "sentence_transformers", "numpy", "pyarrow"],
            ["chromadb>=1.5", "sentence-transformers>=3.0", "numpy>=1.24", "pyarrow>=14.0"]
        ),
        CoreMcpServerPlugin(
            "system", "System Control",
            "Interact with local operating system configurations and hardware defaults.",
            "voxkage-system", "mcp_servers/system_server.py",
            ["psutil", "pyautogui", "PIL"], ["psutil>=5.9", "pyautogui>=0.9", "Pillow>=10.0"]
        ),
        CoreMcpServerPlugin(
            "gui", "GUI Automation",
            "Desktop application UI accessibility control and automation.",
            "voxkage-gui", "mcp_servers/gui_server.py",
            ["pywinauto"] if sys.platform == "win32" else [],
            ["pywinauto>=0.6"] if sys.platform == "win32" else []
        ),
        CoreMcpServerPlugin(
            "health", "System Health",
            "Vitals, memory usage, CPU load, process auditing, and startup checking.",
            "voxkage-health", "mcp_servers/health_server.py",
            ["psutil"], ["psutil>=5.9"]
        ),
        CoreMcpServerPlugin(
            "notify", "Notifications",
            "Display system notifications and toast banners upon milestone completions.",
            "voxkage-notify", "mcp_servers/notify_server.py",
            ["winotify"] if sys.platform == "win32" else [],
            ["winotify>=1.1"] if sys.platform == "win32" else []
        ),
        CoreMcpServerPlugin(
            "memory", "Soul Memory",
            "Retrieve and store permanent user profile details and self-learning logs.",
            "voxkage-memory", "mcp_servers/memory_server.py",
            [], []
        ),
        CoreMcpServerPlugin(
            "tasks", "Background Tasks",
            "Manage and monitor background processes and long-running subagents.",
            "voxkage-tasks", "mcp_servers/task_server.py",
            ["psutil"], ["psutil>=5.9"]
        ),
        CoreMcpServerPlugin(
            "devserver", "Development Server",
            "Detect project types and spawn local dev servers with health checking.",
            "voxkage-devserver", "mcp_servers/devserver_server.py",
            [], []
        ),
        CoreMcpServerPlugin(
            "coding", "ACE Coding Engine",
            "Scaffold code files, generate structural skeletons, and manage plans.",
            "voxkage-coding", "mcp_servers/coding_server.py",
            [], []
        ),
        CoreMcpServerPlugin(
            "session", "Session Manager",
            "Synchronize context and logging streams across CLI and IDE panels.",
            "voxkage-session", "mcp_servers/session_server.py",
            [], []
        ),
        CoreMcpServerPlugin(
            "download", "File Downloader",
            "Acquire files, check sizes, and safely launch installers.",
            "voxkage-download", "mcp_servers/download_server.py",
            ["requests"], ["requests>=2.31"]
        ),
        CoreMcpServerPlugin(
            "file", "File Scanner",
            "Locate and analyze files using native visual layout detection.",
            "voxkage-file", "mcp_servers/file_server.py",
            ["pyautogui", "PIL"], ["pyautogui>=0.9", "Pillow>=10.0"]
        ),
        CoreMcpServerPlugin(
            "fileops", "File Operations",
            "Create, edit, convert, and delete files on the local filesystem.",
            "voxkage-fileops", "mcp_servers/file_ops_server.py",
            ["docx", "pptx", "openpyxl"], ["python-docx>=1.1", "python-pptx>=0.6", "openpyxl>=3.1"]
        ),
        CoreMcpServerPlugin(
            "oscontrol", "OS Control",
            "Perform file management, window scaling, process killing, and wallpaper setups.",
            "voxkage-oscontrol", "mcp_servers/os_control_server.py",
            ["psutil", "PIL"] + (["winshell"] if sys.platform == "win32" else []),
            ["psutil>=5.9", "Pillow>=10.0"] + (["winshell>=0.6"] if sys.platform == "win32" else [])
        ),
    ]

    _BUILTIN_NAMES = {
        "telegram",
        "gmail",
        "spotify",
        "github",
        "firebase",
        "netlify",
        "supabase",
        "clickhouse",
        "sequential-thinking",
        "colab",
        "cognitive-core",
        "websearch",
        "browser",
        "rag",
        "system",
        "gui",
        "health",
        "notify",
        "memory",
        "tasks",
        "devserver",
        "coding",
        "session",
        "download",
        "file",
        "fileops",
        "oscontrol",
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
    except Exception:
        pass

    return builtin + core_plugins + community


def list_plugins():
    """Print all plugins and their configuration status."""
    plugins = _load_all_plugins()

    print()
    print(f"  {BLUE}VoxKage Plugins{RST}")
    print(f"  {META}─────────────────────────────────────────────{RST}")

    builtin_names = {
        "telegram", "gmail", "spotify", "github", "firebase",
        "netlify", "supabase", "clickhouse", "sequential-thinking", "colab"
    }
    core_names = {
        "cognitive-core", "websearch", "browser", "rag", "system", "gui",
        "health", "notify", "memory", "tasks", "devserver", "coding",
        "session", "download", "file", "fileops", "oscontrol"
    }

    builtins = [p for p in plugins if p.name in builtin_names]
    cores = [p for p in plugins if p.name in core_names]
    community = [p for p in plugins if p.name not in builtin_names and p.name not in core_names]

    def print_plugin_item(p):
        status = f"{GREEN}✓ Configured{RST}" if p.is_configured() else f"{RED}✗ Not configured{RST}"
        print(f"  {BLUE}{p.display_name}{RST}  {DIM}({p.name}){RST}")
        print(f"    {p.description}")
        print(f"    Status: {status}")
        if not p.is_configured():
            if p.required_env_vars:
                needs = ", ".join(p.required_env_vars)
            elif hasattr(p, 'dep_packages') and p.dep_packages:
                needs = f"Dependencies: {', '.join(p.dep_packages)}"
            else:
                needs = "None"
            print(f"    Needs:  {DIM}{needs}{RST}")
            print(f"    Setup:  {BLUE}voxkage plugins add {p.name}{RST}")
        print()

    print(f"\n  {BLUE}INTEGRATIONS{RST}")
    print(f"  {META}─────────────────────────────────────────────{RST}\n")
    for p in builtins:
        print_plugin_item(p)

    print(f"\n  {BLUE}CORE MCP SERVERS{RST}")
    print(f"  {META}─────────────────────────────────────────────{RST}\n")
    for p in cores:
        print_plugin_item(p)

    print(f"\n  {BLUE}COMMUNITY PLUGINS{RST}")
    print(f"  {META}─────────────────────────────────────────────{RST}\n")
    if community:
        for p in community:
            print_plugin_item(p)
    else:
        print(f"    (none installed)              Add community plugins via entry-points.")
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
