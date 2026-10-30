"""Cancellation and stale-output protection for session response tasks."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Coroutine


class InterruptionController:
    """Track response work so a new user turn can cancel prior output safely."""

    def __init__(self) -> None:
        self._generation = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._interrupting = False

    def start(self, coroutine: Coroutine[Any, Any, Any]) -> tuple[int, asyncio.Task[Any]]:
        if self._interrupting:
            coroutine.close()
            raise RuntimeError("Response work cannot start during interruption")
        self._generation += 1
        generation = self._generation
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return generation, task

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    @property
    def has_active_tasks(self) -> bool:
        """Return whether any response work is still running."""
        return any(not task.done() for task in self._tasks)

    async def interrupt(
        self,
        on_started: Callable[[int], Awaitable[None]] | None = None,
    ) -> int:
        """Invalidate/cancel output, notify the client, then await provider cleanup."""
        async with self._lock:
            self._interrupting = True
            self._generation += 1
            generation = self._generation
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            try:
                # Notify before waiting for TTS/provider shutdown; browser audio may
                # already be queued several seconds ahead of the server.
                if on_started is not None:
                    await on_started(generation)
            finally:
                try:
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    self._interrupting = False
            return generation
