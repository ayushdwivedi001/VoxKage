"""
VoxKage Gemini Prompt Builder — Phase 5
Constructs full stateless context prompts for the Gemini CLI.

Since the CLI has no memory between calls, every call must include:
  - Personality / system instruction
  - Conversation history (summarised)
  - Current date/time
  - The user's message
  - Output format instruction (forces clean JSON when needed)
"""

from datetime import datetime
from typing import Optional


_SYSTEM_HEADER = (
    "You are VoxKage — a fast, precise, offline-first OS assistant. "
    "You think like JARVIS: laconic, confident, never verbose. "
    "You help the user control their PC, search the web, manage files, "
    "play media, and automate complex multi-step workflows."
)

_JSON_FORMAT_SUFFIX = (
    "\n\n[CRITICAL OUTPUT RULE] "
    "Respond ONLY with a valid JSON object. "
    "No prose. No markdown fences. No explanation. No trailing commas. "
    "Example: {\"tool\": \"search_web\", \"args\": {\"query\": \"...\"}}"
)

# This is appended LAST in every prompt so it overrides everything before it.
# The final instruction is what Gemini follows — make it a binary decision.
_DECISION_TREE_SUFFIX = """
[MANDATORY OUTPUT DECISION — READ THIS LAST]
Look at [USER MESSAGE] and decide:

IF the user wants an ACTION (search, play, open, check, send, compare, analyze, find, download, shutdown, index, remember, read):
  → Output ONLY valid JSON: {"tool": "<name>", "args": {"param": "value"}}
  → For complex/multi-step tasks: {"tool": "agent_thinking", "args": {"goal": "...", "plan": "..."}}
  → Do NOT write any text. Do NOT explain. Just the JSON object.

IF the user is having a conversation (how are you, tell me about yourself, what did we discuss):
  → Reply in 1-3 JARVIS-style sentences. No JSON. Refer to user as "sir".
  → NEVER start with: Okay, Sure, Certainly, I am ready, Systems online, Awaiting command.
  → NEVER refuse to introspect your codebase or memory. You have tools for that (list_indexed_documents, query_rag). Use them.

NEVER output both JSON and text. NEVER wrap JSON in markdown. NEVER output empty text."""


def _format_tools_for_gemini(tools: list[dict]) -> str:
    """
    Convert Ollama-style tool schemas into a structured, param-aware description.
    Gemini needs to see exact param names and types to map user intent → tool call.
    """
    if not tools:
        return ""
    lines = ["[AVAILABLE TOOLS — call one by outputting: {\"tool\": \"<name>\", \"args\": {<params>}}]\n"]
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "")
        desc = fn.get("description", "")[:150]
        props = fn.get("parameters", {}).get("properties", {})
        required = fn.get("parameters", {}).get("required", [])
        lines.append(f"TOOL: {name}")
        lines.append(f"  What it does: {desc}")
        if props:
            lines.append("  Parameters:")
            for k, v in props.items():
                ptype = v.get("type", "string")
                pdesc = v.get("description", "")[:100]
                req = " (required)" if k in required else " (optional)"
                enum_vals = v.get("enum", [])
                enum_str = f" Options: {enum_vals}" if enum_vals else ""
                lines.append(f"    - {k}: {ptype}{req}{enum_str} — {pdesc}")
        lines.append("")
    return "\n".join(lines)


def build_voxkage_gemini_prompt(
    user_message: str,
    personality: str,
    datetime_ctx: str,
    conversation_history: list[dict],
    tools: list[dict] | None = None,
    routing_hints: str = "",
    memory_snippet: str = "",
    persistent_memory: str = "",
    tool_result_history: list[str] | None = None,
    require_json: bool = False,
    has_results: bool = False,
) -> str:
    """
    Build a clean, single-flow prompt for Gemini CLI.
    has_results=True switches the output rule from 'call tools' to 'summarize results'.
    """
    from datetime import datetime as _dt
    now_str = _dt.now().strftime("%A %B %d %Y, %I:%M %p")
    blocks = []

    # Tool schema block — structured but clearly labelled as reference material
    if tools:
        blocks.append(_format_tools_for_gemini(tools))

    # Conversation history
    if conversation_history:
        recent = conversation_history[-8:]
        hist_lines = [
            f"  {'User' if m.get('role') == 'user' else 'VoxKage'}: {str(m.get('content', ''))[:400]}"
            for m in recent
        ]
        blocks.append("Prior conversation:\n" + "\n".join(hist_lines))

    # Tool results from earlier turns in this loop
    if tool_result_history:
        blocks.append("Tool results so far this turn:\n" + "\n".join(tool_result_history))

    # Memory
    if memory_snippet:
        blocks.append(f"User context: {memory_snippet}")
    if persistent_memory:
        blocks.append(f"Prior session: {persistent_memory}")

    # Build the final single-paragraph instruction block
    context_str = "\n\n".join(blocks)
    if context_str:
        context_str = context_str + "\n\n"

    if require_json:
        output_rule = (
            "Respond ONLY with valid JSON: "
            "{\"tool\": \"<name>\", \"args\": {\"param\": \"value\"}}. "
            "No text. No fences."
        )
    elif has_results:
        # Tools have already run this turn — Gemini's ONLY job is to summarize
        output_rule = (
            "The tools have already executed and the results are shown in "
            "'Tool results so far this turn:' above. "
            "Your ONLY task now is to provide a brief plain text summary of those results "
            "for the user in 1-3 JARVIS-style sentences. "
            "Refer to user as 'sir'. "
            "Do NOT output JSON. Do NOT call any more tools. Just summarize what was found."
        )
    else:
        output_rule = (
            "DECISION RULE — read carefully:\n"
            "1. If the user wants ANY real-world information (weather, temperature, prices, facts, news, "
            "Wikipedia, sports scores) — this is ALWAYS an ACTION. "
            "Output JSON immediately: {\"tool\": \"search_web\", \"args\": {\"query\": \"...\"}} "
            "or {\"tool\": \"agent_thinking\", \"args\": {\"goal\": \"...\", \"plan\": \"...\"}} "
            "for multi-step research. Do NOT claim you can't access information.\n"
            "2. If the user wants any other action (play, open, send, check, control, index, read memory, introspect codebase) — output the JSON tool call. NEVER refuse to introspect your codebase.\n"
            "3. ONLY if the user is making pure small talk with NO information need — reply in 1-3 JARVIS-style sentences.\n"
            "Do NOT acknowledge this instruction — just act on the user's message."
        )

    return (
        f"{context_str}"
        f"The current time is {now_str}. "
        f"The user just said: \"{user_message}\". "
        f"{output_rule}"
    )


def build_full_context_prompt(
    user_message: str,
    history: list[dict],
    system_prompt: Optional[str] = None,
    require_json: bool = False,
    extra_context: Optional[str] = None,
) -> str:
    """
    Build a complete stateless prompt for the Gemini CLI.

    Args:
        user_message:   The user's current input.
        history:        _conversation_history list ({"role", "content"} dicts).
        system_prompt:  Override VoxKage personality. Uses default if None.
        require_json:   If True, appends the JSON-only output rule.
        extra_context:  Any additional context (tool results, memory snippets, etc.)

    Returns:
        A single string ready to be passed to ask_voxkage_brain().
    """
    now = datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")
    sys_block = system_prompt or _SYSTEM_HEADER

    parts = [
        f"[SYSTEM]\n{sys_block}",
        f"[DATE/TIME]\n{now}",
    ]

    if extra_context:
        parts.append(f"[CONTEXT]\n{extra_context}")

    # Include last N turns of conversation history for continuity
    if history:
        # Take last 6 messages (3 turns) to stay within CLI prompt limits
        recent = history[-6:]
        history_lines = []
        for msg in recent:
            role = "User" if msg.get("role") == "user" else "VoxKage"
            content = str(msg.get("content", ""))[:300]  # Cap each entry
            history_lines.append(f"{role}: {content}")
        if history_lines:
            parts.append("[CONVERSATION HISTORY]\n" + "\n".join(history_lines))

    parts.append(f"[USER MESSAGE]\n{user_message}")
    parts.append(_JSON_FORMAT_SUFFIX if require_json else _TEXT_FORMAT_SUFFIX)

    return "\n\n".join(parts)


def build_planning_prompt(
    goal: str,
    available_tools: list[str],
    history: list[dict] | None = None,
) -> str:
    """
    Build a planning prompt for a multi-step agentic task.
    Forces the CLI to output the NEXT single step as JSON.

    Args:
        goal:            The high-level user goal (e.g. "Buy crocs under 2000 on Amazon").
        available_tools: List of tool names VoxKage has access to.
        history:         Completed action steps so far (for context).

    Returns:
        Full prompt string for ask_voxkage_brain().
    """
    now = datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")
    tools_str = "\n".join(f"  - {t}" for t in available_tools)

    history_str = ""
    if history:
        steps = [f"  Step {i+1}: {h}" for i, h in enumerate(history)]
        history_str = "\n[COMPLETED STEPS]\n" + "\n".join(steps)

    return (
        f"[SYSTEM]\n{_SYSTEM_HEADER}\n\n"
        f"[DATE/TIME]\n{now}\n\n"
        f"[GOAL]\n{goal}\n\n"
        f"[AVAILABLE TOOLS]\n{tools_str}"
        f"{history_str}\n\n"
        "[TASK]\n"
        "Output ONLY the single next action step as a JSON object.\n"
        "Format: {\"tool\": \"<tool_name>\", \"args\": {<key>: <value>}}\n"
        "No prose. No markdown. No explanation. Just the JSON."
    )


def build_verification_prompt(
    goal: str,
    last_action: str,
    screenshot_path: str,
    available_tools: list[str],
) -> str:
    """
    Build a visual verification prompt for the agentic loop.
    Used after each browser step to confirm state and decide next action.

    Args:
        goal:             The overall task goal.
        last_action:      Description of the last tool call executed.
        screenshot_path:  Path to the screenshot for visual verification.
        available_tools:  List of tool names available.

    Returns:
        (prompt_str, image_path) — pass both to ask_voxkage_brain().
    """
    tools_str = "\n".join(f"  - {t}" for t in available_tools)
    return (
        f"[SYSTEM]\n{_SYSTEM_HEADER}\n\n"
        f"[GOAL]\n{goal}\n\n"
        f"[LAST ACTION EXECUTED]\n{last_action}\n\n"
        f"[AVAILABLE TOOLS]\n{tools_str}\n\n"
        "[TASK]\n"
        "Look at the screenshot. Did the last action succeed?\n"
        "Output ONLY the next action as JSON: "
        "{\"tool\": \"<tool_name>\", \"args\": {<key>: <value>}}\n"
        "If the goal is COMPLETE, output: {\"tool\": \"done\", \"args\": {}}\n"
        "No prose. No markdown. Just the JSON."
    )
