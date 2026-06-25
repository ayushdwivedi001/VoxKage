import os

from .constants import _PARENT
from .storage import _atomic_write_file


def _auto_update_documentation(force=False):
    """
    Automatically updates CLAUDE.md, GEMINI.md.template, and AGENTS.md
    with the latest cognitive core protocol definitions using structured markers.
    """
    try:
        workspace_dir = _PARENT
        claude_path = os.path.join(workspace_dir, "CLAUDE.md")
        gemini_template_path = os.path.join(workspace_dir, "voxkage", "templates", "GEMINI.md.template")
        workspace_agents_path = os.path.join(workspace_dir, ".agents", "AGENTS.md")
        global_agents_path = os.path.join(os.path.expanduser("~"), ".gemini", "config", "AGENTS.md")
        
        def update_doc_file(filepath):
            if not os.path.exists(filepath):
                return
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                
            start_marker = "<!-- COGNITIVE_PROTOCOL_START -->"
            end_marker = "<!-- COGNITIVE_PROTOCOL_END -->"
            
            if start_marker in content and end_marker in content:
                parts_before = content.split(start_marker)[0]
                parts_after = content.split(end_marker)[1]
                
                protocol_content = """
### If result type = "task"
The response includes: `task_id`, `domain`, `tier`, `checklist`, `warnings`, `profile_snapshot`.
Follow the metacognitive cycle based on the returned **tier**:

**Tier 1 (Quick Task) — e.g., "open Chrome", "what time is it", "show git status", "how do I cook eggs":**
- If `start_turn` returns `READ-ONLY task`: Execute directly, skip ALL cognitive tools. Zero overhead.
- Otherwise: Execute → quick mental check → `learn(task_id, "success")` → Deliver

**Tier 2 (Standard Task) — e.g., "write a Python script to sort files", "send this email":**
1. `pre_mortem(task_id, summary)` → Note risks
2. Execute the task with risks in mind
3. `reflect(task_id, output_summary, checklist_results)` → Get structured critique
   - **Use the EXACT IDs shown in the checklist from start_turn output**
   - Format: `"plan_1:pass, plan_2:pass, clarity:pass, accuracy:fail:reason"`
   - Shorthand `"plans:pass"` marks ALL plan_* items as passed at once
4. If REFINE recommended → fix issues → `refine(task_id, issues, iteration)`
5. `learn(task_id, outcome, confidence_was, errors_found)` → Deliver

**Tier 3 (Complex Task) — e.g., "build me a dashboard", "research AI agents thoroughly", "deploy this feature":**
1. `pre_mortem(task_id, summary)` → Note risks
2. Execute with `checkpoint(task_id, sub_task, status)` after each major sub-step
3. `reflect(task_id, output_summary, checklist_results)` → Get structured critique (use exact IDs)
4. Run external verification (lint/test/check) → `verify(task_id, results)`
5. If issues → `refine()` loop (max 3 iterations)
6. `learn(task_id, outcome, confidence_was, errors_found)` → Deliver

**Tier Classification (v3 — Risk-based, not length-based):**
- Pure observation verbs ("tell me", "show me", "what is", "check status", "how do I") → **Tier 1**, even for long messages
- State-change verbs ("build", "create", "write", "deploy", "delete", "commit") → **Tier 2 minimum**
- State-change + complexity signals ("comprehensive", "production", "from scratch") → **Tier 3**

### Follow-up Detection
If the user says "make it blue", "also add X", "change that" — `start_turn()` detects this as a follow-up and returns the SAME task_id from the previous task. Continue in context, don't restart from scratch.

### When the user corrects you:
→ Call `user_corrected(task_id, correction, error_category)` IMMEDIATELY. This is the highest-value learning signal (10x weight). VoxKage will permanently remember this mistake and flag it in future tasks.

### Cognitive Core Tools Reference

| Tool | When to call |
|---|---|
| `start_turn(user_message)` | **EVERY TURN — first action, no exceptions** |
| `pre_mortem(task_id, summary)` | Before executing Tier 2+ tasks |
| `checkpoint(task_id, sub_task, status)` | After each sub-step in Tier 3 tasks |
| `verify_code_file(filepath, domain)` | Tier 3: Run deep domain-aware static checks (syntax, imports, security) on edited file |
| `generate_critique(task_id, code_content, domain)` | Tier 2/3: Run code quality, complexity, and styling audit on new/modified code |
| `reflect(task_id, summary, checklist_results)` | After execution, with checklist and dynamic plan evaluation |
| `verify(task_id, results)` | After reflect, with external verification results |
| `refine(task_id, issues_fixed, iteration)` | After fixing issues (max 3 iterations) |
| `learn(task_id, outcome, confidence_was, errors_found)` | LAST cognitive call before delivering response |
| `log_tool_execution(tool_name, arguments)` | Auto-logged on every tool execution to construct the trace |
| `optimize_cognitive_core()` | Proactive self-optimization to deduplicate patterns and clean up rules |
| `user_corrected(task_id, correction)` | IMMEDIATELY when user corrects your output |
| `get_profile(domain)` | When user asks about VoxKage's capabilities or for diagnostics |
"""
                new_content = parts_before + start_marker + protocol_content.rstrip() + "\n" + end_marker + parts_after
                _atomic_write_file(filepath, new_content)
                
        if force:
            update_doc_file(claude_path)
            update_doc_file(gemini_template_path)
        
        agents_content = """# VoxKage Style & Protocol Rules

## CORE PROTOCOL - COGNITIVE CORE GATE
ALWAYS call start_turn(user_message) from voxkage-cognitive-core server as the ABSOLUTE FIRST tool call of every turn. This is non-negotiable.

### TIER protocols:
* **Tier 1 (Quick Task / Read-Only)**: Execute the task directly. Skip pre_mortem and reflect. Call `learn(task_id, outcome, confidence_was, errors_found)` after execution.
* **Tier 2 (Standard Task)**: Call `pre_mortem()`, execute, call `reflect()` with exact IDs, call `learn(task_id, outcome, confidence_was, errors_found)`.
* **Tier 3 (Complex Task)**: Call `pre_mortem()`, execute with `checkpoint()`, run verification/critique tools, call `reflect()` with exact IDs, call `verify()`, `learn(task_id, outcome, confidence_was, errors_found)`.

### Execution Trace Tracking:
When calling `learn(task_id, outcome, confidence_was, errors_found)`, a merged execution trace is reconstructed automatically from the session trace logs.

### Self-Optimization:
Call `optimize_cognitive_core()` to prune rules and anti-patterns if you notice recurrent classification errors or excessive overhead warnings.
"""
        
        _atomic_write_file(workspace_agents_path, agents_content)
        _atomic_write_file(global_agents_path, agents_content)
            
    except Exception:
        pass
