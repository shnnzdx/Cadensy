"""PR-01C public Chat-service failure-injection contracts."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import base, legacy_runtime
from app.agents import execution
from app.agents.execution import AgentExecutionCapacityExceeded
from app.db.models import ChangeProposal, DecisionRound, PlanItem, Trip, TripMembership, Vote
from app.domain.chat import service as chat_service


@dataclass
class _TrackedWorkerSession:
    entered: bool = False
    closed: bool = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.closed = True


def _agent_result(content: str = "I checked the plan.") -> base.AgentRunResult:
    return base.AgentRunResult(
        content=content,
        trace_id="fake-agent",
        rounds=(),
        tool_results=(),
        total_tokens=0,
        total_elapsed_ms=1.0,
    )


def test_chat_agent_tools_are_built_and_closed_inside_an_independent_worker_session(
    monkeypatch, db: Session, full_trip: dict
):
    worker_sessions: list[_TrackedWorkerSession] = []
    worker_tool_sessions = []

    def worker_session_factory():
        session = _TrackedWorkerSession()
        worker_sessions.append(session)
        return session

    def build_tools(worker_session, *, trip_id, actor_membership_id):
        assert worker_session is not db
        assert trip_id == full_trip["trip"].id
        assert actor_membership_id == full_trip["me"].id
        worker_tool_sessions.append(worker_session)
        return ()

    def fake_call_agent(**kwargs):
        assert kwargs["tools"] == ()
        return _agent_result()

    monkeypatch.setattr(chat_service, "SessionLocal", worker_session_factory, raising=False)
    monkeypatch.setattr(legacy_runtime, "build_read_only_trip_tools", build_tools)
    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三排得太满了，能不能松一点",
    )

    assert result.reply == "I checked the plan."
    assert worker_tool_sessions == worker_sessions
    assert len(worker_sessions) == 1
    assert worker_sessions[0].entered is True
    assert worker_sessions[0].closed is True


def test_chat_agent_actual_session_factory_uses_a_separate_connection_and_closes_it(
    monkeypatch, db: Session, full_trip: dict
):
    """The ownership contract also holds for the real disposable-db factory."""
    original_factory = chat_service.SessionLocal
    worker_sessions: list[Session] = []
    closed = threading.Event()

    def tracked_actual_session_factory():
        worker_session = original_factory()
        original_close = worker_session.close

        def close():
            closed.set()
            original_close()

        monkeypatch.setattr(worker_session, "close", close)
        worker_sessions.append(worker_session)
        return worker_session

    def build_tools(worker_session, **_kwargs):
        assert isinstance(worker_session, Session)
        assert worker_session is not db
        assert worker_session.connection() is not db.connection()
        return ()

    def fake_call_agent(**kwargs):
        assert kwargs["tools"] == ()
        return _agent_result()

    monkeypatch.setattr(chat_service, "SessionLocal", tracked_actual_session_factory)
    monkeypatch.setattr(legacy_runtime, "build_read_only_trip_tools", build_tools)
    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三排得太满了，能不能松一点",
    )

    assert result.reply == "I checked the plan."
    assert len(worker_sessions) == 1
    assert closed.is_set()


def _wait_until(predicate, *, timeout: float = 0.5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _durable_counts(db: Session) -> dict[str, int]:
    return {
        "plan_items": len(db.scalars(select(PlanItem)).all()),
        "decision_rounds": len(db.scalars(select(DecisionRound)).all()),
        "proposals": len(db.scalars(select(ChangeProposal)).all()),
        "votes": len(db.scalars(select(Vote)).all()),
    }


def test_tool_finishing_after_request_deadline_is_discarded_and_closes_worker_session(
    monkeypatch, db: Session, full_trip: dict
):
    tool_started = threading.Event()
    release_tool = threading.Event()
    tool_finished = threading.Event()
    worker_sessions: list[_TrackedWorkerSession] = []
    lifecycle_events = []
    provider_calls = []

    def worker_session_factory():
        session = _TrackedWorkerSession()
        worker_sessions.append(session)
        return session

    def blocking_read_only_tool():
        tool_started.set()
        release_tool.wait(timeout=1)
        tool_finished.set()
        return {"safe": "late tool output"}

    def build_tools(worker_session, **_kwargs):
        assert worker_session is not db
        return (
            base.AgentTool(
                name="slow_read",
                description="test-only read tool",
                parameters={"type": "object", "properties": {}},
                handler=blocking_read_only_tool,
            ),
        )

    def fake_provider(*, timeout_seconds, **_kwargs):
        provider_calls.append(timeout_seconds)
        return base.AgentProviderReply(
            content="",
            tool_calls=(base.AgentToolCall("slow-call", "slow_read", {}),),
        )

    monkeypatch.setattr(chat_service, "SessionLocal", worker_session_factory)
    monkeypatch.setattr(legacy_runtime, "build_read_only_trip_tools", build_tools)
    monkeypatch.setattr(chat_service, "CHAT_AGENT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(chat_service, "CHAT_AGENT_TOOL_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(base, "is_mocked", lambda: False)
    monkeypatch.setattr(base, "_invoke_agent_provider", fake_provider)
    monkeypatch.setattr(execution.trace, "record_agent_lifecycle", lambda **event: lifecycle_events.append(event))
    before = _durable_counts(db)

    try:
        result = chat_service.respond_to_trip_chat(
            db,
            trip_id=full_trip["trip"].id,
            membership=full_trip["me"],
            message="周三排得太满了，能不能松一点",
        )

        assert tool_started.is_set()
        assert result.proposed_change is None
        assert "could not check that reliably" in result.reply.lower()
        assert _durable_counts(db) == before
    finally:
        release_tool.set()

    assert _wait_until(tool_finished.is_set)
    assert _wait_until(lambda: worker_sessions and worker_sessions[0].closed)
    assert len(provider_calls) == 1
    assert [event["phase"] for event in lifecycle_events] == [
        "request_timeout",
        "late_completion",
    ]
    assert lifecycle_events[-1]["result_consumed"] is False
    assert set(lifecycle_events[-1]) == {
        "phase",
        "execution_id",
        "elapsed_ms",
        "result_consumed",
        "worker_cancel_requested",
    }


def test_cross_trip_chat_is_rejected_before_any_agent_worker_or_tool_is_started(
    monkeypatch, db: Session, full_trip: dict
):
    foreign_trip = Trip(
        name="Foreign Trip",
        destination="Elsewhere",
        created_by_user_id=full_trip["members"][1].user_id,
    )
    db.add(foreign_trip)
    db.flush()
    foreign_membership = TripMembership(
        trip_id=foreign_trip.id,
        user_id=full_trip["members"][1].user_id,
        role="participant",
        status="joined",
    )
    db.add(foreign_membership)
    db.flush()
    calls = []
    monkeypatch.setattr(base, "call_agent", lambda **_kwargs: calls.append("agent"))

    try:
        chat_service.respond_to_trip_chat(
            db,
            trip_id=full_trip["trip"].id,
            membership=foreign_membership,
            message="Can you inspect this plan?",
        )
    except chat_service.ChatAccessDenied:
        pass
    else:
        raise AssertionError("Cross-trip chat must be denied before Agent execution")

    assert calls == []


def test_worker_capacity_exhaustion_returns_the_same_safe_degraded_chat_response(
    monkeypatch, db: Session, full_trip: dict
):
    before = _durable_counts(db)

    def exhausted(**_kwargs):
        raise AgentExecutionCapacityExceeded("synthetic capacity exhaustion")

    monkeypatch.setattr(legacy_runtime, "run_agent_with_deadline", exhausted)
    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()
    assert _durable_counts(db) == before


def test_non_cooperative_provider_result_after_http_deadline_is_not_returned_or_applied(
    monkeypatch, db: Session, full_trip: dict
):
    provider_started = threading.Event()
    release_provider = threading.Event()
    provider_finished = threading.Event()
    worker_sessions: list[_TrackedWorkerSession] = []
    lifecycle_events = []
    provider_timeouts = []

    def worker_session_factory():
        session = _TrackedWorkerSession()
        worker_sessions.append(session)
        return session

    def non_cooperative_provider(*, timeout_seconds, **_kwargs):
        provider_timeouts.append(timeout_seconds)
        provider_started.set()
        release_provider.wait(timeout=1)
        provider_finished.set()
        return base.AgentProviderReply(
            content="This late reply must not reach the user.",
        )

    monkeypatch.setattr(chat_service, "SessionLocal", worker_session_factory)
    monkeypatch.setattr(legacy_runtime, "build_read_only_trip_tools", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(chat_service, "CHAT_AGENT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(base, "is_mocked", lambda: False)
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
    monkeypatch.setattr(base, "_invoke_agent_provider", non_cooperative_provider)
    monkeypatch.setattr(execution.trace, "record_agent_lifecycle", lambda **event: lifecycle_events.append(event))
    before = _durable_counts(db)

    try:
        result = chat_service.respond_to_trip_chat(
            db,
            trip_id=full_trip["trip"].id,
            membership=full_trip["me"],
            message="Move this to 3 PM",
            item_id=full_trip["art"].id,
        )

        assert provider_started.is_set()
        assert result.proposed_change is None
        assert "could not check that reliably" in result.reply.lower()
        assert _durable_counts(db) == before
    finally:
        release_provider.set()

    assert _wait_until(provider_finished.is_set)
    assert _wait_until(lambda: worker_sessions and worker_sessions[0].closed)
    assert provider_timeouts and provider_timeouts[0] <= 0.01
    assert [event["phase"] for event in lifecycle_events] == [
        "request_timeout",
        "late_completion",
    ]
    assert lifecycle_events[-1]["result_consumed"] is False
