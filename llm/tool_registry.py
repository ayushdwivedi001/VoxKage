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
            "name": "close_application",
            "description": "Safely closes a file, window, application, or folder process. Use this when the user asks to close an active file, app, or window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "The name of the file (e.g. 'VoxKage app related information.txt'), application name (e.g. 'chrome'), process name, or exact folder path to close."
                    }
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Execute a shell command on the user's PC and return output. USE THIS for VS Code extension management (code --list-extensions, code --install-extension), opening apps, process checks, and any task faster via CLI than GUI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute, e.g. 'code --list-extensions | findstr prettier'"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_and_index",
            "description": "RAG MEMORY GATE: Call this FIRST before reading any file. Automatically indexes new files, reindexes changed files, returns instantly from cache for unchanged files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the file to check and index."}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_rag",
            "description": "RAG MEMORY SEARCH: Semantic search across all indexed documents. Use to answer questions about any file VoxKage has seen before WITHOUT re-reading it. Always check_and_index the file first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language question, e.g. 'how did we handle API fallbacks'"},
                    "top_k": {"type": "integer", "description": "Number of chunks to return (default 5)"},
                    "file_filter": {"type": "string", "description": "Restrict search to files whose name/path contains this string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_document",
            "description": "RAG MEMORY: Index or re-index a single document. Auto-detects changes. Supports PDF, DOCX, XLSX, PPTX, TXT, and all code files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to file to index."}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_directory",
            "description": "RAG MEMORY: Bulk-index an entire directory or codebase. Skips unchanged files automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Absolute path to the folder to index."},
                    "extensions": {"type": "string", "description": "Optional comma-separated extensions, e.g. '.py,.md'. Leave empty for all."},
                    "recursive": {"type": "boolean", "description": "Index subdirectories (default true)"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_indexed_documents",
            "description": "RAG MEMORY: List all documents in VoxKage's knowledge base with their status.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_from_rag",
            "description": "RAG MEMORY: Remove a document from VoxKage's knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to file to remove."}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_clone",
            "description": "GITHUB: Clones a git repository to the local system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Repository URL"},
                    "dest_name": {"type": "string", "description": "Optional custom folder name. Defaults to repo name."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "GITHUB: Returns git status and recent commits for a local repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local git repository"}
                },
                "required": ["repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff_summary",
            "description": "GITHUB: Returns a summary of uncommitted changes (git diff) for a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local git repository"}
                },
                "required": ["repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_smart_commit",
            "description": "GITHUB: Adds all changes, commits, and optionally pushes to remote.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local git repository"},
                    "message": {"type": "string", "description": "Commit message"},
                    "push": {"type": "boolean", "description": "Whether to push after commit. Default true."}
                },
                "required": ["repo_path", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_pull",
            "description": "GITHUB: Pulls latest changes from remote.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local git repository"}
                },
                "required": ["repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fake_commit",
            "description": "GITHUB: Creates an empty commit and pushes to keep activity green.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local git repository"},
                    "message": {"type": "string", "description": "Optional commit message"}
                },
                "required": ["repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "detect_and_install_deps",
            "description": "GITHUB: Detects and installs project dependencies (pip, npm, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local repository"}
                },
                "required": ["repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_project",
            "description": "GITHUB: Runs a project command in the background.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local repository"},
                    "command": {"type": "string", "description": "Command to run (e.g., 'npm start', 'python app.py')"},
                    "port": {"type": "integer", "description": "Port the project runs on, for health checks"}
                },
                "required": ["repo_path", "command", "port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kill_project",
            "description": "GITHUB: Kills a project running in the background.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to local repository"}
                },
                "required": ["repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_project_health",
            "description": "GITHUB: Checks if a background project is responding on its port.",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "description": "Port to check"},
                    "url_path": {"type": "string", "description": "URL path to check (default '/')"}
                },
                "required": ["port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_github_profile",
            "description": "GITHUB: Gets profile details for a GitHub user or authenticated user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Optional GitHub username. If omitted, returns authenticated user."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_repos",
            "description": "GITHUB: Lists the authenticated user's repositories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max repos to return (default 20)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_repo_local",
            "description": "GITHUB: Creates a new GitHub repository remotely AND clones it locally automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Repository name"},
                    "description": {"type": "string", "description": "Repository description"},
                    "private": {"type": "boolean", "description": "Whether the repo is private (default true)"},
                    "init_readme": {"type": "boolean", "description": "Initialize with README (default true)"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "actions_list",
            "description": "GITHUB: Lists recent GitHub Actions workflow runs for a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name with owner (e.g., ayushdwivedi001/VoxKage)"},
                    "limit": {"type": "integer", "description": "Max runs to return (default 10)"}
                },
                "required": ["repo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "actions_get",
            "description": "GITHUB: Gets details and jobs for a specific GitHub Actions workflow run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name with owner"},
                    "run_id": {"type": "string", "description": "Workflow run ID"}
                },
                "required": ["repo", "run_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_job_logs",
            "description": "GITHUB: Downloads the raw logs for a specific GitHub Actions job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name with owner"},
                    "job_id": {"type": "string", "description": "Job ID from actions_get"}
                },
                "required": ["repo", "job_id"]
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
            "name": "search_spotify",
            "description": "Searches Spotify for a topic and returns the top 5 track titles. You MUST use this tool FIRST when the user asks to play a song on Spotify. DO NOT hallucinate song names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search term for songs."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify_selection",
            "description": "Plays a specific song by selecting its number (1 to 5) from the previous search results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "integer",
                        "description": "The number of the song the user wants to play (e.g., 1, 2, 3, 4, 5)."
                    }
                },
                "required": ["number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "play_user_playlist",
            "description": "Plays one of the user's saved playlists (e.g., 'true end', 'scenarios'). Use this when the user says 'play my usual songs' or asks for a specific playlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "playlist_name": {
                        "type": "string",
                        "description": "The name of the playlist, or 'random' to pick one randomly."
                    }
                },
                "required": ["playlist_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Pauses, plays, stops, skips music/videos, or retrieves currently playing song status. Use this when the user says 'pause', 'stop', 'resume', 'next song', or 'what song is playing?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "stop", "next", "prev", "status", "fullscreen"],
                        "description": "The action to perform."
                    },
                    "target": {
                        "type": "string",
                        "enum": ["auto", "spotify", "youtube"],
                        "description": "The target to control. Use 'auto' if you aren't sure."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_gmail",
            "description": "Checks the user's Gmail inbox for recent emails. Can filter by label (INBOX, UNREAD, SPAM) or search query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term (e.g., 'from:linkedin.com' or 'project update')."
                    },
                    "label": {
                        "type": "string",
                        "enum": ["INBOX", "UNREAD", "SPAM", "SENT", "STARRED"],
                        "description": "The Gmail label to search within (default: INBOX)."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of emails to fetch (default: 5)."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_summary",
            "description": "Gets the full text body of a specific email ID retrieved from check_gmail. Crucial for summarizing the actual contents of an email after finding it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The target email ID."
                    }
                },
                "required": ["email_id"]
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
            "name": "create_file",
            "description": "Creates a new file or folder. Supported types: word (.docx), excel (.xlsx), pptx, csv, txt, html, json, python, js, markdown, etc., or folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "directory": {"type": "string", "description": "Absolute path to directory"},
                    "content": {"type": "string", "description": "Content of the file (markdown supported for word files)"},
                    "file_type": {"type": "string", "enum": ["word", "excel", "pptx", "csv", "txt", "html", "json", "python", "js", "markdown", "folder", "auto"]},
                    "confirmed": {"type": "boolean", "description": "Always pass True unless you need user review first"}
                },
                "required": ["filename", "directory", "content", "file_type", "confirmed"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edits an existing file by appending or replacing content. If file path is not absolute, it will be searched for automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path or filename like 'AyushResume'"},
                    "edit_instructions": {"type": "string", "description": "New content to append or replace"},
                    "append": {"type": "boolean", "description": "True to add to end, False to replace entire file"},
                    "confirmed": {"type": "boolean", "description": "Always pass True unless you need user review first"}
                },
                "required": ["file_path", "edit_instructions", "append", "confirmed"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Moves a file or folder to the recycle bin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file or folder"},
                    "confirmed": {"type": "boolean", "description": "Must pass False first to show preview. If user agrees, call again with True."}
                },
                "required": ["path", "confirmed"]
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
    },
    # ── GUI Pilot ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "gui_thinking",
            "description": "Plan a multi-step desktop GUI automation task before acting. Call BEFORE any 3+ GUI step sequence. Like agent_thinking but for the local desktop.",
            "parameters": {"type": "object", "properties": {
                "goal": {"type": "string", "description": "What to accomplish on the desktop"},
                "plan": {"type": "string", "description": "Numbered step-by-step plan"}
            }, "required": ["goal", "plan"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_desktop_state",
            "description": "Take a full desktop screenshot and list all open windows. Always call this first before GUI automation to see current state.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_files",
            "description": "Session awareness: returns all files currently open across all apps (Word, VS Code, VLC, Notepad, PDF viewers). Use when user says 'my open file', 'the PDF I have open', 'my current doc'.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gui_step",
            "description": "Execute ONE atomic desktop GUI action with vision verification. Actions: screenshot, focus (bring app to front), click, right_click, double_click, find_and_click (vision-assisted — PREFERRED for clicking UI elements), type, hotkey, key, scroll, drag, read_screen, wait, open_file.",
            "parameters": {"type": "object", "properties": {
                "action":      {"type": "string", "enum": ["screenshot","focus","click","right_click","double_click","find_and_click","type","hotkey","key","scroll","drag","read_screen","wait","open_file"]},
                "goal":        {"type": "string"},
                "app":         {"type": "string", "description": "App name for focus/screenshot (e.g. 'VS Code', 'Notepad', 'Spotify')"},
                "x":           {"type": "integer", "description": "Click x coordinate"},
                "y":           {"type": "integer", "description": "Click y coordinate"},
                "description": {"type": "string", "description": "Element description for find_and_click (e.g. 'Install button next to Prettier')"},
                "text":        {"type": "string", "description": "Text to type"},
                "keys":        {"type": "string", "description": "Hotkey combo e.g. 'ctrl+s', 'ctrl+shift+x', 'win+d'"},
                "key":         {"type": "string", "description": "Single key e.g. 'enter', 'escape', 'tab', 'f5'"},
                "direction":   {"type": "string", "enum": ["down","up"]},
                "amount":      {"type": "integer"},
                "ms":          {"type": "integer"},
                "from_x":      {"type": "integer"},
                "from_y":      {"type": "integer"},
                "to_x":        {"type": "integer"},
                "to_y":        {"type": "integer"},
                "file_path":   {"type": "string", "description": "File path for open_file action"}
            }, "required": ["action", "goal"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_active_document",
            "description": "Read the full text of the currently active/focused document (Word, VS Code, Notepad, PDF). Use when user says 'what does my open file say' or 'summarize my current doc'.",
            "parameters": {"type": "object", "properties": {}, "required": []}
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
                        from voice.voice_manager import log_to_hud
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
