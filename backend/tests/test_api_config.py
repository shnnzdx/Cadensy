import pytest

from app.api.main import (
    DEFAULT_CORS_ORIGINS,
    _durable_conversation_runtime_enabled,
    _validate_auth_configuration,
    parse_cors_origins,
)


def test_parse_cors_origins_uses_local_defaults():
    assert parse_cors_origins("") == list(DEFAULT_CORS_ORIGINS)


def test_parse_cors_origins_splits_commas_and_trims_slashes():
    assert parse_cors_origins(" https://app.example.com/,http://localhost:3000 ") == [
        "https://app.example.com",
        "http://localhost:3000",
    ]


def test_production_fails_fast_when_retired_membership_header_flag_is_truthy(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "1")

    with pytest.raises(RuntimeError, match="retired"):
        _validate_auth_configuration()


def _ready_legacy_runtime(monkeypatch: pytest.MonkeyPatch, *, environment: str) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "legacy")
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setenv("CADENSY_E2E_FAKE_RUNTIME", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-readiness-placeholder")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _ready_legacy_mock_runtime(
    monkeypatch: pytest.MonkeyPatch, *, environment: str
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "legacy")
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setenv("CADENSY_E2E_FAKE_RUNTIME", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)


def test_non_production_runtime_requires_base_gate_and_local_readiness(monkeypatch):
    _ready_legacy_runtime(monkeypatch, environment="test")
    assert _durable_conversation_runtime_enabled() is True

    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "0")
    assert _durable_conversation_runtime_enabled() is False


def test_non_production_legacy_mock_runtime_requires_the_explicit_base_gate(monkeypatch):
    _ready_legacy_mock_runtime(monkeypatch, environment="test")
    assert _durable_conversation_runtime_enabled() is True

    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "0")
    assert _durable_conversation_runtime_enabled() is False


def test_non_production_mock_mode_does_not_enable_pydantic_or_fallback(monkeypatch):
    _ready_legacy_mock_runtime(monkeypatch, environment="test")
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")

    assert _durable_conversation_runtime_enabled() is False


def test_explicit_fake_browser_runtime_is_ready_only_in_mocked_test_process(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("CADENSY_E2E_FAKE_RUNTIME", "1")
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert _durable_conversation_runtime_enabled() is True

    monkeypatch.setenv("MOCK_AI", "0")
    assert _durable_conversation_runtime_enabled() is False
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setenv("APP_ENV", "development")
    assert _durable_conversation_runtime_enabled() is False
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED", "1")
    monkeypatch.setenv("CONVERSATION_SUBMISSION_HMAC_KEY", "offline-hmac-placeholder")
    assert _durable_conversation_runtime_enabled() is False


def test_production_runtime_requires_both_permissions_and_hmac(monkeypatch):
    _ready_legacy_runtime(monkeypatch, environment="production")
    monkeypatch.setenv("CONVERSATION_SUBMISSION_HMAC_KEY", "offline-hmac-placeholder")
    assert _durable_conversation_runtime_enabled() is False

    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED", "1")
    assert _durable_conversation_runtime_enabled() is True

    monkeypatch.delenv("CONVERSATION_SUBMISSION_HMAC_KEY", raising=False)
    assert _durable_conversation_runtime_enabled() is False


def test_production_mock_runtime_is_refused_even_when_both_gates_are_enabled(monkeypatch):
    _ready_legacy_mock_runtime(monkeypatch, environment="production")
    monkeypatch.setenv("CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED", "1")
    monkeypatch.setenv("CONVERSATION_SUBMISSION_HMAC_KEY", "offline-hmac-placeholder")

    assert _durable_conversation_runtime_enabled() is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CHAT_AGENT_RUNTIME", "invalid"),
        ("DEEPSEEK_API_KEY", ""),
    ],
)
def test_runtime_readiness_fails_closed_for_unsafe_or_incomplete_config(
    monkeypatch, name, value
):
    _ready_legacy_runtime(monkeypatch, environment="test")
    monkeypatch.setenv(name, value)
    assert _durable_conversation_runtime_enabled() is False
