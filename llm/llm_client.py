import asyncio
import aiohttp
from ollama import AsyncClient
import logging
import json
import re
from datetime import datetime
from dotenv import load_dotenv
import os

from llm.tool_registry import TOOLS_SCHEMA, execute_tool_call
from voice.voice_manager import SentenceStreamer, manager, log_to_hud
from llm.constants import OLLAMA_HOST, MODEL_NAME, MAX_HISTORY, GLOBAL_LAST_RESULTS
from llm.helpers import (
    _extract_json_tool_call, 
    _parse_text_intent, 
    _detect_browser_intent, 
    clear_session_memory
)
from llm.utils import check_ollama_server, _llm_chat_with_retry
from llm.system_prompt import get_default_system_prompt, get_datetime_context, get_persistent_memory
from llm.agentic_loop import execute_agentic_loop

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_conversation_history = []


async def generate_response_stream(prompt: str, system_prompt: str = "", use_tools: bool = True):
    global _conversation_history
    is_running = await check_ollama_server()
    if not is_running:
        yield "Error: Ollama server is not running or unreachable. Please start Ollama."
        return

    is_file_injection = "[UI_FILE_INJECTION]" in prompt
    if is_file_injection:
        prompt = prompt.replace("[UI_FILE_INJECTION]", "").strip()
        use_tools = False
        logger.info("File injection detected: disabling tools for this turn.")

    client = AsyncClient(host=OLLAMA_HOST)
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    else:
        messages.append({"role": "system", "content": get_default_system_prompt()})
    
    datetime_context = get_datetime_context()
    messages.append({
        "role": "system",
        "content": datetime_context["content"]
    })
    
    persistent_memory = get_persistent_memory()
    if persistent_memory:
        messages.append({"role": "system", "content": persistent_memory})
    
    for msg in _conversation_history:
        messages.append(msg)
    
    messages.append({"role": "user", "content": prompt})

    manager.was_interrupted = False

    try:
        streamer = SentenceStreamer()
        if use_tools:
            # Multi-turn tool execution loop
            max_tool_turns = 10
            _tools_ran_this_turn = False  # Guard: only fire browser interceptor before any tool runs
            for turn in range(max_tool_turns):
                # Respect Ctrl+Space interruption between turns
                if manager.was_interrupted:
                    logger.info("[llm_client] Interruption detected. Stopping tool loop.")
                    yield "Got it — stopping."
                    return
                
                response = await _llm_chat_with_retry(client, messages, tools=TOOLS_SCHEMA)
                message = response.message
                
                # 1. Handle tool calls
                if hasattr(message, 'tool_calls') and message.tool_calls:
                    # Convert response message to dict for history persistence
                    messages.append({
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
                    })
                    
                    for tool in message.tool_calls:
                        tool_name = tool.function.name
                        # Safe extraction of arguments
                        args_raw = tool.function.arguments
                        if isinstance(args_raw, dict):
                            arguments = args_raw
                        else:
                            try:
                                arguments = json.loads(args_raw or "{}")
                            except Exception:
                                arguments = {}
                                logger.warning(f"Could not parse tool arguments: {args_raw}")
                        
                        if not tool_name:
                            continue
                            
                        # Agentic loop handover
                        if tool_name in ["agent_thinking", "agent_step"]:
                            logger.info(f"Handover to agentic loop via: {tool_name}")
                            async for chunk in execute_agentic_loop(
                                prompt, messages, client, TOOLS_SCHEMA, [], _conversation_history, _llm_chat_with_retry
                            ):
                                yield chunk
                            return

                        # Execute local tool
                        _tools_ran_this_turn = True
                        result = execute_tool_call(tool_name, arguments)
                        # Show result on dashboard, don't speak it
                        result_preview = str(result)[:150].replace("\n", " ")
                        log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")
                        
                        # Add tool result to history for the next turn
                        # Note: Ollama doesn't always provide a tool_call_id; we use tool_name as fallback
                        messages.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": str(result)
                        })
                    
                    # Continue to next turn to get LLM summary of tool results
                    continue
                
                # 2. Handle final text response (No tool calls)
                else:
                    content = message.content or ""
                    
                    # Intercept browser intent ONLY if no tools have run yet this turn.
                    # After search_web/browse_and_extract returns results, the LLM summary
                    # contains words like 'currently' or 'I found' that would falsely trigger
                    # the interceptor and re-launch an unnecessary agentic loop.
                    if not _tools_ran_this_turn and _detect_browser_intent(content, prompt):
                        logger.info("Browser intent detected in text (no tools ran yet). Launching agentic loop.")
                        async for chunk in execute_agentic_loop(
                            prompt, messages, client, TOOLS_SCHEMA, [], _conversation_history, _llm_chat_with_retry
                        ):
                            yield chunk
                        return

                    # Handle conversational text
                    streamer.add_token(content)
                    yield content
                    
                    # Update global history
                    # User prompt is already at the beginning of this function turn,
                    # so we only append the new turn result to the global history.
                    _conversation_history.append({"role": "user", "content": prompt})
                    _conversation_history.append({"role": "assistant", "content": content})
                    # Keep history within reasonable bounds
                    while len(_conversation_history) > MAX_HISTORY * 2:
                        _conversation_history.pop(0)
                    
                    # Finished with this turn
                    break
            
            streamer.flush()
        else:
            full_response = ""
            streamer = SentenceStreamer()
            async_stream = await client.chat(model=MODEL_NAME, messages=messages, stream=True)
            async for chunk in async_stream:
                if manager.was_interrupted:
                    logger.info("TTS Interruption hotkey caught. Halting LLM generation.")
                    break
                if 'message' in chunk and 'content' in chunk['message']:
                    text = chunk['message']['content']
                    full_response += text
                    streamer.add_token(text)
                    yield text
            
            streamer.flush()
            
            # streamer.flush() already handled logging to HUD
            
            _conversation_history.append({"role": "user", "content": prompt})
            _conversation_history.append({"role": "assistant", "content": full_response})
            if len(_conversation_history) > MAX_HISTORY * 2:
                _conversation_history.pop(0)
                _conversation_history.pop(0)
                
    except Exception as e:
        err_str = str(e)
        if "500" in err_str or "xml" in err_str.lower() or "syntax error" in err_str.lower():
            logger.error(f"Error during LLM generation (Ollama parse error): {e}")
            yield "Sorry, I hit a technical glitch — the AI model had a hiccup. Please try again."
        else:
            logger.error(f"Error during LLM generation: {e}")
            yield "Sorry, something went wrong. Please try again."


def ask_llm_sync(prompt: str, system_prompt: str = "", use_tools: bool = True):
    async def _run():
        response = ""
        async for chunk in generate_response_stream(prompt, system_prompt, use_tools):
            response += chunk
        return response
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        import threading
        
        result: list[str] = [""]
        err: list[BaseException | None] = [None]
        def _thread_target():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                result[0] = new_loop.run_until_complete(_run())
            except Exception as e:
                err[0] = e
            finally:
                new_loop.close()
                
        t = threading.Thread(target=_thread_target)
        t.start()
        t.join()
        
        if err[0] is not None:
            raise err[0]
        return result[0]
    else:
        return asyncio.run(_run())


# No main block for production