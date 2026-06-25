"""
MCP Server: VoxKage Cognitive Core (voxkage-cognitive-core)

The metacognitive brain that transforms any model into a self-correcting,
self-evolving agent. Model-agnostic by design — the methodology elevates
ANY model, regardless of size or capability.

Core cycle:
  start_turn → pre_mortem → EXECUTE → checkpoint → reflect → verify → refine → learn → DELIVER

Tools:
  start_turn(user_message)              — mandatory gate: classify intent, load profile
  pre_mortem(task_id, task_summary)      — predict risks before execution
  checkpoint(task_id, sub_task, status)  — mini-reflect on multi-step sub-tasks
  reflect(task_id, output_summary, checklist_results) — structured domain critique
  verify(task_id, verification_results)  — multi-layer verification tracker
  refine(task_id, issues_fixed, iteration) — refinement iteration tracker (max 3)
  learn(task_id, outcome, confidence_was, errors_found) — update global profile
  user_corrected(task_id, correction, error_category) — high-weight learning
  get_profile(domain)                    — view capability heatmap

Storage: ~/.voxkage/cognitive/
  profile.json         — single global evolving profile
  anti_patterns.json   — "never do this" database
  calibration.json     — confidence accuracy per domain
  session_state.json   — current session tracking (reset per session)
  task_history/recent.jsonl — last 100 task summaries

Checklists: shipped in voxkage/data/checklists/*.json (read-only)

Run standalone: python mcp_servers/cognitive_core_server.py
"""

import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PARENT = os.path.dirname(_ROOT)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage._env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-cognitive-core")

# ── Storage paths ─────────────────────────────────────────────────────────────
_COG_DIR = os.path.join(os.path.expanduser("~"), ".voxkage", "cognitive")
_PROFILE_FILE = os.path.join(_COG_DIR, "profile.json")
_ANTI_PATTERNS_FILE = os.path.join(_COG_DIR, "anti_patterns.json")
_CALIBRATION_FILE = os.path.join(_COG_DIR, "calibration.json")
_SESSION_FILE = os.path.join(_COG_DIR, "session_state.json")
_HISTORY_DIR = os.path.join(_COG_DIR, "task_history")
_HISTORY_FILE = os.path.join(_HISTORY_DIR, "recent.jsonl")
_DYNAMIC_RULES_FILE = os.path.join(_COG_DIR, "dynamic_rules.json")
_DYNAMIC_RULES_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "dynamic_rules_template.json")

# Checklists shipped with the package (read-only)
_CHECKLISTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "checklists")
_WRITABLE_CHECKLISTS_DIR = os.path.join(_COG_DIR, "checklists")

os.makedirs(_COG_DIR, exist_ok=True)
os.makedirs(_HISTORY_DIR, exist_ok=True)
os.makedirs(_WRITABLE_CHECKLISTS_DIR, exist_ok=True)

def _atomic_write_file(filepath: str, content: str):
    """Writes content to filepath.tmp, validates it, and replaces filepath."""
    if not content or not content.strip():
        raise ValueError("Content to write is empty, aborting write to prevent data corruption.")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise OSError("Tmp file is empty or does not exist.")
        os.replace(tmp_path, filepath)
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise e

_LOCK_FILE = os.path.join(_COG_DIR, "soul.lock")
import threading
_local_lock_depth = threading.local()

def _acquire_lock():
    """Acquires a lock on soul.lock. If older than 30 seconds, breaks it."""
    lock_path = _LOCK_FILE
    current_pid = os.getpid()
    current_thread = threading.get_ident()
    
    if not getattr(_local_lock_depth, "depth", 0):
        _local_lock_depth.depth = 0
        
    if _local_lock_depth.depth > 0:
        _local_lock_depth.depth += 1
        return True
        
    for _ in range(300):
        try:
            if os.path.exists(lock_path):
                try:
                    with open(lock_path, "r", encoding="utf-8") as f:
                        parts = f.read().strip().split(",")
                        lock_ts = float(parts[0])
                        lock_pid = int(parts[1]) if len(parts) > 1 else 0
                        lock_thread = int(parts[2]) if len(parts) > 2 else 0
                except Exception:
                    lock_ts = 0.0
                    lock_pid = 0
                    lock_thread = 0
                
                if lock_pid == current_pid and lock_thread == current_thread:
                    _local_lock_depth.depth += 1
                    return True
                
                if _time.time() - lock_ts > 30.0:
                    try:
                        os.remove(lock_path)
                    except Exception:
                        pass
                else:
                    _time.sleep(0.1)
                    continue
            
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write(f"{_time.time()},{current_pid},{current_thread}")
            _local_lock_depth.depth = 1
            return True
        except Exception:
            _time.sleep(0.1)
    return False

def _release_lock():
    if not hasattr(_local_lock_depth, "depth") or _local_lock_depth.depth <= 0:
        return
    _local_lock_depth.depth -= 1
    if _local_lock_depth.depth == 0:
        try:
            if os.path.exists(_LOCK_FILE):
                os.remove(_LOCK_FILE)
        except Exception:
            pass

def _auto_update_documentation(force=False):
    """
    Automatically updates CLAUDE.md, GEMINI.md.template, and AGENTS.md
    with the latest cognitive core protocol definitions using structured markers.
    """
    try:
        workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        claude_path = os.path.join(workspace_dir, "CLAUDE.md")
        gemini_template_path = os.path.join(workspace_dir, "voxkage", "templates", "GEMINI.md.template")
        workspace_agents_path = os.path.join(workspace_dir, ".agents", "AGENTS.md")
        global_agents_path = os.path.join(os.path.expanduser("~"), ".gemini", "config", "AGENTS.md")
        
        def update_doc_file(filepath):
            if not os.path.exists(filepath):
                return
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                
            start_marker = "<!-- COGNITIVE_PROTOCOL_START -->"
            end_marker = "<!-- COGNITIVE_PROTOCOL_END -->"
            
            if start_marker in content and end_marker in content:
                parts_before = content.split(start_marker)[0]
                parts_after = content.split(end_marker)[1]
                
                protocol_content = """
### If result type = "task"
The response includes: `task_id`, `domain`, `tier`, `checklist`, `warnings`, `profile_snapshot`.
Follow the metacognitive cycle based on the returned **tier**:

**Tier 1 (Quick Task) — e.g., "open Chrome", "what time is it", "show git status", "how do I cook eggs":**
- If `start_turn` returns `READ-ONLY task`: Execute directly, skip ALL cognitive tools. Zero overhead.
- Otherwise: Execute → quick mental check → `learn(task_id, "success")` → Deliver

**Tier 2 (Standard Task) — e.g., "write a Python script to sort files", "send this email":**
1. `pre_mortem(task_id, summary)` → Note risks
2. Execute the task with risks in mind
3. `reflect(task_id, output_summary, checklist_results)` → Get structured critique
   - **Use the EXACT IDs shown in the checklist from start_turn output**
   - Format: `"plan_1:pass, plan_2:pass, clarity:pass, accuracy:fail:reason"`
   - Shorthand `"plans:pass"` marks ALL plan_* items as passed at once
4. If REFINE recommended → fix issues → `refine(task_id, issues, iteration)`
5. `learn(task_id, outcome, confidence_was, errors_found)` → Deliver

**Tier 3 (Complex Task) — e.g., "build me a dashboard", "research AI agents thoroughly", "deploy this feature":**
1. `pre_mortem(task_id, summary)` → Note risks
2. Execute with `checkpoint(task_id, sub_task, status)` after each major sub-step
3. `reflect(task_id, output_summary, checklist_results)` → Get structured critique (use exact IDs)
4. Run external verification (lint/test/check) → `verify(task_id, results)`
5. If issues → `refine()` loop (max 3 iterations)
6. `learn(task_id, outcome, confidence_was, errors_found)` → Deliver

**Tier Classification (v3 — Risk-based, not length-based):**
- Pure observation verbs ("tell me", "show me", "what is", "check status", "how do I") → **Tier 1**, even for long messages
- State-change verbs ("build", "create", "write", "deploy", "delete", "commit") → **Tier 2 minimum**
- State-change + complexity signals ("comprehensive", "production", "from scratch") → **Tier 3**

### Follow-up Detection
If the user says "make it blue", "also add X", "change that" — `start_turn()` detects this as a follow-up and returns the SAME task_id from the previous task. Continue in context, don't restart from scratch.

### When the user corrects you:
→ Call `user_corrected(task_id, correction, error_category)` IMMEDIATELY. This is the highest-value learning signal (10x weight). VoxKage will permanently remember this mistake and flag it in future tasks.

### Cognitive Core Tools Reference

| Tool | When to call |
|---|---|
| `start_turn(user_message)` | **EVERY TURN — first action, no exceptions** |
| `pre_mortem(task_id, summary)` | Before executing Tier 2+ tasks |
| `checkpoint(task_id, sub_task, status)` | After each sub-step in Tier 3 tasks |
| `verify_code_file(filepath, domain)` | Tier 3: Run deep domain-aware static checks (syntax, imports, security) on edited file |
| `generate_critique(task_id, code_content, domain)` | Tier 2/3: Run code quality, complexity, and styling audit on new/modified code |
| `reflect(task_id, summary, checklist_results)` | After execution, with checklist and dynamic plan evaluation |
| `verify(task_id, results)` | After reflect, with external verification results |
| `refine(task_id, issues_fixed, iteration)` | After fixing issues (max 3 iterations) |
| `learn(task_id, outcome, confidence_was, errors_found)` | LAST cognitive call before delivering response |
| `log_tool_execution(tool_name, arguments)` | Auto-logged on every tool execution to construct the trace |
| `optimize_cognitive_core()` | Proactive self-optimization to deduplicate patterns and clean up rules |
| `user_corrected(task_id, correction)` | IMMEDIATELY when user corrects your output |
| `get_profile(domain)` | When user asks about VoxKage's capabilities or for diagnostics |
"""
                new_content = parts_before + start_marker + protocol_content.rstrip() + "\n" + end_marker + parts_after
                _atomic_write_file(filepath, new_content)
                
        if force:
            update_doc_file(claude_path)
            update_doc_file(gemini_template_path)
        
        agents_content = """# VoxKage Style & Protocol Rules

## CORE PROTOCOL - COGNITIVE CORE GATE
ALWAYS call start_turn(user_message) from voxkage-cognitive-core server as the ABSOLUTE FIRST tool call of every turn. This is non-negotiable.

### TIER protocols:
* **Tier 1 (Quick Task / Read-Only)**: Execute the task directly. Skip pre_mortem and reflect. Call `learn(task_id, outcome, confidence_was, errors_found)` after execution.
* **Tier 2 (Standard Task)**: Call `pre_mortem()`, execute, call `reflect()` with exact IDs, call `learn(task_id, outcome, confidence_was, errors_found)`.
* **Tier 3 (Complex Task)**: Call `pre_mortem()`, execute with `checkpoint()`, run verification/critique tools, call `reflect()` with exact IDs, call `verify()`, `learn(task_id, outcome, confidence_was, errors_found)`.

### Execution Trace Tracking:
When calling `learn(task_id, outcome, confidence_was, errors_found)`, a merged execution trace is reconstructed automatically from the session trace logs.

### Self-Optimization:
Call `optimize_cognitive_core()` to prune rules and anti-patterns if you notice recurrent classification errors or excessive overhead warnings.
"""
        
        _atomic_write_file(workspace_agents_path, agents_content)
        _atomic_write_file(global_agents_path, agents_content)
            
    except Exception:
        pass

# Run auto update of documentation on startup (non-forcing to prevent git dirty diff noise)
_auto_update_documentation(force=False)

def _save_checklist(domain: str, items: list):
    path = os.path.join(_WRITABLE_CHECKLISTS_DIR, f"{domain}.json")
    _save_json(path, {"domain": domain, "items": items})

# ── Turn-level guard ──────────────────────────────────────────────────────────
# Tracks when start_turn() was last called. Other cognitive tools check this
# and emit a soft warning if it was skipped.
import time as _time
_last_start_turn_ts: float = 0.0  # epoch seconds
_GUARD_WINDOWS = {1: 120, 2: 300, 3: 600}  # seconds per tier (Tier 3 = 10 min)

def _guard_check() -> str:
    """Returns a warning if start_turn() was not called recently, but does not block."""
    global _last_start_turn_ts
    if _last_start_turn_ts == 0.0:
        return "[COGNITIVE SYSTEM NOTE: start_turn() has not been called in this session yet. Please ensure start_turn() is called at the beginning of each turn.]\n\n"

    elapsed = _time.time() - _last_start_turn_ts
    try:
        session = _load_session()
        active_task = session.get("active_task")
        tier = (active_task or {}).get("tier", 3)
    except Exception:
        tier = 3

    if elapsed > _GUARD_WINDOWS.get(tier, 600):
        return f"[COGNITIVE SYSTEM NOTE: start_turn() was called {int(elapsed)}s ago, which is outside the active protocol window. Consider calling start_turn() to refresh context.]\n\n"
    return ""


# ═════════════════════════════════════════════════════════════════════════════
# STORAGE I/O
# ═════════════════════════════════════════════════════════════════════════════

def _default_profile() -> dict:
    return {
        "global_profile": {
            "domain_performance": {
                "frontend": {"tasks": 0, "successes": 0, "common_errors": []},
                "backend":  {"tasks": 0, "successes": 0, "common_errors": []},
                "research": {"tasks": 0, "successes": 0, "common_errors": []},
                "system":   {"tasks": 0, "successes": 0, "common_errors": []},
                "coding":   {"tasks": 0, "successes": 0, "common_errors": []},
                "analysis": {"tasks": 0, "successes": 0, "common_errors": []},
                "planning": {"tasks": 0, "successes": 0, "common_errors": []},
                "general":  {"tasks": 0, "successes": 0, "common_errors": []},
                "data":     {"tasks": 0, "successes": 0, "common_errors": []},
                "devops":   {"tasks": 0, "successes": 0, "common_errors": []},
            },
            "error_categories": {},
            "confidence_calibration": 0.5,
            "total_tasks_completed": 0,
            "total_user_corrections": 0,
        }
    }

def _load_json(path: str, default_factory) -> dict:
    _acquire_lock()
    try:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return default_factory()
    finally:
        _release_lock()

def _save_json(path: str, data: dict):
    _acquire_lock()
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        _atomic_write_file(path, content)
    finally:
        _release_lock()

def _load_profile() -> dict:
    data = _load_json(_PROFILE_FILE, _default_profile)
    if not isinstance(data, dict):
        data = _default_profile()
    if "global_profile" not in data or not isinstance(data["global_profile"], dict):
        data["global_profile"] = _default_profile()["global_profile"]
    return data

def _save_profile(profile: dict):
    _save_json(_PROFILE_FILE, profile)

def _load_anti_patterns() -> dict:
    data = _load_json(_ANTI_PATTERNS_FILE, lambda: {"anti_patterns": []})
    if not isinstance(data, dict):
        data = {"anti_patterns": []}
    if "anti_patterns" not in data or not isinstance(data["anti_patterns"], list):
        data["anti_patterns"] = []
    return data

def _save_anti_patterns(data: dict):
    _save_json(_ANTI_PATTERNS_FILE, data)

def _load_calibration() -> dict:
    data = _load_json(_CALIBRATION_FILE, lambda: {"domains": {}})
    if not isinstance(data, dict):
        data = {"domains": {}}
    if "domains" not in data or not isinstance(data["domains"], dict):
        data["domains"] = {}
    return data

def _save_calibration(data: dict):
    _save_json(_CALIBRATION_FILE, data)

def _load_session() -> dict:
    data = _load_json(_SESSION_FILE, lambda: {})
    if not isinstance(data, dict) or not data or not data.get("session_id"):
        data = {
            "session_id": "ses_" + str(uuid.uuid4())[:8],
            "session_start": datetime.now(timezone.utc).isoformat(),
            "active_task": None,
            "last_task": None,
            "current_session_failures": [],
            "elevated_checks": {},
            "tasks_this_session": [],
        }
        _save_json(_SESSION_FILE, data)
    if "elevated_checks" not in data or not isinstance(data["elevated_checks"], dict):
        data["elevated_checks"] = {}
    return data

def _save_session(data: dict):
    _save_json(_SESSION_FILE, data)

def _load_dynamic_rules() -> dict:
    if os.path.exists(_DYNAMIC_RULES_FILE):
        try:
            with open(_DYNAMIC_RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if os.path.exists(_DYNAMIC_RULES_TEMPLATE):
        try:
            import shutil
            shutil.copy2(_DYNAMIC_RULES_TEMPLATE, _DYNAMIC_RULES_FILE)
            with open(_DYNAMIC_RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "negation_rules": [
            {
                "verbs": ["commit", "push", "deploy", "delete", "remove", "change", "modify", "update", "write", "build", "install"],
                "negators": ["do not", "don't", "without", "no", "never", "avoid", "not", "should not", "shouldn't", "stop"],
                "window_size": 4
            }
        ],
        "force_tier_1_patterns": [
            "^\\s*git\\s+status\\b",
            "^\\s*git\\s+diff\\b",
            "^\\s*git\\s+log\\b",
            "^\\s*voxkage\\s+status\\b",
            "^\\s*voxkage\\s+plugins\\b",
            "\\b(show|tell|what)\\s+(is|are|the|uncommitted)?\\s*(changes|status|diff|log|version)\\b"
        ],
        "meta_rules": [
            "For read-only observation tasks, skip all cognitive tools except start_turn and learn.",
            "Do not call pre_mortem or reflect for Tier 1 tasks (read-only or quick).",
            "If a tool fails 2+ times, run search_memory or check documentation before retrying.",
            "Verify syntax locally (e.g. python -m py_compile or node -c) before saving file edits."
        ],
        "domain_keyword_adjustments": {},
        "tier_adjustments": {}
    }

def _save_dynamic_rules(rules: dict):
    _save_json(_DYNAMIC_RULES_FILE, rules)

def _load_checklist(domain: str, task_context: str = "") -> list:
    """Load a dynamic checklist for the given domain from writable storage, falling back to shipped template.

    v3: Universal fallback — if the loaded checklist is entirely code-specific noise for a
    non-code domain (creative, general, research), swap to general.json quality checklist.
    """
    _NON_CODE_DOMAINS = {"general", "creative", "research", "planning"}

    def _load_from_path(p: str) -> list:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("items", [])
        except Exception:
            return []

    def _resolve_path(d: str):
        p = os.path.join(_WRITABLE_CHECKLISTS_DIR, f"{d}.json")
        if not os.path.exists(p):
            shipped = os.path.join(_CHECKLISTS_DIR, f"{d}.json")
            if os.path.exists(shipped):
                try:
                    import shutil
                    shutil.copy2(shipped, p)
                except Exception:
                    p = shipped
            else:
                return None
        return p

    path = _resolve_path(domain)
    if path is None:
        path = _resolve_path("general")
        if path is None:
            return []

    items = _load_from_path(path)

    # Universal fallback: if non-code domain has a code-heavy checklist, use general.json
    if domain in _NON_CODE_DOMAINS and items:
        combined_text = " ".join(item.get("check", "") for item in items)
        code_keyword_hits = len(_CODE_CHECKLIST_KEYWORDS.findall(combined_text))
        if code_keyword_hits >= max(2, len(items) * 0.6):
            general_path = _resolve_path("general")
            if general_path and general_path != path:
                fallback_items = _load_from_path(general_path)
                if fallback_items:
                    return fallback_items

    return items



def _append_task_history(entry: dict):
    """Append a task summary to recent.jsonl, capping at 100 entries."""
    _acquire_lock()
    try:
        entries = []
        if os.path.exists(_HISTORY_FILE):
            try:
                with open(_HISTORY_FILE, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except OSError:
                pass
        entries.append(entry)
        if len(entries) > 100:
            entries = entries[-100:]
        lines = []
        for e in entries:
            lines.append(json.dumps(e, ensure_ascii=False))
        content = "\n".join(lines) + "\n"
        _atomic_write_file(_HISTORY_FILE, content)
    finally:
        _release_lock()


# ═════════════════════════════════════════════════════════════════════════════
# INTENT CLASSIFICATION (rule-based, NO LLM calls)
# ═════════════════════════════════════════════════════════════════════════════

# Conversation patterns (score -1 each)
_GREETING_PATTERNS = re.compile(
    r"\b(hey|hi|hello|howdy|yo|sup|good\s*(morning|evening|night|afternoon)|"
    r"what'?s?\s*up|how\s*(are|r)\s*(you|u)|how'?s?\s*it\s*going)\b", re.I
)
_FAREWELL_PATTERNS = re.compile(
    r"\b(bye|goodbye|good\s*night|see\s*(you|ya)|later|ciao|peace\s*out|"
    r"take\s*care|gn|night)\b", re.I
)
_THANKS_PATTERNS = re.compile(
    r"\b(thanks?|thank\s*you|thx|ty|appreciated|cheers)\b", re.I
)
_SOCIAL_PATTERNS = re.compile(
    r"\b(what\s*do\s*you\s*think|how\s*are\s*you|tell\s*me\s*about\s*yourself|"
    r"who\s*are\s*you|what'?s?\s*your\s*(name|opinion)|you'?re?\s*(cool|awesome|great|funny))\b", re.I
)

# Task patterns (score +1 each)
_ACTION_VERBS = re.compile(
    r"\b(build|create|make|fix|search|find|open|play|send|check|run|write|edit|"
    r"delete|download|install|deploy|analyze|debug|test|design|implement|update|"
    r"configure|move|copy|rename|show|get|fetch|read|list|set|change|modify|"
    r"add|remove|start|stop|restart|kill|close|browse|scrape|scan|compress|"
    r"extract|convert|generate|explain|research|compare|summarize|refactor|"
    r"optimize|sort|clean|format|index|commit|push|pull|merge|clone|log|"
    r"schedule|connect|disconnect|ping|monitor|backup|restore)\b", re.I
)
_CODE_KEYWORDS = re.compile(
    r"\b(function|class|method|api|endpoint|database|server|frontend|backend|"
    r"css|html|javascript|python|typescript|react|node|express|django|flask|"
    r"sql|mongodb|redis|docker|git|npm|pip|webpack|vite|component|module|"
    r"import|export|variable|constant|error|bug|exception|crash|test|lint|"
    r"type|interface|struct|enum|async|await|promise|callback|hook|state|"
    r"route|middleware|controller|model|view|template|schema|migration|"
    r"script|package|dependency|config|env|token|auth|login|signup|"
    r"password|session|cookie|jwt|oauth|websocket|rest|graphql|grpc)\b", re.I
)
_PATH_PATTERN = re.compile(r"[A-Za-z]:\\|/[a-z]+/|~/|\.\./|\./|\\\\")
_URL_PATTERN = re.compile(r"https?://|www\.", re.I)

# ── Risk-based tier signals (v3) ──────────────────────────────────────────────
# Pure observation verbs → read-only, cap tier at 1 regardless of message length
_READ_ONLY_VERBS = re.compile(
    r"\b(tell\s*me|show\s*me|what\s*is|what\s*are|what\s*('?s|was|were)|explain|describe|"
    r"how\s*(does|do|did)|how\s*(many|much|long|far|old)|summarize|check|status|list|"
    r"view|look\s*at|inspect|see|monitor|report|display|output|find|which|review|audit|"
    r"what\s*changed|diff|log|history|who\s*(is|are|was)|where\s*(is|are|was|can)|"
    r"when\s*(is|was|did|does)|translate|convert\s*(this|that)|read\s*(this|that|out)|show\s*me)\b",
    re.I
)
# State-change verbs → mutations, minimum Tier 2
_STATE_CHANGE_VERBS = re.compile(
    r"\b(build|create|make|write|deploy|install|push|commit|delete|modify|update|"
    r"configure|set\s*up|initialize|init|migrate|refactor|implement|add|remove|change|"
    r"move|rename|copy|edit|fix|patch|upgrade|rollback|reset|format|clean|compress|"
    r"extract|generate|send|download|backup|restore|kill|restart|shutdown|start|stop)\b",
    re.I
)

# Domain classification keywords
_DOMAIN_KEYWORDS = {
    "frontend": re.compile(
        r"\b(frontend|front-end|ui|ux|css|html|react|vue|angular|svelte|"
        r"responsive|layout|style|animation|component|page|button|form|"
        r"modal|navbar|sidebar|footer|header|card|grid|flex|tailwind|"
        r"bootstrap|design|color|font|theme|dark\s*mode|mobile|tablet|"
        r"web\s*app|website|landing\s*page|dashboard)\b", re.I
    ),
    "backend": re.compile(
        r"\b(backend|back-end|api|server|endpoint|database|sql|mongo|"
        r"redis|express|django|flask|fastapi|node|rest|graphql|grpc|"
        r"auth|authentication|middleware|route|controller|model|schema|"
        r"migration|orm|query|table|index|cache|session|jwt|oauth|"
        r"microservice|docker|deploy|nginx|kubernetes|lambda|serverless)\b", re.I
    ),
    "research": re.compile(
        r"\b(search|research|find\s*(out|info|about)|look\s*up|what\s*is|"
        r"who\s*is|when\s*(did|was|is)|where\s*(is|can)|how\s*(to|does|do)|"
        r"latest|news|weather|price|stock|compare|review|summarize|"
        r"explain|article|paper|blog|documentation|wiki|learn|guide|tutorial)\b", re.I
    ),
    "system": re.compile(
        r"\b(system|os|windows|process|kill|restart|shutdown|sleep|"
        r"battery|disk|memory|cpu|ram|wifi|bluetooth|network|volume|"
        r"brightness|wallpaper|screenshot|clipboard|notification|"
        r"install|uninstall|update|driver|registry|service|startup|"
        r"firewall|antivirus|recycle\s*bin|task\s*manager|terminal|"
        r"command|powershell|bash|shell|folder|directory|file|"
        r"git\s*status|git\s*log|git\s*diff|uncommitted|staged|branch)\b", re.I
    ),
    "coding": re.compile(
        r"\b(code|coding|program|script|function|class|module|package|"
        r"debug|refactor|optimize|test|lint|compile|build|fix\s*(the\s*)?bug|"
        r"implement|write\s*(a\s*)?(function|script|class|code|program)|"
        r"git|commit|branch|merge|pull\s*request|review|pr|repo|"
        r"algorithm|data\s*structure|pattern|architecture|dependency|"
        r"import|export|type|interface|generic|inheritance)\b", re.I
    ),
    "creative": re.compile(
        r"\b(cook|cooking|recipe|bake|baking|food|meal|dish|ingredient|prepare|how\s*to\s*make|"
        r"write\s*(a\s*)?(story|poem|essay|letter|email|blog|caption|joke|song|script|speech)|"
        r"creative|brainstorm|idea|suggest|recommend|design\s*idea|draw|paint|compose|"
        r"lyrics|fiction|narrative|character|plot|describe\s*(how|what)|craft|art)\b", re.I
    ),
    "analysis": re.compile(
        r"\b(analysis|analyze|investigate|evaluate|review|inspect|audit|"
        r"profile|diagnostic|metrics|log|logs|chart|plot|graph|report|trend)\b", re.I
    ),
    "planning": re.compile(
        r"\b(planning|plan|design|architecture|roadmap|todo|task\s*list|"
        r"strategy|milestone|phases|step-by-step|diagram|flowchart|outline)\b", re.I
    ),
    "general": re.compile(
        r"\b(general|chitchat|hello|help|info|status|version|plugins|about|"
        r"system\s*info|uptime|date|time|volume|brightness|media|spotify)\b", re.I
    ),
    "data": re.compile(
        r"\b(data|database|dataset|sql|json|csv|xml|parquet|dataframe|pandas|"
        r"numpy|ml|machine\s*learning|train|predict|model|numpy|scipy|tensor|"
        r"torch|sklearn|cleaning|preprocess|ingest|etl|analytics)\b", re.I
    ),
    "devops": re.compile(
        r"\b(devops|deploy|ci|cd|pipeline|github\s*action|docker|kubernetes|"
        r"aws|gcp|azure|terraform|ansible|secret|env|environment|nginx|"
        r"server\s*config|ssh|ssl|cert|build|compile|package|publish)\b", re.I
    ),
}

# Complexity signals for tiering
_COMPLEXITY_HIGH = re.compile(
        r"\b(make\s*sure|perfect|thorough|comprehensive|complete|detailed|"
        r"production|robust|scalable|enterprise|full\s*stack|from\s*scratch|"
        r"end\s*to\s*end|step\s*by\s*step|multiple|several|all|every|entire)\b", re.I
)
_MULTI_REQUIREMENT = re.compile(r"\b(and|also|plus|additionally|then|after\s*that|next)\b", re.I)

# Code-specific checklist noise keywords — if a checklist is full of these for a non-code task,
# we fall back to the general quality checklist
_CODE_CHECKLIST_KEYWORDS = re.compile(
    r"\b(sql|injection|database|query|parameterized|api|endpoint|http|cors|"
    r"migration|auth|jwt|hardcoded|secrets|lint|imports|typescript|node\b)\b", re.I
)


def _is_trivial_task(msg: str) -> bool:
    """
    Checks if a message represents a trivial command that should bypass heavy ceremony.
    """
    msg_lower = msg.lower().strip()

    # If it is a direct shell command for git or package manager, it is ALWAYS trivial
    direct_commands = [
        r"^git\s+(commit|push|add|checkout|status|diff|log|branch|pull)\b",
        r"^(npm|pip|yarn|pnpm|cargo|pipenv|poetry)\s+(install|add|update|remove|uninstall)\b",
        r"^git\s+commit\s+-m\b",
    ]
    if any(re.search(pat, msg_lower) for pat in direct_commands):
        return True

    # Explicit implementation verbs (must NEVER bypass ceremony if any of these are present)
    impl_verbs = [
        r"\bcreate\b", r"\bwrite\b", r"\bdelete\b", r"\bmodify\b", r"\bfix\b", 
        r"\brefactor\b", r"\bbuild\b", r"\bdeploy\b", r"\bdesign\b", r"\badd\b", 
        r"\bremove\b", r"\bchange\b", r"\bupdate\b", r"\bupgrade\b", r"\bimplement\b", 
        r"\bbump\b", r"\brename\b", r"\bchore\b", r"\bmake\b", r"\bgenerate\b", 
        r"\bsetup\b", r"\bpatch\b", r"\bedit\b"
    ]
    if any(re.search(pat, msg_lower) for pat in impl_verbs):
        return False

    # Positive list of general trivial keywords / phrases
    trivial_patterns = [
        r"\b(commit|push|add|checkout|status|diff|log|branch|pull|install|uninstall)\b",
    ]
    return any(re.search(pat, msg_lower) for pat in trivial_patterns)


def _log_cognitive_call(tool_name: str):
    """Logs a cognitive core tool execution in the session's cognitive_trace and refreshes guard timestamp."""
    global _last_start_turn_ts
    _last_start_turn_ts = _time.time()
    try:
        session = _load_session()
        cog_trace = session.setdefault("cognitive_trace", [])
        cog_trace.append({
            "tool": tool_name,
            "timestamp": _time.time()
        })
        session["cognitive_trace"] = cog_trace
        _save_session(session)
    except Exception:
        pass


def _classify_intent(msg: str) -> dict:
    """
    Rule-based intent classification. Returns type, domain, tier, etc.
    v3: Risk-based tier classification — observation verbs cap tier at 1,
    state-change verbs escalate to Tier 2+. Domain includes creative/lifestyle.
    Pure computation — no LLM calls, no disk writes. <1ms.
    """
    msg_stripped = msg.strip()
    if not msg_stripped:
        return {"type": "conversation"}

    # Load dynamic rules
    rules = _load_dynamic_rules()

    # ── Force Tier 1 matching ──
    for pat in rules.get("force_tier_1_patterns", []):
        try:
            if re.search(pat, msg_stripped, re.I):
                # Classify domain using the keywords (same as below)
                domain_scores = {}
                for d, pattern in _DOMAIN_KEYWORDS.items():
                    matches = len(pattern.findall(msg))
                    if matches > 0:
                        domain_scores[d] = matches
                domain = max(domain_scores, key=domain_scores.get) if domain_scores else "general"
                return {
                    "type": "task",
                    "domain": domain,
                    "tier": 1,
                    "is_read_only": True,
                }
        except Exception:
            pass

    # Score calculation
    conv_score = 0
    task_score = 0

    # Conversation signals
    if _GREETING_PATTERNS.search(msg):
        conv_score += 1
    if _FAREWELL_PATTERNS.search(msg):
        conv_score += 1
    if _THANKS_PATTERNS.search(msg):
        conv_score += 1
    if _SOCIAL_PATTERNS.search(msg):
        conv_score += 1
    # Very short messages with no action verbs lean conversational
    if len(msg_stripped) < 15 and not _ACTION_VERBS.search(msg):
        conv_score += 1

    # Task signals
    action_matches = len(_ACTION_VERBS.findall(msg))
    task_score += min(action_matches, 3)  # cap at 3 to avoid over-scoring
    if _CODE_KEYWORDS.search(msg):
        task_score += 1
    if _PATH_PATTERN.search(msg):
        task_score += 1
    if _URL_PATTERN.search(msg):
        task_score += 1
    # Questions asking for information/action
    if re.match(r"^(what|how|where|when|why|can\s*you|could\s*you|please|show|tell\s*me)\b", msg_stripped, re.I):
        task_score += 1

    # Task wins when both present (social wrapping rule)
    if task_score > 0 and conv_score > 0:
        task_score += 1  # bias toward task

    if task_score <= conv_score:
        return {"type": "conversation"}

    # ── Domain classification ──────────────────────────────────────────────
    domain_scores = {}
    for domain, pattern in _DOMAIN_KEYWORDS.items():
        matches = len(pattern.findall(msg))
        if matches > 0:
            domain_scores[domain] = matches

    if domain_scores:
        max_score = max(domain_scores.values())
        if max_score <= 1:
            domain = "general"
        else:
            domain = max(domain_scores, key=domain_scores.get)
    else:
        domain = "general"

    # ── v3 Tier classification — ACTION RISK, not message length ──────────
    # Step 1: detect read-only (observation) vs state-change (mutation) intent
    
    # Check for negations of state-change verbs using dynamic rules
    has_state_change = False
    state_change_matches = list(_STATE_CHANGE_VERBS.finditer(msg))
    
    if state_change_matches:
        negation_rules = rules.get("negation_rules", [])
        for match in state_change_matches:
            verb = match.group(0).lower()
            start_idx = match.start()
            
            # Find the prefix string before the verb
            prefix_text = msg[max(0, start_idx - 50):start_idx].lower()
            prefix_words = re.findall(r"\b[\w']+\b", prefix_text)
            
            # Default window size
            window_size = 4
            for rule in negation_rules:
                if verb in rule.get("verbs", []):
                    window_size = rule.get("window_size", 4)
                    break
            
            preceding_words = prefix_words[-window_size:] if prefix_words else []
            
            # Check if negated
            negated = False
            for rule in negation_rules:
                if verb in rule.get("verbs", []):
                    for negator in rule.get("negators", []):
                        neg_words = negator.split()
                        if len(neg_words) == 1:
                            if neg_words[0] in preceding_words:
                                negated = True
                                break
                        else:
                            for i in range(len(preceding_words) - len(neg_words) + 1):
                                if preceding_words[i:i+len(neg_words)] == neg_words:
                                    negated = True
                                    break
                    if negated:
                        break
            
            if not negated:
                has_state_change = True
                break
    else:
        has_state_change = False

    has_read_only = bool(_READ_ONLY_VERBS.search(msg))
    has_complexity = bool(_COMPLEXITY_HIGH.search(msg))
    multi_req_count = len(_MULTI_REQUIREMENT.findall(msg))
    msg_len = len(msg_stripped)
    is_trivial = _is_trivial_task(msg_stripped)

    # Pure read-only observation → Tier 1, even if message is long
    # (e.g. "checkout the codebase and tell me the latest uncommitted changes")
    if has_read_only and not has_state_change:
        tier = 1
    # Trivial task -> Tier 1
    elif is_trivial:
        tier = 1
    # State-change with high complexity signals → Tier 3
    elif has_state_change and (has_complexity or multi_req_count >= 3 or msg_len > 300):
        tier = 3
    # State-change with moderate complexity → Tier 2
    elif has_state_change or (has_complexity and not has_read_only):
        tier = 2
    # Fallback: use length-based heuristics for ambiguous cases
    elif msg_len > 200 or has_complexity:
        tier = 3
    elif msg_len > 80 or multi_req_count >= 2:
        tier = 2
    else:
        tier = 1

    return {
        "type": "task",
        "domain": domain,
        "tier": tier,
        "is_read_only": (has_read_only and not has_state_change) or is_trivial,
    }


def _detect_followup(msg: str, session: dict) -> bool:
    """Detect if this message is a follow-up to the previous task."""
    if not session.get("last_task"):
        return False
    # Pronouns referencing previous work
    followup_signals = re.compile(
        r"\b(it|this|that|those|these|the\s*(same|previous|last)|"
        r"also|too|as\s*well|instead|rather|but\s*(make|change|update)|"
        r"now\s*(make|change|add|remove|fix)|actually|wait)\b", re.I
    )
    if followup_signals.search(msg) and len(msg.strip()) < 100:
        return True
    return False


def _generate_task_plan(user_message: str, domain: str) -> list:
    """Extracts 3-5 specific requirements or actions from user_message to generate a dynamic plan."""
    # Split by newlines, semicolons, and periods (with space)
    raw_sentences = re.split(r'[\n;]|\.\s+', user_message)
    requirements = []
    
    # Simple clean up of bullet points and numbering
    bullet_cleanup = re.compile(r'^\s*([-*+•]|\d+\.?)\s*')
    
    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # Remove leading bullets/numbers
        sentence = bullet_cleanup.sub('', sentence).strip()
        if len(sentence) < 12:
            continue
            
        # Ignore purely conversational greetings or thanks
        if _GREETING_PATTERNS.search(sentence) or _THANKS_PATTERNS.search(sentence) or _FAREWELL_PATTERNS.search(sentence):
            continue
            
        # Check if the sentence has action words or domain keywords to represent a real requirement
        if _ACTION_VERBS.search(sentence) or _CODE_KEYWORDS.search(sentence) or len(sentence) > 30:
            requirements.append(sentence)
            
    # If list is empty, split by logical connectors like 'and also', 'as well as', 'then'
    if not requirements and len(user_message) > 30:
        parts = re.split(r'\b(?:and\s+also|then|additionally|plus)\b', user_message, flags=re.I)
        for part in parts:
            part = bullet_cleanup.sub('', part.strip()).strip()
            if len(part) >= 12:
                requirements.append(part)
                
    # Format as checklist items
    plan_items = []
    for i, req in enumerate(requirements[:5]):
        # Capitalize first letter, ensure ends with punctuation
        req_clean = req[0].upper() + req[1:]
        if not req_clean.endswith(('.', '?', '!')):
            req_clean += '.'
        plan_items.append({
            "id": f"plan_{i+1}",
            "check": f"Dynamic Plan: {req_clean}",
            "severity": "high"
        })
        
    if not plan_items:
        plan_items.append({
            "id": "plan_1",
            "check": "Dynamic Plan: Address all implicit and explicit instructions in user request.",
            "severity": "high"
        })
        
    return plan_items


def _score_anti_pattern(ap: dict, task_summary: str) -> float:
    """Scores a single anti-pattern against the task summary based on severity, recency, and keyword overlap."""
    score = 0.0
    # Base severity boost
    if ap.get("severity") == "user_caught":
        score += 3.0
    
    # Recency boost (newer = higher score, up to 1.5)
    ts_str = ap.get("timestamp")
    if ts_str:
        try:
            ap_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            days_old = (datetime.now(timezone.utc) - ap_dt).days
            score += max(0.0, 1.5 - (days_old / 30.0)) # scale down over 30 days
        except Exception:
            pass
            
    # Keyword overlap
    summary_words = set(re.findall(r'\b\w{4,}\b', task_summary.lower()))
    pattern_words = set(re.findall(r'\b\w{4,}\b', ap.get("pattern", "").lower()))
    overlap = len(summary_words.intersection(pattern_words))
    score += overlap * 2.0
    
    return score


_ARCHIVED_FILE = os.path.join(_COG_DIR, "anti_patterns_archived.json")

def _load_archived_anti_patterns() -> dict:
    data = _load_json(_ARCHIVED_FILE, lambda: {"anti_patterns": []})
    if not isinstance(data, dict):
        data = {"anti_patterns": []}
    if "anti_patterns" not in data or not isinstance(data["anti_patterns"], list):
        data["anti_patterns"] = []
    return data

def parse_timestamp(ts_str):
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None

def _normalize_pattern(msg: str) -> str:
    """Normalizes a query message to extract its core intent pattern.
    - Strips articles (a, an, the).
    - Normalizes whitespace.
    - If < 5 tokens, requires exact match (with word boundaries).
    - If >= 5 tokens, falls to wildcard at the end.
    """
    if not msg:
        return ""
    line = msg.strip().split("\n")[0].lower()
    line = re.sub(r'\b(a|an|the)\b', '', line)
    line = re.sub(r'\s+', ' ', line).strip()
    words = line.split()
    if not words:
        return ""
    if len(words) < 5:
        escaped_words = [re.escape(w) for w in words]
        pattern = r"^\s*" + r"\s+".join(escaped_words) + r"\s*$"
    else:
        core_words = words[:5]
        escaped_words = [re.escape(w) for w in core_words]
        pattern = r"^\s*" + r"\s+".join(escaped_words) + r".*"
    return pattern

def _inject_soul_history(filepath, soul_content):
    """Injects soul_content into the file at filepath.
    If markers exist, replaces content between them.
    If the header exists without markers, replaces that section.
    Otherwise, appends it to the end of the file."""
    if not os.path.exists(filepath):
        try:
            _atomic_write_file(filepath, soul_content)
        except Exception:
            pass
        return
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            
        start_marker = "<!-- SOUL_HISTORY_START -->"
        end_marker = "<!-- SOUL_HISTORY_END -->"
        new_section = f"{start_marker}\n{soul_content}\n{end_marker}"
        
        if start_marker in content and end_marker in content:
            pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
            content = re.sub(pattern, new_section, content, flags=re.DOTALL)
        elif "### VoxKage Consolidated Soul History & Performance" in content:
            lines = content.splitlines()
            start_idx = -1
            end_idx = -1
            for i, line in enumerate(lines):
                if "### VoxKage Consolidated Soul History & Performance" in line:
                    start_idx = i
                    break
            if start_idx != -1:
                for i in range(start_idx + 1, len(lines)):
                    if lines[i].strip() == "---" or (lines[i].startswith("#") and not lines[i].startswith("###")):
                        end_idx = i
                        break
                if end_idx == -1:
                    end_idx = len(lines)
                
                lines_before = lines[:start_idx]
                lines_after = lines[end_idx:]
                content = "\n".join(lines_before) + "\n" + new_section + "\n" + "\n".join(lines_after)
        else:
            if content.endswith("\n"):
                content += f"\n---\n\n{new_section}\n"
            else:
                content += f"\n\n---\n\n{new_section}\n"
                
        _atomic_write_file(filepath, content)
    except Exception:
        pass

def _analyze_execution_trace(task_id: str, domain: str, tier: int, tool_sequence: str) -> list:
    """
    Analyzes the sequence of tools executed during a task.
    Identifies execution-level anti-patterns:
      - tier_overkill: tier was 2 or 3, but no cognitive tools were used
      - cognitive_ceremony_overhead: cognitive tools called for Tier 1 or read-only task
      - stubborn_execution: the same tool called 3+ times consecutively with same arguments
      - missing_validation: Tier 2 or 3 succeeded, but reflect or verify was skipped
    Saves these to anti_patterns.json.
    """
    if not tool_sequence:
        return []

    if ";" in tool_sequence:
        tools = [t.strip() for t in tool_sequence.split(";") if t.strip()]
    else:
        tools = [t.strip() for t in tool_sequence.split(",") if t.strip()]
        
    if not tools:
        return []

    cog_tools = {"start_turn", "pre_mortem", "checkpoint", "reflect", "verify", "refine", "learn", "user_corrected"}
    
    clean_tools_with_args = []
    clean_tools = []
    for t in tools:
        match = re.match(r"^([a-zA-Z0-9_\-]+)(?:\((.*)\))?$", t)
        if match:
            t_name = match.group(1)
            t_args = match.group(2) or ""
        else:
            t_name = t
            t_args = ""
            
        if "cognitive-core" in t_name or "cognitive_core" in t_name:
            t_name = t_name.split("_")[-1].split(".")[-1]
        elif "_" in t_name:
            t_name = t_name.split("_")[-1]
            
        clean_tools_with_args.append((t_name, t_args))
        clean_tools.append(t_name)

    execution_fails = []

    # 1. tier_overkill
    has_cognitive = any(t in ["pre_mortem", "reflect", "verify", "refine", "checkpoint"] for t in clean_tools)
    non_cog_calls = [t for t in clean_tools if t not in cog_tools]
    
    if tier in [2, 3] and not has_cognitive and len(non_cog_calls) <= 2:
        execution_fails.append({
            "pattern": f"Wasted Tier {tier} overhead for simple task. Assigned Tier {tier} but only used basic tools: {', '.join(non_cog_calls)}",
            "type": "tier_overkill",
            "suggested_fix": "Use Tier 1 for simple lookup or observation tasks."
        })

    # 2. cognitive_ceremony_overhead
    if tier == 1 and any(t in ["pre_mortem", "reflect", "verify"] for t in clean_tools):
         execution_fails.append({
            "pattern": "Called heavy cognitive tools (pre_mortem/reflect) on a Tier 1 (Quick/Read-only) task.",
            "type": "cognitive_ceremony_overhead",
            "suggested_fix": "Skip pre_mortem and reflect for Tier 1 tasks to avoid wasted latency/tokens."
         })

    # 3. stubborn_execution
    consec_count = 1
    last_tool = None
    last_args = None
    for t_name, t_args in clean_tools_with_args:
        if t_name in cog_tools:
            continue
        if t_name == last_tool and t_args == last_args:
            consec_count += 1
            if consec_count >= 3:
                execution_fails.append({
                    "pattern": f"Stubborn consecutive calls to tool '{t_name}' with arguments '{t_args}' ({consec_count} times)." if t_args else f"Stubborn consecutive calls to tool '{t_name}' ({consec_count} times).",
                    "type": "stubborn_execution",
                    "suggested_fix": "If a tool fails twice, stop, check documentation or run search_memory instead of retrying blindly."
                })
                break
        else:
            consec_count = 1
            last_tool = t_name
            last_args = t_args

    # 4. missing_validation
    if tier in [2, 3] and "reflect" not in clean_tools:
         execution_fails.append({
            "pattern": f"Completed Tier {tier} task but skipped reflect() quality check.",
            "type": "missing_validation",
            "suggested_fix": "Always run reflect() before delivering a Tier 2+ output."
         })

    if not execution_fails:
        return []

    try:
        ap_data = _load_anti_patterns()
        aps = ap_data.setdefault("anti_patterns", [])
        
        updated = False
        for fail in execution_fails:
            exists = False
            for ap in aps:
                if ap.get("pattern") == fail["pattern"] or (ap.get("error_category") == fail["type"] and ap.get("domain") == "all"):
                    ap["times_prevented"] = ap.get("times_prevented", 0) + 1
                    ap["timestamp"] = datetime.now(timezone.utc).isoformat()
                    exists = True
                    updated = True
                    break
            if not exists:
                aps.append({
                    "pattern": fail["pattern"],
                    "domain": "all",
                    "severity": "medium",
                    "error_category": fail["type"],
                    "learned_from": task_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "times_prevented": 0,
                    "type": "execution",
                    "suggested_fix": fail["suggested_fix"]
                })
                updated = True
                
        if updated:
            ap_data["anti_patterns"] = ap_data["anti_patterns"][-75:]
            _save_anti_patterns(ap_data)
    except Exception:
        pass

    return execution_fails

    return execution_fails


# ═════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def start_turn(user_message: str, refresh_only: bool = False) -> str:
    """
    COGNITIVE CORE — MANDATORY FIRST CALL EVERY SINGLE TURN.

    This is the metacognitive gate. Call this BEFORE doing anything else,
    every single turn, no exceptions. It classifies the user's intent and
    returns either a lightweight "conversation" signal or a full task
    context with domain checklist, anti-pattern warnings, and profile data.

    Cost: ~10-15 tokens. Classification is rule-based (<1ms, no LLM call).

    Parameters:
      user_message : The user's raw message text for this turn.
      refresh_only : If True, only update the active protocol timestamp to keep the gate open.

    Returns:
      - If conversation: { type: "conversation" } — respond normally, skip all other cognitive tools.
      - If task: { type: "task", task_id, domain, tier, checklist, warnings, profile_snapshot }
        → Follow the metacognitive cycle based on the tier level.

    TIER GUIDE:
      Tier 1 (Quick): Execute → light checklist review → learn → deliver
      Tier 2 (Standard): pre_mortem → execute → reflect → verify → learn → deliver
      Tier 3 (Complex): pre_mortem → execute with checkpoints → reflect → verify → refine loop → learn → deliver

    FOLLOW-UP DETECTION:
      If this is a follow-up to a recent task ("make it blue", "also add X"),
      returns the previous task_id so you continue the same context.
    """
    global _last_start_turn_ts
    _last_start_turn_ts = _time.time()  # Mark gate as fired

    session = _load_session()

    if refresh_only:
        session["last_start_turn_ts"] = _time.time()
        _save_session(session)
        return "[COGNITIVE] Protocol window refreshed."

    # Reset traces for a new turn
    session["cognitive_trace"] = [{"tool": "start_turn", "timestamp": _time.time()}]
    session["tool_trace"] = []
    _save_session(session)

    classification = _classify_intent(user_message)

    if classification["type"] == "conversation":
        session["last_start_turn_ts"] = _time.time()
        _save_session(session)
        return "[COGNITIVE] type: conversation\nRespond normally. No cognitive tools needed."

    # ── Task detected ──────────────────────────────────────────────
    domain = classification["domain"]
    tier = classification["tier"]
    is_read_only = classification.get("is_read_only", False)

    # Follow-up detection
    is_followup = _detect_followup(user_message, session)
    if is_followup and session.get("last_task"):
        task_id = session["last_task"]["task_id"]
        domain = session["last_task"].get("domain", domain)
    else:
        task_id = "task_" + str(uuid.uuid4())[:8]

    # Load profile data
    profile = _load_profile()
    gp = profile.get("global_profile", {})
    domain_perf = gp.get("domain_performance", {}).get(domain, {})
    tasks_done = domain_perf.get("tasks", 0)
    successes = domain_perf.get("successes", 0)
    success_rate = round(successes / max(tasks_done, 1), 2)
    common_errors = domain_perf.get("common_errors", [])[:5]

    # Load anti-patterns for this domain, scored by relevance to user_message
    ap_data = _load_anti_patterns()
    all_aps = [
        ap for ap in ap_data.get("anti_patterns", [])
        if ap.get("domain") in (domain, "all")
    ]
    # Score and sort by relevance
    for ap in all_aps:
        ap["_relevance_score"] = _score_anti_pattern(ap, user_message)

    # Deduplicate anti-patterns by pattern text, preserving order
    seen_patterns = set()
    deduped_aps = []
    for ap in sorted(all_aps, key=lambda x: x["_relevance_score"], reverse=True):
        pattern_str = ap.get("pattern", "").strip()
        if pattern_str and pattern_str not in seen_patterns:
            seen_patterns.add(pattern_str)
            deduped_aps.append(ap)
    domain_anti_patterns = deduped_aps[:5]

    # Load baseline checklist
    checklist = _load_checklist(domain)

    # Generate task-specific plan items and prepend them to the checklist
    plan_items = _generate_task_plan(user_message, domain)
    checklist = plan_items + checklist

    # Session elevated checks (from recent failures in this domain)
    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, list):
        # Migrate old flat list to dict isolated by current domain
        session["elevated_checks"] = {domain: raw_elevated}
        _save_session(session)
        elevated = raw_elevated
    else:
        elevated = raw_elevated.get(domain, [])

    # Adjust tier based on profile confidence and safety triggers
    if success_rate < 0.70 and tasks_done >= 3:
        tier = 3  # Force complex tier for low success rate domains
    elif success_rate < 0.85 and tasks_done >= 3:
        tier = max(tier, 2)
    if len(common_errors) >= 3:
        tier = max(tier, 2)
    if len(elevated) > 0:
        tier = 3  # Force complex tier if there are active session failures

    # v3: Never force read-only observation tasks above Tier 1 due to profile triggers alone.
    # They have no side effects, so elevated protocol is always overhead.
    if is_read_only:
        tier = 1
        
    # Calibration accuracy trigger: escalate tier by 1 level if accuracy < 0.60 (only for mutating tasks)
    cal_acc = gp.get("confidence_calibration", 0.5)
    if cal_acc < 0.60 and not is_read_only:
        tier = min(3, tier + 1)

    # Build task context
    task_context = {
        "task_id": task_id,
        "domain": domain,
        "tier": tier,
        "is_followup": is_followup,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_message": user_message,
    }

    # Update session state
    session["active_task"] = task_context
    session["active_plan"] = plan_items
    session["tasks_this_session"].append(task_id)
    session["last_start_turn_ts"] = _time.time()
    _save_session(session)

    # Build response
    lines = [
        f"[COGNITIVE] type: task",
        f"  task_id: {task_id}",
        f"  domain: {domain}",
        f"  tier: {tier} ({'Quick' if tier == 1 else 'Standard' if tier == 2 else 'Complex'})",
        f"  followup: {is_followup}",
    ]

    # Profile snapshot
    lines.append(f"\n  Profile — {domain}:")
    lines.append(f"    success_rate: {success_rate} ({successes}/{tasks_done} tasks)")
    lines.append(f"    confidence_calibration: {cal_acc}")
    if common_errors:
        lines.append(f"    known_weak_areas: {', '.join(common_errors[:3])}")

    # Anti-pattern warnings
    if domain_anti_patterns:
        lines.append(f"\n  ⚠ Anti-Pattern Warnings:")
        for ap in domain_anti_patterns[:3]:
            relevance_note = f" (relevance: {round(ap['_relevance_score'], 1)})" if ap.get("_relevance_score", 0) > 0 else ""
            lines.append(f"    • {ap.get('pattern', '?')}{relevance_note}")

    # Session elevated checks
    if elevated:
        lines.append(f"\n  🔺 Elevated (failed recently this session):")
        for e in elevated[:3]:
            lines.append(f"    • {e}")

    # Checklist — SHOW REAL IDs so reflect() receives correct key strings
    if checklist:
        lines.append(f"\n  Checklist ({domain}, {len(checklist)} items):")
        lines.append(f"  ┌ IMPORTANT: Use these exact IDs when calling reflect(). e.g. \"plan_1:pass, clarity:pass\"")
        for item in checklist:
            priority = " ⚡" if item["id"] in elevated else ""
            is_plan = item["id"].startswith("plan_")
            tag = "PLAN" if is_plan else item['severity'].upper()
            lines.append(f"    \u25a1 [{tag}]{priority} {item['id']}: {item['check']}")

    # Tier instructions
    lines.append(f"\n  Protocol (Tier {tier}):")
    if tier == 1:
        if is_read_only:
            lines.append("    → READ-ONLY task: No pre_mortem, reflect, or verify needed. Zero overhead.")
            lines.append("    1. Execute the task directly (observation only, no side effects)")
            lines.append("    2. Call learn(task_id, outcome, confidence_was) — optional but encouraged")
            lines.append("    3. Deliver immediately")
        else:
            lines.append("    1. Execute the task")
            lines.append("    2. Quick mental check against the checklist above")
            lines.append("    3. Call learn(task_id, outcome, confidence_was, errors_found) → Deliver")
    elif tier == 2:
        lines.append("    1. Predict risks and load failure memories: Call pre_mortem(task_id, summary)")
        lines.append("    2. Execute the task carefully (Optional: call generate_critique if code is written)")
        lines.append("    3. Evaluate output using exact IDs from checklist above: Call reflect(task_id, summary, results)")
        lines.append("       └ Format: \"plan_1:pass, plan_2:pass, clarity:pass, accuracy:fail:reason\"")
        lines.append("    4. If reflect recommends REFINE, fix issues and call refine(task_id, issues, iteration)")
        lines.append("    5. Record outcome and update capability weights: Call learn(task_id, outcome, confidence_was, errors_found) → Deliver")
    else:
        lines.append("    1. Predict risks and load failure memories: Call pre_mortem(task_id, summary)")
        lines.append("    2. Execute task step-by-step, calling checkpoint(task_id, sub_task, status) after each major step")
        lines.append("    3. Run file verification checks: Call verify_code_file(filepath)")
        lines.append("    4. Run critique audit on code: Call generate_critique(task_id, code_content)")
        lines.append("    5. Evaluate output using exact IDs from checklist above: Call reflect(task_id, summary, results)")
        lines.append("       └ Format: \"plan_1:pass, plan_2:pass, syntax_ok:pass, error_handling:fail:no try/catch\"")
        lines.append("    6. Run external checks (lint, test, build, run): Call verify(task_id, results)")
        lines.append("    7. If issues found in reflect/verify, call refine(task_id, issues, iteration) and repeat up to 3 times")
        lines.append("    8. Record outcome and update capability weights: Call learn(task_id, outcome, confidence_was, errors_found) → Deliver")


    # Universal Metacognitive Axioms
    try:
        rules = _load_dynamic_rules()
        meta_rules = rules.get("meta_rules", [])
        if meta_rules:
            lines.append("\n  🧠 Universal Metacognitive Axioms:")
            for rule in meta_rules:
                lines.append(f"    • {rule}")
    except Exception:
        pass

    lines.append("\n  🧠 Metacognitive Directives:")
    lines.append("    • SELF-CORRECTION: If you make a mistake, acknowledge it, analyze the root cause, and call user_corrected() immediately.")
    lines.append("    • SELF-EVOLUTION: Update your behavior based on the known weak areas and anti-pattern warnings. Proactively avoid repeating past failures.")
    lines.append("    • CRITICAL THINKING: Do not just tick checklists blindly. Perform honest self-critique. If a checklist item fails, fail it and refine.")

    return "\n".join(lines)


@mcp.tool()
def pre_mortem(task_id: str, task_summary: str) -> str:
    """
    COGNITIVE CORE — Predict risks BEFORE execution.

    Call this after start_turn() returns a Tier 2 or Tier 3 task.
    Loads anti-patterns, common errors, and session failures to predict
    what could go wrong with THIS specific task.

    Parameters:
      task_id      : The task_id from start_turn()
      task_summary : Brief description of what you're about to do

    Returns predicted risks to keep in mind during execution.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("pre_mortem")
    session = _load_session()
    task = session.get("active_task") or {}
    domain = task.get("domain", "coding")

    profile = _load_profile()
    gp = profile.get("global_profile", {})
    domain_perf = gp.get("domain_performance", {}).get(domain, {})
    common_errors = domain_perf.get("common_errors", [])

    ap_data = _load_anti_patterns()
    all_aps = [
        ap for ap in ap_data.get("anti_patterns", [])
        if ap.get("domain") in (domain, "all")
    ]
    # Score and rank by relevance to task_summary
    for ap in all_aps:
        ap["_relevance_score"] = _score_anti_pattern(ap, task_summary)

    # Deduplicate anti-patterns by pattern text, preserving order
    seen_patterns = set()
    deduped_aps = []
    for ap in sorted(all_aps, key=lambda x: x["_relevance_score"], reverse=True):
        pattern_str = ap.get("pattern", "").strip()
        if pattern_str and pattern_str not in seen_patterns:
            seen_patterns.add(pattern_str)
            deduped_aps.append(ap)
    relevant_aps = deduped_aps[:5]

    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, list):
        elevated = raw_elevated
    else:
        elevated = raw_elevated.get(domain, [])

    lines = [f"[COGNITIVE PRE-MORTEM] task: {task_id}", f"  Summary: {task_summary}", ""]

    risk_count = 0

    if relevant_aps:
        lines.append("  🚫 Anti-Pattern Risks (you have done these before):")
        for ap in relevant_aps[:5]:
            risk_count += 1
            lines.append(f"    Risk {risk_count}: {ap.get('pattern', '?')}")
            if ap.get("severity") == "user_caught":
                lines.append(f"      → USER CORRECTED YOU on this. Do NOT repeat.")

    if common_errors:
        lines.append(f"\n  ⚠ Common errors in {domain} tasks:")
        for err in common_errors[:5]:
            risk_count += 1
            lines.append(f"    Risk {risk_count}: {err}")

    if elevated:
        lines.append(f"\n  🔺 Session-elevated (failed earlier this session):")
        for e in elevated[:3]:
            risk_count += 1
            lines.append(f"    Risk {risk_count}: {e} — DOUBLE-CHECK THIS")

    if risk_count == 0:
        lines.append("  ✅ No known risks for this task type. Proceed with standard caution.")
    else:
        lines.append(f"\n  Total risks identified: {risk_count}")
        lines.append("  Keep these in mind during execution. reflect() will check against them.")

    return guard_warning + "\n".join(lines)


@mcp.tool()
def checkpoint(task_id: str, sub_task: str, status: str, issues: str = "") -> str:
    """
    COGNITIVE CORE — Mini-reflect on sub-steps of multi-step tasks.

    Call after completing each major sub-step in a Tier 3 (Complex) task.
    Records progress and escalates checks if issues are found.

    Parameters:
      task_id  : The task_id from start_turn()
      sub_task : Name of the sub-step just completed (e.g., "API endpoints", "CSS styling")
      status   : "done" or "issues"
      issues   : If status is "issues", describe what went wrong

    Returns acknowledgment with progress update.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("checkpoint")
    session = _load_session()
    task = session.get("active_task") or {}
    domain = task.get("domain", "coding")

    # Per-task sub-task tracking — isolated per task_id, not session-global
    # Migrate old flat-list format to per-task dict if needed
    raw = session.get("sub_tasks")
    if isinstance(raw, list):
        # Old format: wrap all existing sub-tasks under a legacy key
        session["sub_tasks"] = {"__legacy__": raw}
    elif raw is None:
        session["sub_tasks"] = {}
    if task_id not in session["sub_tasks"]:
        session["sub_tasks"][task_id] = []
    session["sub_tasks"][task_id].append({
        "name": sub_task,
        "status": status,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if status == "issues" and issues:
        # Add to session failures for elevated checking
        if issues not in session.get("current_session_failures", []):
            session["current_session_failures"].append(issues)
        # Extract keywords from issues for domain-isolated elevated checks
        raw_elevated = session.get("elevated_checks", {})
        if isinstance(raw_elevated, list):
            session["elevated_checks"] = {domain: raw_elevated}
            raw_elevated = session["elevated_checks"]
        elif raw_elevated is None:
            session["elevated_checks"] = {}
            raw_elevated = session["elevated_checks"]
        
        domain_elevated = raw_elevated.setdefault(domain, [])
        words = re.findall(r'\b\w{4,}\b', issues.lower())
        for w in words[:3]:
            if w not in domain_elevated:
                domain_elevated.append(w)

    _save_session(session)

    current_sub_tasks = session["sub_tasks"].get(task_id, [])
    completed = sum(1 for s in current_sub_tasks if s["status"] == "done")
    total = len(current_sub_tasks)
    with_issues = sum(1 for s in current_sub_tasks if s["status"] == "issues")

    lines = [
        f"[COGNITIVE CHECKPOINT] {sub_task}: {status.upper()}",
        f"  Progress: {completed} done, {with_issues} with issues, {total} total sub-steps",
    ]
    if issues:
        lines.append(f"  Issues: {issues}")
        lines.append(f"  → Elevated checks updated. reflect() will prioritize these areas.")

    return guard_warning + "\n".join(lines)


@mcp.tool()
def reflect(task_id: str, output_summary: str, checklist_results: str) -> str:
    """
    COGNITIVE CORE — Structured domain-specific critique.

    Call after execution, passing your self-evaluation against the checklist
    that start_turn() provided. Format each item as id:pass or id:fail:reason.
    Evaluates both baseline checklist and dynamic plan items with weighted scores.

    Parameters:
      task_id           : The task_id from start_turn()
      output_summary    : Brief summary of what you produced
      checklist_results : Comma-separated results, e.g.:
                          "responsive:pass, accessible:fail:no ARIA labels, error_states:pass"

    Returns structured critique with DELIVER or REFINE recommendation.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("reflect")
    session = _load_session()
    task = session.get("active_task") or {}
    domain = task.get("domain", "coding")
    
    # Load domain-isolated elevated checks
    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, list):
        elevated = raw_elevated
    else:
        elevated = raw_elevated.get(domain, [])

    # Load baseline checklist and active plan
    checklist = _load_checklist(domain)
    active_plan = session.get("active_plan") or []
    
    # Map all valid IDs to their definitions
    checklist_map = {item["id"]: item for item in checklist}
    plan_map = {item["id"]: item for item in active_plan}
    
    combined_items_map = {}
    combined_items_map.update(checklist_map)
    combined_items_map.update(plan_map)
    all_valid_ids = set(combined_items_map.keys())

    # Parse checklist results — v3: fuzzy/prefix/wildcard matching
    passes = []
    fails = []

    # Get all plan IDs for wildcard expansion
    all_plan_ids = [vid for vid in all_valid_ids if vid.startswith("plan_")]

    # Split on both commas and newlines
    raw_items = re.split(r'[,\n]', checklist_results)
    for item in raw_items:
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        item_id = parts[0].strip()

        # Clean item_id to match snake_case checklist identifiers
        item_id_clean = re.sub(r'[^a-zA-Z0-9_\-]', '', item_id).strip()
        if not item_id_clean:
            continue

        status = parts[1].strip().lower() if len(parts) > 1 else "pass"
        reason = parts[2].strip() if len(parts) > 2 else ""

        # ── v3 Fuzzy/Prefix/Wildcard Matching ──────────────────────────────
        # Case 1: Exact match (already works)
        if item_id_clean in all_valid_ids:
            matched_ids = [item_id_clean]

        # Case 2: Wildcard shorthands for all plan items
        # "plan:pass", "plans:pass", "all_plans:pass", "plan_items:pass" etc.
        elif re.match(r'^(all_?)?plans?(_items?)?$', item_id_clean, re.I):
            matched_ids = all_plan_ids  # Expand to all plan_* IDs

        # Case 3: Prefix match — "plan" matches plan_1, plan_2 etc.
        # Also handles "high", "medium", "low" severity group matching
        elif all_valid_ids:
            prefix_matches = [vid for vid in all_valid_ids if vid.startswith(item_id_clean + "_") or vid.startswith(item_id_clean)]
            matched_ids = prefix_matches if prefix_matches else []

        else:
            matched_ids = []

        if matched_ids:
            for mid in matched_ids:
                if status == "pass":
                    if mid not in passes:
                        passes.append(mid)
                else:
                    if not any(f["id"] == mid for f in fails):
                        fails.append({"id": mid, "reason": reason})
        # Silently skip truly unrecognized IDs (don't fail on them, just ignore)

    # Automatically treat unaddressed items as failed ONLY if the model actually
    # provided SOME results (non-empty checklist_results). If checklist_results is
    # empty or trivial, grant a good-faith pass on plan items.
    evaluated_ids = set(passes).union({f["id"] for f in fails})
    is_trivial_input = len(checklist_results.strip()) < 5

    unaddressed_ids = all_valid_ids - evaluated_ids
    for item_id in unaddressed_ids:
        if is_trivial_input:
            # Good-faith: if model gave no checklist at all, mark plan items as pending
            # but don't auto-fail hard checklist items — use soft "unaddressed" severity
            fails.append({"id": item_id, "reason": "Not reported — good-faith partial evaluation"})
        else:
            fails.append({"id": item_id, "reason": "Not reported/addressed in self-reflection"})

    # Calculate weighted score (High = 3, Medium = 2, Low = 1)
    achieved_score = 0
    total_possible_score = 0
    severity_weights = {"high": 3, "medium": 2, "low": 1}
    
    for pass_id in passes:
        item = combined_items_map.get(pass_id)
        weight = severity_weights.get(item.get("severity", "low") if item else "low", 1)
        achieved_score += weight
        total_possible_score += weight
        
    for fail_item in fails:
        item = combined_items_map.get(fail_item["id"])
        weight = severity_weights.get(item.get("severity", "low") if item else "low", 1)
        total_possible_score += weight
        
    quality_score = round(achieved_score / max(total_possible_score, 1), 2)

    # v3: Score floor — 0.0 is only valid for truly empty output.
    # If the model produced substantive output, the minimum score is 0.1.
    # This prevents catastrophic false negatives from propagating into calibration.
    if quality_score == 0.0 and len(output_summary.strip()) > 30:
        quality_score = 0.1

    quality_percent = round(quality_score * 100)

    # Determine recommendation
    # DELIVER if quality_score >= 0.85 and no high severity items failed
    high_severity_failed = False
    for fail_item in fails:
        # Only treat "high" severity fails from ACTUAL evaluations as blockers,
        # not items that were silently unaddressed (naming mismatch protection)
        if "Not reported" in fail_item.get("reason", "") and not any(p in fail_item["id"] for p in ["plan_"]):
            # Unaddressed standard items count as high severity failures
            item = combined_items_map.get(fail_item["id"])
            severity = item.get("severity", "low") if item else "low"
            if severity == "high":
                high_severity_failed = True
                break
        elif "Not reported" not in fail_item.get("reason", ""):
            # Explicitly failed items always count
            item = combined_items_map.get(fail_item["id"])
            severity = item.get("severity", "low") if item else "low"
            if severity == "high":
                high_severity_failed = True
                break
            
    recommend_deliver = (quality_score >= 0.85) and not high_severity_failed

    lines = [
        f"[COGNITIVE REFLECT] task: {task_id}",
        f"  Domain: {domain}",
        f"  Output: {output_summary[:150]}",
        f"  Checklist Quality Score: {quality_score}/1.0 ({quality_percent}%)",
        "",
    ]

    # Group passes/fails by type (Dynamic Plan vs Standard Checklist)
    plan_passes = [p for p in passes if p.startswith("plan_")]
    chk_passes = [p for p in passes if not p.startswith("plan_")]
    
    plan_fails = [f for f in fails if f["id"].startswith("plan_")]
    chk_fails = [f for f in fails if not f["id"].startswith("plan_")]

    if plan_passes or chk_passes:
        lines.append("  ✅ PASSED:")
        if plan_passes:
            lines.append(f"    • Plan Requirements: {', '.join(plan_passes)}")
        if chk_passes:
            lines.append(f"    • Standard Checklist: {', '.join(chk_passes)}")

    if plan_fails or chk_fails:
        lines.append("\n  ❌ FAILED / UNADDRESSED:")
        if plan_fails:
            lines.append("    • Plan Requirements:")
            for f_item in plan_fails:
                item = combined_items_map.get(f_item["id"])
                check_text = item["check"] if item else f_item["id"]
                lines.append(f"      - {f_item['id']}: {check_text} → {f_item['reason']}")
        if chk_fails:
            lines.append("    • Standard Checklist:")
            for f_item in chk_fails:
                item = combined_items_map.get(f_item["id"])
                check_text = item["check"] if item else f_item["id"]
                elevated_mark = " 🔺" if f_item["id"] in elevated else ""
                lines.append(f"      - {f_item['id']}{elevated_mark}: {check_text} → {f_item['reason']}")

    # Check if any elevated items were missed
    elevated_missed = [e for e in elevated if e not in passes and not any(f["id"] == e for f in fails)]
    if elevated_missed:
        lines.append(f"\n  🔺 Elevated checks NOT addressed: {', '.join(elevated_missed)}")

    # Recommendation
    if recommend_deliver:
        lines.append(f"\n  → Recommendation: DELIVER ✅")
    else:
        reason_msg = "high-severity failure(s) present" if high_severity_failed else f"score {quality_percent}% is below 85% threshold"
        lines.append(f"\n  → Recommendation: REFINE ❌ ({reason_msg})")
        
    lines.append(f"  → Score: {quality_score} (threshold: 0.85)")

    # Store reflection data in session for learn()
    session["last_reflection"] = {
        "passes": passes,
        "fails": [f["id"] for f in fails],
        "fail_details": fails,
        "confidence": quality_score,
        "pass_rate": quality_score,
    }
    _save_session(session)

    return guard_warning + "\n".join(lines)


@mcp.tool()
def verify(task_id: str, verification_results: str) -> str:
    """
    COGNITIVE CORE — Multi-layered verification results tracker.

    Call after reflect() for Tier 2+ tasks. Report the results of external
    checks you ran (lint, tests, type-check, manual review, etc).

    Parameters:
      task_id              : The task_id from start_turn()
      verification_results : Description of what you verified and results, e.g.:
                             "lint:pass, tests:3/3 passed, type-check:2 errors in auth.py"

    Returns PASS (deliver) or FAIL (specific issues to refine).
    """
    guard_warning = _guard_check()
    _log_cognitive_call("verify")
    session = _load_session()

    # Parse verification results
    all_pass = True
    issues = []
    items = []
    
    # Split on both commas and newlines
    raw_items = re.split(r'[,\n]', verification_results)
    for item in raw_items:
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 1)
        name = parts[0].strip()
        
        if len(parts) > 1:
            result = parts[1].strip()
            result_lower = result.lower()
            is_pass = any(w in result_lower for w in ["pass", "success", "ok", "true", "yes"])
            is_fail = any(w in result_lower for w in ["fail", "error", "invalid", "false", "no"])
            
            # Determine check mark
            if is_fail:
                mark = "❌"
                all_pass = False
                issues.append(f"{name}: {result}")
            elif is_pass:
                mark = "✅"
            else:
                mark = "ℹ️"
        else:
            result = ""
            mark = "ℹ️"
                
        items.append({"name": name, "result": result, "mark": mark})

    lines = [f"[COGNITIVE VERIFY] task: {task_id}", ""]

    for v in items:
        lines.append(f"  {v['mark']} {v['name']}: {v['result']}")

    if all_pass:
        lines.append(f"\n  → Verification: ALL PASSED ✅")
        lines.append(f"  → Proceed to learn() then deliver.")
    else:
        lines.append(f"\n  → Verification: FAILED ❌")
        lines.append(f"  → Issues to fix:")
        for iss in issues:
            lines.append(f"    • {iss}")
        lines.append(f"  → Fix these, then call refine(task_id, issues, iteration)")

    # Store in session
    session["last_verification"] = {
        "all_pass": all_pass,
        "items": [{"name": i["name"], "result": i["result"]} for i in items],
        "issues": issues,
    }
    _save_session(session)

    return guard_warning + "\n".join(lines)


@mcp.tool()
def refine(task_id: str, issues_fixed: str, iteration: int = 1) -> str:
    """
    COGNITIVE CORE — Track refinement iterations (hard cap: 3).

    Call after fixing issues identified by reflect() or verify().
    The system enforces a maximum of 3 refinement iterations to prevent
    infinite loops. After 3, deliver best-effort with a disclaimer.

    Parameters:
      task_id      : The task_id from start_turn()
      issues_fixed : What you fixed in this iteration
      iteration    : Which iteration this is (1, 2, or 3)

    Returns CONTINUE (re-reflect) or MAX_ITERATIONS (deliver best-effort).
    """
    guard_warning = _guard_check()
    _log_cognitive_call("refine")
    max_iterations = 3

    lines = [f"[COGNITIVE REFINE] task: {task_id}, iteration: {iteration}/{max_iterations}"]
    lines.append(f"  Fixed: {issues_fixed}")

    if iteration >= max_iterations:
        lines.append(f"\n  ⚠ MAX ITERATIONS REACHED ({max_iterations})")
        lines.append(f"  → Deliver your current best-effort output.")
        lines.append(f"  → Include a note to the user about remaining limitations.")
        lines.append(f"  → Proceed to learn(task_id, 'partial') to record what happened.")
        return guard_warning + "\n".join(lines)

    lines.append(f"\n  → Refinement recorded. Re-run reflect() to check if issues are resolved.")
    lines.append(f"  → Iterations remaining: {max_iterations - iteration}")
    return guard_warning + "\n".join(lines)


def _consolidate_soul():
    """Consolidate recent task history and anti-patterns into a background summary.
    Optimized to be memory and token efficient to avoid bloating instructions."""
    try:
        profile = _load_profile()
        gp = profile.get("global_profile", {})
        dp = gp.get("domain_performance", {})
        
        recent_tasks = []
        if os.path.exists(_HISTORY_FILE):
            with open(_HISTORY_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            recent_tasks.append(json.loads(line))
                        except Exception:
                            pass
        
        ap_data = _load_anti_patterns()
        anti_patterns = ap_data.get("anti_patterns", [])
        
        # Deduplicate anti-patterns in the database before processing
        merged_aps = {}
        for ap in anti_patterns:
            pat = ap.get("pattern", "").strip()
            if not pat:
                continue
            if pat not in merged_aps:
                merged_aps[pat] = ap
            else:
                existing = merged_aps[pat]
                # Keep highest severity
                if ap.get("severity") == "user_caught" and existing.get("severity") != "user_caught":
                    existing["severity"] = "user_caught"
                
                # Keep newest timestamp
                ts_existing = parse_timestamp(existing.get("timestamp"))
                ts_current = parse_timestamp(ap.get("timestamp"))
                if ts_current and ts_existing and ts_current > ts_existing:
                    existing["timestamp"] = ap.get("timestamp")
                elif ts_current and not ts_existing:
                    existing["timestamp"] = ap.get("timestamp")
                    
                # Keep max frequency
                existing["times_prevented"] = max(existing.get("times_prevented", 0), ap.get("times_prevented", 0))
        
        anti_patterns = list(merged_aps.values())
        
        now = datetime.now(timezone.utc)
        valid_aps = []
        archived_candidates = []
        
        for ap in anti_patterns:
            ts = parse_timestamp(ap.get("timestamp"))
            if ts is not None and (now - ts).days > 30:
                archived_candidates.append(ap)
            else:
                valid_aps.append(ap)
                
        def get_ap_weight(ap):
            sev = ap.get("severity", "medium")
            sev_w = 3 if sev == "user_caught" else 1
            tp = ap.get("type", "code")
            tp_w = 2 if tp == "execution" else 1
            freq = ap.get("times_prevented", 0) + 1
            return sev_w * tp_w * freq
            
        valid_aps.sort(key=get_ap_weight, reverse=True)
        
        active_aps = valid_aps[:10]
        archived_candidates.extend(valid_aps[10:])
        
        ap_data["anti_patterns"] = active_aps
        _save_anti_patterns(ap_data)
        
        if archived_candidates:
            archived_data = _load_archived_anti_patterns()
            archived_data["anti_patterns"].extend(archived_candidates)
            archived_data["anti_patterns"] = archived_data["anti_patterns"][-500:]
            _save_json(_ARCHIVED_FILE, archived_data)
            
        lines = [
            "### VoxKage Consolidated Soul History & Performance",
            "",
            "**Domain Metrics**:",
        ]
        
        for domain, perf in dp.items():
            tasks = perf.get("tasks", 0)
            successes = perf.get("successes", 0)
            rate = round(successes / max(tasks, 1) * 100, 1) if tasks > 0 else 0
            lines.append(f"- **{domain.upper()}**: {rate}% success rate ({successes}/{tasks} tasks)")
            errors = perf.get("common_errors", [])
            if errors:
                lines.append(f"  - Common Weaknesses: {', '.join(errors[:2])}")
                
        if active_aps:
            lines.append("")
            lines.append("**Learned Negative Constraints (Anti-Patterns)**:")
            for ap in active_aps:
                lines.append(f"- **{ap.get('domain', 'all').upper()}**: Avoid repeating: {ap.get('pattern')}")
                
        if recent_tasks:
            lines.append("")
            lines.append("**Recent Tasks Summary**:")
            for task in recent_tasks[-3:]:
                lines.append(f"- [{task.get('timestamp')[:10]}] {task.get('domain').upper()}: {task.get('outcome').upper()} (Confidence: {task.get('confidence')})")
                
        soul_md = "\n".join(lines)
        consolidation_file = os.path.join(_COG_DIR, "soul_consolidation.md")
        _atomic_write_file(consolidation_file, soul_md)
        
        global_claude_path = os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")
        global_agents_path = os.path.join(os.path.expanduser("~"), ".gemini", "config", "AGENTS.md")
        
        workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        workspace_agents_path = os.path.join(workspace_dir, ".agents", "AGENTS.md")
        
        _inject_soul_history(global_claude_path, soul_md)
        _inject_soul_history(global_agents_path, soul_md)
        _inject_soul_history(workspace_agents_path, soul_md)
        
    except Exception:
        pass



@mcp.tool()
def learn(task_id: str, outcome: str, confidence_was: float = 0.5, errors_found: str = "") -> str:
    """
    COGNITIVE CORE — Update global profile before delivering final output.

    Call this as the LAST cognitive tool before delivering your response.
    Records the task outcome, updates domain performance, calibrates confidence,
    analyzes the execution trace, and writes to persistent task history.

    Parameters:
      task_id        : The task_id from start_turn()
      outcome        : "success", "partial", or "failed"
      confidence_was : Your confidence estimate before execution (0.0-1.0)
      errors_found   : Comma-separated error descriptions found during the task

    Returns profile update confirmation and execution analysis.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("learn")
    session = _load_session()
    task = session.get("active_task") or {}
    domain = task.get("domain", "coding")
    tier = task.get("tier", 1)

    profile = _load_profile()
    gp = profile.get("global_profile", {})

    # Update domain performance
    dp = gp.get("domain_performance", {})
    if domain not in dp:
        dp[domain] = {"tasks": 0, "successes": 0, "common_errors": []}
    # Guard against None values in stored profile (defensive)
    dp[domain]["tasks"] = (dp[domain].get("tasks") or 0) + 1
    if outcome == "success":
        dp[domain]["successes"] = (dp[domain].get("successes") or 0) + 1

    # Add errors to common_errors (deduplicated, max 10)
    if errors_found:
        for err in errors_found.split(","):
            err = err.strip()
            if err and err not in dp[domain]["common_errors"]:
                dp[domain]["common_errors"].append(err)
        dp[domain]["common_errors"] = dp[domain]["common_errors"][-10:]  # Keep last 10

    # Update error categories from reflection data
    reflection = session.get("last_reflection", {})
    for fail_id in reflection.get("fails", []):
        cats = gp.get("error_categories", {})
        if fail_id not in cats:
            cats[fail_id] = {"frequency": 0, "last_seen": ""}
        cats[fail_id]["frequency"] += 1
        cats[fail_id]["last_seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gp["error_categories"] = cats

    gp["total_tasks_completed"] = (gp.get("total_tasks_completed") or 0) + 1
    gp["domain_performance"] = dp
    profile["global_profile"] = gp
    _save_profile(profile)

    # Dynamic Checklist Evolution: Escalate check items with >=3 failures to high severity
    # Also detects false-negative patterns: if model overrides REFINE→success 3+ times on same item,
    # that item is likely a noisy signal — downgrade it to prevent signal erosion.
    try:
        checklist_changed = False
        items = _load_checklist(domain)
        cats = gp.get("error_categories", {})

        # False-negative override detection
        reflect_recommended_refine = not session.get("last_reflection", {}).get("pass_rate", 1.0) >= 0.85
        if outcome == "success" and reflect_recommended_refine:
            # Model called success despite REFINE recommendation
            fn_overrides = gp.get("false_negative_overrides", {})
            for fail_id in session.get("last_reflection", {}).get("fails", []):
                fn_overrides[fail_id] = fn_overrides.get(fail_id, 0) + 1
                # At 3+ overrides, the item is noisy — prune its ability to block delivery
                if fn_overrides[fail_id] >= 3:
                    for item in items:
                        if item.get("id") == fail_id and item.get("severity") == "high":
                            item["severity"] = "medium"  # Downgrade noisy blocker
                            checklist_changed = True
            gp["false_negative_overrides"] = fn_overrides

        for item in items:
            item_id = item.get("id")
            if item_id in reflection.get("fails", []):
                freq = cats.get(item_id, {}).get("frequency", 0)
                if freq >= 3 and item.get("severity") != "high":
                    item["severity"] = "high"
                    checklist_changed = True
        if checklist_changed:
            _save_checklist(domain, items)

    except Exception:
        pass
    # Update confidence calibration
    cal = _load_calibration()
    if domain not in cal["domains"]:
        cal["domains"][domain] = {"predictions": [], "accuracy": None}
    cal["domains"][domain]["predictions"].append({
        "stated": round(confidence_was, 2),
        "actual_success": outcome == "success",
        "task_id": task_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Rolling window of last 50
    cal["domains"][domain]["predictions"] = cal["domains"][domain]["predictions"][-50:]
    # Recalculate accuracy
    preds = cal["domains"][domain]["predictions"]
    if len(preds) >= 5:
        correct = sum(1 for p in preds if (p["stated"] >= 0.5) == p["actual_success"])
        cal["domains"][domain]["accuracy"] = round(correct / len(preds), 2)
        gp["confidence_calibration"] = round(
            sum((cal["domains"][d].get("accuracy") or 0.5) for d in cal["domains"])
            / max(len(cal["domains"]), 1), 2
        )
        profile["global_profile"] = gp
        _save_profile(profile)
    _save_calibration(cal)

    # Append to task history
    _append_task_history({
        "task_id": task_id,
        "domain": domain,
        "outcome": outcome,
        "confidence": confidence_was,
        "errors": errors_found,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Update session — mark task as complete, move to last_task
    session["last_task"] = task
    session["active_task"] = None
    # Clean up this task's sub-tasks to prevent unbounded session growth
    if "sub_tasks" in session and task_id in session["sub_tasks"]:
        del session["sub_tasks"][task_id]
    # Write session failures to global profile if they persisted
    for failure in session.get("current_session_failures", []):
        if failure not in dp[domain].get("common_errors", []):
            dp[domain]["common_errors"].append(failure)
            dp[domain]["common_errors"] = dp[domain]["common_errors"][-10:]
            
    # Clear/prune domain-isolated elevated checks on successful task completion
    if outcome == "success":
        raw_elevated = session.get("elevated_checks", {})
        if isinstance(raw_elevated, dict) and domain in raw_elevated:
            raw_elevated[domain] = []
        elif isinstance(raw_elevated, list):
            session["elevated_checks"] = {}
            
    # Run execution trace analysis
    execution_warnings = []
    try:
        cog_trace = session.get("cognitive_trace", [])
        tool_trace = session.get("tool_trace", [])
        
        merged = []
        for item in cog_trace:
            if isinstance(item, dict):
                merged.append(item)
        for item in tool_trace:
            if isinstance(item, dict):
                merged.append(item)
                
        merged.sort(key=lambda x: x.get("timestamp", 0.0))
        
        sequence_items = []
        for x in merged:
            t_name = x.get("tool", "")
            t_args = x.get("arguments", "")
            if t_args:
                sequence_items.append(f"{t_name}({t_args})")
            else:
                sequence_items.append(t_name)
        
        reconstructed_sequence = "; ".join(sequence_items)
        if reconstructed_sequence:
            execution_warnings = _analyze_execution_trace(task_id, domain, tier, reconstructed_sequence)
    except Exception:
        pass

    # Overfitting protection for Tier overrides
    orig_msg = task.get("user_message", "")
    if orig_msg:
        normalized = _normalize_pattern(orig_msg)
        if normalized:
            is_overkill = any(w.get("type") == "tier_overkill" for w in execution_warnings)
            history = session.setdefault("tier_1_candidate_history", {})
            pattern_history = history.setdefault(normalized, [])
            pattern_history.append("overkill" if is_overkill else "normal")
            history[normalized] = pattern_history[-3:]
            
            if history[normalized].count("overkill") >= 2:
                try:
                    rules = _load_dynamic_rules()
                    force_patterns = rules.setdefault("force_tier_1_patterns", [])
                    if normalized not in force_patterns:
                        force_patterns.append(normalized)
                        _save_dynamic_rules(rules)
                except Exception:
                    pass

    _save_session(session)
    _consolidate_soul()

    # Response
    success_rate = round(
        (dp[domain].get("successes") or 0) / max(dp[domain].get("tasks") or 1, 1) * 100, 1
    )
    cal_score = gp.get("confidence_calibration", 0.5)
    total = gp.get("total_tasks_completed", 0)
    corrections = gp.get("total_user_corrections", 0)

    lines = [
        f"[COGNITIVE LEARN] task: {task_id} → {outcome.upper()}",
        f"  Domain {domain}: {success_rate}% success rate ({dp[domain]['successes']}/{dp[domain]['tasks']})",
        f"  Calibration: {cal_score}",
        f"  Lifetime: {total} tasks, {corrections} user corrections",
    ]
    if errors_found:
        lines.append(f"  Errors logged: {errors_found}")

    if execution_warnings:
        lines.append("\n  ⚠️ Execution Anti-Patterns Detected:")
        for warn in execution_warnings:
            lines.append(f"    • [{warn['type'].upper()}]: {warn['pattern']}")
            lines.append(f"      → Suggested Fix: {warn['suggested_fix']}")

    lines.append(f"\n  → Profile updated. Deliver your response now.")
    return guard_warning + "\n".join(lines)


@mcp.tool()
def user_corrected(task_id: str, correction: str, error_category: str = "") -> str:
    """
    COGNITIVE CORE — High-weight learning from user corrections.

    Call this IMMEDIATELY when the user corrects your output DESPITE
    you having run the full metacognitive cycle. This is the most
    valuable learning signal — weighted 10x higher in the profile.

    Parameters:
      task_id        : The task_id of the corrected task
      correction     : What the user said was wrong and what the fix should be
      error_category : Optional category (e.g., "logic_error", "api_design", "missing_feature")

    Returns confirmation of the high-weight profile update.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("user_corrected")
    session = _load_session()
    task = session.get("last_task") or session.get("active_task") or {}
    domain = task.get("domain", "coding")

    # Update profile
    profile = _load_profile()
    gp = profile.get("global_profile", {})
    gp["total_user_corrections"] = gp.get("total_user_corrections", 0) + 1

    # Add to common errors (high priority)
    dp = gp.get("domain_performance", {})
    if domain in dp:
        err_text = error_category if error_category else correction[:80]
        if err_text not in dp[domain].get("common_errors", []):
            # Insert at front (highest priority)
            dp[domain]["common_errors"].insert(0, err_text)
            dp[domain]["common_errors"] = dp[domain]["common_errors"][:10]

    gp["domain_performance"] = dp
    profile["global_profile"] = gp
    _save_profile(profile)

    # Add to anti-patterns (severity: user_caught — highest weight) unless pruning false positive
    prune_note = ""
    if error_category in ["false_positive", "anti_pattern_error"]:
        keywords = [w.lower() for w in re.findall(r'\b\w{4,}\b', correction) if w.lower() not in ["that", "this", "with", "have", "from"]]
        ap_data = _load_anti_patterns()
        active_aps = ap_data.get("anti_patterns", [])
        
        remaining_aps = []
        archived_candidates = []
        
        for ap in active_aps:
            pat_lower = ap.get("pattern", "").lower()
            same_task = ap.get("learned_from") == task_id
            
            keyword_match = False
            if ap.get("domain") == domain or ap.get("domain") == "all":
                hits = sum(1 for kw in keywords if kw in pat_lower)
                if len(keywords) > 0 and hits >= min(2, len(keywords)):
                    keyword_match = True
            
            if same_task or keyword_match:
                archived_candidates.append(ap)
            else:
                remaining_aps.append(ap)
                
        ap_data["anti_patterns"] = remaining_aps
        _save_anti_patterns(ap_data)
        
        if archived_candidates:
            archived_data = _load_archived_anti_patterns()
            archived_data["anti_patterns"].extend(archived_candidates)
            archived_data["anti_patterns"] = archived_data["anti_patterns"][-500:]
            _save_json(_ARCHIVED_FILE, archived_data)
            
        prune_note = f"\n  → Pruned {len(archived_candidates)} active anti-pattern(s) and archived them."
    else:
        ap_data = _load_anti_patterns()
        ap_data["anti_patterns"].append({
            "pattern": correction[:200],
            "domain": domain,
            "severity": "user_caught",
            "learned_from": task_id,
            "error_category": error_category,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "times_prevented": 0,
        })
        ap_data["anti_patterns"] = ap_data["anti_patterns"][-50:]
        _save_anti_patterns(ap_data)

    # Update checklist dynamic rules
    try:
        items = _load_checklist(domain)
        check_id = f"corr_{task_id}"
        check_text = f"Did we avoid repeating: {correction[:120]}?"
        if not any(it.get("id") == check_id for it in items):
            items.append({
                "id": check_id,
                "check": check_text,
                "severity": "high"
            })
            _save_checklist(domain, items)
    except Exception:
        pass

    # Update session elevated checks — use per-domain dict (same format as everywhere else)
    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, list):
        raw_elevated = {domain: raw_elevated}
        session["elevated_checks"] = raw_elevated
    elif not isinstance(raw_elevated, dict):
        raw_elevated = {}
        session["elevated_checks"] = raw_elevated
    domain_elevated = raw_elevated.setdefault(domain, [])
    if error_category and error_category not in domain_elevated:
        domain_elevated.append(error_category)
    # Also add key words from correction
    words = re.findall(r'\b\w{5,}\b', correction.lower())
    for w in words[:2]:
        if w not in domain_elevated:
            domain_elevated.append(w)
    _save_session(session)

    # Update calibration (this task was a failure despite claimed confidence)
    cal = _load_calibration()
    if domain not in cal["domains"]:
        cal["domains"][domain] = {"predictions": [], "accuracy": None}
    elif not isinstance(cal["domains"][domain], dict):
        cal["domains"][domain] = {"predictions": [], "accuracy": None}
        
    if "predictions" not in cal["domains"][domain] or not isinstance(cal["domains"][domain]["predictions"], list):
        cal["domains"][domain]["predictions"] = []
        
    cal["domains"][domain]["predictions"].append({
        "stated": 0.9,
        "actual_success": False,
        "task_id": task_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    cal["domains"][domain]["predictions"] = cal["domains"][domain]["predictions"][-50:]
    _save_calibration(cal)
    _consolidate_soul()

    # Dynamic classification correction
    override_note = ""
    corr_lower = correction.lower()
    if any(kw in corr_lower for kw in ["tier", "classif", "read-only", "read only", "observation", "ceremony", "overhead"]):
        orig_msg = task.get("user_message", "")
        if orig_msg:
            try:
                rules = _load_dynamic_rules()
                escaped_pattern = _normalize_pattern(orig_msg)
                if escaped_pattern:
                    force_patterns = rules.setdefault("force_tier_1_patterns", [])
                    if escaped_pattern not in force_patterns:
                        force_patterns.append(escaped_pattern)
                        _save_dynamic_rules(rules)
                        override_note = f"\n  → Added dynamic classification override pattern: {escaped_pattern}"
            except Exception as e:
                override_note = f"\n  → Failed to add classification override: {e}"

    corrections = gp.get("total_user_corrections", 0)
    total = gp.get("total_tasks_completed", 0)
    ratio = round(corrections / max(total, 1) * 100, 1)

    return guard_warning + (
        f"[COGNITIVE] ⚡ User correction recorded (HIGH WEIGHT)\n"
        f"  Task: {task_id}\n"
        f"  Domain: {domain}\n"
        f"  Correction: {correction[:150]}\n"
        f"  Category: {error_category or 'uncategorized'}\n"
        f"  → Anti-pattern logged. Will flag this in future pre_mortem() calls.\n"
        f"  → Elevated checks updated for this session.{override_note}\n"
        f"  → Correction ratio: {ratio}% ({corrections}/{total} tasks)\n"
        f"  → This correction is now permanently part of VoxKage's learning."
    )




@mcp.tool()
def optimize_cognitive_core() -> str:
    """
    COGNITIVE CORE — Proactive self-optimization and cleaning.

    Reads recent task history and execution anti-patterns. Prunes obsolete
    checklist items, groups common errors, cleans up redundant rules, and
    performs a self-check on the cognitive core server's dynamic rules.

    Returns summary of optimizations performed.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("optimize_cognitive_core")
    try:
        rules = _load_dynamic_rules()
        profile = _load_profile()
        gp = profile.get("global_profile", {})
        
        # Deduplicate force_tier_1_patterns
        force_pats = rules.get("force_tier_1_patterns", [])
        unique_pats = list(dict.fromkeys(force_pats))
        rules["force_tier_1_patterns"] = unique_pats
        _save_dynamic_rules(rules)
        
        # Consolidate soul history
        _consolidate_soul()
        
        # Perform check of config files size and count
        ap_data = _load_anti_patterns()
        aps_count = len(ap_data.get("anti_patterns", []))
        total_tasks = gp.get("total_tasks_completed", 0)
        
        summary = [
            "[COGNITIVE OPTIMIZE] Proactive Optimization Complete",
            f"  - Deduplicated force_tier_1_patterns (Count: {len(unique_pats)})",
            f"  - Consolidated Soul History MD file",
            f"  - Verified {aps_count} active anti-patterns",
            f"  - Total completed tasks cataloged: {total_tasks}",
        ]
        return guard_warning + "\n".join(summary)
    except Exception as e:
        return guard_warning + f"[COGNITIVE OPTIMIZE] Failed: {e}"




@mcp.tool()
def log_tool_execution(tool_name: str, arguments: str = "") -> str:
    """
    COGNITIVE CORE — Auto-instrumented tool execution logger. Appends a tool execution to the session trace.

    Parameters:
      tool_name : Name of the executed tool.
      arguments : JSON string or raw arguments passed to the tool.
    """
    global _last_start_turn_ts
    _last_start_turn_ts = _time.time()
    try:
        session = _load_session()
        tool_trace = session.setdefault("tool_trace", [])
        
        # Clean arguments if it is a JSON string
        args_cleaned = arguments
        if isinstance(arguments, str) and arguments.strip():
            try:
                # Try to parse and format it compactly
                parsed = json.loads(arguments)
                args_cleaned = json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
        elif not isinstance(arguments, str):
            try:
                args_cleaned = json.dumps(arguments, ensure_ascii=False)
            except Exception:
                args_cleaned = str(arguments)

        # Truncate args_cleaned if too long
        if args_cleaned and len(args_cleaned) > 200:
            args_cleaned = args_cleaned[:200] + "..."

        tool_trace.append({
            "tool": tool_name,
            "arguments": args_cleaned,
            "timestamp": _time.time()
        })
        session["tool_trace"] = tool_trace
        _save_session(session)
    except Exception:
        pass
    return f"Logged tool execution: {tool_name}"




@mcp.tool()
def verify_code_file(filepath: str, domain: str = "") -> str:
    """
    COGNITIVE CORE — Deep domain-aware file verification tool.

    Analyzes a code file for syntax, imports, context-specific issues,
    and security vulnerabilities. Understands if the file is part of a
    web frontend, backend API, machine learning model, mobile app,
    database layer, or desktop application, and runs targeted checks.
    Runs asynchronously with strict 15s timeout to prevent hanging.

    Parameters:
      filepath : Absolute or relative path to the code file to verify.
      domain   : Optional domain context.

    Returns verification report.
    """
    import os
    import re
    import subprocess
    
    guard_warning = _guard_check()
    _log_cognitive_call("verify_code_file")
    
    # Resolve absolute path
    abs_path = os.path.abspath(filepath)
    if not os.path.exists(abs_path):
        return guard_warning + f"[COGNITIVE VERIFY] Error: File '{filepath}' does not exist at absolute path '{abs_path}'."
        
    filename = os.path.basename(abs_path)
    ext = os.path.splitext(filename)[1].lower()
    
    # Read file content safely
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return guard_warning + f"[COGNITIVE VERIFY] Error: Failed to read file content: {e}"
        
    lines_count = len(content.splitlines())
    
    # Helper to run shell commands safely with 15s timeout
    def run_cmd(cmd):
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "TIMEOUT: Command took longer than 15 seconds to execute."
        except Exception as err:
            return -2, "", str(err)

    report_lines = [
        f"[COGNITIVE VERIFY] File: {filename} ({lines_count} lines)",
        f"  Path: {abs_path}",
    ]
    
    # ── Context Detection ─────────────────────────────────────────────────
    context = "General"
    
    # Detect React Native / Expo Mobile Context
    is_mobile = "react-native" in content or "expo" in content or "expo-file-system" in content or "/ios/" in abs_path.replace("\\", "/") or "/android/" in abs_path.replace("\\", "/")
    
    # Detect Machine Learning / Data Science
    is_ml = any(lib in content for lib in ["torch", "tensorflow", "keras", "sklearn", "pandas", "numpy", "scipy"])
    
    # Detect Web Frontend
    is_frontend = any(lib in content for lib in ["react", "vue", "angular", "svelte", "jsx", "tsx"]) and not is_mobile
    
    # Detect Database / Backend Data Layer
    is_db = any(lib in content for lib in ["sqlite3", "psycopg2", "sqlalchemy", "pymongo", "prisma", "pg-pool", "mysql"])
    
    # Detect Backend API
    is_backend = any(lib in content for lib in ["express", "django", "flask", "fastapi", "nestjs", "cors"]) or is_db
    
    # Detect Desktop App
    is_desktop = any(lib in content for lib in ["PyQt5", "PySide", "tkinter", "electron", "tauri"])
    
    if is_mobile:
        context = "Mobile App (React Native/Expo)"
    elif is_ml:
        context = "Machine Learning / Data Science"
    elif is_frontend:
        context = "Web Frontend"
    elif is_backend:
        context = "Backend API"
    elif is_desktop:
        context = "Desktop App"
    elif is_db:
        context = "Database/Data Layer"
        
    report_lines.append(f"  Detected Context: {context}")
    
    # ── Syntax Check ──────────────────────────────────────────────────────
    syntax_ok = True
    syntax_error = ""
    
    if ext == ".py":
        # Python Compile check
        code, out, err = run_cmd(f"python -m py_compile \"{abs_path}\"")
        if code != 0:
            syntax_ok = False
            syntax_error = err or out
    elif ext in [".js", ".jsx"]:
        # Node check
        code, out, err = run_cmd(f"node --check \"{abs_path}\"")
        if code != 0:
            syntax_ok = False
            syntax_error = err or out
    elif ext in [".ts", ".tsx"]:
        # TypeScript Syntax check using tsc if available
        code, out, err = run_cmd(f"npx tsc --noEmit --target esnext --moduleResolution node \"{abs_path}\"")
        if code != 0 and "tsc: command not found" not in err and "Cannot find command" not in err:
            # We only count it as syntax error if tsc succeeded to run but found issues
            syntax_ok = False
            syntax_error = out or err
            
    if syntax_ok:
        report_lines.append("  ✅ Syntax Check: PASSED (Compiles/Parses successfully)")
    else:
        report_lines.append("  ❌ Syntax Check: FAILED")
        report_lines.append(f"    Error: {syntax_error.strip()}")
        
    # ── Static Security Checks (Secrets Scan) ──────────────────────────────
    secrets_found = []
    secret_patterns = {
        "AWS API Key": r"AKIA[0-9A-Z]{16}",
        "Generic Secret/Password": r"(?i)(password|secret|private_key|api_key|token|passwd|credential)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.\~]{8,}['\"]",
        "Slack Webhook": r"https://hooks\.slack\.com/services/[T][A-Z0-9]{8}/[B][A-Z0-9]{8}/[a-zA-Z0-9]{24}",
        "Google API Key": r"AIza[0-9A-Za-z\\-_]{35}"
    }
    
    for name, pattern in secret_patterns.items():
        matches = re.findall(pattern, content)
        if matches:
            # Exclude false positives like placeholder text
            filtered_matches = [m for m in matches if not any(x in str(m).lower() for x in ["placeholder", "your_", "my_", "test_", "dummy", "example"])]
            if filtered_matches:
                secrets_found.append(name)
                
    if secrets_found:
        report_lines.append("  ❌ Security Check: FAILED (Potential Secrets Committed)")
        for sec in secrets_found:
            report_lines.append(f"    • Found potential {sec}")
    else:
        report_lines.append("  ✅ Security Check: PASSED (No hardcoded credentials detected)")
        
    # ── Deprecated/Anti-Pattern Framework Warnings ─────────────────────────
    warnings_found = []
    
    # Context-Specific Checks
    if is_mobile:
        # Expo SDK 56 deprecations (FileSystem downloadAsync)
        if "downloadAsync" in content and "expo-file-system" in content:
            if "expo-file-system/legacy" not in content:
                warnings_found.append("FileSystem.downloadAsync is deprecated in modern Expo SDK 56. Import from 'expo-file-system/legacy' instead.")
                
    if is_ml:
        # Check for unseeded operations (reproducibility)
        if "random" in content or "numpy" in content or "torch" in content:
            if not any(seed in content for seed in ["seed", "manual_seed", "random_state"]):
                warnings_found.append("Unseeded random operations detected. Add np.random.seed() or torch.manual_seed() for reproducibility.")
                
    if is_backend:
        # Check for raw SQL injection vectors in databases
        if ext == ".py" and any(db in content for db in ["sqlite3", "psycopg2", "execute"]):
            # Find execute call with string formatting e.g. execute("SELECT... %s" % var) or execute(f"SELECT... {var}")
            if re.search(r"\.execute\(\s*(f['\"]|['\"].*?%|.*?\+)", content):
                warnings_found.append("Potential SQL injection: raw string formatting detected inside database execute() command. Use parameterized queries.")
                
    if is_frontend:
        # React key warning
        if ".map(" in content and "key=" not in content:
            warnings_found.append("React list render: Potential missing 'key' prop inside array.map iteration.")
            
    if warnings_found:
        report_lines.append("  ⚠ Framework Warnings:")
        for warn in warnings_found:
            report_lines.append(f"    • {warn}")
    else:
        report_lines.append("  ✅ Framework Checks: PASSED (No common domain anti-patterns found)")
        
    report_lines.append("\n  → Verification complete. Incorporate these results into your reflect() call.")
    return guard_warning + "\n".join(report_lines)


@mcp.tool()
def generate_critique(task_id: str, code_content: str, domain: str = "") -> str:
    """
    COGNITIVE CORE — Code quality, complexity, and styling auditor.

    Scans the provided code content for complexity violations (e.g. large
    functions, deep nesting), styling concerns (leftover logs/prints), and
    error handling deficiencies (empty catch blocks) to feed into the reflect phase.

    Parameters:
      task_id      : The task_id of the active task.
      code_content : The actual source code content to review.
      domain       : Optional domain context.

    Returns critique report.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("generate_critique")
    import re
    
    report_lines = [
        f"[COGNITIVE CRITIQUE] task: {task_id}",
        "  Heuristic audit of code quality and standards:",
        ""
    ]
    
    concerns = []
    
    # ── 1. Leftover Debugging Code ─────────────────────────────────────────
    debug_calls = []
    if "console.log" in code_content:
        debug_calls.append("console.log()")
    if "debugger" in code_content:
        debug_calls.append("debugger statements")
    if "alert(" in code_content:
        debug_calls.append("alert() triggers")
    # Python prints - only check if it is python code
    if "def " in code_content or "import " in code_content:
        # regex for print statements
        if re.search(r"\bprint\s*\(", code_content):
            debug_calls.append("print() statements")
            
    if debug_calls:
        concerns.append(f"Leftover debugging traces detected: {', '.join(debug_calls)}. Clean these up for production code.")
        
    # ── 2. Error Handling Deficiencies ──────────────────────────────────────
    # Empty except blocks in Python
    if re.search(r"except\s*(?:Exception)?\s*:\s*(?:pass|continue|break)", code_content):
        concerns.append("Empty 'except:' block with 'pass' or no action. Ensure exceptions are logged or handled gracefully.")
        
    # Empty catch blocks in JS/TS
    if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", code_content):
        concerns.append("Empty catch block in JavaScript/TypeScript. Avoid swallowing errors silently.")
        
    # ── 3. Complexity Warnings ─────────────────────────────────────────────
    # Functions > 60 lines
    func_lines = 0
    in_func = False
    max_func_len = 0
    for line in code_content.splitlines():
        line_strip = line.strip()
        if line_strip.startswith(("def ", "function ", "async function ")) or "=>" in line:
            if in_func and func_lines > max_func_len:
                max_func_len = func_lines
            in_func = True
            func_lines = 0
        elif in_func:
            func_lines += 1
            
    if in_func and func_lines > max_func_len:
        max_func_len = func_lines
        
    if max_func_len > 60:
        concerns.append(f"Function length concern: Largest function has ~{max_func_len} lines. Consider splitting functions longer than 60 lines.")
        
    # Nested loops check (e.g. 3+ nested levels)
    nesting_depth = 0
    max_nesting = 0
    for line in code_content.splitlines():
        indent = len(line) - len(line.lstrip())
        # Heuristic check for nesting by loops/conditionals indents
        if re.search(r"\b(for|while|if)\b", line):
            nesting_depth = indent // 4
            if nesting_depth > max_nesting:
                max_nesting = nesting_depth
                
    if max_nesting >= 4:
        concerns.append(f"Deep nesting concern: Maximum block nesting indentation level is {max_nesting}. Simplify logic to reduce complexity.")
        
    # ── 4. Styling & Quality Check ─────────────────────────────────────────
    # Too many comments vs code or completely missing docstrings
    if "def " in code_content and not '"""' in code_content and not "'''" in code_content:
        concerns.append("Missing Python docstrings. Ensure public functions/classes are documented.")
        
    # Summary of findings
    if concerns:
        report_lines.append(f"  ❌ Quality Concerns Found ({len(concerns)}):")
        for conc in concerns:
            report_lines.append(f"    • {conc}")
    else:
        report_lines.append("  ✅ Audit Passed: Code looks clean, structured, and compliant with best practices.")
        
    report_lines.append("\n  → Critique complete. Use these details to grade your reflect() results.")
    return guard_warning + "\n".join(report_lines)


@mcp.tool()
def get_profile(domain: str = "") -> str:
    """
    COGNITIVE CORE — View current capability heatmap and session state.

    Returns a compact overview of VoxKage's performance across domains,
    anti-pattern count, confidence calibration, and current session state.

    Parameters:
      domain : Optional. If specified, show detailed stats for that domain only.
               If empty, show all domains in compact format.
    """
    _log_cognitive_call("get_profile")
    profile = _load_profile()
    gp = profile.get("global_profile", {})
    dp = gp.get("domain_performance", {})
    session = _load_session()
    cal = _load_calibration()
    ap_data = _load_anti_patterns()

    lines = ["[COGNITIVE PROFILE] VoxKage Capability Heatmap", ""]

    if domain and domain in dp:
        # Detailed view for one domain
        d = dp[domain]
        tasks = d.get("tasks", 0)
        successes = d.get("successes", 0)
        rate = round(successes / max(tasks, 1) * 100, 1)
        errors = d.get("common_errors", [])
        domain_cal = cal.get("domains", {}).get(domain, {})

        lines.append(f"  Domain: {domain.upper()}")
        lines.append(f"  Success rate: {rate}% ({successes}/{tasks})")
        lines.append(f"  Calibration: {domain_cal.get('accuracy', 'N/A')}")
        if errors:
            lines.append(f"  Common errors:")
            for e in errors[:5]:
                lines.append(f"    • {e}")
        domain_aps = [ap for ap in ap_data.get("anti_patterns", []) if ap.get("domain") == domain]
        if domain_aps:
            lines.append(f"  Anti-patterns ({len(domain_aps)}):")
            for ap in domain_aps[:5]:
                lines.append(f"    🚫 {ap.get('pattern', '?')[:80]}")
    else:
        # Compact view of all domains
        lines.append("  Domain Performance:")
        for d_name, d_data in dp.items():
            tasks = d_data.get("tasks", 0)
            successes = d_data.get("successes", 0)
            rate = round(successes / max(tasks, 1) * 100, 1) if tasks > 0 else "N/A"
            bar = "█" * int(successes / max(tasks, 1) * 10) if tasks > 0 else "░" * 10
            lines.append(f"    {d_name:10s} {bar} {rate}% ({successes}/{tasks})")

        lines.append(f"\n  Overall:")
        lines.append(f"    Total tasks: {gp.get('total_tasks_completed', 0)}")
        lines.append(f"    User corrections: {gp.get('total_user_corrections', 0)}")
        lines.append(f"    Confidence calibration: {gp.get('confidence_calibration', 0.5)}")
        lines.append(f"    Anti-patterns tracked: {len(ap_data.get('anti_patterns', []))}")

    # Session state
    lines.append(f"\n  Current Session:")
    lines.append(f"    Session ID: {session.get('session_id', '?')}")
    lines.append(f"    Tasks this session: {len(session.get('tasks_this_session', []))}")
    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, dict):
        elevated = [item for items in raw_elevated.values() for item in items]
    else:
        elevated = raw_elevated if isinstance(raw_elevated, list) else []
    if elevated:
        lines.append(f"    Elevated checks: {', '.join(elevated[:5])}")
    failures = session.get("current_session_failures", [])
    if failures:
        lines.append(f"    Session failures: {', '.join(failures[:3])}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
