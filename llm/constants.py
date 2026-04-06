# LLM Client Constants

OLLAMA_HOST = "http://localhost:11434"
MODEL_NAME = "qwen3.5:4b-q4_k_m"
MAX_HISTORY = 10

# Phase 28c: Conversational memory for follow-up commands
GLOBAL_LAST_RESULTS = []  # Stores last extracted items (titles, URLs, etc.)