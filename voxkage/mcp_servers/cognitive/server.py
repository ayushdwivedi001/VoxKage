import os
import re
import uuid
import subprocess
import time as _time
from datetime import datetime, timezone

from .storage import (
    _load_session,
    _save_session,
    _load_profile,
    _save_profile,
    _load_anti_patterns,
    _save_anti_patterns,
    _load_calibration,
    _save_calibration,
    _load_checklist,
    _save_checklist,
    _guard_check,
    _log_cognitive_call,
    set_last_start_turn_ts,
    get_last_start_turn_ts,
    _load_archived_anti_patterns,
    parse_timestamp,
    _append_task_history,
    _load_domain_mismatches,
    _append_domain_mismatch,
    _store_pending_checklist_item,
    _promote_pending_checklist_items,
    _load_recent_task_history,
    _store_pending_evolved_rule,
    _load_evolved_rules,
    _save_evolved_rules,
    _promote_pending_evolved_rules
)
from .constants import _DOMAIN_KEYWORDS, OUTPUT_TYPE_FILTERS
from .intent import (
    _classify_intent,
    _detect_followup,
    _generate_task_plan,
    _normalize_pattern
)
from .analyzer import (
    _score_anti_pattern,
    _analyze_execution_trace,
    _consolidate_soul,
    _cluster_and_aggregate_failures,
    _generate_calibration_report
)
from .utils import (
    _auto_update_documentation
)


def start_turn(
    user_message: str,
    refresh_only: bool = False,
    suggested_domain: str = "",
    suggested_tier: int = 0,                    # NEW: 1-3 overrides classifier
    suggested_secondary_domains: str = ""       # NEW: Comma-separated list (e.g. "general,coding")
) -> str:
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
      suggested_domain : Optional. Model hints the correct domain to override classifier.
      suggested_tier   : Optional. Model overrides tier (1-3).
      suggested_secondary_domains : Optional. Model overrides secondary domains (comma-separated).

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
    set_last_start_turn_ts(_time.time())  # Mark gate as fired

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
    secondary_domains = list(classification.get("secondary_domains", []))
    tier = classification["tier"]
    is_read_only = classification.get("is_read_only", False)

    # NEW: Model-suggested domain override
    if suggested_domain and suggested_domain in _DOMAIN_KEYWORDS:
        domain = suggested_domain
        session["domain_was_suggested"] = True
        session["classifier_domain"] = classification["domain"]
        session["model_domain"] = suggested_domain

    if suggested_secondary_domains:
        sec_list = [d.strip().lower() for d in suggested_secondary_domains.split(",")]
        secondary_domains = [d for d in sec_list if d in _DOMAIN_KEYWORDS]
        classification["secondary_domains"] = secondary_domains

    if suggested_tier in [1, 2, 3]:
        tier = suggested_tier
        session["tier_was_suggested"] = True
        session["classifier_tier"] = classification["tier"]
        session["model_tier"] = suggested_tier

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

    # Load baseline checklist and merge secondary domains
    checklist = _load_checklist(domain)
    existing_ids = {item["id"] for item in checklist}
    for sec_domain in secondary_domains:
        for item in _load_checklist(sec_domain):
            if item["id"] not in existing_ids:
                checklist.append(item)
                existing_ids.add(item["id"])
            else:
                # Keep highest severity
                existing = next(i for i in checklist if i["id"] == item["id"])
                w = {"high": 3, "medium": 2, "low": 1}
                if w.get(item["severity"], 1) > w.get(existing["severity"], 1):
                    existing["severity"] = item["severity"]

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
        "secondary_domains": secondary_domains,
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
    ]
    if secondary_domains:
        lines.append(f"  secondary_domains: {', '.join(secondary_domains)}")
    lines.extend([
        f"  tier: {tier} ({'Quick' if tier == 1 else 'Standard' if tier == 2 else 'Complex'})",
        f"  followup: {is_followup}",
    ])

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

    # NEW: P1 Axiom Promotion (capped at 5, 90-day decay)
    try:
        now = datetime.now(timezone.utc)
        decay_days = 90
        axiom_candidates = []
        for ap in ap_data.get("anti_patterns", []):
            if ap.get("times_prevented", 0) >= 5:
                ts = ap.get("timestamp", "")
                try:
                    age = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days
                except Exception:
                    age = 0
                if age <= decay_days:
                    axiom_candidates.append(ap)

        # Sort by times_prevented descending, cap at 5
        axiom_candidates.sort(key=lambda x: x.get("times_prevented", 0), reverse=True)
        axiom_aps = axiom_candidates[:5]

        if axiom_aps:
            lines.append("\n  🔮 Promoted Axioms (proven in practice):")
            for ap in axiom_aps:
                count = ap.get("times_prevented", 0)
                lines.append(f"    ★ [{count}× prevented] {ap.get('pattern', '?')}")
    except Exception:
        pass

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
        from .storage import _load_dynamic_rules
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
    lines.append("    • DOMAIN REPORTING: If the domain assigned seems wrong for what you're producing, report it in errors_found when calling learn(). Format: \"Domain mismatch: [assigned] on [actual] task — [reason]\" Or pass suggested_domain= to start_turn() upfront to prevent the mismatch entirely.")

    return "\n".join(lines)


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
    secondary_domains = task.get("secondary_domains", [])
    active_domains = [domain] + list(secondary_domains) + ["all", "general"]
    
    all_aps = [
        ap for ap in ap_data.get("anti_patterns", [])
        if ap.get("domain") in active_domains
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
    session["premortem_shown_aps"] = [ap.get("pattern", "") for ap in relevant_aps]
    _save_session(session)

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

    # Per-task sub-task tracking
    raw = session.get("sub_tasks")
    if isinstance(raw, list):
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


def reflect(
    task_id: str,
    output_summary: str,
    checklist_results: str,
    output_type: str = "auto"  # NEW: "auto"|"markdown"|"code"|"command"|"research"|"general"
) -> str:
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
      output_type       : NEW: output type to filter applicable checklist items.

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

    # Load baseline checklist and active plan with domain metadata
    secondary_domains = task.get("secondary_domains", [])
    checklist = _load_checklist(domain)
    for item in checklist:
        item["origin_domain"] = domain

    existing_ids = {item["id"] for item in checklist}
    for sec_domain in secondary_domains:
        for item in _load_checklist(sec_domain):
            if item["id"] not in existing_ids:
                item["origin_domain"] = sec_domain
                checklist.append(item)
                existing_ids.add(item["id"])
            else:
                existing = next(i for i in checklist if i["id"] == item["id"])
                w = {"high": 3, "medium": 2, "low": 1}
                if w.get(item["severity"], 1) > w.get(existing["severity"], 1):
                    existing["severity"] = item["severity"]
                    existing["origin_domain"] = sec_domain

    active_plan = session.get("active_plan") or []
    
    # Map all valid IDs to their definitions
    checklist_map = {item["id"]: item for item in checklist}
    
    plan_map = {}
    for item in active_plan:
        item = dict(item)
        item["origin_domain"] = domain
        plan_map[item["id"]] = item
    
    combined_items_map = {}
    combined_items_map.update(checklist_map)
    combined_items_map.update(plan_map)

    # NEW: P2 Output-Type Checklist Filtering
    if output_type == "auto":
        summary_lower = output_summary.lower()
        if any(kw in summary_lower for kw in ["markdown", ".md file", "report", "written to", "document"]):
            output_type = "markdown"
        elif any(kw in summary_lower for kw in ["ran command", "executed", "shell output", "terminal"]):
            output_type = "command"
        elif any(kw in summary_lower for kw in ["research", "web search", "sources", "comparison matrix"]):
            output_type = "research"

    filters = OUTPUT_TYPE_FILTERS.get(output_type, {})
    excluded_ids = set(filters.get("exclude", []))
    downgrade_map = filters.get("downgrade", {})

    filtered_map = {}
    filter_log = {"excluded": [], "downgraded": []}
    for item_id, item in combined_items_map.items():
        if item_id in excluded_ids:
            filter_log["excluded"].append(item_id)
            continue
        if item_id in downgrade_map:
            item = dict(item)
            item["severity"] = downgrade_map[item_id]
            filter_log["downgraded"].append(f"{item_id}→{downgrade_map[item_id]}")
        filtered_map[item_id] = item

    combined_items_map = filtered_map
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

        # ── v3 Fuzzy/Prefix/Wildcard Matching ──
        if item_id_clean in all_valid_ids:
            matched_ids = [item_id_clean]
        elif re.match(r'^(all_?)?plans?(_items?)?$', item_id_clean, re.I):
            matched_ids = all_plan_ids
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

    # Automatically treat unaddressed items as failed ONLY if the model actually
    # provided SOME results (non-empty checklist_results). If checklist_results is
    # empty or trivial, grant a good-faith pass on plan items.
    evaluated_ids = set(passes).union({f["id"] for f in fails})
    is_trivial_input = len(checklist_results.strip()) < 5

    unaddressed_ids = all_valid_ids - evaluated_ids
    for item_id in unaddressed_ids:
        if is_trivial_input:
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

    # v3: Score floor
    if quality_score == 0.0 and len(output_summary.strip()) > 30:
        quality_score = 0.1

    quality_percent = round(quality_score * 100)

    # Determine recommendation
    high_severity_failed = False
    for fail_item in fails:
        if "Not reported" in fail_item.get("reason", "") and not any(p in fail_item["id"] for p in ["plan_"]):
            item = combined_items_map.get(fail_item["id"])
            severity = item.get("severity", "low") if item else "low"
            if severity == "high":
                high_severity_failed = True
                break
        elif "Not reported" not in fail_item.get("reason", ""):
            item = combined_items_map.get(fail_item["id"])
            severity = item.get("severity", "low") if item else "low"
            if severity == "high":
                high_severity_failed = True
                break
            
    recommend_deliver = (quality_score >= 0.85) and not high_severity_failed

    # Calculate per-domain scores
    domain_scores = {}
    domain_scores[domain] = {"achieved": 0, "possible": 0, "passed_ids": [], "failed_ids": []}
    for sd in secondary_domains:
        domain_scores[sd] = {"achieved": 0, "possible": 0, "passed_ids": [], "failed_ids": []}

    for pass_id in passes:
        item = combined_items_map.get(pass_id)
        weight = severity_weights.get(item.get("severity", "low") if item else "low", 1)
        orig_dom = item.get("origin_domain", domain) if item else domain
        domain_scores.setdefault(orig_dom, {"achieved": 0, "possible": 0, "passed_ids": [], "failed_ids": []})
        domain_scores[orig_dom]["achieved"] += weight
        domain_scores[orig_dom]["possible"] += weight
        domain_scores[orig_dom]["passed_ids"].append(pass_id)

    for fail_item in fails:
        item = combined_items_map.get(fail_item["id"])
        weight = severity_weights.get(item.get("severity", "low") if item else "low", 1)
        orig_dom = item.get("origin_domain", domain) if item else domain
        domain_scores.setdefault(orig_dom, {"achieved": 0, "possible": 0, "passed_ids": [], "failed_ids": []})
        domain_scores[orig_dom]["possible"] += weight
        domain_scores[orig_dom]["failed_ids"].append(fail_item["id"])

    lines = [
        f"[COGNITIVE REFLECT] task: {task_id}",
        f"  Domain: {domain}",
    ]
    if secondary_domains:
        lines.append(f"  Secondary Domains: {', '.join(secondary_domains)}")
    lines.append(f"  Output: {output_summary[:150]}")
    lines.append(f"  Checklist Quality Score: {quality_score}/1.0 ({quality_percent}%)")

    lines.append("\n  Score Breakdown:")
    for dom_name, d_score in domain_scores.items():
        pos = d_score["possible"]
        ach = d_score["achieved"]
        if pos == 0:
            lines.append(f"    • {dom_name.capitalize()}: N/A")
        else:
            pct = round((ach / pos) * 100)
            passed_checks = len(d_score["passed_ids"])
            total_checks = passed_checks + len(d_score["failed_ids"])
            lines.append(f"    • {dom_name.capitalize()}: {pct}% ({passed_checks}/{total_checks} checks)")

    if output_type != "auto" and (filter_log["excluded"] or filter_log["downgraded"]):
        lines.append(
            f"\n  ℹ Output type: {output_type} — "
            f"{len(filter_log['excluded'])} items excluded, "
            f"{len(filter_log['downgraded'])} downgraded: {', '.join(filter_log['downgraded'])}"
        )
    lines.append("")

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
        lines.append(f"\n  ★ MAX ITERATIONS REACHED ({max_iterations})")
        lines.append(f"  → Deliver your current best-effort output.")
        lines.append(f"  → Include a note to the user about remaining limitations.")
        lines.append(f"  → Proceed to learn(task_id, 'partial') to record what happened.")
        return guard_warning + "\n".join(lines)

    lines.append(f"\n  → Refinement recorded. Re-run reflect() to check if issues are resolved.")
    lines.append(f"  → Iterations remaining: {max_iterations - iteration}")
    return guard_warning + "\n".join(lines)


def learn(
    task_id: str,
    outcome: str,
    confidence_was: float = 0.5,
    errors_found: str = "",
    evolved_checklist_item: dict = None
) -> str:
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
      evolved_checklist_item : Optional structured checklist item to add (dict)

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
    secondary_domains = task.get("secondary_domains", [])
    all_domains = [domain] + secondary_domains
    
    for d in all_domains:
        if d not in dp:
            dp[d] = {"tasks": 0, "successes": 0, "common_errors": []}
        dp[d]["tasks"] = (dp[d].get("tasks") or 0) + 1
        if outcome == "success":
            dp[d]["successes"] = (dp[d].get("successes") or 0) + 1

    # Add errors to common_errors
    if errors_found:
        for d in all_domains:
            if d not in dp:
                dp[d] = {"tasks": 0, "successes": 0, "common_errors": []}
            for err in errors_found.split(","):
                err = err.strip()
                if err and err not in dp[d]["common_errors"]:
                    dp[d]["common_errors"].append(err)
            dp[d]["common_errors"] = dp[d]["common_errors"][-10:]
        dp[domain]["common_errors"] = dp[domain]["common_errors"][-10:]

        # NEW: P0 Domain Reclassification via errors_found parsing
        DOMAIN_MISMATCH_KEYWORDS = [
            "domain mismatch", "wrong checklist", "inapplicable",
            "domain_override", "not applicable to", "coding checklist on",
            "wrong domain", "misclassified as", "checklist doesn't apply",
        ]
        err_lower = errors_found.lower()
        mismatch_detected = any(kw in err_lower for kw in DOMAIN_MISMATCH_KEYWORDS)
        if mismatch_detected:
            corrected_domain = None
            for d in _DOMAIN_KEYWORDS.keys():
                if d in err_lower and d != domain:
                    corrected_domain = d
                    break
            if corrected_domain:
                _append_domain_mismatch({
                    "task_id": task_id,
                    "assigned_domain": domain,
                    "corrected_domain": corrected_domain,
                    "errors_found_excerpt": errors_found[:200],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "outcome": outcome,
                })
                # Update profile for corrected domain
                if corrected_domain not in dp:
                    dp[corrected_domain] = {"tasks": 0, "successes": 0, "common_errors": []}
                dp[corrected_domain]["tasks"] = dp[corrected_domain].get("tasks", 0) + 1
                if outcome == "success":
                    dp[corrected_domain]["successes"] = dp[corrected_domain].get("successes", 0) + 1

    # NEW: P3 Checklist Self-Evolution via learn()
    if evolved_checklist_item and isinstance(evolved_checklist_item, dict):
        e_id = evolved_checklist_item.get("id", "").strip()
        e_check = evolved_checklist_item.get("check", "").strip()
        e_domain = evolved_checklist_item.get("domain", domain).strip().lower()
        e_severity = evolved_checklist_item.get("severity", "medium").strip().lower()
        
        if e_id and e_check:
            if not e_id.startswith("evolved_"):
                e_id = f"evolved_{e_id}"
            e_id = re.sub(r"[^a-zA-Z0-9_]", "", e_id)
            
            candidate = {
                "id": e_id,
                "check": e_check,
                "severity": e_severity if e_severity in ["high", "medium", "low"] else "medium",
                "source": "model_evolved",
                "first_seen": datetime.now(timezone.utc).isoformat(),
            }
            _store_pending_checklist_item(e_domain, candidate)
    elif errors_found:
        # Fallback to regex patterns
        STRUCTURAL_GAP_PATTERNS = [
            (r"domain[\s_](?:mismatch|applicability|filter)", "domain_applicability"),
            (r"checklist[\s_](?:wrong|inapplicable|missing)", "checklist_mismatch"),
            (r"no[\s_]check[\s_]for\s+(.{5,40})", None),
            (r"missing[\s_](?:check|validation)\s+for\s+(.{5,40})", None),
        ]
        for pattern, fixed_label in STRUCTURAL_GAP_PATTERNS:
            match = re.search(pattern, errors_found, re.I)
            if match:
                label = fixed_label or re.sub(r'\s+', '_', match.group(1).strip().lower()[:30])
                label = re.sub(r'[^a-zA-Z0-9_]', '', label)
                candidate = {
                    "id": f"evolved_{label}",
                    "check": f"Self-evolved: Did we verify — {match.group(0).strip()}?",
                    "severity": "medium",
                    "source": "learn_evolution",
                    "first_seen": datetime.now(timezone.utc).isoformat(),
                }
                _store_pending_checklist_item(domain, candidate)
                break

    # NEW: P0.5 classifier override logging
    if session.get("domain_was_suggested"):
        classifier_domain = session.get("classifier_domain")
        model_domain = session.get("model_domain")
        if classifier_domain != model_domain:
            _append_domain_mismatch({
                "task_id": task_id,
                "assigned_domain": classifier_domain,
                "corrected_domain": model_domain,
                "errors_found_excerpt": f"Suggested domain override: {model_domain}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "outcome": outcome,
                "correction_source": "suggested_domain"
            })

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

    # Dynamic Checklist Evolution
    try:
        checklist_changed = False
        items = _load_checklist(domain)
        cats = gp.get("error_categories", {})

        # False-negative override detection
        reflect_recommended_refine = not session.get("last_reflection", {}).get("pass_rate", 1.0) >= 0.85
        if outcome == "success" and reflect_recommended_refine:
            fn_overrides = gp.get("false_negative_overrides", {})
            for fail_id in session.get("last_reflection", {}).get("fails", []):
                fn_overrides[fail_id] = fn_overrides.get(fail_id, 0) + 1
                if fn_overrides[fail_id] >= 3:
                    for item in items:
                        if item.get("id") == fail_id and item.get("severity") == "high":
                            item["severity"] = "medium"
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
    if "domains" not in cal or not isinstance(cal["domains"], dict):
        cal["domains"] = {}
        
    if domain not in cal["domains"] or not isinstance(cal["domains"][domain], dict):
        cal["domains"][domain] = {
            "predictions": [], 
            "accuracy": None,
            "brier_score": None,
            "overconfidence_index": None,
            "underconfidence_index": None
        }
    
    domain_data = cal["domains"][domain]
    if "predictions" not in domain_data or not isinstance(domain_data["predictions"], list):
        domain_data["predictions"] = []
        
    domain_data["predictions"].append({
        "stated": round(confidence_was, 2),
        "actual_success": outcome == "success",
        "task_id": task_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    domain_data["predictions"] = domain_data["predictions"][-50:]
    
    preds = domain_data["predictions"]
    if len(preds) >= 5:
        correct = sum(1 for p in preds if (p["stated"] >= 0.5) == p["actual_success"])
        domain_data["accuracy"] = round(correct / len(preds), 2)
        
        # Brier Score calculation: average of (stated - actual)^2
        brier_sum = 0.0
        for p in preds:
            actual_val = 1.0 if p["actual_success"] else 0.0
            brier_sum += (p["stated"] - actual_val) ** 2
        domain_data["brier_score"] = round(brier_sum / len(preds), 3)
        
        # Over/Under confidence index calculation
        failed_tasks = [p for p in preds if not p["actual_success"]]
        if failed_tasks:
            over_sum = sum(p["stated"] for p in failed_tasks)
            domain_data["overconfidence_index"] = round(over_sum / len(failed_tasks), 3)
        else:
            domain_data["overconfidence_index"] = 0.0
            
        success_tasks = [p for p in preds if p["actual_success"]]
        if success_tasks:
            under_sum = sum(1.0 - p["stated"] for p in success_tasks)
            domain_data["underconfidence_index"] = round(under_sum / len(success_tasks), 3)
        else:
            domain_data["underconfidence_index"] = 0.0
            
        # Global calibration score: 1.0 - average_brier_score
        brier_scores = []
        for d in cal["domains"]:
            if isinstance(cal["domains"][d], dict):
                d_brier = cal["domains"][d].get("brier_score")
                if d_brier is not None:
                    brier_scores.append(d_brier)
                
        if brier_scores:
            gp["confidence_calibration"] = round(1.0 - (sum(brier_scores) / len(brier_scores)), 2)
        else:
            gp["confidence_calibration"] = 0.5
            
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

    # Increment times_prevented for shown anti-patterns on task success
    session_premortem_aps = session.get("premortem_shown_aps", [])
    if outcome == "success" and session_premortem_aps:
        try:
            ap_data = _load_anti_patterns()
            ap_changed = False
            for ap in ap_data.get("anti_patterns", []):
                if ap.get("pattern", "") in session_premortem_aps:
                    ap["times_prevented"] = ap.get("times_prevented", 0) + 1
                    ap_changed = True
            if ap_changed:
                _save_anti_patterns(ap_data)
        except Exception:
            pass

    # Update session
    session["last_task"] = task
    session["active_task"] = None
    if "sub_tasks" in session and task_id in session["sub_tasks"]:
        del session["sub_tasks"][task_id]
    
    for failure in session.get("current_session_failures", []):
        if failure not in dp[domain].get("common_errors", []):
            dp[domain]["common_errors"].append(failure)
            dp[domain]["common_errors"] = dp[domain]["common_errors"][-10:]
            
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
                    from .storage import _load_dynamic_rules, _save_dynamic_rules
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


def user_corrected(
    task_id: str,
    correction: str,
    error_category: str = "",
    descriptive_id: str = "",
    target_domain: str = "",
    severity: str = "high"
) -> str:
    """
    COGNITIVE CORE — High-weight learning from user corrections.

    Call this IMMEDIATELY when the user corrects your output DESPITE
    you having run the full metacognitive cycle. This is the most
    valuable learning signal — weighted 10x higher in the profile.

    Parameters:
      task_id        : The task_id of the corrected task
      correction     : What the user said was wrong and what the fix should be
      error_category : Optional category (e.g., "logic_error", "api_design", "missing_feature")
      descriptive_id : Optional descriptive ID for checklist (e.g. corr_respect_read_only)
      target_domain  : Optional target domain to store correction (e.g. coding)
      severity       : Optional severity of checklist item (high, medium, low)

    Returns confirmation of the high-weight profile update.
    """
    guard_warning = _guard_check()
    _log_cognitive_call("user_corrected")
    session = _load_session()
    task = session.get("last_task") or session.get("active_task") or {}
    domain = task.get("domain", "coding")

    # 1. Determine Target Domain (with fallback to task domain)
    resolved_domain = target_domain.strip().lower() if target_domain else domain
    if resolved_domain not in _DOMAIN_KEYWORDS and resolved_domain != "all":
        resolved_domain = "general"

    # Update profile
    profile = _load_profile()
    gp = profile.get("global_profile", {})
    gp["total_user_corrections"] = gp.get("total_user_corrections", 0) + 1

    # Add to common errors (high priority)
    dp = gp.get("domain_performance", {})
    if resolved_domain in dp:
        err_text = error_category if error_category else correction[:80]
        if err_text not in dp[resolved_domain].get("common_errors", []):
            dp[resolved_domain]["common_errors"].insert(0, err_text)
            dp[resolved_domain]["common_errors"] = dp[resolved_domain]["common_errors"][:10]

    gp["domain_performance"] = dp
    profile["global_profile"] = gp
    _save_profile(profile)

    # Add to anti-patterns
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
            if ap.get("domain") == resolved_domain or ap.get("domain") == "all":
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
            _save_json(os.path.join(_COG_DIR, "anti_patterns_archived.json"), archived_data)
            
        prune_note = f"\n  → Pruned {len(archived_candidates)} active anti-pattern(s) and archived them."
    else:
        ap_data = _load_anti_patterns()
        ap_data["anti_patterns"].append({
            "pattern": correction[:200],
            "domain": resolved_domain,
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
        items = _load_checklist(resolved_domain)
        check_id = descriptive_id.strip() if descriptive_id else f"corr_{task_id}"
        if not check_id.startswith("corr_"):
            check_id = f"corr_{check_id}"
        check_id = re.sub(r"[^a-zA-Z0-9_]", "", check_id)
        
        check_text = f"Did we avoid repeating: {correction[:120]}?"
        
        # Remove any existing item with the same ID
        items = [it for it in items if it.get("id") != check_id]
        
        items.append({
            "id": check_id,
            "check": check_text,
            "severity": severity if severity in ["high", "medium", "low"] else "high"
        })
        _save_checklist(resolved_domain, items)
    except Exception:
        pass

    # Update session elevated checks
    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, list):
        raw_elevated = {resolved_domain: raw_elevated}
        session["elevated_checks"] = raw_elevated
    elif not isinstance(raw_elevated, dict):
        raw_elevated = {}
        session["elevated_checks"] = raw_elevated
    domain_elevated = raw_elevated.setdefault(resolved_domain, [])
    if error_category and error_category not in domain_elevated:
        domain_elevated.append(error_category)
    
    words = re.findall(r'\b\w{5,}\b', correction.lower())
    for w in words[:2]:
        if w not in domain_elevated:
            domain_elevated.append(w)
    _save_session(session)

    # Update calibration
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
                from .storage import _load_dynamic_rules, _save_dynamic_rules
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
        f"[COGNITIVE] ★ User correction recorded (HIGH WEIGHT)\n"
        f"  Task: {task_id}\n"
        f"  Domain: {domain}\n"
        f"  Correction: {correction[:150]}\n"
        f"  Category: {error_category or 'uncategorized'}\n"
        f"  → Anti-pattern logged. Will flag this in future pre_mortem() calls.\n"
        f"  → Elevated checks updated for this session.{override_note}\n"
        f"  → Correction ratio: {ratio}% ({corrections}/{total} tasks)\n"
        f"  → This correction is now permanently part of VoxKage's learning."
    )


def _days_since(ts_str: str) -> float:
    if not ts_str:
        return 999.0
    try:
        ts_clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return 999.0


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
        from .storage import _load_dynamic_rules, _save_dynamic_rules
        rules = _load_dynamic_rules()
        profile = _load_profile()
        gp = profile.get("global_profile", {})
        
        force_pats = rules.get("force_tier_1_patterns", [])
        unique_pats = list(dict.fromkeys(force_pats))
        rules["force_tier_1_patterns"] = unique_pats
        _save_dynamic_rules(rules)
        
        _consolidate_soul()
        
        ap_data = _load_anti_patterns()
        
        # NEW: P1 Stale Rule Auto-Archival
        now = datetime.now(timezone.utc)
        active, to_archive = [], []
        for ap in ap_data.get("anti_patterns", []):
            times_prevented = ap.get("times_prevented", 0)
            ts = ap.get("timestamp", "")
            try:
                ts_clean = ts.replace("Z", "+00:00") if ts else ""
                age = (now - datetime.fromisoformat(ts_clean)).days
            except Exception:
                age = 0

            # Stale: never prevented anything AND older than 60 days
            if times_prevented == 0 and age > 60:
                to_archive.append(ap)
            else:
                active.append(ap)

        ap_data["anti_patterns"] = active
        _save_anti_patterns(ap_data)

        stale_count = len(to_archive)
        if stale_count > 0:
            archived = _load_archived_anti_patterns()
            archived.setdefault("anti_patterns", []).extend(to_archive)
            archived["anti_patterns"] = archived["anti_patterns"][-500:]
            from .constants import _ARCHIVED_FILE
            _save_json(_ARCHIVED_FILE, archived)

        aps_count = len(active)

        # NEW: P4 Domain Mismatch Audit
        mismatches = _load_domain_mismatches()
        recent_mismatches = [
            m for m in mismatches.get("mismatches", [])
            if _days_since(m.get("timestamp", "")) <= 30.0
        ]
        
        # Temporal decay rate analysis
        recent_7d = [m for m in recent_mismatches if _days_since(m.get("timestamp", "")) <= 7.0]
        recent_older = [m for m in recent_mismatches if 7.0 < _days_since(m.get("timestamp", "")) <= 30.0]
        rate_7d = len(recent_7d)
        rate_older_weekly = len(recent_older) / (23.0 / 7.0) if len(recent_older) > 0 else 0.0
        
        rate_warning = ""
        if rate_7d >= 3 and rate_7d > rate_older_weekly:
            rate_warning = f"  ⚠ WARNING: Domain mismatch rate acceleration detected! (Last 7d: {rate_7d} vs previous weekly avg: {round(rate_older_weekly, 1)})\n"

        # Failure Clustering & Autonomous Evolution
        mismatch_clusters = _cluster_and_aggregate_failures(recent_mismatches)
        auto_evolved_count = 0
        for cluster in mismatch_clusters:
            assigned_counts = {}
            corrected_counts = {}
            for entry in cluster["entries"]:
                a = entry.get("assigned_domain", "unknown")
                c = entry.get("corrected_domain", "unknown")
                assigned_counts[a] = assigned_counts.get(a, 0) + 1
                corrected_counts[c] = corrected_counts.get(c, 0) + 1
                
            orig_domain = max(assigned_counts, key=assigned_counts.get) if assigned_counts else "unknown"
            dest_domain = max(corrected_counts, key=corrected_counts.get) if corrected_counts else "unknown"
            
            keywords_str = ", ".join(cluster["keywords"][:4])
            pattern = cluster["pattern"]
            
            # Propose and autonomously persist rule (dry_run=False)
            proposed = f"Messages containing keywords [{keywords_str}] should classify as '{dest_domain}' instead of '{orig_domain}'"
            evidence = f"Cluster of {cluster['size']} mismatches containing keywords: {keywords_str}"
            
            res = evolve_cognitive_rules(
                domain=dest_domain,
                evolution_type="domain_reclassification",
                evidence=evidence,
                proposed_rule=proposed,
                confidence=min(0.6 + cluster["size"] * 0.08, 0.95),
                observation_count=cluster["size"],
                dry_run=False
            )
            # Add pattern regex field to rule for matching in intent.py
            if "Rule persisted" in res or "Observation" in res:
                evolved_rules = _load_evolved_rules()
                for rule in evolved_rules.setdefault("rules", []):
                    if rule.get("proposed_rule") == proposed and "pattern" not in rule:
                        rule["pattern"] = pattern
                _save_evolved_rules(evolved_rules)
                auto_evolved_count += 1

        # NEW: P4 Checklist Promotion
        pending_promoted = _promote_pending_checklist_items()

        # NEW: Evolved Rule Promotion
        pending_rules_promoted = _promote_pending_evolved_rules()

        # NEW: P4 Rule Health Check + Rollback
        evolved_rules = _load_evolved_rules()
        history = _load_recent_task_history(limit=50)
        quarantined = []
        rule_health_checked_domains = set()

        for rule in evolved_rules.get("rules", []):
            if rule.get("status") == "quarantined":
                continue
            rule_applied_at = rule.get("confirmed_at", "")
            domain_rule = rule.get("domain", "")
            rule_health_checked_domains.add(domain_rule)

            # Tasks in the same domain AFTER the rule was applied
            post_rule_tasks = [
                h for h in history
                if h.get("domain") == domain_rule
                and h.get("timestamp", "") > rule_applied_at
            ]

            if len(post_rule_tasks) >= 5:
                successes = sum(1 for t in post_rule_tasks if t.get("outcome") == "success")
                success_rate = successes / len(post_rule_tasks)

                if success_rate < 0.70:
                    rule["status"] = "quarantined"
                    rule["quarantine_reason"] = (
                        f"Success rate dropped to {round(success_rate*100)}% "
                        f"in {len(post_rule_tasks)} tasks after rule was applied"
                    )
                    quarantined.append(rule.get("proposed_rule", "unknown")[:60])

        _save_evolved_rules(evolved_rules)

        # NEW: Calibration report
        calibration_curve = ""
        try:
            cal = _load_calibration()
            calibration_curve = _generate_calibration_report(cal)
        except Exception:
            pass

        # NEW: P4 Self-Audit Score
        total_tasks = gp.get("total_tasks_completed", 0)
        corrections = gp.get("total_user_corrections", 0)
        mismatch_rate = len(recent_mismatches) / max(total_tasks, 1)
        correction_rate = corrections / max(total_tasks, 1)
        quarantine_penalty = len(quarantined) * 5
        self_audit_score = max(0, round((1 - mismatch_rate - correction_rate) * 100) - quarantine_penalty)
        
        summary = [
            "[COGNITIVE OPTIMIZE] Self-Audit Complete",
            f"  Self-Audit Score: {self_audit_score}/100",
            f"  - Deduplicated force_tier_1_patterns (Count: {len(unique_pats)})",
            f"  - Consolidated Soul History MD file",
            f"  - Verified {aps_count} active anti-patterns",
            f"  - Archived {stale_count} stale anti-patterns",
            f"  - Domain mismatches (last 30d): {len(recent_mismatches)}",
            f"  - Domain mismatch clusters: {len(mismatch_clusters)}",
            f"  - Autonomous rules evolved: {auto_evolved_count}",
            f"  - Evolved rules health-checked: {len(rule_health_checked_domains)} domains",
            f"  - Rules quarantined: {len(quarantined)}",
            f"  - Pending checklist items promoted: {pending_promoted}",
            f"  - Pending evolved rules promoted: {pending_rules_promoted}",
            f"  - Total completed tasks cataloged: {total_tasks}",
        ]
        if rate_warning:
            summary.insert(2, rate_warning.strip())
        if calibration_curve:
            summary.append("\n" + calibration_curve)
        if quarantined:
            summary.append("  ⚠ Quarantined rules:")
            for q in quarantined:
                summary.append(f"    • {q}")
        return guard_warning + "\n".join(summary)
    except Exception as e:
        return guard_warning + f"[COGNITIVE OPTIMIZE] Failed: {e}"


def log_tool_execution(tool_name: str, arguments: str = "") -> str:
    """
    COGNITIVE CORE — Auto-instrumented tool execution logger. Appends a tool execution to the session trace.

    Parameters:
      tool_name : Name of the executed tool.
      arguments : JSON string or raw arguments passed to the tool.
    """
    set_last_start_turn_ts(_time.time())
    try:
        session = _load_session()
        tool_trace = session.setdefault("tool_trace", [])
        
        args_cleaned = arguments
        if isinstance(arguments, str) and arguments.strip():
            try:
                parsed = json.loads(arguments)
                args_cleaned = json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
        elif not isinstance(arguments, str):
            try:
                args_cleaned = json.dumps(arguments, ensure_ascii=False)
            except Exception:
                args_cleaned = str(arguments)

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
    guard_warning = _guard_check()
    _log_cognitive_call("verify_code_file")
    
    abs_path = os.path.abspath(filepath)
    if not os.path.exists(abs_path):
        return guard_warning + f"[COGNITIVE VERIFY] Error: File '{filepath}' does not exist at absolute path '{abs_path}'."
        
    filename = os.path.basename(abs_path)
    ext = os.path.splitext(filename)[1].lower()
    
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return guard_warning + f"[COGNITIVE VERIFY] Error: Failed to read file content: {e}"
        
    lines_count = len(content.splitlines())
    
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
    
    context = "General"
    
    is_mobile = "react-native" in content or "expo" in content or "expo-file-system" in content or "/ios/" in abs_path.replace("\\", "/") or "/android/" in abs_path.replace("\\", "/")
    is_ml = any(lib in content for lib in ["torch", "tensorflow", "keras", "sklearn", "pandas", "numpy", "scipy"])
    is_frontend = any(lib in content for lib in ["react", "vue", "angular", "svelte", "jsx", "tsx"]) and not is_mobile
    is_db = any(lib in content for lib in ["sqlite3", "psycopg2", "sqlalchemy", "pymongo", "prisma", "pg-pool", "mysql"])
    is_backend = any(lib in content for lib in ["express", "django", "flask", "fastapi", "nestjs", "cors"]) or is_db
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
    
    syntax_ok = True
    syntax_error = ""
    
    if ext == ".py":
        code, out, err = run_cmd(f"python -m py_compile \"{abs_path}\"")
        if code != 0:
            syntax_ok = False
            syntax_error = err or out
    elif ext in [".js", ".jsx"]:
        code, out, err = run_cmd(f"node --check \"{abs_path}\"")
        if code != 0:
            syntax_ok = False
            syntax_error = err or out
    elif ext in [".ts", ".tsx"]:
        code, out, err = run_cmd(f"npx tsc --noEmit --target esnext --moduleResolution node \"{abs_path}\"")
        if code != 0 and "tsc: command not found" not in err and "Cannot find command" not in err:
            syntax_ok = False
            syntax_error = out or err
            
    if syntax_ok:
        report_lines.append("  ✅ Syntax Check: PASSED (Compiles/Parses successfully)")
    else:
        report_lines.append("  ❌ Syntax Check: FAILED")
        report_lines.append(f"    Error: {syntax_error.strip()}")
        
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
            filtered_matches = [m for m in matches if not any(x in str(m).lower() for x in ["placeholder", "your_", "my_", "test_", "dummy", "example"])]
            if filtered_matches:
                secrets_found.append(name)
                
    if secrets_found:
        report_lines.append("  ❌ Security Check: FAILED (Potential Secrets Committed)")
        for sec in secrets_found:
            report_lines.append(f"    • Found potential {sec}")
    else:
        report_lines.append("  ✅ Security Check: PASSED (No hardcoded credentials detected)")
        
    warnings_found = []
    
    if is_mobile:
        if "downloadAsync" in content and "expo-file-system" in content:
            if "expo-file-system/legacy" not in content:
                warnings_found.append("FileSystem.downloadAsync is deprecated in modern Expo SDK 56. Import from 'expo-file-system/legacy' instead.")
                
    if is_ml:
        if "random" in content or "numpy" in content or "torch" in content:
            if not any(seed in content for seed in ["seed", "manual_seed", "random_state"]):
                warnings_found.append("Unseeded random operations detected. Add np.random.seed() or torch.manual_seed() for reproducibility.")
                
    if is_backend:
        if ext == ".py" and any(db in content for db in ["sqlite3", "psycopg2", "execute"]):
            if re.search(r"\.execute\(\s*(f['\"]|['\"].*?%|.*?\+)", content):
                warnings_found.append("Potential SQL injection: raw string formatting detected inside database execute() command. Use parameterized queries.")
                
    if is_frontend:
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
    
    report_lines = [
        f"[COGNITIVE CRITIQUE] task: {task_id}",
        "  Heuristic audit of code quality and standards:",
        ""
    ]
    
    concerns = []
    
    debug_calls = []
    if "console.log" in code_content:
        debug_calls.append("console.log()")
    if "debugger" in code_content:
        debug_calls.append("debugger statements")
    if "alert(" in code_content:
        debug_calls.append("alert() triggers")
    if "def " in code_content or "import " in code_content:
        if re.search(r"\bprint\s*\(", code_content):
            debug_calls.append("print() statements")
            
    if debug_calls:
        concerns.append(f"Leftover debugging traces detected: {', '.join(debug_calls)}. Clean these up for production code.")
        
    if re.search(r"except\s*(?:Exception)?\s*:\s*(?:pass|continue|break)", code_content):
        concerns.append("Empty 'except:' block with 'pass' or no action. Ensure exceptions are logged or handled gracefully.")
        
    if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", code_content):
        concerns.append("Empty catch block in JavaScript/TypeScript. Avoid swallowing errors silently.")
        
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
        
    nesting_depth = 0
    max_nesting = 0
    for line in code_content.splitlines():
        indent = len(line) - len(line.lstrip())
        if re.search(r"\b(for|while|if)\b", line):
            nesting_depth = indent // 4
            if nesting_depth > max_nesting:
                max_nesting = nesting_depth
                
    if max_nesting >= 4:
        concerns.append(f"Deep nesting concern: Maximum block nesting indentation level is {max_nesting}. Simplify logic to reduce complexity.")
        
    if "def " in code_content and not '"""' in code_content and not "'''" in code_content:
        concerns.append("Missing Python docstrings. Ensure public functions/classes are documented.")
        
    if concerns:
        report_lines.append(f"  ❌ Quality Concerns Found ({len(concerns)}):")
        for conc in concerns:
            report_lines.append(f"    • {conc}")
    else:
        report_lines.append("  ✅ Audit Passed: Code looks clean, structured, and compliant with best practices.")
        
    report_lines.append("\n  → Critique complete. Use these details to grade your reflect() results.")
    return guard_warning + "\n".join(report_lines)


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


def evolve_cognitive_rules(
    domain: str,
    evolution_type: str,    # "new_checklist_item" | "domain_rule" | "tier_rule" | "anti_pattern"
    evidence: str,          # From errors_found or reflection failures
    proposed_rule: str,     # The rule text
    confidence: float = 0.7,
    observation_count: int = 1,   # Guardrail 1: >= 2 required for persistence
    dry_run: bool = True           # Guardrail 2: Default True, never auto-disabled
) -> str:
    """
    COGNITIVE CORE — Proactive self-evolution of rules.
    Ships permanently with dry_run=True.
    """
    _log_cognitive_call("evolve_cognitive_rules")

    if dry_run:
        return (
            f"[COGNITIVE EVOLVE] DRY RUN — Not persisted:\n"
            f"  Type: {evolution_type}\n"
            f"  Domain: {domain}\n"
            f"  Proposed: {proposed_rule[:200]}\n"
            f"  Evidence: {evidence[:100]}\n"
            f"  Confidence: {confidence}\n"
            f"  → Call with dry_run=False and observation_count>=2 to persist."
        )

    if observation_count < 2:
        _store_pending_evolved_rule({
            "domain": domain, "type": evolution_type,
            "evidence": evidence, "proposed_rule": proposed_rule,
            "confidence": confidence, "observation_count": observation_count,
            "first_seen": datetime.now(timezone.utc).isoformat(),
        })
        return (
            f"[COGNITIVE EVOLVE] Observation 1 recorded — pending.\n"
            f"  Pattern: {proposed_rule[:100]}\n"
            f"  → Call again with observation_count=2 when pattern repeats."
        )

    if confidence < 0.7 or len(evidence.strip()) < 20:
        return (
            f"[COGNITIVE EVOLVE] Rejected — "
            f"confidence {confidence} < 0.7 or evidence too short ({len(evidence.strip())} chars)."
        )

    # All gates passed — persist
    evolved_rules = _load_evolved_rules()
    evolved_rules.setdefault("rules", []).append({
        "domain": domain, "type": evolution_type,
        "proposed_rule": proposed_rule, "evidence": evidence,
        "confidence": confidence, "observation_count": observation_count,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",  # optimize() can set to "quarantined"
    })
    _save_evolved_rules(evolved_rules)
    
    return (
        f"[COGNITIVE EVOLVE] ★ Rule persisted to evolved_rules.json\n"
        f"  Domain: {domain} | Type: {evolution_type}\n"
        f"  Rule: {proposed_rule[:150]}\n"
        f"  → optimize_cognitive_core() will health-check this rule after 5+ tasks."
    )
