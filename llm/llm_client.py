import asyncio
import aiohttp
from ollama import AsyncClient
import logging
import json
import re
from datetime import datetime
from dotenv import load_dotenv
import os

from llm.tool_registry import TOOLS_SCHEMA
from llm.mcp_dispatcher import dispatch_tool_call as execute_tool_call
from voice.voice_manager import SentenceStreamer, manager, log_to_hud
from llm.constants import (
    OLLAMA_HOST, MODEL_NAME, MAX_HISTORY, GLOBAL_LAST_RESULTS,
    USE_RAG, TOP_K_TOOLS, USE_MEMORY
)
from llm.helpers import (
    _extract_json_tool_call,
    _parse_text_intent,
    _detect_browser_intent,
    clear_session_memory
)
from llm.utils import check_ollama_server, _llm_chat_with_retry
from llm.system_prompt import (
    get_personality_prompt, get_routing_hints,
    get_datetime_context, get_persistent_memory
)
from llm.agentic_loop import execute_agentic_loop
from automation.gmail_manager import detect_email_intent, handle_compose, handle_edit, handle_send, handle_cancel

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_conversation_history = []

# ─── RAG: Tool retrieval helper ───────────────────────────────────────────────

def _get_tools_for_query(prompt: str) -> tuple[list, list[str]]:
    """
    Return (tool_schemas, tool_names) for the given query.
    Uses RAG if USE_RAG=True, falls back to full TOOLS_SCHEMA.
    Also always ensures agent_thinking + agent_step are included if any browser
    tool is retrieved, so agentic loop handover is always available.
    """
    if not USE_RAG:
        from llm.tool_definitions import get_all_ollama_schemas, TOOL_DEFINITIONS
        schemas = get_all_ollama_schemas()
        names = [t["name"] for t in TOOL_DEFINITIONS]
        return schemas, names

    try:
        from llm.tool_rag import retrieve_tools, retrieve_tool_names
        schemas = retrieve_tools(prompt, top_k=TOP_K_TOOLS)
        names = [s["function"]["name"] for s in schemas]

        # Always ensure agentic loop tools are present if any browser tool retrieved
        _browser_set = {"execute_browser_workflow", "browse_and_extract", "search_web"}
        _agent_tools = {"agent_thinking", "agent_step"}
        if (_browser_set & set(names)) and not (_agent_tools & set(names)):
            from llm.tool_rag import get_schema_by_name
            for at in ["agent_thinking", "agent_step"]:
                s = get_schema_by_name(at)
                if s and at not in names:
                    schemas.append(s)
                    names.append(at)

        return schemas, names

    except Exception as e:
        logger.error(f"[RAG] Tool retrieval failed: {e}. Falling back to TOOLS_SCHEMA.")
        from llm.tool_definitions import get_all_ollama_schemas, TOOL_DEFINITIONS
        schemas = get_all_ollama_schemas()
        names = [t["name"] for t in TOOL_DEFINITIONS]
        return schemas, names


def _ensure_tool_schema(tool_name: str, current_schemas: list) -> list:
    """
    If Qwen calls a tool not in the current retrieved set, add it on the fly.
    This is the 'fallback fetch' that guarantees zero silent failures from RAG gaps.
    """
    current_names = {s["function"]["name"] for s in current_schemas}
    if tool_name not in current_names:
        try:
            from llm.tool_rag import get_schema_by_name
            s = get_schema_by_name(tool_name)
            if s:
                logger.info(f"[RAG] On-demand schema fetch for '{tool_name}' (wasn't in retrieved set).")
                return current_schemas + [s]
        except Exception:
            pass
    return current_schemas


# ─── Main Response Stream ─────────────────────────────────────────────────────

async def generate_response_stream(prompt: str, system_prompt: str = "", use_tools: bool = True):
    global _conversation_history

    # ═══════════ HYBRID ENGINE ROUTER ═══════════════════════════════════════════
    # If ENGINE="gemini_cli", use the Gemini CLI subprocess as the reasoning brain.
    # Full context is assembled identically to the Ollama pipeline:
    #   personality + datetime + routing hints + Mem0 + persistent memory + history
    # Gemini returns either plain text or a JSON tool call — the loop handles both.
    # On failure (after MAX_CLI_RETRIES), silently falls through to Ollama.
    try:
        import llm.constants as _llm_const
        _current_engine = _llm_const.ENGINE
    except ImportError:
        _current_engine = "ollama"

    if _current_engine == "gemini_cli":
        try:
            from llm.gemini_engine import ask_voxkage_brain, GeminiCLIError, clean_cli_json
            from llm.gemini_prompt_builder import build_voxkage_gemini_prompt
            import llm.constants as _consts

            # ── Phase 1: Assemble full context (mirrors Ollama pipeline exactly) ──────
            relevant_tools, retrieved_names = _get_tools_for_query(prompt)
            logger.info(f"[Gemini RAG] Injecting {len(relevant_tools)} tools: {retrieved_names}")

            memory_snippet = ""
            if USE_MEMORY:
                try:
                    from llm.memory_manager import search_memory
                    memory_snippet = search_memory(prompt)
                except Exception as _me:
                    logger.warning(f"[Gemini Memory] Search failed (non-fatal): {_me}")

            routing_hints = get_routing_hints(retrieved_names)
            persistent_memory = get_persistent_memory()
            personality = system_prompt if system_prompt else get_personality_prompt()
            datetime_ctx = get_datetime_context()["content"]

            # ── Phase 2: Tool dispatch loop (up to 8 turns) ───────────────────────────
            MAX_GEMINI_TURNS = 8
            _gemini_tool_history: list[str] = []   # Accumulates tool results for context
            _tools_ran_this_turn = False
            _final_yielded = False
            _seen_tool_calls: set[str] = set()      # Guards against identical repeat calls
            manager.was_interrupted = False

            for _turn in range(MAX_GEMINI_TURNS):
                if manager.was_interrupted:
                    yield "Got it — stopping."
                    return

                # Build full stateless prompt for this turn
                # When tool results exist, pass has_results=True so the prompt
                # explicitly instructs Gemini to SUMMARIZE rather than act again.
                full_prompt = build_voxkage_gemini_prompt(
                    user_message=prompt,
                    personality=personality,
                    datetime_ctx=datetime_ctx,
                    routing_hints=routing_hints,
                    memory_snippet=memory_snippet,
                    persistent_memory=persistent_memory,
                    conversation_history=_conversation_history,
                    tools=relevant_tools,
                    tool_result_history=_gemini_tool_history,
                    has_results=bool(_gemini_tool_history),
                )

                log_to_hud("VoxKage", "🤖 [Gemini] Thinking…")
                raw_response = await ask_voxkage_brain(full_prompt)

                # ── Detect tool call in response ───────────────────────────────────
                tool_call = None
                try:
                    parsed = clean_cli_json(raw_response)
                    if isinstance(parsed, dict) and "tool" in parsed:
                        tool_call = parsed
                except (ValueError, Exception):
                    pass  # Not a tool call — plain text response

                if tool_call:
                    tool_name = tool_call.get("tool", "")
                    tool_args = tool_call.get("args", {})

                    if not tool_name or tool_name == "done":
                        # Gemini signalled completion
                        break

                    # Agentic loop handover for browser tasks
                    if tool_name in ("agent_thinking", "agent_step", "execute_browser_workflow"):
                        logger.info(f"[Gemini] Browser handover via: {tool_name}")
                        # Extract the goal string so the Gemini agentic brain knows its objective
                        handover_goal = (
                            tool_args.get("goal")
                            or tool_args.get("thought")
                            or prompt
                        )
                        client_ol = AsyncClient(host=OLLAMA_HOST)
                        messages_ol = [
                            {"role": "system", "content": personality},
                            {"role": "system", "content": datetime_ctx},
                        ] + list(_conversation_history) + [{"role": "user", "content": prompt}]
                        async for chunk in execute_agentic_loop(
                            prompt, messages_ol, client_ol, relevant_tools,
                            [], _conversation_history, _llm_chat_with_retry,
                            goal=handover_goal,
                        ):
                            yield chunk
                        return

                    # Execute normal MCP tool
                    _tools_ran_this_turn = True
                    relevant_tools = _ensure_tool_schema(tool_name, relevant_tools)

                    # ── Duplicate call guard ───────────────────────────────────────
                    # If Gemini calls the SAME tool with the SAME args again, it means
                    # it failed to read the previous tool result. Break and surface results.
                    import json as _json
                    _call_fingerprint = f"{tool_name}:{_json.dumps(tool_args, sort_keys=True)}"
                    if _call_fingerprint in _seen_tool_calls:
                        logger.warning(
                            f"[Gemini] Duplicate tool call detected: {tool_name}. "
                            f"Breaking loop — surfacing existing results."
                        )
                        break
                    _seen_tool_calls.add(_call_fingerprint)

                    result = execute_tool_call(tool_name, tool_args)
                    result_str = str(result)
                    result_preview = result_str[:150].replace("\n", " ")
                    log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")
                    _gemini_tool_history.append(
                        f"[Tool: {tool_name} args={tool_args}] Result: {result_str[:600]}"
                    )
                    continue  # Next turn: Gemini summarises the tool result — has_results=True

                # ── Plain text response — this is the final answer ─────────────────
                else:
                    # Detect browser intent in plain text when no tools ran yet
                    if not _tools_ran_this_turn and _detect_browser_intent(raw_response, prompt):
                        logger.info("[Gemini] Browser intent in text. Launching agentic loop.")
                        client_ol = AsyncClient(host=OLLAMA_HOST)
                        messages_ol = [
                            {"role": "system", "content": personality},
                            {"role": "system", "content": datetime_ctx},
                        ] + list(_conversation_history) + [{"role": "user", "content": prompt}]
                        async for chunk in execute_agentic_loop(
                            prompt, messages_ol, client_ol, relevant_tools,
                            [], _conversation_history, _llm_chat_with_retry
                        ):
                            yield chunk
                        return

                    streamer = SentenceStreamer()
                    streamer.add_token(raw_response)
                    streamer.flush()
                    manager.wait_to_finish()

                    log_to_hud("VoxKage", f"🤖 [Gemini] {raw_response}")
                    _conversation_history.append({"role": "user", "content": prompt})
                    _conversation_history.append({"role": "assistant", "content": raw_response})
                    if len(_conversation_history) > MAX_HISTORY * 2:
                        _conversation_history = _conversation_history[-(MAX_HISTORY * 2):]

                    yield raw_response
                    _final_yielded = True
                    return

            if not _final_yielded:
                # Loop ended without a text response — surface the last tool result directly
                if _gemini_tool_history:
                    last_result = _gemini_tool_history[-1]
                    # Extract just the result part (after "Result: ")
                    result_text = last_result.split("] Result: ", 1)[-1] if "] Result: " in last_result else last_result
                    streamer = SentenceStreamer()
                    streamer.add_token(result_text)
                    streamer.flush()
                    manager.wait_to_finish()
                    log_to_hud("VoxKage", result_text)
                    _conversation_history.append({"role": "user", "content": prompt})
                    _conversation_history.append({"role": "assistant", "content": result_text})
                    yield result_text
                else:
                    yield "Done, sir."
            return

        except Exception as _gemini_err:
            import llm.constants as _consts
            _consts.ENGINE = "ollama"
            logger.warning(
                f"[HybridEngine] Gemini CLI failed: {_gemini_err}. "
                f"Switching to Ollama for this session."
            )
            log_to_hud("VoxKage", "🏠 [Ollama] Gemini unavailable — switching to local model.")
            # Falls through to the Ollama pipeline below

    # ═══════════ END HYBRID ENGINE ROUTER ═══════════════════════════════════════

    # ═══════════ PRE-LLM EMAIL INTERCEPTOR ═══════════
    # Catches email intents in pure Python BEFORE the prompt reaches the LLM.
    # The LLM never sees compose/send/edit requests — zero hallucination possible.
    email_intent = detect_email_intent(prompt)
    if email_intent:
        action = email_intent["action"]
        logger.info(f"Email intent intercepted: {action}")

        def _speak_and_log(text: str):
            """Speak text via TTS and write to HUD. Used by the email interceptor."""
            log_to_hud("VoxKage", text)
            streamer = SentenceStreamer()
            streamer.add_token(text)
            streamer.flush()
            _conversation_history.append({"role": "user", "content": prompt})
            _conversation_history.append({"role": "assistant", "content": text})

        try:
            if action == "compose":
                announce = "Drafting now, sir."
                log_to_hud("VoxKage", announce)
                streamer = SentenceStreamer()
                streamer.add_token(announce)
                streamer.flush()
                yield announce

                log_to_hud("VoxKage", "📧 Composing email via sub-agent...")
                result = handle_compose(email_intent["recipient"], email_intent["instructions"])
                log_to_hud("VoxKage", result)
                streamer2 = SentenceStreamer()
                streamer2.add_token(result)
                streamer2.flush()
                yield result
                _conversation_history.append({"role": "user", "content": prompt})
                _conversation_history.append({"role": "assistant", "content": f"{announce} {result}"})
                return

            elif action == "send":
                result = handle_send()
                log_to_hud("VoxKage", result)
                streamer = SentenceStreamer()
                streamer.add_token(result)
                streamer.flush()
                yield result
                _conversation_history.append({"role": "user", "content": prompt})
                _conversation_history.append({"role": "assistant", "content": result})
                return

            elif action == "edit":
                announce = "Updating draft now, sir."
                log_to_hud("VoxKage", announce)
                streamer = SentenceStreamer()
                streamer.add_token(announce)
                streamer.flush()
                yield announce

                log_to_hud("VoxKage", "📧 Editing draft via sub-agent...")
                result = handle_edit(email_intent["instructions"])
                log_to_hud("VoxKage", result)
                streamer2 = SentenceStreamer()
                streamer2.add_token(result)
                streamer2.flush()
                yield result
                _conversation_history.append({"role": "user", "content": prompt})
                _conversation_history.append({"role": "assistant", "content": f"{announce} {result}"})
                return

            elif action == "cancel":
                result = handle_cancel()
                log_to_hud("VoxKage", result)
                streamer = SentenceStreamer()
                streamer.add_token(result)
                streamer.flush()
                yield result
                _conversation_history.append({"role": "user", "content": prompt})
                _conversation_history.append({"role": "assistant", "content": result})
                return

        except Exception as e:
            err = f"Email operation failed: {str(e)}"
            logger.error(err)
            log_to_hud("VoxKage", err)
            yield err
            return
    # ═══════════ END EMAIL INTERCEPTOR ═══════════

    is_running = await check_ollama_server()
    if not is_running:
        yield "Error: Ollama server is not running or unreachable. Please start Ollama."
        return

    is_file_injection = "[UI_FILE_INJECTION]" in prompt
    if is_file_injection:
        prompt = prompt.replace("[UI_FILE_INJECTION]", "").strip()
        logger.info("File injection detected: passing to tool engine.")

    client = AsyncClient(host=OLLAMA_HOST)

    # ─── Retrieve relevant tools for this query (Phase 1: Tool RAG) ───────────
    relevant_tools, retrieved_names = _get_tools_for_query(prompt)
    logger.info(f"[RAG] Injecting {len(relevant_tools)} tools: {retrieved_names}")

    # ─── Retrieve relevant memories for this query (Phase 2: Mem0) ──────────────
    memory_snippet = ""
    if USE_MEMORY:
        try:
            from llm.memory_manager import search_memory
            memory_snippet = search_memory(prompt)
        except Exception as _me:
            logger.warning(f"[Memory] Search failed (non-fatal): {_me}")

    # ─── Build message context ─────────────────────────────────────────────────
    messages = []

    # System: personality (fixed, lean)
    personality = system_prompt if system_prompt else get_personality_prompt()
    messages.append({"role": "system", "content": personality})

    # System: date/time (always)
    messages.append({"role": "system", "content": get_datetime_context()["content"]})

    # System: dynamic routing hints for retrieved tools (replaces hard-coded Part 1)
    routing_hints = get_routing_hints(retrieved_names)
    if routing_hints:
        messages.append({"role": "system", "content": routing_hints})

    # System: Mem0 memory snippet (Phase 2 — personalization context)
    if memory_snippet:
        messages.append({"role": "system", "content": memory_snippet})

    # System: legacy search persistence (last_search.json)
    persistent_memory = get_persistent_memory()
    if persistent_memory:
        messages.append({"role": "system", "content": persistent_memory})

    # Conversation history
    for msg in _conversation_history:
        messages.append(msg)

    # User prompt
    messages.append({"role": "user", "content": prompt})

    manager.was_interrupted = False

    try:
        streamer = SentenceStreamer()
        if use_tools:
            # Multi-turn tool execution loop
            max_tool_turns = 10
            _tools_ran_this_turn = False

            for turn in range(max_tool_turns):
                # Respect Ctrl+Space interruption between turns
                if manager.was_interrupted:
                    logger.info("[llm_client] Interruption detected. Stopping tool loop.")
                    yield "Got it — stopping."
                    return

                response = await _llm_chat_with_retry(client, messages, tools=relevant_tools)
                message = response.message

                # 1. Handle tool calls
                if hasattr(message, 'tool_calls') and message.tool_calls:
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

                        # ── On-demand schema fetch for out-of-set tools ─────────
                        relevant_tools = _ensure_tool_schema(tool_name, relevant_tools)

                        # Agentic loop handover
                        if tool_name in ["agent_thinking", "agent_step"]:
                            logger.info(f"Handover to agentic loop via: {tool_name}")
                            async for chunk in execute_agentic_loop(
                                prompt, messages, client, relevant_tools,
                                [], _conversation_history, _llm_chat_with_retry
                            ):
                                yield chunk
                            return

                        # Execute local tool
                        _tools_ran_this_turn = True
                        result = execute_tool_call(tool_name, arguments)
                        result_preview = str(result)[:150].replace("\n", " ")
                        log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")

                        messages.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": str(result)
                        })

                    continue  # Next turn: let LLM summarize tool results

                # 2. Handle final text response (no tool calls)
                else:
                    content = message.content or ""

                    # 2.1 Rescue Hallucinated Qwen XML Tool Calls
                    function_match = re.search(r'<function=([^>]+)>\s*(.*?)\s*</function>', content, re.DOTALL)
                    if function_match:
                        tool_name = function_match.group(1).strip()
                        try:
                            json_str = function_match.group(2).strip()
                            args = json.loads(json_str)
                            logger.info(f"Rescued XML tool call hallucination: {tool_name}")
                            relevant_tools = _ensure_tool_schema(tool_name, relevant_tools)
                            _tools_ran_this_turn = True
                            result = execute_tool_call(tool_name, args)
                            result_preview = str(result)[:150].replace("\n", " ")
                            log_to_hud("VoxKage", f"🔧 [{tool_name}] {result_preview}")
                            messages.append({
                                "role": "tool",
                                "name": tool_name,
                                "content": str(result)
                            })
                            continue
                        except Exception as e:
                            logger.error(f"Failed to parse rescued XML args: {e}")

                    # 2.2 Intercept browser intent ONLY if no tools have run yet this turn
                    if not _tools_ran_this_turn and _detect_browser_intent(content, prompt):
                        logger.info("Browser intent detected in text (no tools ran yet). Launching agentic loop.")
                        async for chunk in execute_agentic_loop(
                            prompt, messages, client, relevant_tools,
                            [], _conversation_history, _llm_chat_with_retry
                        ):
                            yield chunk
                        return

                    # Handle conversational text
                    streamer.add_token(content)
                    yield content

                    _conversation_history.append({"role": "user", "content": prompt})
                    _conversation_history.append({"role": "assistant", "content": content})
                    while len(_conversation_history) > MAX_HISTORY * 2:
                        _conversation_history.pop(0)

                    # ── Async memory update (non-blocking, Phase 2) ──────────────
                    if USE_MEMORY:
                        try:
                            import asyncio
                            from llm.memory_manager import add_memory_async
                            asyncio.ensure_future(add_memory_async(prompt, content))
                        except Exception:
                            pass

                    break

            streamer.flush()
        else:
            # No-tools mode (file injection)
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