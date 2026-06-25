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
- **FRONTEND**: 98.8% success rate (79/80 tasks)
  - Common Weaknesses: dependency_issue, In agent_loop.py, used lowercase 'false' instead of Python's capitalized 'False'
- **BACKEND**: 95.9% success rate (47/49 tasks)
  - Common Weaknesses: None, none
- **RESEARCH**: 97.2% success rate (35/36 tasks)
  - Common Weaknesses: The user requested not to check the EAS build status, but to simply run deploy.p, missing_feature
- **SYSTEM**: 97.5% success rate (39/40 tasks)
  - Common Weaknesses: api_payload_error, None
- **CODING**: 98.6% success rate (70/71 tasks)
  - Common Weaknesses: FileSystem downloadAsync is deprecated in modern expo-file-system SDK 56 and thr, BTW queries failing on backend with 500 error; Drill failing with 'NoneType' obj
- **GENERAL**: 100.0% success rate (9/9 tasks)
  - Common Weaknesses: false_positive, none

**Learned Negative Constraints (Anti-Patterns)**:
- **ALL**: Avoid repeating: Stubborn consecutive calls to tool 'view_file' (3 times).
- **ALL**: Avoid repeating: Called heavy cognitive tools (pre_mortem/reflect) on a Tier 1 (Quick/Read-only) task.
- **ALL**: Avoid repeating: Wasted Tier 3 overhead for simple task. Assigned Tier 3 but only used basic tools: run_command
- **ALL**: Avoid repeating: Completed Tier 3 task but skipped reflect() quality check.
- **FRONTEND**: Avoid repeating: I failed to call start_turn() before every turn, skipping it for multiple queries about git status, commits, RAG indexing, and follow-up questions. The user expects it as the ABSOLUTE FIRST action eve
- **CODING**: Avoid repeating: BTW queries failing on backend with 500 error; Drill failing with 'NoneType' object has no attribute 'startswith'.
- **SYSTEM**: Avoid repeating: DeepSeek API fails with HTTP 400 because message history contains messages with role 'laptop', which is not a supported API role (should be mapped to 'user' or 'assistant' or skipped).
- **FRONTEND**: Avoid repeating: In agent_loop.py, used lowercase 'false' instead of Python's capitalized 'False' inside the mobile_print_pdf tool schema dictionary default value, which caused a NameError at runtime.
- **BACKEND**: Avoid repeating: Metro builder does not support dynamic require calls with variables like require(name); it requires static string literals. Refactored the code to use separate require functions for each module.
- **BACKEND**: Avoid repeating: Agent loop failed: name 'get_tool_label' is not defined

**Recent Tasks Summary**:
- [2026-06-25] GENERAL: SUCCESS (Confidence: 0.95)
- [2026-06-25] GENERAL: SUCCESS (Confidence: 0.5)
- [2026-06-25] GENERAL: SUCCESS (Confidence: 0.5)
<!-- SOUL_HISTORY_END -->
