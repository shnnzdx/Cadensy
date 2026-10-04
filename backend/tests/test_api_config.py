import pytest

from app.api.main import (
    DEFAULT_CORS_ORIGINS,
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
