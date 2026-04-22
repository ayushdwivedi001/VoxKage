"""
VoxKage Hybrid Memory Manager (Phase 2)

Architecture:
  - Short-term: SQLite history DB via Mem0's built-in history_db_path
  - Long-term: Local Qdrant (in-process) vector store via Mem0
  - Embeddings: sentence-transformers/all-MiniLM-L6-v2 (already cached, offline)
  - LLM for memory extraction: Ollama + Qwen3.5 (existing local model)

Mem0 acts as the intelligence layer that decides what is worth remembering
from each conversation exchange, then stores the "meaning" as vectors.

Usage:
  from llm.memory_manager import search_memory, add_memory_async
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_MEMORY_DIR = _ROOT / "data" / "voxkage_memory"
_HISTORY_DB = str(_ROOT / "data" / "voxkage_memory" / "history.db")

# ─── Singletons ───────────────────────────────────────────────────────────────
_mem0_instance = None
_USER_ID = "voxkage_primary_user"  # Single-user assistant — one memory profile

# ─── Config ───────────────────────────────────────────────────────────────────
_MEMORY_ENABLED = True  # Mirrors constants.USE_MEMORY; overridable for tests
_MAX_MEMORY_RESULTS = 3  # How many memories to inject per turn


def _get_mem0() -> Optional[object]:
    """
    Lazy-initialize Mem0 with fully offline configuration:
      - Vector store: local Qdrant (in-process, no Docker)
      - Embedder: sentence-transformers/all-MiniLM-L6-v2 (already cached)
      - LLM: Ollama (Qwen 3.5 — existing local model)
    """
    global _mem0_instance
    if _mem0_instance is not None:
        return _mem0_instance

    try:
        from llm.constants import OLLAMA_HOST, MODEL_NAME, USE_MEMORY
        if not USE_MEMORY:
            logger.info("[Memory] USE_MEMORY=False, skipping initialization.")
            return None

        _MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        from mem0 import Memory
        from mem0.configs.base import MemoryConfig
        from mem0.configs.vector_stores.qdrant import QdrantConfig
        from mem0.configs.embeddings.base import BaseEmbedderConfig
        from mem0.configs.llms.base import BaseLlmConfig

        config = MemoryConfig(
            # ── Vector Store: Local Qdrant (in-process, no Docker required) ─────
            vector_store={
                "provider": "qdrant",
                "config": {
                    "collection_name": "voxkage_memories",
                    "embedding_model_dims": 384,  # all-MiniLM-L6-v2 output dim
                    "path": str(_MEMORY_DIR / "qdrant"),  # Local persistent storage
                    "on_disk": True,  # Persist between sessions
                }
            },
            # ── Embedder: sentence-transformers (already cached offline) ─────────
            embedder={
                "provider": "huggingface",
                "config": {
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                }
            },
            # ── LLM: Ollama (Qwen 3.5 — existing local model) ─────────────────
            llm={
                "provider": "ollama",
                "config": {
                    "model": MODEL_NAME,
                    "ollama_base_url": OLLAMA_HOST,
                    "temperature": 0.1,  # Low temp for factual extraction
                    "max_tokens": 500,
                }
            },
            # ── SQLite history (short-term, conversation-level) ────────────────
            history_db_path=_HISTORY_DB,
            # ── VoxKage-specific extraction prompt ────────────────────────────
            custom_instructions=(
                "Extract facts about the user that will help personalize future responses. "
                "Focus on: preferences (music, apps, habits), names (contacts, projects), "
                "recurring tasks, corrections the user makes, and any personal context. "
                "Do NOT store generic conversation content. Only store memorable facts."
            ),
        )

        _mem0_instance = Memory(config=config)
        logger.info("[Memory] Mem0 initialized (local Qdrant + sentence-transformers + Ollama).")
        return _mem0_instance

    except Exception as e:
        logger.warning(f"[Memory] Mem0 initialization failed (non-fatal): {e}")
        return None


def search_memory(query: str) -> str:
    """
    Search for relevant memories for the given query.
    Returns a formatted string snippet ready to inject as a system message.
    Returns empty string if no relevant memories or memory is disabled.
    """
    try:
        from llm.constants import USE_MEMORY
        if not USE_MEMORY:
            return ""

        mem = _get_mem0()
        if mem is None:
            return ""

        results = mem.search(query=query, filters={"user_id": _USER_ID}, top_k=_MAX_MEMORY_RESULTS)

        # mem0 returns {"results": [{"memory": "...", "score": 0.9, ...}, ...]}
        memories_list = results if isinstance(results, list) else results.get("results", [])

        if not memories_list:
            return ""

        # Only include high-confidence memories (score > 0.5)
        relevant = [
            r.get("memory", "")
            for r in memories_list
            if r.get("score", 0) > 0.5 and r.get("memory", "").strip()
        ]

        if not relevant:
            return ""

        snippet = "USER MEMORY (use to personalize response):\n" + "\n".join(f"  • {m}" for m in relevant)
        logger.info(f"[Memory] Injecting {len(relevant)} memories: {relevant}")
        return snippet

    except Exception as e:
        logger.warning(f"[Memory] Memory search failed (non-fatal): {e}")
        return ""


def add_memory(user_message: str, assistant_response: str) -> None:
    """
    Extract and store memorable facts from a conversation exchange.
    Should be called after each successful response (non-blocking in caller).
    """
    try:
        from llm.constants import USE_MEMORY
        if not USE_MEMORY:
            return

        mem = _get_mem0()
        if mem is None:
            return

        # Skip trivially short or mechanical exchanges
        if len(user_message.strip()) < 10 or len(assistant_response.strip()) < 5:
            return

        # Skip tool-result-only responses (they don't contain memorable facts)
        _skip_prefixes = ["Done, sir", "Right away", "Certainly, sir", "Got it", "Playing", "Opening"]
        if any(assistant_response.strip().startswith(p) for p in _skip_prefixes):
            return

        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_response},
        ]
        mem.add(messages, user_id=_USER_ID)
        logger.debug(f"[Memory] Memory added for exchange: {user_message[:60]!r}")

    except Exception as e:
        logger.warning(f"[Memory] Memory add failed (non-fatal): {e}")


async def add_memory_async(user_message: str, assistant_response: str) -> None:
    """
    Non-blocking async wrapper for add_memory.
    Runs in a thread so it never blocks the main response loop.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, add_memory, user_message, assistant_response)


def get_all_memories() -> list:
    """
    Returns all stored memories for the current user.
    Used by the settings GUI Memory tab.
    """
    try:
        mem = _get_mem0()
        if mem is None:
            return []
        result = mem.get_all(filters={"user_id": _USER_ID})
        memories = result if isinstance(result, list) else result.get("results", [])
        return memories
    except Exception as e:
        logger.warning(f"[Memory] get_all_memories failed: {e}")
        return []


def delete_all_memories() -> bool:
    """
    Deletes all stored memories. Used by the Settings GUI 'Clear Memory' button.
    Returns True on success.
    """
    global _mem0_instance
    try:
        mem = _get_mem0()
        if mem is None:
            return True
        mem.delete_all(user_id=_USER_ID)
        logger.info("[Memory] All memories cleared.")
        return True
    except Exception as e:
        logger.warning(f"[Memory] delete_all_memories failed: {e}")
        return False


def memory_count() -> int:
    """Returns the number of stored memories (for the settings GUI badge)."""
    return len(get_all_memories())
