"""
MCP Server: Download Manager

Tools:
  download_file         — Download any file from a URL (streaming, with confirmation gate)
  download_images       — Search for images using browser DOM extraction and download them
  run_installer         — Execute a downloaded .exe/.msi installer (hard-stop gate)
  get_download_status   — Check progress of active downloads

All destructive/download operations use the HARD STOP confirmation gate.
Run standalone: python mcp_servers/download_server.py
"""

import os
import sys
import re
import time
import hashlib
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urljoin

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-download")

# ── Active download tracking ──────────────────────────────────────────────────
_active_downloads: dict = {}   # filename -> {progress, total, status, path}
_dl_lock = threading.Lock()


# ── Directory resolver ────────────────────────────────────────────────────────

def _resolve_dir(description: str) -> str | None:
    if os.path.isdir(description):
        return os.path.abspath(description)
    user_home = os.path.expanduser("~")
    for name in ("Downloads", "Desktop", "Documents", "Pictures", "Videos", "Music"):
        path = os.path.join(user_home, name)
        if name.lower() in description.lower() and os.path.isdir(path):
            return path
    for root in (user_home, "C:\\", "C:\\Ayush files"):
        try:
            for entry in os.scandir(root):
                if entry.is_dir() and entry.name.lower() in description.lower():
                    return entry.path
        except Exception:
            pass
    return None


def _fmt_size(n_bytes: int) -> str:
    if n_bytes >= 1024 ** 3:
        return f"{n_bytes / 1024**3:.1f}GB"
    if n_bytes >= 1024 ** 2:
        return f"{n_bytes / 1024**2:.1f}MB"
    if n_bytes >= 1024:
        return f"{n_bytes / 1024:.1f}KB"
    return f"{n_bytes}B"


def _sha256(path: str, max_bytes: int = 4 * 1024 * 1024) -> str:
    """Quick partial SHA256 (first 4MB) for installer verification preview."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(max_bytes))
    except Exception:
        return "unavailable"
    return h.hexdigest()[:16] + "..."


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def download_file(
    url: str,
    save_directory: str = "Downloads",
    filename: str = "",
    confirmed: bool = False,
) -> str:
    """
    Download any file from a URL and save it to a local folder.

    Supports: .exe, .msi, .zip, .tar.gz, .pdf, .jpg, .png, datasets, any file type.

    HARD STOP CONFIRMATION GATE:
      1. Call with confirmed=False -> shows URL, source domain, file name, estimated size.
         Show this preview to the user and ask "Agreed?"
      2. END YOUR TURN. Wait for user's next message.
      3. Only call with confirmed=True after user explicitly agrees.

    Parameters:
      url            : Direct download URL (must be a direct file link, not a webpage)
      save_directory : Where to save — natural language ("Downloads", "Desktop") or absolute path
      filename       : Override the filename. Leave empty to infer from URL.
      confirmed      : False = preview only. True = start download.
    """
    import requests

    save_dir = _resolve_dir(save_directory)
    if not save_dir:
        if os.path.isabs(save_directory):
            os.makedirs(save_directory, exist_ok=True)
            save_dir = save_directory
        else:
            return f"Save directory not found: '{save_directory}'. Please provide an absolute path."

    if not filename:
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or "downloaded_file"
        filename = re.sub(r"[?&=].*", "", filename) or "downloaded_file"

    save_path = os.path.join(save_dir, filename)
    domain = urlparse(url).netloc or url[:40]

    if not confirmed:
        size_str = "Unknown size"
        ctype = ""
        try:
            head = requests.head(url, timeout=8, allow_redirects=True)
            cl = head.headers.get("Content-Length", "")
            if cl and cl.isdigit():
                size_str = _fmt_size(int(cl))
            ctype = head.headers.get("Content-Type", "")
        except Exception:
            pass

        return (
            f"[CONFIRM] Download file:\n"
            f"  Source  : {domain}\n"
            f"  File    : {filename}\n"
            f"  Size    : {size_str}\n"
            f"  Type    : {ctype or 'unknown'}\n"
            f"  Save to : {save_path}\n\n"
            f"Agreed?"
        )

    # confirmed=True: start the actual download in a BACKGROUND THREAD.
    # The MCP tool returns immediately so the server stays responsive.
    # The user can call get_download_status() any time to check progress.
    def _do_download():
        try:
            with _dl_lock:
                _active_downloads[filename] = {
                    "progress": 0, "total": 0,
                    "status": "downloading", "path": save_path
                }
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            with requests.get(url, stream=True, timeout=30, headers=headers, allow_redirects=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                downloaded = 0
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            with _dl_lock:
                                _active_downloads[filename]["progress"] = downloaded
                                _active_downloads[filename]["total"] = total
            with _dl_lock:
                _active_downloads[filename]["status"] = "done"
            # Fire Windows toast notification when download completes
            try:
                from mcp_servers.notify_server import _toast
                ext = os.path.splitext(filename)[1].lower()
                is_exec = ext in (".exe", ".msi", ".pkg", ".deb", ".rpm", ".appimage")
                final_size = os.path.getsize(save_path)
                hint = " Ready to install." if is_exec else ""
                _toast(
                    "VoxKage — Download Complete",
                    f"{filename} ({_fmt_size(final_size)}) saved to {save_dir}.{hint}",
                    duration="long",
                )
            except Exception:
                pass  # Notification is a best-effort bonus
        except Exception as e:
            with _dl_lock:
                _active_downloads[filename]["status"] = f"error: {e}"

    t = threading.Thread(target=_do_download, name=f"dl_{filename}", daemon=True)
    t.start()

    ext = os.path.splitext(filename)[1].lower()
    is_executable = ext in (".exe", ".msi", ".pkg", ".deb", ".rpm", ".appimage")
    install_hint = (
        f" When done, run: run_installer(file_path='{save_path}')."
        if is_executable else ""
    )
    return (
        f"DOWNLOADING: '{filename}' started in the background, sir.\n"
        f"  Saving to : {save_path}\n"
        f"  Source    : {domain}\n\n"
        f"Call get_download_status() to monitor progress.{install_hint}\n"
        f"You will receive a Windows notification when it completes."
    )


@mcp.tool()
def download_images(
    query: str,
    count: int = 3,
    aspect_ratio: str = "landscape",
    resolution: str = "high",
    source: str = "unsplash",
    save_directory: str = "Downloads",
    confirmed: bool = False,
) -> str:
    """
    Search for images and download them using the browser's live DOM evaluation.

    This uses a 5-step browser-native flow (no raw HTML regex, no shell scripts):
      1. agent_step(goto)            -> navigate to image source URL
      2. agent_step(wait 3.5s)       -> let React/JS render initial images
      3. agent_step(scroll x2)       -> trigger lazy-loading of more images
      4. agent_step(extract_image_urls) -> JS page.evaluate() on live DOM:
           reads img[src], img[srcset], img[data-src], source[srcset],
           CSS background-image, and data-* lazy-loader attributes
      5. Filter CDN URLs, skip thumbnails < 10KB, download with proper headers

    Auto-fallback: if primary source yields < count images, tries pexels/pixabay.

    CLARIFICATION GATE: if user did not specify count/source/save location, ask first.
    HARD STOP: confirmed=False -> show plan -> END TURN -> confirmed=True.

    Parameters:
      query          : image description (e.g. "dark night sky stars")
      count          : number of images to download (1-10, default 3)
      aspect_ratio   : "landscape", "portrait", or "square"
      resolution     : "high" or "medium"
      source         : "unsplash" (default), "pexels", or "pixabay"
      save_directory : natural language ("Downloads") or absolute path
      confirmed      : False = show plan only. True = execute download.
    """
    import requests
    import json as _json

    count = max(1, min(count, 10))
    save_dir = _resolve_dir(save_directory)
    if not save_dir:
        return f"Save directory not found: '{save_directory}'."

    SOURCE_URLS = {
        "unsplash": "https://unsplash.com/s/photos/{q}",
        "pexels":   "https://www.pexels.com/search/{q}/",
        "pixabay":  "https://pixabay.com/images/search/{q}/",
    }
    src = source.lower().strip()
    if src not in SOURCE_URLS:
        src = "unsplash"
    search_url = SOURCE_URLS[src].format(q=query.replace(" ", "-"))

    if not confirmed:
        return (
            f"[CONFIRM] Image download plan:\n"
            f"  Query       : \"{query}\"\n"
            f"  Count       : {count} images\n"
            f"  Aspect ratio: {aspect_ratio}\n"
            f"  Resolution  : {resolution}\n"
            f"  Source      : {src} ({search_url})\n"
            f"  Save to     : {save_dir}\n\n"
            f"Steps I will take:\n"
            f"  1. Open {src} in the browser\n"
            f"  2. Wait for JavaScript to render images (3.5s)\n"
            f"  3. Scroll twice to trigger lazy-loading\n"
            f"  4. Extract all image URLs from live DOM (not raw HTML)\n"
            f"  5. Download {count} best matching high-res images\n\n"
            f"Agreed?"
        )

    # Import browser agent
    try:
        from automation.web_agent import agent_step_sync
    except Exception as e:
        return f"Browser not available: {e}"

    # CDN hostname hints for quality ranking
    CDN_HINTS = {
        "unsplash": ["images.unsplash.com"],
        "pexels":   ["images.pexels.com"],
        "pixabay":  ["cdn.pixabay.com"],
    }
    # Markers that indicate tiny/thumbnail images to skip
    SKIP_MARKERS = [
        "w=10&", "w=20&", "w=30&", "w=50&", "w=100&", "w=200&",
        "&h=10", "&h=20", "&h=50",
        "_thumb", "_tiny", "_small", "micro", "nano",
        "30x30", "40x40", "50x50", "60x60", "80x80", "100x100",
        "avatar", "profile", "/logo", "/icon", "favicon",
    ]

    def _is_hq(url: str) -> bool:
        low = url.lower()
        return not any(m in low for m in SKIP_MARKERS)

    def _scrape_source(source_name: str, url: str) -> list:
        """Navigate browser, render JS, scroll for lazy-load, extract image URLs from live DOM.
        Hard wall-clock cap of 30s prevents this from blocking the MCP server on slow pages.
        """
        import time as _t
        _scrape_start = _t.monotonic()
        _SCRAPE_LIMIT = 30  # seconds hard cap

        def _step(args: dict):
            """Run an agent_step only if wall-clock budget remains."""
            if _t.monotonic() - _scrape_start > _SCRAPE_LIMIT:
                return None
            return agent_step_sync(args)

        # Step 1: Navigate
        _step({"action": "goto", "goal": f"Open {source_name} search page for '{query}'", "url": url})
        # Step 2: Wait for JavaScript/React to render images
        _step({"action": "wait", "goal": "Wait for JS to render images", "ms": 3500})
        # Step 3: Scroll to trigger lazy-loading
        _step({"action": "scroll", "goal": "Scroll to load lazy images (batch 1)", "direction": "down"})
        _step({"action": "wait", "goal": "Wait after first scroll", "ms": 2000})
        _step({"action": "scroll", "goal": "Scroll to load lazy images (batch 2)", "direction": "down"})
        _step({"action": "wait", "goal": "Wait after second scroll", "ms": 1500})
        # Step 4: Extract all image URLs from live rendered DOM
        result = _step({"action": "extract_image_urls", "goal": f"Extract all image URLs from {source_name} live DOM for query: {query}"})
        if result is None:
            return []  # Timed out before extraction
        # Parse: prefer structured field, fall back to text JSON block
        img_urls = []
        if isinstance(result, dict):
            img_urls = result.get("image_urls", [])
            if not img_urls:
                text = result.get("text", "")
                start = text.find("--- IMAGE URLS (JSON) ---")
                end = text.find("--- END IMAGE URLS ---")
                if start != -1 and end != -1:
                    json_str = text[start + len("--- IMAGE URLS (JSON) ---"):end].strip()
                    try:
                        img_urls = _json.loads(json_str)
                    except Exception:
                        img_urls = []
        return [u for u in img_urls if isinstance(u, str) and u.startswith("http")]

    def _rank(urls: list, cdn_hints: list) -> list:
        """Rank: CDN high-quality first, then other high-quality, then rest."""
        cdn = [u for u in urls if any(h in u for h in cdn_hints) and _is_hq(u)]
        other = [u for u in urls if u not in cdn and _is_hq(u)]
        low = [u for u in urls if u not in cdn and u not in other]
        return cdn + other + low

    # Scrape primary source
    raw = _scrape_source(src, search_url)
    ranked = _rank(raw, CDN_HINTS.get(src, []))

    # Auto-fallback if not enough results
    if len(ranked) < count:
        fallback_src = "pexels" if src != "pexels" else "pixabay"
        fb_url = SOURCE_URLS[fallback_src].format(q=query.replace(" ", "-"))
        fb_raw = _scrape_source(fallback_src, fb_url)
        fb_ranked = _rank(fb_raw, CDN_HINTS.get(fallback_src, []))
        ranked = ranked + fb_ranked

    # Deduplicate preserving rank order
    seen: set = set()
    unique: list = []
    for u in ranked:
        base = u.split("?")[0]
        if base not in seen:
            seen.add(base)
            unique.append(u)

    if not unique:
        return (
            f"Could not extract any image URLs from the DOM for '{query}'.\n"
            f"The page may be using bot detection or canvas-based rendering.\n\n"
            f"Manual fallback: Use agent_step(action='goto', url='{search_url}') then\n"
            f"agent_step(action='take_screenshot') to visually inspect the page.\n"
            f"Then call download_file(url=<image_url>) with a specific image URL."
        )

    # Download images
    downloaded = []
    errors = []
    safe_query = re.sub(r"[^a-z0-9_]", "_", query.lower())[:30]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
        "Referer": search_url,
    }

    # Map content-type → extension (used to detect when server sends AVIF under a .jpg URL)
    _CTYPE_EXT = {
        "image/jpeg": ".jpg",
        "image/jpg":  ".jpg",
        "image/png":  ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/gif":  ".gif",
    }

    for i, img_url in enumerate(unique[:count]):
        # Force JPEG from CDN URLs that support format negotiation
        fetch_url = img_url
        if "images.unsplash.com" in img_url or "images.pexels.com" in img_url:
            # Strip existing format params and request JPEG explicitly
            base_url = img_url.split("?")[0]
            fetch_url = base_url + "?auto=format&fm=jpg&fit=crop&q=85&w=1920"
        elif "cdn.pixabay.com" in img_url:
            fetch_url = img_url  # Pixabay CDN already serves proper JPEGs

        try:
            r = requests.get(fetch_url, timeout=25, headers=headers, allow_redirects=True)
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower().split(";")[0].strip()
            if "image" not in ctype and "octet" not in ctype:
                errors.append(f"  \u2717 #{i+1}: not an image (content-type: {ctype})")
                continue
            if len(r.content) < 10_000:
                errors.append(f"  \u2717 #{i+1}: too small ({len(r.content)}B) \u2014 thumbnail, skipping")
                continue

            # Determine extension from ACTUAL content-type (not URL — URLs lie)
            real_ext = _CTYPE_EXT.get(ctype, None)
            if real_ext is None:
                # Fall back to URL extension only when content-type is vague
                ext_m = re.search(r"\.(jpg|jpeg|png|webp)", img_url.lower())
                real_ext = "." + ext_m.group(1) if ext_m else ".jpg"
            if real_ext == ".avif":
                # AVIF not compatible with most viewers — skip and try next URL
                errors.append(f"  \u2717 #{i+1}: skipped AVIF (not desktop-compatible, will try next source)")
                continue

            fname = f"{safe_query}_{i+1:02d}{real_ext}"
            save_path = os.path.join(save_dir, fname)
            with open(save_path, "wb") as f:
                f.write(r.content)
            downloaded.append(f"  \u2713 {fname} ({_fmt_size(len(r.content))}) \u2192 {save_path}")
        except Exception as e:
            errors.append(f"  \u2717 #{i+1}: {e}")

    lines = [f"Image download complete: {len(downloaded)}/{count} saved\n"]
    lines.extend(downloaded)
    if errors:
        lines.append("\nFailed/Skipped:")
        lines.extend(errors)
        if len(downloaded) < count:
            remaining = count - len(downloaded)
            lines.append(f"\nTo get {remaining} more: try download_images(source='pixabay', count={remaining})")
    lines.append(f"\nAll saved to: {save_dir}")
    return "\n".join(lines)


@mcp.tool()
def run_installer(
    file_path: str,
    silent: bool = False,
    confirmed: bool = False,
) -> str:
    """
    Run a downloaded installer (.exe or .msi) on Windows.

    MANDATORY HARD STOP CONFIRMATION GATE:
      This tool ALWAYS requires explicit user confirmation before executing.
      1. Call with confirmed=False -> shows file info, SHA256 hash, and a security warning
      2. END YOUR TURN. Ask the user: "Are you sure you want to run this installer?"
      3. Only call with confirmed=True if the user explicitly says yes.

    Parameters:
      file_path : absolute path to the .exe or .msi file
      silent    : True = run with /S (silent) flag — no installer UI shown
                  False = run normally (installer UI appears)
      confirmed : False = security preview. True = execute installer.
    """
    if not os.path.isfile(file_path):
        return f"File not found: '{file_path}'"

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".exe", ".msi"):
        return f"Only .exe and .msi files can be run as installers. Got: {ext}"

    file_size = os.path.getsize(file_path)
    sha_preview = _sha256(file_path)

    if not confirmed:
        return (
            f"[SECURITY CONFIRM] Run installer:\n"
            f"  File    : {os.path.basename(file_path)}\n"
            f"  Path    : {file_path}\n"
            f"  Size    : {_fmt_size(file_size)}\n"
            f"  SHA256  : {sha_preview} (first 4MB)\n"
            f"  Mode    : {'Silent (no UI)' if silent else 'Normal (UI shown)'}\n\n"
            f"  \u26a0\ufe0f  WARNING: Only run installers from trusted, official sources.\n"
            f"  \u26a0\ufe0f  Make sure this matches what you intended to download.\n\n"
            f"Are you sure you want to run this installer? (yes/no)"
        )

    try:
        if ext == ".msi":
            cmd = ["msiexec", "/i", file_path]
            if silent:
                cmd += ["/quiet", "/norestart"]
        else:
            cmd = [file_path]
            if silent:
                cmd.append("/S")

        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        mode_str = "silently" if silent else "with installer UI"
        return (
            f"\u2713 Installer launched {mode_str}.\n"
            f"  File : {os.path.basename(file_path)}\n"
            f"  PID  : {proc.pid}\n\n"
            f"The installer is running in the background. "
            f"{'Check your taskbar for the installer window.' if not silent else 'It will install silently.'}"
        )
    except Exception as e:
        return f"Failed to launch installer: {e}"


@mcp.tool()
def get_download_status() -> str:
    """
    Returns the status of all currently active or recently completed file downloads.
    Use this to check if a download started via download_file() is still in progress.
    """
    with _dl_lock:
        if not _active_downloads:
            return "No active or recent downloads."
        lines = [f"Download Status ({len(_active_downloads)} tracked):\n"]
        for fname, info in _active_downloads.items():
            status = info.get("status", "unknown")
            progress = info.get("progress", 0)
            total = info.get("total", 0)
            path = info.get("path", "?")
            if total > 0:
                pct = progress * 100 // total
                bar = "[" + "#" * (pct // 10) + "-" * (10 - pct // 10) + "]"
                sz_str = f"{_fmt_size(progress)} / {_fmt_size(total)} ({pct}%)"
            else:
                bar = ""
                sz_str = _fmt_size(progress) if progress else "starting..."
            lines.append(f"  {fname}")
            lines.append(f"    Status: {status}  {bar}  {sz_str}")
            lines.append(f"    Path  : {path}")
        return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
