# VoxKage Documentation

## Overview

VoxKage is an advanced OS-level agentic AI assistant designed to operate securely and efficiently within your local environment. It leverages the Model Context Protocol (MCP) to interact with a vast array of tools, ranging from file system operations to browser automation, all orchestrated by a powerful LLM engine.

## Dashboard Structure (Suggested Left Menu)

*   **Architecture & Engine:** How VoxKage thinks and acts.
*   **Agentic Loop Execution:** The core operational cycle.
*   **MCP Server Honeycomb:** The distributed tool network.
*   **Security & Shield Protocol:** Safe operation and Safe Mode management.
*   **Agentic Coding Engine (ACE):** Automated development workflows and 95% efficiency.
*   **Browser & Vision Intelligence:** Deep web interaction and multimodal analysis.
*   **Memory System:** How VoxKage learns and adapts (SOUL, Problem/Solution, Frontend).
*   **System Tray & Remote Control:** Background daemon and Telegram watcher.
*   **Plugin Ecosystem:** Extending capabilities seamlessly.
*   **FAQ & General Questions:** Common queries and troubleshooting.
*   **LICENSE & Usability:** Apache 2.0 LICENSE and usability instructions.

---
Core Engine - Architecture, Agentic Loop, MCP Honeycomb, Shield Protocol, Capabilities - Coding Engine (ACE), Browser & Vision, Memory (SOUL), Control - Tray & Remote, Plugin Ecosystem, Resources - FAQ & Support, License & Terms

## 1. Deep Architecture & Execution Engine

VoxKage’s architecture breaks away from traditional chatbot wrappers. It is built as a highly decentralized, agentic operating system layer that bridges an LLM's reasoning capabilities directly to local hardware via the Model Context Protocol (MCP) and asynchronous process management. 

Here is the comprehensive breakdown of how the VoxKage brain is structured and how it operates in real-time.

### 1.1 The Core Reasoning Engine (`gemini_engine.py`)
Unlike web-based assistants, VoxKage does not rely on simple REST API calls for its core loop. Instead, it utilizes the official, locally installed `gemini` CLI as its reasoning engine.

*   **Asynchronous Subprocessing:** VoxKage spawns the `gemini` CLI as a continuous, asynchronous background process. This non-blocking architecture ensures that while the LLM is "thinking," the main thread remains responsive for system tray updates, Telegram polling, and background task management.
*   **Multimodal File Handling:** By running the CLI locally, VoxKage bypasses the need to convert images to Base64 strings. When the vision pipeline is triggered, VoxKage passes absolute file paths directly to the CLI argument flags (e.g., `gemini --image C:\path\to\screenshot.png`). This allows for native, zero-latency ingestion of massive screenshots and documents.
*   **Intelligent Output Parsing:** LLMs are notorious for wrapping valid JSON tool calls inside conversational markdown (e.g., \`\`\`json ... \`\`\`). The `clean_cli_json` module acts as a robust middleware filter. It implements a multi-strategy parser that walks the LLM's raw `stdout` character-by-character to extract valid JSON objects/arrays, completely ignoring conversational hallucination around the tool call.

### 1.2 The Central Nervous System: MCP Dispatcher (`mcp_dispatcher.py`)
VoxKage’s ability to affect the real world relies entirely on the **Model Context Protocol (MCP)**. The dispatcher acts as the central router between the LLM's intent and the actual execution of Python code.

*   **Dynamic Stdio Spawning:** To maintain an incredibly light memory footprint, VoxKage does *not* load all its tools into memory at startup. Instead, when the LLM outputs a tool call (e.g., `mcp_voxkage-browser_search_web`), the dispatcher matches this to a specific Python server file (e.g., `browser_server.py`). It then dynamically spawns this server via `stdio`, sends the JSON RPC execution request, captures the `stdout` result, and immediately terminates the server.
*   **The Honeycomb Topology:** Tools are isolated into distinct, domain-specific servers (Browser, FileOps, OSControl, Telegram, Media). If the `browser_server` crashes due to a Selenium error, it does not bring down the main VoxKage loop. The dispatcher catches the error, reports the stack trace back to the LLM, and allows the LLM to self-correct.
*   **Tool Schema Injection:** At startup, VoxKage dynamically inspects the `mcp_servers/` directory, extracts the JSON schemas for every available tool, and compiles them into the massive System Prompt payload. This is how the LLM instantly knows what tools are available without hardcoding them into the main prompt.

### 1.3 The Agentic Loop (`agentic_loop.py` & `main.py`)
VoxKage does not execute scripts linearly; it runs a continuous, autonomous "Plan-Act-Validate" loop.

1.  **State Assembly:** When a user issues a command (via CLI or Telegram), VoxKage compiles the system state. It injects the `GEMINI.md` project rules, the local file system structure, recent `MEMORY.md` learnings, and the user's prompt into the context window.
2.  **Intent Parsing:** The LLM processes the state. It determines if the request is conversational or actionable.
3.  **Tool Selection (The "Action"):** If actionable, the LLM outputs a JSON payload matching a registered MCP tool. It is strictly programmed to prefer native OS tools (like PowerShell) or specific MCP tools over attempting to write manual scripts.
4.  **Execution & The Feedback Loop:** The `mcp_dispatcher` executes the tool. The critical agentic step occurs here: the raw output of the tool (success, failure, or stack trace) is fed *back* to the LLM automatically.
5.  **Validation & Re-entry:** The LLM reads the result. If a file deletion failed due to permissions, the LLM doesn't stop. It recognizes the failure from the tool output, adjusts its plan (e.g., "I will use PowerShell with elevated privileges"), and generates a *new* tool call. This loop continues autonomously until the LLM outputs a `GOAL_MET` sentinel or reaches a maximum retry limit.

### 1.4 Inter-Process Communication (IPC)
Because VoxKage operates across multiple distinct environments (the main CLI, the background task sub-agents, and the system tray/Telegram watcher), it relies on robust IPC mechanisms.

*   **Named Pipes (Windows):** The `telegram_watcher.py` daemon uses Named Pipes to communicate securely with the active `gemini` CLI window. When you send a Telegram message, the watcher doesn't just print it; it connects to the active pipe and physically injects the text into the terminal's `stdin` stream, ensuring the active session handles the request live.
*   **Task State Files:** Long-running background agents (spawned via `spawn_task`) communicate their progress back to the main loop by writing structured JSON updates to temporary state files. The main thread's `check_tasks` tool reads these files to provide live progress bars to the user without blocking the main event loop.

---

## 2. Agentic Loop Execution

VoxKage operates autonomously using a robust, iterative "Plan-Act-Validate" cycle defined in `agentic_loop.py`. This isn't a simple input/output script; it's a persistent agentic loop.

1.  **Context Assembly:** The user's query, past memory, and system state are assembled.
2.  **Tool Selection (Thinking Phase):** The engine decides *which* MCP tool from the honeycomb is required. It prioritizes specific tools (like `read_file`) over generic shell commands whenever possible for safety and precision.
3.  **Execution (Action Phase):** The `mcp_dispatcher` routes the request to the correct localized server, executing the tool and capturing the output (stdout, stderr, or JSON).
4.  **Validation (Feedback Loop):** The result is fed back into the engine. If a tool fails, VoxKage doesn't just stop; it analyzes the error, adjusts its strategy, and tries an alternative approach until the `GOAL_MET` sentinel is reached.

---

## 3. MCP Server Honeycomb

VoxKage's capabilities are decentralized into specialized "Honeycomb" servers located in `voxkage/mcp_servers/`. This modular design ensures that if one component fails, the entire system doesn't crash. They act as independent microservices managed by the MCP protocol.

*   **Integration and Linking:** While independent, these servers are orchestrated by the `agentic_loop` to perform complex, cross-domain tasks. For example, the `github_server` can clone a repo, the `rag_server` immediately indexes it, the `coding_server` analyzes it, and the `os_control_server` runs the build command—all seamlessly linked by the central reasoning engine passing data between them.
*   **Key Cells in the Honeycomb:**
    *   **`browser_server.py`:** Handles deep DOM inspection, screenshots, and web navigation.
    *   **`coding_server.py`:** Powers the Agentic Coding Engine (ACE) for codebase analysis.
    *   **`os_control_server.py` & `file_ops_server.py`:** Manages file system interactions.
    *   **`rag_server.py`:** Manages local document indexing and semantic search.
    *   **`task_server.py`:** Orchestrates long-running background sub-agents.
    *   **`memory_server.py`:** Manages the SOUL and Problem/Solution logs.

---

## 4. Security & Shield Protocol

Because VoxKage operates directly on the host OS without a restrictive sandbox (like Docker), robust security is paramount. The **Shield Protocol** (`voxkage/shield.py`) acts as the primary defense mechanism.

### Multi-Layered Protection
1.  **Blocklist Configuration:** Loads definitions of dangerous paths, commands, and extensions (`_load_blocklist`).
2.  **Path Gating (`shield_gate_path`):** Before any file operation, the path is checked against protected directories (e.g., `C:\Windows`, `.git` folders).
3.  **Command Gating (`shield_gate_command`):** Shell commands are scanned using regex patterns (`shield_check_command`) to block destructive actions like `format`, `del *`, or registry modifications.
4.  **Extension Gating (`shield_check_delete`):** Prevents accidental deletion of critical files.

### Managing Safe Mode
By default, VoxKage runs with **Safe Mode Enabled**, which strictly enforces the Shield Protocol.
*   **Turning it off:** If a developer needs VoxKage to perform advanced system modifications (e.g., modifying system configs or deleting protected extensions), they can toggle Safe Mode off by editing the `~/.voxkage/settings.json` file and setting `"safe_mode": false`.
*   *Warning:* Disabling Safe Mode grants VoxKage unrestricted access to the file system. It should only be done by power users who understand the risks.

### User Confirmation Gates (Hard Stops)
For irreversible actions (like deleting non-junk files or running `.exe` installers), VoxKage employs a strict "Hard Stop." It pauses execution, presents the proposed action to the user, and requires explicit consent (`confirmed=True`) before proceeding.

---

## 5. Agentic Coding Engine (ACE)

VoxKage includes a sophisticated subsystem for software development: `voxkage-coding`. It is designed for maximum token efficiency and structural understanding.

### The 95% Efficiency Breakthrough: Code Skeletons
Instead of reading massive 2000-line source files (which consumes massive token context and slows down reasoning), ACE relies on `get_code_skeleton`.
*   This tool parses Python, JS, TS, and JSX files to extract *only* the imports, class names, function signatures, docstrings, and top-level constants.
*   This reduces a massive file to roughly 40 lines, saving over 95% of token context while giving VoxKage perfect structural awareness of where to insert code or what functions exist.

### The ACE Workflow
1.  **RAG Indexing (`index_directory`):** Maintains a fresh semantic memory of the codebase.
2.  **Planning (`coding_thinking`):** Generates a persistent, step-by-step `active_plan.md` based on RAG context before writing any code.
3.  **Execution & Tracking:** Executes changes iteratively using skeletons to navigate. It must call `update_coding_plan` after every step (marking it "done" or "failed") to keep the user informed visually.

---

## 6. Browser & Vision Intelligence

VoxKage interacts with the web not just via text, but visually and structurally, mimicking a human user but with deep DOM access.

### Deep DOM Inspection
VoxKage bypasses simple text scraping. It uses tools to understand the *structure* and *styling* of a page:
*   **`dom_get_elements`:** Pulls clean HTML structures based on CSS selectors without the clutter of the full page source.
*   **`dom_get_computed_style`:** Analyzes the exact CSS values (colors, flexbox, animations) driving the visual appearance.
*   **`dom_execute_js`:** Runs custom queries for highly specific interaction logic.

### Vision Validation Loop
When downloading images or interacting with complex UIs, text isn't enough. VoxKage uses a multimodal validation loop:
1.  **Navigate & Capture:** Goes to a URL and takes a screenshot (`get_browser_state` or `take_screenshot`).
2.  **Analyze (`analyze_specific_file`):** Passes the screenshot to Gemini's vision model to answer specific queries (e.g., "Is this image high resolution?", "Where is the download button?").
3.  **Act or Discard:** If an image fails the quality or relevance check, VoxKage autonomously deletes it and tries the next source.

---

## 7. Memory System

VoxKage is not amnesic; it learns across sessions using a multi-tiered memory architecture managed by the `memory_server`.

### The SOUL System (User Preferences)
VoxKage learns about *you*. Using the `remember_user` tool, it autonomously logs facts, habits, and preferences:
*   **Identity:** Name, location, device types.
*   **Preferences:** Preferred music genres, default download folders, preferred coding styles.
*   **Trusted Actions:** If a user repeatedly says "just delete junk without asking," VoxKage logs a `trusted_action` to bypass confirmation gates for that specific workflow in the future.

### Problem/Solution Self-Learning
If VoxKage struggles with a task (e.g., a tool fails twice, or it navigates to the wrong folder), it autonomously calls `log_problem`. Once it figures out the correct path, it calls `log_solution`. Before attempting complex tasks in the future, it uses `search_memory` to avoid repeating past mistakes.

### Frontend Template Memory
When VoxKage encounters a brilliant CSS animation or a perfect React component during web browsing, it can use `save_frontend_snippet` to store that code block permanently. Later, it uses `search_frontend_snippets` to retrieve these templates when building new applications, effectively learning how to code better UIs over time.

---

## 8. Plugin Ecosystem

VoxKage is designed to be highly extensible through its plugin architecture (`voxkage/plugins/`).

### Current State
*   `github.py`: Repository management and commit automation.
*   `gmail.py`: Inbox reading, drafting, and sending.
*   `spotify.py`: Media control and playlist management.
*   `telegram.py`: Remote notification and interaction.

### Future Extensibility (`registry.py`)
The registry supports community plugins via Python entry-point discovery. Developers can create standard Python packages that register a `VoxKagePlugin`. VoxKage automatically discovers, loads, and configures them, instantly adding new MCP servers to the honeycomb without core codebase modifications.

---

## 10. FAQ & General Questions

**Q: How does VoxKage execute tasks without a Sandbox?**
**A:** VoxKage relies on the "Shield Protocol," a robust system of path, command, and extension blocklists. It also utilizes "Hard Stop Confirmation Gates" requiring explicit user approval before executing irreversible actions like deleting files or running unverified `.exe` installers.

**Q: Can I turn off the safety features?**
**A:** Yes, power users can disable "Safe Mode" by editing `~/.voxkage/settings.json` and setting `"safe_mode": false`. This removes the Shield Protocol's automatic blocking, though Confirmation Gates for highly destructive actions may still apply. Proceed with extreme caution.

**Q: Why does VoxKage take screenshots while browsing?**
**A:** VoxKage uses multimodal vision to verify its actions. Screenshots allow it to "see" if an image is high quality, verify that a webpage loaded correctly, or locate a button that isn't easily selectable via standard DOM queries.

**Q: How does the Agentic Coding Engine handle massive repositories?**
**A:** It uses RAG (Retrieval-Augmented Generation) for semantic search across the codebase and `get_code_skeleton` to strip large files down to just their structural signatures (imports, functions, classes). This approach saves up to 95% of the token context, allowing it to understand massive systems without overwhelming the LLM's memory.

**Q: Where does VoxKage store what it learns about me?**
**A:** User preferences and SOUL data are stored locally and securely in `~/.gemini/GEMINI.md` (Global Personal Memory) and within project-specific `.gemini/tmp/` folders (Private Project Memory). It never uploads your personal data or habits to external servers.

**Q: How do I create a custom plugin?**
**A:** You can create a Python package that inherits from the `VoxKagePlugin` base class found in `voxkage.plugins.base`. Once installed in VoxKage's environment, the registry will automatically detect it and spin up its associated MCP server.