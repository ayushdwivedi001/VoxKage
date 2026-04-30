"""
scripts/ollama_fallback.py — VoxKage Offline Mode
==================================================
Simple interactive Ollama chat session for when Gemini CLI is unavailable.
Runs in the terminal (CMD) — launched by the tray icon's "Offline" option.

Requires Ollama to be running: ollama serve
"""

import os
import sys
import json

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "qwen3.5:4b-q4_k_m"

# Load config.json for custom commands
try:
    with open(os.path.join(_ROOT, "config.json"), "r", encoding="utf-8") as f:
        _CONFIG = json.load(f)
except Exception:
    _CONFIG = {}

_APP_COMMANDS = _CONFIG.get("app_launch_commands", {})
_SYS_COMMANDS = _CONFIG.get("system_commands", {})
_WEB_COMMANDS = _CONFIG.get("website_commands", {})

SYSTEM_PROMPT = f"""You are VoxKage, an advanced OS-level agentic AI assistant.
You are running in OFFLINE mode using a local Ollama model.
You communicate in a concise JARVIS-style tone and refer to the user as "sir".

The user has these app shortcuts configured: {list(_APP_COMMANDS.keys())}
The user has these website shortcuts: {list(_WEB_COMMANDS.keys())}

In offline mode, you can:
- Chat and answer questions from your training knowledge
- Open apps and websites using the os.system() commands
- Provide advice, analysis, and conversation

You CANNOT:
- Search the web (no internet tool in offline mode)
- Check email or Telegram
- Play media via browser
"""


def try_local_command(text: str) -> bool:
    """Try to execute a local command from config.json before hitting the LLM."""
    text_lower = text.lower().strip()

    # App launch
    for name, cmd in _APP_COMMANDS.items():
        if name in text_lower or text_lower in name:
            os.system(cmd)
            print(f"\nVoxKage: Opening {name}, sir.\n")
            return True

    # Website
    for name, url in _WEB_COMMANDS.items():
        if name in text_lower:
            os.system(f"start {url}")
            print(f"\nVoxKage: Opening {name}, sir.\n")
            return True

    # System commands
    for name, cmd in _SYS_COMMANDS.items():
        if name in text_lower:
            confirm = input(f"VoxKage: Confirm '{name}'? (y/n): ").strip().lower()
            if confirm == "y":
                os.system(cmd)
                print(f"VoxKage: Executing {name}, sir.")
            return True

    return False


def chat_with_ollama(messages: list) -> str:
    """Send messages to Ollama and return the response."""
    try:
        import requests
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except Exception as e:
        return f"[Ollama error: {e}]"


def main():
    print("=" * 60)
    print("  VoxKage — Offline Mode (Ollama)")
    print(f"  Model: {OLLAMA_MODEL}")
    print("  Type 'exit' or 'quit' to close.")
    print("=" * 60)
    print()
    print("VoxKage: Offline systems online, sir. Running on local model.")
    print("         Internet tools unavailable — knowledge only.\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nVoxKage: Goodbye, sir.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "bye"):
            print("VoxKage: Goodbye, sir.")
            break

        # Try direct command first
        if try_local_command(user_input):
            continue

        # Otherwise send to Ollama
        messages.append({"role": "user", "content": user_input})
        response = chat_with_ollama(messages)
        messages.append({"role": "assistant", "content": response})

        # Keep history manageable
        if len(messages) > 21:
            messages = [messages[0]] + messages[-20:]

        print(f"\nVoxKage: {response}\n")


if __name__ == "__main__":
    main()
