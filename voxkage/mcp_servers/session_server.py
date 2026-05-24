"""
MCP Server: VoxKage Session Logging System

Persistent session memory for VoxKage. Saves structured session logs as .md
files in ~/.voxkage/sessions/ and indexes them into RAG for instant retrieval
from both antigravity CLI and opencode CLI.

Tools:
  - create_session_log()  : Save a structured session log
  - list_sessions()       : List all saved session logs
  - get_session_log()     : Read a specific session log
  - search_sessions()     : Search across session logs by keyword

Run standalone: python mcp_servers/session_server.py
"""

import glob as _glob
import os as _os
import sys as _sys
from datetime import datetime as _datetime

_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), ".."))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-session")

from voxkage.paths import voxkage_dir

_SESSION_DIR = _os.path.join(str(voxkage_dir()), "sessions")


def _ensure_dir():
    _os.makedirs(_SESSION_DIR, exist_ok=True)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    keep = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_", " "):
            keep.append(ch)
    text = "".join(keep)
    parts = text.split()
    return "-".join(parts[:6]) if parts else "untitled"


@mcp.tool()
def create_session_log(
    goal: str,
    key_points: str,
    decisions: str = "",
    action_items: str = "",
    tags: str = "",
    project_dir: str = "",
) -> str:
    """
    Save a structured session log as a markdown file in ~/.voxkage/sessions/
    and index it into RAG for instant recall from both antigravity and opencode.

    Each filename is unique (includes timestamp) and records the project directory
    so sessions from different codebases are never mixed up.

    Parameters:
      goal         : What was the session trying to accomplish (1-2 sentences)
      key_points   : Bullet list or summary of what was discussed / built / discovered
      decisions    : Key decisions made during the session
      action_items : Pending items or next steps
      tags         : Comma-separated tags for easier search (e.g. "browser, tools, leetcode")
      project_dir  : Absolute path to the project/codebase this session belongs to.
                     Use this to filter sessions by project later.
    """
    _ensure_dir()
    now = _datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    slug = _slugify(goal)
    filename = f"{date_str}_{time_str}_{slug}.md"
    filepath = _os.path.join(_SESSION_DIR, filename)

    tag_line = ""
    if tags:
        tag_line = f"Tags: {tags}\n"

    proj_line = ""
    if project_dir:
        proj_line = f"Project: {project_dir}\n"

    content = f"""# Session Log — {date_str}

{tag_line}{proj_line}
## Goal

{goal.strip()}

## Key Points

{key_points.strip()}

## Decisions

{decisions.strip() if decisions.strip() else "_None recorded._"}

## Action Items

{action_items.strip() if action_items.strip() else "_None._"}
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    try:
        from voxkage.mcp_servers.rag_server import index_document
        index_document(file_path=filepath)
    except Exception:
        pass

    return f"Session log saved: {filepath}"


@mcp.tool()
def list_sessions(project_dir: str = "") -> str:
    """
    List all saved session logs sorted by date (newest first).
    Returns filename, date, goal preview, and project for each.

    Parameters:
      project_dir : Optional — filter sessions belonging to a specific project path.
                    Pass a substring like "Vision-Assistant" to match by folder name.
    """
    _ensure_dir()
    files = sorted(
        _glob.glob(_os.path.join(_SESSION_DIR, "*.md")),
        key=_os.path.getmtime,
        reverse=True,
    )

    if not files:
        return "No session logs found in ~/.voxkage/sessions/"

    matched = 0
    lines = [f"Session Logs:", ""]
    for fpath in files:
        name = _os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            raw = ""

        if project_dir:
            proj_match = [l for l in raw.splitlines() if l.startswith("Project:")]
            if not proj_match:
                continue
            if project_dir not in proj_match[0]:
                continue

        try:
            first_line = raw.splitlines()[0] if raw else "# ???"
            proj_info = ""
            for l in raw.splitlines()[:5]:
                if l.startswith("Project:"):
                    proj_info = l.strip()
                    break
            preview = first_line
            matched += 1
        except Exception:
            preview = "(could not read)"
        modified = _os.path.getmtime(fpath)
        mtime = _datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
        lines.append(f"  {name}")
        lines.append(f"    {preview}")
        if proj_info:
            lines.append(f"    {proj_info}")
        lines.append(f"    Modified: {mtime}")
        lines.append("")

    report = f"Session Logs ({matched} matched)"
    if project_dir:
        report += f" for project: {project_dir}"
    report += ":"
    lines[0] = report
    if matched == 0:
        lines.append("  (none)")
    return "\n".join(lines)


@mcp.tool()
def get_session_log(filename: str) -> str:
    """
    Read the full content of a specific session log by filename.

    Parameters:
      filename : The .md filename (e.g. "2026-05-24_browser-overhaul.md").
                 Use list_sessions() to see available files.
    """
    _ensure_dir()
    filepath = _os.path.join(_SESSION_DIR, filename)
    if not _os.path.exists(filepath):
        alt = _glob.glob(_os.path.join(_SESSION_DIR, f"*{filename}*.md"))
        if alt:
            filepath = alt[0]
        else:
            return f"Session log not found: {filename}\nUse list_sessions() to see available logs."

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading {filepath}: {e}"


@mcp.tool()
def search_sessions(query: str, project_dir: str = "") -> str:
    """
    Search across all session logs for a keyword or phrase.

    Parameters:
      query       : Keyword or phrase to search for
      project_dir : Optional — restrict search to sessions from a specific project.
                    Pass a substring like "Vision-Assistant".
    """
    _ensure_dir()
    files = sorted(
        _glob.glob(_os.path.join(_SESSION_DIR, "*.md")),
        key=_os.path.getmtime,
        reverse=True,
    )

    if not files:
        return "No session logs found. Use create_session_log() to save one."

    results = []
    q = query.lower()
    for fpath in files:
        name = _os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        if project_dir:
            proj_match = [l for l in content.splitlines() if l.startswith("Project:")]
            if not proj_match:
                continue
            if project_dir not in proj_match[0]:
                continue

        if q in content.lower():
            lines = content.splitlines()
            previews = [l.strip() for l in lines if q in l.lower()]
            snippet = "; ".join(previews[:3]) if previews else "(matched in body)"
            results.append(f"  {name}\n    {snippet}")

    if not results:
        return f"No session logs found matching: {query!r}"

    return f"Session logs matching {query!r} ({len(results)} found):\n\n" + "\n\n".join(results)


if __name__ == "__main__":
    mcp.run()
