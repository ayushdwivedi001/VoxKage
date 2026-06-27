import os
import re
import subprocess
from datetime import datetime, timezone

from .storage import (
    _load_session,
    _save_session,
    _load_checklist,
    _guard_check,
    _log_cognitive_call
)
from .constants import OUTPUT_TYPE_FILTERS
from .learning import evolve_cognitive_rules

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

    secondary_domains = task.get("secondary_domains", [])
    if session.get("primary_only"):
        secondary_domains = []

    # Check if a tailored task context was explicitly declared
    declared_checklist = session.get("declared_checklist")
    if declared_checklist:
        combined_items_map = {}
        for item in declared_checklist:
            item_copy = dict(item)
            if "origin_domain" not in item_copy:
                item_copy["origin_domain"] = domain
            combined_items_map[item_copy["id"]] = item_copy
    else:
        # Load baseline checklist and active plan with domain metadata
        checklist = _load_checklist(domain)
        for item in checklist:
            item["origin_domain"] = domain

        existing_ids = {item["id"] for item in checklist}
        for sec_domain in secondary_domains:
            for item in _load_checklist(sec_domain):
                # v8: Skip items with domain_scope:isolated — they are user corrections specific
                # to one domain and must NEVER bleed into other domain scoring
                if item.get("domain_scope") == "isolated":
                    continue
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
    exclude_prefixes = filters.get("exclude_prefixes", [])  # v8: prefix-based exclusion
    downgrade_map = filters.get("downgrade", {})

    filtered_map = {}
    filter_log = {"excluded": [], "downgraded": []}
    for item_id, item in combined_items_map.items():
        # v8: Exclude by exact ID
        if item_id in excluded_ids:
            filter_log["excluded"].append(item_id)
            continue
        # v8: Exclude by prefix (e.g. all corr_ items for research/markdown outputs)
        if any(item_id.startswith(pfx) for pfx in exclude_prefixes):
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
            
    # v8: PRIMARY-DOMAIN GATED DELIVERY DECISION
    # First: compute per-domain score breakdown (needed for primary gating)
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

    # Separate chk_fails early so secondary/primary split can reference it
    chk_fails = [f for f in fails if not f["id"].startswith("plan_")]

    # Compute primary domain score separately from secondary domains.
    # DELIVER if primary domain score >= 0.85 — secondary domain failures are informational.
    primary_ds = domain_scores.get(domain, {"achieved": 0, "possible": 0})
    primary_possible = primary_ds["possible"]
    primary_achieved = primary_ds["achieved"]
    primary_score = round(primary_achieved / max(primary_possible, 1), 2)

    # Check for high-severity failures in PRIMARY domain only
    primary_high_failed = False
    for fail_item in fails:
        item = combined_items_map.get(fail_item["id"])
        if item and item.get("origin_domain", domain) == domain:
            severity = item.get("severity", "low")
            if severity == "high" and "Not reported" not in fail_item.get("reason", ""):
                primary_high_failed = True
                break

    # Primary gates delivery — secondary is informational
    if primary_possible > 0:
        recommend_deliver = (primary_score >= 0.85) and not primary_high_failed
    else:
        # No primary items scored — fall back to combined score
        recommend_deliver = (quality_score >= 0.85) and not high_severity_failed

    # Separate secondary failures for informational display
    secondary_chk_fails = [
        f for f in chk_fails
        if combined_items_map.get(f["id"], {}).get("origin_domain", domain) != domain
    ]
    primary_chk_fails = [
        f for f in chk_fails
        if combined_items_map.get(f["id"], {}).get("origin_domain", domain) == domain
    ]

    lines = [
        f"[COGNITIVE REFLECT] task: {task_id}",
        f"  Domain: {domain}",
    ]
    if secondary_domains:
        lines.append(f"  Secondary Domains: {', '.join(secondary_domains)}")
    lines.append(f"  Output: {output_summary[:150]}")
    lines.append(f"  Checklist Quality Score: {quality_score}/1.0 ({quality_percent}%)")
    if primary_possible > 0:
        lines.append(f"  Primary Domain Score ({domain}): {round(primary_score*100)}% — gates DELIVER decision")

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
            role = " (primary — gates delivery)" if dom_name == domain else " (secondary — informational)"
            lines.append(f"    • {dom_name.capitalize()}: {pct}% ({passed_checks}/{total_checks} checks){role}")

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

    if plan_passes or chk_passes:
        lines.append("  ✅ PASSED:")
        if plan_passes:
            lines.append(f"    • Plan Requirements: {', '.join(plan_passes)}")
        if chk_passes:
            lines.append(f"    • Standard Checklist: {', '.join(chk_passes)}")

    if plan_fails or primary_chk_fails:
        lines.append("\n  ❌ FAILED / UNADDRESSED (Primary Domain):")
        if plan_fails:
            lines.append("    • Plan Requirements:")
            for f_item in plan_fails:
                item = combined_items_map.get(f_item["id"])
                check_text = item["check"] if item else f_item["id"]
                lines.append(f"      - {f_item['id']}: {check_text} → {f_item['reason']}")
        if primary_chk_fails:
            lines.append("    • Standard Checklist (Primary):")
            for f_item in primary_chk_fails:
                item = combined_items_map.get(f_item["id"])
                check_text = item["check"] if item else f_item["id"]
                elevated_mark = " 🔺" if f_item["id"] in elevated else ""
                lines.append(f"      - {f_item['id']}{elevated_mark}: {check_text} → {f_item['reason']}")

    # v8: Secondary domain failures are informational — they do NOT block delivery
    if secondary_chk_fails:
        lines.append("\n  ℹ️  Secondary Domain Gaps (informational — do NOT block delivery):")
        for f_item in secondary_chk_fails:
            item = combined_items_map.get(f_item["id"])
            check_text = item["check"] if item else f_item["id"]
            sec_dom = item.get("origin_domain", "?") if item else "?"
            lines.append(f"    • [{sec_dom}] {f_item['id']}: {check_text}")

    # Check if any elevated items were missed
    elevated_missed = [e for e in elevated if e not in passes and not any(f["id"] == e for f in fails)]
    if elevated_missed:
        lines.append(f"\n  🔺 Elevated checks NOT addressed: {', '.join(elevated_missed)}")

    # Check premortem risks
    premortem_risks = session.get("premortem_risks", [])
    if premortem_risks:
        lines.append("\n  🔮 Premortem Risk Mitigation Check:")
        for risk in premortem_risks:
            failed_match = False
            for f_item in fails:
                item = combined_items_map.get(f_item["id"])
                check_text = item["check"].lower() if item else ""
                if f_item["id"].lower() in risk.lower() or check_text in risk.lower() or risk.lower() in check_text:
                    failed_match = True
                    break
            status_str = "❌ UNADDRESSED" if failed_match else "✅ ADDRESSED/SAFE"
            lines.append(f"    • {risk[:80]} → {status_str}")

    # Recommendation
    if recommend_deliver:
        lines.append(f"\n  → Recommendation: DELIVER ✅")
        if secondary_chk_fails:
            lines.append(f"    (Primary domain passed — {len(secondary_chk_fails)} secondary gaps are informational)")
    else:
        if primary_possible > 0:
            reason_msg = "primary domain high-severity failure" if primary_high_failed else f"primary domain score {round(primary_score*100)}% is below 85% threshold"
        else:
            reason_msg = "high-severity failure(s) present" if high_severity_failed else f"score {quality_percent}% is below 85% threshold"
        lines.append(f"\n  → Recommendation: REFINE ❌ ({reason_msg})")
        
    lines.append(f"  → Primary Score: {primary_score} | Combined Score: {quality_score} (threshold: 0.85)")

    # v8: Auto-evolve trigger — when secondary domain fails badly but primary passes
    # This captures the exact mismatch pattern and persists a rule automatically
    auto_evolved_note = ""
    try:
        sec_fail_count = len(secondary_chk_fails)
        if sec_fail_count >= 3 and recommend_deliver and secondary_domains:
            worst_sec_domain = secondary_domains[0] if secondary_domains else ""
            if worst_sec_domain:
                proposed = (
                    f"When primary domain is '{domain}' and output type is '{output_type}', "
                    f"exclude '{worst_sec_domain}' secondary checklist items from scoring — "
                    f"they are not applicable to {output_type} deliverables"
                )
                evidence = (
                    f"{sec_fail_count} secondary domain failures from '{worst_sec_domain}' "
                    f"on a {domain} task with {round(primary_score*100)}% primary score"
                )
                evolve_result = evolve_cognitive_rules(
                    domain=domain,
                    evolution_type="domain_reclassification",
                    evidence=evidence,
                    proposed_rule=proposed,
                    confidence=min(0.7 + sec_fail_count * 0.05, 0.95),
                    observation_count=sec_fail_count,
                    dry_run=False
                )
                if "persisted" in evolve_result.lower() or "observation" in evolve_result.lower():
                    session["auto_evolved"] = True
                    auto_evolved_note = f"\n  🔄 Auto-evolved rule: {proposed[:120]}"
    except Exception:
        pass

    if auto_evolved_note:
        lines.append(auto_evolved_note)

    # Store reflection data in session for learn()
    session["last_reflection"] = {
        "passes": passes,
        "fails": [f["id"] for f in fails],
        "fail_details": fails,
        "confidence": quality_score,
        "pass_rate": quality_score,
        "primary_score": primary_score,
        "recommend_deliver": recommend_deliver,
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


def verify_code_file(filepath: str, domain: str = "") -> str:
    """
    COGNITIVE CORE — Deep domain-aware file verification tool.

    Parameters:
      filepath : Absolute or relative path to the code file to verify.
      domain   : Optional domain context.
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
