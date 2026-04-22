# LLM Client Constants

OLLAMA_HOST = "http://localhost:11434"
MODEL_NAME = "fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:4b"
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