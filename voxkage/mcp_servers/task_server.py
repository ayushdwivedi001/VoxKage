"""
MCP Server: VoxKage Parallel Task Manager

Enables VoxKage to spawn background Gemini CLI sub-agents for long-running
multi-step tasks, keeping the main session free for continued interaction.

Architecture:
  Main VoxKage session  ->  spawn_task()  ->  Hidden gemini CLI sub-agent
                                              (runs autonomously with full MCP access)
                                              calls complete_task() when done
  Main session          ->  check_tasks()  ->  Sees completed results

Tools for Main Agent:
  spawn_task(description, model)   --  Launch a background sub-agent
  check_tasks(status_filter)       --  Check all task statuses
  get_task_result(task_id)         --  Read full sub-agent output
  cancel_task(task_id)             --  Kill a running sub-agent

Tools for Sub-Agent (called by background gemini CLI):
  complete_task(task_id, summary, success, details)  --  Sub-agent reports completion

Run standalone: python mcp_servers/task_server.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-tasks")

# -- Storage -------------------------------------------------------------------
_MEM_DIR  = os.path.join(os.path.expanduser("~"), ".voxkage")
_TASK_FILE = os.path.join(_MEM_DIR, "tasks.jsonl")
_LOG_DIR   = os.path.join(_MEM_DIR, "task_logs")
os.makedirs(_LOG_DIR, exist_ok=True)

# -- Gemini CLI executable resolution -----------------------------------------
# Uses the shared paths module for cross-platform resolution.
from paths import find_gemini_cli as _paths_find_gemini
_GEMINI_EXE = _paths_find_gemini()

# -- Model catalogue & selection ------------------------------------------------
# Ordered by capability (most capable first within each tier)
_MODELS = {
    # Tier: HEAVY  --  complex multi-step research + document creation
    "pro-latest":   "gemini-3.1-pro-preview",
    "pro-stable":   "gemini-2.5-pro",
    # Tier: STANDARD  --  moderate multi-step, single-site research, simple tasks
    "flash-latest": "gemini-3-flash-preview",
    "flash-stable": "gemini-2.5-flash",
}

# Default choices per complexity tier
_MODEL_HEAVY    = _MODELS["pro-latest"]    # gemini-3.1-pro-preview
_MODEL_STANDARD = _MODELS["flash-latest"]  # gemini-3-flash-preview  (matches user's default)

# Aliases the user (or GEMINI.md) can pass as model_override
_MODEL_ALIASES = {
    "pro":              _MODELS["pro-latest"],
    "pro-preview":      _MODELS["pro-latest"],
    "3.1-pro":          _MODELS["pro-latest"],
    "gemini-3.1-pro":   _MODELS["pro-latest"],
    "gemini-3.1-pro-preview": _MODELS["pro-latest"],
    "2.5-pro":          _MODELS["pro-stable"],
    "gemini-2.5-pro":   _MODELS["pro-stable"],
    "flash":            _MODELS["flash-stable"],
    "2.5-flash":        _MODELS["flash-stable"],
    "gemini-2.5-flash": _MODELS["flash-stable"],
    "flash-preview":    _MODELS["flash-latest"],
    "3-flash":          _MODELS["flash-latest"],
}


# -- Helpers -------------------------------------------------------------------

def _select_model(description: str) -> tuple[str, str]:
    """
    Auto-selects the best model for the task complexity.

    HEAVY   (research + creation, deep analysis)   -> gemini-3.1-pro-preview
    STANDARD (single-step research, simple browse)  -> gemini-2.5-flash

    Returns (model_id, reason).
    """
    desc = description.lower()

    # Complexity signals
    research_hits = sum(1 for k in [
        "research", "analyze", "analyse", "comprehensive", "detailed",
        "compare", "comparison", "in-depth", "deep dive", "thesis",
        "essay", "multiple sites", "summarize from", "review and write",
        "report", "write a document", "write a word", "create a word",
        "make a document", "compile",
    ] if k in desc)

    creation_hits = sum(1 for k in [
        "create a", "make a", "write a", "generate a", "build a",
        ".docx", ".pdf", ".xlsx", "word file", "excel file", "document",
    ] if k in desc)

    chain_hits = sum(1 for k in [
        " and then ", " then ", " after that", " following that",
        " and create", " and write", " and make", " and compile",
        " and summarize", " and compare",
    ] if k in desc)

    # Decision tree
    if research_hits >= 1 and creation_hits >= 1:
        # Classic "research + write"  --  heaviest task type
        return _MODEL_HEAVY, f"Research+creation task ({research_hits} research, {creation_hits} creation indicators)"

    if research_hits >= 2 or chain_hits >= 2:
        # Deep research OR long chain = heavy
        return _MODEL_HEAVY, f"Complex task ({research_hits} research + {chain_hits} chain indicators)"

    if research_hits == 1 or chain_hits == 1 or creation_hits >= 1:
        # Moderate: single research step or simple creation
        return _MODEL_STANDARD, f"Moderate task ({research_hits}R+{chain_hits}C+{creation_hits}X indicators)"

    # Simple/quick task
    return _MODEL_STANDARD, "Simple task  --  flash model sufficient"


def _load_tasks() -> list[dict]:
    if not os.path.exists(_TASK_FILE):
        return []
    tasks = []
    with open(_TASK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    tasks.append(json.loads(line))
                except Exception:
                    pass
    return tasks


def _save_task(task: dict):
    with open(_TASK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(task, ensure_ascii=False) + "\n")


# ── File lock helper for tasks.jsonl ──────────────────────────────────────────
# On Windows, fcntl is unavailable. We use a .lock sentinel file approach:
# acquire = create the lock file (fail if exists), release = delete it.
# The lock protects _update_task and _append_step from simultaneous writes
# by the main session and running sub-agents.
import contextlib as _contextlib

@_contextlib.contextmanager
def _task_file_lock(timeout: float = 5.0):
    """Acquire a .lock sentinel file around tasks.jsonl operations.
    Retries for up to `timeout` seconds before giving up (returns without lock).
    """
    import time as _t
    lock_path = _TASK_FILE + ".lock"
    deadline = _t.monotonic() + timeout
    acquired = False
    while _t.monotonic() < deadline:
        try:
            # Exclusive create — fails if file already exists
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            _t.sleep(0.05)  # Retry every 50ms
        except Exception:
            break  # Unexpected error — proceed without lock
    try:
        yield
    finally:
        if acquired:
            try:
                os.unlink(lock_path)
            except Exception:
                pass


def _update_task(task_id: str, updates: dict) -> bool:
    with _task_file_lock():
        tasks = _load_tasks()
        found = False
        for t in tasks:
            if t.get("id") == task_id:
                t.update(updates)
                found = True
                break
        if not found:
            return False
        with open(_TASK_FILE, "w", encoding="utf-8") as f:
            for t in tasks:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return True


def _append_step(task_id: str, step: dict) -> bool:
    """Thread-safe: append a step entry to task['steps'] list."""
    with _task_file_lock():
        tasks = _load_tasks()
        for t in tasks:
            if t.get("id") == task_id:
                steps = t.get("steps") or []   # coerce None -> [] for old records
                steps.append(step)
                t["steps"] = steps
                t["current_step"] = step.get("action", "")
                t["step_updated_at"] = step.get("timestamp", datetime.now().isoformat())
                break
        with open(_TASK_FILE, "w", encoding="utf-8") as f:
            for t in tasks:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return True


def _build_sub_agent_prompt(task_id: str, description: str) -> str:
    """
    Builds the full prompt injected into the background gemini CLI session.
    Overrides VoxKage's JARVIS personality and mandates step logging.
    """
    return f"""[SUB-AGENT MODE  --  TASK {task_id}]
You are VoxKage Sub-Agent. You are running as a background process.
The user is interacting with the MAIN VoxKage session simultaneously.
Do NOT open any browser windows, Chrome, or visible UI  --  you are headless.

YOUR TASK:
{description}

=== MANDATORY EXECUTION RULES ===

1. STEP LOGGING  --  Call log_step() BEFORE and AFTER every major action:
   log_step(task_id="{task_id}", step_num=1, action="Searching web for X", status="started", details="")
   [do the action]
   log_step(task_id="{task_id}", step_num=1, action="Searching web for X", status="done", details="Found 5 results from NASA, Wikipedia")
   Log errors too: log_step(..., status="error", details="Error message + what you will retry")

2. AUTO-CONFIRM ALL FILE OPERATIONS  --  always pass confirmed=True:
   create_file(..., confirmed=True)
   edit_file(..., confirmed=True)
   delete_file(..., confirmed=True)
   convert_file(..., confirmed=True)
   clean_junk_files(..., confirmed=True)

3. USE HEADLESS TOOLS ONLY  --  you are a background process with no visible window:
   For web research: search_web(query=...) or browse_and_extract_tool(url=..., query=...)
   For YouTube: search_media_options(platform="youtube", query=...)
   Do NOT use agent_step with "goto" that opens a visible Chrome window unless essential.
   Prefer search_web for quick facts, browse_and_extract_tool for deep page reads.

4. COMPLETE THE TASK  --  do not stop until you have:
   a) Gathered all needed information
   b) Created/edited all requested files
   c) Verified the output exists

5. FINISH  --  call these TWO tools in order:
   a) complete_task():
      SUCCESS: complete_task(task_id="{task_id}", summary="Created X at path Y", success=True, details="Step-by-step of what was done")
      FAILURE: complete_task(task_id="{task_id}", summary="Failed because Z", success=False, details="What was attempted")
   b) THEN immediately notify_task_done():
      SUCCESS: notify_task_done(task_id="{task_id}", title="Task Complete", message="<1-2 sentence user-friendly summary of what was accomplished>")
      FAILURE: notify_task_done(task_id="{task_id}", title="Task Failed", message="<brief reason why it failed>")

6. Do NOT call spawn_task  --  you are already a sub-agent.
7. Do NOT ask the user for any input or confirmation  --  decide and execute.
8. Do NOT repeat personality rules  --  just execute.

STEP LOG FORMAT REFERENCE:
  step_num  = sequential number starting from 1
  action    = short description of what you are doing ("Searching NASA for black hole info")
  status    = "started" | "done" | "error" | "skipped"
  details   = relevant output, findings, or error message

BEGIN EXECUTING NOW. Start with log_step step 1 = your plan."""


# -- MCP Tools -----------------------------------------------------------------

@mcp.tool()
def spawn_task(
    description: str,
    model_override: str = "",
) -> str:
    """
    Spawn a background Gemini CLI sub-agent to execute a long-running or complex task.

    Use for ANY task that requires multi-step execution and would block the main session:
    - Research + file creation (e.g. "research X and write a Word doc")
    - Multi-site comparisons
    - Lengthy browser workflows
    - Any task involving 3+ sequential tool calls

    The sub-agent runs silently in the background with FULL access to all VoxKage MCP tools.
    It calls complete_task() when done. You can check progress with check_tasks().

    Parameters:
      description    : Full natural language task description for the sub-agent.
                       Be specific  --  include where to save files, what sites to use, etc.
      model_override : Force a specific model: "flash" or "pro". Leave blank for auto-select.

    Returns a task ID. Inform the user the task has been initiated and you will notify them when done.
    """
    task_id = str(uuid.uuid4())[:8]

    # Model resolution  --  alias -> canonical model ID from the 6-model catalogue
    vk_config_path = os.path.join(_MEM_DIR, "config.json")
    subagent_model = _MODEL_STANDARD
    try:
        if os.path.exists(vk_config_path):
            with open(vk_config_path, encoding="utf-8") as f:
                cfg = json.loads(f.read())
                subagent_model = cfg.get("subagent_model", _MODEL_STANDARD)
    except Exception:
        pass

    override_key = (model_override or "").lower().strip()
    if override_key:
        model = _MODEL_ALIASES.get(override_key, model_override)  # pass-through if exact model string
        model_reason = f"Specified: '{model_override}' -> {model}"
    else:
        model = subagent_model
        model_reason = f"From settings: {model}"

    # Build task record
    task = {
        "id":           task_id,
        "status":       "spawning",
        "description":  description,
        "model":        model,
        "model_reason": model_reason,
        "created_at":   datetime.now().isoformat(),
        "started_at":   None,
        "completed_at": None,
        "summary":      None,
        "details":      None,
        "success":      None,
        "pid":          None,
        "log_file":     os.path.join(_LOG_DIR, f"task_{task_id}.log"),
        "steps":        [],
        "current_step": "Initialising...",
        "step_updated_at": None,
    }
    _save_task(task)

    # Build the sub-agent prompt
    prompt = _build_sub_agent_prompt(task_id, description)

    # Launch background gemini CLI process
    # On Windows we must use the .cmd wrapper path directly.
    # shell=True is NOT used  --  it causes extra cmd.exe overhead and PID tracking issues.
    log_path = task["log_file"]
    try:
        # Build env for sub-agent: inherit parent env + set VOXKAGE_SUBAGENT=1
        # so file-ops MCP tools (create_file, edit_file, etc.) know they are
        # running inside a sub-agent and can auto-confirm without blocking.
        sub_env = os.environ.copy()
        sub_env["VOXKAGE_SUBAGENT"] = "1"

        with open(log_path, "w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                [
                    _GEMINI_EXE,
                    "--model", model,
                    "--approval-mode", "yolo",
                    "--prompt", prompt,
                ],
                cwd=_ROOT,
                env=sub_env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW |
                    subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                close_fds=False,
            )

        _update_task(task_id, {
            "status":     "running",
            "started_at": datetime.now().isoformat(),
            "pid":        proc.pid,
        })

        return (
            f"[TASK SPAWNED]\n"
            f"Task ID  : {task_id}\n"
            f"Model    : {model} ({model_reason})\n"
            f"Status   : Running in background (PID {proc.pid})\n"
            f"Task     : {description[:120]}{'...' if len(description) > 120 else ''}\n"
            f"Log      : {log_path}\n\n"
            f"The sub-agent is executing this autonomously. "
            f"Call check_tasks() at any time to see progress, or ask me to check when you want an update."
        )

    except FileNotFoundError:
        _update_task(task_id, {"status": "failed", "summary": "gemini CLI not found in PATH"})
        return (
            f"[ERROR] Could not spawn sub-agent  --  'gemini' command not found in PATH.\n"
            f"Make sure the Gemini CLI is installed and accessible from this terminal."
        )
    except Exception as e:
        _update_task(task_id, {"status": "failed", "summary": str(e)})
        return f"[ERROR] Failed to spawn sub-agent: {e}"


@mcp.tool()
def complete_task(
    task_id: str,
    summary: str,
    success: bool,
    details: str = "",
) -> str:
    """
    Called by a background sub-agent to report task completion.
    DO NOT call this from the main session  --  this is for sub-agents only.

    Parameters:
      task_id : The task ID provided at spawn
      summary : Brief 1-2 sentence summary of what was accomplished (or what failed)
      success : True if the task completed successfully, False if it failed
      details : Full details of actions taken, files created, URLs visited, etc.
    """
    status = "done" if success else "failed"
    updated = _update_task(task_id, {
        "status":       status,
        "summary":      summary,
        "details":      details,
        "success":      success,
        "completed_at": datetime.now().isoformat(),
    })

    if not updated:
        # Task record might be in a different session  --  create a new entry
        task = {
            "id":           task_id,
            "status":       status,
            "description":  "(completed by sub-agent)",
            "model":        "unknown",
            "created_at":   datetime.now().isoformat(),
            "started_at":   None,
            "completed_at": datetime.now().isoformat(),
            "summary":      summary,
            "details":      details,
            "success":      success,
            "pid":          None,
            "log_file":     None,
        }
        _save_task(task)

    icon = "[OK]" if success else "[FAIL]"
    return (
        f"{icon} Task {task_id} marked as {status}.\n"
        f"Summary recorded. The main VoxKage session will see this on next check_tasks() call."
    )


@mcp.tool()
def check_tasks(status_filter: str = "all") -> str:
    """
    Check the status of all background tasks, including live progress for running ones.

    Call this:
    - When the user asks "what happened with that task" / "is it done yet" / "where are you"
    - When the user says "check background tasks" / "update me" / "what's the sub-agent doing"
    - At the start of a new user message if a task was spawned earlier this session

    Parameters:
      status_filter : "all" | "running" | "done" | "failed" | "pending"
    """
    tasks = _load_tasks()
    if not tasks:
        return "[TASKS] No background tasks have been spawned yet."

    if status_filter != "all":
        tasks = [t for t in tasks if t.get("status") == status_filter]

    if not tasks:
        return f"[TASKS] No tasks with status '{status_filter}'."

    # Sort newest first - crash-safe
    tasks = sorted(tasks, key=lambda t: t.get("created_at", ""), reverse=True)

    lines = [f"[BACKGROUND TASKS] {len(tasks)} task(s):\n"]
    for t in tasks:
        try:
            task_id  = t.get("id") or "unknown"
            status   = t.get("status") or "unknown"
            model    = t.get("model") or "?"
            desc     = t.get("description") or "N/A"
            created  = (t.get("created_at") or "")[:19]
            icon = {"running": "[RUN]", "done": "[DONE]", "failed": "[FAIL]", "spawning": "[SPAWN]", "cancelled": "[CANCELLED]"}.get(status, "[?]")
            lines.append(f"{icon} [{task_id}] {status.upper()} -- {model}")
            lines.append(f"   Task     : {desc[:100]}")
            lines.append(f"   Created  : {created}")

            # Live progress for running tasks
            if status == "running":
                current_step = t.get("current_step") or "Initialising..."
                step_time    = (t.get("step_updated_at") or "")[:19]
                step_count   = len(t.get("steps") or [])
                lines.append(f"   Progress : Step {step_count} - {current_step}")
                if step_time:
                    lines.append(f"   Updated  : {step_time}")
                # Show last line of log for live feedback
                log_file = t.get("log_file")
                if log_file and os.path.exists(log_file):
                    try:
                        with open(log_file, encoding="utf-8", errors="ignore") as lf:
                            tail = lf.read()[-500:].strip()
                        if tail:
                            last_line = [l for l in tail.splitlines() if l.strip()][-1]
                            lines.append(f"   Log tail : {last_line[:120]}")
                    except Exception:
                        pass

            finished = t.get("completed_at")
            if finished:
                lines.append(f"   Finished : {str(finished)[:19]}")
            summary = t.get("summary")
            if summary:
                lines.append(f"   Result   : {summary}")
            lines.append("")
        except Exception as row_err:
            lines.append(f"[!] Malformed task record skipped: {row_err}")
            lines.append("")

    # Footer hints
    done_tasks    = [t for t in tasks if t.get("status") == "done"]
    failed_tasks  = [t for t in tasks if t.get("status") == "failed"]
    running_tasks = [t for t in tasks if t.get("status") == "running"]
    if done_tasks:
        lines.append(f"[DONE] {len(done_tasks)} task(s) complete -- call get_task_result('<id>') for output.")
        lines.append(f"   Call clear_completed_tasks() to clean up.")
    if failed_tasks:
        lines.append(f"[WARN] {len(failed_tasks)} task(s) failed -- call get_task_result('<id>') for error details.")
    if running_tasks:
        lines.append(f"[RUN] {len(running_tasks)} task(s) running -- call cancel_task('<id>') to stop one.")
        lines.append(f"   Call cancel_all_tasks() to stop everything at once.")

    return "\n".join(lines)


@mcp.tool()
def get_task_result(task_id: str) -> str:
    """
    Get the full details of a completed background task  --  what the sub-agent did,
    what was created, and what errors occurred.

    Use when user says "what did the research task find" / "show me results of task X".

    Parameters:
      task_id : The task ID (from spawn_task or check_tasks output)
    """
    tasks = _load_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)

    if not task:
        return f"[TASKS] No task found with ID '{task_id}'."

    status = task.get("status", "?")
    icon = {"done": "[OK]", "failed": "[FAIL]", "running": "[RUN]"}.get(status, "*")

    lines = [
        f"=== TASK RESULT: {task_id} ===",
        f"Status    : {icon} {status.upper()}",
        f"Model     : {task.get('model', 'N/A')}",
        f"Created   : {task.get('created_at', 'N/A')[:19]}",
        f"Completed : {task.get('completed_at', 'N/A')[:19] if task.get('completed_at') else 'Still running'}",
        f"",
        f"TASK DESCRIPTION:",
        f"  {task.get('description', 'N/A')}",
        f"",
        f"SUMMARY:",
        f"  {task.get('summary', 'No summary yet  --  task may still be running.')}",
    ]

    # Step-by-step execution history
    steps = task.get("steps") or []   # coerce None -> [] for old records
    if steps:
        lines += ["", "EXECUTION STEPS:"]
        for s in steps:
            step_icon = {"done": "OK", "started": "->", "error": "!!", "skipped": "--"}.get(s.get("status", ""), "*")
            lines.append(f"  {step_icon} Step {s.get('step_num', '?')} [{s.get('status', '?').upper()}]  --  {s.get('action', '')}")
            if s.get("details"):
                lines.append(f"      {s['details'][:200]}")

    if task.get("details"):
        lines += [
            f"",
            f"FULL DETAILS:",
            task.get("details", ""),
        ]

    log_file = task.get("log_file")
    if log_file and os.path.exists(log_file):
        try:
            with open(log_file, encoding="utf-8", errors="ignore") as f:
                log_content = f.read()
            if log_content.strip():
                lines += [
                    f"",
                    f"SUB-AGENT LOG (last 2000 chars):",
                    f"---",
                    log_content[-2000:],
                    f"---",
                ]
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def log_step(
    task_id: str,
    step_num: int,
    action: str,
    status: str,
    details: str = "",
) -> str:
    """
    Called by background sub-agents to log each step of execution in real-time.
    The main VoxKage session reads these steps via check_tasks() to show live progress.

    *** THIS TOOL IS FOR SUB-AGENTS ONLY. Call it before and after every major action. ***

    Parameters:
      task_id  : The task ID provided at spawn
      step_num : Sequential step number (1, 2, 3...)
      action   : Short description of what you are doing ("Searching NASA for black holes")
      status   : "started" | "done" | "error" | "skipped"
      details  : Findings, results, error messages, or relevant output
    """
    step = {
        "step_num":  step_num,
        "action":    action,
        "status":    status,
        "details":   details,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        _append_step(task_id, step)
    except Exception as e:
        return f"[log_step WARNING] Could not write step (non-fatal): {e}. Continuing."
    icon = {"done": "OK", "started": "->", "error": "!!", "skipped": "--"}.get(status, "?")
    return f"{icon} Step {step_num} [{status}] logged for task {task_id}: {action}"


@mcp.tool()
def clear_task(task_id: str) -> str:
    """
    Remove a specific task from the task list  --  done, failed, or cancelled tasks.

    Use when the user says:
    - "clear task abc123"
    - "remove that task"
    - "delete the failed task"
    - "clean up tasks"

    Parameters:
      task_id : The task ID to remove (from check_tasks output)
    """
    tasks = _load_tasks()
    original_count = len(tasks)
    tasks = [t for t in tasks if t.get("id") != task_id]

    if len(tasks) == original_count:
        return f"[TASKS] No task found with ID '{task_id}'  --  nothing was removed."

    with open(_TASK_FILE, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    return f"[TASKS] Task '{task_id}' has been removed from the task list."


@mcp.tool()
def clear_completed_tasks() -> str:
    """
    Auto-clean: remove all successfully completed ('done') tasks from the task list.
    Failed and running tasks are kept.

    Call when:
    - User says "clear completed tasks" / "clean up done tasks" / "clear the task list"
    - After reporting results to the user so the list stays clean
    - Automatically after check_tasks reveals several done tasks
    """
    tasks = _load_tasks()
    done   = [t for t in tasks if t.get("status") == "done"]
    keep   = [t for t in tasks if t.get("status") != "done"]

    if not done:
        return "[TASKS] No completed tasks to clear."

    with open(_TASK_FILE, "w", encoding="utf-8") as f:
        for t in keep:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    ids = ", ".join(t["id"] for t in done)
    return (
        f"[TASKS] Cleared {len(done)} completed task(s): [{ids}]\n"
        f"Kept {len(keep)} task(s) (running/failed/pending)."
    )


@mcp.tool()
def cancel_task(task_id: str) -> str:
    """
    Cancel a running background sub-agent task.

    Use when user says "cancel that task" / "stop the background research".

    Parameters:
      task_id : The task ID to cancel
    """
    import signal
    tasks = _load_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)

    if not task:
        return f"[TASKS] No task found with ID '{task_id}'."

    status = task.get("status", "?")
    if status in ("done", "failed", "cancelled"):
        return f"[TASKS] Task '{task_id}' is already {status}  --  nothing to cancel."

    pid = task.get("pid")
    if pid:
        try:
            import psutil
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
            _update_task(task_id, {
                "status": "cancelled",
                "summary": "Cancelled by user",
                "completed_at": datetime.now().isoformat(),
            })
            return f"[TASKS] Task '{task_id}' (PID {pid}) has been cancelled."
        except Exception as e:
            _update_task(task_id, {"status": "cancelled", "summary": f"Cancelled (kill attempt: {e})"})
            return f"[TASKS] Task '{task_id}' marked cancelled (process may have already finished)."

    _update_task(task_id, {"status": "cancelled", "summary": "Cancelled by user (no PID recorded)"})
    return f"[TASKS] Task '{task_id}' marked as cancelled."


@mcp.tool()
def cancel_all_tasks() -> str:
    """
    Cancel ALL currently running or spawning background tasks at once.
    Use when user says "stop all tasks", "cancel everything", "kill all background jobs".
    """
    tasks = _load_tasks()
    running = [t for t in tasks if t.get("status") in ("running", "spawning")]
    if not running:
        return "[TASKS] No running tasks to cancel."

    results = []
    for t in running:
        pid = t.get("pid")
        task_id = t.get("id", "?")
        if pid:
            try:
                import psutil
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    try: child.kill()
                    except Exception: pass
                proc.kill()
            except Exception:
                pass
        _update_task(task_id, {
            "status": "cancelled",
            "summary": "Cancelled by user (cancel_all)",
            "completed_at": datetime.now().isoformat(),
        })
        results.append(task_id)

    return f"[TASKS] Cancelled {len(results)} task(s): {', '.join(results)}"


@mcp.tool()
def clear_all_tasks() -> str:
    """
    Remove ALL tasks from the task list (done, failed, cancelled, and running).
    Use when user says "wipe all tasks", "clear everything", "fresh start on tasks".
    Running tasks are cancelled before clearing.
    """
    tasks = _load_tasks()
    if not tasks:
        return "[TASKS] Task list is already empty."

    for t in tasks:
        if t.get("status") in ("running", "spawning") and t.get("pid"):
            try:
                import psutil
                proc = psutil.Process(t["pid"])
                for child in proc.children(recursive=True):
                    try: child.kill()
                    except Exception: pass
                proc.kill()
            except Exception:
                pass

    count = len(tasks)
    with open(_TASK_FILE, "w", encoding="utf-8") as f:
        pass  # truncate

    return f"[TASKS] Cleared all {count} task(s). Task list is now empty."




# ── Checkpoint helpers ─────────────────────────────────────────────────────────

import shutil as _shutil
import zipfile as _zipfile
import hashlib as _hashlib

_CHECKPOINT_DIR = os.path.join(_MEM_DIR, "checkpoints")
os.makedirs(_CHECKPOINT_DIR, exist_ok=True)


def _project_hash(project_dir: str) -> str:
    """Short stable hash of the project path for naming snapshots."""
    return _hashlib.md5(project_dir.encode()).hexdigest()[:8]


def _create_checkpoint(project_dir: str, label: str = "pre-evolution") -> dict:
    """
    Create a safe restore point for the given project directory.

    Strategy:
      1. If the directory contains a .git folder → use git commit.
         Returns: {method: "git", checkpoint_id: "<sha>", rollback_cmd: "git reset --hard <sha>"}
      2. Fallback → create a timestamped .zip snapshot in ~/.voxkage/checkpoints/.
         Returns: {method: "zip", checkpoint_id: "<path>", rollback_cmd: "<path>"}

    Raises RuntimeError if neither method succeeds.
    """
    project_dir = os.path.abspath(project_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Git checkpoint ─────────────────────────────────────────────────────────
    git_dir = os.path.join(project_dir, ".git")
    if os.path.isdir(git_dir):
        try:
            # Stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=project_dir, capture_output=True, timeout=30, check=True,
            )
            # Commit — ignore if nothing to commit
            commit_msg = f"VoxKage Auto-Checkpoint: {label} [{ts}]"
            result = subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=project_dir, capture_output=True, timeout=30, text=True,
            )
            if result.returncode not in (0, 1):  # 1 = nothing to commit
                raise RuntimeError(result.stderr)

            # Get HEAD SHA
            sha_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_dir, capture_output=True, timeout=10, text=True, check=True,
            )
            sha = sha_result.stdout.strip()
            return {
                "method":       "git",
                "checkpoint_id": sha,
                "project_dir":  project_dir,
                "rollback_cmd": f"git reset --hard {sha}",
                "created_at":   ts,
            }
        except Exception as e:
            # Git failed — fall through to zip
            print(f"[Checkpoint] Git checkpoint failed ({e}), falling back to zip.")

    # ── Zip snapshot fallback ──────────────────────────────────────────────────
    ph    = _project_hash(project_dir)
    label_safe = label.replace(" ", "_").replace("/", "_")
    zip_name   = f"{ph}_{label_safe}_{ts}"
    zip_path   = os.path.join(_CHECKPOINT_DIR, zip_name)

    _shutil.make_archive(zip_path, "zip", project_dir)
    full_zip = zip_path + ".zip"
    if not os.path.isfile(full_zip):
        raise RuntimeError(f"Zip archive was not created at {full_zip}")

    return {
        "method":        "zip",
        "checkpoint_id": full_zip,
        "project_dir":   project_dir,
        "rollback_cmd":  f"__ZIP_RESTORE__|{full_zip}|{project_dir}",
        "created_at":    ts,
    }


def _rollback_checkpoint(checkpoint: dict) -> str:
    """
    Restore the project to a previously created checkpoint.
    Works for both git and zip methods.
    Returns a human-readable result string.
    """
    method      = checkpoint.get("method")
    project_dir = checkpoint.get("project_dir", "")
    rollback    = checkpoint.get("rollback_cmd", "")

    if method == "git":
        try:
            subprocess.run(
                rollback.split(),
                cwd=project_dir, capture_output=True, timeout=30, check=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=project_dir, capture_output=True, timeout=30, check=True,
            )
            return f"[Rollback] ✓ Git reset to {checkpoint['checkpoint_id'][:12]} and cleaned untracked files in {project_dir}"
        except Exception as e:
            return f"[Rollback] ✗ Git reset failed: {e}"

    elif method == "zip":
        try:
            zip_path, target_dir = rollback.split("|")[1], rollback.split("|")[2]
            # Wipe and restore
            if os.path.isdir(target_dir):
                _shutil.rmtree(target_dir)
            os.makedirs(target_dir, exist_ok=True)
            with _zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(target_dir)
            return f"[Rollback] ✓ Zip snapshot restored to {target_dir}"
        except Exception as e:
            return f"[Rollback] ✗ Zip restore failed: {e}"

    return "[Rollback] ✗ Unknown checkpoint method."


@mcp.tool()
def restore_checkpoint(task_id: str) -> str:
    """
    Restore a project to its safety checkpoint if an evolution task fails.
    
    Parameters:
      task_id : The task ID whose checkpoint should be restored.
    """
    tasks = _load_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        return f"[ERROR] Task '{task_id}' not found."
    
    checkpoint = task.get("checkpoint")
    if not checkpoint:
        return f"[ERROR] Task '{task_id}' has no safety checkpoint."
        
    return _rollback_checkpoint(checkpoint)


def _build_evolution_prompt(task_id: str, description: str, project_dir: str,
                             test_command: str, checkpoint: dict) -> str:
    method   = checkpoint["method"]
    ts       = checkpoint.get("created_at", "")

    return f"""[EVOLUTION SUB-AGENT — TASK {task_id}]
You are VoxKage Self-Healing Sub-Agent. You are running HEADLESSLY in the background.
Do NOT open any visible windows or browser UI.

PROJECT DIRECTORY: {project_dir}
FEATURE TO BUILD: {description}

=== SAFE RESTORE POINT ===
Method : {method}
Created: {ts}

If ANYTHING goes wrong or tests fail, you MUST execute restore_checkpoint(task_id="{task_id}")
before calling complete_task(success=False).

=== MANDATORY EXECUTION PROTOCOL ===

STEP 1 — PLAN
  log_step(task_id="{task_id}", step_num=1, action="Planning implementation", status="started", details="")
  Think through the implementation. Write a test file first (test_<feature>.py or equivalent).
  log_step(task_id="{task_id}", step_num=1, action="Planning implementation", status="done", details="<your plan>")

STEP 2 — WRITE TEST FIRST
  log_step(task_id="{task_id}", step_num=2, action="Writing test file", status="started", details="")
  Create a test file: <project_dir>/test_evolution_{task_id}.py
  The test should import the feature and assert correct behaviour.
  log_step(task_id="{task_id}", step_num=2, action="Writing test file", status="done", details="")

STEP 3 — IMPLEMENT THE FEATURE
  log_step(task_id="{task_id}", step_num=3, action="Implementing feature", status="started", details="")
  Edit the relevant files. Use confirmed=True for all file operations.
  log_step(task_id="{task_id}", step_num=3, action="Implementing feature", status="done", details="")

STEP 4 — RUN TESTS
  log_step(task_id="{task_id}", step_num=4, action="Running tests", status="started", details="")
  Run: {"python test_evolution_" + task_id + ".py" if not test_command else test_command}
  Capture the output. Check for failures.
  log_step(task_id="{task_id}", step_num=4, action="Running tests", status="<done|error>", details="<output>")

STEP 5a — IF TESTS PASSED:
  Delete the test file: test_evolution_{task_id}.py
  log_step(task_id="{task_id}", step_num=5, action="Tests passed — deploying", status="done", details="")
  complete_task(task_id="{task_id}", summary="Feature deployed successfully: {description[:80]}", success=True,
                details="All tests passed. Feature is live.")
  notify_task_done(task_id="{task_id}", title="✅ Evolution Complete",
                   message="New feature deployed: {description[:60]}")

STEP 5b — IF TESTS FAILED:
  log_step(task_id="{task_id}", step_num=5, action="Tests failed — rolling back", status="error", details="<failure output>")
  Call restore_checkpoint(task_id="{task_id}")
  complete_task(task_id="{task_id}", summary="Feature FAILED tests — rolled back to checkpoint", success=False,
                details="<what went wrong>")
  notify_task_done(task_id="{task_id}", title="⚠️ Evolution Rolled Back",
                   message="Tests failed. Code restored to checkpoint. Reason: <brief reason>")

=== ABSOLUTE RULES ===
- Do NOT call spawn_task — you are already a sub-agent.
- Do NOT ask for user input — decide and act.
- Do NOT skip the rollback on failure — this is critical to data integrity.
- The checkpoint restoration will automatically remove any newly created untracked test files.
- complete_task() MUST be called exactly once before this process exits.

BEGIN NOW. Start with STEP 1."""


# ── spawn_evolution_task MCP tool ──────────────────────────────────────────────

@mcp.tool()
def spawn_evolution_task(
    description: str,
    project_dir: str,
    test_command: str = "",
    model_override: str = "",
) -> str:
    """
    Spawn a protected background sub-agent to build a new feature or fix for a project.

    This is the SAFE way to let VoxKage modify its own code or any project code.
    Before touching any files, it automatically creates a restore checkpoint (Git commit
    if the project has Git, or a zip snapshot if not). If the tests fail, the sub-agent
    automatically rolls back to that checkpoint.

    Workflow:
      1. Create safe checkpoint (git commit OR zip snapshot)
      2. Spawn background sub-agent
      3. Sub-agent writes a test file → implements the feature → runs tests
      4. Tests pass → feature deployed, test file deleted
      5. Tests fail → checkpoint rolled back automatically

    Parameters:
      description  : Natural language description of the feature to build or bug to fix.
      project_dir  : Absolute path to the project directory to modify.
      test_command : Optional custom test command (e.g. "pytest tests/"). Defaults to
                     running the auto-generated test file.
      model_override: Optional model to use. Leave blank for auto-selection.

    Returns a task ID. The main session will be notified automatically when the task completes.
    """
    project_dir = os.path.abspath(project_dir)
    if not os.path.isdir(project_dir):
        return f"[ERROR] Project directory does not exist: {project_dir}"

    task_id = str(uuid.uuid4())[:8]

    # ── Model selection ────────────────────────────────────────────────────────
    # Check ~/.voxkage/config.json first, then fall back to auto-select
    vk_config_path = os.path.join(_MEM_DIR, "config.json")
    subagent_model = _MODEL_STANDARD
    try:
        if os.path.exists(vk_config_path):
            cfg = json.loads(open(vk_config_path, encoding="utf-8").read())
            subagent_model = cfg.get("subagent_model", _MODEL_STANDARD)
    except Exception:
        pass

    override_key = (model_override or "").lower().strip()
    if override_key:
        model = _MODEL_ALIASES.get(override_key, model_override)
        model_reason = f"Specified: {model_override}"
    else:
        model = subagent_model
        model_reason = f"From settings: {model}"

    # ── Create checkpoint BEFORE doing anything ────────────────────────────────
    try:
        checkpoint = _create_checkpoint(project_dir, label=f"pre-{task_id}")
    except Exception as e:
        return (
            f"[ERROR] Could not create safety checkpoint for {project_dir}.\n"
            f"Reason: {e}\n"
            f"Aborting evolution task for safety — no files have been modified."
        )

    # ── Build task record ──────────────────────────────────────────────────────
    task = {
        "id":           task_id,
        "type":         "evolution",
        "status":       "spawning",
        "description":  description,
        "project_dir":  project_dir,
        "checkpoint":   checkpoint,
        "model":        model,
        "model_reason": model_reason,
        "created_at":   datetime.now().isoformat(),
        "started_at":   None,
        "completed_at": None,
        "summary":      None,
        "details":      None,
        "success":      None,
        "pid":          None,
        "log_file":     os.path.join(_LOG_DIR, f"task_{task_id}.log"),
        "steps":        [],
        "current_step": "Checkpoint created — spawning sub-agent...",
        "step_updated_at": None,
    }
    _save_task(task)

    # ── Build and launch sub-agent ─────────────────────────────────────────────
    prompt   = _build_evolution_prompt(task_id, description, project_dir, test_command, checkpoint)
    log_path = task["log_file"]

    try:
        sub_env = os.environ.copy()
        sub_env["VOXKAGE_SUBAGENT"] = "1"

        with open(log_path, "w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                [_GEMINI_EXE, "--model", model, "--approval-mode", "yolo", "--prompt", prompt],
                cwd=project_dir,
                env=sub_env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=False,
            )

        _update_task(task_id, {
            "status":     "running",
            "started_at": datetime.now().isoformat(),
            "pid":        proc.pid,
        })

        cp_method = checkpoint["method"]
        cp_id     = checkpoint["checkpoint_id"]
        cp_short  = cp_id[:12] if cp_method == "git" else os.path.basename(cp_id)

        return (
            f"[EVOLUTION TASK SPAWNED]\n"
            f"Task ID     : {task_id}\n"
            f"Model       : {model}\n"
            f"Project     : {project_dir}\n"
            f"Checkpoint  : {cp_method.upper()} — {cp_short}\n"
            f"Status      : Running in background (PID {proc.pid})\n"
            f"Goal        : {description[:120]}{'...' if len(description) > 120 else ''}\n\n"
            f"The sub-agent will write tests, implement the feature, and deploy only if tests pass.\n"
            f"If anything fails, the checkpoint will be automatically restored.\n"
            f"Call check_tasks() at any time to see progress."
        )

    except FileNotFoundError:
        _update_task(task_id, {"status": "failed", "summary": "gemini CLI not found"})
        return "[ERROR] 'gemini' command not found in PATH. Cannot spawn evolution sub-agent."
    except Exception as e:
        _update_task(task_id, {"status": "failed", "summary": str(e)})
        return f"[ERROR] Failed to spawn evolution sub-agent: {e}"


if __name__ == "__main__":
    mcp.run()

