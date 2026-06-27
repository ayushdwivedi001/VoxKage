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
- **FRONTEND**: 100.0% success rate (1/1 tasks)
- **BACKEND**: 0% success rate (0/0 tasks)
- **RESEARCH**: 100.0% success rate (1/1 tasks)
- **SYSTEM**: 0% success rate (0/0 tasks)
  - Common Weaknesses: Ensure we verify the system disk before deletion
- **CODING**: 75.0% success rate (6/8 tasks)
  - Common Weaknesses: Domain mismatch: coding checklist on research task
- **ANALYSIS**: 0% success rate (0/0 tasks)
- **PLANNING**: 0% success rate (0/0 tasks)
- **GENERAL**: 0% success rate (0/0 tasks)
- **DATA**: 0% success rate (0/0 tasks)
- **DEVOPS**: 0% success rate (0/0 tasks)

**Learned Negative Constraints (Anti-Patterns)**:
- **ALL**: Avoid repeating: Completed Tier 3 task but skipped reflect() quality check.
- **ALL**: Avoid repeating: Wasted Tier 3 overhead for simple task. Assigned Tier 3 but only used basic tools: turn
- **ALL**: Avoid repeating: Called heavy cognitive tools (pre_mortem/reflect) on a Tier 1 (Quick/Read-only) task.
- **SYSTEM**: Avoid repeating: Ensure we verify the system disk before deletion
- **ALL**: Avoid repeating: Stubborn consecutive calls to tool 'file' with arguments 'a.py' (3 times).

**Recent Tasks Summary**:
- [2026-06-27] FRONTEND: SUCCESS (Confidence: 0.5)
- [2026-06-27] CODING: SUCCESS (Confidence: 0.5)
- [2026-06-27] CODING: SUCCESS (Confidence: 0.9)
<!-- SOUL_HISTORY_END -->
