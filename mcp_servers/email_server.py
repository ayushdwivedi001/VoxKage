"""
MCP Server: Gmail
"""

from mcp.server.fastmcp import FastMCP
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from automation.gmail_manager import check_gmail, get_email_summary

mcp = FastMCP("voxkage-email")

@mcp.tool()
def check_email(query: str = "", label: str = "INBOX", max_results: int = 5) -> str:
    """Checks the user's Gmail inbox for recent emails."""
    return check_gmail(query=query, label=label, max_results=max_results)

@mcp.tool()
def email_summary(email_id: str) -> str:
    """Gets the full text body of a specific email ID."""
    return get_email_summary(email_id)

if __name__ == "__main__":
    mcp.run()
