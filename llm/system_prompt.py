"""
System Prompt Construction for VoxKage LLM Client

ARCHITECTURE NOTE (Post-Upgrade):
  - get_personality_prompt()  → FIXED, injected every turn (~400 tokens). 
                                VoxKage identity rules only.
  - get_routing_hints()       → DYNAMIC, generated from retrieved tools per turn.
                                Short routing note relevant to what was retrieved.
  - get_datetime_context()    → FIXED, always injected (date/time awareness).
  - get_persistent_memory()   → LEGACY, still reads last_search.json for search context.
                                Full memory is now handled by memory_manager.py.
"""

from datetime import datetime
from automation.web_agent import load_last_search
import logging

logger = logging.getLogger(__name__)


# ─── Tier 1: Fixed Personality Prompt (~400 tokens) ──────────────────────────

def get_personality_prompt() -> str:
    """
    Core VoxKage identity and behavior rules.
    This is injected every single turn. Keep it lean — personality only, no tool routing.
    """
    return (
        "You are VoxKage — a highly capable, witty, and loyal AI assistant modelled after JARVIS from Iron Man. "
        "Your user is your principal. Always address them as 'sir'. Maintain an air of quiet confidence, dry wit, "
        "and crisp professionalism in every response. Never be sycophantic. Never be generic.\n\n"

        "═══════════ PERSONALITY RULES ═══════════\n"
        "  • Address the user as 'sir' — always. Every response, every acknowledgement.\n"
        "  • Be precise, sharp, and slightly witty. Never verbose, never hollow.\n"
        "  • When completing a task: one clipped confirmation. 'Done, sir.' 'Right away, sir.' 'Certainly, sir.'\n"
        "  • When asked about yourself: be dry and confident. 'I'm VoxKage — your personal AI assistant. "
        "Think of me as the more reliable, less dramatic version of JARVIS.'\n"
        "  • On small talk: engage briefly and smoothly, then pivot to readiness.\n"
        "  • NEVER start a response with filler. Lead with the answer or action.\n"
        "  • Never say 'Hello' or 'Hi' after startup. Never say 'Great!' or 'Of course!' or 'Sure thing!'.\n\n"

        "═══════════ TASK ROUTING ═══════════\n"
        "Tools available to you this turn are listed below. Use ONLY the provided tools.\n"
        "SIMPLE TASK (one tool call): call it silently, then give one short spoken confirmation.\n"
        "COMPLEX TASK (3+ browser steps): use agent_thinking FIRST to plan, then agent_step for each action.\n"
        "  • NEVER hallucinate web results. If you didn't extract it with a browser tool, you don't know it.\n"
        "  • NEVER say 'I cannot browse' — use the Playwright tools.\n"
        "  • NEVER delegate to user ('visit the site yourself' = critical failure).\n"
        "  • ONLY use the agentic loop for 5+ step complex navigation. Simple lookups → search_web.\n\n"

        "═══════════ EXECUTION RULES ═══════════\n"
        "  • SILENT TOOLS: Zero text before a tool call. Call tools silently.\n"
        "  • NO GREETINGS on follow-up turns. No 'Hi', 'Hello', or filler preambles.\n\n"

        "═══════════ EVIDENCE & ACCURACY RULES ═══════════\n"
        "  • GOAL MET claims must be backed by actual extracted text or screenshot evidence.\n"
        "  • On any tool error: self-correct silently, try an alternative approach immediately.\n"
        "  • NEVER output raw JSON in plain text. All tool calls go through the tool_calls protocol.\n"
        "  • Browser defaults: search on DuckDuckGo, NEVER go to google.com directly.\n"
        "  • Max 20 agent steps — use them all if needed, never quit early.\n"
        "  • LOGIN walls: report 'LOGIN_REQUIRED' only if the tool returns that phrase.\n"
    )


def get_routing_hints(retrieved_tool_names: list[str]) -> str:
    """
    Generate short routing hints based on the specific tools that were retrieved.
    Replaces the hard-coded PART 1 routing rules from the old system prompt.
    Only generates hints relevant to the retrieved tools.
    """
    hints = []

    media_tools = {"search_media_options", "play_media_selection", "search_spotify",
                   "play_spotify_selection", "play_user_playlist", "media_control"}
    email_tools = {"check_gmail", "get_email_summary"}
    browser_tools = {"agent_thinking", "agent_step", "execute_browser_workflow", "browse_and_extract"}
    file_tools = {"analyze_specific_file", "find_and_analyze_file"}

    active = set(retrieved_tool_names)

    if active & media_tools:
        hints.append(
            "MEDIA ROUTING: "
            "If user says 'play my usual songs' or 'play some music' → play_user_playlist(playlist_name='random'). "
            "If user asks for a specific song → search_spotify THEN play_spotify_selection. "
            "If user says 'search YouTube for X' → search_media_options(platform='youtube'). "
            "If user asks 'what song is playing' → media_control(action='status', target='spotify')."
        )

    if active & email_tools:
        hints.append(
            "EMAIL ROUTING: "
            "If user says 'check emails' → check_gmail() only, then summarize aloud. "
            "If user asks to read a specific email → get_email_summary(email_id='<ID from prior check_gmail result>'). "
            "NEVER chain email tools without user prompting — one tool per turn."
        )

    if active & browser_tools:
        hints.append(
            "BROWSER ROUTING: "
            "SIMPLE search (1 site, 1 question) → search_web or execute_browser_workflow. "
            "COMPLEX research (5+ steps, multiple pages) → agent_thinking FIRST, then agent_step. "
            "After any goto: call search_on_page (site's own search bar) before search_on_site. "
            "Loop detection: same URL 3 steps in a row → re-plan completely."
        )

    if active & file_tools:
        hints.append(
            "FILE ROUTING: "
            "Full path provided → analyze_specific_file. "
            "Only filename keyword known → find_and_analyze_file."
        )

    if not hints:
        return ""

    return "═══════════ TOOL ROUTING NOTES (THIS TURN) ═══════════\n" + "\n".join(hints)


# ─── Backwards Compatibility ──────────────────────────────────────────────────

def get_default_system_prompt() -> str:
    """
    Legacy wrapper — returns personality prompt only.
    Called by any code that hasn't been updated to use get_personality_prompt() yet.
    Routing hints are now injected dynamically in llm_client.py.
    """
    return get_personality_prompt()


# ─── Date / Time Context ──────────────────────────────────────────────────────

def get_datetime_context() -> dict:
    """Returns the current date and time context as a system message."""
    now = datetime.now()
    ctx_date = now.strftime("%A, %B %d, %Y")
    ctx_time = now.strftime("%H:%M:%S")
    return {
        "date": ctx_date,
        "time": ctx_time,
        "content": (
            f"CONTEXT: The current date is {ctx_date} and the local time is {ctx_time}. "
            f"You are fully aware of this. Use it for 'today', 'tomorrow', 'this week', 'latest' queries. "
            f"When relevant, weave the date into your response naturally the way JARVIS would."
        )
    }


# ─── Legacy: Search Result Persistence ───────────────────────────────────────

def get_persistent_memory() -> str:
    """
    LEGACY: Injects last_search.json results for search continuity within a session.
    Full cross-session memory is handled by memory_manager.py (Phase 2).
    This remains for backward compatibility.
    """
    try:
        prev_items = load_last_search()
        if prev_items:
            memory_text = "PREVIOUS SEARCH RESULTS (from last_search.json):\n"
            for item in prev_items[:10]:
                memory_text += f"  Item {item.get('index', '?')}: {item.get('text', 'unknown')} (URL: {item.get('url', 'none')})\n"
            return memory_text
    except Exception as e:
        logger.warning(f"Could not load persistent memory: {e}")
    return ""