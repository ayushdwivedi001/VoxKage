from voxkage.paths import brain_dir
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
from voxkage._env import load_voxkage_env
load_voxkage_env()

# ── MCP server ────────────────────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-browser")
logger = logging.getLogger(__name__)


def _web_agent():
    from voxkage.automation.web_agent import (
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


import json
import time

MEMORY_FILE = os.path.join(str(brain_dir()), "frontend_memory.jsonl")

def _run_js(js_code: str) -> str:
    """Helper to dispatch JS evaluation to the active Playwright page."""
    import queue as _queue
    _, _, _, _, _pw_queue = _web_agent()
    res_q = _queue.Queue()
    _pw_queue.put(("dom_inspect", {"js_code": js_code}, res_q))
    try:
        status, result = res_q.get(timeout=15)
        if status == "ok":
            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2)
            return str(result)
        return f"DOM Evaluation Error: {result}"
    except _queue.Empty:
        return "DOM Evaluation Error: Timeout waiting for browser."

@mcp.tool()
def dom_get_elements(selector: str, properties: str = "innerHTML,className,id") -> str:
    """
    Queries the active web page DOM for elements matching the CSS selector.
    Returns clean, filtered properties without full page clutter.
    
    Parameters:
      selector: A valid CSS selector (e.g. '.btn-primary', '#header', 'article p')
      properties: Comma-separated list of element properties to extract. 
                  (default: 'innerHTML,className,id', can also use 'outerHTML', 'textContent', 'href', 'src')
    """
    props = [p.strip() for p in properties.split(",")]
    
    js_code = f"""
    (() => {{
        const elements = document.querySelectorAll('{selector}');
        const results = [];
        const props = {json.dumps(props)};
        
        elements.forEach(el => {{
            let obj = {{}};
            props.forEach(p => {{
                if (p in el) {{
                    obj[p] = el[p];
                }} else if (el.hasAttribute(p)) {{
                    obj[p] = el.getAttribute(p);
                }}
            }});
            results.push(obj);
        }});
        return results;
    }})()
    """
    return _run_js(js_code)

@mcp.tool()
def dom_get_computed_style(selector: str, pseudo_element: str = "") -> str:
    """
    Retrieves the active, computed CSS properties for the FIRST element matching the selector.
    Crucial for understanding animations, exact layouts, colors, and inherited styles.
    
    Parameters:
      selector: CSS selector for the element
      pseudo_element: Optional (e.g. '::before', '::after')
    """
    js_code = f"""
    (() => {{
        const el = document.querySelector('{selector}');
        if (!el) return {{"error": "Element not found"}};
        const pseudo = '{pseudo_element}' || null;
        const styles = window.getComputedStyle(el, pseudo);
        const result = {{}};
        // Filter out empty or useless default properties to reduce clutter
        for (let i = 0; i < styles.length; i++) {{
            const name = styles[i];
            const value = styles.getPropertyValue(name);
            if (value && value !== 'none' && value !== 'normal' && value !== 'auto' && value !== '0px') {{
                result[name] = value;
            }}
        }}
        return result;
    }})()
    """
    return _run_js(js_code)

@mcp.tool()
def dom_execute_js(code: str) -> str:
    """
    Executes raw JavaScript on the active browser page and returns the result.
    Allows for deeply specific queries or interacting with page variables.
    Make sure your code returns a serializable value (e.g., array, object, string).
    """
    # Wrap in async IIFE to allow await inside code
    js_code = f"""
    (async () => {{
        try {{
            {code}
        }} catch(e) {{
            return "JS Error: " + e.message;
        }}
    }})()
    """
    return _run_js(js_code)

@mcp.tool()
def save_frontend_snippet(title: str, code: str, description: str, tags: str) -> str:
    """
    Saves a useful frontend code snippet (HTML/CSS/JS) into VoxKage's permanent frontend memory.
    Use this autonomously when you encounter a great animation, layout, or component pattern.
    
    Parameters:
      title: Short title (e.g., 'Animated CSS Button')
      code: The code snippet
      description: Why it's useful and how it works
      tags: Comma-separated tags (e.g., 'css, animation, button')
    """
    import os
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    
    entry = {
        "timestamp": time.time(),
        "title": title,
        "description": description,
        "code": code,
        "tags": [t.strip().lower() for t in tags.split(",")]
    }
    
    try:
        with open(MEMORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + "\n")
        return f"Successfully saved snippet '{title}' to {MEMORY_FILE}"
    except Exception as e:
        return f"Failed to save snippet: {e}"

@mcp.tool()
def search_frontend_snippets(query: str) -> str:
    """
    Searches the dedicated frontend memory for previously saved code snippets and patterns.
    """
    import os
    if not os.path.exists(MEMORY_FILE):
        return "No frontend memory file exists yet. Save some snippets first!"
        
    query_lower = query.lower()
    results = []
    
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    # Search in title, desc, and tags
                    searchable_text = f"{data.get('title','')} {data.get('description','')} {' '.join(data.get('tags',[]))}".lower()
                    if query_lower in searchable_text:
                        results.append(data)
                except Exception:
                    continue
                    
        if not results:
            return f"No snippets found matching '{query}'."
            
        out = f"Found {len(results)} matching frontend snippets:\n\n"
        for r in results:
            out += f"--- {r.get('title')} ---\n"
            out += f"Tags: {', '.join(r.get('tags', []))}\n"
            out += f"Description: {r.get('description')}\n"
            out += f"Code:\n{r.get('code')}\n\n"
            
        return out
    except Exception as e:
        return f"Error searching memory: {e}"

def _dispatch_task(action: str, kwargs: dict, timeout: int = 30) -> str:
    import queue as _queue
    import json
    _, _, _, _, _pw_queue = _web_agent()
    res_q = _queue.Queue()
    _pw_queue.put((action, kwargs, res_q))
    try:
        status, result = res_q.get(timeout=timeout)
        if status == "ok":
            if isinstance(result, (dict, list)):
                return json.dumps(result, indent=2)
            return str(result)
        return f"Error: {result}"
    except _queue.Empty:
        return f"Error: Timeout ({timeout}s) waiting for browser execution of action '{action}'."

@mcp.tool()
def take_snapshot(verbose: bool = False, filePath: Optional[str] = None) -> str:
    """
    Take a text snapshot of the currently selected page based on the a11y tree.
    The snapshot lists page elements along with a unique identifier (uid).
    Always use the latest snapshot. Prefer taking a snapshot over taking a screenshot.
    
    Parameters:
      verbose: Whether to include extra element layout and class detail. Default is false.
      filePath: Absolute path to save the snapshot to instead of attaching it to response.
    """
    return _dispatch_task("take_snapshot", {"verbose": verbose, "filePath": filePath})

@mcp.tool()
def click(uid: str, dblClick: bool = False, includeSnapshot: bool = False) -> str:
    """
    Clicks on the provided element by its uid from the snapshot.
    
    Parameters:
      uid: The unique identifier of the element from take_snapshot.
      dblClick: Set to true for double clicks. Default is false.
      includeSnapshot: Set to true to return the post-action snapshot. Default is false.
    """
    res = _dispatch_task("click_uid", {"uid": uid, "dblClick": dblClick})
    if includeSnapshot:
        snap = _dispatch_task("take_snapshot", {})
        return f"{res}\n\n=== Post-Click Snapshot ===\n{snap}"
    return res

@mcp.tool()
def hover(uid: str, includeSnapshot: bool = False) -> str:
    """
    Hovers over the provided element by its uid.
    
    Parameters:
      uid: The unique identifier of the element.
      includeSnapshot: Set to true to return the post-action snapshot. Default is false.
    """
    res = _dispatch_task("hover_uid", {"uid": uid})
    if includeSnapshot:
        snap = _dispatch_task("take_snapshot", {})
        return f"{res}\n\n=== Post-Hover Snapshot ===\n{snap}"
    return res

@mcp.tool()
def fill(uid: str, value: str, includeSnapshot: bool = False) -> str:
    """
    Type text into an input, text area, or select an option from a select element.
    For checkboxes/toggles, pass "true" or "false" to check/uncheck.
    
    Parameters:
      uid: Unique identifier of the target element.
      value: The text or toggle value to input.
      includeSnapshot: Set to true to return the post-action snapshot.
    """
    res = _dispatch_task("fill_uid", {"uid": uid, "value": value})
    if includeSnapshot:
        snap = _dispatch_task("take_snapshot", {})
        return f"{res}\n\n=== Post-Fill Snapshot ===\n{snap}"
    return res

@mcp.tool()
def fill_form(elements: list, includeSnapshot: bool = False) -> str:
    """
    Batch fill multiple form elements at once.
    
    Parameters:
      elements: List of dictionaries with "uid" and "value" keys.
      includeSnapshot: Set to true to return the post-action snapshot.
    """
    res = _dispatch_task("fill_form", {"elements": elements})
    if includeSnapshot:
        snap = _dispatch_task("take_snapshot", {})
        return f"{res}\n\n=== Post-Fill Snapshot ===\n{snap}"
    return res

@mcp.tool()
def drag(from_uid: str, to_uid: str) -> str:
    """
    Drags an element to another target element by their uids.
    """
    return _dispatch_task("drag_uid", {"from_uid": from_uid, "to_uid": to_uid})

@mcp.tool()
def type_text(text: str, submitKey: Optional[str] = None) -> str:
    """
    Types text into the currently focused element page-wide.
    
    Parameters:
      text: The text string to type.
      submitKey: Optional hotkey to press after typing (e.g. "Enter").
    """
    return _dispatch_task("type_text", {"text": text, "submitKey": submitKey})

@mcp.tool()
def press_key(key: str) -> str:
    """
    Simulates a key press (e.g. "Enter", "Control+a", "Backspace").
    """
    return _dispatch_task("press_key", {"key": key})

@mcp.tool()
def wait_for(text: str, timeout: int = 5000) -> str:
    """
    Waits for a text or selector to load/appear on the active page.
    """
    return _dispatch_task("wait_for", {"text": text, "timeout": timeout}, timeout=(timeout//1000 + 5))

@mcp.tool()
def list_pages() -> str:
    """
    Lists all currently open tabs/pages in the browser session.
    """
    return _dispatch_task("list_pages", {})

@mcp.tool()
def new_page(url: Optional[str] = None) -> str:
    """
    Opens a new page/tab and brings it to focus.
    """
    return _dispatch_task("new_page", {"url": url})

@mcp.tool()
def close_page(pageId: Optional[str] = None) -> str:
    """
    Closes a tab by its pageId index. If pageId is omitted, closes active page.
    """
    return _dispatch_task("close_page", {"pageId": pageId})

@mcp.tool()
def select_page(pageId: str) -> str:
    """
    Brings the specified page tab to the front and makes it active.
    """
    return _dispatch_task("select_page", {"pageId": pageId})

@mcp.tool()
def resize_page(width: int, height: int) -> str:
    """
    Resizes the current tab's viewport to the specified width and height.
    """
    return _dispatch_task("resize_page", {"width": width, "height": height})

@mcp.tool()
def navigate_page(type: str, url: Optional[str] = None) -> str:
    """
    Controls page navigation history or goes to a direct URL.
    
    Parameters:
      type: "url", "back", "forward", or "reload".
      url: The target address (required only for type="url").
    """
    return _dispatch_task("navigate_page", {"type": type, "url": url})

@mcp.tool()
def emulate(
    network: Optional[str] = None,
    cpu: Optional[str] = None,
    geolocation: Optional[dict] = None,
    userAgent: Optional[str] = None,
    colorScheme: Optional[str] = None,
    viewport: Optional[dict] = None
) -> str:
    """
    Emulates network, CPU speed, geolocation coordinates, custom color scheme, or viewports.
    """
    return _dispatch_task("emulate", {
        "network": network,
        "cpu": cpu,
        "geolocation": geolocation,
        "userAgent": userAgent,
        "colorScheme": colorScheme,
        "viewport": viewport
    })

@mcp.tool()
def handle_dialog(action: str, promptText: Optional[str] = None) -> str:
    """
    Registers a handler to accept or dismiss dialog alerts, confirms, or prompts.
    
    Parameters:
      action: "accept" or "dismiss".
      promptText: Text to enter into prompt box.
    """
    return _dispatch_task("handle_dialog", {"action": action, "promptText": promptText})

@mcp.tool()
def upload_file(uid: str, filePath: str) -> str:
    """
    Uploads a file to the input file element matching the target uid.
    """
    return _dispatch_task("upload_file", {"uid": uid, "filePath": filePath})

@mcp.tool()
def list_console_messages(pageSize: int = 20, types: Optional[str] = None) -> str:
    """
    Lists captured browser console logs.
    
    Parameters:
      pageSize: Number of logs to return. Default is 20.
      types: Comma-separated list of log types to filter (e.g. "error,warning,log").
    """
    return _dispatch_task("list_console", {"pageSize": pageSize, "types": types})

@mcp.tool()
def get_console_message(msgid: str) -> str:
    """
    Retrieves full details of a specific console message by its unique id.
    """
    return _dispatch_task("get_console", {"msgid": msgid})

@mcp.tool()
def list_network_requests(pageSize: int = 50, resourceTypes: Optional[str] = None) -> str:
    """
    Lists captured network requests.
    
    Parameters:
      pageSize: Number of requests to return. Default is 50.
      resourceTypes: Comma-separated list of resource types to filter (e.g. "document,image,script").
    """
    return _dispatch_task("list_network", {"pageSize": pageSize, "resourceTypes": resourceTypes})

@mcp.tool()
def get_network_request(reqid: str) -> str:
    """
    Retrieves full details of a specific network request by its unique id.
    """
    return _dispatch_task("get_network", {"reqid": reqid})

@mcp.tool()
def take_memory_snapshot() -> str:
    """
    Captures a simulated high-fidelity heap memory utilization statistics snapshot.
    """
    return _dispatch_task("memory_snapshot", {})

@mcp.tool()
def performance_start_trace() -> str:
    """
    Starts recording page performance profiling metrics.
    """
    return _dispatch_task("performance_trace", {"trace_action": "start"})

@mcp.tool()
def performance_stop_trace() -> str:
    """
    Stops page performance recording and dumps statistics summary.
    """
    return _dispatch_task("performance_trace", {"trace_action": "stop"})

@mcp.tool()
def performance_analyze_insight() -> str:
    """
    Analyzes window.performance loading metrics and returns critical optimization stats.
    """
    return _dispatch_task("performance_trace", {"trace_action": "analyze"})

@mcp.tool()
def lighthouse_audit(mode: str = "navigation", device: str = "desktop") -> str:
    """
    Runs a high-fidelity visual and structural accessibility/SEO/Performance Lighthouse audit.
    """
    return _dispatch_task("lighthouse_audit", {"mode": mode, "device": device})

if __name__ == "__main__":
    mcp.run()
