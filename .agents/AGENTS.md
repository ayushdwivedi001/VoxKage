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
- **FRONTEND**: 98.9% success rate (91/92 tasks)
  - Common Weaknesses: dependency_issue, In agent_loop.py, used lowercase 'false' instead of Python's capitalized 'False'
- **BACKEND**: 96.1% success rate (49/51 tasks)
  - Common Weaknesses: None - identified shared IP rate-limiting issue via local server test., None - diagnosed WebSocket error handling omission.
- **RESEARCH**: 98.2% success rate (56/57 tasks)
  - Common Weaknesses: Domain mismatch: coding checklist on research task, Domain mismatch: coding assigned on research/synthesis task — reflection scored against coding checklist items (syntax
- **SYSTEM**: 97.5% success rate (39/40 tasks)
  - Common Weaknesses: Ensure we verify the system disk before deletion, api_payload_error
- **CODING**: 94.2% success rate (114/121 tasks)
  - Common Weaknesses: Domain mismatch: coding checklist on research task, Domain mismatch: coding assigned on research/synthesis task — reflection scored against coding checklist items (syntax
- **GENERAL**: 100.0% success rate (45/45 tasks)
  - Common Weaknesses: verify() tool has colon-parsing bug in structured verification_results — splits on ':' inside values. Should use a different delimiter or accept JSON., 3 minor gaps found in v3 plan: P0's _DOMAIN_KEYWORDS loop is fragile (needs explicit extract function)
- **ANALYSIS**: 100.0% success rate (2/2 tasks)
  - Common Weaknesses: Domain classification mismatch: Cognitive core classified task as 'coding' when actual work was RESEARCH (primary) → GENERAL (synthesis). 8 coding-specific checklist items were excluded as inapplicable to markdown output. Minor: visualization checklist item reported as failed though visualizations are not applicable to text/markdown reports. The verification phase revealed Claude Opus 4.8 (May 28) had superseded Opus 4.7 during the report generation — caught during cross-check and corrected.

**Learned Negative Constraints (Anti-Patterns)**:
- **ALL**: Avoid repeating: Completed Tier 3 task but skipped reflect() quality check.
- **ALL**: Avoid repeating: Wasted Tier 3 overhead for simple task. Assigned Tier 3 but only used basic tools: run_command
- **ALL**: Avoid repeating: Stubborn consecutive calls to tool 'view_file' (3 times).
- **ALL**: Avoid repeating: Called heavy cognitive tools (pre_mortem/reflect) on a Tier 1 (Quick/Read-only) task.
- **CODING**: Avoid repeating: BTW queries failing on backend with 500 error; Drill failing with 'NoneType' object has no attribute 'startswith'.
- **FRONTEND**: Avoid repeating: I failed to call start_turn() before every turn, skipping it for multiple queries about git status, commits, RAG indexing, and follow-up questions. The user expects it as the ABSOLUTE FIRST action eve
- **SYSTEM**: Avoid repeating: DeepSeek API fails with HTTP 400 because message history contains messages with role 'laptop', which is not a supported API role (should be mapped to 'user' or 'assistant' or skipped).
- **FRONTEND**: Avoid repeating: In agent_loop.py, used lowercase 'false' instead of Python's capitalized 'False' inside the mobile_print_pdf tool schema dictionary default value, which caused a NameError at runtime.
- **BACKEND**: Avoid repeating: Metro builder does not support dynamic require calls with variables like require(name); it requires static string literals. Refactored the code to use separate require functions for each module.
- **BACKEND**: Avoid repeating: Agent loop failed: name 'get_tool_label' is not defined

**Recent Tasks Summary**:
- [2026-06-25] RESEARCH: SUCCESS (Confidence: 0.95)
- [2026-06-25] ANALYSIS: SUCCESS (Confidence: 0.92)
- [2026-06-25] CODING: SUCCESS (Confidence: 0.9)
<!-- SOUL_HISTORY_END -->
