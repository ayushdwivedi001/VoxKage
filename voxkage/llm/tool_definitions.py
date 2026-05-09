"""
VoxKage Tool Definitions — Single Source of Truth
All tool schemas live here. tool_rag.py indexes these; tool_registry.py executes them.
Adding a new skill = adding one entry to TOOL_DEFINITIONS. That's it.
"""

TOOL_DEFINITIONS = [
    # ─────────────────────────────────────────────
    # SYSTEM CONTROL
    # ─────────────────────────────────────────────
    {
        "name": "system_control",
        "description": (
            "Controls PC hardware and power state: adjust volume, adjust screen brightness, "
            "toggle WiFi, toggle Bluetooth, toggle mobile hotspot, toggle night light, "
            "run Intel DSA diagnostics, shutdown, restart, sleep, hibernate, lock screen."
        ),
        "tags": ["system", "hardware", "power", "wifi", "bluetooth", "volume", "brightness", "hotspot"],
        "example_queries": [
            "set volume to 50", "turn volume up", "make it louder", "mute the sound",
            "increase brightness", "dim the screen", "screen too bright",
            "turn wifi on", "disable wifi", "enable bluetooth", "bluetooth off",
            "turn on hotspot", "disable hotspot", "toggle night light",
            "run Intel DSA", "check diagnostics",
            "shutdown the computer", "turn off PC", "restart", "reboot",
            "put computer to sleep", "hibernate", "lock the screen"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "The action to perform. One of: 'set_volume', 'set_brightness', "
                        "'wifi_on', 'wifi_off', 'bluetooth_on', 'bluetooth_off', "
                        "'hotspot_on', 'hotspot_off', 'night_light_on', 'night_light_off', "
                        "'intel_dsa', 'shutdown', 'restart', 'sleep', 'hibernate', 'lock'."
                    )
                },
                "level": {
                    "type": "integer",
                    "description": "Percentage level (0–100) for volume or brightness only."
                }
            },
            "required": ["action"]
        }
    },

    # ─────────────────────────────────────────────
    # APP LAUNCHER
    # ─────────────────────────────────────────────
    {
        "name": "open_application",
        "description": (
            "Opens any installed desktop application, folder, or file on Windows. "
            "Use for: launching Chrome, Firefox, VS Code, Notepad, Discord, Steam, games, "
            "opening folders like Downloads, Documents, Pictures, Desktop, or any custom path. "
            "Also handles switching to an open window or closing an application."
        ),
        "tags": ["launcher", "app", "folder", "window"],
        "example_queries": [
            "open chrome", "launch VS Code", "start Discord", "open Downloads folder",
            "open Documents", "run Steam", "start Notepad", "switch to Firefox",
            "open my project folder", "launch the game", "open file explorer",
            "close Chrome", "bring up VS Code"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The name of the application, folder, or window to open."
                }
            },
            "required": ["app_name"]
        }
    },

    # ─────────────────────────────────────────────
    # WEB NAVIGATION
    # ─────────────────────────────────────────────
    {
        "name": "open_url",
        "description": (
            "Opens a specific website URL or named website in the browser. "
            "Use for: opening YouTube, Google, GitHub, Gmail, Reddit, Twitter, LinkedIn, "
            "any .com .in .org website, or a raw URL like https://example.com."
        ),
        "tags": ["browser", "web", "navigation", "url"],
        "example_queries": [
            "open YouTube", "go to GitHub", "open Gmail", "navigate to reddit.com",
            "open this link https://example.com", "take me to LinkedIn",
            "open Google", "go to Twitter", "open Netflix"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL or name of the website to open (e.g., 'youtube.com' or 'https://github.com')."
                }
            },
            "required": ["url"]
        }
    },

    # ─────────────────────────────────────────────
    # WEB SEARCH (quick facts)
    # ─────────────────────────────────────────────
    {
        "name": "search_web",
        "description": (
            "Searches the web for current information, facts, news, prices, weather, or any real-time query. "
            "Use this for: who is X, what is Y, current news, today's weather, stock prices, sports scores, "
            "definitions, quick factual lookups. Uses DuckDuckGo internally. "
            "Do NOT use for YouTube searches or Spotify — use dedicated media tools instead."
        ),
        "tags": ["web", "search", "facts", "news", "research"],
        "example_queries": [
            "what's the weather in Tokyo", "who is Elon Musk", "latest news about AI",
            "what is the price of Bitcoin", "current cricket score",
            "what year did World War 2 end", "definition of machine learning",
            "best programming languages 2025", "who won the Grammy",
            "what is the capital of France"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search term or question to look up on the web."
                }
            },
            "required": ["query"]
        }
    },

    # ─────────────────────────────────────────────
    # BROWSER EXTRACT (single page)
    # ─────────────────────────────────────────────
    {
        "name": "browse_and_extract",
        "description": (
            "Navigates Playwright browser to a specific URL, performs an optional search, "
            "and extracts the visible page text. Use when you need to read content from a specific page "
            "or search within a specific website. For multi-step workflows use execute_browser_workflow."
        ),
        "tags": ["browser", "web", "extract", "scrape", "read"],
        "example_queries": [
            "read the content of this page", "extract text from amazon.com",
            "search on Wikipedia for Python", "get the article from this URL",
            "what does this webpage say"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to. If empty or 'google', searches Google."
                },
                "query": {
                    "type": "string",
                    "description": "Optional search term to type into the page's search box."
                }
            },
            "required": ["query"]
        }
    },

    # ─────────────────────────────────────────────
    # BROWSER WORKFLOW (multi-step)
    # ─────────────────────────────────────────────
    {
        "name": "execute_browser_workflow",
        "description": (
            "Executes a predefined multi-step browser workflow. Use for ANY known-site task: "
            "Amazon product search, Flipkart shopping, LinkedIn profile lookup, GitHub repository browse, "
            "Reddit post search, Wikipedia article, Steam game page, YouTube channel. "
            "For RESEARCH across unknown sites, use the agentic loop (agent_thinking + agent_step) instead."
        ),
        "tags": ["browser", "automation", "shopping", "workflow", "amazon", "flipkart", "linkedin", "github"],
        "example_queries": [
            "search Amazon for wireless headphones", "find product on Flipkart",
            "look up on LinkedIn", "find GitHub repository", "search Reddit for",
            "Wikipedia article about", "Steam game price", "check Epic Games store"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "High-level goal of the workflow."
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered list of browser actions (goto, search_on_site, extract_text, click, type, click_best_link).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["goto", "search_on_site", "type", "click", "click_best_link", "extract_text"]},
                            "url": {"type": "string"},
                            "site": {"type": "string"},
                            "query": {"type": "string"},
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
    },

    # ─────────────────────────────────────────────
    # AGENTIC LOOP — THINKING
    # ─────────────────────────────────────────────
    {
        "name": "agent_thinking",
        "description": (
            "Logs reasoning and planning for complex multi-step browser tasks. "
            "Call this FIRST when a task needs 3+ browser steps (research, price comparison, forum reading). "
            "Use this tool to plan multi-step workflows. "
            "When the goal is complete, call with thought starting 'GOAL MET: <summary>'."
        ),
        "tags": ["agent", "reasoning", "planning", "browser", "complex"],
        "example_queries": [
            "research the latest GPU specs", "compare prices across websites",
            "find forum discussions about", "multi-step web research",
            "complex browser task", "autonomous browsing"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Current reasoning about the task state. Start with 'GOAL MET: <summary>' when complete."
                },
                "next_action": {
                    "type": "string",
                    "description": "The next tool you plan to call and why."
                }
            },
            "required": ["thought", "next_action"]
        }
    },

    # ─────────────────────────────────────────────
    # AGENTIC LOOP — STEP
    # ─────────────────────────────────────────────
    {
        "name": "agent_step",
        "description": (
            "Executes one atomic browser action and takes a screenshot for visual verification. "
            "Actions: goto (navigate URL), search_on_page (use site's own search bar — PREFERRED), "
            "search_on_site (DuckDuckGo fallback), type (fill a form field), click (click element), "
            "click_best_link (pick best search result), scroll, wait, extract_text (scrape page). "
            "Always call agent_thinking FIRST to plan, then use agent_step for each action."
        ),
        "tags": ["agent", "browser", "navigate", "click", "search", "extract", "automation"],
        "example_queries": [
            "navigate to a website", "click a button on the page", "type in a search box",
            "scroll down the page", "extract text from current page",
            "search within the site", "fill in a form", "autonomous web navigation"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["goto", "search_on_page", "search_on_site", "type", "click", "click_best_link", "scroll", "wait", "extract_text"]
                },
                "goal": {"type": "string", "description": "The overall task goal"},
                "intent": {"type": "string", "description": "What this specific step achieves"},
                "url": {"type": "string", "description": "URL for goto action"},
                "site": {"type": "string", "description": "Domain for search_on_site"},
                "query": {"type": "string", "description": "Search query for search_on_page or search_on_site"},
                "text": {"type": "string", "description": "Text to type for type action"},
                "selector": {"type": "string", "description": "CSS selector for click/type"},
                "direction": {"type": "string", "enum": ["down", "up"], "description": "Scroll direction"},
                "ms": {"type": "integer", "description": "Wait duration in milliseconds"}
            },
            "required": ["action", "goal"]
        }
    },

    # ─────────────────────────────────────────────
    # BROWSER STATE
    # ─────────────────────────────────────────────
    {
        "name": "get_browser_state",
        "description": (
            "Takes a screenshot of the current browser tab and returns the URL, page title, and visible text. "
            "Use to visually verify the browser state after navigation, or when unsure what page is open."
        ),
        "tags": ["browser", "screenshot", "verify", "state", "visual"],
        "example_queries": [
            "what page is open", "check the browser", "take a browser screenshot",
            "what does the browser show", "verify navigation worked"
        ],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # ─────────────────────────────────────────────
    # YOUTUBE SEARCH
    # ─────────────────────────────────────────────
    {
        "name": "search_media_options",
        "description": (
            "Searches YouTube for videos and returns the top 5 results with titles. "
            "ALWAYS call this FIRST before playing any YouTube video. "
            "Do NOT hallucinate video names — search first, then let the user pick."
        ),
        "tags": ["youtube", "video", "media", "search", "entertainment"],
        "example_queries": [
            "search YouTube for lofi music", "find videos about Python tutorial",
            "look up gaming videos on YouTube", "search YouTube for movie trailer",
            "YouTube search for cooking recipes", "find YouTube videos about",
            "play something on YouTube", "search on YouTube"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Platform to search (use 'youtube')."
                },
                "query": {
                    "type": "string",
                    "description": "Search term for videos."
                }
            },
            "required": ["platform", "query"]
        }
    },

    # ─────────────────────────────────────────────
    # YOUTUBE PLAY SELECTION
    # ─────────────────────────────────────────────
    {
        "name": "play_media_selection",
        "description": (
            "Plays a specific YouTube video by number (1–5) from the previous search_media_options results. "
            "ONLY call after search_media_options has returned results. "
            "Use when user says 'play number 2', 'play the first one', 'play the third video'."
        ),
        "tags": ["youtube", "video", "play", "media", "selection"],
        "example_queries": [
            "play number 1", "play the first video", "play the second one",
            "play video 3", "select option 4", "play the third result"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "number": {
                    "type": "integer",
                    "description": "Number of the video to play (1–5)."
                }
            },
            "required": ["number"]
        }
    },

    # ─────────────────────────────────────────────
    # SPOTIFY SEARCH
    # ─────────────────────────────────────────────
    {
        "name": "search_spotify",
        "description": (
            "Searches Spotify for songs, artists, or albums and returns top 5 tracks. "
            "ALWAYS call this FIRST when user asks to play a specific song or artist on Spotify. "
            "Do NOT hallucinate track names — search first. "
            "For generic music requests use play_user_playlist instead."
        ),
        "tags": ["spotify", "music", "song", "artist", "search", "media"],
        "example_queries": [
            "search Spotify for The Weeknd", "find song on Spotify",
            "search for Blinding Lights on Spotify", "play this artist on Spotify",
            "find this song", "search for album on Spotify",
            "look up track on Spotify"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Song, artist, or album to search for on Spotify."
                }
            },
            "required": ["query"]
        }
    },

    # ─────────────────────────────────────────────
    # SPOTIFY PLAY SELECTION
    # ─────────────────────────────────────────────
    {
        "name": "play_spotify_selection",
        "description": (
            "Plays a specific Spotify track by number (1–5) from the previous search_spotify results. "
            "Use when user says 'play number 2', 'play the first song', etc. after a Spotify search."
        ),
        "tags": ["spotify", "music", "play", "selection"],
        "example_queries": [
            "play number 1 on Spotify", "play the first song", "select track 2",
            "play the second result", "play option 3"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "number": {
                    "type": "integer",
                    "description": "Number of the track to play (1–5)."
                }
            },
            "required": ["number"]
        }
    },

    # ─────────────────────────────────────────────
    # SPOTIFY PLAYLIST
    # ─────────────────────────────────────────────
    {
        "name": "play_user_playlist",
        "description": (
            "Plays one of the user's saved Spotify playlists. "
            "Use when user says 'play my usual songs', 'play some music', 'I'm bored play something', "
            "'play my true end playlist', 'play scenarios playlist', 'shuffle my music', "
            "'play something random', or any generic music request without a specific track."
        ),
        "tags": ["spotify", "playlist", "music", "random", "saved", "media"],
        "example_queries": [
            "play my usual songs", "play some music", "I'm bored play something",
            "shuffle my playlist", "play something relaxing", "play background music",
            "play my saved music", "put on some tunes", "play lofi study music",
            "play my chill playlist", "play gaming music"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "playlist_name": {
                    "type": "string",
                    "description": "Name of the playlist ('true end', 'scenarios') or 'random' to pick randomly."
                }
            },
            "required": ["playlist_name"]
        }
    },

    # ─────────────────────────────────────────────
    # MEDIA CONTROL
    # ─────────────────────────────────────────────
    {
        "name": "media_control",
        "description": (
            "Controls media playback on Spotify or YouTube: play, pause, stop, next track, "
            "previous track, skip, get currently playing status. "
            "Use for: 'pause music', 'resume', 'stop', 'next song', 'skip', 'previous song', "
            "'what song is playing', 'what am I listening to'."
        ),
        "tags": ["media", "spotify", "youtube", "control", "pause", "play", "skip", "status"],
        "example_queries": [
            "pause the music", "resume playing", "stop music", "next song", "skip track",
            "previous song", "what song is playing", "what am I listening to",
            "pause YouTube", "stop Spotify"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "stop", "next", "previous", "skip", "status"],
                    "description": "The playback action: play, pause, stop, next, previous, skip, or status."
                },
                "target": {
                    "type": "string",
                    "enum": ["auto", "spotify", "youtube"],
                    "description": "Which media player to control: 'auto', 'spotify', or 'youtube'."
                }
            },
            "required": ["action"]
        }
    },

    # ─────────────────────────────────────────────
    # EMAIL (GMAIL)
    # ─────────────────────────────────────────────
    {
        "name": "check_email",
        "description": (
            "Checks the user's Gmail inbox or any folder/label with optional search. "
            "Returns recent emails with sender, subject, and snippet. "
            "Supports labels: INBOX, UNREAD, SENT, SPAM, TRASH, CATEGORY_PROMOTIONS, CATEGORY_SOCIAL, CATEGORY_UPDATES."
        ),
        "tags": ["email", "gmail", "inbox", "mail", "messages", "communication"],
        "example_queries": [
            "check my emails", "check inbox", "any new messages", "do I have mail",
            "check unread emails", "show me my emails", "check spam", "check promotions"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional Gmail search query (e.g. 'from:boss@company.com', 'subject:invoice', 'is:unread')."},
                "label": {"type": "string", "description": "Gmail label/folder (default: INBOX)."},
                "max_results": {"type": "integer", "description": "Number of emails to fetch (default: 5)."}
            },
            "required": []
        }
    },
    {
        "name": "read_email",
        "description": (
            "Reads the full body text of a specific email by its ID. "
            "Use after check_email to read a specific email."
        ),
        "tags": ["email", "gmail", "read", "body", "communication"],
        "example_queries": [
            "read the first email", "read that email", "tell me what it says",
            "read the email from Google", "read more about that"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The Gmail message ID from check_email."}
            },
            "required": ["email_id"]
        }
    },
    {
        "name": "send_email",
        "description": "Compose and send an email via Gmail immediately.",
        "tags": ["email", "gmail", "send", "compose", "communication"],
        "example_queries": ["send an email", "email to bob", "send this to friend@gmail.com"],
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject line."},
                "body": {"type": "string", "description": "Email body text."},
                "cc": {"type": "string", "description": "Optional CC addresses (comma-separated)."}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "reply_to_email",
        "description": "Reply to an existing email thread by ID.",
        "tags": ["email", "gmail", "reply", "communication"],
        "example_queries": ["reply to that email", "reply to the first message"],
        "parameters": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID to reply to."},
                "body": {"type": "string", "description": "Your reply text."}
            },
            "required": ["email_id", "body"]
        }
    },
    {
        "name": "delete_email",
        "description": "Move a specific email to Trash.",
        "tags": ["email", "gmail", "delete", "trash"],
        "example_queries": ["delete this email", "trash that message", "delete the first email"],
        "parameters": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID to delete."}
            },
            "required": ["email_id"]
        }
    },
    {
        "name": "mark_email_read",
        "description": "Mark a specific email as read.",
        "tags": ["email", "gmail", "read", "mark"],
        "example_queries": ["mark as read", "mark this email as read"],
        "parameters": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The message ID to mark as read."}
            },
            "required": ["email_id"]
        }
    },
    {
        "name": "get_email_stats",
        "description": "Get a quick summary of inbox stats: unread count, promotions, spam, etc.",
        "tags": ["email", "gmail", "stats", "summary"],
        "example_queries": ["how many unread emails", "check my email stats", "inbox summary"],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # ─────────────────────────────────────────────
    # SCREENSHOT
    # ─────────────────────────────────────────────
    {
        "name": "take_screenshot",
        "description": (
            "Captures a screenshot of the user's entire screen and saves it with a timestamp. "
            "Use when user says 'take a screenshot', 'capture the screen', 'screenshot this', "
            "'save what's on my screen', 'photo lo screen ka'."
        ),
        "tags": ["screenshot", "screen", "capture", "image", "photo"],
        "example_queries": [
            "take a screenshot", "capture the screen", "screenshot this",
            "save what's on my screen", "grab a screenshot", "ss le lo",
            "take a picture of my screen", "screen capture"
        ],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # ─────────────────────────────────────────────
    # FILE ANALYSIS (specific path)
    # ─────────────────────────────────────────────
    {
        "name": "analyze_specific_file",
        "description": (
            "Reads and analyzes a specific local file (PDF, DOCX, TXT, CSV, Python) by its full path. "
            "Use when user provides or drags a specific file path and asks questions about its content. "
            "For finding a file by name keyword, use find_and_analyze_file instead."
        ),
        "tags": ["file", "document", "pdf", "read", "analyze", "local"],
        "example_queries": [
            "read this PDF file", "analyze this document", "what does this file say",
            "summarize this PDF", "read the contents of", "analyze C:/path/to/file.pdf",
            "what's in this document", "read this text file"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read."
                },
                "query": {
                    "type": "string",
                    "description": "Question to answer about the file content."
                }
            },
            "required": ["file_path", "query"]
        }
    },

    # ─────────────────────────────────────────────
    # FILE FIND + ANALYZE
    # ─────────────────────────────────────────────
    {
        "name": "find_and_analyze_file",
        "description": (
            "Searches for a local file by keyword in its filename across Documents, Downloads, and Desktop, "
            "then reads and answers questions about it. Use when user says 'find my invoice', "
            "'read the report file', 'open that PDF about taxes', without specifying a full path."
        ),
        "tags": ["file", "search", "find", "document", "pdf", "invoice", "report"],
        "example_queries": [
            "find my invoice", "read the budget report", "find that PDF about",
            "look for the resume file", "find and read the contract",
            "where is my tax document", "find the project report"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "filename_keyword": {
                    "type": "string",
                    "description": "Keyword to search for in the filename (e.g., 'invoice', 'report')."
                },
                "query": {
                    "type": "string",
                    "description": "Question to answer about the file."
                }
            },
            "required": ["filename_keyword", "query"]
        }
    },

    # ─────────────────────────────────────────────
    # TELEGRAM BRIDGE
    # ─────────────────────────────────────────────
    {
        "name": "telegram_send_message",
        "description": (
            "Sends a short text message or notification to the user's personal Telegram app on their phone. "
            "Use for: quick updates, alerts, reminders, short answers the user wants on their phone. "
            "Requires Telegram bot to be configured in .env."
        ),
        "tags": ["telegram", "message", "phone", "notify", "send", "alert"],
        "example_queries": [
            "send a message to my telegram", "notify me on telegram", "message me on telegram",
            "tell me on telegram", "send this to my phone", "push notification to telegram"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The text message to send to Telegram."
                }
            },
            "required": ["message"]
        }
    },

    {
        "name": "telegram_send_report",
        "description": (
            "Sends a richly formatted summary, analysis, or report to the user's Telegram phone app. "
            "Use when VoxKage generates a long research result, file summary, or detailed analysis "
            "and the user wants to save it or read it later on their phone. "
            "Formats with markdown for clean mobile reading."
        ),
        "tags": ["telegram", "report", "summary", "analysis", "save", "forward", "phone"],
        "example_queries": [
            "send the summary to telegram", "save this report to my telegram",
            "forward this analysis to my phone", "put the results in telegram",
            "send the research to my telegram app", "save to telegram"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "A short title for the report (e.g. 'Python Tutorial Summary')."
                },
                "content": {
                    "type": "string",
                    "description": "The full content of the report to send."
                }
            },
            "required": ["title", "content"]
        }
    },

    {
        "name": "telegram_ask_save",
        "description": (
            "Asks the user via a Telegram button prompt whether they want the current information "
            "saved/forwarded to their Telegram app. Shows a Yes/No button in Telegram and waits 30s. "
            "Call this AFTER generating a substantial report or summary (>300 characters) to offer "
            "the user an option to save it to their phone. Do NOT call for short conversational replies."
        ),
        "tags": ["telegram", "ask", "save", "prompt", "confirm", "phone"],
        "example_queries": [
            "should i save this to telegram", "want to keep this on phone",
            "offer to send to telegram", "ask if they want the report on telegram"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "content_description": {
                    "type": "string",
                    "description": "A very short description of what was generated (e.g. 'Python web scraping tutorial summary')."
                }
            },
            "required": ["content_description"]
        }
    },

    {
        "name": "telegram_send_file",
        "description": (
            "Sends a local file from this PC directly to the user's Telegram app. "
            "Supports PDFs, images, text files, CSVs, and any document type. "
            "Use when the user says: 'send this file to telegram', 'forward my resume to telegram', "
            "'push this PDF to my phone'."
        ),
        "tags": ["telegram", "file", "pdf", "image", "document", "send", "phone"],
        "example_queries": [
            "send this file to telegram", "forward the pdf to my phone",
            "push my resume to telegram", "send the image to telegram",
            "upload this file to my telegram"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file on this PC to send."
                },
                "caption": {
                    "type": "string",
                    "description": "Optional caption to include with the file."
                }
            },
            "required": ["file_path"]
        }
    },

    {
        "name": "telegram_get_status",
        "description": (
            "Returns the current status of the Telegram bridge — whether the bot is connected "
            "and which chat is linked. Use when the user asks: 'Is Telegram working?', "
            "'telegram status', 'is the telegram bot connected?'."
        ),
        "tags": ["telegram", "status", "connected", "check", "bot"],
        "example_queries": [
            "is telegram connected", "telegram status", "check telegram", "is the bot running",
            "is telegram bot active", "telegram connection status"
        ],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "telegram_get_pending_messages",
        "description": (
            "Fetches pending/unread messages from the connected Telegram user. "
            "Use when the user asks: 'do I have any telegram messages?', "
            "'read my telegram messages', 'check telegram'."
        ),
        "tags": ["telegram", "messages", "read", "check", "inbox"],
        "example_queries": [
            "check my telegram messages", "read my telegram", "do I have any messages on telegram",
            "what did my bot receive"
        ],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    # ─────────────────────────────────────────────
    # RAG MEMORY
    # ─────────────────────────────────────────────
    {
        "name": "check_and_index",
        "description": "RAG MEMORY GATE: Call this FIRST before reading any file. Automatically indexes new files, reindexes changed files, returns instantly from cache for unchanged files.",
        "tags": ["rag", "memory", "index", "file", "cache", "document"],
        "example_queries": ["index this file", "add this document to memory", "check and index file"],
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file to check and index."}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "query_rag",
        "description": (
            "RAG MEMORY SEARCH: Semantic search across all indexed documents. "
            "Use to answer questions about any file VoxKage has seen before WITHOUT re-reading it. "
            "Shows relevance confidence for each result."
        ),
        "tags": ["rag", "memory", "search", "query", "knowledge"],
        "example_queries": [
            "search memory for authentication", "what does the indexed file say",
            "query rag memory for API fallbacks", "find how we handle errors"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language question, e.g. 'how did we handle API fallbacks'"},
                "top_k": {"type": "integer", "description": "Number of results to return (default 5)"},
                "file_filter": {"type": "string", "description": "Optional file name substring to filter results"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "index_document",
        "description": "Forces indexing of a single document into the RAG memory. Only use if check_and_index fails.",
        "tags": ["rag", "memory", "index", "force"],
        "example_queries": ["force index document", "add document to knowledge base"],
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "list_indexed_documents",
        "description": "Lists all documents currently stored in the RAG memory.",
        "tags": ["rag", "memory", "list", "indexed", "documents"],
        "example_queries": ["what is in your memory", "list indexed files", "show rag memory contents"],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_from_rag",
        "description": "Deletes a specific document from the RAG memory.",
        "tags": ["rag", "memory", "delete", "remove"],
        "example_queries": ["remove file from memory", "delete document from rag"],
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file to remove"}
            },
            "required": ["file_path"]
        }
    },
{
        "name": "index_directory",
        "description": (
            "RAG MEMORY: Bulk-index an entire directory (codebase, documents folder) into RAG memory. "
            "Use when asked to learn a whole project or index a codebase. "
            "Shows progress as it indexes. Already-indexed unchanged files are skipped automatically."
        ),
        "tags": ["rag", "memory", "index", "directory", "codebase", "folder"],
        "example_queries": [
            "index my project folder", "learn this codebase", "add this directory to memory",
            "index the vision-assistant folder", "learn the voxkage project"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Absolute path to the directory to index"},
                "extensions": {"type": "string", "description": "Comma-separated extensions filter (e.g. '.py,.md,.txt'). Leave empty for all text files."},
                "recursive": {"type": "boolean", "description": "Whether to recurse into subdirectories (default true)"}
            },
            "required": ["directory"]
        }
    },

    # ─────────────────────────────────────────────
    # CODING ENGINE (ACE - Agentic Coding Engine)
    # ─────────────────────────────────────────────
    {
        "name": "coding_thinking",
        "description": (
            "ACE ENTRY POINT — Creates a persistent step-by-step plan with RAG context integration. "
            "Uses 5-phase pipeline: Problem Decomposition → RAG-First Awareness → Knowledge Gap Fill → Plan → Execute/Verify. "
            "MUST be called BEFORE writing any code. Creates active_plan.md in C:\\VoxKage\\Brain\\."
        ),
        "tags": ["coding", "ace", "plan", "reasoning", "code"],
        "example_queries": [
            "help me create a new feature", "implement authentication", "refactor this module",
            "write tests for the api", "fix that bug", "add validation"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What you are trying to accomplish (natural language)"},
                "project_dir": {"type": "string", "description": "Root directory of the codebase (optional, for cache check only)"},
                "steps": {"type": "string", "description": "Pipe-separated list of planned steps (e.g. 'Create auth module|Add middleware|Update routes')"},
                "rag_context": {"type": "string", "description": "The output from query_rag() to inform the plan."}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "get_code_skeleton",
        "description": (
            "ACE TOOL: Get a compact structural skeleton of a code file. "
            "Returns ONLY: imports, class names, function signatures, docstrings, and top-level constants. "
            "Reduces a 2000-line file to ~40 lines. Use INSTEAD of reading full files to understand structure."
        ),
        "tags": ["coding", "skeleton", "structure", "ast"],
        "example_queries": [
            "show me the structure of this file", "what functions are in main.py",
            "get the skeleton of utils.py", "what classes does auth.py have"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the code file"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "update_coding_plan",
        "description": (
            "ACE TOOL: Mark a step in the active plan as done or failed. "
            "Call this after completing each step in the coding_thinking plan. "
            "Shows remaining open items with checkmarks."
        ),
        "tags": ["coding", "plan", "todo", "checklist", "ace"],
        "example_queries": [
            "mark step 1 done", "step 2 is complete", "step 3 failed",
            "update the plan", "tick off step 1"
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "step_number": {"type": "integer", "description": "Which step to update (1-indexed)"},
                "status": {"type": "string", "description": "Status: 'done' to mark complete, 'failed' to mark as failed"}
            },
            "required": ["step_number"]
        }
    },
    {
        "name": "get_coding_plan",
        "description": (
            "ACE TOOL: Read the current active coding plan. "
            "Returns full contents of C:\\VoxKage\\Brain\\active_plan.md. "
            "Use to recall what steps remain when resuming a task."
        ),
        "tags": ["coding", "plan", "read", "ace"],
        "example_queries": [
            "show me the current plan", "what steps remain", "read the active plan",
            "what is the coding plan"
        ],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },


# ─────────────────────────────────────────────
    # GITHUB OPERATIONS
    # ─────────────────────────────────────────────
    {
        "name": "git_clone",
        "description": "Clone a git repository to the local machine.",
        "tags": ["git", "github", "clone", "repository"],
        "example_queries": ["clone repo", "clone github repo", "git clone"],
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL of the repository to clone."},
                "dest_name": {"type": "string", "description": "Optional destination folder name."}
            },
            "required": ["url"]
        }
    },
    {
        "name": "git_smart_commit",
        "description": "Commit all changes in a local repository with a message and optionally push.",
        "tags": ["git", "github", "commit", "push", "save"],
        "example_queries": ["commit changes", "push to github", "git commit and push"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository."},
                "message": {"type": "string", "description": "Commit message."},
                "push": {"type": "boolean", "description": "Whether to push after committing (default false)."}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "git_pull",
        "description": "Pulls latest changes from the remote repository.",
        "tags": ["git", "github", "pull", "update"],
        "example_queries": ["git pull", "update repository", "pull changes"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository"}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "fake_commit",
        "description": "Create empty fake commits to trigger CI/CD pipelines.",
        "tags": ["git", "github", "commit", "trigger", "ci"],
        "example_queries": ["trigger build", "fake commit", "empty commit"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository."},
                "message": {"type": "string", "description": "Commit message."}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "detect_and_install_deps",
        "description": "Detect package managers (npm, pip, cargo) and auto-install dependencies.",
        "tags": ["git", "dependencies", "install", "npm", "pip", "project"],
        "example_queries": ["install dependencies", "setup project", "npm install", "pip install"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository."}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "run_project",
        "description": "Run a project in the background (npm start, python app.py, etc).",
        "tags": ["project", "run", "start", "server", "background"],
        "example_queries": ["run the project", "start the server", "run app"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository."},
                "command": {"type": "string", "description": "Optional custom command to run."},
                "port": {"type": "integer", "description": "Optional port number."}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "kill_project",
        "description": "Kill a background project started by run_project.",
        "tags": ["project", "kill", "stop", "server"],
        "example_queries": ["stop the server", "kill project", "stop running app"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository."}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "check_project_health",
        "description": "Check if a background project is still running.",
        "tags": ["project", "health", "status", "server"],
        "example_queries": ["is the server running", "check project health", "server status"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the local repository."},
                "port": {"type": "integer", "description": "Port to check."}
            },
            "required": ["repo_path"]
        }
    },
    {
        "name": "get_github_profile",
        "description": "Fetches the authenticated user's GitHub profile information.",
        "tags": ["github", "profile", "user"],
        "example_queries": ["show my github profile", "who am i on github", "my github info"],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_my_repos",
        "description": "List the authenticated user's GitHub repositories.",
        "tags": ["github", "repos", "repositories", "list"],
        "example_queries": ["list my repos", "what repos do i have", "show my repositories"],
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max repos to return (default 10)."},
                "sort": {"type": "string", "description": "Sort by: 'updated', 'created', 'pushed' (default: updated)."}
            },
            "required": []
        }
    },
    {
        "name": "create_repo_local",
        "description": "Create a new GitHub repo and clone it locally.",
        "tags": ["github", "create", "repo", "new", "repository"],
        "example_queries": ["create a new repo", "make a github repository", "create private repo"],
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local directory path."},
                "name": {"type": "string", "description": "Repository name."},
                "private": {"type": "boolean", "description": "Whether repo is private (default true)."},
                "push": {"type": "boolean", "description": "Whether to push after creating (default true)."}
            },
            "required": ["path", "name"]
        }
    },
    {
        "name": "actions_list",
        "description": "GITHUB: Lists recent GitHub Actions workflow runs for a repository.",
        "tags": ["github", "actions", "workflows", "ci"],
        "example_queries": ["check github actions", "list workflow runs", "did my build pass"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name with owner (e.g., ayushdwivedi001/VoxKage)"},
                "limit": {"type": "integer", "description": "Max runs to return (default 10)"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "actions_get",
        "description": "GITHUB: Gets details and jobs for a specific GitHub Actions workflow run.",
        "tags": ["github", "actions", "jobs", "workflow"],
        "example_queries": ["get action details", "why did workflow fail", "action run details"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name with owner"},
                "run_id": {"type": "string", "description": "Workflow run ID"}
            },
            "required": ["repo", "run_id"]
        }
    },
    {
        "name": "get_job_logs",
        "description": "GITHUB: Downloads the raw logs for a specific GitHub Actions job.",
        "tags": ["github", "actions", "logs", "job", "ci", "error"],
        "example_queries": ["get job logs", "show me why the build failed", "github action logs"],
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name with owner"},
                "job_id": {"type": "string", "description": "Job ID from actions_get"}
            },
            "required": ["repo", "job_id"]
        }
    }
]

# Fast lookup by name — used by tool_rag.py and mcp_dispatcher.py
TOOL_DEFINITIONS_BY_NAME: dict = {t["name"]: t for t in TOOL_DEFINITIONS}


def get_schema(tool_def: dict) -> dict:
    """Convert a TOOL_DEFINITIONS entry to a structured OpenAI/Gemini tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": tool_def.get("parameters", {"type": "object", "properties": {}, "required": []})
        }
    }


def get_all_schemas() -> list:
    """Return all tool definitions in standard function-calling format. Used as fallback when RAG is disabled."""
    return [get_schema(t) for t in TOOL_DEFINITIONS]
