"""Agent 地基测试 —— 守的是"DeepSeek 单一路径不会把私密数据和配置混乱带进 prompt"。"""

from __future__ import annotations

from datetime import date

import pytest

from app.agents import base
from app.domain.constraints.engine import classify
from app.domain.constraints.types import (
    Constraint,
    ConstraintKind,
    Importance,
    ItemView,
    ProposedChange,
    Settledness,
)

SECRET = "化疗后早上起不来，不想让任何人知道"


def _verdict():
    item = ItemView(
        id="art",
        day_date=date(2026, 8, 15),
        start_hour=14.0,
        duration_min=120,
        price_per_person=30.0,
        settledness=Settledness.LOOSE,
    )
    change = ProposedChange(
        before=item,
        after=ItemView(
            id="art",
            day_date=date(2026, 8, 15),
            start_hour=8.0,
            duration_min=120,
            price_per_person=30.0,
        ),
        day_walk_km_after=1.4,
        trip_total_after=480.0,
        requested_by_membership_id="m-mia",
    )
    constraint = Constraint(
        id="c1",
        membership_id="m-mia",
        kind=ConstraintKind.TIME_WINDOW,
        importance=Importance.REQUIRED,
        params={"earliest_hour": 9.0},
        private_note=SECRET,
    )
    return classify(change, [constraint])


def _provider(name: str, base_url: str | None, model: str) -> base.ProviderConfig:
    return base.ProviderConfig(
        name=name,
        api_key="test-key",
        base_url=base_url,
        model=model,
    )


def test_safe_context_cannot_carry_identity_or_wording():
    """这条守的是产品承诺: 模型永远看不到是谁，也看不到原话。"""
    ctx = base.safe_context(_verdict())
    blob = ctx.as_prompt_block() + repr(ctx)

    assert "m-mia" not in blob
    assert SECRET not in blob
    assert "Mia" not in blob
    for field in ("membership_id", "user_id", "name", "private_note"):
        assert not hasattr(ctx, field)


def test_safe_context_still_says_enough_to_be_useful():
    """脱敏不能脱到没信息 —— 模型要知道被什么挡住了、影响几个人。"""
    ctx = base.safe_context(_verdict())

    assert ctx.path == "confirm"
    assert any("required" in blocked.lower() for blocked in ctx.blocked_by)
    assert ctx.affected_counts == (1,)
    assert "time window" in ctx.findings[0].lower()


def test_mock_mode_never_touches_the_network(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = base.call_model(
        system="s",
        user="u",
        schema={"type": "object"},
        schema_name="t",
        mock={"kind": "walk_limit"},
    )
    assert result == {"kind": "walk_limit"}


def test_missing_key_raises_agent_unavailable(monkeypatch):
    monkeypatch.delenv("MOCK_AI", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(base.AgentUnavailable):
        base.call_model(
            system="s",
            user="u",
            schema={"type": "object"},
            schema_name="t",
            mock={},
            provider=base.CHAT_ROUTE,
        )


def test_provider_off_without_credentials_never_invokes_external_provider(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def unexpected_provider_invocation(*_args, **_kwargs):
        pytest.fail("Provider invocation must not occur without an injected credential")

    monkeypatch.setattr(base, "_invoke_agent_provider", unexpected_provider_invocation)

    with pytest.raises(base.AgentUnavailable, match="API key is not set"):
        base.call_model(
            system="s",
            user="u",
            schema={"type": "object"},
            schema_name="t",
            mock={},
            provider=base.CHAT_ROUTE,
        )


@pytest.mark.parametrize(
    ("provider_name", "provider_url"),
    [
        (base.DEEPSEEK_PROVIDER, "https://api.deepseek.com"),
        (base.DEEPSEEK_PROVIDER, "https://dashscope-us.aliyuncs.com/compatible-mode/v1"),
        (base.DEEPSEEK_PROVIDER, "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"),
    ],
)
def test_json_object_mode_for_compatible_providers(provider_name: str, provider_url: str):
    config = _provider(provider_name, provider_url, "deepseek-v4-flash")

    assert base._response_format("agent", {"type": "object"}, config) == {
        "type": "json_object"
    }


def test_non_deepseek_urls_keep_strict_json_schema_mode():
    config = _provider("custom", "https://example.com/v1/", "custom-model")

    assert base._response_format("agent", {"type": "object"}, config)["type"] == "json_schema"


def test_all_routes_resolve_to_deepseek():
    assert base._resolve_provider_name(base.CHAT_ROUTE) == base.DEEPSEEK_PROVIDER
    assert base._resolve_provider_name(base.PLANNER_ROUTE) == base.DEEPSEEK_PROVIDER
    assert base._resolve_provider_name(base.EXPLAINER_ROUTE) == base.DEEPSEEK_PROVIDER


def test_old_route_env_values_no_longer_reroute_runtime(monkeypatch):
    monkeypatch.setenv("CHAT_AI_PROVIDER", "deepseek")
    monkeypatch.setenv("PLANNER_AI_PROVIDER", "some_old_provider")
    monkeypatch.setenv("EXPLAINER_AI_PROVIDER", "something_else")

    assert base._resolve_provider_name(base.CHAT_ROUTE) == base.DEEPSEEK_PROVIDER
    assert base._resolve_provider_name(base.PLANNER_ROUTE) == base.DEEPSEEK_PROVIDER
    assert base._resolve_provider_name(base.EXPLAINER_ROUTE) == base.DEEPSEEK_PROVIDER


def test_provider_catalog_reads_deepseek_configuration(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    catalog = base.provider_catalog()

    assert set(catalog) == {base.DEEPSEEK_PROVIDER}
    assert catalog[base.DEEPSEEK_PROVIDER].api_key == "deepseek-test"
    assert catalog[base.DEEPSEEK_PROVIDER].base_url == "https://api.deepseek.com"


def test_provider_runtime_state_reports_only_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")

    state = base.provider_runtime_state()

    assert set(state) == {base.DEEPSEEK_PROVIDER}
    assert state[base.DEEPSEEK_PROVIDER]["has_api_key"] is True
