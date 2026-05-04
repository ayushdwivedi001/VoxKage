"""
MCP Server: Browser Automation & Web Agent

Standalone — run directly:
    python mcp_servers/browser_server.py
"""

import os
import sys
import logging
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Load environment variables ────────────────────────────────────────────────
from _env import load_voxkage_env
load_voxkage_env()

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-browser")
logger = logging.getLogger(__name__)


def _web_agent():
    from automation.web_agent import (
        browse_and_extract, get_browser_state,
        execute_browser_workflow_sync, agent_step_sync, _pw_queue,
        search_media_options as yt_search,
        play_media_selection as yt_play,
    )
    return browse_and_extract, get_browser_state, execute_browser_workflow_sync, agent_step_sync, _pw_queue


@mcp.tool()
def search_web(query: str) -> str:
    """
    Searches the web for any real-time or factual information.
    Use for: weather, temperature, prices, news, sports, facts, Wikipedia, anything.
    Always use this tool instead of saying you cannot access the internet.
    """
    browse_and_extract, *_ = _web_agent()
    return browse_and_extract("https://duckduckgo.com", query)


@mcp.tool()
def open_url(url: str) -> str:
    """
    Opens a specific URL in the persistent browser session.
    Use when the user says 'open X website' or 'go to X'.
    """
    import queue as _queue
    _, _, _, _, _pw_queue = _web_agent()
    res_q = _queue.Queue()
    _pw_queue.put(("agent_step", {
        "action": "goto",
        "url": url,
        "goal": f"Navigate to {url}",
        "intent": "",
    }, res_q))
    try:
        status, result = res_q.get(timeout=15)   # 30s → 15s: prevents double-block cascade
        if status == "ok":
            if isinstance(result, dict):
                return f"Opened {url}\nNow at: {result.get('url_after', url)}"
            return f"Opened {url}"
        return f"Opened {url} (warning: {result})"
    except _queue.Empty:
        return (
            f"TIMEOUT: Browser is still loading {url}. "
            f"Wait a moment, then call get_browser_state() to check. "
            f"Do NOT call open_url again immediately."
        )


@mcp.tool()
def browse_and_extract_tool(url: str, query: str = "") -> str:
    """
    Navigate to a URL and extract its text content. Optionally pass a query
    for DuckDuckGo search when url is blank.

    Returns page text + screenshot (model can see the screenshot to verify
    it landed on the right page).

    For news/articles: pass the exact article URL. Do NOT pass Google/news.google.com
    URLs — those redirect; use search_web to find the actual article URL first.
    """
    browse_and_extract, *_ = _web_agent()
    result = browse_and_extract(url, query)
    # browse_and_extract returns either a plain string (DDG) or a __vision__ dict (article)
    if isinstance(result, dict) and result.get("__vision__"):
        return result["text"]   # MCP tools must return str; screenshot surfaced via __vision__
    return str(result)


@mcp.tool()
def take_screenshot(label: str = "checkpoint") -> str:
    """
    Take a screenshot of the current browser page and return its content.
    Use this to VISUALLY VERIFY where the browser is after a navigation or
    to decide whether to scroll, click, or give up.

    Call this:
    - After agent_step(goto) to confirm you are on the right page
    - After scroll to see what new content appeared
    - When page content seems incomplete or wrong

    Parameters:
      label : A short label for the screenshot filename (e.g. "after_scroll", "reuters_homepage")
    """
    _, get_state, *_ = _web_agent()
    return get_state()


@mcp.tool()
def scroll_and_read(
    direction: str = "down",
    times: int = 2,
) -> str:
    """
    Scroll the current page and re-extract visible text content.
    Use when the page seems cut off or you need more content from below the fold.

    This is the preferred tool when:
    - Page content is short/incomplete after navigation
    - You need to see news headlines further down the page
    - The article body hasn't fully appeared yet

    Parameters:
      direction : "down" (default) or "up"
      times     : how many scroll steps (1 step ≈ 600px, default 2)
    """
    import queue as _queue
    import time as _time
    _, _, _, agent_step_sync, _ = _web_agent()
    results = []
    _wall_start = _time.monotonic()
    _WALL_LIMIT = 45  # seconds — hard cap prevents MCP server freeze on slow pages
    for i in range(max(1, min(times, 6))):
        if _time.monotonic() - _wall_start > _WALL_LIMIT:
            results.append(f"[scroll_and_read: {_WALL_LIMIT}s wall-clock limit hit after {i} scrolls]")
            break
        r = agent_step_sync({"action": "scroll", "goal": f"Scroll {direction} to reveal more content", "direction": direction})
        results.append(r.get("text", "") if isinstance(r, dict) else str(r))
    # Final extract_text (only if time budget remains)
    if _time.monotonic() - _wall_start <= _WALL_LIMIT:
        r = agent_step_sync({"action": "extract_text", "goal": "Extract full visible page text after scrolling"})
        if isinstance(r, dict):
            return r.get("text", "Could not extract text.")
        return str(r)
    partial = " ".join(t for t in results if t)
    return partial or "[scroll_and_read timed out — no text extracted]"


@mcp.tool()
def get_browser_state() -> str:
    """
    Returns the current browser state: URL, page title, visible text, screenshot path.
    Use to check what the browser is currently showing.
    """
    _, get_state, *_ = _web_agent()
    return get_state()


@mcp.tool()
def agent_thinking(goal: str = "", plan: str = "", thought: str = "", next_action: str = "") -> str:
    """
    Logs your reasoning plan for complex multi-step browser tasks.
    Call this BEFORE starting a complex research workflow.
    After calling this, you MUST immediately call agent_step or another tool.

    Use for: comparisons across 2+ sites, deep research, finding best prices, etc.
    """
    effective_goal = goal or thought or ""
    effective_plan = plan or next_action or ""

    is_done = effective_goal.upper().startswith("GOAL MET")
    if is_done:
        return (
            f"GOAL_MET: {effective_goal}\n\n"
            f"━━━ TASK COMPLETE ━━━\n"
            f"DO NOT call any more tools. Summarize the result in plain text for the user now."
        )

    return (
        f"PLAN LOGGED:\nGoal: {effective_goal}\nSteps:\n{effective_plan}\n\n"
        f"━━━ NOW CALL agent_step OR search_web TO EXECUTE THE FIRST STEP ━━━"
    )


@mcp.tool()
def agent_step(
    action: str,
    goal: str,
    url: Optional[str] = None,
    query: Optional[str] = None,
    site: Optional[str] = None,
    intent: Optional[str] = None,
    direction: Optional[str] = None,
    ms: Optional[int] = None,
    text: Optional[str] = None,
    selector: Optional[str] = None,
) -> str:
    """
    Executes ONE atomic browser action with visual verification.

    action values:
      goto           — navigate to url
      search_on_site — search for query on site domain
      search_on_page — search within current page
      click          — click element matching intent
      click_best_link — click the most relevant link on page
      scroll         — scroll direction (up/down)
      wait           — pause for ms milliseconds
      extract_text   — extract visible page text
      extract_image_urls — extract image URLs from live DOM
      extract_download_urls — extract executable/installer URLs from live DOM
      type           — type text into element matching intent/selector

    goal: always required — describe what you are trying to accomplish
    """
    _, _, _, agent_step_sync, _ = _web_agent()
    args = {
        "action":    action,
        "goal":      goal,
        "url":       url       or "",
        "query":     query     or "",
        "site":      site      or "",
        "intent":    intent    or "",
        "direction": direction or "down",
        "ms":        ms if ms is not None else 2000,  # BUGFIX: preserve ms=2000 wait default
        "text":      text      or "",
        "selector":  selector  or "",
    }
    # Strip empties/zeros but NEVER strip ms=2000 (that is a valid wait duration)
    args = {k: v for k, v in args.items() if (
        k in ("action", "goal")       # always keep required fields
        or (k == "ms" and v > 0)      # keep ms if it's a positive wait
        or (k != "ms" and v not in ("", 0))  # other fields: drop empty/zero only
    )}
    result = agent_step_sync(args)
    if isinstance(result, dict):
        if result.get("__vision__"):
            return result.get("text", str(result))
        return str(result)
    return str(result)


@mcp.tool()
def execute_browser_workflow(goal: str, steps: list) -> str:
    """
    Executes a multi-step browser workflow in sequence.
    steps: list of agent_step argument dicts.
    """
    _, _, execute_workflow, *_ = _web_agent()
    if not steps:
        return "Error: No steps provided."
    result = execute_workflow(goal, steps)
    if isinstance(result, dict):
        return result.get("text", str(result))
    return str(result)



@mcp.tool()
def find_download_url(
    software_name: str,
    platform: str = "windows",
) -> str:
    """
    Find the official download URL for a software or file.
    Navigates the browser to the official website, takes a screenshot to verify,
    and extracts the download link — WITHOUT downloading anything.

    Use this as Step 1 of the software download workflow:
      1. find_download_url("Cursor IDE") -> returns URL + screenshot confirmation
      2. Show user what was found, confirm they want to download
      3. download_file(url=..., confirmed=True)
      4. run_installer(file_path=..., confirmed=True)

    Parameters:
      software_name : name of the software (e.g. "Cursor IDE", "VS Code", "Python 3.12")
      platform      : "windows" (default), "mac", or "linux"
    """
    _, _, _, agent_step_sync, _ = _web_agent()

    plat_map = {"windows": "windows x64 .exe OR .msi", "mac": "mac .dmg", "linux": "linux .deb OR .AppImage"}
    plat_hint = plat_map.get(platform.lower(), "windows x64")

    # Step 1: Search for official download page
    search_q = f"{software_name} official download {platform}"
    search_result = agent_step_sync({
        "action": "search_on_site",
        "goal": f"Find official download page for {software_name}",
        "query": search_q,
        "site": "duckduckgo.com",
    })

    # Step 2: Click the most relevant result
    click_result = agent_step_sync({
        "action": "click_best_link",
        "goal": f"Go to the official {software_name} download page",
        "intent": f"{software_name} official site download",
    })

    # Step 3: Extract download URLs from page
    state = agent_step_sync({"action": "extract_download_urls", "goal": f"Find download links for {software_name}"})
    
    download_links = []
    current_url = ""
    page_text = ""
    if isinstance(state, dict):
        download_links = state.get("download_urls", [])
        current_url = state.get("url", "")
        page_text = state.get("text", "")

    # Filter for platform
    if platform.lower() == "windows":
        win_links = [l for l in download_links if any(k in l.lower() for k in ("win", "x64", "amd64", "setup", "install", ".exe", ".msi"))]
        download_links = win_links or download_links
    elif platform.lower() == "mac":
        mac_links = [l for l in download_links if any(k in l.lower() for k in ("mac", "darwin", "osx", ".dmg", ".pkg"))]
        download_links = mac_links or download_links
    elif platform.lower() == "linux":
        linux_links = [l for l in download_links if any(k in l.lower() for k in ("linux", "ubuntu", "debian", ".deb", ".rpm", ".appimage", ".tar.gz"))]
        download_links = linux_links or download_links

    if not download_links:
        # Return what we found so the agent can manually look
        return (
            f"Navigated to: {current_url}\n"
            f"Could not automatically extract a direct download link for {software_name} ({platform}).\n\n"
            f"Page excerpt (look for download button or link):\n{page_text[:1000]}\n\n"
            f"Next step: Use agent_step(action='click', intent='download button {platform}') "
            f"to click the download button, or use gui_step to visually find and click it."
        )

    best_link = download_links[0]
    import os
    fname = os.path.basename(best_link.split("?")[0]) or f"{software_name}_installer"

    return (
        f"Found download link for {software_name} ({platform}):\n\n"
        f"  URL      : {best_link}\n"
        f"  File     : {fname}\n"
        f"  Source   : {current_url}\n\n"
        f"Screenshot taken — verify this is the correct official page before downloading.\n\n"
        f"Next step: Call download_file(url='{best_link}', save_directory='Downloads', confirmed=False) "
        f"to get the download preview and confirm with the user."
    )


if __name__ == "__main__":
    mcp.run()
