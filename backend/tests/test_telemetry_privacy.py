from __future__ import annotations

from pathlib import Path

import pytest

from app.agents import base, trace


def test_legacy_trace_calls_neither_log_nor_persist_sensitive_markers(capsys, monkeypatch, tmp_path):
    marker = "PRIVATE_MARKER_tool_argument_result_guard_reason_provider_error"
    monkeypatch.setattr(trace, "LOG_DIR", tmp_path)

    summary = trace.record_agent_round(
        trace_id=marker,
        route=marker,
        provider=marker,
        model=marker,
        round_index=1,
        requested_tool=True,
        tool_name=marker,
        tool_arguments={"nested": [marker]},
        tool_result={
            "calls": [
                {
                    "tool": marker,
                    "arguments": {"secret": marker},
                    "output": {"secret": marker},
                    "guard_rejected": True,
                    "guard_reason": marker,
                }
            ]
        },
        elapsed_ms=1.0,
        tokens=trace.TokenUsage(total_tokens=1),
    )
    trace.record_model_call(
        trace_id=marker,
        route=marker,
        provider=marker,
        model=marker,
        provider_elapsed_ms=1.0,
        total_elapsed_ms=1.0,
        tokens=None,
        ok=False,
        failures=[{"provider": marker, "reason": marker}],
    )
    trace.record_agent_lifecycle(
        phase=marker,
        execution_id=marker,
        elapsed_ms=1.0,
        result_consumed=False,
        worker_cancel_requested=False,
    )

    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert marker not in repr(summary)
    assert summary == {
        "round_index": 1,
        "requested_tool": True,
        "tool_name": None,
        "guard_rejected": True,
        "guard_limit_exceeded": False,
        "tool_dispatch_status": "guard_rejected",
    }
    assert list(Path(tmp_path).iterdir()) == []


def test_call_model_never_prints_a_provider_exception_representation(capsys, monkeypatch):
    marker = "PRIVATE_MARKER_provider_exception"
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setattr(base, "_provider_chain", lambda _provider: (base.DEEPSEEK_PROVIDER,))
    monkeypatch.setattr(
        base,
        "provider_catalog",
        lambda: {
            base.DEEPSEEK_PROVIDER: base.ProviderConfig(
                name=base.DEEPSEEK_PROVIDER,
                api_key="test-key",
                base_url="https://safe.example",
                model="test-model",
            )
        },
    )

    def failing_provider(**_kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(base, "_invoke_provider", failing_provider)
    with pytest.raises(base.AgentUnavailable):
        base.call_model(
            system="system",
            user="user",
            schema={"type": "object"},
            schema_name="test",
            mock={},
        )

    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err


def test_agent_rounds_keep_behavioral_flags_but_not_tool_contents(monkeypatch):
    marker = "PRIVATE_MARKER_agent_tool_content"
    monkeypatch.setenv("MOCK_AI", "1")

    result = base.call_agent(
        system="system",
        user="user",
        tools=(
            base.AgentTool(
                name="test_tool",
                description="test tool",
                parameters={"type": "object"},
                handler=lambda value: {"private": value},
                guard=lambda _state, _args: marker,
            ),
        ),
        mock_rounds=(
            base.AgentProviderReply(
                content="",
                tool_calls=(base.AgentToolCall("call-1", "test_tool", {"value": marker}),),
            ),
        ),
        max_rounds=1,
    )

    assert len(result.rounds) == 1
    assert result.rounds[0]["requested_tool"] is True
    assert result.rounds[0]["guard_rejected"] is True
    assert marker not in repr(result.rounds)
    # Provenance remains internal for the existing deterministic agent path.
    assert result.tool_results[0]["guard_reason"] == marker
