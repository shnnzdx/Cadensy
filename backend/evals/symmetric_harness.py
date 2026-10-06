"""Test-only symmetric Runtime harness primitives for PR-04D2.

This module intentionally runs one synthetic case at a time.  It does not
grade cases, aggregate scores, emit an A/B report, or construct a production
Provider.  The two lowerers keep their framework-native wire protocols while
sharing an oracle-independent scenario description and scoped fixture facts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Any, Literal
from unittest.mock import patch

from pydantic_ai import ModelResponse, ToolCallPart, models
from pydantic_ai.models.function import FunctionModel
from sqlalchemy.orm import Session

from app.agents import base, legacy_runtime
from app.agents.legacy_runtime import LegacyChatAgentRuntime, LegacyReadTripCapability
from app.agents.pydantic_runtime import PydanticChatAgentRuntime
from app.domain.chat import service as chat_service


ScenarioKind = Literal[
    "suggested_change",
    "provider_failure",
    "tool_failure",
    "reply_only",
    "must_not_run",
]
RuntimeName = Literal["legacy", "pydantic"]
ToolOutcome = Literal["success", "failure"]


class SyntheticProviderFailure(RuntimeError):
    """An actual fake dispatch failure, never a Golden-derived label."""


class SyntheticToolFailure(RuntimeError):
    """An actual handler failure injected only around a test-only fixture."""


class UnexpectedRuntimeExecution(AssertionError):
    """A deterministic preflight case reached a Runtime unexpectedly."""


@dataclass(frozen=True)
class NeutralScenario:
    """Oracle-independent input to the two framework-specific fake seams."""

    case_id: str
    kind: ScenarioKind
    item_key: str | None
    patch: Mapping[str, object]
    reply: str


@dataclass(frozen=True)
class ToolInvocationEvent:
    name: str
    arguments: Mapping[str, object]
    outcome: ToolOutcome


@dataclass(frozen=True)
class HarnessObservation:
    """Sanitized, ungraded facts from exactly one Runtime execution."""

    runtime: RuntimeName
    runtime_executed: bool
    provider_events: tuple[str, ...]
    attempted_tool_calls: tuple[ToolInvocationEvent, ...]
    successful_tool_calls: tuple[ToolInvocationEvent, ...]
    output_kind: str
    clarification_source: str | None
    decision_path: str | None
    safe_degraded: bool
    network_attempts: int
    worker_session_is_independent: bool


def compile_neutral_scenario(case: Mapping[str, Any]) -> NeutralScenario:
    """Compile only Dataset execution inputs, never Golden expected fields."""

    mode = case["fake_provider"]
    input_data = case["input"]
    item_key = input_data.get("item_key")
    if mode == "notice_preview":
        return NeutralScenario(
            case_id=case["id"],
            kind="suggested_change",
            item_key=item_key,
            patch={"start_hour": 15.5},
            reply="I can prepare this change; the Current Plan has not changed.",
        )
    if mode == "confirm_preview":
        return NeutralScenario(
            case_id=case["id"],
            kind="suggested_change",
            item_key=item_key,
            patch={"start_hour": 18.0},
            reply="I can prepare this change; the Current Plan has not changed.",
        )
    if mode == "raise_provider_failure":
        return NeutralScenario(case["id"], "provider_failure", item_key, {}, "")
    if mode == "tool_failure":
        return NeutralScenario(case["id"], "tool_failure", item_key, {}, "")
    if mode == "safe_privacy_reply":
        return NeutralScenario(
            case_id=case["id"],
            kind="reply_only",
            item_key=item_key,
            patch={},
            reply="I can help with the current plan, but I cannot disclose private member details.",
        )
    if mode == "must_not_run":
        return NeutralScenario(case["id"], "must_not_run", item_key, {}, "")
    raise ValueError(f"Unsupported symmetric-harness scenario: {mode!r}")


class _SanitizingToolRecorder:
    """Keep only schema-safe call arguments; never retain tool output."""

    def __init__(self, item_keys: Mapping[str, str]) -> None:
        # Fixture ownership is actual internal id -> public symbolic key.
        self._item_keys = dict(item_keys)
        self.events: list[ToolInvocationEvent] = []

    def __call__(
        self,
        name: str,
        arguments: Mapping[str, object],
        outcome: ToolOutcome,
    ) -> None:
        self.events.append(
            ToolInvocationEvent(
                name=name,
                arguments=_sanitize_tool_arguments(arguments, self._item_keys),
                outcome=outcome,
            )
        )


def _sanitize_tool_arguments(
    arguments: Mapping[str, object], item_keys: Mapping[str, str]
) -> dict[str, object]:
    """Preserve only explicitly safe contract arguments in a public shape."""

    safe: dict[str, object] = {}
    for key, value in arguments.items():
        if key in {"day", "new_start_hour", "new_duration_min"} and isinstance(
            value, (str, int, float)
        ):
            safe[key] = value
        elif key == "item_id" and isinstance(value, str):
            safe[key] = item_keys.get(value, "[redacted]")
        elif key in {"new_day_date"} and isinstance(value, str):
            safe[key] = value
        elif key in {"item_title", "conflict_description", "keywords"}:
            safe[key] = "[redacted]"
        else:
            safe[key] = "[redacted]"
    return safe


def run_single_symmetric_case(
    db: Session,
    *,
    fixture: Mapping[str, Any],
    case: Mapping[str, Any],
    runtime_name: RuntimeName,
) -> HarnessObservation:
    """Execute one synthetic case through real Application and Runtime paths.

    This is deliberately not an evaluator or scorer.  Callers must select one
    case and inspect its ungraded facts; PR-04D3 owns any formal A/B run.
    """

    scenario = compile_neutral_scenario(case)
    item_key = case["input"].get("item_key")
    membership = fixture["memberships"][case["fixture_preconditions"]["actor"]]
    recorder = _SanitizingToolRecorder(fixture["item_keys"])
    provider_events: list[str] = []
    clarification_source: str | None = None
    safe_degradation = False
    network_attempts = 0
    base_worker_session_factory = _session_factory_for(db)
    worker_sessions: list[Session] = []

    def worker_session_factory() -> Session:
        worker = base_worker_session_factory()
        worker_sessions.append(worker)
        return worker

    def capability_factory(**kwargs: object) -> LegacyReadTripCapability:
        return LegacyReadTripCapability(
            trip_id=str(kwargs["trip_id"]),
            actor_membership_id=str(kwargs["actor_membership_id"]),
            session_factory=worker_session_factory,
            tool_invocation_recorder=recorder,
        )

    def deny_http(*_args: object, **_kwargs: object) -> object:
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("A symmetric fake harness must not dispatch HTTP")

    original_degraded_reply = chat_service._degraded_reply
    original_plain_clarification = chat_service._plain_text_clarification_reply
    original_ambiguous_reply = chat_service._ambiguous_item_reference_reply
    original_missing_reply = chat_service._missing_item_reference_reply

    def record_degraded_reply(*args: object, **kwargs: object) -> chat_service.ChatResult:
        nonlocal safe_degradation
        safe_degradation = True
        return original_degraded_reply(*args, **kwargs)

    def record_plain_clarification(*args: object, **kwargs: object) -> str | None:
        nonlocal clarification_source
        reply = original_plain_clarification(*args, **kwargs)
        if reply is not None:
            clarification_source = "deterministic_missing_slot"
        return reply

    def record_ambiguous_reply(*args: object, **kwargs: object) -> str:
        nonlocal clarification_source
        clarification_source = "deterministic_ambiguous_item"
        return original_ambiguous_reply(*args, **kwargs)

    def record_missing_reply(*args: object, **kwargs: object) -> str:
        nonlocal clarification_source
        clarification_source = "deterministic_missing_item"
        return original_missing_reply(*args, **kwargs)

    with ExitStack() as stack:
        # Keep real-provider traffic impossible even if a lowerer regresses.
        import httpx

        stack.enter_context(patch.object(httpx.Client, "request", deny_http))
        stack.enter_context(patch.object(httpx.AsyncClient, "request", deny_http))
        stack.enter_context(patch.object(chat_service, "SessionLocal", worker_session_factory))
        stack.enter_context(patch.object(chat_service, "_degraded_reply", record_degraded_reply))
        stack.enter_context(
            patch.object(chat_service, "_plain_text_clarification_reply", record_plain_clarification)
        )
        stack.enter_context(
            patch.object(chat_service, "_ambiguous_item_reference_reply", record_ambiguous_reply)
        )
        stack.enter_context(
            patch.object(chat_service, "_missing_item_reference_reply", record_missing_reply)
        )
        stack.enter_context(
            patch.object(chat_service, "LegacyReadTripCapability", capability_factory)
        )
        stack.enter_context(_inject_tool_failure_if_needed(scenario))

        if runtime_name == "legacy":
            stack.enter_context(patch.object(base, "is_mocked", lambda: False))
            stack.enter_context(
                patch.object(
                    base,
                    "provider_catalog",
                    lambda: {
                        base.DEEPSEEK_PROVIDER: base.ProviderConfig(
                            name=base.DEEPSEEK_PROVIDER,
                            api_key="symmetric-fake-key",
                            base_url="https://invalid.example.test",
                            model="symmetric-fake-model",
                        )
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    base,
                    "_invoke_agent_provider",
                    _legacy_provider_script(scenario, fixture, provider_events),
                )
            )
            stack.enter_context(
                patch.object(
                    chat_service,
                    "build_chat_runtime",
                    lambda *, system_prompt: LegacyChatAgentRuntime(system_prompt=system_prompt),
                )
            )
        elif runtime_name == "pydantic":
            stack.enter_context(patch.object(models, "ALLOW_MODEL_REQUESTS", False))
            model = FunctionModel(_pydantic_model_script(scenario, fixture, provider_events))
            stack.enter_context(
                patch.object(
                    chat_service,
                    "build_chat_runtime",
                    lambda *, system_prompt: PydanticChatAgentRuntime(
                        model=model, system_prompt=system_prompt
                    ),
                )
            )
        else:  # pragma: no cover - constrained Literal, kept fail-closed at runtime.
            raise ValueError(f"Unsupported Runtime: {runtime_name!r}")

        try:
            result = chat_service.respond_to_trip_chat(
                db,
                trip_id=fixture["trip"].id,
                membership=membership,
                message=case["input"]["message"],
                item_id=fixture["items"][item_key].id if item_key else None,
            )
        except chat_service.ChatAccessDenied:
            result = None

    return _observation(
        runtime_name=runtime_name,
        result=result,
        provider_events=provider_events,
        attempted_tool_calls=recorder.events,
        clarification_source=clarification_source,
        safe_degradation=safe_degradation,
        network_attempts=network_attempts,
        worker_session_is_independent=bool(worker_sessions)
        and all(worker is not db for worker in worker_sessions),
    )


def _session_factory_for(db: Session) -> Callable[[], Session]:
    bind = db.get_bind()
    engine = bind.engine if hasattr(bind, "engine") else bind
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _inject_tool_failure_if_needed(scenario: NeutralScenario):
    if scenario.kind != "tool_failure":
        return _null_context()

    original_factory = legacy_runtime.build_read_only_trip_tools

    def failing_factory(*args: object, **kwargs: object) -> tuple[base.AgentTool, ...]:
        tools = original_factory(*args, **kwargs)
        rewritten: list[base.AgentTool] = []
        for tool in tools:
            if tool.name != "get_current_plan":
                rewritten.append(tool)
                continue

            def fail_handler(**_arguments: object) -> object:
                raise SyntheticToolFailure("synthetic read-tool failure")

            rewritten.append(replace(tool, handler=fail_handler))
        return tuple(rewritten)

    return patch.object(legacy_runtime, "build_read_only_trip_tools", failing_factory)


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> bool:
        return False


def _legacy_provider_script(
    scenario: NeutralScenario,
    fixture: Mapping[str, Any],
    events: list[str],
) -> Callable[..., base.AgentProviderReply]:
    calls = 0

    def scripted_provider(**_kwargs: object) -> base.AgentProviderReply:
        nonlocal calls
        calls += 1
        events.append("legacy_provider_dispatch")
        if scenario.kind == "must_not_run":
            raise UnexpectedRuntimeExecution(scenario.case_id)
        if scenario.kind == "provider_failure":
            events.append("provider_failure")
            raise SyntheticProviderFailure("synthetic provider failure")
        if scenario.kind == "tool_failure":
            return base.AgentProviderReply(
                content="",
                tool_calls=(base.AgentToolCall("tool-failure", "get_current_plan", {"day": "all"}),),
            )
        if scenario.kind == "suggested_change":
            if calls == 1:
                return base.AgentProviderReply(
                    content="",
                    tool_calls=(
                        base.AgentToolCall("read-plan", "get_current_plan", {"day": "all"}),
                    ),
                )
            if calls == 2:
                item = fixture["items"][scenario.item_key or ""]
                return base.AgentProviderReply(
                    content="",
                    tool_calls=(
                        base.AgentToolCall(
                            "classify-change",
                            "classify_change",
                            {
                                "item_title": item.title,
                                "item_id": item.id,
                                **_legacy_patch_arguments(scenario.patch),
                            },
                        ),
                    ),
                )
            return base.AgentProviderReply(content=scenario.reply)
        if scenario.kind == "reply_only":
            return base.AgentProviderReply(content=scenario.reply)
        raise AssertionError(f"Legacy script unexpectedly reached call {calls}")

    return scripted_provider


def _legacy_patch_arguments(patch: Mapping[str, object]) -> dict[str, object]:
    """Lower common safe-patch field names to the Legacy tool schema."""

    renamed = {
        "start_hour": "new_start_hour",
        "day_date": "new_day_date",
        "duration_min": "new_duration_min",
        "title": "new_title",
        "place": "new_place",
        "price_per_person": "new_price_per_person",
        "lat": "new_lat",
        "lng": "new_lng",
    }
    return {renamed.get(key, key): value for key, value in patch.items()}


def _pydantic_model_script(
    scenario: NeutralScenario,
    fixture: Mapping[str, Any],
    events: list[str],
) -> Callable[..., ModelResponse]:
    calls = 0

    def scripted_model(_messages: object, _info: object) -> ModelResponse:
        nonlocal calls
        calls += 1
        events.append("pydantic_function_model_dispatch")
        if scenario.kind == "must_not_run":
            raise UnexpectedRuntimeExecution(scenario.case_id)
        if scenario.kind == "provider_failure":
            events.append("provider_failure")
            raise SyntheticProviderFailure("synthetic provider failure")
        if scenario.kind == "tool_failure":
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
            raise SyntheticToolFailure("synthetic post-tool model stop")
        if scenario.kind == "suggested_change":
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
            item = fixture["items"][scenario.item_key or ""]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_suggested_change",
                        {
                            "output_kind": "suggested_change",
                            "reply": scenario.reply,
                            "item_ref": item.id,
                            "safe_patch": dict(scenario.patch),
                        },
                    )
                ]
            )
        if scenario.kind == "reply_only":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": scenario.reply},
                    )
                ]
            )
        raise AssertionError(f"Pydantic script unexpectedly reached call {calls}")

    return scripted_model


def _observation(
    *,
    runtime_name: RuntimeName,
    result: chat_service.ChatResult | None,
    provider_events: list[str],
    attempted_tool_calls: list[ToolInvocationEvent],
    clarification_source: str | None,
    safe_degradation: bool,
    network_attempts: int,
    worker_session_is_independent: bool,
) -> HarnessObservation:
    if result is None:
        return HarnessObservation(
            runtime=runtime_name,
            runtime_executed=False,
            provider_events=tuple(provider_events),
            attempted_tool_calls=tuple(attempted_tool_calls),
            successful_tool_calls=tuple(
                event for event in attempted_tool_calls if event.outcome == "success"
            ),
            output_kind="access_denied",
            clarification_source=clarification_source,
            decision_path=None,
            safe_degraded=False,
            network_attempts=network_attempts,
            worker_session_is_independent=worker_session_is_independent,
        )
    if safe_degradation:
        output_kind = "safe_degraded"
        decision_path = None
    elif result.proposed_change is not None:
        output_kind = "change_preview"
        decision_path = result.proposed_change.verdict.path
    elif clarification_source is not None:
        output_kind = "clarification"
        decision_path = None
    else:
        output_kind = "reply_only"
        decision_path = None
    return HarnessObservation(
        runtime=runtime_name,
        runtime_executed=bool(provider_events),
        provider_events=tuple(provider_events),
        attempted_tool_calls=tuple(attempted_tool_calls),
        successful_tool_calls=tuple(
            event for event in attempted_tool_calls if event.outcome == "success"
        ),
        output_kind=output_kind,
        clarification_source=clarification_source,
        decision_path=decision_path,
        safe_degraded=safe_degradation,
        network_attempts=network_attempts,
        worker_session_is_independent=worker_session_is_independent,
    )
