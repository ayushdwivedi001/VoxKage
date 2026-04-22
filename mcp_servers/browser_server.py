"""
MCP Server: Browser Automation & Web Agent
"""

from mcp.server.fastmcp import FastMCP
import os
import sys
import logging
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from automation.browser_control import open_website
from automation.web_agent import browse_and_extract, get_browser_state, execute_browser_workflow_sync, agent_step_sync

mcp = FastMCP("voxkage-browser")
logger = logging.getLogger(__name__)

@mcp.tool()
def open_url(url: str) -> str:
    """Opens a specific website URL in the default browser."""
    return open_website(url)

@mcp.tool()
def search_web(query: str) -> str:
    """Searches the web via internal Playwright browser."""
    logger.info(f"[search_web] Using internal Playwright browser for: {query!r}")
    return browse_and_extract("https://duckduckgo.com", query)

@mcp.tool()
def browse_and_extract_tool(url: str = "google", query: str = "") -> str:
    """Navigates to a site, searches, and reads the content."""
    return browse_and_extract(url, query)

@mcp.tool()
def browser_state() -> str:
    """Returns browser state and takes a screenshot."""
    return get_browser_state()

@mcp.tool()
def execute_browser_workflow(goal: str, steps: list) -> str:
    """Executes a multi-step workflow. Fixes malformed steps if needed."""
    if not steps:
        return "Failed: No valid steps provided."
    return execute_browser_workflow_sync(goal, steps)

@mcp.tool()
def agent_thinking(thought: str, next_action: str) -> str:
    """Logs reasoning for complex browser tasks."""
    from voice.voice_manager import log_to_hud
    is_goal_met = thought.upper().startswith("GOAL MET")

    if is_goal_met:
        log_to_hud("VoxKage", f"✅ Goal Achieved: {thought}")
        return f"GOAL_MET: {thought}"
    else:
        log_to_hud("VoxKage", f"🧠 Thinking: {thought}\n→ Next: {next_action}")
        return (
            f"THINKING LOGGED: {thought}\n"
            f"NEXT PLANNED: {next_action}\n"
            f"━━━ YOU MUST CALL A TOOL IMMEDIATELY ━━━\n"
            f"FAILING TO CALL A TOOL WILL ABORT THE TASK."
        )

@mcp.tool()
def agent_step(action: str, goal: str, **kwargs) -> str:
    """Executes ONE atomic browser action."""
    args = {"action": action, "goal": goal}
    args.update(kwargs)
    return agent_step_sync(args)

if __name__ == "__main__":
    mcp.run()
