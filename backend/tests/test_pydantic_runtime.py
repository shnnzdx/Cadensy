"""PR-04B isolated Pydantic Runtime contract tests.

These tests exercise the public ``ChatAgentRuntime.run`` seam with local
Pydantic AI FunctionModels only.  They neither route through HTTP nor permit a
network request.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from evals.runner import _independent_session_factory, _synthetic_fixture

pytest.importorskip(
    "pydantic_ai",
    reason="PR-04B adapter tests require requirements-pydantic-ai-poc.lock.txt",
)

from pydantic_ai import ModelResponse, ToolCallPart, models
from pydantic_ai.models.function import FunctionModel

from app.agents import execution
from app.agents.execution import AgentExecutionConfig
from app.agents.legacy_runtime import LegacyReadTripCapability
from app.agents.pydantic_runtime import PydanticChatAgentRuntime
from app.agents.runtime_factory import build_chat_runtime
from app.agents.runtime_contract import (
    AgentClarification,
    AgentReplyOnly,
    AgentSuggestedChange,
    RuntimeHistoryTurn,
    RuntimeLimits,
    RuntimeResult,
    RuntimeRequest,
)
from app.db.models import ChangeProposal, PlanItem, Vote


# Prevent an accidental replacement of a fake model with a live model.
models.ALLOW_MODEL_REQUESTS = False


@dataclass
class _SyntheticReadTool:
    name: str = "get_current_plan"
    description: str = "Read the scoped Current Plan."
    parameters: dict[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "additionalProperties": False,
            "properties": {"day": {"type": "string"}},
            "required": ["day"],
        }
    )
    plan: dict[str, object] = field(
        default_factory=lambda: {
            "days": [
                {
                    "items": [
                        {
                            "id": "scoped-item",
                            "title": "Scoped museum visit",
                            "start_hour": 14.0,
                        }
                    ]
                }
            ]
        }
    )
    calls: list[dict[str, object]] = field(default_factory=list)
    block_for_seconds: float = 0.0

    def invoke(self, **arguments: object) -> object:
        self.calls.append(arguments)
        if self.block_for_seconds:
            time.sleep(self.block_for_seconds)
        if set(arguments) != {"day"} or not isinstance(arguments["day"], str):
            raise AssertionError("tool arguments were not validated")
        return self.plan


@dataclass
class _SyntheticCapability:
    tools: tuple[_SyntheticReadTool, ...] = field(default_factory=lambda: (_SyntheticReadTool(),))
    entered: threading.Event = field(default_factory=threading.Event)

    def run_with_read_only_tools(self, operation):  # type: ignore[no-untyped-def]
        self.entered.set()
        return operation(self.tools)


def _request(*, max_rounds: int = 3, max_total_tokens: int | None = 1_000) -> RuntimeRequest:
    return RuntimeRequest(
        message="Please help with the Current Plan.",
        history=(RuntimeHistoryTurn(role="user", text="Earlier question"),),
        selected_item_ref="scoped-item",
        request_id="pydantic-runtime-test",
        limits=RuntimeLimits(
            max_rounds=max_rounds,
            max_total_tokens=max_total_tokens,
            max_tokens=128,
            guard_reject_limit=1,
        ),
    )


def _execution(*, request: float = 0.5, tool: float = 0.1) -> AgentExecutionConfig:
    return AgentExecutionConfig(
        request_timeout_seconds=request,
        provider_timeout_seconds=0.25,
        tool_timeout_seconds=tool,
    )


def _runtime(model: object) -> PydanticChatAgentRuntime:
    return PydanticChatAgentRuntime(model=model, system_prompt="Synthetic system prompt.")


def test_explicit_pydantic_selector_composes_only_an_injected_pydantic_runtime(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")
    expected = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Synthetic reply."},
                    )
                ]
            )
        )
    )

    runtime = build_chat_runtime(
        system_prompt="Synthetic system prompt.",
        pydantic_runtime_factory=lambda: expected,
    )

    assert runtime is expected


@pytest.mark.parametrize(
    ("tool_name", "payload", "outcome_type"),
    [
        (
            "runtime_reply",
            {"output_kind": "reply_only", "reply": "Here is the plan summary."},
            AgentReplyOnly,
        ),
        (
            "runtime_clarification",
            {
                "output_kind": "clarification",
                "reply": "Which time should I use for the museum?",
            },
            AgentClarification,
        ),
    ],
)
def test_typed_reply_and_clarification_map_to_common_runtime_result(
    tool_name: str, payload: dict[str, object], outcome_type: type[object]
):
    result = _runtime(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[ToolCallPart(tool_name, payload)]))
    ).run(_request(), _SyntheticCapability(), execution=_execution())

    assert isinstance(result.outcome, outcome_type)
    assert result.reply == payload["reply"]
    assert result.candidate_options == ()
    assert result.observation.failure is None


def test_suggested_change_requires_same_run_scoped_read_and_has_no_write_authority():
    calls = 0
    capability = _SyntheticCapability()

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_suggested_change",
                    {
                        "output_kind": "suggested_change",
                        "reply": "I can prepare this proposed time change.",
                        "item_ref": "scoped-item",
                        "safe_patch": {"start_hour": 15.5},
                    },
                )
            ]
        )

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 2
    assert capability.tools[0].calls == [{"day": "all"}]
    assert isinstance(result.outcome, AgentSuggestedChange)
    assert result.outcome.item_ref == "scoped-item"
    assert result.outcome.safe_patch == {"start_hour": 15.5}
    assert result.reply
    assert result.observation.failure is None


@pytest.mark.parametrize(
    "unsupported_patch",
    [
        {
            "title": "Invented Cafe",
            "place": "Invented Address",
            "lat": 41.0,
            "lng": -87.0,
        },
        {"price_per_person": 20},
    ],
)
def test_replacement_shaped_suggestion_without_provenance_fails_closed(
    unsupported_patch: dict[str, object],
):
    calls = 0
    capability = _SyntheticCapability()

    def read_then_replacement_attempt(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_suggested_change",
                    {
                        "output_kind": "suggested_change",
                        "reply": "I can replace this venue.",
                        "item_ref": "scoped-item",
                        "safe_patch": unsupported_patch,
                    },
                )
            ]
        )

    result = _runtime(FunctionModel(read_then_replacement_attempt)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 3  # plan read plus one bounded invalid-output retry
    assert capability.tools[0].calls == [{"day": "all"}]
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"
    assert not isinstance(result.outcome, AgentSuggestedChange)


@pytest.mark.parametrize("item_ref", ["scoped-item", "forged-other-trip-item"])
def test_suggested_change_without_matching_same_run_read_fails_closed(item_ref: str):
    calls = 0
    capability = _SyntheticCapability()

    def invented_suggestion(_messages, _info):
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_suggested_change",
                    {
                        "output_kind": "suggested_change",
                        "reply": "I will change it.",
                        "item_ref": item_ref,
                        "safe_patch": {"start_hour": 15.0},
                    },
                )
            ]
        )

    result = _runtime(FunctionModel(invented_suggestion)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 2  # exactly one bounded output-validation retry
    assert capability.tools[0].calls == []
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"
    assert not isinstance(result.outcome, AgentSuggestedChange)


def test_tool_schema_has_no_model_selectable_scope_or_storage_parameters():
    captured_schema: dict[str, object] = {}

    def capture_schema(_messages, info):
        tool = next(tool for tool in info.function_tools if tool.name == "get_current_plan")
        captured_schema.update(tool.parameters_json_schema)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_reply",
                    {"output_kind": "reply_only", "reply": "I need no scoped read."},
                )
            ]
        )

    result = _runtime(FunctionModel(capture_schema)).run(
        _request(), _SyntheticCapability(), execution=_execution()
    )

    assert result.observation.failure is None
    serialized = json.dumps(captured_schema, sort_keys=True).lower()
    for forbidden in (
        "trip_id",
        "actor_membership_id",
        "membership_id",
        "session",
        "sql",
        "query",
        "commit",
    ):
        assert forbidden not in serialized
    assert "day" in serialized


def test_invalid_tool_arguments_are_rejected_before_the_scoped_tool_runs():
    calls = 0
    capability = _SyntheticCapability()

    def invalid_then_reply(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": 7})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_reply",
                    {"output_kind": "reply_only", "reply": "Please provide a day."},
                )
            ]
        )

    result = _runtime(FunctionModel(invalid_then_reply)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 1  # the minimum read tool has zero framework retries
    assert capability.tools[0].calls == []
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"


def test_adapter_uses_common_capability_with_independent_session_and_no_durable_write(
    db: Session,
):
    with _synthetic_fixture(db) as fixture:
        item = fixture["items"]["art"]
        item_id = item.id
        original_start_hour = item.start_hour
        base_factory = _independent_session_factory(db)
        worker_sessions: list[Session] = []
        worker_connection_is_independent: list[bool] = []
        closed = threading.Event()

        def tracked_session_factory() -> Session:
            worker_session = base_factory()
            original_close = worker_session.close

            def close() -> None:
                closed.set()
                original_close()

            worker_session.close = close  # type: ignore[method-assign]
            worker_connection_is_independent.append(
                worker_session.connection() is not db.connection()
            )
            worker_sessions.append(worker_session)
            return worker_session

        capability = LegacyReadTripCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=tracked_session_factory,
        )
        with base_factory() as verification_db:
            proposal_count_before = len(
                verification_db.scalars(
                    select(ChangeProposal).where(ChangeProposal.plan_item_id == item_id)
                ).all()
            )
            vote_count_before = len(verification_db.scalars(select(Vote)).all())
        calls = 0

        def scripted_model(_messages, _info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_suggested_change",
                        {
                            "output_kind": "suggested_change",
                            "reply": "Here is a non-authoritative suggestion.",
                            "item_ref": item.id,
                            "safe_patch": {"start_hour": 16.0},
                        },
                    )
                ]
            )

        result = _runtime(FunctionModel(scripted_model)).run(
            _request(), capability, execution=_execution()
        )

        with base_factory() as verification_db:
            persisted_item = verification_db.get(PlanItem, item_id)
            proposal_count_after = len(
                verification_db.scalars(
                    select(ChangeProposal).where(ChangeProposal.plan_item_id == item_id)
                ).all()
            )
            vote_count_after = len(verification_db.scalars(select(Vote)).all())

        assert isinstance(result.outcome, AgentSuggestedChange)
        assert persisted_item is not None
        assert persisted_item.start_hour == original_start_hour
        assert proposal_count_after == proposal_count_before
        assert vote_count_after == vote_count_before
        assert len(worker_sessions) == 1
        assert worker_sessions[0] is not db
        assert worker_connection_is_independent == [True]
        assert closed.is_set()


def test_blocking_scoped_tool_maps_to_tool_timeout_without_consuming_late_result():
    tool = _SyntheticReadTool(block_for_seconds=0.03)
    capability = _SyntheticCapability(tools=(tool,))

    result = _runtime(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})]))
    ).run(_request(), capability, execution=_execution(tool=0.001))

    assert tool.calls == [{"day": "all"}]
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "tool_timeout"


def test_request_timeout_discards_late_pydantic_result(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_model(_messages, _info):
        started.set()
        release.wait(timeout=5.0)
        finished.set()
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_reply",
                    {"output_kind": "reply_only", "reply": "This reply arrived too late."},
                )
            ]
        )

    lifecycle_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        execution.trace,
        "record_agent_lifecycle",
        lambda **event: lifecycle_events.append(event),
    )
    result_holder: dict[str, RuntimeResult] = {}
    caller_errors: list[BaseException] = []

    def call_runtime() -> None:
        try:
            result_holder["result"] = _runtime(FunctionModel(blocking_model)).run(
                _request(),
                _SyntheticCapability(),
                execution=_execution(request=0.5),
            )
        except BaseException as error:
            caller_errors.append(error)

    caller = threading.Thread(target=call_runtime)
    caller.start()
    try:
        assert started.wait(timeout=2.0)
        caller.join(timeout=2.0)
        assert not caller.is_alive()
        assert not caller_errors
        result = result_holder["result"]
        assert result.observation.failure is not None
        assert result.observation.failure.normalized_kind == "request_timeout"
    finally:
        release.set()

    caller.join(timeout=2.0)
    assert not caller.is_alive()
    assert finished.wait(timeout=2.0)
    assert _wait_until(lambda: any(event["phase"] == "late_completion" for event in lifecycle_events))
    late_completion = next(
        event for event in lifecycle_events if event["phase"] == "late_completion"
    )
    assert late_completion["result_consumed"] is False
    assert late_completion["worker_cancel_requested"] is False


def test_usage_limit_and_malformed_output_are_normalized_and_bounded():
    usage_result = _runtime(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})]))
    ).run(_request(max_rounds=1), _SyntheticCapability(), execution=_execution())

    assert usage_result.observation.failure is not None
    assert usage_result.observation.failure.normalized_kind == "usage_limit"

    invalid_calls = 0

    def malformed_output(_messages, _info):
        nonlocal invalid_calls
        invalid_calls += 1
        return ModelResponse(
            parts=[ToolCallPart("runtime_suggested_change", {"output_kind": "suggested_change"})]
        )

    malformed_result = _runtime(FunctionModel(malformed_output)).run(
        _request(), _SyntheticCapability(), execution=_execution()
    )

    assert invalid_calls == 2
    assert malformed_result.observation.failure is not None
    assert malformed_result.observation.failure.normalized_kind == "malformed_runtime_output"


def test_missing_minimum_capability_is_an_adapter_failure_not_malformed_output():
    result = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Not reached."},
                    )
                ]
            )
        )
    ).run(_request(), _SyntheticCapability(tools=()), execution=_execution())

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "unexpected_exception"
    assert result.observation.failure.technical_kind == "_ToolSurfaceError"


def test_adapter_side_type_error_is_not_blamed_on_model_output(monkeypatch):
    async def broken_adapter(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise TypeError("synthetic adapter implementation error")

    monkeypatch.setattr(PydanticChatAgentRuntime, "_run_async", broken_adapter)

    result = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Not reached."},
                    )
                ]
            )
        )
    ).run(_request(), _SyntheticCapability(), execution=_execution())

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "unexpected_exception"
    assert result.observation.failure.technical_kind == "TypeError"


def test_pydantic_adapter_isolated_from_legacy_tool_internals_and_current_chat_service():
    adapter_source = inspect.getsource(__import__("app.agents.pydantic_runtime", fromlist=["*"]))
    common_contract_source = inspect.getsource(__import__("app.agents.runtime_contract", fromlist=["*"]))
    legacy_adapter_source = inspect.getsource(__import__("app.agents.legacy_runtime", fromlist=["*"]))
    chat_source = inspect.getsource(__import__("app.domain.chat.service", fromlist=["*"]))

    assert "app.agents.base" not in adapter_source
    assert "from . import base" not in adapter_source
    assert "base.AgentTool" not in adapter_source
    assert "_for_legacy_orchestration" not in adapter_source
    assert "_legacy_tool" not in adapter_source
    assert "pydantic_ai" not in common_contract_source
    assert "pydantic_ai" not in legacy_adapter_source
    assert "PydanticChatAgentRuntime" not in chat_source

    result = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Common DTO only."},
                    )
                ]
            )
        )
    ).run(_request(), _SyntheticCapability(), execution=_execution())
    assert type(result) is RuntimeResult
    assert type(result.outcome) is AgentReplyOnly


def _wait_until(predicate, *, timeout: float = 0.5) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
