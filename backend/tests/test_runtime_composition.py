"""PR-04C selector contracts that run in the Legacy-only environment."""

from __future__ import annotations

import importlib.util
import sys

import pytest
from sqlalchemy.orm import Session

from app.agents.legacy_runtime import LegacyChatAgentRuntime
from app.agents.runtime_contract import (
    AgentCandidateOption,
    AgentReplyOnly,
    AgentSuggestedChange,
    RuntimeFailure,
    RuntimeObservation,
    RuntimeResult,
)
from app.agents.runtime_factory import (
    ChatRuntimeConfigurationError,
    build_chat_runtime,
)
from app.agents import runtime_factory
from app.domain.chat import service as chat_service


class _RecordingRuntime:
    def __init__(self, result: RuntimeResult) -> None:
        self.result = result
        self.calls = []

    def run(self, request, capability, *, execution):  # type: ignore[no-untyped-def]
        self.calls.append((request, capability, execution))
        return self.result


def _result(
    *,
    reply: str = "I can prepare that change.",
    outcome: object | None = None,
    candidates: tuple[AgentCandidateOption, ...] = (),
    failure: RuntimeFailure | None = None,
) -> RuntimeResult:
    return RuntimeResult(
        reply=reply if failure is None else "",
        outcome=outcome or AgentReplyOnly(),
        candidate_options=candidates,
        observation=RuntimeObservation(
            trace_id="test-runtime",
            round_count=1,
            total_tokens=1,
            total_elapsed_ms=1.0,
            failure=failure,
        ),
    )


def test_missing_selector_chooses_legacy_without_importing_pydantic(monkeypatch):
    monkeypatch.delenv("CHAT_AGENT_RUNTIME", raising=False)

    runtime = build_chat_runtime(system_prompt="test prompt")

    assert isinstance(runtime, LegacyChatAgentRuntime)
    if importlib.util.find_spec("pydantic_ai") is None:
        assert "app.agents.pydantic_composition" not in sys.modules


def test_legacy_only_environment_imports_chat_service_without_pydantic_runtime():
    """The Service/factory import path must not require optional Pydantic AI."""

    if importlib.util.find_spec("pydantic_ai") is None:
        assert "app.agents.pydantic_runtime" not in sys.modules
    assert chat_service.respond_to_trip_chat is not None


def test_explicit_legacy_selector_chooses_legacy(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "legacy")

    runtime = build_chat_runtime(system_prompt="test prompt")

    assert isinstance(runtime, LegacyChatAgentRuntime)
    if importlib.util.find_spec("pydantic_ai") is None:
        assert "app.agents.pydantic_composition" not in sys.modules


def test_explicit_pydantic_selector_uses_only_injected_pydantic_composition(monkeypatch):
    selected = object()
    calls = 0

    def build_injected_runtime():
        nonlocal calls
        calls += 1
        return selected

    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")

    runtime = build_chat_runtime(
        system_prompt="test prompt",
        pydantic_runtime_factory=build_injected_runtime,
    )

    assert runtime is selected
    assert calls == 1
    if importlib.util.find_spec("pydantic_ai") is None:
        assert "app.agents.pydantic_composition" not in sys.modules


def test_empty_selector_chooses_legacy(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "")

    runtime = build_chat_runtime(system_prompt="test prompt")

    assert isinstance(runtime, LegacyChatAgentRuntime)


@pytest.mark.parametrize("selector", ["old", "pydantic_ai", "experimental", "nope"])
def test_invalid_explicit_selector_fails_closed(monkeypatch, selector: str):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", selector)

    with pytest.raises(ChatRuntimeConfigurationError):
        build_chat_runtime(system_prompt="test prompt")


def test_pydantic_without_installed_dependency_fails_closed_without_legacy_fallback(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")
    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder-pr04e-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setitem(sys.modules, "pydantic_ai", None)
    monkeypatch.setattr(
        runtime_factory,
        "LegacyChatAgentRuntime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no Legacy fallback")),
    )

    with pytest.raises(ChatRuntimeConfigurationError):
        build_chat_runtime(system_prompt="test prompt")


def test_pydantic_production_composition_exception_is_normalized_without_fallback(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")
    monkeypatch.setattr(
        runtime_factory,
        "_build_production_pydantic_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("credential=never-expose")),
    )
    monkeypatch.setattr(
        runtime_factory,
        "LegacyChatAgentRuntime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no Legacy fallback")),
    )

    with pytest.raises(ChatRuntimeConfigurationError) as error:
        build_chat_runtime(system_prompt="test prompt")

    assert str(error.value) == "Pydantic Chat Runtime configuration is unavailable"
    assert "never-expose" not in str(error.value)


def test_chat_service_uses_selected_runtime_with_one_neutral_request(
    monkeypatch, db: Session, full_trip: dict
):
    runtime = _RecordingRuntime(_result())
    build_calls = []

    def build(**kwargs):
        build_calls.append(kwargs)
        return runtime

    monkeypatch.setattr(chat_service, "build_chat_runtime", build, raising=False)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert result.reply == "I can prepare that change."
    assert len(build_calls) == 1
    assert len(runtime.calls) == 1
    request, _capability, execution = runtime.calls[0]
    assert request.selected_item_ref == full_trip["art"].id
    assert "Art Institute of Chicago" in request.message
    assert execution.request_timeout_seconds == chat_service.CHAT_AGENT_TIMEOUT_SECONDS


def test_runtime_suggested_schedule_change_is_revalidated_by_domain(
    monkeypatch, db: Session, full_trip: dict
):
    runtime = _RecordingRuntime(
        _result(
            outcome=AgentSuggestedChange(
                item_ref=full_trip["art"].id,
                safe_patch={"start_hour": 15.0},
            )
        )
    )
    monkeypatch.setattr(chat_service, "build_chat_runtime", lambda **_kwargs: runtime, raising=False)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"start_hour": 15.0}


@pytest.mark.parametrize(
    "item_ref,patch",
    [
        ("forged-item", {"start_hour": 15.0}),
        ("{selected}", {"title": "Unproven replacement"}),
    ],
)
def test_common_runtime_suggestions_cannot_bypass_service_validation(
    monkeypatch, db: Session, full_trip: dict, item_ref: str, patch: dict[str, object]
):
    item_ref = full_trip["art"].id if item_ref == "{selected}" else item_ref
    runtime = _RecordingRuntime(
        _result(outcome=AgentSuggestedChange(item_ref=item_ref, safe_patch=patch))
    )
    monkeypatch.setattr(chat_service, "build_chat_runtime", lambda **_kwargs: runtime, raising=False)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None


def test_selected_runtime_failure_degrades_without_running_another_runtime(
    monkeypatch, db: Session, full_trip: dict
):
    selected = _RecordingRuntime(
        _result(
            failure=RuntimeFailure(
                normalized_kind="provider_timeout", technical_kind="synthetic"
            )
        )
    )
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")
    monkeypatch.setattr(chat_service, "build_chat_runtime", lambda **_kwargs: selected, raising=False)
    monkeypatch.setattr(
        LegacyChatAgentRuntime,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no fallback")),
    )

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert len(selected.calls) == 1
    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()


def test_selected_legacy_failure_degrades_without_importing_or_running_pydantic(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.delenv("CHAT_AGENT_RUNTIME", raising=False)

    def failed_legacy_run(*_args, **_kwargs):
        return _result(
            failure=RuntimeFailure(
                normalized_kind="provider_timeout", technical_kind="synthetic"
            )
        )

    monkeypatch.setattr(LegacyChatAgentRuntime, "run", failed_legacy_run)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()
    if importlib.util.find_spec("pydantic_ai") is None:
        assert "app.agents.pydantic_runtime" not in sys.modules


def test_deterministic_preflight_returns_before_runtime_composition(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(
        chat_service,
        "build_chat_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("runtime must not run")),
        raising=False,
    )

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="change time",
        item_id=full_trip["art"].id,
    )

    assert "What time would you like to move it to?" in result.reply
