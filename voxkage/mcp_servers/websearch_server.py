"""
MCP Server: VoxKage WebSearch
Headless, fast DuckDuckGo search and article text/markdown fetching.
Bypasses the heavy Playwright Chromium browser spin-up for simple textual lookups.

Requirements:
    pip install duckduckgo_search trafilatura aiohttp

Standalone — run directly:
    python mcp_servers/websearch_server.py
"""

import os
import sys
import time
import random
import asyncio
from typing import List, Dict, Any

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-websearch")

# ── Optional Imports & Graceful Degradation ───────────────────────────────────
try:
    from ddgs import DDGS
    HAS_DDG = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        HAS_DDG = True
    except ImportError:
        HAS_DDG = False

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


# ── Configuration & Constants ──────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
]


# ── In-Memory TTL Cache ────────────────────────────────────────────────────────
class TTLCache:
    def __init__(self, ttl_seconds: float = 900):
        self.ttl = ttl_seconds
        self.cache = {}

    def get(self, key):
        if key in self.cache:
            val, expire = self.cache[key]
            if time.time() < expire:
                return val
            else:
                del self.cache[key]
        return None

    def set(self, key, value):
        self.cache[key] = (value, time.time() + self.ttl)


search_cache = TTLCache(ttl_seconds=900)  # 15-minute TTL
fetch_cache = TTLCache(ttl_seconds=900)   # 15-minute TTL


# ═══════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def web_search(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Search the web using DuckDuckGo (headless, fast, no browser window).
    Use this as the PRIMARY tool for simple text queries, general knowledge,
    or looking up quick information.

    Parameters:
      query       : The search query string.
      max_results : Maximum results to return (default 10).
    """
    if not HAS_DDG:
        return [{"error": "ddgs library not installed. Please run: pip install ddgs"}]

    cached = search_cache.get((query, max_results))
    if cached is not None:
        return cached

    # Run DuckDuckGo search in a separate thread to avoid blocking the event loop
    def _run_ddg():
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    try:
        results = await asyncio.to_thread(_run_ddg)
        parsed = []
        for r in results:
            parsed.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")
            })
        search_cache.set((query, max_results), parsed)
        return parsed
    except Exception as e:
        return [{"error": f"Failed to execute DuckDuckGo search: {e}"}]


@mcp.tool()
async def web_search_parallel(queries: List[str], max_results: int = 5) -> List[List[Dict[str, Any]]]:
    """
    Execute multiple searches concurrently. Excellent for multi-faceted research tasks.

    Parameters:
      queries     : A list of search query strings.
      max_results : Maximum results per query (default 5).
    """
    tasks = [web_search(q, max_results) for q in queries]
    return list(await asyncio.gather(*tasks))


@mcp.tool()
async def web_fetch(url: str) -> Dict[str, Any]:
    """
    Fetch a URL and extract its main article text/markdown (headless, fast, no browser window).
    Caps response content size to ~50KB to prevent context window bloat.

    Parameters:
      url : The absolute URL to fetch.
    """
    if not HAS_AIOHTTP or not HAS_TRAFILATURA:
        return {
            "url": url,
            "error": "aiohttp or trafilatura is not installed. Please run: pip install aiohttp trafilatura"
        }

    cached = fetch_cache.get(url)
    if cached is not None:
        return cached

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.google.com/"
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    return {
                        "url": url,
                        "error": f"HTTP status error: {response.status} {response.reason}"
                    }

                html_content = await response.text(errors='replace')

                # Run trafilatura extraction in thread pool to not block event loop
                def _extract():
                    return trafilatura.extract(
                        html_content,
                        include_links=True,
                        include_images=False,
                        include_tables=True,
                        output_format="markdown"
                    )

                content = await asyncio.to_thread(_extract)

                # Fallback to simple parser if trafilatura failed or returned empty
                if not content:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html_content, 'html.parser')
                        for element in soup(["script", "style", "nav", "footer", "header"]):
                            element.decompose()
                        raw_text = soup.get_text(separator="\n")
                        lines = [line.strip() for line in raw_text.splitlines()]
                        content = "\n".join([line for line in lines if line])
                    except Exception:
                        content = ""

                if not content:
                    return {
                        "url": url,
                        "error": "Could not extract clean text content from the page."
                    }

                # Cap length to ~50KB to avoid token bloat (approx 30,000 characters)
                if len(content) > 30000:
                    content = content[:30000] + "\n\n...[Content truncated due to size limits]..."

                result = {
                    "url": url,
                    "status": response.status,
                    "content": content
                }

                fetch_cache.set(url, result)
                return result

    except asyncio.TimeoutError:
        return {"url": url, "error": "Request timed out (15s limit)"}
    except Exception as e:
        return {"url": url, "error": f"Fetch failed: {str(e)}"}


@mcp.tool()
async def web_fetch_parallel(urls: List[str]) -> List[Dict[str, Any]]:
    """
    Fetch multiple URLs concurrently. Useful to scrape multiple reference pages simultaneously.

    Parameters:
      urls : List of absolute URLs to fetch.
    """
    tasks = [web_fetch(url) for url in urls]
    return list(await asyncio.gather(*tasks))


@mcp.tool()
async def web_search_deep(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Deep search: searches DuckDuckGo, then concurrently fetches and extracts clean text
    content from the top results. Returns search snippets alongside full extracted text.

    Parameters:
      query       : The search query string.
      max_results : Number of top search results to fetch (default 3, max 5).
    """
    max_results = min(max_results, 5)

    # 1. Run Search
    results = await web_search(query, max_results=max_results)
    if not results or "error" in results[0]:
        return results

    # 2. Get URLs
    urls = [r["url"] for r in results if r.get("url")]
    if not urls:
        return results

    # 3. Concurrently fetch page content
    fetch_results = await web_fetch_parallel(urls)

    # 4. Merge results
    combined = []
    for r in results:
        url = r.get("url")
        matched = next((f for f in fetch_results if f.get("url") == url), None)

        entry = {
            "title": r.get("title", ""),
            "url": url,
            "snippet": r.get("snippet", ""),
        }

        if matched:
            if "error" in matched:
                entry["fetch_error"] = matched["error"]
            else:
                entry["content"] = matched.get("content", "")
        else:
            entry["fetch_error"] = "No content retrieved"

        combined.append(entry)

    return combined


if __name__ == "__main__":
    mcp.run()
