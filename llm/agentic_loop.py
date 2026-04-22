"""
Agentic Loop Implementation for VoxKage LLM Client
Contains the Phase 5: LangGraph Agentic State Machine
"""
import asyncio
import json
import re
from typing import Dict, Any, List, Optional, AsyncGenerator, TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END
from llm.mcp_dispatcher import dispatch_tool_call as execute_tool_call
from voice.voice_manager import SentenceStreamer, manager, log_to_hud
import logging

logger = logging.getLogger(__name__)

# Constants for the agentic loop
MAX_AGENT_STEPS = 20

# Patterns that are NEVER allowed in spoken/user-facing output
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

def _sanitize_response(text: str) -> str:
    """Strip all agent-internal instruction fragments from a user-facing response."""
    import re as _re
    for pat in _AGENT_NOISE_PATTERNS:
        text = _re.sub(pat, "", text, flags=_re.DOTALL | _re.IGNORECASE)
    # Collapse excessive whitespace / blank lines
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    # Remove any trailing stray punctuation left by pattern removal
    text = _re.sub(r"^[\s\.\,\-\=]+$", "", text, flags=_re.MULTILINE).strip()
    return text


def _clean_tool_result(text: str, tname: str = "", max_chars: int = 0) -> str:
    """Strip verbose browser crash logs before storing."""
    if max_chars == 0:
        # Action-aware limits: browser extractions need more room
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


# --- LangGraph State Definition ---
class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    client: Any
    tools: Any
    _llm_chat_with_retry: Any
    step_count: int
    final_response: str
    goal_is_met: bool


# --- LangGraph Nodes ---
async def llm_node(state: AgentState):
    """Calls the Ollama model and updates state."""
    logger.info(f"[Phase5] LLM Node - Iteration {state['step_count']}/{MAX_AGENT_STEPS}")
    
    # Interruption check
    if manager.was_interrupted:
        logger.info("[Phase5] User interrupted task at loop start.")
        log_to_hud("VoxKage", "⛔ Task interrupted by user.")
        return {"final_response": "Got it — I've stopped the current task.", "goal_is_met": True}

    client = state["client"]
    tools = state["tools"]
    messages = state["messages"]
    _llm_chat_with_retry = state["_llm_chat_with_retry"]

    try:
        response = await _llm_chat_with_retry(client, messages, tools=tools)
        message = response.message
        
        # Check for standard tool calls
        if hasattr(message, 'tool_calls') and message.tool_calls:
            # Convert to dictionary format required by Ollama client
            msg_dict = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "function": {
                            "name": t.function.name,
                            "arguments": t.function.arguments
                        }
                    } for t in message.tool_calls
                ]
            }
            return {"messages": [msg_dict], "step_count": state["step_count"] + 1}
        else:
            content = message.content or ""
            
            # Check for hallucinated XML tool calls as a fallback
            function_match = re.search(r'<function=([^>]+)>\s*(.*?)\s*</function>', content, re.DOTALL)
            if function_match:
                # Convert into a proper tool call dict
                msg_dict = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": function_match.group(1).strip(),
                                "arguments": json.loads(function_match.group(2).strip() or "{}")
                            }
                        }
                    ]
                }
                logger.info(f"[Phase5] Rescued XML tool call hallucination: {msg_dict['tool_calls'][0]['function']['name']}")
                return {"messages": [msg_dict], "step_count": state["step_count"] + 1}

            # If no tool calls, the LLM has given a natural text response. Goal is met!
            clean_content = _sanitize_response(content)
            
            _preamble_noise = [
                "standing by", "online and", "at your service", "ready when",
                "voxkage online", "systems green", "synchronized", "what's the priority",
                "what can i", "how can i", "shall i", "systems nominal"
            ]
            _is_preamble = any(p in clean_content.lower()[:100] for p in _preamble_noise)
            if clean_content and len(clean_content) > 30 and not _is_preamble:
                log_to_hud("VoxKage", f"💬 {clean_content[:200]}")
            
            return {
                "messages": [{"role": "assistant", "content": content}],
                "final_response": content,
                "goal_is_met": True,
                "step_count": state["step_count"] + 1
            }
            
    except Exception as e:
        logger.error(f"[Phase5] Error in LLM node: {e}")
        return {"final_response": f"Encountered an error: {str(e)}. Please try a different approach.", "goal_is_met": True}


async def tool_node(state: AgentState):
    """Executes tools and returns results."""
    last_message = state["messages"][-1]
    
    if not isinstance(last_message, dict) or not last_message.get("tool_calls"):
        return {}

    tool_calls = last_message["tool_calls"]
    new_messages = []
    
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
            thought = arguments.get("thought", "")
            if thought:
                log_to_hud("VoxKage", f"🧠 {thought}")
                
        logger.info(f"[Phase5] Executing tool: {tool_name}")
        result = execute_tool_call(tool_name, arguments)
        
        result_preview = str(result)[:120].replace("\n", " ")
        log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")

        # === PHASE 3: Multimodal Vision Handling ===
        if isinstance(result, dict) and result.get("__vision__"):
            latest_screenshot_b64 = result.get("screenshot_b64")
            last_tool_result_text = result.get("text", "")
            
            if latest_screenshot_b64:
                new_messages.append({
                    "role": "user",
                    "content": last_tool_result_text,
                    "images": [latest_screenshot_b64]
                })
            else:
                new_messages.append({"role": "user", "content": last_tool_result_text})
        else:
            last_tool_result_text = _clean_tool_result(str(result), tool_name)
            new_messages.append({
                "role": "tool",
                "name": tool_name,
                "content": str(last_tool_result_text)
            })
            
            # Check for GOAL MET signal embedded in the result
            if "GOAL MET" in last_tool_result_text.upper() or "GOAL_MET" in last_tool_result_text.upper() or "TASK COMPLETE" in last_tool_result_text.upper():
                # We can extract the final text natively from the tool response
                raw = last_tool_result_text
                for prefix in ["GOAL_MET:", "GOAL MET:", "TASK COMPLETE:"]:
                    idx = raw.upper().find(prefix)
                    if idx != -1:
                        raw = raw[idx + len(prefix):].strip()
                        break
                clean_final = raw.split("━━━")[0].strip()
                return {"messages": new_messages, "final_response": clean_final, "goal_is_met": True}

    return {"messages": new_messages}


def should_continue(state: AgentState):
    """Router logic for LangGraph."""
    if state["goal_is_met"]:
        return END
    
    if state["step_count"] >= MAX_AGENT_STEPS:
        return END

    last_message = state["messages"][-1]
    
    # If the last message is from the assistant and contains tool_calls, route to tools
    if isinstance(last_message, dict) and last_message.get("role") == "assistant" and last_message.get("tool_calls"):
        return "tools"
        
    # If it's a natural text response, exit
    return END


from langgraph.graph import START

def route_initial(state: AgentState):
    """Determine if we should start by calling the LLM or executing handed-over tools."""
    messages = state.get("messages", [])
    if not messages:
        return "llm"
    
    last_message = messages[-1]
    # If llm_client.py handed over an unexecuted tool call, execute it first!
    if isinstance(last_message, dict) and last_message.get("role") == "assistant" and last_message.get("tool_calls"):
        return "tools"
    
    return "llm"


# --- Build Graph ---
builder = StateGraph(AgentState)
builder.add_node("llm", llm_node)
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
    _llm_chat_with_retry: Any
) -> AsyncGenerator[str, None]:
    """
    Executes the Phase 5 LangGraph agentic state machine for complex browser tasks.
    Yields response chunks as they are generated.
    """
    logger.info("[Phase5] Starting LangGraph Agentic Execution")
    
    initial_state = {
        "messages": messages,
        "client": client,
        "tools": tools,
        "_llm_chat_with_retry": _llm_chat_with_retry,
        "step_count": 0,
        "final_response": "",
        "goal_is_met": False
    }
    
    streamer = SentenceStreamer()
    final_text = ""
    
    try:
        # Ainvoke runs the graph to completion (or END / max steps)
        result_state = await graph.ainvoke(initial_state)
        
        if result_state.get("final_response"):
            final_text = result_state["final_response"]
        elif result_state.get("step_count", 0) >= MAX_AGENT_STEPS:
            # If it hit max steps without setting a final_response
            final_text = "I've reached my iteration limit. Please check the dashboard for the information gathered so far."
            
    except Exception as e:
        logger.error(f"[Phase5] LangGraph execution failed: {e}")
        final_text = f"Encountered a critical error: {str(e)}."

    # Sanitize the output to avoid leaking instructions
    final_text = _sanitize_response(final_text)
    
    # Ensure there is at least some fallback text
    if not final_text:
        final_text = "I have completed the requested task."
        
    # Update global history
    if _conversation_history is not None:
        _conversation_history.append({"role": "user", "content": prompt})
        _conversation_history.append({"role": "assistant", "content": final_text})

    # Output to TTS
    streamer.add_token(final_text)
    streamer.flush()
    yield final_text
