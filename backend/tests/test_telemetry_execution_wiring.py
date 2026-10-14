"""Regression coverage for PR-06A.2 execution-facing telemetry hooks."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.agents import base, trace
from app.agents.execution import AgentExecutionConfig, AgentRequestDeadlineExceeded, run_agent_with_deadline
from app.observability.telemetry import (
    ChatRequestStarted,
    SafeTelemetryEmitter,
    TelemetryContext,
    emit_event_safely,
)


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
    monkeypatch.setattr(
        main,
        "emit_event_safely",
        lambda factory: emitted.append(factory()) or True,
    )
    monkeypatch.setattr(main, "_chat_with_trip_impl", lambda *args, **kwargs: {"reply": "ok"})

    response = main.chat_with_trip(
        "trip_12345678", main.ChatRequest(message="hello"), db=object(), me=object()
    )

    assert response == {"reply": "ok"}
    assert [event.event_name.value for event in emitted] == [
        "chat.request_started", "chat.request_completed",
    ]
    assert emitted[0].context.request_id == emitted[1].context.request_id


def test_telemetry_constructor_failure_does_not_change_legacy_result(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setattr(base, "ModelCallCompleted", lambda **_kwargs: (_ for _ in ()).throw(ValueError("marker")))

    result = base.call_agent(
        system="s",
        user="u",
        mock_rounds=(base.AgentProviderReply(content="unchanged"),),
        telemetry_context=_context(),
    )

    assert result.content == "unchanged"


def test_event_factory_mapping_and_emitter_failures_are_quiet_and_fail_open(capsys):
    marker = "SYNTHETIC_PRIVATE_MARKER_12345"

    class RaisingEmitter:
        def emit(self, _event):  # type: ignore[no-untyped-def]
            raise RuntimeError(marker)

    assert emit_event_safely(
        lambda: (_ for _ in ()).throw(ValueError(marker))
    ) is False
    assert emit_event_safely(
        lambda: ChatRequestStarted(context=_context()), emitter=RaisingEmitter()  # type: ignore[arg-type]
    ) is False

    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err


def test_unknown_globally_approved_tool_is_null_when_not_exposed(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    lines: list[str] = []
    base.call_agent(
        system="s",
        user="u",
        tools=(),
        mock_rounds=(
            base.AgentProviderReply(
                content="",
                tool_calls=(base.AgentToolCall(id="call", name="get_current_plan", arguments={}),),
            ),
            base.AgentProviderReply(content="done"),
        ),
        telemetry_context=_context(),
        telemetry_emitter=SafeTelemetryEmitter(lines.append),
    )

    event = next(json.loads(line) for line in lines if "tool_call_completed" in line)
    assert event["tool_dispatch_status"] == "unknown_tool"
    assert event["tool_name"] is None


@pytest.mark.parametrize(
    ("status_code", "expected_terminal"),
    [(403, "access_denied"), (404, "not_found"), (409, "conflict"), (503, "runtime_failure")],
)
def test_chat_route_maps_controlled_http_status_to_one_terminal_event(
    monkeypatch, status_code, expected_terminal
):
    from app.api import main

    emitted = []
    monkeypatch.setattr(
        main, "emit_event_safely", lambda factory: emitted.append(factory()) or True
    )
    monkeypatch.setattr(
        main,
        "_chat_with_trip_impl",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code)),
    )

    with pytest.raises(HTTPException):
        main.chat_with_trip("trip_12345678", main.ChatRequest(message="hello"), db=object(), me=object())

    assert [event.event_name.value for event in emitted] == [
        "chat.request_started", "chat.request_completed",
    ]
    assert emitted[-1].terminal_category.value == expected_terminal


def test_chat_route_continues_when_context_construction_fails(monkeypatch):
    from app.api import main

    monkeypatch.setattr(
        main.TelemetryContext,
        "new",
        classmethod(lambda cls: (_ for _ in ()).throw(ValueError("marker"))),
    )
    monkeypatch.setattr(main, "_chat_with_trip_impl", lambda *args, **kwargs: {"reply": "ok"})

    assert main.chat_with_trip(
        "trip_12345678", main.ChatRequest(message="hello"), db=object(), me=object()
    ) == {"reply": "ok"}


@pytest.mark.parametrize(
    "terminal",
    ["succeeded", "deterministic_clarification", "degraded_fallback"],
)
def test_chat_route_uses_structured_success_terminal_without_reply_text_inference(
    monkeypatch, terminal
):
    from app.api import main
    from app.observability.telemetry import TerminalCategory

    emitted = []
    monkeypatch.setattr(
        main, "emit_event_safely", lambda factory: emitted.append(factory()) or True
    )
    monkeypatch.setattr(
        main,
        "_chat_with_trip_impl",
        lambda *args, **kwargs: main._ChatTelemetryResponse(
            {"reply": "same reply text for every outcome"}, TerminalCategory(terminal)
        ),
    )

    assert main.chat_with_trip(
        "trip_12345678", main.ChatRequest(message="hello"), db=object(), me=object()
    ) == {"reply": "same reply text for every outcome"}
    assert emitted[-1].terminal_category.value == terminal


def test_chat_route_marks_unhandled_exception_once_before_reraising(monkeypatch):
    from app.api import main

    emitted = []
    monkeypatch.setattr(
        main, "emit_event_safely", lambda factory: emitted.append(factory()) or True
    )
    monkeypatch.setattr(
        main,
        "_chat_with_trip_impl",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("synthetic")),
    )

    with pytest.raises(ValueError, match="synthetic"):
        main.chat_with_trip(
            "trip_12345678", main.ChatRequest(message="hello"), db=object(), me=object()
        )

    assert [event.event_name.value for event in emitted] == [
        "chat.request_started", "chat.request_completed",
    ]
    assert emitted[-1].terminal_category.value == "unhandled_error"
