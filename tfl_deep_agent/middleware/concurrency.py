"""ConcurrencyMiddleware: bounds concurrent model calls with the same
process-wide semaphore the four pipelines use.

``agent_system.lib.llm_client.get_concurrency_semaphore()`` is a lazily built
``threading.Semaphore(TFL_MAX_CONCURRENCY)`` (default 4) shared by every
pipeline's ``LiveRunner`` / ``LLMRunner`` in the process. ``ChatAnthropic``
does not go through ``AnthropicClient``, so without this middleware the
agent's calls (parallel ``run_workflow`` branches, ``task`` subagents) would
not count against it. The semaphore object is reused, not re-implemented, so
a TFL Lab process that runs a pipeline and a deep-agent task at once shares one
limit.

Sync calls block on ``acquire()``. Async calls never block the event loop:
they poll ``acquire(blocking=False)`` with a short ``asyncio.sleep`` back-off,
which also makes cancellation safe (a cancelled waiter holds no slot). One
slot covers one model call including the SDK's own retries.

``run_semaphore`` (optional) is a second bound for one run
(``settings.limits.max_parallel``): the run's slot is taken first, then the
process-wide one, always in that order and released in reverse, so two
nested bounds cannot deadlock.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Iterator
from typing import Any

from agent_system.lib.llm_client import get_concurrency_semaphore

try:  # the [deep] extra
    from langchain.agents.middleware import AgentMiddleware as _AgentMiddleware
    _HAVE_LANGCHAIN = True
except ImportError:  # pragma: no cover
    _AgentMiddleware = object  # type: ignore[assignment,misc]
    _HAVE_LANGCHAIN = False

POLL_START_S = 0.005
POLL_MAX_S = 0.1


def shared_semaphore() -> threading.Semaphore:
    """The pipelines' process-wide semaphore (``TFL_MAX_CONCURRENCY``)."""
    return get_concurrency_semaphore()


@contextlib.contextmanager
def hold(sem: threading.Semaphore) -> Iterator[None]:
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


async def acquire_async(sem: threading.Semaphore, *, poll_start: float = POLL_START_S,
                        poll_max: float = POLL_MAX_S) -> None:
    """Acquire a ``threading.Semaphore`` without blocking the event loop."""
    delay = poll_start
    while not sem.acquire(blocking=False):
        await asyncio.sleep(delay)
        delay = min(delay * 2, poll_max)


class ConcurrencyMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Holds one slot of the shared semaphore for the duration of each model
    call. ``semaphore`` / ``semaphore_factory`` exist for tests; by default the
    semaphore is looked up at call time, so ``TFL_MAX_CONCURRENCY`` set before
    the first call anywhere in the process takes effect."""

    def __init__(
        self,
        semaphore: threading.Semaphore | None = None,
        *,
        semaphore_factory: Callable[[], threading.Semaphore] | None = None,
        run_semaphore: threading.Semaphore | None = None,
    ) -> None:
        if not _HAVE_LANGCHAIN:  # pragma: no cover
            raise ImportError("ConcurrencyMiddleware needs the [deep] extra (langchain)")
        super().__init__()
        if semaphore is not None and semaphore_factory is not None:
            raise ValueError("pass semaphore or semaphore_factory, not both")
        self._semaphore = semaphore
        self._factory = semaphore_factory or shared_semaphore
        self.run_semaphore = run_semaphore
        self.tools = []

    @property
    def semaphore(self) -> threading.Semaphore:
        return self._semaphore if self._semaphore is not None else self._factory()

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        run_sem = self.run_semaphore
        with hold(run_sem) if run_sem is not None else contextlib.nullcontext(), hold(self.semaphore):
            return handler(request)

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        run_sem = self.run_semaphore
        if run_sem is not None:
            await acquire_async(run_sem)
        try:
            sem = self.semaphore
            await acquire_async(sem)
            try:
                return await handler(request)
            finally:
                sem.release()
        finally:
            if run_sem is not None:
                run_sem.release()


def make_middleware(settings: Any = None, *, role: str | None = None,
                    run_semaphore: threading.Semaphore | None = None) -> ConcurrencyMiddleware:
    """Hook for ``agent.default_middleware``: every agent and subagent gets one,
    all sharing the process-wide semaphore (and the run's ``max_parallel``
    semaphore when one is given)."""
    return ConcurrencyMiddleware(run_semaphore=run_semaphore)


__all__ = ["ConcurrencyMiddleware", "acquire_async", "hold", "make_middleware", "shared_semaphore"]
