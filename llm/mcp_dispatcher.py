"""
MCP Dispatcher - Routes tool calls to standalone MCP servers.
Falls back to direct tool execution if the MCP server fails.
"""
import os
import sys
import json
import asyncio
import logging
from typing import Optional

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

from llm.tool_registry import execute_tool_call as fallback_execute_tool_call

logger = logging.getLogger(__name__)

# Mapping of tools to their respective MCP server file
TOOL_TO_SERVER = {
    "system_control": "system_server.py",
    "open_application": "system_server.py",
    "close_application": "system_server.py",
    "run_shell_command": "system_server.py",
    
    "search_media_options": "media_server.py",
    "play_media_selection": "media_server.py",
    "search_spotify": "media_server.py",
    "play_spotify_selection": "media_server.py",
    "play_user_playlist": "media_server.py",
    "media_control": "media_server.py",
    
    "open_url": "browser_server.py",
    "search_web": "browser_server.py",
    "browse_and_extract": "browser_server.py",
    "get_browser_state": "browser_server.py",
    "execute_browser_workflow": "browser_server.py",
    "agent_thinking": "browser_server.py",
    "agent_step": "browser_server.py",
    
    "check_gmail": "email_server.py",
    "get_email_summary": "email_server.py",
    
    "analyze_specific_file": "file_server.py",
    "find_and_analyze_file": "file_server.py",
    "take_screenshot": "file_server.py",

    "create_file": "file_ops_server.py",
    "edit_file": "file_ops_server.py",
    "delete_file": "file_ops_server.py",
    "convert_file": "file_ops_server.py",
    "list_directory": "file_ops_server.py",

    "telegram_send_message": "telegram_server.py",
    "telegram_send_report": "telegram_server.py",
    "telegram_ask_save": "telegram_server.py",
    "telegram_send_file": "telegram_server.py",
    "telegram_get_status": "telegram_server.py",

    "index_document": "rag_server.py",
    "check_and_index": "rag_server.py",
    "query_rag": "rag_server.py",
    "list_indexed_documents": "rag_server.py",
    "delete_from_rag": "rag_server.py",
    "index_directory": "rag_server.py",

    "git_clone": "github_server.py",
    "git_status": "github_server.py",
    "git_diff_summary": "github_server.py",
    "git_smart_commit": "github_server.py",
    "git_pull": "github_server.py",
    "fake_commit": "github_server.py",
    "detect_and_install_deps": "github_server.py",
    "run_project": "github_server.py",
    "kill_project": "github_server.py",
    "check_project_health": "github_server.py",
    "get_github_profile": "github_server.py",
    "list_my_repos": "github_server.py",
    "create_repo_local": "github_server.py",
    "actions_list": "github_server.py",
    "actions_get": "github_server.py",
    "get_job_logs": "github_server.py",
}

# Tools that must run via tool_registry directly when inside an async context.
# These are pure action tools whose tool_registry implementation is stable and
# whose MCP subprocess path hangs (browser launch inside subprocess from thread).
# NOTE: agent_thinking and agent_step must NOT be here — they are agentic loop
# control tools that require the full MCP browser_server.py subprocess path.
_HEAVY_TOOLS = {
    "search_media_options", "play_media_selection", "search_spotify",
    "play_spotify_selection", "play_user_playlist", "media_control",
    "system_control", "open_application", "close_application", "run_shell_command",
    "analyze_specific_file", "find_and_analyze_file", "take_screenshot",
    # GUI Pilot — must run directly; returns __vision__ dicts for agentic loop
    "gui_thinking", "get_desktop_state", "get_open_files",
    "gui_step", "read_active_document",
    # RAG Knowledge Base — must run directly to share ChromaDB state
    "index_document", "check_and_index", "query_rag",
    "list_indexed_documents", "delete_from_rag", "index_directory",
    # GitHub integration
    "git_clone", "git_status", "git_diff_summary", "git_smart_commit",
    "git_pull", "fake_commit", "detect_and_install_deps", "run_project",
    "kill_project", "check_project_health", "get_github_profile",
    "list_my_repos", "create_repo_local", "actions_list", "actions_get", "get_job_logs",
}

# The Python executable in the virtual environment
PYTHON_EXE = sys.executable
MCP_SERVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp_servers"))


async def _dispatch_mcp_tool(tool_name: str, arguments: dict, server_file: str) -> str:
    """
    Spawns the MCP server via stdio, connects, calls the tool, and returns the result.
    Has explicit asyncio timeouts to prevent subprocess hangs.
    """
    server_path = os.path.join(MCP_SERVERS_DIR, server_file)
    
    server_params = StdioServerParameters(
        command=PYTHON_EXE,
        args=[server_path],
        env=None
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # 10s timeout for initialization — if the subprocess hangs at startup, fail fast
                await asyncio.wait_for(session.initialize(), timeout=10.0)
                
                # 60s timeout for the actual tool call
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=60.0
                )
                
                if result.content:
                    text_outputs = [item.text for item in result.content if item.type == 'text']
                    return "\n".join(text_outputs)
                return "Task completed via MCP."
    except asyncio.TimeoutError:
        logger.warning(f"[MCP] Timeout waiting for {server_file}/{tool_name}. Will fall back.")
        raise
    except Exception as e:
        logger.error(f"MCP server {server_file} failed for tool {tool_name}: {e}")
        raise e


def dispatch_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Main entry point for tool routing.
    
    Strategy:
      - If no running event loop → asyncio.run(MCP subprocess) directly (safe).
      - If inside a running event loop (LangGraph / generate_response_stream):
          * Heavy tools (browser, media, system) → tool_registry directly.
            These tools do heavy I/O that's unreliable in a stdio subprocess
            spawned from a thread pool inside an async context.
          * Light stateless tools (telegram, email status) → ThreadPoolExecutor
            with its own event loop, so the MCP subprocess call is isolated.
      - Falls back to tool_registry on any exception.
    """
    if tool_name == "browse_and_extract_tool":
        tool_name = "browse_and_extract"

    server_file = TOOL_TO_SERVER.get(tool_name)
    
    if not server_file:
        logger.debug(f"Tool '{tool_name}' not mapped to an MCP server. Using direct fallback.")
        return fallback_execute_tool_call(tool_name, arguments)

    # ── HEAVY TOOLS BYPASS ───────────────────────────────────────────────────
    # Tools like RAG, GitHub, and GUI Pilot do heavy I/O and state management
    # that is unreliable in stdio subprocesses. We always run them directly
    # via the registry, bypassing the MCP subprocess overhead completely.
    if tool_name in _HEAVY_TOOLS:
        logger.info(f"[MCP] Heavy tool '{tool_name}' — bypassing subprocess, executing directly.")
        return fallback_execute_tool_call(tool_name, arguments)

    try:
        logger.info(f"Routing '{tool_name}' to MCP Server: {server_file}")
        
        try:
            asyncio.get_running_loop()
            # We are inside an async context (generate_response_stream or LangGraph loop).
            
            # Light stateless tools (telegram, email): safe to spawn via thread pool.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    _dispatch_mcp_tool(tool_name, arguments, server_file)
                )
                return future.result(timeout=75)

        except RuntimeError:
            # No running event loop — safe to use asyncio.run directly for light tools.
            return asyncio.run(_dispatch_mcp_tool(tool_name, arguments, server_file))
            
    except Exception as e:
        logger.warning(f"MCP execution failed for '{tool_name}': {e}. Falling back to direct execution.")
        return fallback_execute_tool_call(tool_name, arguments)
