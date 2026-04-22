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
}

# The Python executable in the virtual environment
PYTHON_EXE = sys.executable
MCP_SERVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp_servers"))


async def _dispatch_mcp_tool(tool_name: str, arguments: dict, server_file: str) -> str:
    """
    Spawns the MCP server via stdio, connects, calls the tool, and returns the result.
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
                await session.initialize()
                
                # Check if the tool exists on the server (optional, but good for safety)
                # Call the tool
                result = await session.call_tool(tool_name, arguments)
                
                # The result is a CallToolResult which has 'content' (a list of TextContent/ImageContent)
                if result.content:
                    # Extract text content
                    text_outputs = [item.text for item in result.content if item.type == 'text']
                    return "\n".join(text_outputs)
                return "Task completed via MCP."
    except Exception as e:
        logger.error(f"MCP server {server_file} failed for tool {tool_name}: {e}")
        raise e


def dispatch_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Main entry point for tool routing.
    Tries to route to the appropriate MCP server. If it fails or is not mapped,
    falls back to the monolithic tool_registry execution.
    """
    if tool_name == "browse_and_extract_tool":
         tool_name = "browse_and_extract"

    server_file = TOOL_TO_SERVER.get(tool_name)
    
    if not server_file:
        logger.debug(f"Tool '{tool_name}' not mapped to an MCP server. Using direct fallback.")
        return fallback_execute_tool_call(tool_name, arguments)
        
    try:
        # Run the async MCP call synchronously
        logger.info(f"Routing '{tool_name}' to MCP Server: {server_file}")
        
        # If there's already an active event loop (e.g. from Discord bot or other async context),
        # asyncio.run will fail. We must handle that.
        try:
            loop = asyncio.get_running_loop()
            # If we are in an async loop, we shouldn't be calling this synchronous dispatch if we can help it,
            # but if we must, we'd need nest_asyncio or fallback. Let's just fallback for safety.
            logger.warning("Event loop is already running! Falling back to direct execution to avoid blocking.")
            return fallback_execute_tool_call(tool_name, arguments)
        except RuntimeError:
            # No running event loop, safe to use asyncio.run
            return asyncio.run(_dispatch_mcp_tool(tool_name, arguments, server_file))
            
    except Exception as e:
        logger.warning(f"MCP execution failed for '{tool_name}': {e}. Falling back to direct execution.")
        return fallback_execute_tool_call(tool_name, arguments)
