from __future__ import annotations

import ast
import inspect
import threading
from dataclasses import fields

import pytest
from sqlalchemy.orm import Session

from app.agents import base, chat as chat_agent, legacy_runtime
from app.agents import runtime_contract
from app.agents.execution import (
    AgentExecutionCapacityExceeded,
    AgentExecutionConfig,
    AgentRequestDeadlineExceeded,
    AgentToolDeadlineExceeded,
)
from app.agents.runtime_contract import (
    AgentClarification,
    AgentReplyOnly,
    AgentSuggestedChange,
    ReplacementCandidateEvidence,
    RuntimeHistoryTurn,
    RuntimeLimits,
    RuntimeObservation,
    RuntimeRequest,
    RuntimeResult,
    ToolExecutionEvidence,
)
from app.agents.legacy_runtime import LegacyChatAgentRuntime, LegacyReadTripCapability
from app.domain.chat import service as chat_service
from tests import runtime_contract_fake_runtime
from tests.runtime_contract_fake_runtime import FakeAlternativeRuntime


def _request(*, selected_item_ref: str | None = None) -> RuntimeRequest:
    return RuntimeRequest(
        message="Move the museum to 3 PM",
        history=(RuntimeHistoryTurn(role="user", text="Please help with Wednesday."),),
        selected_item_ref=selected_item_ref,
        request_id="runtime-contract-test",
        limits=RuntimeLimits(max_rounds=5, max_total_tokens=120000),
    )


def _execution() -> AgentExecutionConfig:
    return AgentExecutionConfig(
        request_timeout_seconds=1.0,
        provider_timeout_seconds=0.5,
        tool_timeout_seconds=0.25,
    )


class _RecordingCapability:
    def __init__(self) -> None:
        self.calls = 0

    def run_with_read_only_tools(self, operation):
        self.calls += 1
        return operation(())


def test_runtime_request_has_no_authoritative_scope_or_request_session_fields():
    field_names = {field.name for field in fields(RuntimeRequest)}

    assert field_names == {
        "message",
        "history",
        "selected_item_ref",
        "request_id",
        "limits",
    }
    assert "trip_id" not in field_names
    assert "actor_membership_id" not in field_names
    assert "session" not in field_names


def test_evidence_dtos_detach_mutable_inputs_and_reject_nested_mutation():
    class MutableText(str):
        pass

    candidate = ReplacementCandidateEvidence(
        candidate_id="trusted-cafe",
        title="Trusted Cafe",
        place="River North",
        lat=41.9,
        lng=-87.6,
        price_per_person=21.0,
    )
    fabricated = ReplacementCandidateEvidence(
        candidate_id="fabricated-cafe",
        title="Fabricated Cafe",
        place="River North",
        lat=41.9,
        lng=-87.6,
        price_per_person=21.0,
    )
    caller_candidates = [candidate]
    evidence = ToolExecutionEvidence(
        tool_name="find_replacement_place",
        status="succeeded",
        replacement_candidates=caller_candidates,
    )

    caller_candidates.append(fabricated)

    assert evidence.replacement_candidates == (candidate,)
    assert isinstance(evidence.replacement_candidates, tuple)
    with pytest.raises(AttributeError):
        evidence.replacement_candidates.append(fabricated)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        evidence.replacement_candidates[0].title = "Changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="identity and location text"):
        ReplacementCandidateEvidence(
            candidate_id=MutableText("mutable-text"),
            title="Trusted Cafe",
            place="River North",
            lat=41.9,
            lng=-87.6,
            price_per_person=21.0,
        )


def test_runtime_result_detaches_evidence_collection_and_rejects_unvalidated_entries():
    evidence = ToolExecutionEvidence(tool_name="get_current_plan", status="succeeded")
    caller_evidence = [evidence]
    result = RuntimeResult(
        reply="Scoped plan read completed.",
        outcome=AgentReplyOnly(),
        candidate_options=(),
        observation=RuntimeObservation(
            trace_id="immutable-evidence", round_count=1, total_tokens=1, total_elapsed_ms=1
        ),
        tool_execution_evidence=caller_evidence,
    )

    caller_evidence.append(ToolExecutionEvidence(tool_name="get_trip_facts", status="succeeded"))

    assert result.tool_execution_evidence == (evidence,)
    assert isinstance(result.tool_execution_evidence, tuple)
    with pytest.raises(AttributeError):
        result.tool_execution_evidence.append(evidence)  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="validated DTOs"):
        ToolExecutionEvidence(
            tool_name="find_replacement_place",
            status="succeeded",
            replacement_candidates=[object()],  # type: ignore[list-item]
        )
    with pytest.raises(ValueError, match="validated DTOs"):
        RuntimeResult(
            reply="Invalid evidence is rejected.",
            outcome=AgentReplyOnly(),
            candidate_options=(),
            observation=RuntimeObservation(
                trace_id="invalid-evidence", round_count=1, total_tokens=1, total_elapsed_ms=1
            ),
            tool_execution_evidence=[object()],  # type: ignore[list-item]
        )


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_framework_neutral_contract_has_no_framework_provider_or_orm_imports():
    imports = _imported_modules(inspect.getsource(runtime_contract))
    forbidden = {"pydantic_ai", "openai", "fastapi", "sqlalchemy"}

    assert all(
        not any(module == name or module.startswith(f"{name}.") for name in forbidden)
        for module in imports
    )
    assert "pydantic_ai" in _imported_modules("from pydantic_ai import Agent")
    assert "sqlalchemy.orm" in _imported_modules("from sqlalchemy.orm import Session")


def test_clarification_outcome_requires_explicit_user_visible_reply():
    observation = RuntimeObservation(
        trace_id="trace-clarification",
        round_count=0,
        total_tokens=0,
        total_elapsed_ms=0.0,
    )

    result = RuntimeResult(
        reply="What time should I move the museum to?",
        outcome=AgentClarification(),
        candidate_options=(),
        observation=observation,
    )

    assert result.reply == "What time should I move the museum to?"
    with pytest.raises(ValueError, match="user-visible reply"):
        RuntimeResult(
            reply="",
            outcome=AgentClarification(),
            candidate_options=(),
            observation=observation,
        )


def test_legacy_adapter_maps_reply_only_result_to_a_user_visible_reply(monkeypatch):
    capability = _RecordingCapability()
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: base.AgentRunResult(
            content="The museum is currently at 2 PM.",
            trace_id="trace-reply",
            rounds=(),
            tool_results=(),
            total_tokens=7,
            total_elapsed_ms=2.5,
        ),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), capability, execution=_execution()
    )

    assert result.reply == "The museum is currently at 2 PM."
    assert isinstance(result.outcome, AgentReplyOnly)
    assert result.candidate_options == ()
    assert result.observation.failure is None
    assert result.observation.trace_id == "trace-reply"
    assert capability.calls == 1


def test_legacy_adapter_keeps_reply_suggested_change_and_candidates_together(monkeypatch):
    capability = _RecordingCapability()
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: base.AgentRunResult(
            content="I can prepare the museum move and these alternatives.",
            trace_id="trace-combined",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "guard_rejected": False,
                    "output": {
                        "item": {"id": "item-art"},
                        "proposed_patch": {"start_hour": 15.0, "verdict": "ignored"},
                        "classification": {"path": "notice"},
                    },
                },
                {
                    "tool": "propose_options",
                    "guard_rejected": False,
                    "output": {
                        "options": [
                            {
                                "id": "shorten",
                                "label": "Shorten",
                                "title": "Shorten museum",
                                "body": "Reduce the visit.",
                                "tradeoff": "Less museum time.",
                                "item_id": "item-art",
                                "patch": {"duration_min": 90},
                            }
                        ]
                    },
                },
            ),
            total_tokens=13,
            total_elapsed_ms=4.0,
        ),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), capability, execution=_execution()
    )

    assert result.reply == "I can prepare the museum move and these alternatives."
    assert isinstance(result.outcome, AgentSuggestedChange)
    assert result.outcome.item_ref == "item-art"
    assert result.outcome.safe_patch == {"start_hour": 15.0}
    assert not hasattr(result.outcome, "verdict")
    assert len(result.candidate_options) == 1
    assert result.candidate_options[0].id == "shorten"
    assert result.candidate_options[0].safe_patch == {"duration_min": 90}


def test_legacy_adapter_detaches_only_completed_approved_tool_evidence(monkeypatch):
    capability = _RecordingCapability()
    replacement = {
        "candidate_id": "place-river-north",
        "title": "New lunch",
        "place": "River North",
        "lat": 41.9,
        "lng": -87.6,
        "price_per_person": 21.0,
    }
    invalid_replacement = {**replacement, "candidate_id": "invalid-price", "price_per_person": -1.0}
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: base.AgentRunResult(
            content="I found a scoped alternative.",
            trace_id="trace-evidence",
            rounds=(),
            tool_results=(
                {
                    "tool": "find_replacement_place",
                    "guard_rejected": False,
                    "cached": False,
                    "output": {"candidates": [replacement, invalid_replacement]},
                },
                {
                    "tool": "find_replacement_place",
                    "guard_rejected": False,
                    "cached": True,
                    "output": {"candidates": [replacement]},
                },
                {
                    "tool": "find_replacement_place",
                    "guard_rejected": True,
                    "cached": False,
                    "output": {"candidates": [replacement]},
                },
                {"tool": "model_invented_tool", "guard_rejected": False, "output": {}},
            ),
            total_tokens=13,
            total_elapsed_ms=4.0,
        ),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), capability, execution=_execution()
    )

    assert [entry.status for entry in result.tool_execution_evidence] == [
        "succeeded",
        "cache_hit",
        "guard_rejected",
        "unsupported",
    ]
    assert [entry.tool_name for entry in result.tool_execution_evidence] == [
        "find_replacement_place",
        "find_replacement_place",
        "find_replacement_place",
        "unsupported",
    ]
    assert result.tool_execution_evidence[0].replacement_candidates[0].candidate_id == "place-river-north"
    assert len(result.tool_execution_evidence[0].replacement_candidates) == 1
    assert result.tool_execution_evidence[1].replacement_candidates[0].title == "New lunch"
    assert result.tool_execution_evidence[2].replacement_candidates == ()
    assert result.tool_execution_evidence[3].replacement_candidates == ()


def test_legacy_adapter_marks_deadline_without_returning_usable_tool_evidence(monkeypatch):
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: (_ for _ in ()).throw(AgentToolDeadlineExceeded("late")),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), _RecordingCapability(), execution=_execution()
    )

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "tool_timeout"
    assert [(entry.tool_name, entry.status, entry.replacement_candidates) for entry in result.tool_execution_evidence] == [
        ("agent_execution", "timed_out", ()),
    ]


def test_legacy_adapter_marks_unclassified_execution_failure_without_tool_facts(monkeypatch):
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic handler failure")),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), _RecordingCapability(), execution=_execution()
    )

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "unexpected_exception"
    assert [(entry.tool_name, entry.status, entry.replacement_candidates) for entry in result.tool_execution_evidence] == [
        ("agent_execution", "failed", ()),
    ]


def test_legacy_adapter_maps_stopped_result_to_redacted_failure_without_reply(monkeypatch):
    capability = _RecordingCapability()
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: base.AgentRunResult(
            content="ERROR: Agent exceeded the 5-round tool loop limit.",
            trace_id="trace-stopped",
            rounds=(),
            tool_results=(),
            total_tokens=11,
            total_elapsed_ms=3.0,
            stopped_reason="round_limit_exceeded",
        ),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), capability, execution=_execution()
    )

    assert result.reply == ""
    assert isinstance(result.outcome, AgentReplyOnly)
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "usage_limit"
    assert result.observation.failure.technical_kind == "round_limit_exceeded"
    assert "ERROR" not in result.observation.safe_detail


def test_legacy_read_capability_is_the_only_scope_source_and_closes_its_session(monkeypatch):
    seen: dict[str, object] = {}

    class TrackedSession:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

    worker_session = TrackedSession()
    monkeypatch.setattr(
        "app.agents.legacy_runtime.build_read_only_trip_tools",
        lambda session, *, trip_id, actor_membership_id: (
            seen.update(
                session=session,
                trip_id=trip_id,
                actor_membership_id=actor_membership_id,
            )
            or ()
        ),
    )
    capability = LegacyReadTripCapability(
        trip_id="trip-authorized",
        actor_membership_id="member-authorized",
        session_factory=lambda: worker_session,
    )

    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: base.AgentRunResult(
            content="Scoped reply.",
            trace_id="scope-trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        ),
    )
    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(selected_item_ref="trip-forged"), capability, execution=_execution()
    )

    assert result.reply == "Scoped reply."
    assert seen["session"] is worker_session
    assert seen["trip_id"] == "trip-authorized"
    assert seen["actor_membership_id"] == "member-authorized"
    assert worker_session.closed is True


def test_alternative_runtime_can_invoke_scoped_tool_without_legacy_agenttool_import(
    monkeypatch,
):
    class TrackedSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    legacy_tool = base.AgentTool(
        name="get_trip_facts",
        description="Read only facts",
        parameters={"type": "object"},
        handler=lambda: {"destination": "Synthetic City"},
    )
    monkeypatch.setattr(
        "app.agents.legacy_runtime.build_read_only_trip_tools",
        lambda *_args, **_kwargs: (legacy_tool,),
    )
    capability = LegacyReadTripCapability(
        trip_id="trip-authorized",
        actor_membership_id="member-authorized",
        session_factory=TrackedSession,
    )

    result = FakeAlternativeRuntime().read_first_tool(capability)

    assert result == {"destination": "Synthetic City"}
    assert "app.agents.base" not in _imported_modules(
        inspect.getsource(runtime_contract_fake_runtime)
    )


def test_deadline_expiring_during_tool_construction_prevents_provider_execution(monkeypatch):
    construction_started = threading.Event()
    release_construction = threading.Event()
    construction_finished = threading.Event()
    provider_called = threading.Event()

    class BlockingCapability:
        def run_with_read_only_tools(self, operation):
            construction_started.set()
            try:
                assert release_construction.wait(timeout=0.5)
                return operation(())
            finally:
                construction_finished.set()

    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: provider_called.set(),
    )
    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(),
        BlockingCapability(),
        execution=AgentExecutionConfig(
            request_timeout_seconds=0.01,
            provider_timeout_seconds=0.01,
            tool_timeout_seconds=0.01,
        ),
    )

    assert construction_started.is_set()
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "request_timeout"
    assert provider_called.is_set() is False
    release_construction.set()
    assert construction_finished.wait(timeout=0.5)
    assert provider_called.is_set() is False


def test_inactive_deadline_before_capability_construction_skips_tools_and_provider(
    monkeypatch,
):
    """An expired request must not open the scoped capability/session at all."""

    capability_entered = threading.Event()
    provider_called = threading.Event()

    class InactiveDeadline:
        def ensure_request_active(self):
            raise AgentRequestDeadlineExceeded("synthetic inactive request")

    class RecordingCapability:
        def run_with_read_only_tools(self, operation):
            capability_entered.set()
            return operation(())

    def run_with_inactive_deadline(*, worker, **_kwargs):
        return worker(InactiveDeadline())

    monkeypatch.setattr(
        legacy_runtime,
        "run_agent_with_deadline",
        run_with_inactive_deadline,
    )
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **_kwargs: provider_called.set(),
    )

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), RecordingCapability(), execution=_execution()
    )

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "request_timeout"
    assert capability_entered.is_set() is False
    assert provider_called.is_set() is False


def test_capacity_exhaustion_has_its_own_runtime_capacity_failure(monkeypatch):
    def exhausted(**_kwargs):
        raise AgentExecutionCapacityExceeded("synthetic capacity exhaustion")

    monkeypatch.setattr(legacy_runtime, "run_agent_with_deadline", exhausted)

    result = LegacyChatAgentRuntime(system_prompt="test system").run(
        _request(), _RecordingCapability(), execution=_execution()
    )

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "runtime_capacity"
    assert result.observation.failure.technical_kind == "AgentExecutionCapacityExceeded"


def test_chat_service_uses_the_legacy_adapter_by_default(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.delenv("CHAT_AGENT_RUNTIME", raising=False)
    observed_requests = []

    def legacy_run(_self, request, _capability, *, execution):
        observed_requests.append((request, execution))
        return RuntimeResult(
            reply="I can prepare that change.",
            outcome=AgentReplyOnly(),
            candidate_options=(),
            observation=RuntimeObservation(
                trace_id="legacy-default",
                round_count=1,
                total_tokens=0,
                total_elapsed_ms=1.0,
            ),
        )

    monkeypatch.setattr(LegacyChatAgentRuntime, "run", legacy_run)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
        history=(chat_agent.HistoryTurn(role="user", text="Please help."),),
    )

    assert result.reply == "I can prepare that change."
    assert len(observed_requests) == 1
    assert observed_requests[0][0].selected_item_ref == full_trip["art"].id
