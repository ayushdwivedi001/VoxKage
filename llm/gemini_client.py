"""
VoxKage Gemini CLI Client — Reliable subprocess wrapper.

Key insight from testing:
- Gemini CLI boot = ~14s (Node.js + OAuth)
- Model thinking = 5-30s (depends on prompt complexity + model)
- Total per call = 20-45s

Strategy:
1. Use generous timeouts (90s for pro, 60s for flash)
2. The pipeline pre-boots a keepalive process to warm OS/disk caches
3. Each real call still spawns fresh, but disk cache makes boot ~10s faster
4. For agentic steps: fire processes in PARALLEL to overlap boot times
"""

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GeminiCLIError(Exception):
    """Raised when the Gemini CLI fails."""
    pass


class GeminiClient:
    """
    Thin async wrapper around the Gemini CLI subprocess.
    
    Improvements over raw subprocess:
    - Pre-boots a keepalive process to warm OS disk cache
    - Parallel execution for agentic batch steps
    - Proper timeout handling and cleanup
    - Warning line stripping
    """

    def __init__(
        self,
        model: str = "gemini-2.5-pro",
        cli_path: str = "gemini",
        default_timeout: float = 90.0,
    ):
        self._model = model
        self._cli_path = cli_path
        self._default_timeout = default_timeout
        self._project_root = str(Path(__file__).resolve().parent.parent)
        self._env = {
            **os.environ,
            "NO_COLOR": "1",
            "CI": "1",
            "FORCE_COLOR": "0",
            "TERM": "xterm-256color",
        }
        self._call_count = 0
        self._preboot_done = False

    async def start(self):
        """Pre-boot a keepalive process to warm OS disk cache."""
        if self._preboot_done:
            return
        # Fire-and-forget: don't block startup
        asyncio.create_task(self._do_preboot())
        logger.info(f"[GeminiClient] Initialized model={self._model}")

    async def _do_preboot(self):
        """Run a trivial CLI call to warm the disk/auth cache."""
        try:
            args = [self._cli_path, "-m", "gemini-2.5-flash", "-p", ""]
            cmd_str = subprocess.list2cmdline(args)
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._project_root,
            )
            t0 = time.monotonic()
            await asyncio.wait_for(
                proc.communicate(input=b"say: READY"),
                timeout=60.0,
            )
            elapsed = time.monotonic() - t0
            self._preboot_done = True
            logger.info(f"[GeminiClient] Cache warmed in {elapsed:.1f}s")
        except Exception as e:
            logger.debug(f"[GeminiClient] Pre-boot failed (non-fatal): {e}")
            self._preboot_done = True  # Don't retry

    async def ask(
        self,
        prompt: str,
        model: str = None,
        image_path: str = None,
        timeout: float = None,
    ) -> str:
        """
        Send a prompt to Gemini CLI and return response text.
        
        Each call spawns a fresh subprocess (CLI limitation).
        Use generous timeouts: the CLI needs boot + thinking time.
        """
        self._call_count += 1
        _model = model or self._model
        _timeout = timeout or self._default_timeout

        args = [self._cli_path, "-m", _model]
        if image_path and Path(image_path).exists():
            args += ["--image", str(image_path)]
        args += ["-p", ""]

        cmd_str = subprocess.list2cmdline(args)
        t0 = time.monotonic()

        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._project_root,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=_timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise GeminiCLIError(f"Timed out after {_timeout}s")

        elapsed = time.monotonic() - t0
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()

        # Strip Warning: lines
        stdout = "\n".join(
            line for line in stdout.splitlines()
            if not line.startswith("Warning:")
        ).strip()

        if proc.returncode and proc.returncode != 0:
            raise GeminiCLIError(
                f"CLI exited {proc.returncode}: {stderr[:200]}"
            )
        if not stdout:
            raise GeminiCLIError("CLI returned empty stdout")

        logger.info(
            f"[GeminiClient] Response in {elapsed:.1f}s "
            f"({len(stdout)} chars) [call #{self._call_count}]"
        )
        return stdout

    async def ask_parallel(
        self,
        prompts: list[str],
        timeout: float = None,
    ) -> list:
        """
        Send multiple prompts in PARALLEL.
        
        All CLI processes boot simultaneously, so total wall time
        = max(individual times) instead of sum.
        
        For 3 prompts that each take 16s sequentially (48s total),
        parallel execution takes ~16-20s.
        """
        tasks = [self.ask(p, timeout=timeout) for p in prompts]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self):
        """Cleanup (no-op for subprocess-based client)."""
        logger.info("[GeminiClient] Shutdown")


# ── Module-level singleton ────────────────────────────────────────────────

_client: Optional[GeminiClient] = None


async def get_pool(model: str = None) -> GeminiClient:
    """
    Get or create the module-level client singleton.
    Named get_pool for backward compat with gemini_engine.py imports.
    """
    global _client
    if _client is None:
        try:
            from llm.constants import GEMINI_AGENTIC_MODEL, GEMINI_CLI_PATH
        except ImportError:
            GEMINI_AGENTIC_MODEL = model or "gemini-2.5-pro"
            GEMINI_CLI_PATH = "gemini"
        _client = GeminiClient(
            model=model or GEMINI_AGENTIC_MODEL,
            cli_path=GEMINI_CLI_PATH,
            default_timeout=90.0,
        )
        await _client.start()
    return _client
