"""
VoxKage Tool RAG Engine
Embedding + LanceDB: Just-In-Time tool retrieval.
Only the most semantically relevant tools are injected into each LLM call.

Embedding Backend Priority:
  1. sentence-transformers (all-MiniLM-L6-v2) — uses existing torch from Whisper
  2. fastembed (BAAI/bge-small-en-v1.5) — requires one-time internet download
  3. BM25 keyword fallback — zero dependencies, always works offline
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _ROOT / "data"
_LANCE_DIR = str(_DATA_DIR / "tool_index.lance")
_HASH_FILE = str(_DATA_DIR / "tool_index.hash")

# ─── Module-level singletons (loaded once, reused across calls) ───────────────
_embed_model = None
_embed_backend = None  # "sentence_transformers" | "fastembed" | "bm25"
_lance_table = None
_embed_dim = 384       # auto-detected when model loads


def _get_embed_model():
    """
    Lazy-load the best available embedding model.
    Priority: sentence-transformers (uses existing torch) → fastembed → BM25 fallback.
    sentence-transformers is preferred because torch is already installed for Whisper.
    """
    global _embed_model, _embed_backend
    if _embed_model is not None:
        return _embed_model

    # 1) Try sentence-transformers (torch already installed for Whisper)
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("[ToolRAG] Loading sentence-transformers all-MiniLM-L6-v2...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        _embed_backend = "sentence_transformers"
        # Detect actual output dimension
        test_vec = _embed_model.encode(["test"], normalize_embeddings=True)
        global _embed_dim
        _embed_dim = int(test_vec.shape[1])
        logger.info(f"[ToolRAG] sentence-transformers ready (dim={_embed_dim}).")
        return _embed_model
    except Exception as e:
        logger.warning(f"[ToolRAG] sentence-transformers not available: {e}")

    # 2) Try fastembed (requires network for first download)
    try:
        from fastembed import TextEmbedding
        logger.info("[ToolRAG] Loading FastEmbed BAAI/bge-small-en-v1.5...")
        _embed_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        _embed_backend = "fastembed"
        logger.info("[ToolRAG] FastEmbed model ready.")
        return _embed_model
    except Exception as e:
        logger.warning(f"[ToolRAG] FastEmbed not available: {e}")

    # 3) BM25 keyword fallback (zero dependencies, always works)
    logger.warning("[ToolRAG] No embedding model available — using BM25 keyword fallback.")
    _embed_backend = "bm25"
    _embed_model = "bm25"
    return _embed_model


def _embed(texts: List[str]) -> List[List[float]]:
    """Embed a list of strings. Returns list of float vectors."""
    model = _get_embed_model()
    if _embed_backend == "bm25":
        raise RuntimeError("BM25 mode: use _bm25_retrieve() directly, not LanceDB.")
    elif _embed_backend == "sentence_transformers":
        vecs = model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
    elif _embed_backend == "fastembed":
        return [vec.tolist() for vec in model.embed(texts)]
    raise RuntimeError(f"Unknown embed backend: {_embed_backend}")


def _definitions_hash() -> str:
    """Hash the tool_definitions.py file content to detect changes."""
    defs_path = Path(__file__).parent / "tool_definitions.py"
    with open(defs_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _index_is_fresh() -> bool:
    """Return True if the LanceDB index exists and matches current tool definitions."""
    if not os.path.exists(_LANCE_DIR) or not os.path.exists(_HASH_FILE):
        return False
    with open(_HASH_FILE, "r") as f:
        stored_hash = f.read().strip()
    return stored_hash == _definitions_hash()


def _bm25_retrieve(query: str, top_k: int = 8) -> list:
    """
    BM25-style keyword retrieval. Used when no embedding model is available.
    Scores each tool by term overlap between query and corpus_text.
    """
    from llm.tool_definitions import TOOL_DEFINITIONS, get_ollama_schema
    query_tokens = set(query.lower().split())
    scored = []
    for tool in TOOL_DEFINITIONS:
        examples = " ".join(tool.get("example_queries", []))
        corpus = f"{tool['name']} {tool['description']} {examples}".lower()
        corpus_tokens = set(corpus.split())
        overlap = len(query_tokens & corpus_tokens)
        # Boost on name/tag exact match
        name_match = 5 if tool["name"].replace("_", " ") in query.lower() else 0
        tag_match = sum(3 for t in tool.get("tags", []) if t in query.lower())
        scored.append((overlap + name_match + tag_match, tool))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [get_ollama_schema(t) for _, t in scored[:top_k]]


def build_tool_index(force: bool = False) -> None:
    """
    Build (or rebuild) the LanceDB vector index from TOOL_DEFINITIONS.
    Skips if already fresh unless force=True.
    Falls back gracefully if no embedding model is available (BM25 mode).
    """
    # Initialize embed model first to detect BM25 mode
    _get_embed_model()
    if _embed_backend == "bm25":
        logger.warning("[ToolRAG] BM25 mode — no vector index built. Install sentence-transformers.")
        return

    if not force and _index_is_fresh():
        logger.info("[ToolRAG] Index is fresh, skipping rebuild.")
        return

    logger.info("[ToolRAG] Building tool index...")
    from llm.tool_definitions import TOOL_DEFINITIONS, get_ollama_schema
    import lancedb
    import pyarrow as pa

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Build corpus for embedding: name + description + example_queries joined
    records = []
    corpus = []
    for tool in TOOL_DEFINITIONS:
        examples = " | ".join(tool.get("example_queries", []))
        full_text = f"{tool['name']}: {tool['description']} Examples: {examples}"
        corpus.append(full_text)
        records.append({
            "name": tool["name"],
            "description": tool["description"],
            "tags": json.dumps(tool.get("tags", [])),
            "schema_json": json.dumps(get_ollama_schema(tool)),
            "corpus_text": full_text,
        })

    logger.info(f"[ToolRAG] Embedding {len(corpus)} tool descriptions...")
    embeddings = _embed(corpus)

    # Build PyArrow table
    schema = pa.schema([
        pa.field("name", pa.string()),
        pa.field("description", pa.string()),
        pa.field("tags", pa.string()),
        pa.field("schema_json", pa.string()),
        pa.field("corpus_text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), _embed_dim)),
    ])

    table_data = {
        "name":        [r["name"] for r in records],
        "description": [r["description"] for r in records],
        "tags":        [r["tags"] for r in records],
        "schema_json": [r["schema_json"] for r in records],
        "corpus_text": [r["corpus_text"] for r in records],
        "vector":      [[float(x) for x in v] for v in embeddings],
    }

    # Connect to LanceDB and (re)create table
    db = lancedb.connect(_LANCE_DIR)
    if "tools" in db.table_names():
        db.drop_table("tools")
    db.create_table("tools", data=[
        {k: table_data[k][i] for k in table_data}
        for i in range(len(records))
    ], schema=schema)

    # Store the hash so we don't rebuild unnecessarily
    with open(_HASH_FILE, "w") as f:
        f.write(_definitions_hash())

    logger.info(f"[ToolRAG] Index built: {len(records)} tools indexed.")


def _get_table():
    """Get the LanceDB table, building index if needed. Returns None in BM25 mode."""
    global _lance_table
    _get_embed_model()  # Ensure backend is detected
    if _embed_backend == "bm25":
        return None
    if _lance_table is None:
        import lancedb
        ensure_index_fresh()
        db = lancedb.connect(_LANCE_DIR)
        _lance_table = db.open_table("tools")
    return _lance_table


def ensure_index_fresh() -> None:
    """Build the index if it doesn't exist or is stale. Called at startup."""
    global _lance_table
    if not _index_is_fresh():
        _lance_table = None  # Force re-open after rebuild
        build_tool_index()


def retrieve_tools(query: str, top_k: int = 8) -> list:
    """
    Retrieve the top_k most relevant tool schemas for the given query.
    Uses vector search (LanceDB) if an embedding model is available, else BM25 keyword matching.
    Returns a list of Ollama-format tool schemas ready to pass to ollama.chat(tools=...).
    Falls back to full schema list if everything fails.
    """
    try:
        _get_embed_model()  # Ensure backend is detected

        # BM25 path (no embedding model available)
        if _embed_backend == "bm25":
            schemas = _bm25_retrieve(query, top_k)
            names = [s["function"]["name"] for s in schemas]
            logger.info(f"[ToolRAG/BM25] Retrieved {len(schemas)} tools: {names}")
            return schemas

        # Vector search path
        table = _get_table()
        query_vec = _embed([query])[0]
        results = table.search(query_vec).limit(top_k).to_list()

        schemas = []
        retrieved_names = []
        for row in results:
            try:
                schema = json.loads(row["schema_json"])
                schemas.append(schema)
                retrieved_names.append(row["name"])
            except Exception as e:
                logger.warning(f"[ToolRAG] Failed to parse schema for {row.get('name', '?')}: {e}")

        logger.info(f"[ToolRAG/{_embed_backend}] Retrieved {len(schemas)} tools: {retrieved_names}")
        return schemas

    except Exception as e:
        logger.error(f"[ToolRAG] Retrieval failed: {e}. Falling back to full schema.")
        from llm.tool_definitions import get_all_ollama_schemas
        return get_all_ollama_schemas()


def retrieve_tool_names(query: str, top_k: int = 8) -> list:
    """Same as retrieve_tools but returns only the tool names (for logging/routing)."""
    schemas = retrieve_tools(query, top_k)
    return [s["function"]["name"] for s in schemas]


def get_schema_by_name(tool_name: str) -> dict | None:
    """
    Fetch the full Ollama schema for a specific tool by name.
    Used as fallback when Qwen calls a tool that wasn't in the retrieved subset.
    """
    from llm.tool_definitions import TOOL_DEFINITIONS_BY_NAME, get_ollama_schema
    tool_def = TOOL_DEFINITIONS_BY_NAME.get(tool_name)
    if tool_def:
        return get_ollama_schema(tool_def)
    return None


# ─── CLI entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    force = "--force" in sys.argv or "--build" in sys.argv
    build_tool_index(force=force)
    print("✅ Tool index built successfully.")
    
    # Quick smoke test
    if "--test" in sys.argv:
        test_queries = [
            "play lofi music on Spotify",
            "set volume to 70",
            "check my Gmail inbox",
            "search Amazon for headphones",
            "take a screenshot",
        ]
        print("\n--- Retrieval Smoke Test ---")
        for q in test_queries:
            names = retrieve_tool_names(q, top_k=5)
            print(f"Query: {q!r}")
            print(f"  → {names}\n")
