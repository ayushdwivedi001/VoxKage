"""
MCP Server: VoxKage Agentic Coding Engine — ACE (voxkage-coding)

The reasoning brain that transforms any model tier (Flash Lite → Pro) into a
methodical, plan-driven developer.  Every coding task passes through a
mandatory 5-phase pipeline:

  Phase 0  Problem Decomposition   → micro-issue breakdown
  Phase 1  RAG-First Awareness     → index & query before touching code
  Phase 2  Knowledge Gap Fill      → browse docs if tech is unknown
  Phase 3  Plan → Todo List        → write active_plan.md
  Phase 4  Execute → Verify → Tick → per-task loop with test commands
  Phase 5  Final System Check      → syntax + server verification

Tools:
  coding_thinking(goal, project_dir)         — ACE entry: index, RAG, plan
  get_code_skeleton(file_path)               — AST skeleton (97 % token save)
  update_coding_plan(step_number, status)    — tick / fail a plan step
  get_coding_plan()                          — read the current active plan

Run standalone: python mcp_servers/coding_server.py
"""

import os
import sys
import ast
import re
import json
import logging
import time
from pathlib import Path
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from _env import load_voxkage_env
load_voxkage_env()

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("voxkage-coding")
logger = logging.getLogger(__name__)

# ── Storage ───────────────────────────────────────────────────────────────────
_BRAIN_DIR = Path(r"C:\VoxKage\Brain")
_BRAIN_DIR.mkdir(parents=True, exist_ok=True)
_PLAN_FILE = _BRAIN_DIR / "active_plan.md"
_SCRATCH_DIR = _BRAIN_DIR / "scratch"
_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _ast_skeleton_python(file_path: str) -> str:
    """
    Parse a Python file with the ast module and return a compact skeleton
    showing only imports, class names, method signatures, and docstrings.
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return f"[SYNTAX ERROR in {os.path.basename(file_path)}] {e}"
    except Exception as e:
        return f"[PARSE ERROR] {e}"

    lines = [f"# Skeleton: {os.path.basename(file_path)}", ""]

    # Imports
    imports = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name for a in node.names[:5])
            if len(node.names) > 5:
                names += ", ..."
            imports.append(f"from {module} import {names}")
    if imports:
        lines.append("# ── Imports ──")
        lines.extend(imports[:20])  # Cap for sanity
        if len(imports) > 20:
            lines.append(f"# ... and {len(imports) - 20} more imports")
        lines.append("")

    # Top-level functions and classes
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            sig = _format_func_sig(node)
            lines.append(f"{prefix}def {sig}:")
            docstring = ast.get_docstring(node)
            if docstring:
                short = docstring.split("\n")[0].strip()[:120]
                lines.append(f'    """{short}"""')
            lines.append("")

        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(_name_of(b) for b in node.bases[:3])
            lines.append(f"class {node.name}({bases}):")
            docstring = ast.get_docstring(node)
            if docstring:
                short = docstring.split("\n")[0].strip()[:120]
                lines.append(f'    """{short}"""')

            # Class methods
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async " if isinstance(item, ast.AsyncFunctionDef) else ""
                    sig = _format_func_sig(item)
                    lines.append(f"    {prefix}def {sig}:")
                    mdoc = ast.get_docstring(item)
                    if mdoc:
                        short = mdoc.split("\n")[0].strip()[:100]
                        lines.append(f'        """{short}"""')
            lines.append("")

    # Global variables / constants (top-level assignments)
    constants = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.append(target.id)
    if constants:
        lines.append("# ── Constants ──")
        lines.append(", ".join(constants[:20]))
        lines.append("")

    return "\n".join(lines)


def _format_func_sig(node) -> str:
    """Format a function node into a human-readable signature string."""
    args = []
    # Regular arguments
    for a in node.args.args:
        name = a.arg
        ann = ""
        if a.annotation:
            ann = ": " + _annotation_str(a.annotation)
        args.append(f"{name}{ann}")

    # *args
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")

    # **kwargs
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")

    sig = f"{node.name}({', '.join(args)})"

    # Return annotation
    if node.returns:
        sig += f" -> {_annotation_str(node.returns)}"

    return sig


def _annotation_str(node) -> str:
    """Best-effort annotation → string."""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _name_of(node) -> str:
    """Best-effort node → name string."""
    try:
        return ast.unparse(node)
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        return "..."


def _regex_skeleton_js(file_path: str) -> str:
    """
    Regex-based skeleton for JS/TS files.
    Extracts function/class/export declarations.
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[READ ERROR] {e}"

    lines = [f"// Skeleton: {os.path.basename(file_path)}", ""]

    # Imports
    for m in re.finditer(r'^(import\s+.+?)$', source, re.MULTILINE):
        lines.append(m.group(1).strip()[:120])

    if len(lines) > 2:
        lines.append("")

    # Functions and classes
    patterns = [
        (r'^(export\s+)?(async\s+)?function\s+(\w+)\s*\(([^)]*)\)', 'function'),
        (r'^(export\s+)?class\s+(\w+)(\s+extends\s+\w+)?', 'class'),
        (r'^(export\s+)?const\s+(\w+)\s*=\s*(async\s+)?\(([^)]*)\)\s*=>', 'arrow'),
        (r'^(export\s+)?const\s+(\w+)\s*=\s*\{', 'object'),
    ]

    for pattern, kind in patterns:
        for m in re.finditer(pattern, source, re.MULTILINE):
            lines.append(m.group(0).strip()[:120])

    return "\n".join(lines) if len(lines) > 2 else f"// No declarations found in {os.path.basename(file_path)}"


def _build_plan_markdown(goal: str, steps: list[str], rag_context: str = "") -> str:
    """Build a formatted active_plan.md file."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    plan_lines = [
        f"# VoxKage ACE — Active Plan",
        f"",
        f"**Goal:** {goal}",
        f"**Created:** {now}",
        f"**Status:** IN PROGRESS",
        f"",
        f"---",
        f"",
        f"## Todo Checklist",
        f"",
    ]
    for i, step in enumerate(steps, 1):
        plan_lines.append(f"- [ ] **Step {i}:** {step}")

    plan_lines.extend([
        "",
        "---",
        "",
        "## RAG Context (auto-retrieved)",
        "",
        rag_context or "_No RAG context retrieved — codebase may not be indexed yet._",
        "",
        "---",
        "",
        "## Execution Log",
        "",
        "_Execution has not started yet._",
    ])

    return "\n".join(plan_lines)


# ═════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def coding_thinking(goal: str, project_dir: str = "", steps: str = "") -> str:
    """
    ACE ENTRY POINT — Mandatory first call for ANY development task.

    This tool initializes VoxKage's coding engine by:
      1. Auto-indexing the project directory into RAG (skips unchanged files)
      2. Querying RAG for relevant codebase architecture
      3. Creating a persistent todo plan at C:\\VoxKage\\Brain\\active_plan.md

    Parameters:
      goal        : What you are trying to accomplish (natural language)
      project_dir : Root directory of the codebase to work on (optional)
      steps       : Pipe-separated list of planned steps
                    e.g. "Create auth module|Add middleware|Update routes|Write tests"

    After calling this, read the returned plan and execute step by step.
    After each step, call update_coding_plan(step_number, "done") to tick it off.

    MANDATORY RULES:
    - Call this BEFORE writing any code for a new task
    - Call this BEFORE modifying any file in a project
    - If project_dir is given, codebase is auto-indexed (costs 0 tokens for cached files)
    """
    output_parts = []

    # ── Phase 1: Auto-index the project ───────────────────────────────────
    indexed_summary = ""
    if project_dir and os.path.isdir(project_dir):
        try:
            # Import RAG server internals for direct indexing
            sys.path.insert(0, os.path.dirname(__file__))
            from rag_server import index_directory
            idx_result = index_directory(
                directory=project_dir,
                extensions=".py,.js,.ts,.tsx,.jsx,.json,.md,.yaml,.yml,.css,.html",
                recursive=True,
            )
            indexed_summary = idx_result
            output_parts.append(f"[ACE Phase 1] RAG Indexing:\n{idx_result}")
        except Exception as e:
            output_parts.append(f"[ACE Phase 1] RAG indexing skipped: {e}")
    else:
        output_parts.append("[ACE Phase 1] No project_dir provided — skipping auto-index.")

    # ── Phase 2: Query RAG for relevant context ───────────────────────────
    rag_context = ""
    try:
        from rag_server import query_rag
        rag_result = query_rag(query=goal, top_k=6)
        if rag_result and "[RAG] Found" in rag_result:
            rag_context = rag_result
            output_parts.append(f"\n[ACE Phase 2] RAG Context Retrieved:\n{rag_result[:2000]}")
        else:
            output_parts.append(f"\n[ACE Phase 2] No relevant RAG context found for: \"{goal}\"")
    except Exception as e:
        output_parts.append(f"\n[ACE Phase 2] RAG query failed: {e}")

    # ── Phase 3: Build the plan ───────────────────────────────────────────
    step_list = [s.strip() for s in steps.split("|") if s.strip()] if steps else []
    if not step_list:
        step_list = [
            "Analyze relevant files and dependencies",
            "Implement core changes",
            "Update related files (imports, configs, registrations)",
            "Run syntax verification (py_compile / build)",
            "Test and verify functionality",
        ]

    plan_md = _build_plan_markdown(goal, step_list, rag_context[:3000])

    try:
        _PLAN_FILE.write_text(plan_md, encoding="utf-8")
        output_parts.append(f"\n[ACE Phase 3] Plan saved to: {_PLAN_FILE}")
    except Exception as e:
        output_parts.append(f"\n[ACE Phase 3] Plan file write failed: {e}")

    # ── Return the full plan to the model ─────────────────────────────────
    output_parts.append(f"\n{'='*60}")
    output_parts.append("ACE PLAN — Execute these steps in order:")
    output_parts.append(f"{'='*60}")
    for i, step in enumerate(step_list, 1):
        output_parts.append(f"  [ ] Step {i}: {step}")
    output_parts.append(f"{'='*60}")
    output_parts.append(
        "\nINSTRUCTIONS: Execute each step one at a time. "
        "After completing each step, call update_coding_plan(step_number, 'done'). "
        "If a step fails, call update_coding_plan(step_number, 'failed') and fix it before proceeding. "
        "After all steps are done, run a final syntax check across all changed files. "
        "Use get_code_skeleton(file_path) to understand file structure before editing — "
        "NEVER read a full file when a skeleton suffices."
    )

    return "\n".join(output_parts)


@mcp.tool()
def get_code_skeleton(file_path: str) -> str:
    """
    ACE TOOL: Get a compact structural skeleton of a code file.

    Returns ONLY: imports, class names, function signatures, docstrings,
    and top-level constants. Reduces a 2000-line file to ~40 lines.

    Use this INSTEAD of reading the full file when you need to understand
    structure, find where to insert code, or check what functions exist.

    Supports: Python (.py), JavaScript (.js), TypeScript (.ts/.tsx),
              JSX (.jsx), and CSS (.css — returns selectors only).

    This saves 95%+ of tokens compared to reading the full file.
    """
    file_path = os.path.normpath(os.path.abspath(file_path))

    if not os.path.exists(file_path):
        return f"[ACE ERROR] File not found: {file_path}"

    ext = Path(file_path).suffix.lower()
    basename = os.path.basename(file_path)
    size_kb = round(os.path.getsize(file_path) / 1024, 1)

    header = f"[ACE SKELETON] {basename} ({size_kb} KB)\n"

    if ext == ".py":
        return header + _ast_skeleton_python(file_path)

    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
        return header + _regex_skeleton_js(file_path)

    elif ext == ".css":
        try:
            source = Path(file_path).read_text(encoding="utf-8", errors="replace")
            selectors = re.findall(r'^([^{/\s@][^{]*)\{', source, re.MULTILINE)
            result = [f"/* Skeleton: {basename} */", ""]
            for s in selectors[:50]:
                result.append(s.strip() + " { ... }")
            if len(selectors) > 50:
                result.append(f"/* ... and {len(selectors) - 50} more selectors */")
            return header + "\n".join(result)
        except Exception as e:
            return header + f"[ERROR] {e}"

    elif ext in (".json", ".yaml", ".yml"):
        try:
            source = Path(file_path).read_text(encoding="utf-8", errors="replace")
            # Show just the top-level keys
            if ext == ".json":
                data = json.loads(source)
                if isinstance(data, dict):
                    keys = list(data.keys())[:30]
                    return header + f"Top-level keys ({len(data)} total):\n" + "\n".join(f"  - {k}" for k in keys)
                elif isinstance(data, list):
                    return header + f"Array with {len(data)} items. First item keys: {list(data[0].keys()) if data and isinstance(data[0], dict) else 'N/A'}"
            else:
                # YAML — show first 30 lines
                lines = source.split("\n")[:30]
                return header + "\n".join(lines)
        except Exception as e:
            return header + f"[ERROR] {e}"

    else:
        return f"[ACE] Skeleton not supported for {ext}. Use query_rag() or read the file directly."


@mcp.tool()
def update_coding_plan(step_number: int, status: str = "done") -> str:
    """
    ACE TOOL: Mark a step in the active plan as done or failed.

    Parameters:
      step_number : Which step to update (1-indexed)
      status      : "done" to mark complete, "failed" to mark as failed

    Returns the updated plan showing remaining open items.
    Call this after completing each step in the coding_thinking plan.
    """
    if not _PLAN_FILE.exists():
        return "[ACE] No active plan found. Call coding_thinking() first to create one."

    try:
        content = _PLAN_FILE.read_text(encoding="utf-8")
        lines = content.split("\n")

        # Find and update the target step
        step_count = 0
        target_found = False
        for i, line in enumerate(lines):
            if re.match(r'^- \[[ x!]\] \*\*Step \d+', line):
                step_count += 1
                if step_count == step_number:
                    if status == "done":
                        lines[i] = line.replace("- [ ]", "- [x]").replace("- [!]", "- [x]")
                    elif status == "failed":
                        lines[i] = line.replace("- [ ]", "- [!]").replace("- [x]", "- [!]")
                    target_found = True

        if not target_found:
            return f"[ACE] Step {step_number} not found in plan (found {step_count} total steps)."

        # Update status line if all steps done
        open_steps = sum(1 for l in lines if re.match(r'^- \[ \] \*\*Step', l))
        done_steps = sum(1 for l in lines if re.match(r'^- \[x\] \*\*Step', l))
        failed_steps = sum(1 for l in lines if re.match(r'^- \[!\] \*\*Step', l))

        for i, line in enumerate(lines):
            if line.startswith("**Status:**"):
                if open_steps == 0 and failed_steps == 0:
                    lines[i] = "**Status:** ✅ COMPLETE"
                elif open_steps == 0 and failed_steps > 0:
                    lines[i] = f"**Status:** ⚠️ COMPLETE WITH {failed_steps} FAILURE(S)"
                else:
                    lines[i] = f"**Status:** IN PROGRESS ({done_steps} done, {open_steps} remaining)"

        # Add execution log entry
        now = datetime.now().strftime("%H:%M:%S")
        log_marker = "_Execution has not started yet._"
        log_entry = f"- [{now}] Step {step_number}: **{status.upper()}**"

        updated = "\n".join(lines)
        updated = updated.replace(log_marker, log_entry)
        if log_marker not in content:
            # Append to execution log section
            updated = updated.rstrip() + f"\n{log_entry}\n"

        _PLAN_FILE.write_text(updated, encoding="utf-8")

        # Return summary of remaining work
        remaining = []
        for line in lines:
            if re.match(r'^- \[ \] \*\*Step', line):
                remaining.append(line.strip())

        mark = "✅" if status == "done" else "❌"
        result = f"[ACE] {mark} Step {step_number} marked as {status}.\n"
        if remaining:
            result += f"\n📋 Remaining steps ({len(remaining)}):\n"
            result += "\n".join(remaining)
        else:
            result += "\n🎉 All steps complete! Run final verification now."

        return result

    except Exception as e:
        return f"[ACE ERROR] Failed to update plan: {e}"


@mcp.tool()
def get_coding_plan() -> str:
    """
    ACE TOOL: Read the current active coding plan.

    Returns the full contents of C:\\VoxKage\\Brain\\active_plan.md.
    Use this to recall what steps remain when resuming a task.
    """
    if not _PLAN_FILE.exists():
        return "[ACE] No active plan. Call coding_thinking() to create one."

    try:
        return _PLAN_FILE.read_text(encoding="utf-8")
    except Exception as e:
        return f"[ACE ERROR] Could not read plan: {e}"


if __name__ == "__main__":
    mcp.run()
