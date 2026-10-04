"""PR-03 isolated Pydantic AI compatibility contracts.

These tests exercise only the standalone PoC adapter. They must not route
through the production Chat endpoint or replace the Legacy runtime.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from sqlalchemy.orm import Session

from evals.runner import _independent_session_factory, _synthetic_fixture

pytest.importorskip(
    "pydantic_ai",
    reason="PR-03 compatibility tests require requirements-pydantic-ai-poc.lock.txt",
)

from pydantic_ai import ModelResponse, ToolCallPart, UsageLimits, models
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.models.function import FunctionModel

from app.agents import execution
from app.agents.execution import AgentExecutionConfig, AgentRequestDeadlineExceeded
from app.agents.pydantic_poc import (
    TripReadCapability,
    build_deepseek_chat_model,
    classify_poc_exception,
    run_fake_read_only_probe,
    run_structured_preview_with_deadline,
    run_structured_preview_poc,
)


# PR-03 must fail before sending a real provider request, even if a future test
# accidentally passes a live model name instead of TestModel/FunctionModel.
models.ALLOW_MODEL_REQUESTS = False


def test_fake_model_executes_a_trip_scoped_read_only_tool_with_immutable_deps(
    db: Session,
):
    """The PoC uses a new worker-owned Session, never the request Session."""
    with _synthetic_fixture(db) as fixture:
        base_factory = _independent_session_factory(db)
        worker_sessions = []
        worker_connection_is_independent = []
        closed = threading.Event()

        def worker_session_factory():
            worker_session = base_factory()
            worker_connection_is_independent.append(
                worker_session.connection() is not db.connection()
            )
            original_close = worker_session.close

            def close():
                closed.set()
                original_close()

            worker_session.close = close
            worker_sessions.append(worker_session)
            return worker_session

        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=worker_session_factory,
        )

        observation = asyncio.run(run_fake_read_only_probe(capability))

    assert observation.output_kind == "reply_only"
    assert observation.tool_trajectory == ("get_trip_facts",)
    assert observation.facts["destination"] == "Synthetic City"
    assert observation.facts["member_count"] == 2
    assert observation.usage.requests >= 1
    assert len(worker_sessions) == 1
    assert worker_sessions[0] is not db
    assert worker_connection_is_independent == [True]
    assert closed.is_set()


def test_function_model_retries_invalid_tool_arguments_and_invalid_structured_output(
    db: Session,
):
    """Typed tool and output validators reject invented data before a preview."""
    with _synthetic_fixture(db) as fixture:
        item_id = fixture["items"]["art"].id
        membership_id = fixture["memberships"]["organizer"].id
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=membership_id,
            session_factory=_independent_session_factory(db),
        )
        calls = 0

        def scripted_model(_messages, _info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[ToolCallPart("get_plan_item", {"item_id": 9})]
                )
            if calls == 2:
                return ModelResponse(
                    parts=[ToolCallPart("get_plan_item", {"item_id": item_id})]
                )
            if calls == 3:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "change_preview",
                            {
                                "output_kind": "change_preview",
                                "suggested_action": {
                                    "kind": "move_time",
                                    "item_id": "invented-item",
                                    "new_start_hour": 15.5,
                                },
                            },
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "change_preview",
                        {
                            "output_kind": "change_preview",
                            "suggested_action": {
                                "kind": "move_time",
                                "item_id": item_id,
                                "new_start_hour": 15.5,
                            },
                        },
                    )
                ]
            )

        observation = asyncio.run(
            run_structured_preview_poc(
                capability,
                model=FunctionModel(scripted_model),
                message="Move this to 3:30 PM",
            )
        )

    assert calls == 4
    assert observation.output_kind == "change_preview"
    assert observation.suggested_action == {
        "kind": "move_time",
        "item_id": item_id,
        "new_start_hour": 15.5,
    }
    assert observation.tool_trajectory == ("get_plan_item",)
    assert observation.error_classification is None
    trace_text = json.dumps(observation.safety_metadata, sort_keys=True)
    assert "sensitive fixture phrase" not in trace_text
    assert membership_id not in trace_text
    assert observation.safety_metadata["request_session_shared"] is False


@pytest.mark.parametrize(
    "limits",
    [
        UsageLimits(request_limit=1, tool_calls_limit=1),
        UsageLimits(request_limit=2, tool_calls_limit=0),
    ],
)
def test_fake_model_respects_request_and_tool_usage_limits(
    db: Session, limits: UsageLimits
):
    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=_independent_session_factory(db),
        )
        with pytest.raises(UsageLimitExceeded) as error:
            asyncio.run(run_fake_read_only_probe(capability, usage_limits=limits))

    assert classify_poc_exception(error.value) == "usage_limit_exceeded"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, "provider_auth_failed"), (429, "provider_rate_limited"), (503, "provider_unavailable")],
)
def test_fake_transport_http_errors_map_to_stable_failure_taxonomy(
    db: Session, status_code: int, expected: str
):
    def failing_transport(_messages, _info):
        raise ModelHTTPError(
            status_code,
            "deepseek-v4-flash",
            body={"synthetic": True},
        )

    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=_independent_session_factory(db),
        )
        with pytest.raises(ModelHTTPError) as error:
            asyncio.run(
                run_structured_preview_poc(
                    capability,
                    model=FunctionModel(failing_transport),
                    message="Move this later",
                )
            )

    assert classify_poc_exception(error.value) == expected


def test_deepseek_model_configuration_is_constructible_without_a_network_call():
    model = build_deepseek_chat_model(api_key="poc-placeholder")

    assert model.model_name == "deepseek-v4-flash"
    assert type(model.provider).__name__ == "DeepSeekProvider"
    assert model.settings["thinking"] is False


def test_invalid_model_output_exhausts_only_the_bounded_output_retry_budget(
    db: Session,
):
    calls = 0

    def always_invalid_output(_messages, _info):
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "change_preview",
                    {"output_kind": "change_preview", "suggested_action": {}},
                )
            ]
        )

    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=_independent_session_factory(db),
        )
        with pytest.raises(UnexpectedModelBehavior):
            asyncio.run(
                run_structured_preview_poc(
                    capability,
                    model=FunctionModel(always_invalid_output),
                    message="Move this later",
                )
            )

    assert calls == 2


def test_cooperative_pydantic_run_is_cancelled_by_the_pr01c_request_deadline(
    monkeypatch, db: Session
):
    started = threading.Event()
    cancelled = threading.Event()
    never = asyncio.Event()
    lifecycle_events = []

    async def cooperative_model(_messages, _info):
        started.set()
        try:
            await never.wait()
        finally:
            cancelled.set()

    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=_independent_session_factory(db),
        )
        monkeypatch.setattr(
            execution.trace,
            "record_agent_lifecycle",
            lambda **event: lifecycle_events.append(event),
        )
        with pytest.raises(AgentRequestDeadlineExceeded):
            run_structured_preview_with_deadline(
                capability,
                model=FunctionModel(cooperative_model),
                message="Move this later",
                config=AgentExecutionConfig(
                    request_timeout_seconds=0.01,
                    provider_timeout_seconds=0.5,
                    tool_timeout_seconds=0.5,
                ),
            )

    assert started.is_set()
    assert _wait_until(cancelled.is_set)
    assert lifecycle_events[0]["phase"] == "request_timeout"


def test_non_cooperative_pydantic_run_is_discarded_after_the_pr01c_deadline(
    monkeypatch, db: Session
):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    lifecycle_events = []

    def blocking_model(_messages, _info):
        started.set()
        release.wait(timeout=1)
        finished.set()
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "clarification",
                    {"output_kind": "clarification", "question": "Which time?"},
                )
            ]
        )

    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=_independent_session_factory(db),
        )
        monkeypatch.setattr(
            execution.trace,
            "record_agent_lifecycle",
            lambda **event: lifecycle_events.append(event),
        )
        try:
            with pytest.raises(AgentRequestDeadlineExceeded):
                run_structured_preview_with_deadline(
                    capability,
                    model=FunctionModel(blocking_model),
                    message="Move this later",
                    config=AgentExecutionConfig(
                        request_timeout_seconds=0.01,
                        provider_timeout_seconds=0.5,
                        tool_timeout_seconds=0.5,
                    ),
                )
            assert started.is_set()
        finally:
            release.set()

    assert _wait_until(finished.is_set)
    assert _wait_until(lambda: any(event["phase"] == "late_completion" for event in lifecycle_events))
    assert lifecycle_events[-1]["result_consumed"] is False


def _wait_until(predicate, *, timeout: float = 0.5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
