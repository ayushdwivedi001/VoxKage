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

if __name__ == "__main__":
    unittest.main()
