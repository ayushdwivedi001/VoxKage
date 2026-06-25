import os
import json
import threading
import uuid
import time as _time
import shutil
from datetime import datetime, timezone

from .constants import (
    _COG_DIR,
    _PROFILE_FILE,
    _ANTI_PATTERNS_FILE,
    _CALIBRATION_FILE,
    _SESSION_FILE,
    _DYNAMIC_RULES_FILE,
    _DYNAMIC_RULES_TEMPLATE,
    _WRITABLE_CHECKLISTS_DIR,
    _CHECKLISTS_DIR,
    _LOCK_FILE,
    _ARCHIVED_FILE,
    _HISTORY_FILE,
    _CODE_CHECKLIST_KEYWORDS,
    _GUARD_WINDOWS,
    _DOMAIN_MISMATCHES_FILE,
    _EVOLVED_RULES_FILE,
    _EVOLVED_RULES_PENDING_FILE,
    _CHECKLISTS_PENDING_FILE
)

_local_lock_depth = threading.local()
_last_start_turn_ts: float = 0.0

def set_last_start_turn_ts(ts: float):
    global _last_start_turn_ts
    _last_start_turn_ts = ts

def get_last_start_turn_ts() -> float:
    return _last_start_turn_ts

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

def _save_checklist(domain: str, items: list):
    path = os.path.join(_WRITABLE_CHECKLISTS_DIR, f"{domain}.json")
    _save_json(path, {"domain": domain, "items": items})

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

def _load_domain_mismatches() -> dict:
    return _load_json(_DOMAIN_MISMATCHES_FILE, lambda: {"mismatches": []})

def _append_domain_mismatch(entry: dict):
    mismatches = _load_domain_mismatches()
    mismatches.setdefault("mismatches", []).append(entry)
    _save_json(_DOMAIN_MISMATCHES_FILE, mismatches)

def _store_pending_checklist_item(domain: str, item: dict):
    data = _load_json(_CHECKLISTS_PENDING_FILE, lambda: {"pending": []})
    pending_list = data.setdefault("pending", [])
    
    found = False
    for p in pending_list:
        if p.get("domain") == domain and p.get("id") == item["id"]:
            p["observation_count"] = p.get("observation_count", 1) + 1
            found = True
            break
            
    if not found:
        item = dict(item)
        item["domain"] = domain
        item["observation_count"] = 1
        pending_list.append(item)
        
    _save_json(_CHECKLISTS_PENDING_FILE, data)

def _promote_pending_checklist_items() -> int:
    data = _load_json(_CHECKLISTS_PENDING_FILE, lambda: {"pending": []})
    pending_list = data.setdefault("pending", [])
    
    still_pending = []
    promoted_count = 0
    
    # Group by domain
    by_domain = {}
    for p in pending_list:
        if p.get("observation_count", 0) >= 2:
            domain = p.get("domain")
            by_domain.setdefault(domain, []).append(p)
            promoted_count += 1
        else:
            still_pending.append(p)
            
    for domain, items in by_domain.items():
        checklist_items = _load_checklist(domain)
        existing_ids = {item["id"] for item in checklist_items}
        for item in items:
            if item["id"] not in existing_ids:
                checklist_items.append({
                    "id": item["id"],
                    "check": item["check"],
                    "severity": item.get("severity", "medium"),
                    "source": item.get("source", "learn_evolution")
                })
        _save_checklist(domain, checklist_items)
        
    data["pending"] = still_pending
    _save_json(_CHECKLISTS_PENDING_FILE, data)
    return promoted_count

def _load_recent_task_history(limit: int = 50) -> list:
    """Load up to limit recent task summaries from recent.jsonl, newest first."""
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
        # recent.jsonl is appended to, so last items are newest.
        # Reverse to get newest first.
        entries.reverse()
        return entries[:limit]
    finally:
        _release_lock()

def _store_pending_evolved_rule(rule: dict):
    data = _load_json(_EVOLVED_RULES_PENDING_FILE, lambda: {"pending": []})
    pending_list = data.setdefault("pending", [])
    
    found = False
    for p in pending_list:
        if p.get("proposed_rule") == rule.get("proposed_rule") and p.get("domain") == rule.get("domain"):
            p["observation_count"] = p.get("observation_count", 1) + 1
            found = True
            break
            
    if not found:
        rule = dict(rule)
        if "observation_count" not in rule:
            rule["observation_count"] = 1
        pending_list.append(rule)
        
    _save_json(_EVOLVED_RULES_PENDING_FILE, data)

def _load_evolved_rules() -> dict:
    return _load_json(_EVOLVED_RULES_FILE, lambda: {"rules": []})

def _save_evolved_rules(rules: dict):
    _save_json(_EVOLVED_RULES_FILE, rules)

