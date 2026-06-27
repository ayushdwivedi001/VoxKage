import os
import re
import json
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
    _load_archived_anti_patterns,
    _append_task_history,
    _load_domain_mismatches,
    _append_domain_mismatch,
    _store_pending_checklist_item,
    _promote_pending_checklist_items,
    _load_recent_task_history,
    _store_pending_evolved_rule,
    _load_evolved_rules,
    _save_evolved_rules,
    _promote_pending_evolved_rules,
    _COG_DIR
)
from .constants import _DOMAIN_KEYWORDS
from .intent import _normalize_pattern
from .analyzer import (
    _analyze_execution_trace,
    _consolidate_soul,
    _cluster_and_aggregate_failures,
    _generate_calibration_report
)

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
                # P0: Save to classification examples so future queries with similar fingerprints
                # get pre-emptively reclassified — breaks the recurrence cycle.
                from .storage import _append_classification_example
                _append_classification_example(
                    message=task.get("user_message", ""),
                    correct_domain=corrected_domain,
                    correct_tier=task.get("tier", 1),
                    classifier_said=domain,
                    was_corrected=True,
                    correction_source="auto_learn"
                )

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
            # Save to classification examples to prevent recurrence
            from .storage import _append_classification_example
            _append_classification_example(
                message=task.get("user_message", ""),
                correct_domain=model_domain,
                correct_tier=task.get("tier", 1),
                classifier_said=classifier_domain,
                was_corrected=True,
                correction_source="auto_learn_suggested"
            )

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

    # v8: Detect classification retry and auto-log it as an error
    auto_errors = list(errors_found) if isinstance(errors_found, (list, tuple)) else (
        [errors_found] if errors_found else []
    )
    if session.get("start_turn_retry"):
        auto_errors.append("start_turn_conversation_misclassification")
        # Also log as a domain mismatch so optimize() can pick it up
        try:
            _append_domain_mismatch({
                "task_id": task_id,
                "assigned_domain": domain,
                "corrected_domain": domain,  # same domain, just wrong type (conversation vs task)
                "error_type": "conversation_misclassification",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        session["start_turn_retry"] = False

    errors_display = ", ".join(auto_errors) if auto_errors else errors_found

    # v8: Auto-evolve on false-alarm REFINE (task succeeded despite REFINE recommendation)
    false_alarm_note = ""
    try:
        last_reflect = session.get("last_reflection", {})
        reflect_recommended_deliver = last_reflect.get("recommend_deliver", True)
        primary_score_was = last_reflect.get("primary_score", 1.0)
        if outcome == "success" and not reflect_recommended_deliver and primary_score_was >= 0.85:
            # Reflect said REFINE but task actually succeeded and primary was fine
            proposed = (
                f"When primary domain '{domain}' score >= 85% and task outcome is success, "
                f"do not REFINE \u2014 combined secondary domain failures are not blocking"
            )
            evidence = (
                f"Task {task_id} succeeded with primary_score={primary_score_was} "
                f"despite REFINE recommendation \u2014 false alarm confirmed"
            )
            evolve_result = evolve_cognitive_rules(
                domain=domain,
                evolution_type="domain_rule",
                evidence=evidence,
                proposed_rule=proposed,
                confidence=0.85,
                observation_count=2,
                dry_run=False
            )
            if "persisted" in evolve_result.lower() or "observation" in evolve_result.lower():
                session["auto_evolved"] = True
                false_alarm_note = "\n  🔄 Auto-evolved rule: false-alarm REFINE pattern captured and persisted."
    except Exception:
        pass

    lines = [
        f"[COGNITIVE LEARN] task: {task_id} \u2192 {outcome.upper()}",
        f"  Domain {domain}: {success_rate}% success rate ({dp[domain]['successes']}/{dp[domain]['tasks']})",
        f"  Calibration: {cal_score}",
        f"  Lifetime: {total} tasks, {corrections} user corrections",
    ]
    if errors_display:
        lines.append(f"  Errors logged: {errors_display}")

    if false_alarm_note:
        lines.append(false_alarm_note)

    if execution_warnings:
        lines.append("\n  ⚠️ Execution Anti-Patterns Detected:")
        for warn in execution_warnings:
            lines.append(f"    • [{warn['type'].upper()}]: {warn['pattern']}")
            lines.append(f"      → Suggested Fix: {warn['suggested_fix']}")

    # v8: Optimize directive when mismatch or auto-evolved rule was detected
    if session.get("auto_evolved") or "misclassification" in str(auto_errors):
        lines.append(
            "\n  ⚡ AUTO-OPTIMIZE REQUIRED: Call optimize_cognitive_core() before delivering "
            "\u2014 domain mismatch or auto-evolved rule detected this session."
        )
    else:
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
            
            # Save archived JSON
            # Need to define _save_json helper or use standard atomic write
            from .storage import _atomic_write_file
            _atomic_write_file(os.path.join(_COG_DIR, "anti_patterns_archived.json"), archived_data)
            
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
            "severity": severity if severity in ["high", "medium", "low"] else "high",
            # v8: Isolated scope — this item must NEVER bleed into secondary domain scoring
            # It is a correction specific to this exact domain and context.
            "domain_scope": "isolated",
        })
        _save_checklist(resolved_domain, items)
    except Exception:
        pass

    # Save original query to classification examples (CRITICAL) and trigger rule evolution
    try:
        from .storage import _append_classification_example
        orig_msg = task.get("user_message", "")
        if orig_msg:
            _append_classification_example(
                message=orig_msg,
                correct_domain=resolved_domain,
                correct_tier=task.get("tier", 1),
                classifier_said=domain,
                was_corrected=True,
                correction_source="user"
            )
            evolve_cognitive_rules(
                domain=resolved_domain,
                evolution_type="domain_reclassification",
                evidence=f"User correction: {correction[:100]} for query: {orig_msg[:100]}",
                proposed_rule=f"Reclassify matching pattern to {resolved_domain}",
                confidence=0.9,
                observation_count=2,
                dry_run=False,
                force_persist=True
            )
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
            
            from .storage import _atomic_write_file
            _atomic_write_file(os.path.join(_COG_DIR, "anti_patterns_archived.json"), archived)

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
            summary.append("  ⚠️ Quarantined rules:")
            for q in quarantined:
                summary.append(f"    • {q}")
        return guard_warning + "\n".join(summary)
    except Exception as e:
        return guard_warning + f"[COGNITIVE OPTIMIZE] Failed: {e}"


def log_tool_execution(tool_name: str, arguments: str = "") -> str:
    """
    COGNITIVE CORE — Auto-instrumented tool execution logger. Appends a tool execution to the session trace.
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


def get_profile(domain: str = "") -> str:
    """
    COGNITIVE CORE — View current capability heatmap and session state.
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
    dry_run: bool = True,          # Guardrail 2: Default True, never auto-disabled
    force_persist: bool = False   # NEW: Bypass pending state and force persist immediately
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

    if observation_count < 2 and not force_persist:
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

    if not force_persist and (confidence < 0.7 or len(evidence.strip()) < 20):
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
