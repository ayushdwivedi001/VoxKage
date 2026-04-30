"""
VoxKage Gemini CLI Engine — Phase 1
Async subprocess wrapper for the Gemini CLI (brain-only layer).
The MCP dispatcher handles all tool execution; Gemini only plans.
"""

import asyncio
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"
_BASE_COOLDOWN: float = 1.5      # Mandatory anti-bot cooldown per call (seconds)
_JITTER_RANGE: float = 0.3       # ± random jitter added to cooldown
_LAST_CALL_TIME: float = 0.0     # Module-level monotonic timestamp of last call


class GeminiCLIError(Exception):
    """Raised when the Gemini CLI fails after all retries are exhausted."""
    pass


# ─── Startup Availability Check ───────────────────────────────────────────────

def check_gemini_available(cli_path: str = "gemini") -> bool:
    """
    Synchronous startup check — runs `gemini --version`.
    Returns True if the CLI is on PATH and responds, False otherwise.
    Safe to call at import time.
    """
    try:
        cmd = f"{cli_path} --version" if _WINDOWS else None
        args = cmd if _WINDOWS else [cli_path, "--version"]
        result = subprocess.run(
            args,
            shell=_WINDOWS,
            capture_output=True,
            timeout=5.0,
            text=True,
        )
        if result.returncode == 0:
            logger.info(f"[GeminiEngine] CLI available: {result.stdout.strip()[:60]}")
            return True
        logger.warning(f"[GeminiEngine] CLI non-zero exit: {result.stderr.strip()[:100]}")
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"[GeminiEngine] CLI not available: {e}")
        return False


# ─── JSON Extractor ───────────────────────────────────────────────────────────

def _find_json_span(text: str) -> Optional[str]:
    """Walk text character-by-character to find the first complete JSON object/array."""
    start = None
    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if in_string:
            escape_next = (ch == "\\")
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            if start is not None:
                in_string = True
            continue
        if ch in "{[":
            if start is None:
                start = i
            depth += 1
        elif ch in "}]":
            if start is not None:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def clean_cli_json(stdout: str) -> dict:
    """
    Multi-strategy JSON extractor for Gemini CLI output.

    Handles:
      - ```json { ... } ``` markdown fences
      - ``` { ... } ``` plain fences
      - Inline JSON with prose before/after
      - Trailing commas (common hallucination) via regex strip
      - Nested objects / arrays

    Raises ValueError if no valid JSON is found after all strategies.
    """
    if not stdout.strip():
        raise ValueError("Empty CLI output — nothing to parse.")

    # Strategy 1: Extract from markdown fences
    for pattern in [
        r'```json\s*(\{.*?\})\s*```',
        r'```json\s*(\[.*?\])\s*```',
        r'```\s*(\{.*?\})\s*```',
        r'```\s*(\[.*?\])\s*```',
    ]:
        m = re.search(pattern, stdout, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

    # Strategy 2: Bracket-walk to find first complete JSON blob
    candidate = _find_json_span(stdout)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Strip trailing commas (common Gemini hallucination)
            cleaned = re.sub(r',\s*([}\]])', r'\1', candidate)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    # Strategy 3: Strip all markdown and try everything as JSON
    stripped = re.sub(r'```(?:json)?', '', stdout).replace('`', '').strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    raise ValueError(
        f"[GeminiEngine] No valid JSON after all strategies. "
        f"Preview: {stdout[:300]!r}"
    )


# ─── Low-Level Subprocess Runner ──────────────────────────────────────────────

async def _run_subprocess(
    cli_path: str,
    model: str,
    prompt: str,
    image_path: Optional[str],
    timeout: float,
) -> tuple[str, str, int]:
    """
    Executes the Gemini CLI as an async subprocess.

    Key design decisions:
      - Prompt is passed via STDIN pipe, NOT as a shell argument.
        Reason: prompts contain JSON braces/quotes which break Windows cmd.exe
        quoting when passed via list2cmdline / subprocess shell argument.
      - cwd is set to the VoxKage project root so the CLI auto-discovers GEMINI.md
        (the system instruction file) and injects it into every call automatically.
      - Uses -p with empty string to trigger non-interactive mode, then stdin carries the prompt.
      - Windows: create_subprocess_shell handles gemini.cmd PATH resolution.
      - POSIX:   create_subprocess_exec for clean argument passing.
    Returns (stdout, stderr, returncode).
    """
    # Project root = parent of the llm/ directory where this file lives
    project_root = str(Path(__file__).resolve().parent.parent)

    # Build args — use -p with empty string to enter headless mode.
    # The actual prompt is delivered via stdin to avoid shell quoting issues.
    args = [cli_path]
    if model:
        args += ["-m", model]
    if image_path and Path(image_path).exists():
        args += ["--image", str(image_path)]
    args += ["-p", ""]  # Empty -p triggers headless mode; stdin carries the real prompt

    # Environment: disable all interactive / color features for subprocess safety
    env = {
        **os.environ,
        "NO_COLOR": "1",           # Standard: disables color output
        "CI": "1",                 # CI mode: disables interactive prompts
        "FORCE_COLOR": "0",        # Explicitly disables forced color
        "TERM": "xterm-256color",  # Provide a valid TERM to satisfy CLI check
    }

    prompt_bytes = prompt.encode("utf-8")

    if _WINDOWS:
        cmd_str = subprocess.list2cmdline(args)
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=project_root,   # ← CLI reads GEMINI.md from here
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=project_root,   # ← CLI reads GEMINI.md from here
        )

    stdout_b, stderr_b = await asyncio.wait_for(
        proc.communicate(input=prompt_bytes), timeout=timeout
    )
    return (
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


# ─── Main Public API ──────────────────────────────────────────────────────────

async def ask_voxkage_brain(
    prompt: str,
    image_path: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = None,
    cli_path: Optional[str] = None,
    max_retries: Optional[int] = None,
) -> str:
    """
    Primary entry point — call the Gemini CLI and return raw stdout text.

    Uses GeminiClient (gemini_client.py) as primary path with cache warming.
    Falls back to direct subprocess if the client is unavailable.
    """
    global _LAST_CALL_TIME

    try:
        from llm.constants import (
            GEMINI_CLI_PATH, GEMINI_MODEL, MAX_CLI_RETRIES, GEMINI_CLI_TIMEOUT,
        )
        _cli = cli_path or GEMINI_CLI_PATH
        _model = model or GEMINI_MODEL
        _max_retries = max_retries if max_retries is not None else MAX_CLI_RETRIES
        _timeout = timeout if timeout is not None else GEMINI_CLI_TIMEOUT
    except ImportError:
        _cli = cli_path or "gemini"
        _model = model or "gemini-2.5-flash"
        _max_retries = max_retries if max_retries is not None else 2
        _timeout = timeout if timeout is not None else 60.0

    # ── Strategy 1: Persistent REPL (boot-once, instant subsequent calls) ──
    try:
        from llm.gemini_repl import get_repl, GeminiREPLError
        repl = await get_repl(model=_model)
        logger.info(
            f"[GeminiEngine] REPL call model={_model!r} prompt_len={len(prompt)}"
        )
        result = await repl.ask(prompt, timeout=_timeout)
        return result
    except Exception as repl_err:
        logger.warning(
            f"[GeminiEngine] REPL failed ({repl_err}). "
            f"Falling back to subprocess."
        )

    # ── Strategy 2: Direct subprocess fallback (14s boot per call) ────────
    last_error: Exception = RuntimeError("No attempts made.")

    for attempt in range(1, _max_retries + 1):
        now = time.monotonic()
        elapsed = now - _LAST_CALL_TIME
        jitter = random.uniform(-_JITTER_RANGE, _JITTER_RANGE)
        wait = max(0.0, _BASE_COOLDOWN + jitter - elapsed)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_CALL_TIME = time.monotonic()

        t0 = time.monotonic()
        try:
            logger.info(
                f"[GeminiEngine] Subprocess attempt {attempt}/{_max_retries} "
                f"model={_model!r} prompt_len={len(prompt)}"
            )
            stdout, stderr, code = await _run_subprocess(
                _cli, _model, prompt, image_path, _timeout
            )
            elapsed_s = time.monotonic() - t0

            if code != 0:
                preview = (stderr or stdout).strip()[:200]
                raise GeminiCLIError(f"CLI exited {code}: {preview}")

            stdout = stdout.strip()
            stdout = "\n".join(
                line for line in stdout.splitlines()
                if not line.startswith("Warning:")
            ).strip()
            if not stdout:
                raise GeminiCLIError("CLI returned empty stdout (only warnings).")

            logger.info(f"[GeminiEngine] ✅ Response in {elapsed_s:.1f}s ({len(stdout)} chars)")
            return stdout

        except asyncio.TimeoutError:
            last_error = GeminiCLIError(f"Timed out after {_timeout}s")
            logger.warning(f"[GeminiEngine] Timeout on attempt {attempt}.")

        except (FileNotFoundError, OSError) as e:
            raise GeminiCLIError(f"Gemini CLI not on PATH: {e}") from e

        except GeminiCLIError as e:
            last_error = e
            logger.warning(f"[GeminiEngine] CLI error attempt {attempt}: {e}")

        except Exception as e:
            last_error = e
            logger.warning(f"[GeminiEngine] Unexpected error attempt {attempt}: {e}")

        if attempt < _max_retries:
            await asyncio.sleep(attempt * 1.0)

    raise GeminiCLIError(
        f"All {_max_retries} CLI attempts failed. Last: {last_error}"
    ) from last_error

