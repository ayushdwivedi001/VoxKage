import os
import sys

# ── Offline model flags (Local models like Whisper + sentence-transformers) ─────
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from config_loader import load_config
CONFIG = load_config()

def start_assistant():
    from llm.llm_client import clear_session_memory
    clear_session_memory()

    # ── Pre-warm the Tool RAG index (runs once at startup, ~2s) ─────────────────
    try:
        from llm.tool_rag import ensure_index_fresh
        ensure_index_fresh()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Tool RAG warm-up failed (non-fatal): {_e}")

    # ── Pre-warm the Semantic Router (builds route embeddings once) ──────────────
    try:
        from voice.semantic_router import warmup as router_warmup
        router_warmup()
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Semantic Router warm-up failed (non-fatal): {_e}")

    # ── Pre-boot Gemini REPL (boot happens in background, ~14s) ─────────────────
    try:
        from llm.constants import ENGINE
        if ENGINE == "gemini_cli":
            from llm.gemini_repl import boot_repl_sync
            boot_repl_sync()  # Non-blocking — boots in daemon thread
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(f"Gemini REPL pre-boot failed (non-fatal): {_e}")

    # ── Start the Tray application (which starts the Telegram daemon) ───────────
    from tray.tray_app import setup_tray
    setup_tray()


if __name__ == "__main__":
    start_assistant()
