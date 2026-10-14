from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.observability import telemetry
from app.observability.telemetry import (
    AgentCapacityRejected,
    AgentDeadlineExceeded,
    ApprovedToolName,
    ChatRequestStarted,
    FailureCategory,
    ModelCallCompleted,
    InvocationOutcome,
    InvocationSource,
    ModelId,
    ProviderName,
    RuntimeSelected,
    RuntimeName,
    SafeTelemetryEmitter,
    TelemetryContext,
    ToolCallCompleted,
    ToolDispatchStatus,
    UsageStatus,
    DeadlineScope,
)


def _context() -> TelemetryContext:
    return TelemetryContext(
        request_id="request_12345678",
        trace_id="trace_12345678",
        execution_id="execution_12345678",
    )


def test_context_is_opaque_typed_and_has_no_arbitrary_metadata():
    context = _context()

    assert context.request_id == "request_12345678"
    with pytest.raises(ValueError, match="opaque"):
        TelemetryContext(request_id="trip id is not opaque")
    with pytest.raises(TypeError):
        TelemetryContext(request_id="request_12345678", metadata={})  # type: ignore[call-arg]


def test_event_schema_is_fixed_and_emits_one_compact_json_line():
    lines: list[str] = []
    event = ChatRequestStarted(context=_context())

    assert SafeTelemetryEmitter(lines.append).emit(event) is True
    assert len(lines) == 1
    assert "\n" not in lines[0]
    assert json.loads(lines[0]) == {
        "schema_version": "cadensy.telemetry.v1",
        "event_name": "chat.request_started",
        "timestamp_utc": event.timestamp_utc,
        "request_id": "request_12345678",
        "trace_id": "trace_12345678",
        "execution_id": "execution_12345678",
        "route_category": "trip_chat",
        "request_path": "trip_chat",
    }
    with pytest.raises(TypeError):
        ChatRequestStarted(context=_context(), raw_payload={})  # type: ignore[call-arg]
    assert SafeTelemetryEmitter(lines.append).emit({"event_name": "chat.request_started"}) is False  # type: ignore[arg-type]
    assert len(lines) == 1


def test_sink_failures_are_non_recursive_and_do_not_escape():
    calls = 0

    def failing_sink(_line: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("synthetic private sink failure")

    emitter = SafeTelemetryEmitter(failing_sink)
    assert emitter.emit(ChatRequestStarted(context=_context())) is False
    assert emitter.emit(ChatRequestStarted(context=_context())) is False
    assert calls == 2


def test_unregistered_subclass_cannot_extend_or_serialize_the_approved_schema(capsys):
    marker = "SYNTHETIC_PRIVATE_MARKER_12345"

    @dataclass(frozen=True, kw_only=True)
    class LeakyEvent(ChatRequestStarted):
        raw_user_message: str

    event = LeakyEvent(context=_context(), raw_user_message=marker)
    lines: list[str] = []

    with pytest.raises(ValueError, match="unregistered"):
        event.to_dict()
    assert SafeTelemetryEmitter(lines.append).emit(event) is False
    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert lines == []


def test_default_stdout_sink_failure_is_quiet_and_reports_failure(capsys, monkeypatch):
    marker = "SYNTHETIC_PRIVATE_MARKER_12345"

    class FailingStdout:
        def write(self, _text: str) -> int:
            raise RuntimeError(marker)

        def flush(self) -> None:
            return None

    with monkeypatch.context() as patch:
        patch.setattr(telemetry.sys, "stdout", FailingStdout())
        assert SafeTelemetryEmitter().emit(ChatRequestStarted(context=_context())) is False

    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert "Logging error" not in captured.err
    assert "Traceback" not in captured.err


def test_model_usage_preserves_null_vs_reported_zero_and_rejects_invalid_counts():
    unknown_usage = ModelCallCompleted(
        context=_context(),
        model_invocation_index=1,
        invocation_source=InvocationSource.MOCK,
        provider=ProviderName.DEEPSEEK,
        model_id=ModelId.DEEPSEEK_V4_FLASH,
        invocation_outcome=InvocationOutcome.RETURNED,
        failure_category=None,
        elapsed_ms=0,
        usage_status=UsageStatus.NOT_REPORTED,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
    )
    zero_usage = ModelCallCompleted(
        context=_context(),
        model_invocation_index=2,
        invocation_source=InvocationSource.FAKE,
        provider=ProviderName.DEEPSEEK,
        model_id=ModelId.DEEPSEEK_V4_FLASH,
        invocation_outcome=InvocationOutcome.RETURNED,
        failure_category=None,
        elapsed_ms=0.5,
        usage_status=UsageStatus.REPORTED,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )

    assert unknown_usage.to_dict()["total_tokens"] is None
    assert zero_usage.to_dict()["total_tokens"] == 0
    with pytest.raises(ValueError, match="non-negative"):
        ModelCallCompleted(
            context=_context(),
            model_invocation_index=1,
            invocation_source=InvocationSource.MOCK,
            provider=ProviderName.DEEPSEEK,
            model_id=ModelId.DEEPSEEK_V4_FLASH,
            invocation_outcome=InvocationOutcome.RETURNED,
            failure_category=None,
            elapsed_ms=-1,
            usage_status=UsageStatus.NOT_REPORTED,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    for invalid_count in (1.5, True, -1, 10_000_001):
        with pytest.raises(ValueError, match="non-negative bounded integer"):
            ModelCallCompleted(
                context=_context(),
                model_invocation_index=1,
                invocation_source=InvocationSource.MOCK,
                provider=ProviderName.DEEPSEEK,
                model_id=ModelId.DEEPSEEK_V4_FLASH,
                invocation_outcome=InvocationOutcome.RETURNED,
                failure_category=None,
                elapsed_ms=0,
                usage_status=UsageStatus.REPORTED,
                prompt_tokens=invalid_count,
                completion_tokens=0,
                total_tokens=0,
            )


def test_unknown_tool_name_never_enters_the_typed_schema():
    unknown = ToolCallCompleted(
        context=_context(),
        tool_name=None,
        tool_dispatch_status=ToolDispatchStatus.UNKNOWN_TOOL,
        handler_executed=False,
        cache_hit=False,
    )

    assert unknown.to_dict()["tool_name"] is None
    with pytest.raises(ValueError, match="approved"):
        ToolCallCompleted(
            context=_context(),
            tool_name="marker-from-model",  # type: ignore[arg-type]
            tool_dispatch_status=ToolDispatchStatus.UNKNOWN_TOOL,
            handler_executed=False,
            cache_hit=False,
        )
    with pytest.raises(ValueError, match="unknown tools"):
        ToolCallCompleted(
            context=_context(),
            tool_name=ApprovedToolName.GET_CURRENT_PLAN,
            tool_dispatch_status=ToolDispatchStatus.UNKNOWN_TOOL,
            handler_executed=False,
            cache_hit=False,
        )
    with pytest.raises(ValueError, match="closed ToolDispatchStatus"):
        ToolCallCompleted(
            context=_context(),
            tool_name=None,
            tool_dispatch_status="unknown_tool",  # type: ignore[arg-type]
            handler_executed=False,
            cache_hit=False,
        )


def test_dispatch_failure_is_not_represented_as_a_handler_failure():
    dispatch_error = ToolCallCompleted(
        context=_context(),
        tool_name=ApprovedToolName.GET_CURRENT_PLAN,
        tool_dispatch_status=ToolDispatchStatus.DISPATCH_FAILED,
        handler_executed=False,
        cache_hit=False,
    )
    assert dispatch_error.to_dict()["tool_dispatch_status"] == "dispatch_failed"
    with pytest.raises(ValueError, match="handler outcomes"):
        ToolCallCompleted(
            context=_context(),
            tool_name=ApprovedToolName.GET_CURRENT_PLAN,
            tool_dispatch_status=ToolDispatchStatus.HANDLER_FAILED,
            handler_executed=False,
            cache_hit=False,
        )


def test_capacity_rejection_is_not_a_deadline_event():
    capacity = AgentCapacityRejected(context=_context(), elapsed_ms=0)
    assert capacity.to_dict()["event_name"] == "agent.capacity_rejected"
    with pytest.raises(ValueError, match="capacity rejection"):
        AgentDeadlineExceeded(
            context=_context(),
            deadline_scope=DeadlineScope.REQUEST,
            failure_category=FailureCategory.RUNTIME_CAPACITY,
            elapsed_ms=0,
            result_consumed=False,
            cancellation_requested=False,
        )
    with pytest.raises(ValueError, match="do not match"):
        AgentDeadlineExceeded(
            context=_context(),
            deadline_scope=DeadlineScope.REQUEST,
            failure_category=FailureCategory.PROVIDER_TIMEOUT,
            elapsed_ms=0,
            result_consumed=False,
            cancellation_requested=False,
        )


def test_runtime_model_and_tool_events_require_agent_correlation_ids():
    missing_correlation = TelemetryContext(request_id="request_12345678")

    with pytest.raises(ValueError, match="requires trace_id and execution_id"):
        RuntimeSelected(context=missing_correlation, runtime_name=RuntimeName.LEGACY)
    with pytest.raises(ValueError, match="requires trace_id and execution_id"):
        ModelCallCompleted(
            context=missing_correlation,
            model_invocation_index=1,
            invocation_source=InvocationSource.MOCK,
            provider=ProviderName.DEEPSEEK,
            model_id=ModelId.DEEPSEEK_V4_FLASH,
            invocation_outcome=InvocationOutcome.RETURNED,
            failure_category=None,
            elapsed_ms=0,
            usage_status=UsageStatus.NOT_REPORTED,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    with pytest.raises(ValueError, match="requires trace_id and execution_id"):
        ToolCallCompleted(
            context=missing_correlation,
            tool_name=ApprovedToolName.GET_CURRENT_PLAN,
            tool_dispatch_status=ToolDispatchStatus.HANDLER_SUCCEEDED,
            handler_executed=True,
            cache_hit=False,
        )
