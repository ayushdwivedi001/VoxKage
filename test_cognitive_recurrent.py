import os
import sys
import unittest

# Ensure root is in sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from voxkage.mcp_servers.cognitive_core_server import (
    _classify_intent,
    _analyze_execution_trace,
    _load_dynamic_rules,
    _normalize_pattern,
    _acquire_lock,
    _release_lock,
    _atomic_write_file
)

class TestCognitiveCoreRecurrent(unittest.TestCase):
    def test_negated_state_change_classification(self):
        # Text with negated commit verb
        msg = "checkout the codebase and tell me all the changes which have been done. do not push or commit anything yet."
        res = _classify_intent(msg)
        self.assertEqual(res["tier"], 1, f"Failed: Expected Tier 1 for negated task, got Tier {res['tier']}")
        self.assertTrue(res["is_read_only"], "Failed: Expected is_read_only to be True")

    def test_pure_read_only_classification(self):
        msg = "tell me what is the current status of the git repository"
        res = _classify_intent(msg)
        self.assertEqual(res["tier"], 1)
        self.assertTrue(res["is_read_only"])

    def test_state_change_escalation(self):
        msg = "create a new config file and write the system settings, then commit it."
        res = _classify_intent(msg)
        self.assertGreaterEqual(res["tier"], 2, f"Failed: Expected Tier 2+ for state-change mutation, got Tier {res['tier']}")
        self.assertFalse(res["is_read_only"])

    def test_force_tier_1_override(self):
        msg = "git status"
        res = _classify_intent(msg)
        self.assertEqual(res["tier"], 1)
        self.assertTrue(res["is_read_only"])

    def test_trace_analyzer_tier_overkill(self):
        # Tier 3 task, but only start_turn, run_command, and learn were called
        fails = _analyze_execution_trace(
            task_id="test_task_1",
            domain="system",
            tier=3,
            tool_sequence="start_turn, run_command, learn"
        )
        types = [f["type"] for f in fails]
        self.assertIn("tier_overkill", types)

    def test_trace_analyzer_ceremony_overhead(self):
        # Tier 1 task, but pre_mortem and reflect were called
        fails = _analyze_execution_trace(
            task_id="test_task_2",
            domain="system",
            tier=1,
            tool_sequence="start_turn, pre_mortem, run_command, reflect, learn"
        )
        types = [f["type"] for f in fails]
        self.assertIn("cognitive_ceremony_overhead", types)

    def test_trace_analyzer_stubborn_execution(self):
        # Same tool called 3+ times consecutively with same arguments
        fails = _analyze_execution_trace(
            task_id="test_task_3",
            domain="system",
            tier=2,
            tool_sequence="start_turn; pre_mortem; view_file(a.py); view_file(a.py); view_file(a.py); reflect; learn"
        )
        types = [f["type"] for f in fails]
        self.assertIn("stubborn_execution", types)

    def test_trace_analyzer_stubborn_execution_different_args(self):
        # Same tool but different arguments - should NOT flag stubborn_execution
        fails = _analyze_execution_trace(
            task_id="test_task_4",
            domain="system",
            tier=2,
            tool_sequence="start_turn; pre_mortem; view_file(a.py); view_file(b.py); view_file(c.py); reflect; learn"
        )
        types = [f["type"] for f in fails]
        self.assertNotIn("stubborn_execution", types)

    def test_pattern_normalization_exact(self):
        # Less than 5 tokens
        pat = _normalize_pattern("git status")
        self.assertEqual(pat, r"^\s*git\s+status\s*$")
        
        # Stripping article
        pat = _normalize_pattern("show the git status")
        self.assertEqual(pat, r"^\s*show\s+git\s+status\s*$")

    def test_pattern_normalization_wildcard(self):
        # 5+ tokens
        pat = _normalize_pattern("checkout the codebase and show changes now")
        # "the" is stripped, leaving: "checkout codebase and show changes now" -> 6 tokens
        # core words: checkout, codebase, and, show, changes
        self.assertEqual(pat, r"^\s*checkout\s+codebase\s+and\s+show\s+changes.*")

    def test_atomic_write_and_locking(self):
        # Test atomic write
        test_file = os.path.join(_ROOT, "test_atomic_temp.txt")
        try:
            _atomic_write_file(test_file, "Hello atomic world!")
            self.assertTrue(os.path.exists(test_file))
            with open(test_file, encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "Hello atomic world!")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)
                
        # Test locking
        self.assertTrue(_acquire_lock())
        # Re-entrant acquire on same thread should succeed immediately
        self.assertTrue(_acquire_lock())
        _release_lock()
        _release_lock()

    def test_triviality_gating(self):
        from voxkage.mcp_servers.cognitive_core_server import _is_trivial_task, _classify_intent
        # Trivial command
        self.assertTrue(_is_trivial_task("git commit -m 'Initial commit'"))
        self.assertTrue(_is_trivial_task("npm install lodash"))
        self.assertTrue(_is_trivial_task("push changes"))
        
        # Non-trivial command (contains implementation verbs)
        self.assertFalse(_is_trivial_task("commit changes and write tests"))
        self.assertFalse(_is_trivial_task("create file and commit it"))
        
        # Verify classification intent
        res = _classify_intent("git commit -m 'fix syntax'")
        self.assertEqual(res["tier"], 1)
        self.assertTrue(res["is_read_only"])
        
        res = _classify_intent("write code and commit it")
        self.assertGreaterEqual(res["tier"], 2)
        
    def test_trace_logger_and_merging_in_learn(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, log_tool_execution, learn, _load_session
        
        # Initialize a task
        start_turn("test task for trace logging")
        
        # Log some tools
        log_tool_execution("search_web", '{"query": "test query"}')
        log_tool_execution("edit_file", '{"file": "test.txt"}')
        
        # Let's verify session trace
        session = _load_session()
        self.assertEqual(len(session.get("tool_trace", [])), 2)
        
        # Run learn - should run successfully and reconstruct sequence
        res = learn(task_id="test_task", outcome="success")
        self.assertIn("COGNITIVE LEARN", res)
        
    def test_window_refresh(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn
        res = start_turn("ignored", refresh_only=True)
        self.assertEqual(res, "[COGNITIVE] Protocol window refreshed.")

    def test_suggested_domain_override(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, _load_session
        from voxkage.mcp_servers.cognitive.storage import _save_session
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        session["domain_was_suggested"] = False
        _save_session(session)

        start_turn("write some coding work", suggested_domain="research")
        session = _load_session()
        active_task = session.get("active_task") or {}
        self.assertEqual(active_task.get("domain"), "research")
        self.assertTrue(session.get("domain_was_suggested"))
        
    def test_domain_mismatch_logging_in_learn(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, learn
        from voxkage.mcp_servers.cognitive.storage import _load_domain_mismatches, _load_session, _save_session
        
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        session["domain_was_suggested"] = False
        _save_session(session)

        start_turn("write some python code")
        learn(task_id="test_mismatch_task", outcome="success", errors_found="Domain mismatch: coding checklist on research task")
        
        mismatches = _load_domain_mismatches()
        m_list = mismatches.get("mismatches", [])
        self.assertTrue(len(m_list) > 0)
        self.assertEqual(m_list[-1]["corrected_domain"], "research")
        
    def test_reflect_output_type_filtering(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, reflect
        from voxkage.mcp_servers.cognitive.storage import _load_session, _save_session
        
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        session["domain_was_suggested"] = False
        _save_session(session)

        start_turn("write some code", suggested_domain="coding")
        res = reflect(task_id="test_filter_task", output_summary="markdown report", checklist_results="imports_correct:fail", output_type="markdown")
        self.assertIn("items excluded", res)
        
    def test_evolve_cognitive_rules_dry_run(self):
        from voxkage.mcp_servers.cognitive_core_server import evolve_cognitive_rules
        res = evolve_cognitive_rules(
            domain="coding",
            evolution_type="anti_pattern",
            evidence="Too many consecutive view_file calls",
            proposed_rule="Avoid calling view_file consecutively on same path",
            confidence=0.8,
            observation_count=1,
            dry_run=True
        )
        self.assertIn("DRY RUN — Not persisted", res)

    def test_metadata_header_parsing(self):
        msg = "Domains: RESEARCH (primary) -> GENERAL (synthesis)\nTier: 3\nWrite a comparison report."
        res = _classify_intent(msg)
        self.assertEqual(res["domain"], "research")
        self.assertIn("general", res["secondary_domains"])
        self.assertEqual(res["tier"], 3)

    def test_suggested_tier_and_secondary_override(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, _load_session
        from voxkage.mcp_servers.cognitive.storage import _save_session
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        _save_session(session)

        start_turn("create a new config file and write system settings, then commit", suggested_domain="coding", suggested_tier=3, suggested_secondary_domains="research,general")
        session = _load_session()
        task = session.get("active_task") or {}
        self.assertEqual(task.get("domain"), "coding")
        self.assertEqual(task.get("tier"), 3)
        self.assertIn("research", task.get("secondary_domains", []))
        self.assertIn("general", task.get("secondary_domains", []))

    def test_pre_mortem_domain_filtering(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, pre_mortem
        from voxkage.mcp_servers.cognitive.storage import _load_session, _save_session, _load_anti_patterns, _save_anti_patterns
        
        # Save a coding-specific anti-pattern
        ap_data = _load_anti_patterns()
        # Clean current anti-patterns to avoid pollution
        old_aps = list(ap_data.setdefault("anti_patterns", []))
        ap_data["anti_patterns"] = [{
            "pattern": "Test coding-specific anti-pattern risk warning",
            "domain": "coding",
            "severity": "high",
            "times_prevented": 1
        }]
        _save_anti_patterns(ap_data)

        try:
            session = _load_session()
            session["last_task"] = None
            session["active_task"] = None
            _save_session(session)

            # Start a research task (which has no coding domain)
            start_turn("do some web research on climate data", suggested_domain="research", suggested_secondary_domains="general")
            res = pre_mortem(task_id="test_pm_task", task_summary="research summary")
            # Verify that the coding anti-pattern is NOT in the pre-mortem output
            self.assertNotIn("Test coding-specific anti-pattern risk warning", res)
        finally:
            # Restore anti-patterns
            ap_data = _load_anti_patterns()
            ap_data["anti_patterns"] = old_aps
            _save_anti_patterns(ap_data)

    def test_custom_user_correction(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, user_corrected
        from voxkage.mcp_servers.cognitive.storage import _load_session, _save_session, _load_checklist
        
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        _save_session(session)

        start_turn("some system work", suggested_domain="system")
        user_corrected(
            task_id="test_correction_task",
            correction="Ensure we verify the system disk before deletion",
            descriptive_id="corr_verify_system_disk",
            target_domain="system",
            severity="high"
        )
        
        items = _load_checklist("system")
        found = [it for it in items if it.get("id") == "corr_verify_system_disk"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "high")

    def test_structured_checklist_evolution(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, learn
        from voxkage.mcp_servers.cognitive.storage import _load_json, _save_session, _load_session
        from voxkage.mcp_servers.cognitive.constants import _CHECKLISTS_PENDING_FILE
        
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        _save_session(session)

        # Clear checklists pending
        import json
        if os.path.exists(_CHECKLISTS_PENDING_FILE):
            try:
                os.remove(_CHECKLISTS_PENDING_FILE)
            except OSError:
                pass

        start_turn("do some frontend work", suggested_domain="frontend")
        learn(
            task_id="test_evolve_task",
            outcome="success",
            evolved_checklist_item={
                "id": "verify_mobile_viewport",
                "check": "Did we check mobile viewport responsiveness?",
                "severity": "medium",
                "domain": "frontend"
            }
        )
        
        data = _load_json(_CHECKLISTS_PENDING_FILE, lambda: {"pending": []})
        pending = data.get("pending", [])
        found = [p for p in pending if p.get("id") == "evolved_verify_mobile_viewport"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "medium")
        self.assertEqual(found[0]["domain"], "frontend")

    def test_reflect_output_type_normalization(self):
        from voxkage.mcp_servers.cognitive_core_server import start_turn, reflect
        from voxkage.mcp_servers.cognitive.storage import _load_session, _save_session
        
        session = _load_session()
        session["last_task"] = None
        session["active_task"] = None
        _save_session(session)

        # Start coding task
        start_turn("write python code", suggested_domain="coding")
        # Under output_type='markdown', all coding items (like syntax_valid, security, edge_cases) should be excluded
        # Let's verify that the output results in items excluded
        res = reflect(task_id="test_norm_task", output_summary="markdown report", checklist_results="documentation:pass", output_type="markdown")
        self.assertIn("items excluded", res)
        # Excluded items should not be in failed or passed sections
        self.assertNotIn("syntax_valid", res)
        self.assertNotIn("security", res)
        self.assertNotIn("edge_cases", res)
        self.assertNotIn("imports_correct", res)

    def test_exact_battle_test_prompt_classification(self):
        msg = (" alright soo i want you to test out your complete cognitive MCP server performance "
               "to test out your recursive self improvement architecture and to see if it actually perfectly works or not. "
               "and for this i have a task for you. now your task - Title: \"Comprehensive Comparison: Top 5 AI Coding Assistants in June 2026\"\n\n"
               "      Domains: RESEARCH (primary) → GENERAL (synthesis) → CODING (optional deliverable)\n\n"
               "      Task Breakdown:\n"
               "      1. Research phase — Web-search each of 5 tools (Claude Code, Copilot, Cursor, Windsurf, Codeium) for latest pricing, features, and reviews\n"
               "      2. Comparison phase — Build a structured comparison matrix (pricing, strengths, weaknesses, ideal use-case)\n"
               "      3. Synthesis phase — Write a concise report to C:\\Users\\AYUSH\\.voxkage\\output\\ai-assistants-comparison.md\n"
               "      4. Verification phase — Cross-check specific claims against fresh sources\n"
               "      5. Refinement phase — Fix any inaccuracies or missing categories. work on this properly and show me the work done in this directory. "
               "soo once you are done with this, i want you to tell me how much of this has actually been effective enough for recursive self improvement.")
        res = _classify_intent(msg)
        self.assertEqual(res["domain"], "research")
        self.assertIn("general", res["secondary_domains"])
        self.assertIn("coding", res["secondary_domains"])

    def test_confusing_human_like_prompt_classification(self):
        # Human prompt mixing verbs/topics
        msg = "build me a report in your feed while telling me about top 5 AI coding agents right now. do research to build this"
        res = _classify_intent(msg)
        # Main intent is research/report generation, not writing source code
        self.assertEqual(res["domain"], "research")

    def test_autonomous_evolved_rule_promotion(self):
        from voxkage.mcp_servers.cognitive.storage import (
            _store_pending_evolved_rule, 
            _promote_pending_evolved_rules, 
            _load_evolved_rules,
            _save_evolved_rules
        )
        # Clean up existing test rules first to ensure test isolation
        evolved = _load_evolved_rules()
        evolved["rules"] = [r for r in evolved.get("rules", []) if r.get("proposed_rule") != "test promotion rule"]
        _save_evolved_rules(evolved)

        # Store a pending evolved rule with count 3
        rule = {
            "domain": "research",
            "proposed_rule": "test promotion rule",
            "evidence": "testing promotion logic in unit test",
            "observation_count": 3
        }
        _store_pending_evolved_rule(rule)
        
        # Promote
        promoted = _promote_pending_evolved_rules()
        self.assertGreaterEqual(promoted, 1)
        
        # Check active evolved rules
        evolved = _load_evolved_rules()
        found = [r for r in evolved.get("rules", []) if r.get("proposed_rule") == "test promotion rule"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["status"], "active")

        # Clean up after test
        evolved["rules"] = [r for r in evolved.get("rules", []) if r.get("proposed_rule") != "test promotion rule"]
        _save_evolved_rules(evolved)

    def test_confidence_calibration_brier_score(self):
        from voxkage.mcp_servers.cognitive.storage import _load_calibration, _save_calibration, _load_session, _save_session
        from voxkage.mcp_servers.cognitive_core_server import learn, start_turn
        
        # Reset calibration database for coding domain
        cal = _load_calibration()
        cal["domains"]["coding"] = {"predictions": [], "accuracy": None}
        _save_calibration(cal)
        
        # Log 5 tasks with various confidence levels and outcomes
        # Prediction 1: stated 0.8, success -> (0.8 - 1.0)^2 = 0.04
        start_turn("task 1", suggested_domain="coding")
        learn("task_1", "success", confidence_was=0.8)
        
        # Prediction 2: stated 0.9, success -> (0.9 - 1.0)^2 = 0.01
        start_turn("task 2", suggested_domain="coding")
        learn("task_2", "success", confidence_was=0.9)
        
        # Prediction 3: stated 0.7, failed -> (0.7 - 0.0)^2 = 0.49
        start_turn("task 3", suggested_domain="coding")
        learn("task_3", "failed", confidence_was=0.7)
        
        # Prediction 4: stated 0.5, success -> (0.5 - 1.0)^2 = 0.25
        start_turn("task 4", suggested_domain="coding")
        learn("task_4", "success", confidence_was=0.5)
        
        # Prediction 5: stated 0.6, failed -> (0.6 - 0.0)^2 = 0.36
        start_turn("task 5", suggested_domain="coding")
        learn("task_5", "failed", confidence_was=0.6)
        
        # Total Brier Sum = 0.04 + 0.01 + 0.49 + 0.25 + 0.36 = 1.15
        # Expected Brier Score = 1.15 / 5 = 0.23
        cal_after = _load_calibration()
        d_cal = cal_after["domains"]["coding"]
        self.assertAlmostEqual(d_cal["brier_score"], 0.23, places=2)

if __name__ == "__main__":
    unittest.main()
