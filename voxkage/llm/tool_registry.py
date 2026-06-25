import logging
logger = logging.getLogger(__name__)

from voxkage.automation.app_launcher import open_app, execute_special_command
from voxkage.automation.browser_control import open_website, search_google
from voxkage.automation.system_control import (
    set_volume, set_brightness, toggle_wifi, toggle_bluetooth
)
from voxkage.automation.web_agent import browse_and_extract, search_media_options, play_media_selection, execute_browser_workflow_sync, get_browser_state, agent_step_sync, control_media_web
from voxkage.automation.document_parser import analyze_specific_file_sync, find_file
from voxkage.automation.spotify_control import search_spotify_app, play_spotify_app, control_spotify_app, USER_PLAYLISTS, browse_spotify_search, browse_spotify_play, control_spotify_web, is_spotify_app_installed
from voxkage.automation.gmail_manager import check_gmail, get_email_summary
# RAG Knowledge Base — imported lazily (rag_server resolves its own paths at import-time;
# importing at module level from inside the llm package breaks __file__ resolution)
def _rag(fn_name: str, **kwargs):
    from voxkage.mcp_servers.rag_server import (
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

# Coding Engine (ACE) — imported lazily to share RAG/ChromaDB state
def _coding(fn_name: str, **kwargs):
    from voxkage.mcp_servers.coding_server import (
        coding_thinking, get_code_skeleton,
        update_coding_plan, get_coding_plan,
    )
    _fns = {
        "coding_thinking": coding_thinking,
        "get_code_skeleton": get_code_skeleton,
        "update_coding_plan": update_coding_plan,
        "get_coding_plan": get_coding_plan,
    }
    return _fns[fn_name](**kwargs)

# Session Logging — imported lazily
def _session(fn_name: str, **kwargs):
    from voxkage.mcp_servers.session_server import (
        create_session_log, list_sessions,
        get_session_log, search_sessions,
    )
    _fns = {
        "create_session_log": create_session_log,
        "list_sessions": list_sessions,
        "get_session_log": get_session_log,
        "search_sessions": search_sessions,
    }
    return _fns[fn_name](**kwargs)

# GUI Pilot — imported lazily to avoid pyautogui import on headless machines
def _gui(fn_name: str, **kwargs):
    from voxkage.mcp_servers.gui_server import gui_thinking, get_desktop_state, get_open_files, gui_step, read_active_document
    _fns = {
        "gui_thinking": gui_thinking, "get_desktop_state": get_desktop_state,
        "get_open_files": get_open_files, "gui_step": gui_step,
        "read_active_document": read_active_document,
    }
    return _fns[fn_name](**kwargs)

# Cognitive Core — imported lazily
def _cognitive(fn_name: str, **kwargs):
    from voxkage.mcp_servers.cognitive_core_server import (
        start_turn, pre_mortem, checkpoint, reflect, verify,
        refine, learn, user_corrected, get_profile, log_tool_execution
    )
    _fns = {
        "start_turn": start_turn,
        "pre_mortem": pre_mortem,
        "checkpoint": checkpoint,
        "reflect": reflect,
        "verify": verify,
        "refine": refine,
        "learn": learn,
        "user_corrected": user_corrected,
        "get_profile": get_profile,
        "log_tool_execution": log_tool_execution
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
                        from voxkage.llm.helpers import log_to_hud
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
                from voxkage.automation.spotify_control import play_spotify_selection_api
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
                from voxkage.automation.web_agent import _dispatch_task
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
                from voxkage.mcp_servers.os_control_server import set_wallpaper
                import os
                pics = os.path.join(os.path.expanduser("~"), "Pictures")
                return set_wallpaper(pics)
            elif action in ['shutdown', 'restart', 'sleep']:
                return execute_special_command(action)
            else:
                return f"Unsupported system control action: {action}"

        # ── New atomic OS control tools ──────────────────────────────────────
        elif tool_name == "set_volume":
            from voxkage.automation.system_control import set_volume as _sv
            return _sv(arguments.get("level", 50))

        elif tool_name == "get_volume":
            from voxkage.automation.system_control import get_volume as _gv
            return _gv()

        elif tool_name == "toggle_mute":
            from voxkage.automation.system_control import mute_microphone
            # toggle system audio mute via pycaw directly
            try:
                from voxkage.automation.system_control import _PYCAW_AVAILABLE, _AudioUtilities, _IAudioEndpointVolume, _CLSCTX_ALL, _ctypes_cast, _POINTER
                if _PYCAW_AVAILABLE:
                    devices = _AudioUtilities.GetSpeakers()
                    interface = devices.Activate(_IAudioEndpointVolume._iid_, _CLSCTX_ALL, None)
                    vol = _ctypes_cast(interface, _POINTER(_IAudioEndpointVolume))
                    vol.SetMute(1 if arguments.get("mute", True) else 0, None)
                    return f"Audio {'muted' if arguments.get('mute') else 'unmuted'}."
            except Exception:
                pass
            import pyautogui; pyautogui.press("volumemute")
            return "Audio muted/unmuted (keypress fallback)."

        elif tool_name == "set_brightness":
            from voxkage.automation.system_control import set_brightness as _sb
            return _sb(arguments.get("level", 70))

        elif tool_name == "get_brightness":
            from voxkage.automation.system_control import get_brightness as _gb
            return _gb()

        elif tool_name == "toggle_night_light":
            from voxkage.automation.system_control import toggle_night_light as _tnl
            return _tnl(arguments.get("enable", True))

        elif tool_name == "toggle_dark_mode":
            from voxkage.automation.system_control import toggle_dark_mode as _tdm
            return _tdm(arguments.get("dark", True))

        elif tool_name == "power_action":
            return execute_special_command(arguments.get("action", "lock"))

        elif tool_name == "schedule_shutdown":
            from voxkage.automation.system_control import schedule_shutdown as _ss
            return _ss(arguments.get("minutes", 30))

        elif tool_name == "cancel_scheduled_shutdown":
            from voxkage.automation.system_control import cancel_scheduled_shutdown as _css
            return _css()

        elif tool_name == "set_power_plan":
            from voxkage.automation.system_control import set_power_profile
            return set_power_profile(arguments.get("mode", "balanced"))

        elif tool_name == "get_battery_status":
            from voxkage.automation.system_control import get_battery_status as _gbs
            return _gbs()

        elif tool_name == "get_system_info":
            from voxkage.automation.system_control import get_system_info as _gsi
            return _gsi()

        elif tool_name == "get_disk_usage":
            from voxkage.automation.system_control import get_disk_usage as _gdu
            return _gdu()

        elif tool_name == "get_system_uptime":
            from voxkage.automation.system_control import system_uptime
            return system_uptime()

        elif tool_name == "get_running_processes":
            from voxkage.automation.system_control import get_running_processes as _grp
            return _grp(arguments.get("sort_by", "cpu"), arguments.get("top_n", 15))

        elif tool_name == "kill_process":
            from voxkage.automation.system_control import kill_process_by_name
            return kill_process_by_name(arguments.get("name_or_pid", ""))

        elif tool_name == "suspend_process":
            from voxkage.automation.system_control import suspend_process as _sp
            return _sp(arguments.get("name", ""))

        elif tool_name == "resume_process":
            from voxkage.automation.system_control import resume_process as _rp
            return _rp(arguments.get("name", ""))

        elif tool_name == "boost_process_priority":
            from voxkage.automation.system_control import boost_process
            return boost_process(arguments.get("name", ""))

        elif tool_name == "get_startup_programs":
            from voxkage.automation.system_control import get_startup_programs as _gsp
            return _gsp()

        elif tool_name == "toggle_wifi":
            return toggle_wifi(arguments.get("enable", True))

        elif tool_name == "toggle_bluetooth":
            return toggle_bluetooth(arguments.get("enable", True))

        elif tool_name == "toggle_hotspot":
            from voxkage.automation.system_control import toggle_hotspot
            return toggle_hotspot(arguments.get("enable", True))

        elif tool_name == "toggle_airplane_mode":
            from voxkage.automation.system_control import toggle_airplane_mode as _tam
            return _tam(arguments.get("enable", True))

        elif tool_name == "get_network_status":
            from voxkage.automation.system_control import get_network_status as _gns
            return _gns()

        elif tool_name == "get_wifi_networks":
            from voxkage.automation.system_control import get_wifi_networks as _gwn
            return _gwn()

        elif tool_name == "connect_wifi":
            from voxkage.automation.system_control import connect_wifi as _cw
            return _cw(arguments.get("ssid", ""), arguments.get("password", ""))

        elif tool_name == "ping_host":
            from voxkage.automation.system_control import ping_host as _ph
            return _ph(arguments.get("host", "8.8.8.8"), arguments.get("count", 4))

        elif tool_name == "get_open_ports":
            from voxkage.automation.system_control import get_open_ports as _gop
            return _gop()

        elif tool_name == "run_network_speed_test":
            from voxkage.automation.system_control import run_speed_test
            return run_speed_test()

        elif tool_name == "flush_dns":
            from voxkage.automation.system_control import flush_dns
            return flush_dns()

        elif tool_name == "list_open_windows":
            from voxkage.automation.system_control import list_open_windows as _low
            return _low()

        elif tool_name == "minimize_window":
            from voxkage.automation.system_control import minimize_window as _mw
            return _mw(arguments.get("title", ""))

        elif tool_name == "maximize_window":
            from voxkage.automation.system_control import maximize_window as _mxw
            return _mxw(arguments.get("title", ""))

        elif tool_name == "tile_windows":
            from voxkage.automation.system_control import tile_windows as _tw
            return _tw(arguments.get("layout", "side_by_side"))

        elif tool_name == "get_installed_apps":
            from voxkage.automation.system_control import get_installed_apps as _gia
            return _gia(arguments.get("search", ""))

        elif tool_name == "get_clipboard":
            from voxkage.automation.system_control import get_clipboard_content
            return get_clipboard_content()

        elif tool_name == "set_clipboard":
            from voxkage.automation.system_control import set_clipboard_content
            return set_clipboard_content(arguments.get("text", ""))

        elif tool_name == "type_text":
            from voxkage.automation.system_control import type_text_input
            return type_text_input(arguments.get("text", ""), arguments.get("delay_ms", 30))

        elif tool_name == "press_hotkey":
            from voxkage.automation.system_control import press_keyboard_hotkey
            return press_keyboard_hotkey(arguments.get("keys", ""))

        elif tool_name == "clear_temp_files":
            from voxkage.automation.system_control import clear_temp_files as _ctf
            return _ctf()

        elif tool_name == "get_largest_files":
            from voxkage.automation.system_control import get_largest_files as _glf
            return _glf(arguments.get("directory", "~/Downloads"), arguments.get("count", 10))

        elif tool_name == "get_folder_size":
            from voxkage.automation.system_control import get_folder_size as _gfs
            return _gfs(arguments.get("path", ""))

        elif tool_name == "toggle_hidden_files":
            from voxkage.automation.system_control import toggle_hidden_files as _thf
            return _thf(arguments.get("show", True))

        elif tool_name == "toggle_focus_mode":
            from voxkage.automation.system_control import toggle_focus_mode
            return toggle_focus_mode(arguments.get("enable", True))

        elif tool_name == "update_all_software":
            from voxkage.automation.system_control import update_all_software
            return update_all_software()

        elif tool_name == "mute_microphone":
            from voxkage.automation.system_control import mute_microphone as _mm
            return _mm(arguments.get("mute", True))

        elif tool_name == "set_audio_output_device":
            from voxkage.automation.system_control import set_audio_output
            return set_audio_output(arguments.get("device_name", ""))

                
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
        
        # ── Coding Engine (ACE) ─────────────────────────────────────────────
        elif tool_name == "coding_thinking":
            return _coding(
                "coding_thinking",
                goal=arguments.get("goal", ""),
                project_dir=arguments.get("project_dir", ""),
                steps=arguments.get("steps", ""),
                rag_context=arguments.get("rag_context", ""),
            )
        
        elif tool_name == "get_code_skeleton":
            return _coding(
                "get_code_skeleton",
                file_path=arguments.get("file_path", ""),
            )
        
        elif tool_name == "update_coding_plan":
            return _coding(
                "update_coding_plan",
                step_number=arguments.get("step_number", 1),
                status=arguments.get("status", "done"),
            )
        
        elif tool_name == "get_coding_plan":
            return _coding("get_coding_plan")
                
        elif tool_name == "take_screenshot":
            from voxkage.automation.screenshot import take_screenshot
            filepath = take_screenshot()
            if filepath:
                return f"Successfully took a screenshot and saved it to: {filepath}"
            else:
                return "Failed to take screenshot."
                
        elif tool_name == "agent_thinking":
            thought = arguments.get("thought", "")
            next_action = arguments.get("next_action", "")
            from voxkage.llm.helpers import log_to_hud

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

        elif tool_name in ("create_session_log", "list_sessions", "get_session_log", "search_sessions"):
            return _session(tool_name, **arguments)

        elif tool_name in (
            "start_turn", "pre_mortem", "checkpoint", "reflect", "verify",
            "refine", "learn", "user_corrected", "get_profile", "log_tool_execution",
            "verify_code_file", "generate_critique", "optimize_cognitive_core"
        ):
            return _cognitive(tool_name, **arguments)

        else:
            return f"Tool {tool_name} not found in registry."
            
    except Exception as e:
        return f"Error executing tool {tool_name}: {str(e)}"
