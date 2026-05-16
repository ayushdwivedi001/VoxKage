# VoxKage: The Living Agentic OS Framework

VoxKage is an advanced evolution of AI assistants, designed to operate as a "system-wide brain" rather than just a chatbot or code editor plugin. It leverages the Gemini CLI as its conversational interface while orchestrating a complex network of autonomous capabilities.

## 🏗️ Core Architecture

VoxKage follows a modular, "honeycomb" architecture:

1.  **Interface Layer (Gemini CLI):**
    *   Acts as the "mouthpiece" and primary I/O.
    *   VoxKage injects specialized directives to "hijack" the CLI session.
    *   Implemented in `voxkage/llm/gemini_repl.py` and `voxkage/llm/gemini_engine.py`.

2.  **Reasoning Layer (Agentic Loop):**
    *   Powered by **LangGraph**, providing a robust state machine for multi-step reasoning.
    *   **Self-Healing:** Detects tool execution errors and feeds them back to the LLM for correction.
    *   **Context Optimization:** Injects tool "cheatsheets" into prompts to ensure precise tool usage.
    *   Located in `voxkage/llm/agentic_loop.py`.

3.  **Capability Layer (MCP Servers):**
    *   Implements over 18 specialized **Model Context Protocol (MCP)** servers.
    *   Modular design: each server (e.g., `browser_server`, `system_server`, `coding_server`) provides a specific set of tools.
    *   Found in `voxkage/mcp_servers/`.

4.  **Automation Layer:**
    *   Low-level Python scripts that interact directly with the OS, hardware, and external APIs.
    *   Includes Playwright for web, PyAutoGUI for GUI, and custom integrations for Spotify, Gmail, etc.
    *   Located in `voxkage/automation/`.

## 🚀 Key Features & Innovations

### ⚙️ ACE: Agentic Coding Engine
A strict 5-phase developer pipeline that ensures reliability:
*   **RAG Awareness:** Indexes the codebase before making changes.
*   **Planning:** Maintains an `active_plan.md` to track progress.
*   **AST Skeletons:** Summarizes large files into structural metadata, achieving **95% token efficiency**.
*   **Verification:** Automatically runs checks (build, lint, etc.) after edits.

### 🌐 GUI Thinking & Web Automation
*   Uses Playwright to interact with the web like a human (clicking, typing, scrolling).
*   Can take screenshots and perform OCR/Visual Analysis to "see" what's happening.
*   Capable of multi-domain research and autonomous software installation.

### 🌉 Remote Bridges
*   **Telegram:** Control your PC remotely via a Telegram bot.
*   **System Tray:** A Windows tray application for persistent access and status monitoring.

### 🛡️ Security: VoxKage Shield
*   Monitors and filters shell commands to prevent accidental or malicious execution of dangerous operations (e.g., `rm -rf /`, `format C:`).
*   Implemented in `voxkage/shield.py`.

## 📂 Project Structure Overview

*   `voxkage/`: Main package directory.
    *   `automation/`: System, browser, and app automation scripts.
    *   `llm/`: Core AI logic, agentic loop, and Gemini integration.
    *   `mcp_servers/`: Implementation of the 18+ capability servers.
    *   `plugins/`: External service integrations (Spotify, GitHub, etc.).
    *   `tray/`: System tray UI logic.
    *   `main.py`: Entry point for the assistant.
    *   `cli.py`: Command-line interface for setup and management.

## 🛠️ Important Concepts for Developers

*   **Tool Registry:** All capabilities are registered as tools that the LLM can call. See `voxkage/llm/tool_registry.py`.
*   **State Management:** The agent's memory and current task state are tracked within the LangGraph `AgentState`.
*   **Lazy Loading:** Many components (like MCP servers and heavy libraries) are loaded lazily to improve startup speed.
