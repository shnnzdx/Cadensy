"""Pydantic-only contracts for opt-in production Runtime composition."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent, models
from pydantic_ai.providers.deepseek import DeepSeekProvider

from app.agents.pydantic_composition import (
    PydanticRuntimeCompositionError,
    build_pydantic_chat_runtime,
)
from app.agents.pydantic_runtime import PydanticChatAgentRuntime
from app.agents.execution import AgentExecutionConfig
from app.agents.runtime_factory import ChatRuntimeConfigurationError, build_chat_runtime
from app.agents.runtime_contract import RuntimeLimits, RuntimeRequest


_OFFICIAL_ENDPOINT = "https://api.deepseek.com"
_PLACEHOLDER_KEY = "placeholder-pr04e-key"
_MODEL_NAME = "deepseek-v4-flash-pr04e-test"
_WIRE_MODEL_NAME = "deepseek-flash"


def _configure_valid_pydantic_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", _PLACEHOLDER_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", _OFFICIAL_ENDPOINT)
    monkeypatch.setenv("DEEPSEEK_MODEL", _MODEL_NAME)


def test_valid_configuration_constructs_actual_runtime_without_http(monkeypatch):
    _configure_valid_pydantic_runtime(monkeypatch)
    dispatched: list[object] = []

    def deny_http(*args, **kwargs):  # type: ignore[no-untyped-def]
        dispatched.append((args, kwargs))
        raise AssertionError("composition must not dispatch HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", deny_http)
    monkeypatch.setattr(httpx.Client, "send", deny_http)

    runtime = build_chat_runtime(system_prompt="test prompt")

    assert isinstance(runtime, PydanticChatAgentRuntime)
    assert runtime.system_prompt == "test prompt"
    assert dispatched == []


def test_model_name_and_thinking_policy_are_composed_explicitly(monkeypatch):
    _configure_valid_pydantic_runtime(monkeypatch)

    runtime = build_pydantic_chat_runtime(system_prompt="test prompt")

    assert runtime.model.model_name == _MODEL_NAME
    assert runtime.model.settings["openai_reasoning_effort"] == "none"
    assert "thinking" not in runtime.model.settings


@dataclass
class _ScopedCurrentPlanTool:
    name: str = "get_current_plan"
    description: str = "Read the scoped Current Plan."
    parameters: dict[str, object] = field(default_factory=lambda: {
        "type": "object",
        "additionalProperties": False,
        "properties": {"day": {"type": "string"}},
        "required": ["day"],
    })
    calls: list[dict[str, object]] = field(default_factory=list)

    def invoke(self, **arguments: object) -> dict[str, object]:
        self.calls.append(arguments)
        assert arguments == {"day": "all"}
        return {"days": [{"items": [{"id": "scoped-item", "title": "Museum"}]}]}


@dataclass
class _ScopedReadCapability:
    tool: _ScopedCurrentPlanTool = field(default_factory=_ScopedCurrentPlanTool)
    entered: bool = False

    def run_with_read_only_tools(self, operation):  # type: ignore[no-untyped-def]
        self.entered = True
        return operation((self.tool,))


def _tool_completion(
    request: httpx.Request, *, name: str, arguments: dict[str, object]
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "offline-tool-call",
            "object": "chat.completion",
            "created": 0,
            "model": _WIRE_MODEL_NAME,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "offline-call-id",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
        request=request,
    )


@pytest.mark.parametrize("output_cap", [4_096, 777])
def test_full_runtime_serializes_deepseek_compatible_required_tool_requests(
    monkeypatch, output_cap: int,
):
    """The production runtime, not a bare Agent, preserves the DeepSeek wire contract."""

    _configure_valid_pydantic_runtime(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_MODEL", _WIRE_MODEL_NAME)
    captured_payloads: list[dict[str, object]] = []
    capability = _ScopedReadCapability()

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured_payloads.append(payload)
        if len(captured_payloads) == 1:
            return _tool_completion(
                request, name="get_current_plan", arguments={"day": "all"}
            )
        if len(captured_payloads) == 2:
            return _tool_completion(
                request,
                name="runtime_reply",
                arguments={"output_kind": "reply_only", "reply": "Current plan read."},
            )
        raise AssertionError("the runtime made an unexpected provider request")

    async def exercise() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            openai_client = AsyncOpenAI(
                api_key=_PLACEHOLDER_KEY,
                base_url=_OFFICIAL_ENDPOINT,
                http_client=client,
                max_retries=0,
            )
            provider = DeepSeekProvider(openai_client=openai_client)
            monkeypatch.setattr(
                "pydantic_ai.providers.deepseek.DeepSeekProvider",
                lambda **_kwargs: provider,
            )
            runtime = build_pydantic_chat_runtime(system_prompt="test prompt")
            with models.override_allow_model_requests(True):
                result = runtime.run(
                    RuntimeRequest(
                        message="Read the Current Plan.",
                        history=(),
                        selected_item_ref="scoped-item",
                        request_id="offline-deepseek-wire-test",
                        limits=RuntimeLimits(
                            max_rounds=3,
                            max_total_tokens=1_000,
                            max_tokens=output_cap,
                            guard_reject_limit=1,
                        ),
                    ),
                    capability,
                    execution=AgentExecutionConfig(
                        request_timeout_seconds=2.0,
                        provider_timeout_seconds=1.0,
                        tool_timeout_seconds=1.0,
                    ),
                )
            return result, runtime.model.profile

    result, profile = asyncio.run(exercise())

    assert result.reply == "Current plan read."
    assert result.observation.failure is None
    assert capability.entered is True
    assert capability.tool.calls == [{"day": "all"}]
    assert len(captured_payloads) == 2
    for payload in captured_payloads:
        assert payload["model"] == _WIRE_MODEL_NAME
        assert payload["tool_choice"] == "required"
        assert payload["reasoning_effort"] == "none"
        assert payload["max_tokens"] == output_cap
        assert "thinking" not in payload
        assert "max_completion_tokens" not in payload
        assert "response_format" not in payload

    tools = captured_payloads[0]["tools"]
    assert isinstance(tools, list)
    assert {tool["function"]["name"] for tool in tools} == {
        "get_current_plan",
        "runtime_reply",
        "runtime_clarification",
        "runtime_suggested_change",
    }
    for tool in tools:
        assert tool["type"] == "function"
        assert "strict" not in tool["function"]
        assert tool["function"]["parameters"]["type"] == "object"
        assert tool["function"]["parameters"]["additionalProperties"] is False
    assert profile["openai_chat_supports_max_completion_tokens"] is False


def test_production_model_sends_deepseek_max_tokens_and_preserves_provider_profile(
    monkeypatch,
):
    _configure_valid_pydantic_runtime(monkeypatch)
    captured_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "offline-completion",
                "object": "chat.completion",
                "created": 0,
                "model": _MODEL_NAME,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Offline reply."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
            request=request,
        )

    async def exercise() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            openai_client = AsyncOpenAI(
                api_key=_PLACEHOLDER_KEY,
                base_url=_OFFICIAL_ENDPOINT,
                http_client=client,
                max_retries=0,
            )
            provider = DeepSeekProvider(openai_client=openai_client)
            monkeypatch.setattr(
                "pydantic_ai.providers.deepseek.DeepSeekProvider",
                lambda **_kwargs: provider,
            )
            runtime = build_pydantic_chat_runtime(system_prompt="test prompt")
            with models.override_allow_model_requests(True):
                result = await Agent(runtime.model).run(
                    "Return a short offline reply.",
                    model_settings={"max_tokens": 321},
                )
            return result.output, runtime.model.profile

    output, profile = asyncio.run(exercise())

    assert output == "Offline reply."
    assert len(captured_payloads) == 1
    assert captured_payloads[0]["max_tokens"] == 321
    assert captured_payloads[0]["reasoning_effort"] == "none"
    assert "thinking" not in captured_payloads[0]
    assert "max_completion_tokens" not in captured_payloads[0]
    assert profile["openai_chat_supports_max_completion_tokens"] is False
    assert profile["supports_json_object_output"] is True
    assert profile["openai_chat_thinking_field"] == "reasoning_content"


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_missing_or_blank_api_key_fails_without_exposing_a_value(monkeypatch, api_key: str | None):
    _configure_valid_pydantic_runtime(monkeypatch)
    if api_key is None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DEEPSEEK_API_KEY", api_key)

    with pytest.raises(PydanticRuntimeCompositionError) as error:
        build_pydantic_chat_runtime(system_prompt="test prompt")

    assert _PLACEHOLDER_KEY not in str(error.value)


def test_mock_ai_fails_closed_without_a_fake_runtime(monkeypatch):
    _configure_valid_pydantic_runtime(monkeypatch)
    monkeypatch.setenv("MOCK_AI", "1")

    with pytest.raises(PydanticRuntimeCompositionError) as error:
        build_pydantic_chat_runtime(system_prompt="test prompt")

    assert "MOCK_AI=0" in str(error.value)
    assert _PLACEHOLDER_KEY not in str(error.value)


def test_custom_endpoint_fails_closed_without_exposing_a_credential(monkeypatch):
    _configure_valid_pydantic_runtime(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://custom.example.test/v1")

    with pytest.raises(PydanticRuntimeCompositionError) as error:
        build_pydantic_chat_runtime(system_prompt="test prompt")

    assert _PLACEHOLDER_KEY not in str(error.value)
    assert "official DeepSeek endpoint" in str(error.value)


def test_public_factory_normalizes_configuration_errors_without_a_credential(monkeypatch):
    _configure_valid_pydantic_runtime(monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ChatRuntimeConfigurationError) as error:
        build_chat_runtime(system_prompt="test prompt")

    assert str(error.value) == "Pydantic Chat Runtime configuration is unavailable"
    assert _PLACEHOLDER_KEY not in str(error.value)
