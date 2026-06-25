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

import os
import sys

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

# Import modular implementations
from voxkage.mcp_servers.cognitive import server
from voxkage.mcp_servers.cognitive.intent import (
    _classify_intent,
    _is_trivial_task,
    _normalize_pattern
)
from voxkage.mcp_servers.cognitive.analyzer import _analyze_execution_trace
from voxkage.mcp_servers.cognitive.storage import (
    _load_dynamic_rules,
    _acquire_lock,
    _release_lock,
    _atomic_write_file,
    _load_session
)
from voxkage.mcp_servers.cognitive.utils import _auto_update_documentation

# Run auto update of documentation on startup (non-forcing to prevent git dirty diff noise)
_auto_update_documentation(force=False)


# ── FastMCP Tool Registrations ───────────────────────────────────────────────

@mcp.tool()
def start_turn(user_message: str, refresh_only: bool = False, suggested_domain: str = "") -> str:
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
    return server.start_turn(user_message, refresh_only=refresh_only, suggested_domain=suggested_domain)


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
    return server.pre_mortem(task_id, task_summary)


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
    return server.checkpoint(task_id, sub_task, status, issues)


@mcp.tool()
def reflect(task_id: str, output_summary: str, checklist_results: str, output_type: str = "auto") -> str:
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
    return server.reflect(task_id, output_summary, checklist_results, output_type=output_type)


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
    return server.verify(task_id, verification_results)


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
    return server.refine(task_id, issues_fixed, iteration)


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
    return server.learn(task_id, outcome, confidence_was, errors_found)


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
    return server.user_corrected(task_id, correction, error_category)


@mcp.tool()
def optimize_cognitive_core() -> str:
    """
    COGNITIVE CORE — Proactive self-optimization and cleaning.

    Reads recent task history and execution anti-patterns. Prunes obsolete
    checklist items, groups common errors, cleans up redundant rules, and
    performs a self-check on the cognitive core server's dynamic rules.

    Returns summary of optimizations performed.
    """
    return server.optimize_cognitive_core()


@mcp.tool()
def log_tool_execution(tool_name: str, arguments: str = "") -> str:
    """
    COGNITIVE CORE — Auto-instrumented tool execution logger. Appends a tool execution to the session trace.

    Parameters:
      tool_name : Name of the executed tool.
      arguments : JSON string or raw arguments passed to the tool.
    """
    return server.log_tool_execution(tool_name, arguments)


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
    return server.verify_code_file(filepath, domain)


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
    return server.generate_critique(task_id, code_content, domain)


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
    return server.get_profile(domain)


@mcp.tool()
def evolve_cognitive_rules(
    domain: str,
    evolution_type: str,
    evidence: str,
    proposed_rule: str,
    confidence: float = 0.7,
    observation_count: int = 1,
    dry_run: bool = True
) -> str:
    """
    COGNITIVE CORE — Proactive self-evolution of rules.
    Ships permanently with dry_run=True.
    """
    return server.evolve_cognitive_rules(
        domain=domain,
        evolution_type=evolution_type,
        evidence=evidence,
        proposed_rule=proposed_rule,
        confidence=confidence,
        observation_count=observation_count,
        dry_run=dry_run
    )


if __name__ == "__main__":
    mcp.run()
