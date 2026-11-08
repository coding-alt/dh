"""Minimal in-process task runner (no broker needed for the MVP)."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from ..logger import log

_running: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, label: str = "task") -> asyncio.Task:
    task = asyncio.create_task(coro, name=label)
    _running.add(task)

    def _done(finished: asyncio.Task) -> None:
        _running.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc:
            log.exception("后台任务异常 (%s): %s", label, exc, exc_info=exc)

    task.add_done_callback(_done)
    return task


def running_count() -> int:
    return len(_running)


async def shutdown() -> None:
    for task in list(_running):
        task.cancel()
    if _running:
        await asyncio.gather(*_running, return_exceptions=True)
