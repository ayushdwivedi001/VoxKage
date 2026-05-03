# LLM Client Constants

MAX_HISTORY = 10

# Phase 28c: Conversational memory for follow-up commands
GLOBAL_LAST_RESULTS = []  # Stores last extracted items (titles, URLs, etc.)

# ─── Architecture Upgrade Feature Flags ───────────────────────────────────────
# Set to False to instantly fall back to the previous behavior for that feature.

# Phase 1: Tool RAG — retrieve only the N most relevant tools per query
USE_RAG = True
TOP_K_TOOLS = 8          # Number of tools to retrieve per query (5–10 recommended)

# Phase 2: Mem0 Hybrid Memory — persistent cross-session personalization
USE_MEMORY = True

# Phase 3: Semantic Router — instant bypass for simple commands
USE_SEMANTIC_ROUTER = True
SEMANTIC_ROUTER_THRESHOLD = 0.80  # Cosine similarity threshold (0–1)

# ─── Hybrid Reasoning Engine ──────────────────────────────────────────────────
# ENGINE is now permanently gemini_cli
ENGINE: str = "gemini_cli"

# Gemini CLI subprocess settings
GEMINI_CLI_PATH: str = "gemini"           # Resolves to gemini.cmd on Windows PATH
GEMINI_MODEL: str = "gemini-2.5-flash"   # Flash: best speed/quality for voice UX
GEMINI_PRO_MODEL: str = "gemini-2.5-pro" # Kept for reference (not used — too slow for voice)
GEMINI_CLI_TIMEOUT: float = 60.0          # Timeout per response (REPL: only thinking time)

# ─── Agentic Loop Overrides ───────────────────────────────────────────────────
# Using flash for agentic steps too — pro is 40-60s per step, unusable for voice.
GEMINI_AGENTIC_MODEL: str = "gemini-2.5-flash"
GEMINI_AGENTIC_TIMEOUT: float = 60.0     # Flash thinks in ~5-15s via persistent REPL
GEMINI_AGENTIC_RETRIES: int = 1          # Max retries for Gemini execution

# ─── Persistent REPL Settings ─────────────────────────────────────────────────
# The REPL keeps ONE CLI process alive. Boot happens ONCE at startup (~14s).
# Subsequent calls take only model thinking time (~5-15s for flash).
GEMINI_REPL_SILENCE_TIMEOUT: float = 1.5  # Seconds of stdout silence = response done
GEMINI_REPL_MAX_WAIT: float = 60.0        # Hard timeout per response