"""Framework-neutral deadline and late-completion handling for Agent work.

This module intentionally does not claim that Python can kill an already
running synchronous thread or a request already accepted by a remote provider.
It establishes the narrower, testable contract: the request returns a safe
deadline failure, signals local cancellation, never consumes a late result, and
records the worker's eventual terminal event without private prompt/tool data.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import BoundedSemaphore, Event
from typing import Any, TypeVar

from . import trace


T = TypeVar("T")


# A request deadline cannot safely kill a running Python thread. Keep the
# consequence bounded instead: each timed-out non-cooperative worker continues
# to occupy one process-local slot until it really returns, and new Agent work
# fails closed once all slots are occupied. This is deliberately independent of
# the HTTP server's own worker pool.
AGENT_WORKER_CAPACITY = 4
_agent_worker_slots = BoundedSemaphore(AGENT_WORKER_CAPACITY)
_agent_worker_executor = ThreadPoolExecutor(
    max_workers=AGENT_WORKER_CAPACITY,
    thread_name_prefix="cadensy-agent",
)


class AgentExecutionDeadlineExceeded(RuntimeError):
    """Base class for a deadline that prevents use of an Agent result."""

    kind = "deadline_exceeded"


class AgentRequestDeadlineExceeded(AgentExecutionDeadlineExceeded):
    """The HTTP/request-owned deadline elapsed before safe completion."""

    kind = "request_deadline_exceeded"


class AgentProviderDeadlineExceeded(AgentExecutionDeadlineExceeded):
    """A provider phase has no remaining safe local time budget."""

    kind = "provider_deadline_exceeded"


class AgentToolDeadlineExceeded(AgentExecutionDeadlineExceeded):
    """A read-only tool crossed its bounded execution budget."""

    kind = "tool_deadline_exceeded"


class AgentExecutionCapacityExceeded(RuntimeError):
    """All bounded Agent worker slots are held by active or late work."""

    kind = "agent_capacity_exhausted"


@dataclass(frozen=True)
class AgentExecutionConfig:
    """Independent limits; a request deadline is the outer hard boundary."""

    request_timeout_seconds: float
    provider_timeout_seconds: float
    tool_timeout_seconds: float

    def __post_init__(self) -> None:
        for name, value in (
            ("request_timeout_seconds", self.request_timeout_seconds),
            ("provider_timeout_seconds", self.provider_timeout_seconds),
            ("tool_timeout_seconds", self.tool_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True)
class AgentLifecycleEvent:
    """Trace-safe lifecycle metadata; it deliberately carries no model output."""

    phase: str
    execution_id: str
    elapsed_ms: float
    result_consumed: bool = False
    worker_cancel_requested: bool = False


class AgentExecutionContext:
    """Immutable execution capability supplied to the isolated worker only."""

    def __init__(self, *, execution_id: str, config: AgentExecutionConfig):
        self.execution_id = execution_id
        self.config = config
        self._started_at = time.monotonic()
        self._request_deadline_at = self._started_at + config.request_timeout_seconds
        self._cancelled = Event()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancelled.is_set()

    def request_remaining_seconds(self) -> float:
        return self._request_deadline_at - time.monotonic()

    def request_cancellation(self) -> None:
        self._cancelled.set()

    def ensure_request_active(self) -> None:
        if self.cancellation_requested or self.request_remaining_seconds() <= 0:
            raise AgentRequestDeadlineExceeded("Agent request deadline elapsed")

    def provider_timeout_seconds(self) -> float:
        self.ensure_request_active()
        remaining = min(
            self.config.provider_timeout_seconds,
            self.request_remaining_seconds(),
        )
        if remaining <= 0:
            raise AgentProviderDeadlineExceeded("No local provider budget remains")
        return remaining

    def before_provider(self) -> float:
        return self.provider_timeout_seconds()

    def after_provider(self) -> None:
        self.ensure_request_active()

    def before_tool(self) -> float:
        self.ensure_request_active()
        return time.monotonic()

    def after_tool(self, started_at: float) -> None:
        elapsed = time.monotonic() - started_at
        if elapsed > self.config.tool_timeout_seconds:
            raise AgentToolDeadlineExceeded("Read-only Agent tool exceeded its deadline")
        self.ensure_request_active()


def run_agent_with_deadline(
    *,
    worker: Callable[[AgentExecutionContext], T | Awaitable[T]],
    config: AgentExecutionConfig,
    lifecycle_recorder: Callable[[AgentLifecycleEvent], None] | None = None,
) -> T:
    """Run one isolated worker without accepting a result after request expiry.

    ``future.cancel()`` only cancels queued work. A running synchronous worker
    may continue until its own provider/tool call returns; its completion is
    recorded asynchronously, while its result is deliberately discarded.
    """
    execution_id = trace.new_trace_id()
    context = AgentExecutionContext(execution_id=execution_id, config=config)
    recorder = lifecycle_recorder or _record_lifecycle
    if not _agent_worker_slots.acquire(blocking=False):
        recorder(
            AgentLifecycleEvent(
                phase=AgentExecutionCapacityExceeded.kind,
                execution_id=execution_id,
                elapsed_ms=_elapsed_ms(context),
            )
        )
        raise AgentExecutionCapacityExceeded(
            "Agent worker capacity is occupied by active or late work"
        )

    try:
        future = _agent_worker_executor.submit(_run_worker, worker, context)
    except BaseException:
        _agent_worker_slots.release()
        raise
    future.add_done_callback(lambda _completed: _agent_worker_slots.release())
    try:
        result = future.result(timeout=config.request_timeout_seconds)
        context.ensure_request_active()
        recorder(
            AgentLifecycleEvent(
                phase="completed",
                execution_id=execution_id,
                elapsed_ms=_elapsed_ms(context),
            )
        )
        return result
    except AgentExecutionDeadlineExceeded as exc:
        context.request_cancellation()
        recorder(
            AgentLifecycleEvent(
                phase=exc.kind,
                execution_id=execution_id,
                elapsed_ms=_elapsed_ms(context),
            )
        )
        raise
    except TimeoutError as exc:
        context.request_cancellation()
        cancelled = future.cancel()
        recorder(
            AgentLifecycleEvent(
                phase="request_timeout",
                execution_id=execution_id,
                elapsed_ms=_elapsed_ms(context),
                worker_cancel_requested=cancelled,
            )
        )
        future.add_done_callback(
            lambda _completed: recorder(
                AgentLifecycleEvent(
                    # A cancelled queued Future never began a worker. Keep it
                    # distinct from a running synchronous worker that later
                    # returns after the HTTP response has been abandoned.
                    phase=("queued_cancellation" if cancelled else "late_completion"),
                    execution_id=execution_id,
                    elapsed_ms=_elapsed_ms(context),
                    result_consumed=False,
                    worker_cancel_requested=cancelled,
                )
            )
        )
        raise AgentRequestDeadlineExceeded("Agent request deadline elapsed") from exc


def _run_worker(
    worker: Callable[[AgentExecutionContext], T | Awaitable[T]],
    context: AgentExecutionContext,
) -> T:
    value = worker(context)
    if inspect.isawaitable(value):
        return asyncio.run(_await_with_local_cancellation(value, context))
    return value


async def _await_with_local_cancellation(
    awaitable: Awaitable[T], context: AgentExecutionContext
) -> T:
    """Cancel a cooperative coroutine when the local request signal arrives."""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            context.ensure_request_active()
            try:
                return await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=min(0.01, context.request_remaining_seconds()),
                )
            except TimeoutError:
                continue
    except AgentRequestDeadlineExceeded:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


def _elapsed_ms(context: AgentExecutionContext) -> float:
    return round((context.config.request_timeout_seconds - context.request_remaining_seconds()) * 1000, 2)


def _record_lifecycle(event: AgentLifecycleEvent) -> None:
    trace.record_agent_lifecycle(
        phase=event.phase,
        execution_id=event.execution_id,
        elapsed_ms=event.elapsed_ms,
        result_consumed=event.result_consumed,
        worker_cancel_requested=event.worker_cancel_requested,
    )
