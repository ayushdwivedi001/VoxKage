# VoxKage Style & Protocol Rules

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

---

<!-- SOUL_HISTORY_START -->
### VoxKage Consolidated Soul History & Performance

**Domain Metrics**:
- **FRONTEND**: 98.9% success rate (93/94 tasks)
  - Common Weaknesses: dependency_issue, In agent_loop.py, used lowercase 'false' instead of Python's capitalized 'False'
- **BACKEND**: 96.6% success rate (56/58 tasks)
  - Common Weaknesses: Folder locked by python http.server 8000 process, None — all specs cross-verified against 8+ independent sources (morphllm
- **RESEARCH**: 98.8% success rate (79/80 tasks)
  - Common Weaknesses: Wikipedia, Spheron
- **SYSTEM**: 97.7% success rate (43/44 tasks)
  - Common Weaknesses: Ensure we verify the system disk before deletion, api_payload_error
- **CODING**: 95.0% success rate (133/140 tasks)
  - Common Weaknesses: None — all claims cross-verified across 4+ independent sources (Sakana AI official site, sakutto.ai
- **GENERAL**: 98.1% success rate (51/52 tasks)
  - Common Weaknesses: Domain mismatch: CODING secondary domain triggered 12 coding-specific checklist items (syntax_valid, imports_correct
- **ANALYSIS**: 100.0% success rate (8/8 tasks)
  - Common Weaknesses: Domain classification mismatch: Cognitive core classified task as 'coding' when actual work was RESEARCH (primary) → GENERAL (synthesis). 8 coding-specific checklist items were excluded as inapplicable to markdown output. Minor: visualization checklist item reported as failed though visualizations are not applicable to text/markdown reports. The verification phase revealed Claude Opus 4.8 (May 28) had superseded Opus 4.7 during the report generation — caught during cross-check and corrected., None — all claims cross-verified against 8+ independent sources. Minor: initial draft listed Cursor as "most popular" but Claude Code actually holds 54% market share — caught during verification phase and corrected.
- **PLANNING**: 100.0% success rate (5/5 tasks)
  - Common Weaknesses: None — all data from live fetched documentation of all 5 platforms, None
- **DEVOPS**: 100.0% success rate (2/2 tasks)
  - Common Weaknesses: Domain mismatch: coding and devops secondary domains assigned to research task — 16 irrelevant coding-specific checklist items scored against a markdown report, artificially lowering combined score from 93% (research-only) to 47% (combined). The system auto-evolved a fix: exclude coding secondary items when domain is research and output is documentation.
- **DATA**: 100.0% success rate (4/4 tasks)
  - Common Weaknesses: None, None — all data was provided in structured JSON format

**Learned Negative Constraints (Anti-Patterns)**:
- **ALL**: Avoid repeating: Completed Tier 3 task but skipped reflect() quality check.
- **ALL**: Avoid repeating: Wasted Tier 3 overhead for simple task. Assigned Tier 3 but only used basic tools: run_command
- **ALL**: Avoid repeating: Called heavy cognitive tools (pre_mortem/reflect) on a Tier 1 (Quick/Read-only) task.
- **ALL**: Avoid repeating: Stubborn consecutive calls to tool 'view_file' (3 times).
- **CODING**: Avoid repeating: BTW queries failing on backend with 500 error; Drill failing with 'NoneType' object has no attribute 'startswith'.
- **BACKEND**: Avoid repeating: Metro builder does not support dynamic require calls with variables like require(name); it requires static string literals. Refactored the code to use separate require functions for each module.
- **BACKEND**: Avoid repeating: Agent loop failed: name 'get_tool_label' is not defined
- **FRONTEND**: Avoid repeating: I failed to call start_turn() before every turn, skipping it for multiple queries about git status, commits, RAG indexing, and follow-up questions. The user expects it as the ABSOLUTE FIRST action eve
- **FRONTEND**: Avoid repeating: In agent_loop.py, used lowercase 'false' instead of Python's capitalized 'False' inside the mobile_print_pdf tool schema dictionary default value, which caused a NameError at runtime.
- **SYSTEM**: Avoid repeating: DeepSeek API fails with HTTP 400 because message history contains messages with role 'laptop', which is not a supported API role (should be mapped to 'user' or 'assistant' or skipped).

**Recent Tasks Summary**:
- [2026-06-27] RESEARCH: SUCCESS (Confidence: 1.0)
- [2026-06-27] GENERAL: SUCCESS (Confidence: 0.98)
- [2026-06-27] CODING: SUCCESS (Confidence: 1.0)
<!-- SOUL_HISTORY_END -->
