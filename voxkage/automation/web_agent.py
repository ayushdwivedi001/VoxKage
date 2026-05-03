import queue
import threading
from playwright.sync_api import sync_playwright
import urllib.parse
import os
import sys
import logging
import atexit
import base64

logger = logging.getLogger(__name__)

# Global instances and Queues
_pw_queue = queue.Queue()
_pw_thread = None
_playwright = None
_context = None
_active_page = None

# Global state to remember the last searched videos resilient to LLM memory wipes
GLOBAL_VIDEO_OPTIONS = []

# === PHASE 2: DIRECT-SITE SEARCH URL TEMPLATES ===
# Used by the search_on_site step to bypass DuckDuckGo for known domains
DIRECT_SEARCH_URLS = {
    "amazon.in":              "https://www.amazon.in/s?k={q}",
    "amazon.com":             "https://www.amazon.com/s?k={q}",
    "flipkart.com":           "https://www.flipkart.com/search?q={q}",
    "myntra.com":             "https://www.myntra.com/{q}",
    "linkedin.com":           "https://www.linkedin.com/jobs/search/?keywords={q}",
    "github.com":             "https://github.com/search?q={q}&type=repositories",
    "youtube.com":            "https://www.youtube.com/results?search_query={q}",
    "reddit.com":             "https://www.reddit.com/search/?q={q}",
    "wikipedia.org":          "https://en.wikipedia.org/w/index.php?search={q}",
    "dominos.co.in":          "https://www.dominos.co.in/menu?q={q}",
    "dominos.com":            "https://www.dominos.com/search?q={q}",
    "zomato.com":             "https://www.zomato.com/search?q={q}",
    "swiggy.com":             "https://www.swiggy.com/search?q={q}",
    # Steam
    "steam.com":              "https://store.steampowered.com/search/?term={q}",
    "steampowered.com":       "https://store.steampowered.com/search/?term={q}",
    "store.steampowered.com": "https://store.steampowered.com/search/?term={q}",
    "steamstore.com":         "https://store.steampowered.com/search/?term={q}",
    "steamdb.info":           "https://steamdb.info/search/?a=app&q={q}",
    # Epic Games Store
    "epicgames.com":          "https://store.epicgames.com/en-US/browse?q={q}&sortBy=relevancy&sortDir=DESC&count=40",
    "store.epicgames.com":    "https://store.epicgames.com/en-US/browse?q={q}&sortBy=relevancy&sortDir=DESC&count=40",
    "epic.games":             "https://store.epicgames.com/en-US/browse?q={q}&sortBy=relevancy&sortDir=DESC&count=40",
    # Other game stores
    "gog.com":                "https://www.gog.com/games?query={q}",
    "humblebundle.com":       "https://www.humblebundle.com/store/search?search={q}",
    "howlongtobeat.com":      "https://howlongtobeat.com/?q={q}",
    # Search engines — never append site: to themselves
    "duckduckgo.com":         "https://duckduckgo.com/?q={q}",
    "www.duckduckgo.com":     "https://duckduckgo.com/?q={q}",
    "google.com":             "https://www.google.com/search?q={q}",
    "www.google.com":         "https://www.google.com/search?q={q}",
    "bing.com":               "https://www.bing.com/search?q={q}",
}

def _encode_screenshot(path: str) -> str:
    """Encode a screenshot file as base64 for multimodal LLM consumption."""
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""

def _cleanup():
    # Send quit signal to worker
    try:
        if _pw_thread and _pw_thread.is_alive():
            _pw_queue.put(("QUIT", None, None))
            _pw_thread.join(timeout=5)
    except:
        pass

atexit.register(_cleanup)

# Set path for packaged app
if getattr(sys, 'frozen', False):
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(sys.executable)
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(base_path, 'playwright', 'driver', 'package', '.local-browsers')

# === PHASE 28e: LAST SEARCH MEMORY ===
import json as _json

# Path for persistent search memory (cleared on VoxKage full restart)
_LAST_SEARCH_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "last_search.json")

def save_last_search(items):
    """Save search results to persistent memory file."""
    try:
        data = {"items": items, "timestamp": __import__('time').time()}
        with open(_LAST_SEARCH_PATH, 'w', encoding='utf-8') as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[28e] Saved {len(items)} items to last_search.json")
    except Exception as e:
        logger.error(f"[28e] Failed to save last_search.json: {e}")

def load_last_search():
    """Load previous search results from memory file."""
    try:
        if os.path.exists(_LAST_SEARCH_PATH):
            with open(_LAST_SEARCH_PATH, 'r', encoding='utf-8') as f:
                data = _json.load(f)
            # Handle both old dict format and new list format (from clear_session_memory wipe)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("items", [])
    except Exception as e:
        logger.error(f"[28e] Failed to load last_search.json: {e}")
    return []

def clear_last_search():
    """Clear search memory (called on VoxKage startup)."""
    try:
        if os.path.exists(_LAST_SEARCH_PATH):
            os.remove(_LAST_SEARCH_PATH)
            logger.info("[28e] Cleared last_search.json on startup")
    except:
        pass

def clear_session_screenshots():
    """=== PHASE 3: CLEANUP SCREENSHOTS ON STARTUP ===
    Remove all agent_step screenshots from C:\\VoxKage\\Brain so each session starts fresh.
    """
    import glob
    try:
        ss_dir = r"C:\VoxKage\Brain"
        if os.path.exists(ss_dir):
            # Remove all agent_step_*.jpg screenshots
            for f in glob.glob(os.path.join(ss_dir, "agent_step_*.jpg")):
                os.remove(f)
            # Also remove the latest browser state screenshot
            latest_ss = os.path.join(ss_dir, "latest_browser_state.jpg")
            if os.path.exists(latest_ss):
                os.remove(latest_ss)
            logger.info("[Phase3] Cleared session screenshots on startup")
    except Exception as e:
        logger.warning(f"[Phase3] Failed to clear screenshots: {e}")

def _sync_chrome_sessions(voxkage_dir):
    """One-time sync: copy session data from Chrome Default profile to VoxKage."""
    import shutil
    chrome_default = r"C:\Users\AYUSH\AppData\Local\Google\Chrome\User Data\Default"
    voxkage_default = os.path.join(voxkage_dir, "Default")
    
    if not os.path.exists(chrome_default):
        logger.warning("[28e] Chrome Default profile not found. Skipping session sync.")
        return
    
    os.makedirs(voxkage_default, exist_ok=True)
    
    # Copy ONLY session-relevant files (no passwords, no extensions)
    session_files = [
        "Cookies", "Cookies-journal",
        "Local Storage", "Session Storage",
        "Login Data", "Login Data-journal",
        "Web Data", "Web Data-journal",
        "Preferences",
    ]
    
    copied = 0
    for item in session_files:
        src = os.path.join(chrome_default, item)
        dst = os.path.join(voxkage_default, item)
        try:
            if os.path.isdir(src):
                if not os.path.exists(dst):
                    shutil.copytree(src, dst)
                    copied += 1
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
                copied += 1
        except Exception as e:
            logger.debug(f"[28e] Could not copy {item}: {e}")
    
    # Mark as synced
    marker = os.path.join(voxkage_dir, ".session_synced")
    with open(marker, 'w') as f:
        f.write("synced")
    
    logger.info(f"[28e] Session sync complete: copied {copied} items from Chrome to VoxKage")

def _pw_worker():
    global _playwright, _context, _active_page, GLOBAL_VIDEO_OPTIONS
    
    with sync_playwright() as p:
        _playwright = p
        
        def _force_kill_browser_profile_locks(voxkage_home):
            """Kill any orphaned Chrome processes holding our profile, delete lock files."""
            import subprocess
            import glob
            # Delete Chrome singleton lock files that prevent relaunch
            lock_patterns = [
                os.path.join(voxkage_home, "SingletonLock"),
                os.path.join(voxkage_home, "SingletonCookie"),
                os.path.join(voxkage_home, "SingletonSocket"),
                os.path.join(voxkage_home, "lockfile"),
            ]
            for lock_path in lock_patterns:
                if os.path.exists(lock_path):
                    try:
                        os.remove(lock_path)
                        logger.info(f"[28e] Removed stale lock: {lock_path}")
                    except Exception as le:
                        logger.warning(f"[28e] Could not remove lock {lock_path}: {le}")
            # Kill any Chrome instances using our BrowserData path
            try:
                result = subprocess.run(
                    ["wmic", "process", "where",
                     f"CommandLine like '%{voxkage_home}%'",
                     "get", "ProcessId", "/format:value"],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("ProcessId=") and line[10:].strip().isdigit():
                        pid = int(line[10:].strip())
                        try:
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                           capture_output=True, timeout=3)
                            logger.info(f"[28e] Killed stale Chrome process PID={pid}")
                        except Exception:
                            pass
            except Exception as ke:
                logger.debug(f"[28e] Profile process kill failed (non-critical): {ke}")
        # Startup URL — opened immediately after every fresh browser launch.
        # Replaces about:blank so the agent starts on a search engine, not a blank page.
        BROWSER_STARTUP_URL = "https://duckduckgo.com"

        def _ensure_page():
            global _context, _active_page
            VOXKAGE_HOME = r"C:\VoxKage\BrowserData"

            args = [
                "--start-maximized",
                "--ignore-gpu-blocklist",
                "--enable-gpu-rasterization",
                "--enable-zero-copy",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]

            # ── Detect dead context (not just None) ──
            if _context is not None:
                try:
                    _ = _context.pages  # Probe: throws if context is dead
                except Exception:
                    logger.warning("[28e] Dead context detected (probe failed). Resetting.")
                    _context = None
                    _active_page = None

            if _context is None:
                os.makedirs(VOXKAGE_HOME, exist_ok=True)
                sync_marker = os.path.join(VOXKAGE_HOME, ".session_synced")
                if not os.path.exists(sync_marker):
                    _sync_chrome_sessions(VOXKAGE_HOME)
                _force_kill_browser_profile_locks(VOXKAGE_HOME)

                try:
                    logger.info("[28e] Launching VoxKage Chrome browser.")
                    _context = p.chromium.launch_persistent_context(
                        user_data_dir=VOXKAGE_HOME,
                        channel="chrome",
                        headless=False,
                        no_viewport=True,
                        args=args,
                        ignore_default_args=["--enable-automation"],
                        timeout=60000,
                    )
                    logger.info("[28e] VoxKage Chrome launched (C:\\VoxKage\\BrowserData)")
                except Exception as e:
                    logger.error(f"[28e] Failed to launch VoxKage browser: {e}")
                    raise

                # ── Navigate to DuckDuckGo on every fresh launch ──
                # Replaces about:blank. The agent starts with a search engine ready,
                # saving one full goto step at the start of every browser task.
                try:
                    startup_page = _context.pages[0] if _context.pages else _context.new_page()
                    startup_page.goto(BROWSER_STARTUP_URL, wait_until="commit", timeout=15000)
                    logger.info(f"[28e] Startup navigation: {BROWSER_STARTUP_URL}")
                except Exception as nav_err:
                    logger.warning(f"[28e] Startup navigation failed (non-fatal): {nav_err}")

            try:
                pages = _context.pages
                if _active_page is None or _active_page.is_closed():
                    if pages:
                        _active_page = pages[-1]
                    else:
                        _active_page = _context.new_page()
            except Exception:
                logger.warning("Browser context disappeared. Re-initializing natively.")
                _context = None
                return _ensure_page()   # recursive retry

            return _active_page

        while True:
            try:
                task = _pw_queue.get()
                if task is None: break
                action, kwargs, res_q = task
                if action == "QUIT":
                    break
                    
                page = _ensure_page()

                if action == "browse":
                    url   = kwargs.get('url', '')
                    query = kwargs.get('query', '')

                    # ── Only use DDG when the URL is blank OR is literally a search engine ──
                    # IMPORTANT: do NOT check "search" in url — that would route
                    # article URLs like reuters.com/...search... to DDG silently.
                    _search_engine_domains = ("duckduckgo.com", "html.duckduckgo.com", "google.com", "bing.com")
                    use_ddg_search = (
                        not url
                        or any(d in url.lower() for d in _search_engine_domains)
                    )

                    if use_ddg_search:
                        # Use the HTML version of DDG (fastest, most reliable, no JS)
                        encoded_q = urllib.parse.quote_plus(query)
                        search_url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
                        try:
                            page.goto(search_url, wait_until="commit", timeout=20000)
                            page.wait_for_timeout(2000)
                        except Exception as ge:
                            logger.warning(f"[browse] DDG goto issue: {ge}")

                        # Take a screenshot for vision reference
                        screenshot_b64 = ""
                        try:
                            import base64 as _b64
                            ss_path = os.path.join(r"C:\VoxKage\Brain", "search_result.jpg")
                            os.makedirs(os.path.dirname(ss_path), exist_ok=True)
                            page.screenshot(path=ss_path, type="jpeg", quality=60)
                            with open(ss_path, "rb") as f:
                                screenshot_b64 = _b64.b64encode(f.read()).decode()
                        except Exception:
                            pass

                        # Extract rich result data: title + snippet + url
                        results_text = ""
                        try:
                            result_els = page.locator(".result").all()
                            for i, el in enumerate(result_els[:8]):
                                try:
                                    title   = el.locator(".result__title").inner_text(timeout=500).strip()
                                    snippet = el.locator(".result__snippet").inner_text(timeout=500).strip()
                                    href    = ""
                                    link    = el.locator("a.result__url")
                                    if link.count() > 0:
                                        href = link.first.get_attribute("href") or ""
                                    results_text += f"[{i+1}] {title}\n{snippet}\n{href}\n\n"
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        # Fallback: raw body text
                        if not results_text.strip():
                            try:
                                results_text = page.locator("body").inner_text(timeout=5000)[:3000]
                            except Exception:
                                results_text = "No results extracted."

                        result_payload = (
                            f"Search Results for '{query}' (DuckDuckGo):\n"
                            f"Current URL: {page.url}\n\n"
                            f"{results_text[:2500]}"
                        )
                        res_q.put(("ok", result_payload))

                    else:
                        # ── Navigate to the actual article/page URL ──
                        if not url.startswith("http"):
                            url = "https://" + url
                        try:
                            page.goto(url, wait_until="commit", timeout=25000)
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception as ge:
                            logger.warning(f"[browse] goto issue for {url}: {ge}")

                        # Dismiss cookie / popup overlays
                        _dismiss_selectors = [
                            'button:has-text("Accept")', 'button:has-text("Accept All")',
                            'button:has-text("I agree")', 'button:has-text("Got it")',
                            'button:has-text("Close")', 'button:has-text("No thanks")',
                            '[aria-label="Close"]', '[aria-label="Dismiss"]',
                        ]
                        try:
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(400)
                            for sel in _dismiss_selectors:
                                try:
                                    btn = page.locator(sel).first
                                    if btn.is_visible(timeout=300):
                                        btn.click()
                                        page.wait_for_timeout(400)
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass

                        # Take a screenshot so the model can visually verify page state
                        import base64 as _b64
                        screenshot_b64 = ""
                        try:
                            ss_dir  = r"C:\VoxKage\Brain"
                            os.makedirs(ss_dir, exist_ok=True)
                            ss_path = os.path.join(ss_dir, "browse_result.jpg")
                            page.screenshot(path=ss_path, type="jpeg", quality=55)
                            with open(ss_path, "rb") as f:
                                screenshot_b64 = _b64.b64encode(f.read()).decode()
                        except Exception:
                            pass

                        # Extract above-the-fold content
                        page_text = ""
                        _content_selectors = [
                            "article", "main", "#content", "#main-content",
                            ".article-body", ".story-body", ".post-content",
                        ]
                        for sel in _content_selectors:
                            try:
                                el = page.locator(sel).first
                                if el.is_visible(timeout=500):
                                    page_text = el.inner_text(timeout=5000)
                                    if len(page_text.strip()) > 300:
                                        break
                            except Exception:
                                pass
                        if not page_text.strip():
                            try:
                                page_text = page.locator("body").inner_text(timeout=5000)
                            except Exception:
                                page_text = "Could not extract page text."

                        # Scroll down and grab more content if above-the-fold is thin
                        if len(page_text.strip()) < 800:
                            try:
                                page.mouse.wheel(0, 1200)
                                page.wait_for_timeout(1200)
                                extra = ""
                                for sel in _content_selectors:
                                    try:
                                        el = page.locator(sel).first
                                        if el.is_visible(timeout=500):
                                            extra = el.inner_text(timeout=5000)
                                            if len(extra.strip()) > len(page_text.strip()):
                                                page_text = extra
                                                break
                                    except Exception:
                                        pass
                                if not extra.strip():
                                    try:
                                        extra = page.locator("body").inner_text(timeout=5000)
                                        if len(extra) > len(page_text):
                                            page_text = extra
                                    except Exception:
                                        pass
                                # Take another screenshot after scroll
                                try:
                                    ss_path2 = os.path.join(ss_dir, "browse_result_scrolled.jpg")
                                    page.screenshot(path=ss_path2, type="jpeg", quality=55)
                                    with open(ss_path2, "rb") as f:
                                        screenshot_b64 = _b64.b64encode(f.read()).decode()
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        try:
                            page_title = page.title()
                        except Exception:
                            page_title = "unknown"

                        text_out = (
                            f"=== PAGE LOADED ===\n"
                            f"URL   : {page.url}\n"
                            f"TITLE : {page_title}\n"
                            f"--- CONTENT (up to 5000 chars) ---\n"
                            f"{page_text[:5000]}\n"
                            f"--- END ---\n"
                            f"VERIFICATION: Confirm the URL above matches the target domain. "
                            f"If content looks like a login wall or CAPTCHA, try search_web instead."
                        )
                        res_q.put(("ok", {"__vision__": True, "text": text_out, "screenshot_b64": screenshot_b64}))

                elif action == "search_youtube":
                    query = kwargs.get('query')
                    encoded_title = urllib.parse.quote(query)
                    search_url = f"https://www.youtube.com/results?search_query={encoded_title}"
                    page.goto(search_url)
                    
                    # Wait for video renders
                    page.wait_for_selector('ytd-video-renderer', timeout=15000)
                    
                    # Use page.evaluate to instantly scrape the top 5 videos in JS (avoids Playwright's auto-wait overhead)
                    videos_data = page.evaluate("""() => {
                        let vids = Array.from(document.querySelectorAll('ytd-video-renderer')).slice(0, 5);
                        return vids.map((v, i) => {
                            let titleEl = v.querySelector('a#video-title');
                            if (!titleEl) return null;
                            let title = titleEl.getAttribute('title') || titleEl.innerText;
                            let href = titleEl.getAttribute('href');
                            if (href && !href.startsWith('http')) {
                                href = "https://www.youtube.com" + href;
                            }
                            return {
                                number: i + 1,
                                title: title ? title.replace(/\\n/g, ' ').trim() : 'Unknown',
                                url: href
                            };
                        }).filter(v => v !== null);
                    }""")
                    
                    # Clear and populate global local memory state
                    GLOBAL_VIDEO_OPTIONS.clear()
                    if videos_data:
                        GLOBAL_VIDEO_OPTIONS.extend(videos_data)
                        
                    res_q.put(("ok", GLOBAL_VIDEO_OPTIONS))
                    
                elif action == "search_spotify_web":
                    query = kwargs.get('query')
                    from automation.spotify_control import GLOBAL_SPOTIFY_OPTIONS
                    encoded_query = urllib.parse.quote(query)
                    search_url = f"https://open.spotify.com/search/{encoded_query}/tracks"
                    page.goto(search_url)
                    page.wait_for_load_state("networkidle", timeout=10000)
                    
                    # Wait for track results - spotify web UI changes occasionally
                    try:
                        page.wait_for_selector('div[data-testid="tracklist-row"]', timeout=10000)
                    except:
                        pass # Sometimes it just loads slowly
                        
                    tracks = page.locator('div[data-testid="tracklist-row"]').all()
                    
                    GLOBAL_SPOTIFY_OPTIONS.clear()
                    
                    for i, tr in enumerate(tracks[:5]):
                        try:
                            # The title is usually in a div inside the row
                            title_el = tr.locator('a[data-testid="internal-track-link-name"]')
                            title = title_el.inner_text()
                            
                            # Artists
                            artist_els = tr.locator('a[href^="/artist/"]').all()
                            artists = ", ".join([a.inner_text() for a in artist_els])
                            
                            GLOBAL_SPOTIFY_OPTIONS.append({"number": i+1, "title": f"{title} by {artists}", "element_index": i})
                        except Exception:
                            continue
                            
                    res_q.put(("ok", GLOBAL_SPOTIFY_OPTIONS))

                elif action == "play_spotify_web":
                    number = kwargs.get('number')
                    from automation.spotify_control import GLOBAL_SPOTIFY_OPTIONS
                    
                    if number < 1 or number > len(GLOBAL_SPOTIFY_OPTIONS):
                        res_q.put(("error", "Invalid selection number."))
                        continue
                        
                    # Find the track row and double click or click play button
                    idx = GLOBAL_SPOTIFY_OPTIONS[number - 1].get("element_index", number - 1)
                    tracks = page.locator('div[data-testid="tracklist-row"]').all()
                    if idx < len(tracks):
                        tr = tracks[idx]
                        
                        # Use evaluating a double click or hover to show play button
                        tr.hover()
                        page.wait_for_timeout(500)
                        
                        try:
                            # Play button inside the track row appears on hover
                            play_btn = tr.locator('button[data-testid="play-button"]')
                            play_btn.click()
                            res_q.put(("ok", "Playing Spotify track on Web."))
                        except:
                            # Fallback to double clicking the row
                            tr.dblclick()
                            res_q.put(("ok", "Double-clicked Spotify track on Web."))
                    else:
                        res_q.put(("error", "Could not locate the track row."))

                elif action == "play_spotify_web_url":
                    url = kwargs.get('url')
                    page.goto(url)
                    page.wait_for_load_state("networkidle", timeout=10000)
                    # Click the giant play button
                    # The locator can be 'play-button' or 'action-bar-button' or 'aria-label="Play"' depending on layout
                    try:
                        for selector in [
                            'button[data-testid="play-button"]', 
                            'button[data-testid="action-bar-button"]',
                            'button[aria-label="Play"]'
                        ]:
                            btn = page.locator(selector).first
                            if btn.is_visible(timeout=2000):
                                btn.click()
                                res_q.put(("ok", "Started playing playlist on Spotify Web."))
                                break
                        else:
                            res_q.put(("error", "Failed to find the play button on playlist page."))
                    except Exception as e:
                        res_q.put(("error", f"Failed to click play on playlist: {e}"))


                elif action == "control_spotify_web":
                    btn_action = kwargs.get('action')
                    try:
                        # Bottom player bar
                        if btn_action == "play" or btn_action == "resume" or btn_action == "pause" or btn_action == "stop":
                            btn = page.locator('button[data-testid="control-button-playpause"]')
                            btn.click(timeout=5000)
                            res_q.put(("ok", f"Toggled play/pause on Spotify Web."))
                        elif btn_action == "next":
                            btn = page.locator('button[data-testid="control-button-skip-forward"]')
                            btn.click(timeout=5000)
                            res_q.put(("ok", f"Skipped forward on Spotify Web."))
                        elif btn_action == "prev" or btn_action == "previous":
                            btn = page.locator('button[data-testid="control-button-skip-back"]')
                            btn.click(timeout=5000)
                            res_q.put(("ok", f"Skipped backward on Spotify Web."))
                        else:
                            res_q.put(("error", f"Unsupported control action: {btn_action}"))
                    except Exception as e:
                        res_q.put(("error", f"Failed to control Spotify web player. Not on page or element not found: {e}"))
                    
                elif action == "get_state":
                    try:
                        # Try preferred location, fall back to system temp dir
                        try:
                            save_dir = r"C:\VoxKage\Brain"
                            os.makedirs(save_dir, exist_ok=True)
                            screenshot_path = os.path.join(save_dir, "latest_browser_state.jpg")
                        except Exception:
                            import tempfile
                            screenshot_path = os.path.join(tempfile.gettempdir(), "voxkage_browser_state.jpg")

                        screenshot_b64 = ""
                        try:
                            page.screenshot(path=screenshot_path, type="jpeg", quality=70)
                            screenshot_b64 = _encode_screenshot(screenshot_path)
                        except Exception as ss_err:
                            logger.warning(f"Screenshot failed: {ss_err}")
                            screenshot_path = "screenshot_unavailable"

                        url = page.url
                        try:
                            title = page.title()
                        except Exception:
                            title = "unknown"

                        # Extract page text with generous timeout
                        page_text = ""
                        try:
                            main_el = page.locator("main").first
                            if main_el.count() > 0 and main_el.is_visible(timeout=1000):
                                page_text = main_el.inner_text(timeout=3000)
                        except Exception:
                            pass

                        if not page_text.strip():
                            try:
                                page_text = page.locator("body").inner_text(timeout=5000)
                            except Exception:
                                page_text = "Could not extract page text."

                        # Detect login walls
                        hard_indicators = [
                            'input[type="password"]',
                            'form[action*="login"]', 'form[action*="signin"]',
                            'input[name="session_key"]'
                        ]
                        is_login_wall = False
                        for sel in hard_indicators:
                            try:
                                if page.locator(sel).first.is_visible(timeout=200):
                                    is_login_wall = True
                                    break
                            except Exception:
                                pass

                        page_text_lower = page_text.lower()
                        is_captcha = any(k in page_text_lower for k in [
                            "challenge-platform", "captcha", "verify you are human",
                            "i am not a robot", "prove you're human"
                        ])

                        res_q.put(("ok", {
                            "url": url,
                            "title": title,
                            "page_text_snippet": page_text[:800],
                            "screenshot_path": screenshot_path,
                            "screenshot_b64": screenshot_b64,   # for qwen3.5 vision
                            "is_login_wall": is_login_wall,
                            "is_captcha": is_captcha
                        }))
                    except Exception as e:
                        logger.error(f"get_state failed: {e}")
                        res_q.put(("error", f"Failed to get browser state: {e}"))


                elif action == "play":
                    number = kwargs.get('number')
                    if not GLOBAL_VIDEO_OPTIONS:
                        res_q.put(("error", "No videos available in memory. Please ask me to search for a topic on YouTube first."))
                        continue
                        
                    try:
                        idx = int(number) - 1
                        if 0 <= idx < len(GLOBAL_VIDEO_OPTIONS):
                            url = GLOBAL_VIDEO_OPTIONS[idx]["url"]
                            page.goto(url)
                            page.bring_to_front()
                            res_q.put(("ok", "Video is now playing successfully. Let me know if I can help you with anything else."))
                        else:
                            res_q.put(("error", f"Invalid selection. I only found {len(GLOBAL_VIDEO_OPTIONS)} videos. Please say 'play number 1', or 'play number {len(GLOBAL_VIDEO_OPTIONS)}'."))
                    except Exception as e:
                        res_q.put(("error", f"Failed to play selection: {e}"))
                        
                elif action == "control_media_web":
                    btn_action = kwargs.get('action', '').lower()
                    try:
                        # Ensure we are on a page containing a video before controlling
                        if "youtube.com" not in page.url:
                            res_q.put(("error", "YouTube is not currently open in the active browser tab."))
                            continue
                            
                        # First determine if video is playing or paused
                        is_paused = page.evaluate("() => { const v = document.querySelector('video'); return v ? v.paused : true; }")
                        
                        if btn_action in ["pause", "stop"]:
                            if is_paused:
                                res_q.put(("ok", "Video is already paused."))
                            else:
                                page.evaluate("() => { const v = document.querySelector('video'); if(v) v.pause(); }")
                                res_q.put(("ok", "Video paused successfully."))
                        elif btn_action in ["play", "resume"]:
                            if not is_paused:
                                res_q.put(("ok", "Video is already playing."))
                            else:
                                page.evaluate("() => { const v = document.querySelector('video'); if(v) v.play(); }")
                                res_q.put(("ok", "Video resumed successfully."))
                        elif btn_action in ["next", "skip"]:
                            page.keyboard.press("Shift+N") # YouTube native shortcut for next video
                            res_q.put(("ok", "Skipped to next video on YouTube."))
                        elif btn_action == "prev" or btn_action == "previous":
                            page.keyboard.press("Shift+P") # YouTube native shortcut inside playlists
                            res_q.put(("ok", "Skipped to previous video on YouTube."))
                        elif btn_action == "fullscreen":
                            page.keyboard.press("f")
                            res_q.put(("ok", "Toggled fullscreen mode."))
                        elif btn_action == "status":
                            title = page.title().replace(" - YouTube", "")
                            state = "paused" if is_paused else "playing"
                            res_q.put(("ok", f"Currently {state} on YouTube: {title}. Note to self: If the user asked about the creator, search_web. Otherwise, Goal Met: tell the user exactly what is playing."))
                        else:
                            res_q.put(("error", f"Unknown media control action: {btn_action}"))
                    except Exception as e:
                        res_q.put(("error", f"Failed to control YouTube media: {e}"))
                
                elif action == "agent_step":
                    # === PHASE 3: SINGLE ATOMIC ACTION WITH SCREENSHOT ===
                    sub_action = kwargs.get("action", "")
                    goal = kwargs.get("goal", "")
                    intent = kwargs.get("intent", "")
                    
                    try:
                        # Reuse helpers from workflow
                        def find_element_by_intent(p, intent_label: str):
                            def attempt_find(label: str):
                                locators = [
                                    p.get_by_role('textbox', name=label, exact=False),
                                    p.get_by_role('button', name=label, exact=False),
                                    p.get_by_placeholder(label, exact=False),
                                    p.locator(f'input[name*="{label}" i], input[id*="{label}" i], button[id*="{label}" i], [aria-label*="{label}" i], [title*="{label}" i]'),
                                    p.locator(f'input[class*="search" i], input[class*="query" i], input[data-testid*="search" i]'),
                                ]
                                for loc in locators:
                                    try:
                                        if loc.count() > 0:
                                            first = loc.first
                                            if first.is_visible(timeout=500):
                                                return first
                                    except:
                                        pass
                                return None
                                
                            el = attempt_find(intent_label)
                            if not el and " " in intent_label:
                                el = attempt_find(intent_label.split()[-1])
                                if not el:
                                    el = attempt_find(intent_label.split()[0])
                                
                            if not el:
                                try:
                                    p.mouse.wheel(0, 500)
                                    p.wait_for_timeout(500)
                                    el = attempt_find(intent_label)
                                    if not el and " " in intent_label:
                                        el = attempt_find(intent_label.split()[-1])
                                        if not el:
                                            el = attempt_find(intent_label.split()[0])
                                except:
                                    pass
                            return el

                        def human_type(p, locator, text: str):
                            import pyautogui, random, time
                            try:
                                x, y = pyautogui.position()
                                pyautogui.moveTo(x + random.randint(-15, 15), y + random.randint(-15, 15), 0.2)
                            except:
                                pass
                            locator.click()
                            for char in text:
                                p.keyboard.type(char)
                                time.sleep(random.uniform(0.05, 0.2))
                        
                        def _dismiss_obstacles(p):
                            try:
                                p.keyboard.press("Escape")
                                p.wait_for_timeout(500)
                                dismiss_selectors = [
                                    'button:has-text("Accept")', 'button:has-text("Accept All")',
                                    'button:has-text("I agree")', 'button:has-text("Got it")',
                                    'button:has-text("Close")', 'button:has-text("No thanks")',
                                    'button:has-text("Not now")', 'button:has-text("Dismiss")',
                                    'button:has-text("Skip")', '[aria-label="Close"]',
                                    '[aria-label="Dismiss"]', '.close-button', '.modal-close',
                                ]
                                for sel in dismiss_selectors:
                                    try:
                                        btn = p.locator(sel).first
                                        if btn.is_visible(timeout=300):
                                            btn.click()
                                            p.wait_for_timeout(500)
                                            break
                                    except:
                                        continue
                            except:
                                pass

                        def _checkpoint_screenshot(label: str) -> str:
                            """Take screenshot and return base64. Caps at 1280x720 for speed."""
                            try:
                                try:
                                    ss_dir = r"C:\VoxKage\Brain"
                                    os.makedirs(ss_dir, exist_ok=True)
                                except Exception:
                                    import tempfile
                                    ss_dir = tempfile.gettempdir()
                                ss_path = os.path.join(ss_dir, f"agent_step_{label}.jpg")
                                # Clip to 1280x720 max — keeps base64 small (~60KB) so
                                # vision processing is fast on 6GB VRAM hardware.
                                try:
                                    vp = page.viewport_size
                                    w = min(vp["width"],  1280) if vp else 1280
                                    h = min(vp["height"], 720)  if vp else 720
                                    page.screenshot(
                                        path=ss_path, type="jpeg", quality=50,
                                        clip={"x": 0, "y": 0, "width": w, "height": h}
                                    )
                                except Exception:
                                    # Fallback: full-page at low quality
                                    page.screenshot(path=ss_path, type="jpeg", quality=40)
                                with open(ss_path, "rb") as f:
                                    b64 = base64.b64encode(f.read()).decode("utf-8")
                                return b64
                            except Exception:
                                return ""

                        # === EXECUTE THE SINGLE ACTION ===
                        action_result = {"success": False, "message": "", "url_after": page.url}
                        
                        if sub_action == "goto":
                            url = kwargs.get("url", "")
                            if url and not url.startswith("http"):
                                url = "https://" + url
                            # Use 'commit' — fires on first byte, not after full DOM.
                            # Prevents slow SPAs (Epic, Steam) from timing out.
                            try:
                                page.goto(url, wait_until="commit", timeout=60000)
                            except Exception as goto_err:
                                logger.warning(f"[agent_step] goto timeout for {url}: {goto_err}. Continuing with partial load.")
                            try:
                                page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                page.wait_for_timeout(3000)
                            _dismiss_obstacles(page)
                            action_result = {"success": True, "message": f"Navigated to {page.url}", "url_after": page.url}
                            
                        elif sub_action == "search_on_site":
                            site = kwargs.get("site", "").lower().replace("www.", "")
                            # Strip sub-paths (e.g. 'store.epicgames.com/en-US' -> 'store.epicgames.com')
                            site_domain = site.split("/")[0]
                            query = kwargs.get("query", kwargs.get("text", ""))
                            encoded_q = urllib.parse.quote_plus(query)
                            direct_url = None
                            for known_site, template in DIRECT_SEARCH_URLS.items():
                                known_domain = known_site.split("/")[0]
                                if site_domain in known_domain or known_domain in site_domain:
                                    direct_url = template.replace("{q}", encoded_q)
                                    break
                            if not direct_url:
                                direct_url = f"https://duckduckgo.com/?q={encoded_q}+site:{site_domain}"
                            try:
                                page.goto(direct_url, wait_until="commit", timeout=60000)
                            except Exception as goto_err:
                                logger.warning(f"[agent_step] search_on_site goto timeout: {goto_err}")
                            try:
                                page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                page.wait_for_timeout(3000)
                            _dismiss_obstacles(page)
                            action_result = {"success": True, "message": f"Searched {site_domain} for '{query}'", "url_after": page.url}

                        elif sub_action == "search_on_page":
                            # === NATIVE SITE SEARCH ===
                            # Find the search bar on the CURRENT page, type the query, hit Enter.
                            # This is the 'human' way to search: open a site, use its own search box.
                            query = kwargs.get("query", kwargs.get("text", intent or ""))
                            url_before_search = page.url

                            # Give any lazy-loaded JS UI a moment to settle
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception:
                                page.wait_for_timeout(2000)

                            # Broad set of search-input selectors, ordered by specificity
                            search_selectors = [
                                'input[type="search"]',
                                'input[name="q"]',
                                'input[name="query"]',
                                'input[name="search"]',
                                'input[placeholder*="search" i]',
                                'input[placeholder*="find" i]',
                                'input[placeholder*="look" i]',
                                '[role="searchbox"]',
                                '[aria-label*="search" i]',
                                'input[id*="search" i]',
                                'input[class*="search" i]',
                                'input[data-testid*="search" i]',
                                'input[type="text"]',   # last resort
                            ]

                            search_el = None
                            for sel in search_selectors:
                                try:
                                    candidates = page.locator(sel).all()
                                    for candidate in candidates:
                                        if candidate.is_visible(timeout=500):
                                            search_el = candidate
                                            break
                                except Exception:
                                    pass
                                if search_el:
                                    break

                            # If not found: dismiss obstacles (popups) and retry once
                            if not search_el:
                                _dismiss_obstacles(page)
                                page.wait_for_timeout(1500)
                                for sel in search_selectors:
                                    try:
                                        candidates = page.locator(sel).all()
                                        for candidate in candidates:
                                            if candidate.is_visible(timeout=500):
                                                search_el = candidate
                                                break
                                    except Exception:
                                        pass
                                    if search_el:
                                        break

                            if search_el:
                                logger.info(f"[agent_step] search_on_page: found input, typing '{query}'")
                                # Clear existing content, then type naturally
                                try:
                                    search_el.triple_click()
                                    page.wait_for_timeout(200)
                                except Exception:
                                    pass
                                human_type(page, search_el, query)
                                page.wait_for_timeout(800)

                                # React SPAs (Reddit, Epic Games, etc.) manage input value
                                # in virtual DOM — form.submit() bypasses React state and
                                # submits an empty query. Always use keyboard Enter, which
                                # fires React's onKeyDown/onKeyPress synthetic events.
                                page.keyboard.press("Enter")

                                # Wait for navigation to the results page
                                for _retry in range(8):
                                    try:
                                        page.wait_for_load_state("domcontentloaded", timeout=5000)
                                    except Exception:
                                        pass
                                    page.wait_for_timeout(1500)
                                    if page.url != url_before_search:
                                        logger.info(f"[agent_step] search_on_page: navigated to {page.url}")
                                        break
                                    if _retry == 2:
                                        page.keyboard.press("Enter")  # retry Enter
                                    elif _retry == 4:
                                        # Last resort: click a visible search-submit button
                                        for btn_sel in [
                                            'button[type="submit"]',
                                            'button[aria-label*="search" i]',
                                            '[data-testid*="search-submit" i]',
                                        ]:
                                            try:
                                                btn = page.locator(btn_sel).first
                                                if btn.is_visible(timeout=500):
                                                    btn.click()
                                                    break
                                            except Exception:
                                                pass

                                try:
                                    page.wait_for_load_state("networkidle", timeout=10000)
                                except Exception:
                                    page.wait_for_timeout(3000)
                                _dismiss_obstacles(page)

                                # Sanity check: if URL has empty q= param, React didn't pick
                                # up the typed text. Fall back to the direct search URL.
                                final_url = page.url
                                import re as _re
                                if _re.search(r'[?&]q=(&|$)', final_url) or final_url.endswith('?q='):
                                    logger.warning(f"[agent_step] search_on_page: empty query in URL {final_url}. Using direct search URL fallback.")
                                    current_domain = final_url.split('/')[2].lower().replace('www.', '')
                                    encoded_q = urllib.parse.quote_plus(query)
                                    fallback_url = None
                                    for known, template in DIRECT_SEARCH_URLS.items():
                                        if current_domain in known or known in current_domain:
                                            fallback_url = template.replace('{q}', encoded_q)
                                            break
                                    if not fallback_url:
                                        fallback_url = f"https://duckduckgo.com/?q={encoded_q}+site:{current_domain}"
                                    try:
                                        page.goto(fallback_url, wait_until="commit", timeout=30000)
                                        page.wait_for_timeout(2000)
                                        logger.info(f"[agent_step] search_on_page fallback: navigated to {page.url}")
                                    except Exception as fb_err:
                                        logger.warning(f"[agent_step] fallback goto failed: {fb_err}")

                                action_result = {
                                    "success": True,
                                    "message": f"Searched for '{query}' using the site's search bar. Now on {page.url}",
                                    "url_after": page.url
                                }
                            else:
                                # No search bar found — tell the LLM clearly
                                action_result = {
                                    "success": False,
                                    "message": (
                                        f"Could not find a search box on {page.url}. "
                                        f"The page may not have loaded fully, or uses a custom UI. "
                                        f"Try: (1) wait and retry, or (2) use search_on_site to search via DuckDuckGo."
                                    ),
                                    "url_after": page.url
                                }
                                logger.warning(f"[agent_step] search_on_page: no search input found on {page.url}")
                            
                        elif sub_action == "type":
                            selector = kwargs.get("selector")
                            text = kwargs.get("text", "")
                            el = None
                            if intent:
                                el = find_element_by_intent(page, intent)
                            if not el and selector:
                                try:
                                    if page.locator(selector).count() > 0:
                                        el = page.locator(selector).first
                                except:
                                    pass
                            if el:
                                human_type(page, el, text)
                                page.wait_for_timeout(800)
                                try:
                                    page.evaluate("""
                                        (() => {
                                            const input = document.querySelector('input[type="text"], input[name="q"], input[type="search"]');
                                            if (input && input.form) { input.form.submit(); return true; }
                                            return false;
                                        })()
                                    """)
                                except:
                                    page.keyboard.press("Enter")
                                for _retry in range(4):
                                    try:
                                        page.wait_for_load_state("domcontentloaded", timeout=4000)
                                    except:
                                        pass
                                    page.wait_for_timeout(1000)
                                    if page.url != action_result["url_after"]:
                                        break
                                _dismiss_obstacles(page)
                                action_result = {"success": True, "message": f"Typed '{text}' and submitted", "url_after": page.url}
                            else:
                                action_result = {"success": False, "message": f"Could not find input field for '{intent or selector}'", "url_after": page.url}
                                
                        elif sub_action == "click":
                            selector = kwargs.get("selector")
                            el = None
                            if intent:
                                el = find_element_by_intent(page, intent)
                            if not el and selector:
                                try:
                                    if page.locator(selector).count() > 0:
                                        el = page.locator(selector).first
                                except:
                                    pass
                            if el:
                                el.click()
                                try:
                                    page.wait_for_load_state("networkidle", timeout=15000)
                                except:
                                    page.wait_for_timeout(3000)
                                _dismiss_obstacles(page)
                                action_result = {"success": True, "message": f"Clicked '{intent or selector}'", "url_after": page.url}
                            else:
                                action_result = {"success": False, "message": f"Could not find element to click for '{intent or selector}'", "url_after": page.url}
                                
                        elif sub_action == "click_best_link":
                            links = page.locator('a[href]').all()
                            skip_patterns = ['login', 'sign', 'account', 'cart', 'help', 'about', 'privacy', 'terms', 'cookie']
                            target_domain = None
                            goal_lower = goal.lower()
                            for known_domain in DIRECT_SEARCH_URLS.keys():
                                domain_name = known_domain.split(".")[0]
                                if domain_name in goal_lower or known_domain in goal_lower:
                                    target_domain = known_domain
                                    break
                            
                            clicked = False
                            if target_domain:
                                for link in links[:15]:
                                    try:
                                        href = link.get_attribute("href") or ""
                                        if target_domain in href and not any(s in href for s in skip_patterns):
                                            link.scroll_into_view_if_needed()
                                            link.click()
                                            clicked = True
                                            break
                                    except:
                                        continue
                            if not clicked:
                                for link in links[:20]:
                                    try:
                                        href = link.get_attribute("href") or ""
                                        text = link.inner_text().strip()
                                        if (len(text) > 10 and href.startswith("http") and not any(s in href.lower() for s in skip_patterns)):
                                            link.scroll_into_view_if_needed()
                                            link.click()
                                            clicked = True
                                            break
                                    except:
                                        continue
                            
                            if clicked:
                                try:
                                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                                except:
                                    pass
                                _dismiss_obstacles(page)
                                action_result = {"success": True, "message": f"Clicked best link, landed on {page.url}", "url_after": page.url}
                            else:
                                action_result = {"success": False, "message": "No suitable link found to click", "url_after": page.url}
                                
                        elif sub_action == "scroll":
                            direction = kwargs.get("direction", "down")
                            amount = 600 if direction == "down" else -600
                            page.mouse.wheel(0, amount)
                            page.wait_for_timeout(1000)
                            action_result = {"success": True, "message": f"Scrolled {direction}", "url_after": page.url}
                            
                        elif sub_action == "wait":
                            ms = int(kwargs.get("ms", 2000))
                            page.wait_for_timeout(min(ms, 10000))
                            action_result = {"success": True, "message": f"Waited {ms}ms", "url_after": page.url}
                            
                        elif sub_action == "extract_text":
                            pass  # Text extraction happens after action
                            
                        elif sub_action == "extract_image_urls":
                            # Extract all image URLs from the live rendered DOM via JS evaluation.
                            # Works on JavaScript-heavy sites (Unsplash, Pexels, Pixabay, Google Images).
                            # Returns a JSON array of de-duplicated, high-res image URLs.
                            try:
                                js_result = page.evaluate("""() => {
                                    const seen = new Set();
                                    const urls = [];

                                    function addUrl(u) {
                                        if (!u || typeof u !== 'string') return;
                                        // Normalise: strip query params for dedup key
                                        const base = u.split('?')[0];
                                        if (seen.has(base)) return;
                                        // Skip tiny/icon/avatar images
                                        const low = u.toLowerCase();
                                        const skip = ['avatar', 'logo', 'icon', 'favicon', 'pixel', 'tracker',
                                                      'badge', '1x1', 'spacer', 'blank', 'loading', 'placeholder'];
                                        if (skip.some(s => low.includes(s))) return;
                                        seen.add(base);
                                        urls.push(u);
                                    }

                                    // 1. Standard img tags
                                    document.querySelectorAll('img').forEach(img => {
                                        addUrl(img.src);
                                        addUrl(img.dataset.src);
                                        addUrl(img.dataset.lazySrc);
                                        addUrl(img.dataset.original);
                                        // Parse srcset: pick the highest-res URL
                                        if (img.srcset) {
                                            const parts = img.srcset.split(',').map(s => s.trim().split(/\s+/)[0]);
                                            parts.forEach(addUrl);
                                        }
                                    });

                                    // 2. Picture source elements (srcset)
                                    document.querySelectorAll('source[srcset]').forEach(src => {
                                        const parts = src.srcset.split(',').map(s => s.trim().split(/\s+/)[0]);
                                        parts.forEach(addUrl);
                                    });

                                    // 3. CSS background-image on common image containers
                                    document.querySelectorAll('a[style], div[style], figure[style], span[style]').forEach(el => {
                                        const bg = el.style.backgroundImage || '';
                                        const m = bg.match(/url\\(['"]?([^'"\\)]+)['"]?\\)/);
                                        if (m) addUrl(m[1]);
                                    });

                                    // 4. data attributes used by lazy loaders
                                    document.querySelectorAll('[data-image], [data-photo-url], [data-full]').forEach(el => {
                                        addUrl(el.dataset.image);
                                        addUrl(el.dataset.photoUrl);
                                        addUrl(el.dataset.full);
                                    });

                                    // Filter: keep only http/https URLs
                                    return urls.filter(u => u && (u.startsWith('http://') || u.startsWith('https://')));
                                }""")
                                import json as _json
                                img_urls_json = _json.dumps(js_result if isinstance(js_result, list) else [])
                                action_result = {
                                    "success": True,
                                    "message": f"Extracted {len(js_result) if isinstance(js_result, list) else 0} image URLs from DOM",
                                    "url_after": page.url,
                                    "image_urls": js_result if isinstance(js_result, list) else [],
                                    "image_urls_json": img_urls_json,
                                }
                            except Exception as js_e:
                                action_result = {
                                    "success": False,
                                    "message": f"JS evaluation failed: {js_e}",
                                    "url_after": page.url,
                                    "image_urls": [],
                                    "image_urls_json": "[]",
                                }

                        elif sub_action == "trigger_download":
                            # Click a download button and capture the download event to get the real file URL.
                            # Use this when a site hides the direct URL behind a button click.
                            intent_label = kwargs.get("intent", "download")
                            try:
                                with page.expect_download(timeout=15000) as download_info:
                                    # Try common download button patterns
                                    clicked = False
                                    for sel in [
                                        f'[aria-label*="{intent_label}" i]',
                                        f'a[download]',
                                        f'button:has-text("{intent_label}")',
                                        f'a:has-text("{intent_label}")',
                                        f'[data-testid*="download"]',
                                        f'[class*="download"]',
                                    ]:
                                        try:
                                            el = page.locator(sel).first
                                            if el.is_visible(timeout=1000):
                                                el.click(timeout=5000)
                                                clicked = True
                                                break
                                        except Exception:
                                            continue
                                    if not clicked:
                                        raise Exception("No download button found")

                                download = download_info.value
                                download_url = download.url
                                suggested_name = download.suggested_filename
                                action_result = {
                                    "success": True,
                                    "message": f"Download triggered: {suggested_name}",
                                    "url_after": page.url,
                                    "download_url": download_url,
                                    "suggested_filename": suggested_name,
                                }
                            except Exception as dl_e:
                                action_result = {
                                    "success": False,
                                    "message": f"trigger_download failed: {dl_e}",
                                    "url_after": page.url,
                                }

                        else:
                            action_result = {"success": False, "message": f"Unknown action: {sub_action}", "url_after": page.url}
                        
                        # === CAPTURE SCREENSHOT AND PAGE TEXT ===
                        screenshot_b64 = _checkpoint_screenshot(sub_action)

                        # Wait for dynamic content to settle before extracting
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            page.wait_for_timeout(1500)

                        # Try to extract MAIN content region (skip nav headers)
                        page_text = ""
                        main_content_selectors = [
                            # Amazon product grid
                            '[data-component-type="s-search-result"]',
                            '#search .s-result-list',
                            '#search',
                            # Flipkart product grid
                            '._1YokD2._3Mn1Gg',
                            '[data-id]._30jeq3',
                            # Generic main content areas
                            'main',
                            '#main-content',
                            '#content',
                            'article',
                        ]
                        for sel in main_content_selectors:
                            try:
                                el = page.locator(sel).first
                                if el.is_visible(timeout=500):
                                    page_text = el.inner_text(timeout=5000)
                                    if len(page_text.strip()) > 200:
                                        break
                            except Exception:
                                pass

                        # Fallback: full body text
                        if not page_text or len(page_text.strip()) < 100:
                            try:
                                page_text = page.locator("body").inner_text(timeout=5000)
                            except Exception:
                                page_text = "Could not extract page text."

                        try:
                            page_title = page.title()
                        except Exception:
                            page_title = "unknown"

                        status_msg = "Page loaded normally."
                        if action_result.get("success"):
                            status_msg = f"Action executed successfully: {action_result.get('message', '')}"
                        else:
                            status_msg = f"Action failed: {action_result.get('message', '')}"

                        # Include current URL clearly so model can verify it landed on the right domain
                        current_url = page.url

                        # Build extra data block for actions that return structured data
                        extra_lines = ""
                        if sub_action == "extract_image_urls" and action_result.get("image_urls_json"):
                            extra_lines = (
                                f"\n--- IMAGE URLS (JSON) ---\n"
                                f"{action_result['image_urls_json']}\n"
                                f"--- END IMAGE URLS ---\n"
                                f"TOTAL FOUND: {len(action_result.get('image_urls', []))} image URLs\n"
                            )
                        elif sub_action == "trigger_download":
                            dl_url = action_result.get("download_url", "")
                            dl_name = action_result.get("suggested_filename", "")
                            if dl_url:
                                extra_lines = (
                                    f"\n--- DOWNLOAD CAPTURED ---\n"
                                    f"DOWNLOAD URL: {dl_url}\n"
                                    f"FILENAME: {dl_name}\n"
                                    f"--- END DOWNLOAD ---\n"
                                )

                        res_q.put(("ok", {
                            "__vision__": True,
                            "text": (
                                f"=== AGENT STEP RESULT ===\n"
                                f"ACTION: {sub_action}\n"
                                f"RESULT: {'success' if action_result.get('success') else 'failed'}\n"
                                f"CURRENT URL: {current_url}\n"
                                f"PAGE TITLE: {page_title}\n"
                                f"--- PAGE CONTENT (up to 3000 chars) ---\n"
                                f"{page_text[:3000]}\n"
                                f"--- END CONTENT ---\n"
                                f"STATUS: {status_msg}\n"
                                f"{extra_lines}"
                                f"VERIFICATION: Check that CURRENT URL contains the expected domain. If yes, this step succeeded."
                            ),
                            "screenshot_b64": screenshot_b64,
                            # Pass structured data through for programmatic consumers (download_server.py)
                            "image_urls": action_result.get("image_urls", []),
                            "download_url": action_result.get("download_url", ""),
                            "suggested_filename": action_result.get("suggested_filename", ""),
                        }))
                        
                    except Exception as step_e:
                        res_q.put(("error", f"agent_step failed: {step_e}"))

                elif action == "workflow":
                    steps = kwargs.get('steps', [])
                    
                    def find_element_by_intent(p, intent_label: str):
                        def attempt_find(label: str):
                            locators = [
                                p.get_by_role('textbox', name=label, exact=False),
                                p.get_by_role('button', name=label, exact=False),
                                p.get_by_placeholder(label, exact=False),
                                p.locator(f'input[name*="{label}" i], input[id*="{label}" i], button[id*="{label}" i], [aria-label*="{label}" i], [title*="{label}" i]'),
                                # Phase 28: LinkedIn / professional site selectors
                                p.locator(f'input[class*="search" i], input[class*="query" i], input[data-testid*="search" i]'),
                            ]
                            for loc in locators:
                                try:
                                    if loc.count() > 0:
                                        first = loc.first
                                        if first.is_visible(timeout=500):
                                            return first
                                except:
                                    pass
                            return None
                            
                        el = attempt_find(intent_label)
                        if not el and " " in intent_label:
                            # Try just the first and last words as fallback (e.g. "Submit Search" -> "Search")
                            el = attempt_find(intent_label.split()[-1])
                            if not el:
                                el = attempt_find(intent_label.split()[0])
                            
                        if not el:
                            try:
                                p.mouse.wheel(0, 500)
                                p.wait_for_timeout(500)
                                el = attempt_find(intent_label)
                                if not el and " " in intent_label:
                                    el = attempt_find(intent_label.split()[-1])
                                    if not el:
                                        el = attempt_find(intent_label.split()[0])
                            except:
                                pass
                        return el

                    def human_type(p, locator, text: str):
                        import pyautogui, random, time
                        try:
                            x, y = pyautogui.position()
                            pyautogui.moveTo(x + random.randint(-15, 15), y + random.randint(-15, 15), 0.2)
                        except:
                            pass
                            
                        locator.click()
                        for char in text:
                            p.keyboard.type(char)
                            time.sleep(random.uniform(0.05, 0.2))
                    
                    # === PHASE 27: OBSTACLE BYPASS ===
                    def _dismiss_obstacles(p):
                        """Dismiss popups, sign-in walls, cookie banners, and overlays."""
                        try:
                            # Try Escape first to dismiss any modal
                            p.keyboard.press("Escape")
                            p.wait_for_timeout(500)
                            
                            # Look for common dismiss/close/accept buttons
                            dismiss_selectors = [
                                # Cookie consent
                                'button:has-text("Accept")',
                                'button:has-text("Accept All")',
                                'button:has-text("Accept all")',
                                'button:has-text("I agree")',
                                'button:has-text("Got it")',
                                'button:has-text("OK")',
                                # Popup close buttons
                                'button:has-text("Close")',
                                'button:has-text("No thanks")',
                                'button:has-text("Not now")',
                                'button:has-text("Dismiss")',
                                'button:has-text("Skip")',
                                '[aria-label="Close"]',
                                '[aria-label="Dismiss"]',
                                '.close-button', '.modal-close', '.popup-close',
                                # Reddit-specific
                                'button:has-text("Continue")',
                                '[data-testid="close-button"]',
                                'shreddit-experience-tree-provider button:has-text("Close")',
                            ]
                            
                            for sel in dismiss_selectors:
                                try:
                                    btn = p.locator(sel).first
                                    if btn.is_visible(timeout=300):
                                        btn.click()
                                        logger.info(f"[27] Dismissed obstacle: {sel}")
                                        p.wait_for_timeout(500)
                                        break
                                except:
                                    continue
                        except Exception as e:
                            logger.debug(f"[27] Obstacle dismiss pass completed: {e}")
                    # === PHASE 28: LOGIN / CAPTCHA WALL DETECTOR ===
                    def _check_for_login_wall(p):
                        """Detect login/captcha walls. Only triggers on BLOCKING walls, not nav 'Sign in' links."""
                        # TIER 1: Hard login indicators — always block
                        hard_indicators = [
                            'input[type="password"]',
                            'form[action*="login"]', 'form[action*="signin"]',
                            '#captcha', '.captcha', '[data-testid="login"]',
                            'input[name="session_key"]',  # LinkedIn login form
                            'input[name="username"]',
                        ]
                        
                        # TIER 2: Soft indicators — only block if page has NO real content
                        soft_indicators = [
                            'button:has-text("Sign in")',
                            'button:has-text("Log in")',
                            'a:has-text("Sign in")',
                            'a:has-text("Log in")',
                        ]
                        
                        # Check hard indicators first (always trigger)
                        is_hard_login = False
                        for sel in hard_indicators:
                            try:
                                el = p.locator(sel).first
                                if el.is_visible(timeout=500):
                                    is_hard_login = True
                                    break
                            except:
                                continue
                        
                        if is_hard_login:
                            # Definitely a login wall
                            pass
                        else:
                            # Check soft indicators, but ONLY if page is content-sparse
                            is_soft_login = False
                            for sel in soft_indicators:
                                try:
                                    el = p.locator(sel).first
                                    if el.is_visible(timeout=300):
                                        is_soft_login = True
                                        break
                                except:
                                    continue
                            
                            if not is_soft_login:
                                return False  # No login indicators at all
                            
                            # Soft indicator found — but does the page have real content?
                            try:
                                body_text = p.locator("body").inner_text(timeout=2000)
                                if len(body_text.strip()) > 500:
                                    # Page has substantial content — 'Sign in' is just a nav link
                                    logger.debug("[28e] Soft login indicator found but page has content. NOT a login wall.")
                                    return False
                            except:
                                pass
                        
                        if not is_hard_login:
                            return False  # Soft login with content = not blocked
                        
                        # --- LOGIN WALL DETECTED ---
                        site_name = p.url.split('/')[2] if '/' in p.url else p.url
                        logger.warning(f"[28] LOGIN WALL detected on {site_name}. Entering pause state...")
                        
                        # Speak to the user via TTS
                        try:
                            from voice.voice_manager import speak
                            speak(f"I've reached the {site_name} login page. Please log in or clear the security check so I can continue.")
                        except Exception as tts_err:
                            logger.debug(f"[28] TTS notification failed: {tts_err}")
                        
                        # === PAUSE & POLL: Wait for user to log in (max 60 seconds) ===
                        for wait_cycle in range(30):  # 30 x 2s = 60 seconds
                            p.wait_for_timeout(2000)
                            
                            # Check if login wall has cleared
                            still_login = False
                            for sel in hard_indicators:
                                try:
                                    el = p.locator(sel).first
                                    if el.is_visible(timeout=300):
                                        still_login = True
                                        break
                                except:
                                    continue
                            
                            if not still_login:
                                logger.info(f"[28] Login wall CLEARED after {(wait_cycle+1)*2}s. Resuming workflow...")
                                try:
                                    from voice.voice_manager import speak
                                    speak("Thank you! I can see you're logged in. Let me continue with your task.")
                                except:
                                    pass
                                # Wait for page to settle after login
                                try:
                                    p.wait_for_load_state('domcontentloaded', timeout=5000)
                                except:
                                    p.wait_for_timeout(2000)
                                return True  # Was login, now cleared
                            
                            if wait_cycle % 5 == 4:
                                logger.info(f"[28] Still waiting for login... ({(wait_cycle+1)*2}s elapsed)")
                        
                        # Timeout: user didn't log in within 60 seconds
                        logger.warning("[28] Login wait timeout (60s). Proceeding with current page state.")
                        return True  # Was login wall, may not have cleared

                    def _run_workflow_steps():
                        import re as _re
                        import urllib.parse as _up
                        import tempfile

                        goal = kwargs.get("goal", "")
                        url_before = page.url  # Track URL to verify navigation happened
                        step_num = [0]  # mutable counter for checkpoint screenshots

                        def _checkpoint_screenshot(label: str) -> str:
                            """Take a checkpoint screenshot and return base64. Non-fatal."""
                            try:
                                step_num[0] += 1
                                try:
                                    ss_dir = r"C:\VoxKage\Brain"
                                    os.makedirs(ss_dir, exist_ok=True)
                                except Exception:
                                    ss_dir = tempfile.gettempdir()
                                ss_path = os.path.join(ss_dir, f"step_{step_num[0]}_{label}.jpg")
                                page.screenshot(path=ss_path, type="jpeg", quality=65)
                                b64 = _encode_screenshot(ss_path)
                                logger.info(f"[P2] Checkpoint screenshot: {ss_path} ({len(b64)} b64 chars)")
                                return b64
                            except Exception as ce:
                                logger.warning(f"[P2] Checkpoint screenshot failed: {ce}")
                                return ""

                        def _current_domain() -> str:
                            try:
                                return page.url.split("/")[2].lower().replace("www.", "")
                            except Exception:
                                return ""

                        def _is_serp_domain(url: str) -> bool:
                            """Returns True if the URL is a search engine results page."""
                            serp_domains = ["duckduckgo.com", "google.com", "bing.com", "search.yahoo.com"]
                            return any(d in url for d in serp_domains)

                        # === PHASE 2: PRE-PROCESS — search engine shortcut + search_on_site ===
                        processed_steps = list(steps)
                        i = 0
                        while i < len(processed_steps):
                            cur = processed_steps[i]

                            # Inline search_on_site: convert to direct goto
                            if cur.get("action") == "search_on_site":
                                site = cur.get("site", "").lower().replace("www.", "")
                                query = cur.get("query", cur.get("text", ""))
                                encoded_q = _up.quote_plus(query)

                                # Find matching template
                                direct_url = None
                                for known_site, template in DIRECT_SEARCH_URLS.items():
                                    if site in known_site or known_site in site:
                                        direct_url = template.replace("{q}", encoded_q)
                                        break

                                if not direct_url:
                                    # Fallback: DuckDuckGo with site: operator
                                    direct_url = f"https://duckduckgo.com/?q={encoded_q}+site:{site}"

                                logger.info(f"[P2] search_on_site → direct URL: {direct_url}")
                                processed_steps[i] = {"action": "goto", "url": direct_url}

                            # Search engine shortcut: merge goto+type into direct search URL
                            elif cur.get("action") == "goto" and i + 1 < len(processed_steps):
                                nxt = processed_steps[i + 1]
                                if nxt.get("action") == "type":
                                    url_low = (cur.get("url") or "").lower()
                                    text = nxt.get("text", "")
                                    if text and any(se in url_low for se in ["google.com", "duckduckgo.com", "bing.com"]):
                                        encoded_q = _up.quote_plus(text)
                                        if "duckduckgo" in url_low:
                                            search_url = f"https://duckduckgo.com/?q={encoded_q}"
                                        elif "bing" in url_low:
                                            search_url = f"https://www.bing.com/search?q={encoded_q}"
                                        else:
                                            search_url = f"https://duckduckgo.com/?q={encoded_q}"
                                        logger.info(f"[P2] Search shortcut: {search_url}")
                                        processed_steps[i] = {"action": "goto", "url": search_url}
                                        processed_steps.pop(i + 1)
                                        continue
                            i += 1

                        # === STEP EXECUTION LOOP ===
                        for step in processed_steps:
                            act = step.get("action")

                            if act == "goto":
                                url = step.get("url")
                                if url and not url.startswith("http"):
                                    url = "https://" + url
                                # Use 'commit' wait_until — fires on first byte received,
                                # not after full DOM load. Slow sites (Steam, etc.) won't
                                # time out; the extraction phase waits for content instead.
                                # 60s timeout handles CDN-throttled / slow servers.
                                try:
                                    page.goto(url, wait_until="commit", timeout=60000)
                                except Exception as goto_err:
                                    # If even 'commit' times out, take a screenshot of whatever
                                    # loaded and continue rather than failing the whole workflow.
                                    logger.warning(f"[P2] goto timeout for {url}: {goto_err}. Continuing with partial load.")
                                try:
                                    page.wait_for_load_state("networkidle", timeout=10000)
                                except Exception:
                                    page.wait_for_timeout(3000)
                                url_before = page.url
                                _checkpoint_screenshot("goto")
                                logger.info(f"[P2] goto → landed on: {page.url}")
                                _check_for_login_wall(page)

                            elif act == "type":
                                selector = step.get("selector")
                                intent = step.get("intent")
                                text = step.get("text")
                                if text:
                                    el = None
                                    if intent:
                                        el = find_element_by_intent(page, intent)
                                    if not el and selector:
                                        try:
                                            if page.locator(selector).count() > 0:
                                                el = page.locator(selector).first
                                        except Exception:
                                            pass

                                    if el:
                                        human_type(page, el, text)
                                        page.wait_for_timeout(800)
                                        # Strategy 1: JS form submit
                                        try:
                                            page.evaluate("""
                                                (() => {
                                                    const input = document.querySelector('input[type="text"], input[name="q"], input[type="search"], textarea[name="q"]');
                                                    if (input && input.form) { input.form.submit(); return true; }
                                                    return false;
                                                })()
                                            """)
                                            logger.info("[P2] type: committed via JS form.submit()")
                                        except Exception as js_err:
                                            logger.info(f"[P2] type: JS submit failed ({js_err}), using Enter")
                                            page.keyboard.press("Escape")
                                            page.wait_for_timeout(300)
                                            page.keyboard.press("Enter")

                                        # Poll for navigation
                                        for _retry in range(6):
                                            try:
                                                page.wait_for_load_state("domcontentloaded", timeout=4000)
                                            except Exception:
                                                pass
                                            page.wait_for_timeout(1000)
                                            if page.url != url_before:
                                                logger.info(f"[P2] type: navigation confirmed → {page.url}")
                                                break
                                            if _retry == 1:
                                                try:
                                                    el.click()
                                                except Exception:
                                                    pass
                                                page.keyboard.press("Enter")
                                            elif _retry == 2:
                                                sub = find_element_by_intent(page, "Search") or find_element_by_intent(page, "Submit")
                                                if sub:
                                                    try:
                                                        sub.click()
                                                    except Exception:
                                                        pass
                                            elif _retry >= 3:
                                                page.keyboard.press("Enter")

                                        try:
                                            page.wait_for_load_state("networkidle", timeout=8000)
                                        except Exception:
                                            page.wait_for_timeout(2000)
                                        _checkpoint_screenshot("type_search")
                                    else:
                                        res_q.put(("ok",
                                            f"I reached {page.url} but I cannot find the search input field '{intent or selector}'. "
                                            f"The page may need scrolling or a different selector. "
                                            f"Tell the user what you see and ask them to try a different approach."))
                                        return

                            elif act == "click":
                                selector = step.get("selector")
                                intent = step.get("intent")
                                el = None
                                if intent:
                                    el = find_element_by_intent(page, intent)
                                if not el and selector:
                                    try:
                                        if page.locator(selector).count() > 0:
                                            el = page.locator(selector).first
                                    except Exception:
                                        pass

                                if el:
                                    import pyautogui, random
                                    try:
                                        x, y = pyautogui.position()
                                        pyautogui.moveTo(x + random.randint(-15, 15), y + random.randint(-15, 15), 0.2)
                                    except Exception:
                                        pass
                                    el.click()
                                    try:
                                        page.wait_for_load_state("networkidle", timeout=15000)
                                    except Exception:
                                        page.wait_for_timeout(3000)
                                    _checkpoint_screenshot("click")
                                else:
                                    logger.info(f"[P2] click: target '{intent or selector}' not found, proceeding.")

                            elif act == "click_best_link":
                                # === PHASE 2: DOMAIN-AWARE click_best_link ===
                                logger.info("[P2] click_best_link: scanning for best domain-aware result...")

                                # Extract target domain hint from the goal string
                                target_domain = None
                                goal_lower = goal.lower()
                                for known_domain in DIRECT_SEARCH_URLS.keys():
                                    domain_name = known_domain.split(".")[0]  # e.g. "amazon"
                                    if domain_name in goal_lower or known_domain in goal_lower:
                                        target_domain = known_domain
                                        break

                                logger.info(f"[P2] click_best_link: target_domain={target_domain}, current={page.url}")

                                clicked = False
                                clicked_url = ""

                                def _try_click_links(link_locator_str: str, prefer_domain: str = None):
                                    nonlocal clicked, clicked_url
                                    if clicked:
                                        return
                                    links = page.locator(link_locator_str).all()
                                    skip_patterns = ['login', 'sign', 'account', 'cart', 'help', 'about',
                                                     'privacy', 'terms', 'cookie', 'nav', 'menu', 'header',
                                                     'footer', 'logo', 'ads', 'sponsor']

                                    # Pass 1: prefer target domain if specified
                                    if prefer_domain:
                                        for link in links[:15]:
                                            try:
                                                href = link.get_attribute("href") or ""
                                                if prefer_domain in href and not any(s in href for s in skip_patterns):
                                                    link.scroll_into_view_if_needed()
                                                    link.click()
                                                    clicked = True
                                                    clicked_url = href
                                                    logger.info(f"[P2] click_best_link: DOMAIN MATCH → {href[:80]}")
                                                    return
                                            except Exception:
                                                continue

                                    # Pass 2: any good link
                                    for link in links[:20]:
                                        try:
                                            href = link.get_attribute("href") or ""
                                            text = link.inner_text().strip()
                                            if (len(text) > 10
                                                and href.startswith("http")
                                                and not _is_serp_domain(href)
                                                and not any(s in href.lower() for s in skip_patterns)
                                                and not any(s in text.lower() for s in skip_patterns)):
                                                link.scroll_into_view_if_needed()
                                                link.click()
                                                clicked = True
                                                clicked_url = href
                                                logger.info(f"[P2] click_best_link: generic → '{text[:40]}' {href[:60]}")
                                                return
                                        except Exception:
                                            continue

                                # Try DuckDuckGo selectors first (most common SERP)
                                _try_click_links('.result__a, .result__title a', prefer_domain=target_domain)
                                # Then Google
                                if not clicked:
                                    _try_click_links('div#search a h3', prefer_domain=target_domain)
                                # Then generic
                                if not clicked:
                                    _try_click_links('a[href]', prefer_domain=target_domain)

                                if clicked:
                                    try:
                                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                                    except Exception:
                                        pass
                                    try:
                                        page.wait_for_load_state("networkidle", timeout=8000)
                                    except Exception:
                                        page.wait_for_timeout(3000)
                                    _dismiss_obstacles(page)

                                    # === POST-CLICK SERP RE-LANDING CHECK ===
                                    landed_url = page.url
                                    if _is_serp_domain(landed_url):
                                        logger.warning(f"[P2] click_best_link: landed back on SERP ({landed_url}). Retrying next result...")
                                        # Try to click the second result
                                        page.go_back()
                                        page.wait_for_timeout(2000)
                                        ddg2 = page.locator('.result__a, .result__title a').all()
                                        if len(ddg2) > 1:
                                            try:
                                                ddg2[1].click()
                                                page.wait_for_load_state("domcontentloaded", timeout=10000)
                                                _dismiss_obstacles(page)
                                                logger.info(f"[P2] click_best_link: retry → {page.url}")
                                            except Exception as retry_e:
                                                logger.warning(f"[P2] Retry click failed: {retry_e}")

                                    _checkpoint_screenshot("click_best_link")
                                    logger.info(f"[P2] click_best_link: landed on {page.url}")
                                else:
                                    logger.warning("[P2] click_best_link: no suitable link found. Will extract current page.")

                            elif act == "extract_text":
                                pass  # handled by structured extractor below

                                
                        # === PHASE 27: DISMISS OBSTACLES BEFORE EXTRACTION ===
                        _dismiss_obstacles(page)
                        
                        # === PHASE 26c/27: DOMAIN-AGNOSTIC STRUCTURED EXTRACTOR ===
                        final_url = page.url
                        url_changed = final_url != url_before
                        logger.info(f"[27] Extraction phase. URL changed: {url_changed} ({url_before} -> {final_url})")
                        
                        # Give the page a moment to fully render dynamic content
                        page.wait_for_timeout(2000)
                        
                        # Extract the page text
                        try:
                            main_content = page.locator('main').first
                            if main_content.count() > 0 and main_content.is_visible(timeout=1000):
                                page_text = main_content.inner_text()
                            else:
                                page_text = page.locator("body").inner_text()
                        except:
                            page_text = page.locator("body").inner_text()
                        
                        # === PHASE 28e: FRESH PROFILE RETRY ===
                        # On fresh profiles, Amazon/LinkedIn may show cookie consent or splash.
                        # If no content found, dismiss obstacles, reload, and extract again.
                        price_indicators = ['₹', '$', '€', '£', 'Rs.', 'Rs ', 'USD', 'INR']
                        data_indicators = ['rating', 'stars', 'reviews', 'score', 'upvote', 'views', 'followers']
                        has_prices = any(sym in page_text for sym in price_indicators)
                        has_data = any(kw.lower() in page_text.lower() for kw in data_indicators)
                        
                        if not has_prices and not has_data and len(page_text.strip()) < 500:
                            logger.info("[28e] Sparse content on first try. Reloading page for fresh profile...")
                            _dismiss_obstacles(page)
                            page.reload(wait_until="domcontentloaded", timeout=15000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=8000)
                            except:
                                page.wait_for_timeout(3000)
                            _dismiss_obstacles(page)
                            page.wait_for_timeout(2000)
                            
                            # Re-extract
                            try:
                                main_content = page.locator('main').first
                                if main_content.count() > 0 and main_content.is_visible(timeout=1000):
                                    page_text = main_content.inner_text()
                                else:
                                    page_text = page.locator("body").inner_text()
                            except:
                                page_text = page.locator("body").inner_text()
                            
                            final_url = page.url
                            has_prices = any(sym in page_text for sym in price_indicators)
                            has_data = any(kw.lower() in page_text.lower() for kw in data_indicators)
                            logger.info(f"[28e] After reload: {len(page_text)} chars, prices={has_prices}, data={has_data}")
                        
                        # === PRICE / DATA VALIDATION ===
                        
                        if not has_prices and not has_data and not url_changed:
                            # We're likely still on the suggestions dropdown
                            logger.warning("[26c] No price/data indicators found AND URL didn't change. Likely still on suggestions.")
                            res_q.put(("ok", 
                                "WARNING: I typed the query but the page did not navigate to results. "
                                "I see only search suggestions, not actual product listings or data. "
                                "Tell the user: 'I typed your search but the results page didn't load. Let me try again.' "
                                "Do NOT summarize suggestions as if they were real results."))
                            return
                        
                        # === STRUCTURED COMPARISON GRID ===
                        # Try to extract structured blocks for product/result items
                        structured_items = []
                        
                        # Split page text into lines and look for price-bearing blocks
                        lines = page_text.split('\n')
                        current_block = []
                        for line in lines:
                            stripped = line.strip()
                            if not stripped:
                                if current_block:
                                    block_text = ' | '.join(current_block)
                                    # Check if this block has a price indicator
                                    if any(sym in block_text for sym in price_indicators):
                                        structured_items.append(block_text)
                                    current_block = []
                                continue
                            current_block.append(stripped)
                        # Don't forget the last block
                        if current_block:
                            block_text = ' | '.join(current_block)
                            if any(sym in block_text for sym in price_indicators):
                                structured_items.append(block_text)
                        
                        # Build the structured response
                        if structured_items:
                            top_items = structured_items[:5]
                            comparison_text = "\n".join([f"Item {i+1}: {item[:300]}" for i, item in enumerate(top_items)])
                            response = (
                                f"I have reached the results page at {final_url}.\n"
                                f"I found {len(structured_items)} items with prices. Here are the top {len(top_items)}:\n\n"
                                f"{comparison_text}\n\n"
                                f"Summarize these items for the user in a natural, conversational way. "
                                f"For each item, mention the product name and price clearly. "
                                f"Format like: 'I found [N] options. Option 1 is [Name] at [Price]...' "
                                f"Keep it concise and focus only on the actual data."
                            )
                            # === PHASE 28e: PERSIST TO MEMORY ===
                            memory_items = [{"index": i+1, "text": item[:300], "url": final_url} for i, item in enumerate(top_items)]
                            save_last_search(memory_items)
                        else:
                            # No structured items, but we have data — use the raw text
                            # Try to extract line-based items for memory
                            page_lines = [l.strip() for l in page_text.split('\n') if l.strip() and len(l.strip()) > 10]
                            memory_items = [{"index": i+1, "text": line[:200], "url": final_url} for i, line in enumerate(page_lines[:10])]
                            if memory_items:
                                save_last_search(memory_items)
                            
                            response = (
                                f"I have navigated to {final_url}.\n"
                                f"Here is the content I found:\n{page_text[:3000]}\n\n"
                                f"Carefully read this extracted webpage text and provide a helpful, "
                                f"natural response to the user's original query based on it. "
                                f"Keep it concise and focus only on the actual data."
                            )
                        
                        res_q.put(("ok", response))
                        
                    try:
                        _run_workflow_steps()
                    except Exception as loop_e:
                        if "closed" in str(loop_e).lower() or "target page" in str(loop_e).lower() or "context" in str(loop_e).lower():
                            logger.warning(f"Browser closed mid-workflow. Rebooting context and retrying: {loop_e}")
                            _context = None
                            page = _ensure_page()
                            # Try one more time after reboot
                            _run_workflow_steps()
                        else:
                            raise loop_e
                            
            except Exception as e:
                err_url = "unknown"
                try:
                    if _active_page and not _active_page.is_closed():
                        err_url = _active_page.url
                except Exception:
                    pass
                error_msg = (
                    f"Browser workflow failed at URL: {err_url}. "
                    f"Error: {str(e) or 'Unknown error (page may have crashed or navigated away)'}. "
                    f"Tell the user what happened and offer to try again with a different approach."
                )
                logger.error(f"Playwright Worker Loop Error: {e} (URL: {err_url})")
                if res_q:
                    res_q.put(("error", error_msg))

def _ensure_worker():
    global _pw_thread
    if _pw_thread is None or not _pw_thread.is_alive():
        _pw_thread = threading.Thread(target=_pw_worker, daemon=True)
        _pw_thread.start()

def _dispatch_task(action: str, kwargs: dict):
    """Safely blocks and waits for the worker thread to perform playwright tasks."""
    _ensure_worker()
    res_q = queue.Queue()
    _pw_queue.put((action, kwargs, res_q))
    try:
        status, res = res_q.get(timeout=150)  # 150s: generous for SPA sites (Epic Games ~60s load + extraction)
    except Exception:
        raise Exception(
            "Browser workflow timed out after 90 seconds. "
            "The page may be loading slowly. Tell the user and offer to retry."
        )
    if status == "error":
        raise Exception(res)
    return res

def get_browser_state():
    """
    Captures the current browser state: URL, page title, visible text, and a screenshot.
    The screenshot is saved locally and shown on the HUD for the user.
    The LLM receives the URL and page text so it can decide the next action.
    """
    _ensure_worker()
    res_q = queue.Queue()
    _pw_queue.put(("get_state", {}, res_q))
    try:
        status, res = res_q.get(timeout=20)
    except Exception:
        return (
            "BROWSER STATE ERROR: Timed out waiting for browser response. "
            "The browser may not be open yet. Try using execute_browser_workflow to navigate to a page first."
        )

    if status == "error":
        return f"BROWSER STATE ERROR: {res}. Use execute_browser_workflow to navigate to a page first."

    url = res.get("url", "unknown")
    title = res.get("title", "unknown")
    page_text = res.get("page_text_snippet", "")
    screenshot_path = res.get("screenshot_path", "")
    is_login_wall = res.get("is_login_wall", False)
    is_captcha = res.get("is_captcha", False)

    # Show screenshot in the HUD so the user can see it visually
    try:
        from voice.voice_manager import log_to_hud
        hud_msg = f"📸 Browser snapshot — URL: {url}\nTitle: {title}"
        if is_login_wall:
            hud_msg += "\n⚠️ LOGIN WALL DETECTED"
        if is_captcha:
            hud_msg += "\n⚠️ CAPTCHA DETECTED"
        log_to_hud("VoxKage", hud_msg)
    except Exception:
        pass

    # Build the actionable text response for the LLM
    status_flags = ""
    if is_login_wall:
        status_flags = "\n⚠️ STATUS: LOGIN_REQUIRED — Tell the user they need to log in before continuing."
    elif is_captcha:
        status_flags = "\n⚠️ STATUS: CAPTCHA_DETECTED — Tell the user to solve the captcha in the browser."
    else:
        status_flags = "\nSTATUS: Page loaded normally. Continue with your task based on the content below."

    page_text_clean = page_text.strip() if page_text.strip() else "No readable text found on this page."
    screenshot_b64 = res.get("screenshot_b64", "")

    text_result = (
        f"=== BROWSER STATE CAPTURED ===\n"
        f"URL: {url}\n"
        f"Title: {title}\n"
        f"{status_flags}\n\n"
        f"--- PAGE CONTENT (first 800 chars) ---\n"
        f"{page_text_clean}\n"
        f"--- END OF PAGE CONTENT ---\n\n"
        f"INSTRUCTION: Based on what you see in the screenshot and the page content above, decide your next action. "
        f"If the content contains what the user asked for, summarize it conversationally. "
        f"If the page is not what you expected (e.g. still on homepage or search results), "
        f"use execute_browser_workflow to navigate deeper. NEVER go silent after receiving this."
    )

    # Return a special marker dict that llm_client.py uses to inject the image into the vision pipeline.
    # String casting (str()) of this dict will be caught and handled in llm_client.
    return {"__vision__": True, "text": text_result, "screenshot_b64": screenshot_b64}


def browse_and_extract(url: str, query: str):
    """
    Navigate to a URL (or DuckDuckGo), search for the query, and extract the resulting text
    so the LLM can read it and summarize it for the user.
    """
    logger.info(f"Using Playwright to browse {url} for query: {query}")
    try:
        return _dispatch_task("browse", {"url": url, "query": query})
    except Exception as e:
        logger.error(f"Error in browse_and_extract: {e}")
        return f"Failed to extract information: {str(e)}"

def search_media_options(platform: str, query: str):
    """
    Navigate to a media platform (like YouTube), search for the query, and extract the top 5 videos.
    """
    logger.info(f"Using Playwright to search '{query}' on {platform}")
    if "youtube" not in platform.lower():
        return f"Platform '{platform}' is not supported for interactive search right now."
        
    try:
        results = _dispatch_task("search_youtube", {"query": query})
        
        # Log to HUD
        try:
            from voice.voice_manager import log_to_hud
            log_text = "Found YouTube Videos:\n" + "\n".join([f"{r['number']}: {r['title']}" for r in results])
            log_to_hud("VoxKage", log_text)
        except Exception:
            pass
            
        return (
            f"Found 5 videos. Read these options to the user clearly (e.g. 'I found 5 videos. 1: [Title 1], 2: [Title 2]... Which one should I play?').\n"
            f"OPTIONS: {results}"
        )
    except Exception as e:
        logger.error(f"Error in search_media_options: {e}")
        return f"Failed to search media: {str(e)}"

def play_media_selection(number: int):
    """
    Plays the chosen video number in the currently active browser page.
    """
    logger.info(f"Navigating to selected media number: {number}")
    try:
        return _dispatch_task("play", {"number": number})
    except Exception as e:
        logger.error(f"Error playing selection: {e}")
        return f"Failed to play selection: {str(e)}"

def control_media_web(action: str):
    """
    Controls media (YouTube) in the active browser page.
    """
    logger.info(f"Executing web media control: {action}")
    try:
        return _dispatch_task("control_media_web", {"action": action})
    except Exception as e:
        logger.error(f"Error executing web media control: {e}")
        return f"Failed to execute {action} on web: {str(e)}"


def execute_browser_workflow_sync(goal: str, steps: list):
    """
    Executes a flexible list of Playwright steps to accomplish a multi-step workflow.
    """
    logger.info(f"Executing browser workflow: {goal} with {len(steps)} steps.")
    try:
        return _dispatch_task("workflow", {"steps": steps})
    except Exception as e:
        logger.error(f"Error executing browser workflow: {e}")
        return f"Workflow Error: {str(e)}"


def agent_step_sync(arguments: dict):
    """
    === PHASE 3: AGENT STEP ===
    Execute ONE atomic browser action + take screenshot.
    Returns vision dict {"__vision__": True, "text": "...", "screenshot_b64": "..."}.
    === PHASE 5: Browser crash recovery ===
    If the browser context dies, auto-recover by re-launching and retrying once.
    """
    action = arguments.get('action', 'unknown')
    logger.info(f"Agent step: {action}")
    try:
        return _dispatch_task("agent_step", arguments)
    except Exception as e:
        err_str = str(e).lower()
        # Fix 5: Detect browser crash and auto-recover
        if any(keyword in err_str for keyword in ["closed", "crashed", "target page", "context has been", "browser has been"]):
            logger.warning(f"[agent_step] Browser context died — forcing re-launch ({action})")
            # Force _ensure_page to detect dead context and re-launch
            # by calling get_browser_state which triggers _ensure_page
            try:
                _recovery_state = get_browser_state()
                logger.info(f"[agent_step] Browser re-launch succeeded, retrying {action}")
                # Retry the action once with fresh browser
                return _dispatch_task("agent_step", arguments)
            except Exception as retry_err:
                logger.error(f"[agent_step] Browser re-launch failed: {retry_err}")
                return {
                    "__vision__": True,
                    "text": f"Browser crashed during {action} and re-launch also failed: {str(retry_err)[:300]}. The browser will auto-recover on the next attempt.",
                    "screenshot_b64": ""
                }
        # Non-crash error — return normal failure
        logger.error(f"Error in agent_step: {e}")
        return {"__vision__": True, "text": f"agent_step failed: {str(e)}", "screenshot_b64": ""}
