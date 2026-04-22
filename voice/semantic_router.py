"""
VoxKage Semantic Router (Phase 3)

Uses sentence-transformers (all-MiniLM-L6-v2, already cached) to embed
user commands and compare them against canonical route embeddings.

If cosine similarity > THRESHOLD → instant bypass of LLM (< 50ms response).
If similarity < THRESHOLD → return None → falls through to existing pipeline.

Coverage: ~30% of all commands handled instantly without touching Ollama.
"""

import os
import logging
import json
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────
THRESHOLD = 0.80  # Cosine similarity threshold for confident routing

# ─── Route Definitions ────────────────────────────────────────────────────────
# Each route has canonical examples. The router embeds ALL of them at startup.
# More examples = better coverage. Mix English + Hinglish for VoxKage's user.
SEMANTIC_ROUTES = {
    "set_volume": [
        "set volume to 50", "volume 70 percent", "turn volume up", "make it louder",
        "increase volume", "decrease volume", "lower the sound", "volume kam karo",
        "awaz badhao", "awaz kam karo", "mute the sound", "unmute",
        "volume up", "volume down", "set audio to 60", "make it quieter",
        "turn the sound down", "sound zyada karo", "sound thoda kam karo",
    ],
    "set_brightness": [
        "increase brightness", "decrease brightness", "dim the screen",
        "make screen brighter", "screen too bright", "lower brightness",
        "brightness 80 percent", "set brightness to 50", "screen dark karo",
        "brightness badhao", "display light kam karo", "screen roshni badhao",
        "reduce screen brightness", "max brightness",
    ],
    "wifi_on": [
        "turn wifi on", "enable wifi", "connect to wifi", "wifi on karo",
        "wifi chalu karo", "enable internet", "turn on wifi",
        "connect internet", "wifi enable karo",
    ],
    "wifi_off": [
        "turn wifi off", "disable wifi", "disconnect wifi", "wifi band karo",
        "wifi off karo", "internet band karo", "disconnect internet",
        "disable internet connection", "wifi shut off",
    ],
    "bluetooth_on": [
        "turn bluetooth on", "enable bluetooth", "bluetooth on karo",
        "bluetooth chalu karo", "connect bluetooth", "bt on",
    ],
    "bluetooth_off": [
        "turn bluetooth off", "disable bluetooth", "bluetooth band karo",
        "bluetooth off karo", "bt off", "disconnect bluetooth",
    ],
    "shutdown": [
        "shutdown the computer", "turn off the pc", "power off",
        "shut down computer", "pc band karo", "computer shut down",
        "shutdown now", "turn off system",
    ],
    "restart": [
        "restart the computer", "reboot the system", "restart pc",
        "reboot now", "pc restart karo", "restart system",
        "reboot computer", "restart machine",
    ],
    "sleep": [
        "put computer to sleep", "sleep mode", "hibernate",
        "pc sula do", "suspend computer", "go to sleep",
        "put system to sleep", "sleep the computer",
    ],
    "screenshot": [
        "take a screenshot", "capture the screen", "screenshot this",
        "save what's on screen", "grab a screenshot", "screen capture",
        "ss le lo", "screenshot lo", "photo lo screen ka",
        "take screen picture", "capture screen now",
    ],
    "wallpaper": [
        "change wallpaper", "new desktop background", "change background",
        "wallpaper badlo", "background change karo", "change desktop picture",
        "set new wallpaper", "random wallpaper",
    ],
    "lock": [
        "lock the screen", "lock pc", "lock computer", "screen lock karo",
        "lock workstation", "lock my computer",
    ],
}

# ─── Singletons ───────────────────────────────────────────────────────────────
_route_embeddings = None  # {route_name: np.ndarray of shape (N_examples, 384)}
_embed_model = None


def _get_embed_model():
    """Get the sentence-transformers model (shared with tool_rag.py if already loaded)."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    try:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        logger.info("[SemanticRouter] Embedding model loaded.")
        return _embed_model
    except Exception as e:
        logger.warning(f"[SemanticRouter] Could not load model: {e}")
        return None


def _build_route_embeddings():
    """Pre-compute embeddings for all route examples. Called once at startup."""
    global _route_embeddings
    model = _get_embed_model()
    if model is None:
        _route_embeddings = {}
        return

    import numpy as np
    _route_embeddings = {}
    for route_name, examples in SEMANTIC_ROUTES.items():
        vecs = model.encode(examples, normalize_embeddings=True, show_progress_bar=False)
        _route_embeddings[route_name] = vecs  # shape: (N, 384)

    total = sum(len(v) for v in _route_embeddings.values())
    logger.info(f"[SemanticRouter] Built embeddings for {len(_route_embeddings)} routes ({total} examples).")


def _ensure_routes_ready():
    """Ensure route embeddings are computed (lazy initialization)."""
    global _route_embeddings
    if _route_embeddings is None:
        _build_route_embeddings()


def route_command(text: str, threshold: float = THRESHOLD) -> Optional[dict]:
    """
    Try to route a command to a deterministic handler without LLM.

    Args:
        text: The raw user command string.
        threshold: Cosine similarity threshold for confident routing.

    Returns:
        A dict like {"intent": "set_volume", "level": 70} if confident match.
        None if the command should fall through to the LLM pipeline.
    """
    from llm.constants import USE_SEMANTIC_ROUTER, SEMANTIC_ROUTER_THRESHOLD
    if not USE_SEMANTIC_ROUTER:
        return None

    threshold = SEMANTIC_ROUTER_THRESHOLD  # Use the configured threshold

    try:
        _ensure_routes_ready()

        if not _route_embeddings:
            return None

        import numpy as np
        model = _get_embed_model()
        if model is None:
            return None

        query_vec = model.encode([text], normalize_embeddings=True)[0]  # shape: (384,)

        best_route = None
        best_score = 0.0

        for route_name, example_vecs in _route_embeddings.items():
            # Score = max cosine similarity across all examples for this route
            scores = example_vecs @ query_vec  # shape: (N,)
            max_score = float(np.max(scores))
            if max_score > best_score:
                best_score = max_score
                best_route = route_name

        if best_score >= threshold:
            logger.info(f"[SemanticRouter] Matched '{best_route}' (score={best_score:.3f}) for: {text!r}")
            return _build_intent(best_route, text)
        else:
            logger.debug(f"[SemanticRouter] No confident match (best={best_score:.3f}) for: {text!r}")
            return None

    except Exception as e:
        logger.warning(f"[SemanticRouter] Routing failed (non-fatal): {e}")
        return None


def _build_intent(route_name: str, text: str) -> dict:
    """
    Build the structured intent dict from a route name.
    Extracts level values for volume/brightness from the text.
    """
    import re

    if route_name == "set_volume":
        nums = re.findall(r'\d+', text)
        level = int(nums[0]) if nums else _infer_level(text, default=50)
        return {"intent": "set_volume", "level": level}

    elif route_name == "set_brightness":
        nums = re.findall(r'\d+', text)
        level = int(nums[0]) if nums else _infer_level(text, default=70)
        return {"intent": "set_brightness", "level": level}

    elif route_name in ("wifi_on", "wifi_off", "bluetooth_on", "bluetooth_off",
                        "shutdown", "restart", "sleep", "lock"):
        return {"intent": route_name}

    elif route_name == "screenshot":
        return {"intent": "screenshot"}

    elif route_name == "wallpaper":
        return {"intent": "wallpaper"}

    return {"intent": route_name}


def _infer_level(text: str, default: int = 50) -> int:
    """Infer a percentage level from qualitative words."""
    t = text.lower()
    if any(w in t for w in ["low", "quiet", "soft", "dim", "small", "kam"]):
        return 20
    if any(w in t for w in ["medium", "moderate", "middle", "normal"]):
        return 50
    if any(w in t for w in ["high", "loud", "bright", "max", "full", "zyada", "badhao"]):
        return 80
    if any(w in t for w in ["up", "more", "increase", "raise"]):
        return 70
    if any(w in t for w in ["down", "less", "decrease", "lower", "reduce"]):
        return 30
    return default


def warmup():
    """Pre-warm the semantic router at startup. Call from main.py."""
    try:
        _ensure_routes_ready()
        logger.info("[SemanticRouter] Warmed up successfully.")
    except Exception as e:
        logger.warning(f"[SemanticRouter] Warm-up failed (non-fatal): {e}")
