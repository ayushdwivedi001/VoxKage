import logging
logger = logging.getLogger(__name__)

from automation.app_launcher import open_app, execute_special_command
from automation.browser_control import open_website, search_google
from automation.system_control import (
    set_volume, set_brightness, toggle_wifi, toggle_bluetooth, change_wallpaper_from_folder
)
from automation.web_agent import browse_and_extract, search_media_options, play_media_selection, execute_browser_workflow_sync, get_browser_state, agent_step_sync
from automation.document_parser import analyze_specific_file_sync, find_file

# Define schemas for Ollama
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Opens a local desktop application or folder (e.g., Chrome, VS Code, Notepad, Downloads).",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "The name of the application or folder to open."
                    }
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_browser_workflow",
            "description": "Executes a multi-step browser workflow. Use for ANY site-specific task (Amazon, Reddit, LinkedIn, Wikipedia, GitHub). For KNOWN SHOPPING/JOB SITES use search_on_site step for direct navigation. For RESEARCH, use DuckDuckGo with click_best_link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "The high-level goal of this workflow."
                    },
                    "steps": {
                        "type": "array",
                        "description": "Sequential web actions. Supported actions: goto (navigate to URL), search_on_site (PREFERRED for Amazon/Flipkart/LinkedIn/GitHub/YouTube/Reddit/Wikipedia — goes directly to site search), type (type in a field), click (click an element), click_best_link (click best SERP result, prefers target domain), extract_text (scrape page). For Amazon/Flipkart/Myntra use search_on_site. For research use: goto duckduckgo, then click_best_link, then extract_text.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["goto", "search_on_site", "type", "click", "click_best_link", "extract_text"]},
                                "url": {"type": "string", "description": "URL for goto action"},
                                "site": {"type": "string", "description": "Domain for search_on_site, e.g. 'amazon.in', 'flipkart.com', 'linkedin.com'"},
                                "query": {"type": "string", "description": "Search query for search_on_site action"},
                                "selector": {"type": "string"},
                                "intent": {"type": "string"},
                                "text": {"type": "string"}
                            },
                            "required": ["action"]
                        }
                    }
                },
                "required": ["goal", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Opens a specific website URL or known website (e.g., YouTube, Google, GitHub) in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL or name of the website to open."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Searches the web for a query. MUST be used when the user asks for information you do not know intrinsically (like current news, prices, realtime info, or 'the iPhone Test').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search term or question to query."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_control",
            "description": "Controls system settings like volume, brightness, wifi, bluetooth, shutdown, restart, sleep, or changes wallpaper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action to perform (e.g., 'volume', 'brightness', 'wifi_on', 'wifi_off', 'bluetooth_on', 'bluetooth_off', 'shutdown', 'restart', 'sleep', 'wallpaper')."
                    },
                    "level": {
                        "type": "integer",
                        "description": "The percentage level (0-100) for volume or brightness (only required if action is volume or brightness)."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse_and_extract",
            "description": "Uses Playwright to navigate to a site, perform a search, and read the webpage content. Use this to find prices, specific facts, or read articles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to. If empty or 'google', it searches Google."
                    },
                    "query": {
                        "type": "string",
                        "description": "The search term to type into the search box."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_media_options",
            "description": "Searches YouTube for a topic and returns the top 5 video titles. You MUST use this tool FIRST when the user asks to play a video or search YouTube. DO NOT hallucinate video names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "The platform to use (e.g., 'youtube')."
                    },
                    "query": {
                        "type": "string",
                        "description": "The search term for videos."
                    }
                },
                "required": ["platform", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_media_selection",
            "description": "Plays a specific video by selecting its number (1 to 5) from the previous search results. You MUST use this tool when the user says 'play number X'. DO NOT decline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "integer",
                        "description": "The number of the video the user wants to play (e.g., 1, 2, 3, 4, 5)."
                    }
                },
                "required": ["number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_specific_file",
            "description": "Reads the text of a specific local file (PDF, DOCX, TXT) so you can answer the user's questions about it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute path to the file to read."
                    },
                    "query": {
                        "type": "string",
                        "description": "The question the user asked about the file."
                    }
                },
                "required": ["file_path", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_and_analyze_file",
            "description": "Searches for a local file by a keyword in its name, reads it, and answers the user's questions about it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename_keyword": {
                        "type": "string",
                        "description": "A part of the filename to search for (e.g., 'report', 'invoice')."
                    },
                    "query": {
                        "type": "string",
                        "description": "The question the user asked about the file."
                    }
                },
                "required": ["filename_keyword", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Takes a local screenshot of what the user is currently seeing on their screen. Use this when the user asks you to 'take a screenshot' or 'capture this'.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_browser_state",
            "description": "Takes a screenshot and captures the current page URL, title, and visible text. Use this to visually verify what the browser is showing after any navigation or interaction step.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "agent_thinking",
            "description": "Use this to reason about your current browser state before taking the next action. Call this after each agent_step to decide what to do next. Your thought is shown to the user. Always call agent_step after thinking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "Your current reasoning about the browser state"
                    },
                    "next_action": {
                        "type": "string",
                        "description": "What you plan to do next"
                    }
                },
                "required": ["thought", "next_action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "agent_step",
            "description": "Execute ONE atomic browser action and take a screenshot. PREFERRED WORKFLOW: goto a site → search_on_page (uses the site's own search bar) → extract_text. search_on_page is like a human typing into the site's search box. Only use search_on_site (DuckDuckGo fallback) if search_on_page fails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["goto", "search_on_page", "search_on_site", "type", "click", "click_best_link", "scroll", "wait", "extract_text"],
                        "description": "goto: navigate to a URL. search_on_page: find the search bar on the CURRENT page and type a query into it (PREFERRED after goto). search_on_site: fall back to DuckDuckGo 'site:' search (only if search_on_page fails). type: type text into a specific element. click: click an element. scroll: scroll the page. wait: pause. extract_text: extract visible page text."
                    },
                    "goal": {
                        "type": "string",
                        "description": "The overall task goal"
                    },
                    "intent": {
                        "type": "string",
                        "description": "What this specific step is trying to achieve"
                    },
                    "url": {
                        "type": "string",
                        "description": "URL for goto action"
                    },
                    "site": {
                        "type": "string",
                        "description": "Domain for search_on_site, e.g. 'amazon.in'"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query for search_on_page or search_on_site"
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type for type action"
                    },
                    "selector": {
                        "type": "string",
                        "description": "Optional CSS selector for click/type"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up"],
                        "description": "For scroll: down or up"
                    },
                    "ms": {
                        "type": "integer",
                        "description": "For wait: milliseconds to wait"
                    }
                },
                "required": ["action", "goal"]
            }
        }
    }
]

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
            # === PHASE 4: Default platform to youtube when LLM omits it ===
            platform = arguments.get("platform", "youtube")
            query = arguments.get("query", "")
            return search_media_options(platform, query)
            
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
            from voice.voice_manager import log_to_hud

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
                
        else:
            return f"Tool {tool_name} not found in registry."
            
    except Exception as e:
        return f"Error executing tool {tool_name}: {str(e)}"
