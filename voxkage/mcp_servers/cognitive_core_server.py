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

# Checklists shipped with the package (read-only)
_CHECKLISTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "checklists")
_WRITABLE_CHECKLISTS_DIR = os.path.join(_COG_DIR, "checklists")

os.makedirs(_COG_DIR, exist_ok=True)
os.makedirs(_HISTORY_DIR, exist_ok=True)
os.makedirs(_WRITABLE_CHECKLISTS_DIR, exist_ok=True)

def _save_checklist(domain: str, items: list):
    path = os.path.join(_WRITABLE_CHECKLISTS_DIR, f"{domain}.json")
    _save_json(path, {"domain": domain, "items": items})

# ── Turn-level guard ──────────────────────────────────────────────────────────
# Tracks when start_turn() was last called. Other cognitive tools check this
# and emit a loud warning if it was skipped.
import time as _time
_last_start_turn_ts: float = 0.0  # epoch seconds
_GUARD_WINDOW_SECS = 120  # allow 2 minutes between start_turn and other tools

def _guard_check() -> str:
    """Returns a protocol violation warning if start_turn() was not called recently."""
    global _last_start_turn_ts
    elapsed = _time.time() - _last_start_turn_ts
    if _last_start_turn_ts == 0.0 or elapsed > _GUARD_WINDOW_SECS:
        return (
            "\n⚠⚠⚠ PROTOCOL VIOLATION ⚠⚠⚠\n"
            "You called a cognitive tool WITHOUT calling start_turn() first!\n"
            "RULE ZERO: start_turn(user_message) MUST be your FIRST action every turn.\n"
            "Call start_turn() NOW before proceeding.\n"
            "⚠⚠⚠ END VIOLATION ⚠⚠⚠\n\n"
        )
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
            },
            "error_categories": {},
            "confidence_calibration": 0.5,
            "total_tasks_completed": 0,
            "total_user_corrections": 0,
        }
    }

def _load_json(path: str, default_factory) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default_factory()

def _save_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_profile() -> dict:
    return _load_json(_PROFILE_FILE, _default_profile)

def _save_profile(profile: dict):
    _save_json(_PROFILE_FILE, profile)

def _load_anti_patterns() -> dict:
    return _load_json(_ANTI_PATTERNS_FILE, lambda: {"anti_patterns": []})

def _save_anti_patterns(data: dict):
    _save_json(_ANTI_PATTERNS_FILE, data)

def _load_calibration() -> dict:
    return _load_json(_CALIBRATION_FILE, lambda: {"domains": {}})

def _save_calibration(data: dict):
    _save_json(_CALIBRATION_FILE, data)

def _load_session() -> dict:
    data = _load_json(_SESSION_FILE, lambda: {})
    if not data or not data.get("session_id"):
        # New session
        data = {
            "session_id": "ses_" + str(uuid.uuid4())[:8],
            "session_start": datetime.now(timezone.utc).isoformat(),
            "active_task": None,
            "last_task": None,
            "current_session_failures": [],
            "elevated_checks": [],
            "tasks_this_session": [],
        }
        _save_json(_SESSION_FILE, data)
    return data

def _save_session(data: dict):
    _save_json(_SESSION_FILE, data)

def _load_checklist(domain: str) -> list:
    """Load a dynamic checklist for the given domain from writable storage, falling back to shipped template."""
    path = os.path.join(_WRITABLE_CHECKLISTS_DIR, f"{domain}.json")
    if not os.path.exists(path):
        shipped_path = os.path.join(_CHECKLISTS_DIR, f"{domain}.json")
        if os.path.exists(shipped_path):
            try:
                import shutil
                shutil.copy2(shipped_path, path)
            except Exception:
                path = shipped_path
        else:
            return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items", [])
    except Exception:
        return []


def _append_task_history(entry: dict):
    """Append a task summary to recent.jsonl, capping at 100 entries."""
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
    # FIFO cap at 100
    if len(entries) > 100:
        entries = entries[-100:]
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


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
        r"explain|article|paper|blog|documentation|wiki)\b", re.I
    ),
    "system": re.compile(
        r"\b(system|os|windows|process|kill|restart|shutdown|sleep|"
        r"battery|disk|memory|cpu|ram|wifi|bluetooth|network|volume|"
        r"brightness|wallpaper|screenshot|clipboard|notification|"
        r"install|uninstall|update|driver|registry|service|startup|"
        r"firewall|antivirus|recycle\s*bin|task\s*manager|terminal|"
        r"command|powershell|bash|shell|folder|directory|file)\b", re.I
    ),
    "coding": re.compile(
        r"\b(code|coding|program|script|function|class|module|package|"
        r"debug|refactor|optimize|test|lint|compile|build|fix\s*(the\s*)?bug|"
        r"implement|write\s*(a\s*)?(function|script|class|code|program)|"
        r"git|commit|branch|merge|pull\s*request|review|pr|repo|"
        r"algorithm|data\s*structure|pattern|architecture|dependency|"
        r"import|export|type|interface|generic|inheritance)\b", re.I
    ),
}

# Complexity signals for tiering
_COMPLEXITY_HIGH = re.compile(
    r"\b(make\s*sure|perfect|thorough|comprehensive|complete|detailed|"
    r"production|robust|scalable|enterprise|full\s*stack|from\s*scratch|"
    r"end\s*to\s*end|step\s*by\s*step|multiple|several|all|every|entire)\b", re.I
)
_MULTI_REQUIREMENT = re.compile(r"\b(and|also|plus|additionally|then|after\s*that|next)\b", re.I)


def _classify_intent(msg: str) -> dict:
    """
    Rule-based intent classification. Returns type, domain, tier, etc.
    Pure computation — no LLM calls, no disk writes. <1ms.
    """
    msg_stripped = msg.strip()
    if not msg_stripped:
        return {"type": "conversation"}

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
        domain = max(domain_scores, key=domain_scores.get)
    else:
        # Default: if message has code keywords → coding, else general task
        domain = "coding" if _CODE_KEYWORDS.search(msg) else "system"

    # ── Tier classification ────────────────────────────────────────────────
    tier = 1  # Default: quick task
    msg_len = len(msg_stripped)

    if msg_len > 200 or _COMPLEXITY_HIGH.search(msg):
        tier = 3
    elif msg_len > 80 or len(_MULTI_REQUIREMENT.findall(msg)) >= 2:
        tier = 2

    return {
        "type": "task",
        "domain": domain,
        "tier": tier,
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


# ═════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def start_turn(user_message: str) -> str:
    """
    COGNITIVE CORE — MANDATORY FIRST CALL EVERY SINGLE TURN.

    This is the metacognitive gate. Call this BEFORE doing anything else,
    every single turn, no exceptions. It classifies the user's intent and
    returns either a lightweight "conversation" signal or a full task
    context with domain checklist, anti-pattern warnings, and profile data.

    Cost: ~10-15 tokens. Classification is rule-based (<1ms, no LLM call).

    Parameters:
      user_message : The user's raw message text for this turn.

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
    classification = _classify_intent(user_message)

    if classification["type"] == "conversation":
        session["last_start_turn_ts"] = _time.time()
        _save_session(session)
        return "[COGNITIVE] type: conversation\nRespond normally. No cognitive tools needed."

    # ── Task detected ──────────────────────────────────────────────────────
    domain = classification["domain"]
    tier = classification["tier"]

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

    # Load anti-patterns for this domain
    ap_data = _load_anti_patterns()
    domain_anti_patterns = [
        ap for ap in ap_data.get("anti_patterns", [])
        if ap.get("domain") == domain or ap.get("domain") == "all"
    ][:5]

    # Load checklist
    checklist = _load_checklist(domain)

    # Session elevated checks (from recent failures)
    elevated = session.get("elevated_checks", [])

    # Adjust tier based on profile confidence and safety triggers
    if success_rate < 0.70 and tasks_done >= 3:
        tier = 3  # Force complex tier for low success rate domains
    elif success_rate < 0.85 and tasks_done >= 3:
        tier = max(tier, 2)
    if len(common_errors) >= 3:
        tier = max(tier, 2)
    if len(elevated) > 0:
        tier = 3  # Force complex tier if there are active session failures

    # Build task context
    task_context = {
        "task_id": task_id,
        "domain": domain,
        "tier": tier,
        "is_followup": is_followup,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Update session state
    session["active_task"] = task_context
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
    lines.append(f"    confidence_calibration: {gp.get('confidence_calibration', 0.5)}")
    if common_errors:
        lines.append(f"    known_weak_areas: {', '.join(common_errors[:3])}")

    # Anti-pattern warnings
    if domain_anti_patterns:
        lines.append(f"\n  ⚠ Anti-Pattern Warnings:")
        for ap in domain_anti_patterns[:3]:
            lines.append(f"    • {ap.get('pattern', '?')}")

    # Session elevated checks
    if elevated:
        lines.append(f"\n  🔺 Elevated (failed recently this session):")
        for e in elevated[:3]:
            lines.append(f"    • {e}")

    # Checklist
    if checklist:
        lines.append(f"\n  Checklist ({domain}, {len(checklist)} items):")
        for item in checklist:
            priority = " ⚡" if item["id"] in elevated else ""
            lines.append(f"    □ [{item['severity'].upper()}]{priority} {item['check']}")

    # Tier instructions
    lines.append(f"\n  Protocol (Tier {tier}):")
    if tier == 1:
        lines.append("    1. Execute the task")
        lines.append("    2. Quick mental check against the checklist above")
        lines.append("    3. learn(task_id, outcome) → Deliver")
    elif tier == 2:
        lines.append("    1. pre_mortem(task_id, summary) → Note risks")
        lines.append("    2. Execute the task with risks in mind")
        lines.append("    3. Evaluate output against checklist → reflect(task_id, summary, results)")
        lines.append("    4. If REFINE recommended → fix → refine(task_id, issues, iteration)")
        lines.append("    5. learn(task_id, outcome) → Deliver")
    else:
        lines.append("    1. pre_mortem(task_id, summary) → Note risks")
        lines.append("    2. Execute with checkpoint() after each major sub-step")
        lines.append("    3. Full checklist evaluation → reflect(task_id, summary, results)")
        lines.append("    4. Run external verification → verify(task_id, results)")
        lines.append("    5. If issues → refine() loop (max 3 iterations)")
        lines.append("    6. learn(task_id, outcome) → Deliver")

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
    session = _load_session()
    task = session.get("active_task", {})
    domain = task.get("domain", "coding")

    profile = _load_profile()
    gp = profile.get("global_profile", {})
    domain_perf = gp.get("domain_performance", {}).get(domain, {})
    common_errors = domain_perf.get("common_errors", [])

    ap_data = _load_anti_patterns()
    relevant_aps = [
        ap for ap in ap_data.get("anti_patterns", [])
        if ap.get("domain") in (domain, "all")
    ]

    elevated = session.get("elevated_checks", [])

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
    session = _load_session()

    if "sub_tasks" not in session:
        session["sub_tasks"] = []
    session["sub_tasks"].append({
        "name": sub_task,
        "status": status,
        "issues": issues,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if status == "issues" and issues:
        # Add to session failures for elevated checking
        if issues not in session.get("current_session_failures", []):
            session["current_session_failures"].append(issues)
        # Extract keywords from issues for elevated checks
        words = re.findall(r'\b\w{4,}\b', issues.lower())
        for w in words[:3]:
            if w not in session.get("elevated_checks", []):
                session["elevated_checks"].append(w)

    _save_session(session)

    completed = sum(1 for s in session["sub_tasks"] if s["status"] == "done")
    total = len(session["sub_tasks"])
    with_issues = sum(1 for s in session["sub_tasks"] if s["status"] == "issues")

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

    Parameters:
      task_id           : The task_id from start_turn()
      output_summary    : Brief summary of what you produced
      checklist_results : Comma-separated results, e.g.:
                          "responsive:pass, accessible:fail:no ARIA labels, error_states:pass"

    Returns structured critique with DELIVER or REFINE recommendation.
    """
    guard_warning = _guard_check()
    session = _load_session()
    task = session.get("active_task", {})
    domain = task.get("domain", "coding")
    elevated = session.get("elevated_checks", [])

    # Parse checklist results
    passes = []
    fails = []
    for item in checklist_results.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)
        item_id = parts[0].strip()
        status = parts[1].strip().lower() if len(parts) > 1 else "pass"
        reason = parts[2].strip() if len(parts) > 2 else ""

        if status == "pass":
            passes.append(item_id)
        else:
            fails.append({"id": item_id, "reason": reason})

    total = len(passes) + len(fails)
    pass_rate = len(passes) / max(total, 1)

    lines = [
        f"[COGNITIVE REFLECT] task: {task_id}",
        f"  Domain: {domain}",
        f"  Output: {output_summary[:150]}",
        f"  Checklist: {len(passes)}/{total} passed ({round(pass_rate * 100)}%)",
        "",
    ]

    if passes:
        lines.append(f"  ✅ PASSED ({len(passes)}): {', '.join(passes)}")

    if fails:
        lines.append(f"\n  ❌ FAILED ({len(fails)}):")
        for f_item in fails:
            elevated_mark = " 🔺" if f_item["id"] in elevated else ""
            lines.append(f"    • {f_item['id']}{elevated_mark}: {f_item['reason'] or 'not addressed'}")

    # Check if any elevated items were missed
    elevated_missed = [e for e in elevated if e not in passes and not any(f["id"] == e for f in fails)]
    if elevated_missed:
        lines.append(f"\n  🔺 Elevated checks NOT addressed: {', '.join(elevated_missed)}")

    # Recommendation
    confidence = round(pass_rate, 2)
    if fails:
        high_severity_fails = [f for f in fails if f["id"] in elevated or f.get("reason")]
        if high_severity_fails:
            lines.append(f"\n  → Recommendation: REFINE (fix {len(fails)} issue(s) before delivering)")
            lines.append(f"  → Confidence: {confidence}")
        else:
            lines.append(f"\n  → Recommendation: REFINE (minor issues, but worth fixing)")
            lines.append(f"  → Confidence: {confidence}")
    else:
        lines.append(f"\n  → Recommendation: DELIVER ✅")
        lines.append(f"  → Confidence: {confidence}")

    # Store reflection data in session for learn()
    session["last_reflection"] = {
        "passes": passes,
        "fails": [f["id"] for f in fails],
        "fail_details": fails,
        "confidence": confidence,
        "pass_rate": pass_rate,
    }
    _save_session(session)

    return "\n".join(lines)


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
    session = _load_session()

    # Parse verification results
    all_pass = True
    issues = []
    items = []
    for item in verification_results.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 1)
        name = parts[0].strip()
        result = parts[1].strip() if len(parts) > 1 else "pass"
        items.append({"name": name, "result": result})
        if "fail" in result.lower() or "error" in result.lower():
            all_pass = False
            issues.append(f"{name}: {result}")

    lines = [f"[COGNITIVE VERIFY] task: {task_id}", ""]

    for v in items:
        mark = "✅" if "pass" in v["result"].lower() or "success" in v["result"].lower() else "❌"
        lines.append(f"  {mark} {v['name']}: {v['result']}")

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
        "items": items,
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
    """Consolidate recent task history and anti-patterns into a background summary."""
    try:
        # Load profile
        profile = _load_profile()
        gp = profile.get("global_profile", {})
        dp = gp.get("domain_performance", {})
        
        # Load recent task history
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
        
        # Load anti-patterns
        ap_data = _load_anti_patterns()
        anti_patterns = ap_data.get("anti_patterns", [])
        
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
                lines.append(f"  - Common Weaknesses: {', '.join(errors[:3])}")
                
        if anti_patterns:
            lines.append("")
            lines.append("**Learned Negative Constraints (Anti-Patterns)**:")
            # Show last 10 anti-patterns
            for ap in anti_patterns[-10:]:
                lines.append(f"- **{ap.get('domain', 'all').upper()}**: Avoid repeating: {ap.get('pattern')}")
                
        if recent_tasks:
            lines.append("")
            lines.append("**Recent Tasks Summary**:")
            # Show last 5 tasks
            for task in recent_tasks[-5:]:
                lines.append(f"- [{task.get('timestamp')[:10]}] {task.get('domain').upper()}: {task.get('outcome').upper()} (Confidence: {task.get('confidence')})")
                
        consolidation_file = os.path.join(_COG_DIR, "soul_consolidation.md")
        with open(consolidation_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass



@mcp.tool()
def learn(task_id: str, outcome: str, confidence_was: float = 0.5, errors_found: str = "") -> str:
    """
    COGNITIVE CORE — Update global profile before delivering final output.

    Call this as the LAST cognitive tool before delivering your response.
    Records the task outcome, updates domain performance, calibrates confidence,
    and writes to persistent task history.

    Parameters:
      task_id        : The task_id from start_turn()
      outcome        : "success", "partial", or "failed"
      confidence_was : Your confidence estimate before execution (0.0-1.0)
      errors_found   : Comma-separated error descriptions found during the task

    Returns profile update confirmation.
    """
    guard_warning = _guard_check()
    session = _load_session()
    task = session.get("active_task", {})
    domain = task.get("domain", "coding")

    profile = _load_profile()
    gp = profile.get("global_profile", {})

    # Update domain performance
    dp = gp.get("domain_performance", {})
    if domain not in dp:
        dp[domain] = {"tasks": 0, "successes": 0, "common_errors": []}
    dp[domain]["tasks"] += 1
    if outcome == "success":
        dp[domain]["successes"] += 1

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

    gp["total_tasks_completed"] = gp.get("total_tasks_completed", 0) + 1
    gp["domain_performance"] = dp
    profile["global_profile"] = gp
    _save_profile(profile)

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
            sum(cal["domains"][d].get("accuracy", 0.5) for d in cal["domains"])
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
    # Write session failures to global profile if they persisted
    for failure in session.get("current_session_failures", []):
        if failure not in dp[domain].get("common_errors", []):
            dp[domain]["common_errors"].append(failure)
            dp[domain]["common_errors"] = dp[domain]["common_errors"][-10:]
    _save_session(session)
    _consolidate_soul()

    # Response
    success_rate = round(dp[domain]["successes"] / max(dp[domain]["tasks"], 1) * 100, 1)
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
    session = _load_session()
    task = session.get("last_task") or session.get("active_task", {})
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

    # Add to anti-patterns (severity: user_caught — highest weight)
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
    # Cap at 50 anti-patterns
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

    # Update session elevated checks
    if error_category and error_category not in session.get("elevated_checks", []):
        session["elevated_checks"].append(error_category)
    # Also add key words from correction
    words = re.findall(r'\b\w{5,}\b', correction.lower())
    for w in words[:2]:
        if w not in session.get("elevated_checks", []):
            session["elevated_checks"].append(w)
    _save_session(session)

    # Update calibration (this task was a failure despite claimed confidence)
    cal = _load_calibration()
    if domain in cal.get("domains", {}):
        cal["domains"][domain]["predictions"].append({
            "stated": 0.9,  # We assumed high confidence since we delivered
            "actual_success": False,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        cal["domains"][domain]["predictions"] = cal["domains"][domain]["predictions"][-50:]
    _save_calibration(cal)
    _consolidate_soul()

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
        f"  → Elevated checks updated for this session.\n"
        f"  → Correction ratio: {ratio}% ({corrections}/{total} tasks)\n"
        f"  → This correction is now permanently part of VoxKage's learning."
    )


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
    elevated = session.get("elevated_checks", [])
    if elevated:
        lines.append(f"    Elevated checks: {', '.join(elevated[:5])}")
    failures = session.get("current_session_failures", [])
    if failures:
        lines.append(f"    Session failures: {', '.join(failures[:3])}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
