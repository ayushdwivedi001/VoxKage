"""
MCP Server: File Analysis & Screenshot
"""

from mcp.server.fastmcp import FastMCP
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from automation.document_parser import analyze_specific_file_sync, find_file
from automation.screenshot import take_screenshot as do_take_screenshot

mcp = FastMCP("voxkage-file")

@mcp.tool()
def analyze_specific_file(file_path: str, query: str) -> str:
    """Reads the text of a specific local file."""
    return analyze_specific_file_sync(file_path, query)

@mcp.tool()
def find_and_analyze_file(filename_keyword: str, query: str) -> str:
    """Searches for a local file by keyword, reads it, and answers questions."""
    file_path = find_file(filename_keyword)
    if file_path:
        return f"Found file at {file_path}. Content:\n" + analyze_specific_file_sync(file_path, query)
    return f"Could not find any file matching '{filename_keyword}' in Documents, Downloads, or Desktop."

@mcp.tool()
def take_screenshot() -> str:
    """Takes a local screenshot of what the user is currently seeing."""
    filepath = do_take_screenshot()
    if filepath:
        return f"Successfully took a screenshot and saved it to: {filepath}"
    return "Failed to take screenshot."

if __name__ == "__main__":
    mcp.run()
