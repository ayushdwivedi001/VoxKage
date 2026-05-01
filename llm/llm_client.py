import asyncio
import logging
import json
import re
from datetime import datetime
import os

from llm.tool_registry import TOOLS_SCHEMA
from llm.mcp_dispatcher import dispatch_tool_call as execute_tool_call
from llm.helpers import SentenceStreamer, log_to_hud
from llm.constants import MAX_HISTORY, GLOBAL_LAST_RESULTS, USE_RAG, TOP_K_TOOLS, USE_MEMORY
from llm.helpers import (
    _extract_json_tool_call,
    _parse_text_intent,
    _detect_browser_intent,
    clear_session_memory
)
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

class ManagerFake:
    was_interrupted = False
manager = ManagerFake()

# ─── RAG: Tool retrieval helper ───────────────────────────────────────────────

def _get_tools_for_query(prompt: str) -> tuple[list, list[str]]:
    if not USE_RAG:
        from llm.tool_definitions import get_all_schemas, TOOL_DEFINITIONS
        schemas = get_all_schemas()
        names = [t["name"] for t in TOOL_DEFINITIONS]
        return schemas, names

    try:
        from llm.tool_rag import retrieve_tools, retrieve_tool_names
        schemas = retrieve_tools(prompt, top_k=TOP_K_TOOLS)
        names = [s["function"]["name"] for s in schemas]

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
        from llm.tool_definitions import get_all_schemas, TOOL_DEFINITIONS
        schemas = get_all_schemas()
        names = [t["name"] for t in TOOL_DEFINITIONS]
        return schemas, names


def _ensure_tool_schema(tool_name: str, current_schemas: list) -> list:
    current_names = {s["function"]["name"] for s in current_schemas}
    if tool_name not in current_names:
        try:
            from llm.tool_rag import get_schema_by_name
            s = get_schema_by_name(tool_name)
            if s:
                logger.info(f"[RAG] On-demand schema fetch for '{tool_name}'.")
                return current_schemas + [s]
        except Exception:
            pass
    return current_schemas


# ─── Main Response Stream ─────────────────────────────────────────────────────

async def generate_response_stream(prompt: str, system_prompt: str = "", use_tools: bool = True):
    global _conversation_history

    from llm.gemini_engine import ask_voxkage_brain, GeminiCLIError, clean_cli_json
    from llm.gemini_prompt_builder import build_voxkage_gemini_prompt

    # ═══════════ PRE-LLM EMAIL INTERCEPTOR ═══════════
    email_intent = detect_email_intent(prompt)
    if email_intent:
        action = email_intent["action"]
        logger.info(f"Email intent intercepted: {action}")
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

    is_file_injection = "[UI_FILE_INJECTION]" in prompt
    if is_file_injection:
        prompt = prompt.replace("[UI_FILE_INJECTION]", "").strip()
        logger.info("File injection detected: passing to tool engine.")

    # ── Phase 1: Assemble full context
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

    # ── Phase 2: Tool dispatch loop (up to 8 turns)
    MAX_GEMINI_TURNS = 8
    _gemini_tool_history: list[str] = []
    _tools_ran_this_turn = False
    _final_yielded = False
    _seen_tool_calls: set[str] = set()

    for _turn in range(MAX_GEMINI_TURNS):
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
        try:
            raw_response = await ask_voxkage_brain(full_prompt)
        except Exception as e:
            logger.error(f"[Gemini] Engine failure: {e}")
            yield "Gemini engine is unreachable. Please check your CLI configuration."
            return

        tool_call = None
        try:
            parsed = clean_cli_json(raw_response)
            if isinstance(parsed, dict) and "tool" in parsed:
                tool_call = parsed
        except (ValueError, Exception):
            pass

        if tool_call:
            tool_name = tool_call.get("tool", "")
            tool_args = tool_call.get("args", {})

            if not tool_name or tool_name == "done":
                break

            if tool_name in ("agent_thinking", "agent_step", "execute_browser_workflow"):
                logger.info(f"[Gemini] Browser handover via: {tool_name}")
                handover_goal = (
                    tool_args.get("goal")
                    or tool_args.get("thought")
                    or prompt
                )
                
                messages_gem = [
                    {"role": "system", "content": personality},
                    {"role": "system", "content": datetime_ctx},
                ] + list(_conversation_history) + [{"role": "user", "content": prompt}]
                
                async for chunk in execute_agentic_loop(
                    prompt, messages_gem, None, relevant_tools,
                    [], _conversation_history, None,
                    goal=handover_goal,
                ):
                    yield chunk
                return

            _tools_ran_this_turn = True
            relevant_tools = _ensure_tool_schema(tool_name, relevant_tools)

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
            continue
        else:
            if not _tools_ran_this_turn and _detect_browser_intent(raw_response, prompt):
                logger.info("[Gemini] Browser intent in text. Launching agentic loop.")
                messages_gem = [
                    {"role": "system", "content": personality},
                    {"role": "system", "content": datetime_ctx},
                ] + list(_conversation_history) + [{"role": "user", "content": prompt}]
                async for chunk in execute_agentic_loop(
                    prompt, messages_gem, None, relevant_tools,
                    [], _conversation_history, None
                ):
                    yield chunk
                return

            streamer = SentenceStreamer()
            streamer.add_token(raw_response)
            streamer.flush()

            log_to_hud("VoxKage", f"🤖 [Gemini] {raw_response}")
            _conversation_history.append({"role": "user", "content": prompt})
            _conversation_history.append({"role": "assistant", "content": raw_response})
            if len(_conversation_history) > MAX_HISTORY * 2:
                _conversation_history = _conversation_history[-(MAX_HISTORY * 2):]

            yield raw_response
            _final_yielded = True
            return

    if not _final_yielded:
        if _gemini_tool_history:
            last_result = _gemini_tool_history[-1]
            result_text = last_result.split("] Result: ", 1)[-1] if "] Result: " in last_result else last_result
            streamer = SentenceStreamer()
            streamer.add_token(result_text)
            streamer.flush()
            log_to_hud("VoxKage", result_text)
            _conversation_history.append({"role": "user", "content": prompt})
            _conversation_history.append({"role": "assistant", "content": result_text})
            yield result_text
        else:
            yield "Done, sir."
    return

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