"""Deterministic async polling for tests (ck3_chronicler-27ov.82 / audit L39).

Replaces fixed ``await asyncio.sleep(...)`` synchronisation — slow when the
interval is padded for safety, flaky when it is too short under load — with
a poll-until-true that returns the instant the condition holds and fails
loudly on timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def await_condition(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    interval: float = 0.005,
    message: str | None = None,
) -> None:
    """Yield to the loop until ``predicate()`` is truthy or ``timeout`` elapses.

    Uses the loop's monotonic clock so it is unaffected by wall-clock
    changes. Raises ``AssertionError`` on timeout — a fixed sleep that was
    too short would instead pass intermittently and fail elsewhere.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError(message or f"condition not met within {timeout}s")
        await asyncio.sleep(interval)
