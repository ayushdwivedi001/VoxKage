import os
import json
import re
from datetime import datetime, timezone

from .constants import (
    _HISTORY_FILE,
    _ARCHIVED_FILE,
    _COG_DIR,
    _PARENT
)
from .storage import (
    _load_profile,
    _load_anti_patterns,
    _save_anti_patterns,
    _load_archived_anti_patterns,
    parse_timestamp,
    _atomic_write_file,
    _save_json
)


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
        
        workspace_dir = _PARENT
        workspace_agents_path = os.path.join(workspace_dir, ".agents", "AGENTS.md")
        
        _inject_soul_history(global_claude_path, soul_md)
        _inject_soul_history(global_agents_path, soul_md)
        _inject_soul_history(workspace_agents_path, soul_md)
        
    except Exception:
        pass
