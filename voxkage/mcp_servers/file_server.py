"""
MCP Server: File Analysis, Screenshot & OS-wide Smart Finder

Standalone — run directly:
    python mcp_servers/file_server.py
"""

import os
import re
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from _env import load_voxkage_env
load_voxkage_env()

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-file")


def _parsers():
    from voxkage.automation.document_parser import analyze_specific_file_sync, find_file
    return analyze_specific_file_sync, find_file


def _screenshot():
    from voxkage.automation.screenshot import take_screenshot as do_screenshot
    return do_screenshot


# ─────────────────────────────────────────────────────────────────────────────
# OS-Wide Smart Finder Utilities
# ─────────────────────────────────────────────────────────────────────────────

_NOISE = {"the", "a", "an", "my", "is", "in", "on", "at", "file", "folder",
          "app", "application", "of", "to", "open", "go", "launch", "find", "show", "me",
          "please", "can", "you", "it", "i", "want", "that", "with"}


def _tokens(text: str) -> set:
    """Tokenize a string, removing noise words."""
    parts = set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())
    return parts - _NOISE


def _fuzzy_score(query: str, name: str) -> float:
    """
    Returns 0.0–1.0: how well `name` matches `query` via token overlap.
    Bonus if the query is a substring of the name (direct contains match).
    """
    q = _tokens(query)
    n = _tokens(name)
    if not q:
        return 0.0
    # Direct substring bonus
    if query.lower() in name.lower() or name.lower() in query.lower():
        return 1.0
    if not n:
        return 0.0
    overlap = len(q & n)
    # score = fraction of query tokens matched
    return overlap / len(q)


def _list_dir_brief(path: str, max_items: int = 50) -> str:
    """Returns a readable listing of directory contents."""
    try:
        entries = list(os.scandir(path))
    except PermissionError:
        return "(Permission denied)"
    except Exception as e:
        return f"(Error reading directory: {e})"

    folders = sorted([e for e in entries if e.is_dir()], key=lambda e: e.name.lower())
    files = sorted([e for e in entries if e.is_file()], key=lambda e: e.name.lower())

    lines = []
    half = max_items // 2

    if folders:
        lines.append(f"📁 Folders ({len(folders)}):")
        for e in folders[:half]:
            lines.append(f"  {e.name}/")
        if len(folders) > half:
            lines.append(f"  ... and {len(folders) - half} more folders")

    if files:
        lines.append(f"📄 Files ({len(files)}):")
        for e in files[:half]:
            try:
                size = e.stat().st_size
                size_str = f"{size // 1024}KB" if size > 1024 else f"{size}B"
            except Exception:
                size_str = "?"
            lines.append(f"  {e.name} ({size_str})")
        if len(files) > half:
            lines.append(f"  ... and {len(files) - half} more files")

    lines.append(f"\nTotal: {len(folders)} folders, {len(files)} files")
    return "\n".join(lines)


def _get_config_dirs() -> list:
    """Extract all real folder paths defined in config.json."""
    from config_loader import load_config
    config = load_config()
    dirs = []
    for cmd in config.get("app_launch_commands", {}).values():
        if 'start "" "' in cmd or "start \\\"\\\" " in cmd:
            # Extract the path from: start "" "C:\path"
            parts = cmd.split('"')
            for i, p in enumerate(parts):
                if len(p) > 3 and (":" in p or "\\" in p):
                    candidate = p.strip()
                    if os.path.isdir(candidate):
                        dirs.append(candidate)
    return list(set(dirs))


def _find_best_match(description: str, threshold: float = 0.5):
    """
    Searches the OS for any file, folder, or app matching `description`.

    Priority order:
      1. config.json app_launch_commands (fuzzy)
      2. config.json website_commands (fuzzy)
      3. C:\\ root-level folders (fuzzy)
      4. User folders: Desktop, Documents, Downloads, Pictures, Videos (2 levels)
      5. User-configured folders from config.json (2 levels)
      6. Installed apps: AppData\\Local\\Programs (exe + folder names)
      7. Program Files / Program Files (x86) (folder names only, 1 level)
      8. Start Menu shortcuts (.lnk files, recursive)

    Returns (kind, display_name, path_or_cmd) or None.
    """
    from config_loader import load_config
    config = load_config()
    user_home = os.path.expanduser("~")

    # ── 1. Config app_launch_commands ────────────────────────────────────────
    for alias, cmd in config.get("app_launch_commands", {}).items():
        score = _fuzzy_score(description, alias)
        if score >= threshold:
            return ("app_config", alias, cmd)

    # ── 2. Config website_commands ───────────────────────────────────────────
    for alias, url in config.get("website_commands", {}).items():
        if _fuzzy_score(description, alias) >= threshold:
            return ("website", alias, url)

    # ── 3. C:\ root-level folders ────────────────────────────────────────────
    try:
        for entry in os.scandir("C:\\"):
            if entry.is_dir():
                if _fuzzy_score(description, entry.name) >= threshold:
                    return ("folder", entry.name, entry.path)
    except Exception:
        pass

    # ── 4. User folders (2 levels deep) ──────────────────────────────────────
    user_scan_roots = [
        os.path.join(user_home, "Desktop"),
        os.path.join(user_home, "Documents"),
        os.path.join(user_home, "Downloads"),
        os.path.join(user_home, "Pictures"),
        os.path.join(user_home, "Videos"),
        os.path.join(user_home, "Music"),
    ]
    _INSTALLER_KEYWORDS = ("setup", "install", "usersetup", "update", "uninstall")

    best_file = None
    best_file_score = 0.0
    for root in user_scan_roots:
        if not os.path.exists(root):
            continue
        try:
            for entry in os.scandir(root):
                # Skip installer files — prefer actual apps/docs over setup binaries
                fname_lower = entry.name.lower()
                if any(kw in fname_lower for kw in _INSTALLER_KEYWORDS):
                    continue
                score = _fuzzy_score(description, entry.name)
                if score >= threshold and score > best_file_score:
                    best_file_score = score
                    best_file = entry
                # Level 2
                if entry.is_dir():
                    try:
                        for sub in os.scandir(entry.path):
                            sub_lower = sub.name.lower()
                            if any(kw in sub_lower for kw in _INSTALLER_KEYWORDS):
                                continue
                            s2 = _fuzzy_score(description, sub.name)
                            if s2 >= threshold and s2 > best_file_score:
                                best_file_score = s2
                                best_file = sub
                    except Exception:
                        pass
        except Exception:
            pass
    if best_file:
        kind = "folder" if best_file.is_dir() else "file"
        return (kind, best_file.name, best_file.path)



    # ── 5. User-configured folders from config (2 levels) ────────────────────
    for cfg_dir in _get_config_dirs():
        if not os.path.exists(cfg_dir):
            continue
        try:
            for entry in os.scandir(cfg_dir):
                if _fuzzy_score(description, entry.name) >= threshold:
                    kind = "folder" if entry.is_dir() else "file"
                    return (kind, entry.name, entry.path)
        except Exception:
            pass

    # ── 6. AppData\Local\Programs (user-installed apps) ───────────────────────
    local_programs = os.path.join(user_home, "AppData", "Local", "Programs")
    if os.path.exists(local_programs):
        best_name, best_path, best_score = None, None, 0.0
        try:
            for app_dir in os.scandir(local_programs):
                dir_score = _fuzzy_score(description, app_dir.name)
                if dir_score > best_score:
                    # Look for main exe inside
                    try:
                        exes = [e for e in os.scandir(app_dir.path)
                                if e.is_file() and e.name.lower().endswith(".exe")]
                        if exes:
                            best_score = dir_score
                            best_name = app_dir.name
                            # Prefer the exe named closest to the app dir
                            best_path = max(exes, key=lambda e: _fuzzy_score(description, e.name[:-4])).path
                    except Exception:
                        pass
                    # Also recurse one level for installers that put exe in subfolder
                    try:
                        for subdir in os.scandir(app_dir.path):
                            if subdir.is_dir():
                                for e in os.scandir(subdir.path):
                                    if e.is_file() and e.name.lower().endswith(".exe"):
                                        escore = _fuzzy_score(description, e.name[:-4])
                                        combined = max(dir_score, escore)
                                        if combined > best_score:
                                            best_score = combined
                                            best_name = e.name
                                            best_path = e.path
                    except Exception:
                        pass
        except Exception:
            pass
        if best_path and best_score >= threshold:
            return ("exe", best_name, best_path)

    # ── 6. User-configured folders from config (2 levels) ────────────────────
    for cfg_dir in _get_config_dirs():
        if not os.path.exists(cfg_dir):
            continue
        try:
            for entry in os.scandir(cfg_dir):
                if _fuzzy_score(description, entry.name) >= threshold:
                    kind = "folder" if entry.is_dir() else "file"
                    return (kind, entry.name, entry.path)
        except Exception:
            pass

    # ── 7. Program Files / Program Files (x86) — folder names only ───────────
    for pf in ("C:\\Program Files", "C:\\Program Files (x86)"):
        if not os.path.exists(pf):
            continue
        try:
            for app_dir in os.scandir(pf):
                if not app_dir.is_dir():
                    continue
                dir_score = _fuzzy_score(description, app_dir.name)
                if dir_score >= threshold:
                    # Find main exe
                    try:
                        exes = [e for e in os.scandir(app_dir.path)
                                if e.is_file() and e.name.lower().endswith(".exe")]
                        if exes:
                            exe = max(exes, key=lambda e: _fuzzy_score(description, e.name[:-4]))
                            return ("exe", exe.name, exe.path)
                    except Exception:
                        pass
                    return ("folder", app_dir.name, app_dir.path)
        except Exception:
            pass

    # ── 8. Start Menu shortcuts ───────────────────────────────────────────────
    start_menu_dirs = [
        "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs",
        os.path.join(user_home, "AppData", "Roaming", "Microsoft",
                     "Windows", "Start Menu", "Programs"),
    ]
    for sm_dir in start_menu_dirs:
        if not os.path.exists(sm_dir):
            continue
        try:
            import time as _t
            _sm_start = _t.monotonic()
            for dirpath, _, filenames in os.walk(sm_dir):
                # Soft 3s timeout: Stop Menu walks can be very large on some installs
                if _t.monotonic() - _sm_start > 3.0:
                    break
                for fname in filenames:
                    if fname.lower().endswith(".lnk"):
                        lnk_name = fname[:-4]
                        if _fuzzy_score(description, lnk_name) >= threshold:
                            return ("shortcut", lnk_name, os.path.join(dirpath, fname))
        except Exception:
            pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def smart_open(description: str) -> str:
    """
    Opens ANY file, folder, or application on the OS by natural language description.
    Does NOT require the item to be pre-configured anywhere.

    Searches the entire OS intelligently:
    - Pre-configured app/website shortcuts (config.json)
    - C:\\ root directories (e.g., "<user_custom_folder>", "Anime", "Movies")
    - User folders: Desktop, Documents, Downloads, Pictures, Videos
    - Installed apps in AppData\\Local\\Programs (Cursor, VS Code, Spline, etc.)
    - Program Files and Program Files (x86)
    - Start Menu shortcuts (.lnk)

    If it's a folder, also returns a full listing of its contents.

    Examples:
      smart_open("cursor")              → finds Cursor.exe in AppData\\Local\\Programs
      smart_open("ayush files")         → finds <user_custom_folder>, opens + lists it
      smart_open("anime folder")        → opens C:\\Anime
      smart_open("discord")             → finds Discord via Start Menu shortcut
      smart_open("my resume")           → finds resume PDF in Documents/Downloads
    """
    import subprocess

    # ── Short-circuit: if already a valid absolute path, open directly ────────
    if os.path.exists(description):
        if os.path.isfile(description):
            subprocess.Popen(f'start "" "{description}"', shell=True)
            return f"Opened file '{os.path.basename(description)}' at: {description}"
        elif os.path.isdir(description):
            subprocess.Popen(f'start "" "{description}"', shell=True)
            listing = _list_dir_brief(description)
            return f"Opened folder at: {description}\n\nContents:\n{listing}"

    result = _find_best_match(description)
    if not result:
        return (
            f"Could not locate anything matching '{description}' on the OS. "
            "Try a more specific name, or tell me which folder it's inside."
        )

    kind, name, path = result

    try:
        if kind == "app_config":
            from voxkage.automation.app_launcher import open_app
            return open_app(name)

        elif kind == "website":
            subprocess.Popen(f'start "" "{path}"', shell=True)
            return f"Opening website '{name}': {path}"

        elif kind == "folder":
            subprocess.Popen(f'start "" "{path}"', shell=True)
            listing = _list_dir_brief(path)
            return (
                f"Opened folder '{name}' at: {path}\n\n"
                f"Contents:\n{listing}"
            )

        elif kind == "file":
            subprocess.Popen(f'start "" "{path}"', shell=True)
            return f"Opened '{name}' at: {path}"

        elif kind == "exe":
            subprocess.Popen(
                f'start "" "{path}"', 
                shell=True, 
                cwd=os.path.dirname(path)
            )
            return f"Launched '{name}' from: {path}"

        elif kind == "shortcut":
            subprocess.Popen(f'start "" "{path}"', shell=True)
            return f"Launched '{name}' via Start Menu shortcut."

    except Exception as e:
        return f"Found '{name}' at '{path}' but failed to open it: {e}"

    return f"Unhandled match kind: {kind}"


@mcp.tool()
def browse_directory(path: str) -> str:
    """
    Returns a complete listing of any directory on the OS.
    Use this when the user wants to see what's inside a folder,
    or when you need context about a folder's contents before opening a file.

    path: absolute Windows path, e.g.:
      "C:\\Users\\<Username>\\Documents"
      "C:\\Users\\<Username>\\Downloads"
    """
    if not os.path.exists(path):
        # Try smart-finding the folder first
        result = _find_best_match(path)
        if result and result[0] in ("folder",):
            path = result[2]
        else:
            return f"Path not found: '{path}'. Check spelling or provide absolute path."

    if not os.path.isdir(path):
        return f"'{path}' is a file. Use analyze_specific_file to read its contents."

    listing = _list_dir_brief(path, max_items=60)
    return f"Contents of '{path}':\n\n{listing}"


@mcp.tool()
def analyze_specific_file(file_path: str, query: str = "") -> str:
    """
    Reads and analyzes a specific local file.
    Supports: PDF, DOCX, TXT, CSV, log files, Python scripts.
    file_path: absolute path to the file
    query: optional question to answer about the file contents
    """
    analyze, _ = _parsers()
    return analyze(file_path, query)


@mcp.tool()
def find_and_analyze_file(filename_keyword: str, query: str = "") -> str:
    """
    Searches for a file by keyword across all user directories and user-configured
    folders, then reads it.

    Searches: Documents, Downloads, Desktop, and any folders configured in config.json.
    filename_keyword: partial filename (e.g. "resume", "ayushresume", "invoice", "notes")
    query: optional question about the file

    Use when user says: 'read my resume', 'find my invoice', 'open my japanese book pdf'.
    """
    from config_loader import load_config
    config = load_config()

    user_home = os.path.expanduser("~")
    # Base search dirs
    search_dirs = [
        os.path.join(user_home, "Documents"),
        os.path.join(user_home, "Downloads"),
        os.path.join(user_home, "Desktop"),
    ]
    # Also search all real folder paths from config.json
    search_dirs.extend(_get_config_dirs())
    # Deduplicate
    search_dirs = list(dict.fromkeys(d for d in search_dirs if os.path.exists(d)))

    _, find_file = _parsers()
    file_path = find_file(filename_keyword, search_dirs=search_dirs)

    if file_path:
        analyze, _ = _parsers()
        content = analyze(file_path, query)
        return f"Found: {file_path}\n\n{content}"

    return (
        f"No file matching '{filename_keyword}' found in Documents, Downloads, "
        "Desktop, or your configured folders. Try smart_open if you want to open "
        "the folder it's in and browse it."
    )


@mcp.tool()
def take_screenshot() -> str:
    """
    Takes a screenshot of what the user is currently seeing on screen.
    Returns the ABSOLUTE FILE PATH where the screenshot was saved.

    Use when user says: 'take a screenshot', 'screenshot this', 'capture the screen'.

    VISION PIPELINE: After calling this, you MUST call:
        analyze_specific_file(file_path=<returned_path>, query="<what to look for>")
    to have Gemini visually inspect the screenshot. The file path alone is NOT
    enough — Gemini cannot see images by path, only through analyze_specific_file.
    """
    do_screenshot = _screenshot()
    filepath = do_screenshot()
    if filepath:
        return (
            f"Screenshot saved to: {filepath}\n"
            f"To visually inspect it, call: "
            f"analyze_specific_file(file_path='{filepath}', query='<your question about the screen>')"
        )
    return "Failed to take screenshot."


if __name__ == "__main__":
    mcp.run()
