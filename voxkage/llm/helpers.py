import json
import re
from datetime import datetime
from voxkage.llm.tool_registry import execute_tool_call
import logging
import os

logger = logging.getLogger(__name__)

def log_to_hud(sender: str, text: str):
    """Universal helper to append chat strings to Settings GUI HUD log."""
    try:
        from voxkage.paths import voxkage_dir
        log_path = os.path.join(voxkage_dir(), ".hud_log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"sender": sender, "text": text, "timestamp": datetime.now().isoformat()}) + "\n")
    except Exception as e:
        logger.error(f"Failed to write to HUD log: {e}")

class SentenceStreamer:
    """
    Utility class to buffer streaming LLM tokens into full sentences
    and log them to the HUD in real-time.
    """
    def __init__(self):
        self.buffer = ""
        self.terminators = {'.', '!', '?'}

    def add_token(self, token: str):
        self.buffer += token
        if self.buffer.strip().upper().startswith("IGNORE"):
            return
        if any(t in self.buffer for t in self.terminators):
            match = re.search(r'([.!?])\s+', self.buffer)
            if match:
                split_idx = match.end()
                sentence = self.buffer[:split_idx].strip()
                self.buffer = self.buffer[split_idx:]
                if sentence:
                    log_to_hud("VoxKage", sentence)

    def flush(self):
        buf = self.buffer.strip()
        if buf and not buf.upper().startswith("IGNORE"):
            log_to_hud("VoxKage", buf)
            self.buffer = ""


def _extract_json_tool_call(text: str):
    """
    Extract a complete JSON tool call using brace-depth counting.
    Handles nested JSON that defeats simple .*? regex patterns.
    Module-level so the agentic loop can import it directly.
    Returns (parsed_dict, raw_str) or (None, None).
    """
    for pat in ['"name"', "'name'"]:
        idx = text.find(pat)
        if idx == -1:
            continue
        brace_start = text.rfind('{', 0, idx)
        if brace_start == -1:
            continue
        depth = 0
        i = brace_start
        while i < len(text):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[brace_start:i+1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and 'name' in parsed:
                            return parsed, candidate
                    except Exception:
                        pass
                    break
            i += 1
    return None, None


def _parse_text_intent(text: str, user_prompt: str) -> dict | None:
    """
    Extract tool-call intent from plain LLM text when all JSON rescue fails.
    Only called after MAX_TEXT_RETRIES consecutive text responses.
    Returns {"tool": str, "args": dict} or None.
    """
    text_lower = text.lower()

    # 0) Fix: Parse agent_step(action='X', query='Y') format from LLM text
    # Handles: agent_step(action='search_on_page', query='qwen3.5:4b')
    _step_match = re.search(
        r"agent_step\s*\(\s*action\s*=\s*['\"](\w+)['\"]\s*"
        r"(?:,\s*query\s*=\s*['\"]([^'\"]+)['\"]\s*)?"
        r"(?:,\s*url\s*=\s*['\"]([^'\"]+)['\"]\s*)?"
        r"(?:,\s*site\s*=\s*['\"]([^'\"]+)['\"]\s*)?"
        r"\)",
        text, re.IGNORECASE
    )
    if _step_match:
        _action = _step_match.group(1)
        _query = _step_match.group(2)
        _url = _step_match.group(3)
        _site = _step_match.group(4)
        _args = {"action": _action, "goal": user_prompt[:100]}
        if _query:
            _args["query"] = _query
        if _url:
            _args["url"] = _url
        if _site:
            _args["site"] = _site
        return {"tool": "agent_step", "args": _args}

    # 1) Explicit navigation verb + domain
    url_match = re.search(
        r'(?:goto|navigate(?:\s+to)?|go\s+to|open|visit|searching\s+on)\s+'
        r'([a-zA-Z][a-zA-Z0-9\-]*\.[a-zA-Z]{2,}(?:[/\w\-\.?=&]*)?)',
        text, re.IGNORECASE
    )
    if url_match:
        return {"tool": "agent_step",
                "args": {"action": "goto", "url": url_match.group(1).rstrip('./'),
                          "goal": user_prompt[:100]}}

    # 2) Implicit domain reference ("on openrouter.ai", "at github.com")
    domain_match = re.search(
        r'\b(?:on|at|from|using)\s+([a-zA-Z][a-zA-Z0-9\-]*\.[a-zA-Z]{2,})',
        text, re.IGNORECASE
    )
    if domain_match:
        return {"tool": "agent_step",
                "args": {"action": "goto", "url": domain_match.group(1).rstrip('./'),
                          "goal": user_prompt[:100]}}

    # 3) Search intent
    search_match = re.search(
        r'(?:search(?:ing)?|find(?:ing)?|look(?:ing)?\s+for|query(?:ing)?)\s+(?:for\s+)?'
        r'(.+?)(?:\.|,|\s+on\s+|\s+in\s+|$)',
        text, re.IGNORECASE
    )
    if search_match:
        _q = search_match.group(1).strip()[:200]
        if len(_q) > 3:
            return {"tool": "agent_step",
                    "args": {"action": "search_on_page", "query": _q, "goal": user_prompt[:100]}}

    # 4) Extract/read intent
    if any(w in text_lower for w in ['extract', 'read the page', 'scrape', 'get the text', 'extract_text']):
        return {"tool": "agent_step",
                "args": {"action": "extract_text", "goal": user_prompt[:100]}}

    # 5) Completion signals
    if any(w in text_lower for w in ['found the', 'here are', 'here is the', 'i found', 'task is complete', 'i have found']):
        return {"tool": "agent_thinking",
                "args": {"thought": f"GOAL MET: {text[:300]}", "next_action": "done"}}

    # 6) Last resort — any URL-like token in the text
    any_url = re.search(r'\b([a-zA-Z][a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:[/\w\-\.?=&]*)?)\b', text)
    if any_url:
        raw_url = any_url.group(1).rstrip('./')
        if len(raw_url) > 4:
            return {"tool": "agent_step",
                    "args": {"action": "goto", "url": raw_url, "goal": user_prompt[:100]}}

    return None


def _detect_browser_intent(llm_text: str, user_prompt: str) -> bool:
    """
    Returns True if the user's prompt clearly requires a browser action
    but the LLM returned text instead of calling a tool.
    Only fires on the FIRST LLM response before any tools have run.
    """
    prompt_lower = user_prompt.lower()
    text_lower = llm_text.lower()

    # Safe starters — Jarvis-style responses that are NEVER hallucination
    _SAFE_STARTERS = [
        "good morning", "good afternoon", "good evening", "good night",
        "certainly", "right away", "understood", "of course",
        "i've analyzed", "i've completed", "here is the summary",
        "i found ", "here are ", "the results", "based on",
        "i'm voxkage", "voxkage here", "standing by", "online and",
        "at your service", "happy to help", "✅", "done",
    ]
    if any(text_lower.startswith(s) or s in text_lower[:120] for s in _SAFE_STARTERS):
        return False

    # Trigger verbs in the user prompt that imply browser use
    browser_verbs = [
        'search', 'find', 'look up', 'navigate', 'go to',
        'show me', 'get me', 'browse', 'visit', 'load',
        'what is on', 'latest', 'price of', 'buy', 'shop',
    ]
    # Known web domains/keywords in the prompt that imply browsing
    web_keywords = [
        'youtube', 'steam', 'reddit', 'amazon', 'flipkart',
        'wikipedia', 'github', 'linkedin',
        '.com', '.in', '.org', 'website', 'site', 'online',
        'internet', 'url', 'link',
    ]

    has_browser_verb = any(v in prompt_lower for v in browser_verbs)
    has_web_keyword = any(k in prompt_lower for k in web_keywords)

    # Signals that the LLM answered from memory without evidence
    hallucination_signals = [
        'release date', 'developer:', 'publisher:', 'available for',
        '₹', '$', 'price:', 'out of 5', 'rating:', 'metacritic',
        'the latest version', 'as of my knowledge',
    ]
    looks_hallucinated = any(s in text_lower for s in hallucination_signals)

    # STRICT rule: only trigger if BOTH a web verb AND a web keyword are present
    # AND the response looks like hallucinated data (or explicitly mentions a domain)
    if not (has_browser_verb and has_web_keyword):
        return False

    return looks_hallucinated or len(llm_text) > 200



def clear_session_memory():
    """Wipe all per-session memory on startup so old context never bleeds into new sessions."""
    # Import _conversation_history from llm_client to avoid circular import issues
    from voxkage.llm.llm_client import _conversation_history
    _conversation_history.clear()
    # Wipe last_search.json so stale results don't corrupt new sessions
    try:
        import os
        from voxkage.paths import data_dir
        search_file = os.path.join(data_dir(), 'last_search.json')
        if os.path.exists(search_file):
            with open(search_file, 'w', encoding='utf-8') as f:
                f.write('{"items": [], "timestamp": 0}')
            logger.info("Session start: last_search.json wiped clean.")
    except Exception as e:
        logger.warning(f"Could not wipe last_search.json: {e}")
    # === PHASE 3: CLEAR SCREENSHOTS ON STARTUP ===
    try:
        from voxkage.automation.web_agent import clear_session_screenshots
        clear_session_screenshots()
    except Exception as e:
        logger.warning(f"Could not clear session screenshots: {e}")
    # === PHASE 4: CLEAR TASK FILES ON STARTUP ===
    try:
        from voxkage.llm.task_tracker import cleanup_all_tasks
        cleanup_all_tasks()
    except Exception as e:
        logger.warning(f"Could not cleanup task files: {e}")