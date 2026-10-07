"""Pydantic-only contracts for opt-in production Runtime composition."""

from __future__ import annotations

import httpx
import pytest

from app.agents.pydantic_composition import (
    PydanticRuntimeCompositionError,
    build_pydantic_chat_runtime,
)
from app.agents.pydantic_runtime import PydanticChatAgentRuntime
from app.agents.runtime_factory import ChatRuntimeConfigurationError, build_chat_runtime


_OFFICIAL_ENDPOINT = "https://api.deepseek.com"
_PLACEHOLDER_KEY = "placeholder-pr04e-key"
_MODEL_NAME = "deepseek-v4-flash-pr04e-test"


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
    assert runtime.model.settings["thinking"] is False


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
