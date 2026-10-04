"""Failure-injection contract for the framework-neutral Agent execution boundary."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from threading import BoundedSemaphore

import pytest

from app.agents import execution
from app.agents.execution import (
    AgentExecutionCapacityExceeded,
    AgentExecutionConfig,
    AgentLifecycleEvent,
    AgentRequestDeadlineExceeded,
    run_agent_with_deadline,
)


def _wait_until(predicate, *, timeout: float = 0.5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_non_cooperative_worker_returns_safe_request_timeout_then_records_late_completion():
    started = threading.Event()
    release = threading.Event()
    events = []

    def blocking_worker(_context):
        started.set()
        release.wait(timeout=0.5)
        return "late result that the request must not consume"

    with pytest.raises(AgentRequestDeadlineExceeded):
        run_agent_with_deadline(
            worker=blocking_worker,
            config=AgentExecutionConfig(
                request_timeout_seconds=0.01,
                provider_timeout_seconds=0.01,
                tool_timeout_seconds=0.01,
            ),
            lifecycle_recorder=events.append,
        )

    assert started.is_set()
    assert [event.phase for event in events] == ["request_timeout"]
    release.set()
    assert _wait_until(lambda: any(event.phase == "late_completion" for event in events))
    assert events[-1].phase == "late_completion"
    assert events[-1].result_consumed is False


def test_cooperative_async_worker_receives_local_cancellation_after_request_timeout():
    cancelled = threading.Event()
    events = []

    async def cooperative_worker(_context):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(AgentRequestDeadlineExceeded):
        run_agent_with_deadline(
            worker=cooperative_worker,
            config=AgentExecutionConfig(
                request_timeout_seconds=0.01,
                provider_timeout_seconds=0.01,
                tool_timeout_seconds=0.01,
            ),
            lifecycle_recorder=events.append,
        )

    assert _wait_until(cancelled.is_set)
    assert _wait_until(lambda: len(events) == 2)
    assert [event.phase for event in events] == ["request_timeout", "late_completion"]


def test_non_cooperative_late_workers_are_bounded_and_new_work_fails_closed():
    """Timed-out synchronous calls retain a slot until they actually return."""
    started = threading.Event()
    release = threading.Event()
    events = []

    def blocking_worker(_context):
        started.set()
        release.wait(timeout=0.5)
        return "must never reach the expired HTTP response"

    config = AgentExecutionConfig(
        request_timeout_seconds=0.01,
        provider_timeout_seconds=0.01,
        tool_timeout_seconds=0.01,
    )
    try:
        for _ in range(4):
            with pytest.raises(AgentRequestDeadlineExceeded):
                run_agent_with_deadline(
                    worker=blocking_worker,
                    config=config,
                    lifecycle_recorder=events.append,
                )

        assert started.is_set()
        with pytest.raises(AgentExecutionCapacityExceeded):
            run_agent_with_deadline(
                worker=blocking_worker,
                config=config,
                lifecycle_recorder=events.append,
            )
    finally:
        release.set()

    assert _wait_until(lambda: len([event for event in events if event.phase == "late_completion"]) == 4)


def test_queued_worker_cancellation_is_distinct_from_running_late_completion(monkeypatch):
    """A queued Future can cancel; a running worker can only finish later."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eval-queued")
    slots = BoundedSemaphore(2)
    monkeypatch.setattr(execution, "_agent_worker_executor", executor)
    monkeypatch.setattr(execution, "_agent_worker_slots", slots)
    running_started = threading.Event()
    release_running = threading.Event()
    queued_started = threading.Event()
    first_result = []
    queued_events = []

    def running_worker(_context):
        running_started.set()
        release_running.wait(timeout=1)
        return "first completed"

    def run_first_request():
        first_result.append(
            run_agent_with_deadline(
                worker=running_worker,
                config=AgentExecutionConfig(
                    request_timeout_seconds=0.5,
                    provider_timeout_seconds=0.5,
                    tool_timeout_seconds=0.5,
                ),
            )
        )

    first_request = threading.Thread(target=run_first_request)
    first_request.start()
    assert running_started.wait(timeout=0.5)
    try:
        with pytest.raises(AgentRequestDeadlineExceeded):
            run_agent_with_deadline(
                worker=lambda _context: queued_started.set(),
                config=AgentExecutionConfig(
                    request_timeout_seconds=0.01,
                    provider_timeout_seconds=0.01,
                    tool_timeout_seconds=0.01,
                ),
                lifecycle_recorder=queued_events.append,
            )
        assert queued_started.is_set() is False
        assert [event.phase for event in queued_events] == [
            "request_timeout",
            "queued_cancellation",
        ]
        assert queued_events[0].worker_cancel_requested is True
    finally:
        release_running.set()
        first_request.join(timeout=1)
        executor.shutdown(wait=True, cancel_futures=True)

    assert first_result == ["first completed"]


def test_lifecycle_trace_redacts_synthetic_private_fields(monkeypatch, tmp_path):
    private_phrase = "medication timing and membership id secret"
    monkeypatch.setattr(execution.trace, "LOG_DIR", tmp_path)

    execution.trace.record_agent_lifecycle(
        phase="late_completion",
        execution_id="opaque-execution-id",
        elapsed_ms=12.5,
        result_consumed=False,
        worker_cancel_requested=False,
    )

    payload = json.loads(next(tmp_path.glob("agent-lifecycle-*.jsonl")).read_text(encoding="utf-8"))
    assert private_phrase not in json.dumps(payload)
    assert set(payload) == {
        "timestamp",
        "phase",
        "execution_id",
        "elapsed_ms",
        "result_consumed",
        "worker_cancel_requested",
    }
    assert {field.name for field in fields(AgentLifecycleEvent)} == {
        "phase",
        "execution_id",
        "elapsed_ms",
        "result_consumed",
        "worker_cancel_requested",
    }
