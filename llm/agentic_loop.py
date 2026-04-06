"""
Agentic Loop Implementation for VoxKage LLM Client
Contains the Phase 4: Sliding Window Agentic Loop logic
"""
import asyncio
import json
import re
from typing import Dict, Any, List, Optional, AsyncGenerator
from llm.tool_registry import execute_tool_call
from voice.voice_manager import SentenceStreamer, manager, log_to_hud
import logging

logger = logging.getLogger(__name__)

# Constants for the agentic loop
MAX_AGENT_STEPS = 20
MAX_TEXT_RETRIES = 3

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
    Execute the Phase 4: Sliding Window Agentic Loop for complex browser tasks.
    
    Yields response chunks as they are generated.
    """
    agent_step_count = 0
    final_response = ""
    goal_is_met = False
    last_tool_name = ""
    last_tool_result_text = ""
    last_step_intent = ""
    task_plan = ""           # Extracted from first agent_thinking call
    latest_screenshot_b64 = None   # ONLY the most recent screenshot
    text_response_retries = 0      # Consecutive invalid text responses
    
    # We'll collect the LLM responses here to yield them gradually
    accumulated_response = ""
    streamer = SentenceStreamer()
    
    while agent_step_count < MAX_AGENT_STEPS:
        agent_step_count += 1

        # ── Ctrl+Space interrupt check — top of every iteration ──
        if manager.was_interrupted:
            logger.info("[Phase4] User interrupted task at loop start.")
            log_to_hud("VoxKage", "⛔ Task interrupted by user.")
            final_response = "Got it — I've stopped the current task."
            goal_is_met = True
            # Yield the interruption message
            yield final_response
            break

        logger.info(f"[Phase4] Agentic loop iteration {agent_step_count}/{MAX_AGENT_STEPS}")

        # Get LLM response with tools enabled
        try:
            response = await _llm_chat_with_retry(client, messages, tools=tools)
            message = response.message
            
            # Process the message - check for tool calls or text response
            if hasattr(message, 'tool_calls') and message.tool_calls:
                # Handle tool calls
                tool_results = []
                for tool in message.tool_calls:
                    tool_name = tool.function.name
                    args_raw = tool.function.arguments
                    if isinstance(args_raw, dict):
                        arguments = args_raw
                    else:
                        try:
                            arguments = json.loads(args_raw or "{}")
                        except Exception:
                            arguments = {}
                            logger.warning(f"Could not parse tool arguments as JSON: {args_raw}")
                    
                    if tool_name == "agent_thinking":
                        thought = arguments.get("thought", "")
                        if thought:
                            # Dashboard only — do NOT speak thoughts
                            log_to_hud("VoxKage", f"🧠 {thought}")
                    
                    logger.info(f"[Phase4] Executing tool: {tool_name}")
                    result = execute_tool_call(tool_name, arguments)
                    tool_results.append((tool_name, result))
                    
                    # Show tool result on dashboard only — NOT spoken
                    result_preview = str(result)[:120].replace("\n", " ")
                    log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")
                
                # Process tool results and update state
                if tool_results:
                    last_tool_name, last_tool_result = tool_results[-1]
                    
                    # === PHASE 3: Multimodal Vision Handling ===
                    if isinstance(last_tool_result, dict) and last_tool_result.get("__vision__"):
                        latest_screenshot_b64 = last_tool_result.get("screenshot_b64")
                        last_tool_result_text = last_tool_result.get("text", "")
                        
                        # Add as a multimodal message if screenshot exists
                        if latest_screenshot_b64:
                            messages.append({
                                "role": "user",
                                "content": last_tool_result_text,
                                "images": [latest_screenshot_b64]
                            })
                        else:
                            messages.append({"role": "user", "content": last_tool_result_text})
                    else:
                        last_tool_result_text = _clean_tool_result(str(last_tool_result), last_tool_name)
                        messages.append({
                            "role": "assistant",
                            "content": f"Tool {last_tool_name} executed with result: {last_tool_result_text[:400]}"
                        })
                    
                    # Check if goal is met — support both 'GOAL MET:' and 'GOAL_MET:' variants
                    goal_signal = str(last_tool_result_text).upper()
                    if "GOAL MET" in goal_signal or "GOAL_MET" in goal_signal or "TASK COMPLETE" in goal_signal:
                        goal_is_met = True
                        # Extract just the human summary after the signal prefix
                        raw = str(last_tool_result_text)
                        for prefix in ["GOAL_MET:", "GOAL MET:", "TASK COMPLETE:"]:
                            idx = raw.upper().find(prefix)
                            if idx != -1:
                                raw = raw[idx + len(prefix):].strip()
                                break
                        # Clean up any system instruction fragments
                        final_response = raw.split("━━━")[0].strip()
                        # Do NOT yield here, we handle it at the very end
                        break
                        
            else:
                # Handle text response
                content = message.content or ""
                
                # Validate text response
                if not content.strip() or len(content.strip()) < 10:
                    text_response_retries += 1
                    if text_response_retries >= MAX_TEXT_RETRIES:
                        logger.warning("[Phase4] Max text retries exceeded, forcing tool use")
                        # Force a tool call by adding a system message
                        messages.append({
                            "role": "system",
                            "content": "You must use a tool to proceed. Call agent_thinking to make a plan or agent_step to take an action."
                        })
                        continue
                    else:
                        # Skip yielding generic waiting messages to avoid timing/redundancy issues
                        continue
                
                text_response_retries = 0  # Reset counter on valid response

                # Sanitize intermediate text before logging to dashboard
                clean_content = _sanitize_response(content)

                # Only log to dashboard if the content is genuinely informative
                # Skip generic LLM preambles like "Standing by, sir" that add noise
                _preamble_noise = [
                    "standing by", "online and", "at your service", "ready when",
                    "voxkage online", "systems green", "synchronized", "what's the priority",
                    "what can i", "how can i", "shall i", "systems nominal",
                ]
                _is_preamble = any(p in clean_content.lower()[:100] for p in _preamble_noise)
                if clean_content and len(clean_content) > 30 and not _is_preamble:
                    log_to_hud("VoxKage", f"ð¬ {clean_content[:200]}")

                accumulated_response += content
                
                # Check if this seems like a final response
                if any(phrase in content.lower() for phrase in ["i found", "here are", "the result is", "completed", "finished"]):
                    goal_is_met = True
                    final_response = content
                    break
                    
                # Add to conversation history
                messages.append({"role": "assistant", "content": content})
                
        except Exception as e:
            logger.error(f"[Phase4] Error in agentic loop: {e}")
            yield f"Encountered an error: {str(e)}. Let me try a different approach."
            # Continue the loop to try recovery
            
    # If we exited the loop without meeting the goal, provide a summary
    if not goal_is_met and accumulated_response:
        # Sanitize accumulated intermediate text before using as fallback
        clean_acc = _sanitize_response(accumulated_response)
        final_response = clean_acc if clean_acc else "I've completed the task steps but couldn't extract a clear answer. Please check the dashboard for details."
    elif not goal_is_met:
        final_response = "I've worked on this task but couldn't find the information. Please try rephrasing or ask me to try a different approach."
    
    # --- Final Loop Conclusion ---
    if final_response:
        # Sanitize before speaking to remove any leaked agent instructions
        final_response = _sanitize_response(final_response)
        
        # Update global history
        if _conversation_history is not None:
            _conversation_history.append({"role": "user", "content": prompt})
            _conversation_history.append({"role": "assistant", "content": final_response})
            
        # Speak the final result
        streamer.add_token(final_response)
        streamer.flush()
        yield final_response
    elif accumulated_response:
        clean = _sanitize_response(accumulated_response)
        if clean:
            if _conversation_history is not None:
                _conversation_history.append({"role": "user", "content": prompt})
                _conversation_history.append({"role": "assistant", "content": clean})
            streamer.add_token(clean)
            streamer.flush()
            yield clean