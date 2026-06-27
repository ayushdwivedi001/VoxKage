import os
import re
import uuid
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from .storage import (
    _load_session,
    _save_session,
    _load_profile,
    _load_anti_patterns,
    _load_checklist,
    _guard_check,
    _log_cognitive_call,
    set_last_start_turn_ts,
    _append_classification_example
)
from .constants import _DOMAIN_KEYWORDS, OUTPUT_TYPE_FILTERS
from .intent import (
    _classify_intent,
    _detect_followup,
    _generate_task_plan
)
from .analyzer import (
    _score_anti_pattern
)

def start_turn(
    user_message: str,
    refresh_only: bool = False,
    suggested_domain: str = "",
    suggested_tier: int = 0,                    # 1-3 overrides classifier
    suggested_secondary_domains: str = "",      # Comma-separated list (e.g. "general,coding")
    primary_only: bool = False                  # v8: If True, strip secondary checklists entirely
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

    # v8: Detect classification retry (start_turn called again with no cognitive tools in between)
    cog_trace = session.get("cognitive_trace", [])
    is_retry = (
        len(cog_trace) >= 1
        and cog_trace[-1].get("tool") == "start_turn"
        and suggested_tier > 0  # Only flag as retry when caller explicitly overrides
    )
    if is_retry:
        session["start_turn_retry"] = True

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

    # v8: primary_only — strip secondary domains entirely so they never pollute checklists
    if primary_only:
        secondary_domains = []
        session["primary_only"] = True
    else:
        session["primary_only"] = False

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
    conf = classification.get("confidence", 0.0)
    reason_codes = classification.get("reason_codes", [])
    similar_examples = classification.get("similar_examples", [])

    task_context = {
        "task_id": task_id,
        "domain": domain,
        "secondary_domains": secondary_domains,
        "tier": tier,
        "is_followup": is_followup,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_message": user_message,
        "classification_confidence": conf,
        "reason_codes": reason_codes,
        "similar_examples": similar_examples,
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

    # Classification Transparency
    lines.append("\n  Classification Transparency:")
    lines.append(f"    Confidence: {round(conf * 100)}%")
    if reason_codes:
        lines.append(f"    Reason Codes: {', '.join(reason_codes)}")
    if similar_examples:
        best_ex = similar_examples[0]
        lines.append(f"    Similar Past: \"{best_ex.get('message_sample', '')[:60]}...\" ➔ {best_ex.get('correct_domain')} (sim: {round(best_ex.get('similarity', 0.0)*100)}%)")

    # Classification Gate
    lines.append("\n  ⚠️ CLASSIFICATION GATE:")
    if conf >= 0.80:
        lines.append("    Status: PASS (Advisory only)")
        lines.append("    Review the checklist. If correct, proceed to pre_mortem().")
        lines.append("    If wrong, call verify_classification(task_id, override_domain=\"X\", reason=\"why\")")
    elif 0.60 <= conf < 0.80:
        lines.append("    Status: ⚠️ WARNING (Low Confidence)")
        lines.append("    verify_classification() is strongly suggested if the domain looks incorrect.")
        lines.append("    If checklists are irrelevant, call declare_task_context(task_id, output_type=\"X\", primary_domain=\"Y\") to customize.")
    else:
        lines.append("    Status: 🔴 CRITICAL (Ambiguous / Garbled Intent)")
        lines.append("    You MUST review this classification before calling pre_mortem().")
        lines.append("    If incorrect, call verify_classification(task_id, override_domain=\"correct_domain\", reason=\"override\") first.")

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
        lines.append("    0. ⚡ MEMORY CHECK (REQUIRED): Call search_memory('domain + task type keywords') BEFORE any execution")
        lines.append("       └ Failure to call search_memory will be flagged as high-severity in reflect()")
        lines.append("    1. Predict risks and load failure memories: Call pre_mortem(task_id, summary)")
        lines.append("    2. Execute task step-by-step, calling checkpoint(task_id, sub_task, status) after each major step")
        lines.append("    3. Run file verification checks: Call verify_code_file(filepath)")
        lines.append("    4. Run critique audit on code: Call generate_critique(task_id, code_content)")
        lines.append("    5. Evaluate output using exact IDs from checklist above: Call reflect(task_id, summary, results)")
        lines.append("       └ Format: \"plan_1:pass, plan_2:pass, syntax_ok:pass, error_handling:fail:no try/catch\"")
        lines.append("    6. Run external checks (lint, test, build, run): Call verify(task_id, results)")
        lines.append("    7. If issues found in reflect/verify, call refine(task_id, issues, iteration) and repeat up to 3 times")
        lines.append("    8. Record outcome and update capability weights: Call learn(task_id, outcome, confidence_was, errors_found)")
        lines.append("    9. ⚡ ALWAYS call optimize_cognitive_core() after learn() for Tier 3 tasks — commits evolved rules and prunes stale anti-patterns")
        lines.append("    10. Deliver")

    # v8: Surface classification override instructions on every task turn
    lines.append("\n  ℹ️  Classification Override: If this domain/tier is wrong, re-call start_turn() with:")
    lines.append(f"       suggested_domain='correct_domain', suggested_tier=N")
    lines.append(f"       primary_only=True  ← strips secondary domain checklists entirely from scoring")

    if primary_only:
        lines.append("\n  🎯 primary_only=True: Secondary domain checklists excluded from scoring.")

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
    raw_elevated = session.get("elevated_checks", {})
    if isinstance(raw_elevated, list):
        elevated = raw_elevated
    else:
        elevated = raw_elevated.get(domain, [])

    # Track all premortem risks in session
    premortem_risks = []
    if task.get("classification_overridden"):
        premortem_risks.append(f"Classification overridden to {domain}: {task.get('override_reason')}")
    if relevant_aps:
        premortem_risks.extend([ap.get('pattern', '?') for ap in relevant_aps[:5]])
    if common_errors:
        premortem_risks.extend(common_errors[:5])
    if elevated:
        premortem_risks.extend(elevated[:3])
        
    session["premortem_risks"] = premortem_risks
    _save_session(session)

    lines = [f"[COGNITIVE PRE-MORTEM] task: {task_id}", f"  Summary: {task_summary}", ""]

    risk_count = 0

    if task.get("classification_overridden"):
        risk_count += 1
        lines.append(f"  ⚠️  Risk {risk_count} (Classification Override):")
        lines.append(f"      Intent was overridden to {domain} because: {task.get('override_reason')}")
        lines.append("")

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


def verify_classification(
    task_id: str,
    override_domain: str,
    override_tier: int,
    reason: str,
    remove_domains: str = ""
) -> str:
    """
    COGNITIVE CORE — Override intent domain or tier.
    Saves the corrected query to example database and force-persists evolved rules immediately.
    """
    _log_cognitive_call("verify_classification")
    session = _load_session()
    task = session.get("active_task")
    if not task:
        return "[COGNITIVE ERROR] No active task found in session to override classification."
        
    if task.get("task_id") != task_id:
        return f"[COGNITIVE ERROR] Active task ID mismatch ({task.get('task_id')} vs override request {task_id})."

    old_domain = task.get("domain", "general")
    old_tier = task.get("tier", 1)
    
    # Apply override
    task["domain"] = override_domain
    task["tier"] = override_tier
    task["classification_overridden"] = True
    task["override_domain"] = override_domain
    task["override_reason"] = reason
    session["active_task"] = task
    
    # 1. Regenerate checklist
    checklist = _load_checklist(override_domain)
    existing_ids = {item["id"] for item in checklist}
    
    # Get secondary domains
    secondary_domains = task.get("secondary_domains", [])
    # Strip remove_domains
    if remove_domains:
        rem_set = {d.strip().lower() for d in remove_domains.split(",")}
        secondary_domains = [d for d in secondary_domains if d not in rem_set]
        task["secondary_domains"] = secondary_domains
        
    for sec_domain in secondary_domains:
        for item in _load_checklist(sec_domain):
            if item["id"] not in existing_ids:
                checklist.append(item)
                existing_ids.add(item["id"])
            else:
                existing = next(i for i in checklist if i["id"] == item["id"])
                w = {"high": 3, "medium": 2, "low": 1}
                if w.get(item["severity"], 1) > w.get(existing["severity"], 1):
                    existing["severity"] = item["severity"]

    # Regenerate task-specific plan items and prepend
    plan_items = _generate_task_plan(task.get("user_message", ""), override_domain)
    checklist = plan_items + checklist
    session["active_plan"] = plan_items
    
    # Exclude remove_domains items if any matching IDs or keywords
    if remove_domains:
        rem_set = {d.strip().lower() for d in remove_domains.split(",")}
        filtered_checklist = []
        for item in checklist:
            item_id = item["id"]
            if any(ex_dom in item_id for ex_dom in rem_set):
                continue
            filtered_checklist.append(item)
        checklist = filtered_checklist

    session["declared_checklist"] = checklist
    _save_session(session)
    
    # 2. Append original query to classification examples (CRITICAL)
    try:
        from .storage import _append_classification_example
        _append_classification_example(
            message=task.get("user_message", ""),
            correct_domain=override_domain,
            correct_tier=override_tier,
            classifier_said=old_domain,
            was_corrected=True,
            correction_source="model"
        )
    except Exception:
        pass
        
    # 3. Call evolve_cognitive_rules to force-persist immediately
    evolve_msg = ""
    try:
        from .learning import evolve_cognitive_rules
        evolve_msg = evolve_cognitive_rules(
            domain=override_domain,
            evolution_type="domain_reclassification",
            evidence=f"Model override from {old_domain} to {override_domain} for task: {task.get('user_message', '')[:100]}",
            proposed_rule=f"Reclassify matching pattern to {override_domain}",
            confidence=0.9,
            observation_count=2,
            dry_run=False,
            force_persist=True
        )
    except Exception as e:
        evolve_msg = f"Evolve failed: {str(e)}"
        
    # Build result output
    lines = [
        f"[COGNITIVE VERIFY] Classification overridden successfully.",
        f"  Task ID: {task_id}",
        f"  Domain: {old_domain} ➔ {override_domain}",
        f"  Tier: {old_tier} ➔ {override_tier}",
        f"  Reason: {reason}",
        f"  Evolve output: {evolve_msg}",
        f"\n  New Checklist ({override_domain}, {len(checklist)} items):"
    ]
    for item in checklist:
        is_plan = item["id"].startswith("plan_")
        tag = "PLAN" if is_plan else item['severity'].upper()
        lines.append(f"    \u25a1 [{tag}] {item['id']}: {item['check']}")
        
    return "\n".join(lines)


def declare_task_context(
    task_id: str,
    output_type: str,
    primary_domain: str,
    include_items: str = "",
    exclude_domains: str = "",
    custom_items: str = ""
) -> str:
    """
    COGNITIVE CORE — Explicitly declare task parameters to build a tailored checklist.
    Strips coding checks for non-coding tasks (e.g. markdown outputs), and applies exclusions.
    The resulting checklist is stored in the session and used by reflect().
    """
    _log_cognitive_call("declare_task_context")
    session = _load_session()
    task = session.get("active_task")
    if not task:
        return "[COGNITIVE ERROR] No active task found in session to declare context."
        
    if task.get("task_id") != task_id:
        return f"[COGNITIVE ERROR] Active task ID mismatch ({task.get('task_id')} vs request {task_id})."
        
    # Rebuild checklist
    # Start with plan items
    plan_items = session.get("active_plan") or []
    
    # Load primary checklist
    checklist = _load_checklist(primary_domain)
    
    # Parse exclusions
    exclusions = {d.strip().lower() for d in exclude_domains.split(",")} if exclude_domains else set()
    
    # 1. Apply Output Type filters
    if output_type in OUTPUT_TYPE_FILTERS:
        filters = OUTPUT_TYPE_FILTERS[output_type]
        exclude_ids = set(filters.get("exclude", []))
        exclude_prefixes = filters.get("exclude_prefixes", [])
        
        filtered_checklist = []
        for item in checklist:
            item_id = item["id"]
            # Check exclusions
            if item_id in exclude_ids:
                continue
            if any(item_id.startswith(p) for p in exclude_prefixes):
                continue
            filtered_checklist.append(item)
        checklist = filtered_checklist

    # 2. Strip excluded domains
    if exclusions:
        filtered_checklist = []
        for item in checklist:
            item_id = item["id"]
            if any(ex_dom in item_id for ex_dom in exclusions):
                continue
            filtered_checklist.append(item)
        checklist = filtered_checklist

    # 3. Include requested items from other checklists
    if include_items:
        inc_ids = {i.strip() for i in include_items.split(",")}
        for domain_name in _DOMAIN_KEYWORDS.keys():
            if domain_name == primary_domain:
                continue
            other_checklist = _load_checklist(domain_name)
            for item in other_checklist:
                if item["id"] in inc_ids:
                    if not any(x["id"] == item["id"] for x in checklist):
                        checklist.append(item)

    # 4. Add custom items
    if custom_items:
        for c_item in custom_items.split("|"):
            parts = c_item.split(":", 2)
            if len(parts) == 3:
                c_id, c_sev, c_check = parts
                checklist.append({
                    "id": c_id.strip(),
                    "severity": c_sev.strip().lower(),
                    "check": c_check.strip()
                })

    # Combine plan items + filtered items
    checklist = plan_items + checklist
    
    # Store in session
    session["declared_checklist"] = checklist
    _save_session(session)
    
    # Format output
    lines = [
        f"[COGNITIVE CONTEXT] Task context declared successfully.",
        f"  Task ID: {task_id}",
        f"  Output Type: {output_type}",
        f"  Primary Domain: {primary_domain}",
        f"  Excluding domains: {exclude_domains or 'None'}",
        f"\n  Declared Checklist ({len(checklist)} items):"
    ]
    for item in checklist:
        is_plan = item["id"].startswith("plan_")
        tag = "PLAN" if is_plan else item['severity'].upper()
        lines.append(f"    \u25a1 [{tag}] {item['id']}: {item['check']}")
        
    return "\n".join(lines)
