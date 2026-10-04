from __future__ import annotations

from typing import Any

import pytest

from app.agents import base, trace
from app.agents.execution import (
    AgentExecutionConfig,
    AgentExecutionContext,
    AgentProviderDeadlineExceeded,
    AgentToolDeadlineExceeded,
)


PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "value": {"type": "string"},
        "day": {"type": "string"},
    },
}


def _call(id_: str, name: str, args: dict[str, Any] | None = None) -> base.AgentToolCall:
    return base.AgentToolCall(id=id_, name=name, arguments=args or {})


def _reply(
    content: str = "",
    calls: tuple[base.AgentToolCall, ...] = (),
    tokens: int = 1,
) -> base.AgentProviderReply:
    return base.AgentProviderReply(
        content=content,
        tool_calls=calls,
        tokens=trace.TokenUsage(total_tokens=tokens),
    )


def _tool(name: str, handler, guard=None) -> base.AgentTool:
    return base.AgentTool(
        name=name,
        description=f"{name} test tool",
        parameters=PARAMS,
        handler=handler,
        guard=guard,
    )


def test_call_agent_single_round_without_tools(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")

    result = base.call_agent(
        system="s",
        user="u",
        tools=(),
        mock_rounds=(_reply("done"),),
    )

    assert result.content == "done"
    assert result.stopped_reason is None
    assert len(result.rounds) == 1
    assert result.rounds[0]["requested_tool"] is False


def test_call_agent_one_tool_then_final(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def echo(value: str = ""):
        calls.append(value)
        return f"echo:{value}"

    result = base.call_agent(
        system="s",
        user="u",
        tools=(_tool("echo", echo),),
        mock_rounds=(
            _reply(calls=(_call("c1", "echo", {"value": "a"}),)),
            _reply("final"),
        ),
    )

    assert result.content == "final"
    assert calls == ["a"]
    assert [round_["requested_tool"] for round_ in result.rounds] == [True, False]
    assert result.tool_results[0]["output"] == "echo:a"


def test_call_agent_executes_multiple_tool_calls_and_feeds_results(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "0")
    seen_messages = []

    def fake_provider(*, messages, **kwargs):
        seen_messages.append([dict(message) for message in messages])
        if len(seen_messages) == 1:
            return base.AgentProviderReply(
                content="",
                tool_calls=(
                    _call("c1", "one", {"value": "a"}),
                    _call("c2", "two", {"value": "b"}),
                ),
                tokens=trace.TokenUsage(total_tokens=2),
            )
        return base.AgentProviderReply(content="final", tokens=trace.TokenUsage(total_tokens=1))

    monkeypatch.setattr(base, "_invoke_agent_provider", fake_provider)

    result = base.call_agent(
        system="s",
        user="u",
        tools=(
            _tool("one", lambda value="": f"one:{value}"),
            _tool("two", lambda value="": f"two:{value}"),
        ),
    )

    assert result.content == "final"
    second_messages = seen_messages[1]
    tool_messages = [message for message in second_messages if message["role"] == "tool"]
    assert [message["content"] for message in tool_messages] == ["one:a", "two:b"]
    assert [message["name"] for message in tool_messages] == ["one", "two"]


def test_guard_rejection_recovers_after_prerequisite_tool(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")

    def require_plan(state: base.AgentRunState, args: dict[str, Any]) -> str | None:
        if state.tool_call_counts.get("get_plan", 0) == 0:
            return "Cannot propose yet. Call get_plan(day='Wednesday') first, then retry."
        return None

    result = base.call_agent(
        system="s",
        user="u",
        tools=(
            _tool("get_plan", lambda day="": {"day": day, "items": ["museum"]}),
            _tool("propose", lambda value="": "proposal", guard=require_plan),
        ),
        mock_rounds=(
            _reply(calls=(_call("c1", "propose", {"value": "looser"}),)),
            _reply(calls=(_call("c2", "get_plan", {"day": "Wednesday"}),)),
            _reply(calls=(_call("c3", "propose", {"value": "looser"}),)),
            _reply("final"),
        ),
    )

    assert result.content == "final"
    assert result.stopped_reason is None
    assert [round_["guard_rejected"] for round_ in result.rounds] == [
        True,
        False,
        False,
        False,
    ]
    assert [item["tool"] for item in result.tool_results] == [
        "propose",
        "get_plan",
        "propose",
    ]
    assert result.tool_results[0]["guard_rejected"] is True
    assert result.tool_results[2]["output"] == "proposal"


def test_guard_rejection_limit_forces_stop(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")

    def always_reject(state: base.AgentRunState, args: dict[str, Any]) -> str:
        return "Call get_plan first."

    result = base.call_agent(
        system="s",
        user="u",
        tools=(_tool("propose", lambda: "never", guard=always_reject),),
        mock_rounds=(
            _reply(calls=(_call("c1", "propose"),)),
            _reply(calls=(_call("c2", "propose"),)),
            _reply(calls=(_call("c3", "propose"),)),
            _reply("would be too late"),
        ),
        guard_reject_limit=2,
    )

    assert result.stopped_reason == "guard_rejection_limit_exceeded"
    assert result.rounds[-1]["guard_limit_exceeded"] is True
    assert len(result.rounds) == 3


def test_round_limit_forces_stop(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")

    result = base.call_agent(
        system="s",
        user="u",
        tools=(_tool("loop", lambda: "again"),),
        mock_rounds=(
            _reply(calls=(_call("c1", "loop"),)),
            _reply(calls=(_call("c2", "loop"),)),
            _reply("would be too late"),
        ),
        max_rounds=2,
    )

    assert result.stopped_reason == "round_limit_exceeded"
    assert "exceeded the 2-round" in result.content
    assert len(result.rounds) == 2


def test_tool_cache_reuses_same_name_and_arguments(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    executions = 0

    def load(value: str = ""):
        nonlocal executions
        executions += 1
        return f"loaded:{value}"

    result = base.call_agent(
        system="s",
        user="u",
        tools=(_tool("load", load),),
        mock_rounds=(
            _reply(calls=(_call("c1", "load", {"value": "x"}),)),
            _reply(calls=(_call("c2", "load", {"value": "x"}),)),
            _reply("final"),
        ),
    )

    assert result.content == "final"
    assert executions == 1
    assert result.tool_results[1]["cached"] is True


def test_mock_ai_runs_full_tool_flow(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")

    result = base.call_agent(
        system="s",
        user="u",
        tools=(_tool("mock_tool", lambda value="": f"mock:{value}"),),
        mock_rounds=(
            _reply(calls=(_call("c1", "mock_tool", {"value": "ok"}),)),
            _reply("mock final"),
        ),
    )

    assert result.content == "mock final"
    assert result.tool_results[0]["output"] == "mock:ok"


def test_token_limit_forces_stop(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")

    result = base.call_agent(
        system="s",
        user="u",
        tools=(_tool("tool", lambda: "ok"),),
        mock_rounds=(_reply(calls=(_call("c1", "tool"),), tokens=10),),
        max_total_tokens=5,
    )

    assert result.stopped_reason == "token_limit_exceeded"


def test_agent_route_defaults_to_deepseek(monkeypatch):
    monkeypatch.delenv("AGENT_AI_PROVIDER", raising=False)

    assert base._resolve_provider_name(base.AGENT_ROUTE) == base.DEEPSEEK_PROVIDER


def test_call_agent_passes_only_the_remaining_provider_budget_to_the_provider(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "0")
    seen_timeouts = []

    def fake_provider(*, timeout_seconds, **_kwargs):
        seen_timeouts.append(timeout_seconds)
        return _reply("done")

    monkeypatch.setattr(base, "_invoke_agent_provider", fake_provider)
    deadline = AgentExecutionContext(
        execution_id="deadline-test",
        config=AgentExecutionConfig(
            request_timeout_seconds=1.0,
            provider_timeout_seconds=0.25,
            tool_timeout_seconds=0.1,
        ),
    )

    result = base.call_agent(
        system="s",
        user="u",
        tools=(),
        deadline=deadline,
    )

    assert result.content == "done"
    assert seen_timeouts == [0.25]


def test_tool_result_past_its_deadline_is_not_sent_to_another_provider_round(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "0")
    provider_calls = []

    def fake_provider(**_kwargs):
        provider_calls.append("provider")
        return _reply(calls=(_call("slow-call", "slow"),))

    def slow_read_only_tool():
        import time

        time.sleep(0.02)
        return "late tool result"

    monkeypatch.setattr(base, "_invoke_agent_provider", fake_provider)
    deadline = AgentExecutionContext(
        execution_id="tool-deadline-test",
        config=AgentExecutionConfig(
            request_timeout_seconds=1.0,
            provider_timeout_seconds=0.25,
            tool_timeout_seconds=0.001,
        ),
    )

    with pytest.raises(AgentToolDeadlineExceeded):
        base.call_agent(
            system="s",
            user="u",
            tools=(_tool("slow", slow_read_only_tool),),
            deadline=deadline,
        )

    assert provider_calls == ["provider"]


def test_provider_timeout_has_a_distinct_deadline_failure_category(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setattr(
        base,
        "provider_catalog",
        lambda: {
            base.DEEPSEEK_PROVIDER: base.ProviderConfig(
                name=base.DEEPSEEK_PROVIDER,
                api_key="fake-key",
                base_url="https://fake.test",
                model="fake",
            )
        },
    )

    def timed_out_provider(**_kwargs):
        raise TimeoutError("fake provider timed out")

    monkeypatch.setattr(base, "_invoke_agent_provider", timed_out_provider)
    deadline = AgentExecutionContext(
        execution_id="provider-deadline-test",
        config=AgentExecutionConfig(
            request_timeout_seconds=1.0,
            provider_timeout_seconds=0.01,
            tool_timeout_seconds=0.1,
        ),
    )

    with pytest.raises(AgentProviderDeadlineExceeded):
        base.call_agent(system="s", user="u", tools=(), deadline=deadline)


def test_a_finished_answer_is_returned_even_when_it_went_over_budget():
    """预算上限的作用是不再继续花钱,不是销毁已经买到的答案。

    真实场景:一周的行程跑四轮,累计 token 会超过上限(每轮都重发整段上下文),
    此时模型已经给出了完整回答。旧行为把它换成一句 ERROR,调用方看到
    stopped_reason 就降级,于是用户永远看不到 agent 的真实回答。
    """
    result = base.call_agent(
        system="s",
        user="u",
        tools=(),
        mock_rounds=(_reply("here is the real answer", tokens=999),),
        max_rounds=3,
        max_total_tokens=10,
    )

    assert result.content == "here is the real answer"
    assert result.stopped_reason is None
    assert result.total_tokens == 999
