"""
MCP Server: VoxKage Persistent Memory (Self-Learning + User Soul)

TWO MEMORY TRACKS:
  Track 1 — Problem/Solution Memory (self-healing)
    VoxKage logs failures autonomously, then records solutions.
    Before complex tasks, it retrieves past learnings.

  Track 2 — User Soul (personalization)
    Permanent structured profile of the user — preferences, habits,
    trusted actions, identity. Never cleared. Always recalled.

Storage:
  ~/.voxkage/memory.jsonl        problem/solution log (JSONL, append-only)
  ~/.voxkage/user_profile.json   user soul (structured JSON, keyed)

Retrieval: TF-IDF keyword similarity (pure Python, zero heavy deps)
Token efficiency: recall_user() returns max 10 compact bullets, not raw JSON

Run standalone: python mcp_servers/memory_server.py
"""

import json
import math
import os
import re
import sys
import uuid
from datetime import datetime

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-memory")

# ── Storage paths ─────────────────────────────────────────────────────────────
_MEM_DIR     = os.path.join(os.path.expanduser("~"), ".voxkage")
_MEM_FILE    = os.path.join(_MEM_DIR, "memory.jsonl")
_PROFILE_FILE = os.path.join(_MEM_DIR, "user_profile.json")

os.makedirs(_MEM_DIR, exist_ok=True)


# ── TF-IDF similarity (pure Python) ──────────────────────────────────────────

_STOP = {
    "the","a","an","is","it","in","on","at","to","of","and","or","but",
    "i","you","me","my","was","were","be","been","have","has","had",
    "this","that","these","those","for","with","from","not","did","do",
    "then","when","could","would","should","so","what","how","why","where",
    "just","also","very","will","can","get","use","used",
}

def _tokenize(text: str) -> list:
    return [w for w in re.sub(r"[^a-z0-9]", " ", text.lower()).split()
            if w not in _STOP and len(w) > 2]

def _tf(tokens: list) -> dict:
    freq: dict = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    total = max(len(tokens), 1)
    return {t: c / total for t, c in freq.items()}

def _cosine(a: dict, b: dict) -> float:
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[k] * b[k] for k in shared)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

def _similarity(query: str, entry_text: str) -> float:
    return _cosine(_tf(_tokenize(query)), _tf(_tokenize(entry_text)))


# ── Problem/Solution Memory I/O ───────────────────────────────────────────────

def _load_all() -> list:
    if not os.path.exists(_MEM_FILE):
        return []
    entries = []
    with open(_MEM_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries

def _save_entry(entry: dict):
    with open(_MEM_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def _update_entry(entry_id: str, updates: dict) -> bool:
    entries = _load_all()
    found = False
    for e in entries:
        if e.get("id") == entry_id:
            e.update(updates)
            found = True
    if not found:
        return False
    with open(_MEM_FILE, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return True


# ── User Profile I/O ──────────────────────────────────────────────────────────

def _load_profile() -> dict:
    """Load user_profile.json. Returns empty skeleton if not found."""
    if not os.path.exists(_PROFILE_FILE):
        return {
            "identity":       {},
            "preferences":    {},
            "habits":         [],
            "trusted_actions": {},
            "notes":          [],
            "last_updated":   None,
        }
    try:
        with open(_PROFILE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "identity":       {},
            "preferences":    {},
            "habits":         [],
            "trusted_actions": {},
            "notes":          [],
            "last_updated":   None,
        }

def _save_profile(profile: dict):
    profile["last_updated"] = datetime.now().isoformat()
    with open(_PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


# ═════════════════════════════════════════════════════════════════════════════
# TRACK 1 — PROBLEM / SOLUTION SELF-LEARNING
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def log_problem(
    problem: str,
    context: str,
    attempted_approaches: str = "",
) -> str:
    """
    SELF-LEARNING: Log a failure or mistake VoxKage made during a task.

    Call this AUTONOMOUSLY (without being asked) when:
    - A tool was called 2+ times without success on the same task
    - The user had to manually intervene or correct you
    - You navigated to the wrong file/folder/app before finding the right one
    - An agent_step workflow failed or stalled requiring manual intervention
    - Any error that required the user to provide explicit correction

    DO NOT log minor single-step corrections. Only log significant failures.

    Parameters:
      problem             : Short description of what failed
      context             : More details — what the user asked, what tools failed, what went wrong
      attempted_approaches: What you tried that didn't work

    Returns the memory ID (save it to call log_solution later).
    """
    entry = {
        "id":        str(uuid.uuid4())[:8],
        "type":      "problem",
        "timestamp": datetime.now().isoformat(),
        "problem":   problem,
        "context":   context,
        "attempted": attempted_approaches,
        "solution":  None,
        "solved_at": None,
        "status":    "unsolved",
    }
    _save_entry(entry)
    return (
        f"[MEMORY] Problem logged — ID: {entry['id']}\n"
        f"Problem: {problem}\n"
        f"Status: unsolved — will update when solution is found."
    )


@mcp.tool()
def log_solution(
    problem_id: str,
    solution: str,
    what_worked: str,
    prevention: str = "",
) -> str:
    """
    SELF-LEARNING: Record the solution to a previously logged problem.

    Call this AUTONOMOUSLY after successfully resolving a failure that was
    logged with log_problem. Only call when the task ACTUALLY completed.

    Parameters:
      problem_id  : The ID returned by log_problem
      solution    : What ultimately worked
      what_worked : Specific tool/action that resolved it
      prevention  : How to avoid this issue next time

    Returns confirmation.
    """
    updated = _update_entry(problem_id, {
        "solution":   solution,
        "what_worked": what_worked,
        "prevention": prevention,
        "solved_at":  datetime.now().isoformat(),
        "status":     "solved",
    })

    if not updated:
        # Problem may be from a different session — save standalone
        entry = {
            "id":          problem_id,
            "type":        "solution",
            "timestamp":   datetime.now().isoformat(),
            "solution":    solution,
            "what_worked": what_worked,
            "prevention":  prevention,
            "solved_at":   datetime.now().isoformat(),
            "status":      "solved",
        }
        _save_entry(entry)

    return (
        f"[MEMORY] Solution recorded for '{problem_id}'.\n"
        f"Solution: {solution}\n"
        f"Prevention: {prevention}"
    )


@mcp.tool()
def search_memory(query: str, top_k: int = 3) -> str:
    """
    SELF-LEARNING: Search past failures and solutions relevant to current task.

    Call this BEFORE attempting complex tasks — especially if:
    - Opening/finding files or apps
    - Multi-step browser tasks
    - Image or file downloading
    - Any workflow that has failed before

    Parameters:
      query : Description of what you're about to do
      top_k : Number of relevant memories to return (default 3, max 5)

    Returns relevant past learnings ranked by similarity. Token-compact output.
    """
    entries = [e for e in _load_all() if e.get("type") in ("problem", "solution")]
    if not entries:
        return "[MEMORY] No past learnings yet. Proceed with current best approach."

    top_k = min(top_k, 5)
    scored = []
    for e in entries:
        text = " ".join(filter(None, [
            e.get("problem", ""),
            e.get("context", ""),
            e.get("solution", ""),
            e.get("prevention", ""),
            e.get("what_worked", ""),
        ]))
        score = _similarity(query, text)
        if score > 0.05:
            scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        return "[MEMORY] No relevant past learnings found. Proceed normally."

    lines = [f"[MEMORY] {len(top)} relevant past learning(s):\n"]
    for i, (score, e) in enumerate(top, 1):
        status = "\u2705 SOLVED" if e.get("status") == "solved" else "\u26a0\ufe0f UNSOLVED"
        lines.append(f"--- Learning #{i} [{status}] ---")
        lines.append(f"Problem: {e.get('problem', 'N/A')}")
        if e.get("solution"):
            lines.append(f"\u2705 Solution: {e['solution']}")
        if e.get("what_worked"):
            lines.append(f"What worked: {e['what_worked']}")
        if e.get("prevention"):
            lines.append(f"\U0001f6e1 Prevention: {e['prevention']}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def list_memory(status_filter: str = "all") -> str:
    """
    Show all stored problem/solution memories.

    Parameters:
      status_filter : "all", "solved", or "unsolved"
    """
    entries = [e for e in _load_all() if e.get("type") in ("problem", "solution")]
    if not entries:
        return "[MEMORY] No problem/solution memories stored yet."

    if status_filter != "all":
        entries = [e for e in entries if e.get("status") == status_filter]

    if not entries:
        return f"[MEMORY] No {status_filter} memories found."

    lines = [f"[MEMORY] {len(entries)} entr(ies) [{status_filter}]:\n"]
    for e in entries:
        icon = "\u2705" if e.get("status") == "solved" else "\u26a0\ufe0f"
        lines.append(f"{icon} [{e.get('id', '?')}] {e.get('timestamp', '')[:10]}")
        lines.append(f"   Problem: {e.get('problem', 'N/A')}")
        if e.get("solution"):
            lines.append(f"   Solution: {e['solution']}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def forget_memory(memory_id: str) -> str:
    """
    Remove a specific problem/solution memory entry by its ID.

    Parameters:
      memory_id : The ID of the memory to delete
    """
    entries = _load_all()
    before = len(entries)
    entries = [e for e in entries if e.get("id") != memory_id]
    after = len(entries)

    if before == after:
        return f"[MEMORY] No entry found with ID '{memory_id}'."

    with open(_MEM_FILE, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    return f"[MEMORY] Entry '{memory_id}' deleted."


# ═════════════════════════════════════════════════════════════════════════════
# TRACK 2 — USER SOUL (PERSONALIZATION)
# ═════════════════════════════════════════════════════════════════════════════

_VALID_CATEGORIES = {
    "identity",       # name, location, device, language
    "preferences",    # youtube, spotify, image_style, apps, etc.
    "habits",         # recurring patterns ("usually works in Downloads")
    "trusted_actions", # actions that skip confirmation gates
    "notes",          # free-form observations about the user
}

@mcp.tool()
def remember_user(
    category: str,
    key: str,
    value: str,
    context: str = "",
) -> str:
    """
    USER SOUL: Save or update a fact, preference, or habit about the user.

    Call this AUTONOMOUSLY (without being asked) whenever you detect:
    - User mentions their name, location, age, device → category="identity"
    - User expresses preference ("I prefer X", "I like Y", "play my usual") → category="preferences"
    - User reveals a habit ("I always...", "I usually...", "my routine is...") → category="habits"
    - User says "don't ask me again for X" or "just do X without asking" → use set_trusted_action()
    - You observe something important about the user mid-conversation → category="notes"

    This is IDEMPOTENT — updating an existing key replaces it, no duplicates.
    This memory is PERMANENT and never cleared between sessions.

    Examples:
      remember_user("identity", "name", "Ayush")
      remember_user("preferences", "youtube", "AI news, tech reviews, coding tutorials")
      remember_user("preferences", "spotify", "lofi beats, hip-hop")
      remember_user("preferences", "image_ratio", "16:10 landscape for 16-inch laptop")
      remember_user("habits", "download_folder", "prefers saving everything to Downloads")
      remember_user("notes", "laptop_size", "uses 16-inch laptop, needs 16:10 wallpapers")

    Parameters:
      category : one of: identity, preferences, habits, trusted_actions, notes
      key      : the specific attribute (e.g. "name", "youtube", "spotify")
      value    : the value to store
      context  : optional — why this was noted (helps with future retrieval)
    """
    cat = category.lower().strip()
    if cat not in _VALID_CATEGORIES:
        cat = "notes"

    profile = _load_profile()

    if cat == "habits":
        # habits is a list — avoid duplicates by checking similarity
        habit_entry = f"{key}: {value}"
        existing = profile.get("habits", [])
        # Remove old entry for same key if exists
        existing = [h for h in existing if not h.lower().startswith(key.lower() + ":")]
        existing.append(habit_entry)
        profile["habits"] = existing
    elif cat == "notes":
        note_entry = {"date": datetime.now().strftime("%Y-%m-%d"), "key": key, "note": value}
        notes = profile.get("notes", [])
        # Remove old note for same key
        notes = [n for n in notes if n.get("key") != key]
        notes.append(note_entry)
        profile["notes"] = notes
    else:
        # identity, preferences, trusted_actions — dict with key→value
        if cat not in profile:
            profile[cat] = {}
        profile[cat][key] = value

    _save_profile(profile)
    return f"[SOUL] Remembered: {category}.{key} = \"{value}\""


@mcp.tool()
def recall_user(query: str = "") -> str:
    """
    USER SOUL: Retrieve relevant facts about the user as a compact summary.

    Call this at the START of a session (once) or when a request seems personal/habitual.
    Returns a compact bullet list (max 12 items) — never the raw JSON.

    Examples of when to call:
      - "play my usual videos" → recall_user("youtube preferences")
      - "download some wallpapers" → recall_user("image preferences laptop size")
      - "what do you know about me?" → recall_user() with no query (returns all)
      - Session start → recall_user("identity habits preferences")

    Parameters:
      query : optional topic to filter by (e.g. "music", "youtube", "downloads")
              leave empty to get a full identity+preferences summary

    Returns compact bullet list of relevant user facts.
    """
    profile = _load_profile()
    lines = ["[SOUL] User profile:\n"]
    has_data = False

    query_low = query.lower()

    def _matches(text: str) -> bool:
        if not query_low:
            return True
        return any(word in text.lower() for word in query_low.split())

    # Identity — always show if no specific query or query matches
    identity = profile.get("identity", {})
    if identity and (not query_low or _matches("identity name location device")):
        for k, v in identity.items():
            lines.append(f"  • {k}: {v}")
            has_data = True

    # Preferences — filter by query
    prefs = profile.get("preferences", {})
    for k, v in prefs.items():
        if _matches(k + " " + str(v)):
            lines.append(f"  • prefers {k}: {v}")
            has_data = True

    # Habits — filter by query
    habits = profile.get("habits", [])
    for h in habits:
        if _matches(h):
            lines.append(f"  • habit: {h}")
            has_data = True

    # Trusted actions — show if query matches or no query
    trusted = profile.get("trusted_actions", {})
    for k, v in trusted.items():
        if isinstance(v, dict) and v.get("trusted") and _matches(k):
            lines.append(f"  • trusted (no confirm needed): {k}")
            has_data = True

    # Notes — filter by query
    notes = profile.get("notes", [])
    for n in notes:
        note_text = n.get("note", "") + " " + n.get("key", "")
        if _matches(note_text):
            lines.append(f"  • note: {n.get('note', '')}")
            has_data = True

    if not has_data:
        if not query_low:
            return "[SOUL] No user profile data saved yet. Profile will grow as we interact."
        return f"[SOUL] No user facts found matching '{query}'. Profile will grow as we interact."

    # Cap at 12 bullets for token efficiency
    if len(lines) > 13:
        lines = lines[:13]
        lines.append("  • ... (use get_user_profile() to see all)")

    lines.append(f"\n  Last updated: {profile.get('last_updated', 'never')[:10]}")
    return "\n".join(lines)


@mcp.tool()
def get_user_profile() -> str:
    """
    USER SOUL: Return the complete user profile as formatted text.

    Use this ONLY when the user explicitly asks "what do you know about me?"
    or "show me my profile". DO NOT call this during normal tasks — use
    recall_user(query) instead for token efficiency.
    """
    profile = _load_profile()

    has_any = any([
        profile.get("identity"),
        profile.get("preferences"),
        profile.get("habits"),
        profile.get("trusted_actions"),
        profile.get("notes"),
    ])

    if not has_any:
        return (
            "[SOUL] No user profile data saved yet.\n"
            "Profile grows automatically as we interact — just talk naturally."
        )

    lines = ["[SOUL] Complete User Profile:\n"]

    identity = profile.get("identity", {})
    if identity:
        lines.append("=== Identity ===")
        for k, v in identity.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    prefs = profile.get("preferences", {})
    if prefs:
        lines.append("=== Preferences ===")
        for k, v in prefs.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    habits = profile.get("habits", [])
    if habits:
        lines.append("=== Habits ===")
        for h in habits:
            lines.append(f"  • {h}")
        lines.append("")

    trusted = profile.get("trusted_actions", {})
    if trusted:
        lines.append("=== Trusted Actions (no confirmation needed) ===")
        for k, v in trusted.items():
            if isinstance(v, dict):
                lines.append(f"  • {k}: {v.get('reason', 'user confirmed')}")
            else:
                lines.append(f"  • {k}")
        lines.append("")

    notes = profile.get("notes", [])
    if notes:
        lines.append("=== Notes ===")
        for n in notes[-10:]:  # show last 10
            lines.append(f"  [{n.get('date', '?')}] {n.get('note', '')}")
        lines.append("")

    lines.append(f"Last updated: {profile.get('last_updated', 'never')[:19]}")
    return "\n".join(lines)


@mcp.tool()
def forget_user(category: str, key: str) -> str:
    """
    USER SOUL: Remove a specific user preference or fact.

    Use when a stored preference is outdated or wrong.

    Parameters:
      category : identity, preferences, habits, trusted_actions, or notes
      key      : the specific attribute to remove
    """
    profile = _load_profile()
    cat = category.lower().strip()

    if cat == "habits":
        before = len(profile.get("habits", []))
        profile["habits"] = [h for h in profile.get("habits", [])
                             if not h.lower().startswith(key.lower() + ":")]
        after = len(profile.get("habits", []))
        removed = before - after
    elif cat == "notes":
        before = len(profile.get("notes", []))
        profile["notes"] = [n for n in profile.get("notes", []) if n.get("key") != key]
        after = len(profile.get("notes", []))
        removed = before - after
    elif cat in profile and isinstance(profile[cat], dict):
        if key in profile[cat]:
            del profile[cat][key]
            removed = 1
        else:
            removed = 0
    else:
        return f"[SOUL] Category '{category}' not found."

    if removed == 0:
        return f"[SOUL] No entry found for {category}.{key}."

    _save_profile(profile)
    return f"[SOUL] Removed {category}.{key} from user profile."


@mcp.tool()
def set_trusted_action(
    action_key: str,
    trusted: bool,
    reason: str = "",
) -> str:
    """
    USER SOUL: Mark an action as trusted (no longer needs confirmation).

    Call this AUTONOMOUSLY when user says things like:
    - "don't ask me again before emptying the bin"
    - "just do it without asking"
    - "I always confirm, just do it"
    - "stop asking for yes before deleting junk"

    Parameters:
      action_key : identifier for the action (e.g. "empty_recycle_bin",
                   "delete_junk_files", "silent_download", "run_installers")
      trusted    : True = skip confirmation. False = re-enable confirmation.
      reason     : why this was set (e.g. "user said don't ask again")

    IMPORTANT: Even with trusted=True, ALWAYS ask for confirmation before:
    - Deleting files the user has not categorized as junk
    - Running installers from unknown sources
    - Sending emails
    These cannot be trusted — only repetitive safe maintenance actions.
    """
    profile = _load_profile()
    if "trusted_actions" not in profile:
        profile["trusted_actions"] = {}

    profile["trusted_actions"][action_key] = {
        "trusted": trusted,
        "reason":  reason or ("user requested no confirmation" if trusted else "re-enabled confirmation"),
        "set_at":  datetime.now().isoformat(),
    }
    _save_profile(profile)

    verb = "trusted (confirmation skipped)" if trusted else "untrusted (confirmation restored)"
    return f"[SOUL] Action '{action_key}' marked as {verb}.\nReason: {reason}"


@mcp.tool()
def check_trusted(action_key: str) -> str:
    """
    USER SOUL: Check if an action has been marked as trusted (skip confirmation).

    Call this BEFORE showing a confirmation gate for routine actions.
    If this returns 'trusted', skip the confirmation and proceed directly.

    Parameters:
      action_key : identifier for the action (e.g. "empty_recycle_bin",
                   "delete_junk_files", "silent_download")

    Returns: "trusted" or "not_trusted"
    """
    profile = _load_profile()
    ta = profile.get("trusted_actions", {})
    entry = ta.get(action_key, {})

    if isinstance(entry, dict) and entry.get("trusted"):
        return (
            f"trusted\n"
            f"Action '{action_key}' is pre-approved by user.\n"
            f"Reason: {entry.get('reason', 'user confirmed')}\n"
            f"Set at: {entry.get('set_at', 'unknown')[:10]}\n"
            f"Proceed without asking for confirmation."
        )
    return f"not_trusted\nAction '{action_key}' requires normal confirmation. Show gate as usual."


if __name__ == "__main__":
    mcp.run()
