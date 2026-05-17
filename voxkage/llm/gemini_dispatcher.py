"""
VoxKage Multi-Agent Stagger Dispatcher — Phase 4
Launches multiple Gemini CLI subprocesses in parallel with staggered start
times to prevent OS-level bot detection and API throttling.
"""

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


async def stagger_agents(
    tasks: list[Coroutine],
    delay_step: float = 0.5,
    return_exceptions: bool = True,
) -> list[Any]:
    """
    Launch a list of coroutines in parallel with staggered start times.

    Task i is wrapped with asyncio.sleep(i * delay_step) before launching,
    preventing all subprocesses from firing at the same millisecond.

    Args:
        tasks:             List of unawaited coroutines.
        delay_step:        Seconds between each task's start. Default 0.5s.
        return_exceptions: If True, failed tasks return Exception objects.

    Returns:
        List of results in the same order as `tasks`.
    """
    async def _delayed(coro: Coroutine, start_delay: float, idx: int) -> Any:
        if start_delay > 0:
            logger.debug(f"[Dispatcher] Agent {idx} staggered by {start_delay:.1f}s")
            await asyncio.sleep(start_delay)
        logger.info(f"[Dispatcher] Agent {idx} starting.")
        return await coro

    wrapped = [
        _delayed(task, i * delay_step, i)
        for i, task in enumerate(tasks)
    ]
    return list(await asyncio.gather(*wrapped, return_exceptions=return_exceptions))


async def morning_routine_example() -> dict[str, str]:
    """
    Demo: 3 parallel agents launched with 0.0s, 0.5s, 1.0s stagger.
    Showcases how to fire multiple Gemini CLI subprocesses concurrently.
    In production, replace prompts with real VoxKage planning queries.
    """
    from voxkage.llm.gemini_engine import ask_voxkage_brain
    from voxkage.llm.constants import GEMINI_MODEL

    tasks = [
        ask_voxkage_brain("Give a one-sentence morning greeting.", model=GEMINI_MODEL),
        ask_voxkage_brain("Give today's one-sentence top tech headline.", model=GEMINI_MODEL),
        ask_voxkage_brain("Give one morning productivity tip in one sentence.", model=GEMINI_MODEL),
    ]
    labels = ["morning_greeting", "tech_news", "productivity_tip"]

    logger.info("[Dispatcher] Firing morning_routine — 3 agents, 0.5s stagger.")
    results = await stagger_agents(tasks, delay_step=0.5)

    output: dict[str, str] = {}
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            logger.error(f"[Dispatcher] Agent '{label}' failed: {result}")
            output[label] = f"[Error] {result}"
        else:
            output[label] = result
            logger.info(f"[Dispatcher] '{label}': {result[:80]}")

    return output
