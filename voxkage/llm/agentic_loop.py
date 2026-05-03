"""
Agentic Loop Implementation for VoxKage LLM Client
Contains the Phase 5: LangGraph Agentic State Machine

Brain: Gemini CLI (primary, 20s timeout, 1 attempt) → Gemini/Qwen (instant fallback)

Key fixes in this version:
  - Gemini agentic calls use 20s timeout (short prompts respond in <10s) and fall
    to Gemini immediately on first timeout (no 3×45s silence).
  - AgentState tracks current_url and last_error so the planner always knows where
    the browser is and what went wrong on the previous step.
  - agent_step args cheatsheet injected into every planning prompt so Gemini always
    generates the correct {action, goal, url/query/...} shape.
  - tool_node detects pydantic validation errors and injects a corrective message
    so the planner knows to fix its args on the next attempt.
  - Browser session is persistent (Playwright context stays alive); current_url is
    extracted from every agent_step/search_web result and stored in state.
"""
import asyncio
import json
import re
import os
from typing import Dict, Any, List, Optional, AsyncGenerator, TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END, START
from llm.mcp_dispatcher import dispatch_tool_call as execute_tool_call
from llm.helpers import SentenceStreamer, log_to_hud
import logging

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 20

# ── Agent Step quick-reference injected into every Gemini planning prompt ──────
# This is the single most important context for avoiding validation errors.
_AGENT_STEP_CHEATSHEET = """
AGENT_STEP ACTION REFERENCE (use agent_step for all browser interactions):
  Navigate to URL:   {"tool":"agent_step","args":{"action":"goto","goal":"<goal>","url":"https://..."}}
  Search a site:     {"tool":"agent_step","args":{"action":"search_on_site","goal":"<goal>","site":"domain.com","query":"<q>"}}
  Type in searchbar: {"tool":"agent_step","args":{"action":"search_on_page","goal":"<goal>","query":"<q>"}}
  Click element:     {"tool":"agent_step","args":{"action":"click","goal":"<goal>","intent":"<button label>"}}
  Scroll page:       {"tool":"agent_step","args":{"action":"scroll","goal":"<goal>","direction":"down"}}
  Extract text:      {"tool":"agent_step","args":{"action":"extract_text","goal":"<goal>"}}
  Web search:        {"tool":"search_web","args":{"query":"<search query>"}}
ALWAYS include "action" and "goal" keys in agent_step calls. No exceptions.
""".strip()

_GUI_STEP_CHEATSHEET = """
GUI_STEP ACTION REFERENCE (use gui_step for all LOCAL DESKTOP interactions):
  See current screen:    {"tool":"gui_step","args":{"action":"screenshot","goal":"<goal>"}}
  Focus an app:          {"tool":"gui_step","args":{"action":"focus","goal":"<goal>","app":"VS Code"}}
  Click by description:  {"tool":"gui_step","args":{"action":"find_and_click","goal":"<goal>","description":"Install button next to Prettier","app":"VS Code"}}
  Click coordinates:     {"tool":"gui_step","args":{"action":"click","goal":"<goal>","x":820,"y":340}}
  Type text:             {"tool":"gui_step","args":{"action":"type","goal":"<goal>","text":"Prettier"}}
  Press hotkey:          {"tool":"gui_step","args":{"action":"hotkey","goal":"<goal>","keys":"ctrl+shift+x"}}
  Press single key:      {"tool":"gui_step","args":{"action":"key","goal":"<goal>","key":"enter"}}
  Scroll:                {"tool":"gui_step","args":{"action":"scroll","goal":"<goal>","direction":"down","amount":3}}
  Open file in app:      {"tool":"gui_step","args":{"action":"open_file","goal":"<goal>","file_path":"C:\\\\path\\\\to\\\\file.txt","app":"VS Code"}}
  Read screen text:      {"tool":"gui_step","args":{"action":"read_screen","goal":"<goal>"}}
  Plan complex task:     {"tool":"gui_thinking","args":{"goal":"<goal>","plan":"1. Focus app\\n2. Hotkey\\n3. Type\\n4. Click"}}
  Get open files:        {"tool":"get_open_files","args":{}}
  See desktop + windows: {"tool":"get_desktop_state","args":{}}
ALWAYS include "action" and "goal" in gui_step. Use find_and_click (not raw x,y) unless coordinates are known.
GUI WORKFLOW: gui_thinking → get_desktop_state → focus → find_and_click → screenshot to verify → repeat
""".strip()

# Patterns stripped from user-facing output
_AGENT_NOISE_PATTERNS = [
    r"FAILING TO CALL A TOOL[^\n]*",
    r"━+\s*YOU MUST CALL[^\n]*",
    r"━━━[^\n]*━━━[^\n]*",
    r"Do NOT write any text[^\n]*",
    r"Call exactly one tool now[^\n]*",
    r"THINKING LOGGED:[^\n]*",
    r"NEXT PLANNED:[^\n]*",
    r"→ If next is[^\n]*",
    r"\u2192 If next is[^\n]*",
    r"→ If task is done[^\n]*",
    r"→ If the task[^\n]*",
    r"tool with exact parameters[^\n]*",
    r"<summary>[^<]*</summary>",
    r"<summary>[^\n]*",
    r"\[INTERNAL_DATA_START\].*?\[INTERNAL_DATA_END\]",
    r"\[UI_FILE_INJECTION\][^\n]*",
    r"\[INTERNAL_DATA_START\][^\n]*",
    r"\[INTERNAL_DATA_END\][^\n]*",
    r"<\w+>",
]

_SCREENSHOT_PATH_RE = re.compile(
    r"([A-Za-z]:\\[^\s\n\"']+\.(?:jpg|jpeg|png))",
    re.IGNORECASE,
)

# Regex to extract current URL from tool results
_URL_RE = re.compile(r"(?:URL after|Current URL|url_after|Now on)[:\s]+\s*(https?://[^\s\n\"']+)", re.IGNORECASE)


def _sanitize_response(text: str) -> str:
    import re as _re
    for pat in _AGENT_NOISE_PATTERNS:
        text = _re.sub(pat, "", text, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    text = _re.sub(r"^[\s\.\,\-\=]+$", "", text, flags=_re.MULTILINE).strip()
    return text


def _clean_tool_result(text: str, tname: str = "", max_chars: int = 0) -> str:
    if max_chars == 0:
        max_chars = 1200 if tname in (
            "agent_step", "execute_browser_workflow",
            "browse_and_extract", "extract_text"
        ) else 600
    if "Browser logs:" in text:
        key_part = text.split("Browser logs:")[0].strip()
        key_part = key_part.replace(
            "Tell the user what happened and offer to try again with a different approach.", ""
        ).strip()
        return key_part[:max_chars]
    return text[:max_chars]


def _extract_screenshot_path(result_text: str) -> Optional[str]:
    match = _SCREENSHOT_PATH_RE.search(result_text)
    if match:
        path = match.group(1)
        if os.path.isfile(path):
            return path
    for fallback in [
        r"C:\VoxKage\Brain\latest_browser_state.jpg",
        r"C:\VoxKage\Brain\agent_step_goto.jpg",
        r"C:\VoxKage\Brain\agent_step_search_on_site.jpg",
        r"C:\VoxKage\Brain\agent_step_search_on_page.jpg",
        r"C:\VoxKage\Brain\agent_step_extract_text.jpg",
    ]:
        if os.path.isfile(fallback):
            return fallback
    return None


def _extract_url_from_result(result_text: str) -> str:
    """Extract current browser URL from agent_step / search_web result strings."""
    match = _URL_RE.search(result_text)
    if match:
        url = match.group(1).rstrip(".,;)")
        return url
    return ""


def _extract_tool_call_json(text: str) -> Optional[dict]:
    """
    Robustly extract a valid {"tool": ..., "args": ...} JSON object from Gemini's
    raw output, even when the output is malformed (duplicate JSON, extra prose).

    Strategies:
    1. Direct json.loads() after stripping markdown fences
    2. Scan from every '{' — find LAST parseable object with "tool" key
       (handles Gemini's duplicate-JSON pattern)
    3. Regex for innermost {"tool":...,"args":{...}} block
    """
    import json as _json

    clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    try:
        parsed = _json.loads(clean)
        if isinstance(parsed, dict) and "tool" in parsed:
            return parsed
    except _json.JSONDecodeError:
        pass

    last_valid: Optional[dict] = None
    for i, ch in enumerate(clean):
        if ch == "{":
            try:
                candidate = _json.loads(clean[i:])
                if isinstance(candidate, dict) and "tool" in candidate:
                    last_valid = candidate
            except _json.JSONDecodeError:
                pass
    if last_valid:
        return last_valid

    pattern = re.compile(
        r'\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*\}',
        re.DOTALL,
    )
    matches = pattern.findall(clean)
    for m in reversed(matches):
        try:
            parsed = _json.loads(m)
            if isinstance(parsed, dict) and "tool" in parsed:
                return parsed
        except _json.JSONDecodeError:
            pass

    return None


# --- LangGraph State Definition ---
class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    client: Any
    tools: Any
    _llm_chat_with_retry: Any
    step_count: int
    final_response: str
    goal_is_met: bool
    goal: str
    screenshot_path: str
    tool_history: Annotated[list, operator.add]
    # NEW: browser's current URL — tracked across every step for self-healing
    current_url: str
    # NEW: last error message — fed back into the planner for self-correction
    last_error: str


# ─────────────────────────────────────────────────────────────────────────────
# Gemini-powered LLM node (primary brain, Gemini fallback)
# ─────────────────────────────────────────────────────────────────────────────
async def gemini_llm_node(state: AgentState):
    """
    Calls Gemini CLI (20s timeout, 1 attempt) to plan the next browser step.
    Falls back to Gemini immediately on any failure — no 135s silence gaps.
    """
    logger.info(f"[Phase5/Gemini] Planning step {state['step_count']}/{MAX_AGENT_STEPS}")

    goal = state.get("goal", "Complete the requested task")
    tool_history = state.get("tool_history", [])
    screenshot_path = state.get("screenshot_path", "")
    current_url = state.get("current_url", "")
    last_error = state.get("last_error", "")
    messages = state["messages"]
    tools = state["tools"]
    _llm_chat_with_retry = state["_llm_chat_with_retry"]

    # ── Build compact Gemini planning prompt ───────────────────────────────
    history_lines = "\n".join(
        f"  Step {i+1}: {entry}" for i, entry in enumerate(tool_history[-8:])
    )

    latest_result = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            latest_result = str(msg.get("content", ""))[:800]
            break
        if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("images"):
            latest_result = str(msg.get("content", ""))[:800]
            break

    browser_state_line = ""
    if current_url:
        browser_state_line = f"\nBROWSER STATE: Currently on {current_url} (browser is OPEN, do NOT re-navigate unless needed)"

    error_hint = ""
    if last_error:
        error_hint = f"\n⚠️ PREVIOUS STEP ERROR: {last_error}\nFix the args and retry with the correct format shown below."

    vision_note = ""
    if screenshot_path and os.path.isfile(screenshot_path):
        vision_note = f"\n[Screenshot attached: {screenshot_path}]\nAnalyze the screenshot to confirm what is visible before deciding the next step."

    planning_prompt = (
        f"You are executing a multi-step task (browser OR desktop GUI).\n"
        f"GOAL: {goal}\n"
        f"{browser_state_line}"
        f"{error_hint}\n\n"
        f"Steps completed:\n{history_lines if history_lines else '  (none yet)'}\n\n"
        f"Latest result:\n{latest_result if latest_result else '  (no result yet)'}\n"
        f"{vision_note}\n\n"
        f"{_AGENT_STEP_CHEATSHEET}\n\n"
        f"{_GUI_STEP_CHEATSHEET}\n\n"
        f"If goal is FULLY achieved, respond:\n"
        f"  GOAL MET: <2-3 sentence summary for the user>\n\n"
        f"Otherwise output exactly ONE JSON tool call. No prose. No explanation."
    )

    # ── Try Gemini CLI (45s timeout, 1 attempt only — fail fast to Gemini) ─
    # Uses gemini-3-flash (gemini-2.5-flash-preview-04-17) which is faster and
    # more instruction-following than 2.5-flash for multi-step planning prompts.
    gemini_response = None
    try:
        from llm.gemini_engine import ask_voxkage_brain, GeminiCLIError
        from llm.constants import (
            GEMINI_AGENTIC_MODEL, GEMINI_AGENTIC_TIMEOUT, GEMINI_AGENTIC_RETRIES
        )
        img_path = screenshot_path if (screenshot_path and os.path.isfile(screenshot_path)) else None
        # 45s timeout + max 1 attempt: if Gemini is slow/rate-limited, fall
        # immediately to Gemini rather than burning 3×45s = 135s in silence.
        gemini_response = await ask_voxkage_brain(
            planning_prompt,
            image_path=img_path,
            model=GEMINI_AGENTIC_MODEL,
            timeout=GEMINI_AGENTIC_TIMEOUT,
            max_retries=GEMINI_AGENTIC_RETRIES,
        )
        logger.info(f"[Phase5/Gemini] Raw: {gemini_response[:200]!r}")
    except Exception as gem_err:
        logger.warning(f"[Phase5/Gemini] Failed → Gemini: {gem_err}")

    # ── Parse Gemini response ──────────────────────────────────────────────
    if gemini_response:
        text = gemini_response.strip()

        if text.upper().startswith("GOAL MET"):
            summary = text[8:].lstrip(":").strip()
            log_to_hud("VoxKage", f"✅ Goal achieved: {summary[:120]}")
            return {
                "final_response": summary,
                "goal_is_met": True,
                "step_count": state["step_count"] + 1,
                "last_error": "",
            }

        parsed_call = _extract_tool_call_json(text)
        if parsed_call:
            tool_name = parsed_call.get("tool", "")
            tool_args = parsed_call.get("args", {})
            if tool_name:
                msg_dict = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": tool_name, "arguments": tool_args}}],
                }
                log_to_hud("VoxKage", f"🤖 [Gemini→{tool_name}] Planning step {state['step_count']+1}")
                return {
                    "messages": [msg_dict],
                    "step_count": state["step_count"] + 1,
                    "last_error": "",
                }
        else:
            logger.warning(f"[Phase5/Gemini] All JSON strategies failed. text={text[:200]!r}")

        looks_like_json = text.lstrip().startswith("{")
        if not looks_like_json and len(text) > 30:
            clean_text = _sanitize_response(text)
            if clean_text:
                return {
                    "final_response": clean_text,
                    "goal_is_met": True,
                    "step_count": state["step_count"] + 1,
                    "last_error": "",
                }

        if looks_like_json:
            logger.warning(f"[Phase5/Gemini] JSON parse failed → No fallback available.")

    # ── If all fails, return error ──────────────────────────────────────────
    logger.error("[Phase5] Error in LLM node: Gemini failed to return valid JSON.")
    return {
        "final_response": "Encountered an error: Gemini failed to return a valid plan. Please try a different approach.",
        "goal_is_met": True,
        "last_error": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool execution node — self-healing
# ─────────────────────────────────────────────────────────────────────────────
async def tool_node(state: AgentState):
    """
    Executes tools and returns results.
    - Extracts screenshot path and current URL for the next planning step.
    - Detects pydantic validation errors and injects a corrective hint back into
      state so the planner self-corrects on the next iteration.
    """
    last_message = state["messages"][-1]

    if not isinstance(last_message, dict) or not last_message.get("tool_calls"):
        return {}

    tool_calls = last_message["tool_calls"]
    new_messages = []
    new_tool_history = []
    latest_screenshot_path = state.get("screenshot_path", "")
    current_url = state.get("current_url", "")
    last_error = ""

    for tool in tool_calls:
        tool_name = tool["function"]["name"]
        args_raw = tool["function"]["arguments"]

        if isinstance(args_raw, dict):
            arguments = args_raw
        else:
            try:
                arguments = json.loads(args_raw or "{}")
            except Exception:
                arguments = {}

        if tool_name == "agent_thinking":
            thought = arguments.get("goal", arguments.get("thought", ""))
            if thought:
                log_to_hud("VoxKage", f"🧠 {thought}")

        logger.info(f"[Phase5] Executing tool: {tool_name}")

        try:
            result = execute_tool_call(tool_name, arguments)
            # --- FIX: MCP String Deserialization for Vision Dicts ---
            # FastMCP casts dict returns to str. If it's a __vision__ payload, parse it back to dict
            # so the multimodal vision handling picks it up and prevents 500k base64 text tokens.
            if isinstance(result, str) and result.strip().startswith("{") and "__vision__" in result[:50]:
                try:
                    import json as _json
                    result = _json.loads(result.strip())
                except Exception:
                    import ast
                    try:
                        result = ast.literal_eval(result.strip())
                    except Exception:
                        pass
            # --------------------------------------------------------
        except Exception as exec_err:
            result = f"Error executing tool {tool_name}: {exec_err}"

        result_str = str(result)
        result_preview = result_str[:120].replace("\n", " ")
        log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")

        # ── Detect validation / argument errors and self-correct ──────────
        is_validation_error = any(k in result_str.lower() for k in [
            "validation error", "field required", "input should be",
            "missing", "type=missing", "error executing tool"
        ])
        if is_validation_error:
            last_error = f"Tool '{tool_name}' failed with: {result_str[:300]}"
            logger.warning(f"[Phase5] Validation error on {tool_name}: {result_str[:200]}")
            new_tool_history.append(
                f"[{tool_name}] FAILED (bad args) → {result_str[:150]}"
            )
            new_messages.append({
                "role": "tool",
                "name": tool_name,
                "content": (
                    f"⚠️ TOOL CALL FAILED: {result_str[:300]}\n"
                    f"Fix your args using the AGENT_STEP ACTION REFERENCE above and retry."
                )
            })
            continue  # Don't update URL/screenshot from a failed call

        # ── Compact history entry ─────────────────────────────────────────
        args_summary = json.dumps(arguments, ensure_ascii=False)[:120]
        new_tool_history.append(f"[{tool_name}({args_summary})] → {result_str[:200]}")

        # ── Extract current URL from result ───────────────────────────────
        extracted_url = _extract_url_from_result(result_str)
        if extracted_url:
            current_url = extracted_url
            logger.info(f"[Phase5] Browser now at: {current_url}")

        # ── Multimodal Vision Handling ────────────────────────────────────
        if isinstance(result, dict) and result.get("__vision__"):
            last_tool_result_text = result.get("text", "")
            screenshot_b64 = result.get("screenshot_b64", "")

            ss_path = _extract_screenshot_path(last_tool_result_text)
            if ss_path:
                latest_screenshot_path = ss_path
                logger.info(f"[Phase5] Screenshot for Gemini vision: {ss_path}")

            # Also extract URL from the structured result text
            extracted_url = _extract_url_from_result(last_tool_result_text)
            if extracted_url:
                current_url = extracted_url

            if screenshot_b64:
                new_messages.append({
                    "role": "user",
                    "content": last_tool_result_text,
                    "images": [screenshot_b64]
                })
            else:
                new_messages.append({"role": "user", "content": last_tool_result_text})
        else:
            last_tool_result_text = _clean_tool_result(result_str, tool_name)
            new_messages.append({
                "role": "tool",
                "name": tool_name,
                "content": str(last_tool_result_text)
            })

            if any(sig in last_tool_result_text.upper() for sig in ["GOAL MET", "GOAL_MET", "TASK COMPLETE"]):
                raw = last_tool_result_text
                for prefix in ["GOAL_MET:", "GOAL MET:", "TASK COMPLETE:"]:
                    idx = raw.upper().find(prefix)
                    if idx != -1:
                        raw = raw[idx + len(prefix):].strip()
                        break
                clean_final = raw.split("━━━")[0].strip()
                return {
                    "messages": new_messages,
                    "tool_history": new_tool_history,
                    "screenshot_path": latest_screenshot_path,
                    "current_url": current_url,
                    "last_error": last_error,
                    "final_response": clean_final,
                    "goal_is_met": True,
                }

    return {
        "messages": new_messages,
        "tool_history": new_tool_history,
        "screenshot_path": latest_screenshot_path,
        "current_url": current_url,
        "last_error": last_error,
    }


def should_continue(state: AgentState):
    if state["goal_is_met"]:
        return END
    if state["step_count"] >= MAX_AGENT_STEPS:
        return END
    last_message = state["messages"][-1]
    if isinstance(last_message, dict) and last_message.get("role") == "assistant" and last_message.get("tool_calls"):
        return "tools"
    return END


def route_initial(state: AgentState):
    messages = state.get("messages", [])
    if not messages:
        return "llm"
    last_message = messages[-1]
    if isinstance(last_message, dict) and last_message.get("role") == "assistant" and last_message.get("tool_calls"):
        return "tools"
    return "llm"


# --- Build Graph ---
builder = StateGraph(AgentState)
builder.add_node("llm", gemini_llm_node)
builder.add_node("tools", tool_node)
builder.add_conditional_edges(START, route_initial, {"llm": "llm", "tools": "tools"})
builder.add_conditional_edges("llm", should_continue, {"tools": "tools", END: END})
builder.add_edge("tools", "llm")
graph = builder.compile()


async def execute_agentic_loop(
    prompt: str,
    messages: List[Dict[str, Any]],
    client: Any,
    tools: Any,
    _fixed_msgs: List[Dict[str, Any]],
    _conversation_history: List[Dict[str, Any]],
    _llm_chat_with_retry: Any,
    goal: str = "",
) -> AsyncGenerator[str, None]:
    """
    Executes the Phase 5 LangGraph agentic state machine.
    Gemini CLI is the primary brain (20s timeout, instant Gemini fallback).
    Yields the final response string.
    """
    logger.info("[Phase5] Starting LangGraph Agentic Execution (Gemini brain, self-healing)")

    initial_state = {
        "messages": messages,
        "client": client,
        "tools": tools,
        "_llm_chat_with_retry": _llm_chat_with_retry,
        "step_count": 0,
        "final_response": "",
        "goal_is_met": False,
        "goal": goal or prompt,
        "screenshot_path": "",
        "tool_history": [],
        "current_url": "",
        "last_error": "",
    }

    streamer = SentenceStreamer()
    final_text = ""

    try:
        result_state = await graph.ainvoke(initial_state)
        if result_state.get("final_response"):
            final_text = result_state["final_response"]
        elif result_state.get("step_count", 0) >= MAX_AGENT_STEPS:
            final_text = "I've reached my iteration limit. Please review the output for the information gathered so far."
    except Exception as e:
        logger.error(f"[Phase5] LangGraph execution failed: {e}")
        final_text = f"Encountered a critical error: {str(e)}."

    final_text = _sanitize_response(final_text)
    if not final_text:
        final_text = "I have completed the requested task."

    if _conversation_history is not None:
        _conversation_history.append({"role": "user", "content": prompt})
        _conversation_history.append({"role": "assistant", "content": final_text})

    streamer.add_token(final_text)
    streamer.flush()
    yield final_text
