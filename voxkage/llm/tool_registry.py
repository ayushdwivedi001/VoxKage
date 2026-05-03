import logging
logger = logging.getLogger(__name__)

from automation.app_launcher import open_app, execute_special_command
from automation.browser_control import open_website, search_google
from automation.system_control import (
    set_volume, set_brightness, toggle_wifi, toggle_bluetooth, change_wallpaper_from_folder
)
from automation.web_agent import browse_and_extract, search_media_options, play_media_selection, execute_browser_workflow_sync, get_browser_state, agent_step_sync, control_media_web
from automation.document_parser import analyze_specific_file_sync, find_file
from automation.spotify_control import search_spotify_app, play_spotify_app, control_spotify_app, USER_PLAYLISTS, browse_spotify_search, browse_spotify_play, control_spotify_web, is_spotify_app_installed
from automation.gmail_manager import check_gmail, get_email_summary
# RAG Knowledge Base — imported lazily (rag_server resolves its own paths at import-time;
# importing at module level from inside the llm package breaks __file__ resolution)
def _rag(fn_name: str, **kwargs):
    from mcp_servers.rag_server import (
        index_document, check_and_index, query_rag,
        list_indexed_documents, delete_from_rag, index_directory,
    )
    _fns = {
        "index_document": index_document,
        "check_and_index": check_and_index,
        "query_rag": query_rag,
        "list_indexed_documents": list_indexed_documents,
        "delete_from_rag": delete_from_rag,
        "index_directory": index_directory,
    }
    return _fns[fn_name](**kwargs)

# GUI Pilot — imported lazily to avoid pyautogui import on headless machines
def _gui(fn_name: str, **kwargs):
    from mcp_servers.gui_server import gui_thinking, get_desktop_state, get_open_files, gui_step, read_active_document
    _fns = {
        "gui_thinking": gui_thinking, "get_desktop_state": get_desktop_state,
        "get_open_files": get_open_files, "gui_step": gui_step,
        "read_active_document": read_active_document,
    }
    return _fns[fn_name](**kwargs)





# Map tool names to actual functions in the codebase
def execute_tool_call(tool_name: str, arguments: dict):
    """
    Executes the matched function from the codebase.
    Returns a string containing the result to give back to the LLM or user.
    """
    try:
        import json
        args_str = json.dumps(arguments, indent=2)
        logger.debug(f"Executing {tool_name} tool with arguments:\n{args_str}")
        if tool_name == "open_application":
            app_name = arguments.get("app_name")
            return open_app(app_name)
            
        elif tool_name == "open_url":
            url = arguments.get("url")
            return open_website(url)
            
        elif tool_name == "search_web":
            query = arguments.get("query", "")
            # Always use VoxKage's internal Playwright browser, NOT the OS default browser.
            # This keeps everything inside Chrome (VoxKage session) and never opens Vivaldi.
            logger.info(f"[search_web] Using internal Playwright browser for: {query!r}")
            return browse_and_extract("https://duckduckgo.com", query)
            
        elif tool_name == "browse_and_extract":
            url = arguments.get("url", "google")
            query = arguments.get("query", "")
            return browse_and_extract(url, query)
            
        elif tool_name == "get_browser_state":
            return get_browser_state()
            
        elif tool_name == "search_media_options":
            platform = arguments.get("platform", "youtube")
            query = arguments.get("query", "")
            return search_media_options(platform, query)
            
        elif tool_name == "search_spotify":
            query = arguments.get("query", "")
            if is_spotify_app_installed():
                # Provide a nice list response like Youtube does
                opts = search_spotify_app(query)
                if opts:
                    log_text = "Found Spotify Tracks:\n" + "\n".join([f"{r['number']}: {r['title']}" for r in opts])
                    try:
                        from llm.helpers import log_to_hud
                        log_to_hud("VoxKage", log_text)
                    except:
                        pass
                    return (
                        f"Found {len(opts)} tracks on Spotify. Read these options to the user clearly (e.g. 'I found 5 songs. 1: [Title 1], 2: [Title 2]... Which one should I play?').\n"
                        f"OPTIONS: {opts}"
                    )
                # If opts is empty, likely we lack API keys. Fall through to web explicitly.
            return browse_spotify_search(query)
                
        elif tool_name == "play_spotify_selection":
            number = arguments.get("number", 1)
            if is_spotify_app_installed():
                from automation.spotify_control import play_spotify_selection_api
                res = play_spotify_selection_api(number)
                # If it failed or we don't have API keys, play_spotify_app returns a string starting with "Failed"
                if not "Failed" in res:
                    return res
            # Fallback
            return browse_spotify_play(number)
            
        elif tool_name == "play_user_playlist":
            pname = arguments.get("playlist_name", "random").lower()
            if "true end" in pname:
                uri = USER_PLAYLISTS["true end???"]
            elif "scenario" in pname:
                uri = USER_PLAYLISTS["scenarios"]
            else:
                import random
                uri = random.choice(list(USER_PLAYLISTS.values()))
                
            if is_spotify_app_installed():
                return play_spotify_app(uri) + " Note to self: Goal Met. Tell the user the playlist is started and ask if they need anything else."
            else:
                # web fallback won't have a direct URI playlist play easily unless we goto the URI in browser.
                # Spotify web can handle URIs! Just convert spotify:playlist:123 to open.spotify.com/playlist/123
                web_url = uri.replace("spotify:playlist:", "https://open.spotify.com/playlist/")
                from automation.web_agent import _dispatch_task
                return _dispatch_task("play_spotify_web_url", {"url": web_url}) + " Note to self: Goal Met. Tell the user the playlist is started."

        elif tool_name == "media_control":
            action = arguments.get("action")
            target = arguments.get("target", "auto")
            if target == "youtube":
                return control_media_web(action)
            elif target == "spotify":
                if is_spotify_app_installed():
                    return control_spotify_app(action)
                else:
                    return control_spotify_web(action)
            else:
                # Auto - try both
                r1 = control_media_web(action)
                if is_spotify_app_installed():
                    r2 = control_spotify_app(action)
                else:
                    r2 = control_spotify_web(action)
                return f"Auto control attempted. YouTube: {r1}. Spotify: {r2}."

        elif tool_name == "check_gmail":
            return check_gmail(
                query=arguments.get("query", ""),
                label=arguments.get("label", "INBOX"),
                max_results=arguments.get("max_results", 5)
            )

        elif tool_name == "get_email_summary":
            return get_email_summary(arguments.get("email_id"))

        # compose_email, edit_draft, send_draft are handled by the Python
        # interceptor in llm_client.py — they never reach this function.

        elif tool_name == "execute_browser_workflow":
            goal = arguments.get("goal", "")
            steps = arguments.get("steps", [])
            # === REPAIR: Fix malformed LLM arguments (flat keys instead of steps array) ===
            if not steps and not goal:
                # LLM generated flat keys like {"goto": "...", "click_best_link": "true", "search_query": "..."}
                # Convert to proper steps format
                repaired_steps = []
                if "goto" in arguments:
                    repaired_steps.append({"action": "goto", "url": arguments["goto"]})
                if "search_on_site" in arguments or "site" in arguments:
                    repaired_steps.append({
                        "action": "search_on_site",
                        "site": arguments.get("site", ""),
                        "query": arguments.get("search_query", arguments.get("query", ""))
                    })
                if "type" in arguments:
                    repaired_steps.append({
                        "action": "type",
                        "selector": arguments.get("selector", ""),
                        "text": arguments.get("type", ""),
                        "intent": arguments.get("intent", "")
                    })
                if "click" in arguments:
                    repaired_steps.append({
                        "action": "click",
                        "selector": arguments.get("selector", ""),
                        "intent": arguments.get("intent", "")
                    })
                if "click_best_link" in arguments:
                    repaired_steps.append({"action": "click_best_link"})
                if "extract_text" in arguments:
                    repaired_steps.append({"action": "extract_text"})
                if not repaired_steps:
                    # Fallback: try to build a basic search step from available keys
                    if "search_query" in arguments or "query" in arguments:
                        site = arguments.get("site", "")
                        query = arguments.get("search_query", arguments.get("query", ""))
                        if site:
                            repaired_steps.append({
                                "action": "search_on_site",
                                "site": site,
                                "query": query
                            })
                        else:
                            repaired_steps.append({
                                "action": "goto",
                                "url": "https://duckduckgo.com"
                            })
                            repaired_steps.append({
                                "action": "type",
                                "intent": "search",
                                "text": query
                            })
                            repaired_steps.append({"action": "click_best_link"})
                            repaired_steps.append({"action": "extract_text"})
                goal = arguments.get("goal", "Browser task")
                steps = repaired_steps
                import logging
                logging.getLogger(__name__).warning(f"Repaired malformed execute_browser_workflow arguments: {arguments} -> goal={goal}, steps={steps}")
            return execute_browser_workflow_sync(goal, steps)
            
        elif tool_name == "play_media_selection":
            number = arguments.get("number", 1)
            try:
                number = int(number)
            except:
                number = 1
            return play_media_selection(number)
            
        elif tool_name == "system_control":
            action = arguments.get("action", "").lower()
            lvl = arguments.get("level")
            
            if action == 'volume' and lvl is not None:
                return set_volume(lvl)
            elif action == 'brightness' and lvl is not None:
                return set_brightness(lvl)
            elif action == 'wifi_on':
                return toggle_wifi(True)
            elif action == 'wifi_off':
                return toggle_wifi(False)
            elif action == 'bluetooth_on':
                return toggle_bluetooth(True)
            elif action == 'bluetooth_off':
                return toggle_bluetooth(False)
            elif action == 'wallpaper':
                return change_wallpaper_from_folder()
            elif action in ['shutdown', 'restart', 'sleep']:
                return execute_special_command(action)
            else:
                return f"Unsupported system control action: {action}"
                
        elif tool_name == "analyze_specific_file":
            file_path = arguments.get("file_path", "")
            query = arguments.get("query", "")
            return analyze_specific_file_sync(file_path, query)
            
        elif tool_name == "find_and_analyze_file":
            keyword = arguments.get("filename_keyword", "")
            query = arguments.get("query", "")
            file_path = find_file(keyword)
            if file_path:
                return f"Found file at {file_path}. Content:\n" + analyze_specific_file_sync(file_path, query)
            else:
                return f"Could not find any file matching '{keyword}' in Documents, Downloads, or Desktop."

        # ── RAG Knowledge Base ───────────────────────────────────────────────
        elif tool_name == "check_and_index":
            return _rag("check_and_index", file_path=arguments.get("file_path", ""))

        elif tool_name == "query_rag":
            return _rag(
                "query_rag",
                query=arguments.get("query", ""),
                top_k=int(arguments.get("top_k", arguments.get("n_results", 5))),
                file_filter=arguments.get("file_filter", ""),
            )

        elif tool_name == "index_document":
            return _rag("index_document", file_path=arguments.get("file_path", ""))

        elif tool_name == "list_indexed_documents":
            return _rag("list_indexed_documents")

        elif tool_name == "delete_from_rag":
            return _rag("delete_from_rag", file_path=arguments.get("file_path", ""))

        elif tool_name == "index_directory":
            return _rag(
                "index_directory",
                directory=arguments.get("directory", ""),
                extensions=arguments.get("extensions", ""),
                recursive=bool(arguments.get("recursive", True)),
            )
                
        elif tool_name == "take_screenshot":
            from automation.screenshot import take_screenshot
            filepath = take_screenshot()
            if filepath:
                return f"Successfully took a screenshot and saved it to: {filepath}"
            else:
                return "Failed to take screenshot."
                
        elif tool_name == "agent_thinking":
            thought = arguments.get("thought", "")
            next_action = arguments.get("next_action", "")
            from llm.helpers import log_to_hud

            # Check for GOAL_MET sentinel — signals the loop to exit cleanly
            is_goal_met = thought.upper().startswith("GOAL MET")

            if is_goal_met:
                log_to_hud("VoxKage", f"✅ Goal Achieved: {thought}")
                return f"GOAL_MET: {thought}"
            else:
                log_to_hud("VoxKage", f"🧠 Thinking: {thought}\n→ Next: {next_action}")
                return (
                    f"THINKING LOGGED: {thought}\n"
                    f"NEXT PLANNED: {next_action}\n"
                    f"━━━ YOU MUST CALL A TOOL IMMEDIATELY ━━━\n"
                    f"Do NOT write any text. Do NOT output JSON. Call exactly one tool now:\n"
                    f"  → If next is navigation: call agent_step(action='goto', url='<site>')\n"
                    f"  → If next is search: call agent_step(action='search_on_page', query='<q>')\n"
                    f"  → If next is extract: call agent_step(action='extract_text')\n"
                    f"  → If task is done: call agent_thinking(thought='GOAL MET: <summary>')\n"
                    f"FAILING TO CALL A TOOL WILL ABORT THE TASK."
                )
            
        elif tool_name == "agent_step":
            return agent_step_sync(arguments)

        elif tool_name == "gui_thinking":
            return _gui("gui_thinking", goal=arguments.get("goal",""), plan=arguments.get("plan",""))

        elif tool_name == "get_desktop_state":
            return _gui("get_desktop_state")

        elif tool_name == "get_open_files":
            return _gui("get_open_files")

        elif tool_name == "gui_step":
            return _gui("gui_step", **arguments)

        elif tool_name == "read_active_document":
            return _gui("read_active_document")

        else:
            return f"Tool {tool_name} not found in registry."
            
    except Exception as e:
        return f"Error executing tool {tool_name}: {str(e)}"
