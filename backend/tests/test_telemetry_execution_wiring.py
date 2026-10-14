"""Regression coverage for PR-06A.2 execution-facing telemetry hooks."""

from __future__ import annotations

import json

import pytest

from app.agents import base, trace
from app.agents.execution import AgentExecutionConfig, AgentRequestDeadlineExceeded, run_agent_with_deadline
from app.observability.telemetry import SafeTelemetryEmitter, TelemetryContext


def _context() -> TelemetryContext:
    return TelemetryContext(
        request_id="request_12345678",
        trace_id="trace___12345678",
        execution_id="execution_12345678",
    )


def test_legacy_mock_and_real_tool_dispatch_emit_closed_safe_facts(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    lines: list[str] = []
    tool = base.AgentTool(
        name="get_current_plan",
        description="safe",
        parameters={"type": "object"},
        handler=lambda: {"private": "never serialized"},
    )
    result = base.call_agent(
        system="s",
        user="SYNTHETIC_PRIVATE_MARKER_12345",
        tools=(tool,),
        mock_rounds=(
            base.AgentProviderReply(
                content="",
                tool_calls=(base.AgentToolCall(id="call", name="get_current_plan", arguments={}),),
                tokens=trace.TokenUsage(total_tokens=0),
            ),
            base.AgentProviderReply(content="done", tokens=trace.TokenUsage()),
        ),
        telemetry_context=_context(),
        telemetry_emitter=SafeTelemetryEmitter(lines.append),
    )

    assert result.content == "done"
    events = [json.loads(line) for line in lines]
    assert [event["event_name"] for event in events] == [
        "agent.model_call_completed",
        "agent.tool_call_completed",
        "agent.model_call_completed",
    ]
    assert events[0]["invocation_source"] == "mock"
    assert events[0]["usage_status"] == "reported"
    assert events[0]["total_tokens"] == 0
    assert events[1]["tool_dispatch_status"] == "handler_succeeded"
    assert all("SYNTHETIC_PRIVATE_MARKER_12345" not in line for line in lines)


def test_deadline_lifecycle_telemetry_is_best_effort_and_does_not_change_timeout():
    lines: list[str] = []
    with pytest.raises(AgentRequestDeadlineExceeded):
        run_agent_with_deadline(
            worker=lambda context: (context.request_cancellation(), context.ensure_request_active()),
            config=AgentExecutionConfig(
                request_timeout_seconds=1,
                provider_timeout_seconds=1,
                tool_timeout_seconds=1,
            ),
            telemetry_context=_context(),
            telemetry_emitter=SafeTelemetryEmitter(lines.append),
        )
    events = [json.loads(line) for line in lines]
    assert events[-1]["event_name"] == "agent.deadline_exceeded"
    assert events[-1]["failure_category"] == "request_timeout"


def test_chat_route_owns_one_start_and_one_terminal_event(monkeypatch):
    from app.api import main

    emitted = []
    monkeypatch.setattr(main, "emit_safely", lambda event: emitted.append(event) or True)
    monkeypatch.setattr(main, "_chat_with_trip_impl", lambda *args, **kwargs: {"reply": "ok"})

    response = main.chat_with_trip(
        "trip_12345678", main.ChatRequest(message="hello"), db=object(), me=object()
    )

    assert response == {"reply": "ok"}
    assert [event.event_name.value for event in emitted] == [
        "chat.request_started", "chat.request_completed",
    ]
    assert emitted[0].context.request_id == emitted[1].context.request_id
