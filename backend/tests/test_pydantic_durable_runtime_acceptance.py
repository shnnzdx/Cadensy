"""Stage 03A direct Pydantic durable-lifecycle acceptance coverage.

Every scenario enters the public Durable Conversation coordinator with a real
Pydantic ``FunctionModel`` and committed PostgreSQL setup.  The only scripted
boundary is the model/provider response; Preview, scoped reads, persistence,
and authorization remain real application code.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import threading
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

pytest.importorskip("pydantic_ai", reason="Stage 03A requires the Pydantic test dependency")

from pydantic_ai import ModelResponse, ToolCallPart, models
from pydantic_ai.models.function import FunctionModel

from app.agents.execution import AgentExecutionConfig
from app.agents.pydantic_runtime import PydanticChatAgentRuntime
from app.agents.runtime_contract import RuntimeLimits
from app.db.models import ConversationMessage, ConversationMessageOption, Plan, PlanItem, Trip, TripMembership, User
from app.domain.conversations import context as conversation_context
from app.domain.conversations import service as conversation_service
from app.domain.conversations.runtime import ConversationRuntimeCoordinator


# A FunctionModel is a local deterministic fake. Keep accidental provider
# access disabled even if a developer has credentials in a parent environment.
models.ALLOW_MODEL_REQUESTS = False


class _TracingSession(Session):
    """Test-only Session subtype that records the actual backend at transaction start."""


class _ConnectionTrace:
    """Associate physical PostgreSQL backends with real acceptance-test phases."""

    def __init__(self) -> None:
        self.mutation_committed = threading.Event()
        self._phase = threading.local()
        self._lock = threading.Lock()
        self.records: list[tuple[str, int, int]] = []

    @contextmanager
    def phase(self, name: str):  # type: ignore[no-untyped-def]
        previous = getattr(self._phase, "name", "setup")
        self._phase.name = name
        try:
            yield
        finally:
            self._phase.name = previous

    def record(self, *, session_id: int, backend_pid: int) -> None:
        phase = getattr(self._phase, "name", "setup")
        if phase == "worker":
            phase = "worker_after_mutation" if self.mutation_committed.is_set() else "worker_before_mutation"
        with self._lock:
            self.records.append((phase, session_id, backend_pid))

    def backend_pids(self, phase: str) -> set[int]:
        with self._lock:
            return {backend_pid for recorded_phase, _session_id, backend_pid in self.records if recorded_phase == phase}


@event.listens_for(_TracingSession, "after_begin")
def _record_postgres_connection(session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
    trace = session.info.get("stage03_connection_trace")
    if trace is None:
        return
    # psycopg exposes the server-assigned backend PID without issuing SQL. This
    # must not run a query before Preview sets its read-only transaction mode.
    backend_pid = int(connection.connection.driver_connection.info.backend_pid)
    trace.record(session_id=id(session), backend_pid=backend_pid)


def _factory(test_engine):  # type: ignore[no-untyped-def]
    return sessionmaker(bind=test_engine, expire_on_commit=False, future=True)


@pytest.fixture
def independent_postgres_sessions(test_engine):  # type: ignore[no-untyped-def]
    """A NullPool factory: every acceptance Session gets a fresh physical backend."""

    engine = create_engine(test_engine.url, future=True, poolclass=NullPool)
    trace = _ConnectionTrace()
    factory = sessionmaker(
        bind=engine,
        class_=_TracingSession,
        expire_on_commit=False,
        future=True,
        info={"stage03_connection_trace": trace},
    )
    try:
        yield factory, trace
    finally:
        engine.dispose()


def _assert_independent_postgres_race(trace: _ConnectionTrace) -> None:
    """Require distinct actual backends for pre-mutation, mutation, and recheck work."""

    before = trace.backend_pids("worker_before_mutation")
    mutation = trace.backend_pids("mutator")
    after = trace.backend_pids("worker_after_mutation")
    observer = trace.backend_pids("observer")
    assert before, "Preview/context must begin before the FunctionModel barrier"
    assert mutation, "the test-controlled mutation must use a traced PostgreSQL backend"
    assert after, "Preview/Durable must begin a fresh transaction after the committed mutation"
    assert observer, "a later independent read must observe the committed mutation"
    assert before.isdisjoint(mutation)
    assert mutation.isdisjoint(after)
    assert mutation.isdisjoint(observer)
    print(
        "stage03a_postgres_backends "
        f"before={sorted(before)} mutation={sorted(mutation)} "
        f"after={sorted(after)} observer={sorted(observer)}"
    )


def _seed_committed_trip(test_engine, *, session_factory=None) -> dict[str, str | int]:  # type: ignore[no-untyped-def]
    """Create one committed owner-scoped trip for independent worker sessions."""

    factory = session_factory or _factory(test_engine)
    with factory.begin() as db:
        nonce = uuid4().hex
        owner = User(name="Stage 03 Owner", email=f"stage03-owner-{nonce}@example.test")
        other = User(name="Stage 03 Other", email=f"stage03-other-{nonce}@example.test")
        db.add_all((owner, other))
        db.flush()
        trip = Trip(name="Stage 03 trip", destination="Chicago", created_by_user_id=owner.id)
        db.add(trip)
        db.flush()
        membership = TripMembership(
            trip_id=trip.id, user_id=owner.id, role="organizer", status="joined"
        )
        other_membership = TripMembership(
            trip_id=trip.id, user_id=other.id, role="participant", status="joined"
        )
        db.add_all((membership, other_membership))
        plan = Plan(trip_id=trip.id)
        db.add(plan)
        db.flush()
        item = PlanItem(
            plan_id=plan.id,
            day_index=0,
            day_date=date(2026, 8, 15),
            start_hour=14.0,
            duration_min=120,
            title="Stage 03 museum",
            place="Michigan Avenue",
        )
        db.add(item)
        db.flush()
        return {
            "trip_id": trip.id,
            "membership_id": membership.id,
            "other_membership_id": other_membership.id,
            "plan_id": plan.id,
            "item_id": item.id,
            "plan_revision": plan.revision,
            "facts_revision": trip.planning_facts_revision,
        }


def _runtime(script):  # type: ignore[no-untyped-def]
    return PydanticChatAgentRuntime(
        model=FunctionModel(script), system_prompt="Stage 03A deterministic test prompt."
    )


def _coordinator(test_engine, runtime_factory, *, session_factory=None):  # type: ignore[no-untyped-def]
    return ConversationRuntimeCoordinator(
        session_factory=session_factory or _factory(test_engine),
        runtime_factory=runtime_factory,
        execution=AgentExecutionConfig(
            request_timeout_seconds=2.0,
            provider_timeout_seconds=1.0,
            tool_timeout_seconds=0.5,
        ),
        budget=conversation_context.ContextBudget(10_000, 100, 100),
        runtime_limits=RuntimeLimits(max_rounds=4, max_total_tokens=1_000),
    )


def _submit(
    factory, ids, *, conversation_id: str, key: str, text: str,
    selected_plan_item_id: str | None = None,
):  # type: ignore[no-untyped-def]
    with factory.begin() as db:
        return conversation_service.submit_message(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id,
            idempotency_key=key,
            text=text,
            selected_plan_item_id=selected_plan_item_id,
        )


def _identity(ids, conversation_id: str, submission):  # type: ignore[no-untyped-def]
    return conversation_context.ExecutionIdentity(
        trip_id=str(ids["trip_id"]),
        owner_membership_id=str(ids["membership_id"]),
        conversation_id=conversation_id,
        submission_id=submission.submission_id,
        attempt_number=submission.attempt_number,
        fence=submission.fence,
    )


def _conversation_option_count(db: Session, conversation_id: str) -> int:
    return db.query(ConversationMessageOption).join(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id
    ).count()


def test_pydantic_two_turn_durable_conversation_reconstructs_server_history(test_engine):
    """A1: two real Pydantic turns persist once and replay only server history."""

    ids = _seed_committed_trip(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        conversation = conversation_service.create_or_discover(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            scope="personal",
            context_key="personal",
        )
        conversation_id = conversation.id

    model_messages: list[str] = []
    calls = 0

    def scripted_model(messages, _info):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        model_messages.append(str(messages))
        if calls in {1, 3}:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_reply",
            {
                "output_kind": "reply_only",
                "reply": "First durable reply." if calls == 2 else "Second durable reply.",
            },
        )])

    coordinator = _coordinator(test_engine, lambda: _runtime(scripted_model))
    first = _submit(factory, ids, conversation_id=conversation_id, key="turn-one", text="First stored user turn.")
    first_result = coordinator.execute(identity=_identity(ids, conversation_id, first), now=conversation_service.utcnow())
    second = _submit(factory, ids, conversation_id=conversation_id, key="turn-two", text="Second stored user turn.")
    second_result = coordinator.execute(identity=_identity(ids, conversation_id, second), now=conversation_service.utcnow())

    with factory() as db:
        rows = list(db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence)
        ).all())
        plan = db.get(Plan, str(ids["plan_id"]))
        item = db.get(PlanItem, str(ids["item_id"]))

    assert first_result.state == second_result.state == "completed"
    assert [(row.role, row.content) for row in rows] == [
        ("user", "First stored user turn."),
        ("assistant", "First durable reply."),
        ("user", "Second stored user turn."),
        ("assistant", "Second durable reply."),
    ]
    assert calls == 4
    assert "First stored user turn." in model_messages[2]
    assert "First durable reply." in model_messages[2]
    assert model_messages[2].count("First stored user turn.") == 1
    assert model_messages[2].count("First durable reply.") == 1
    assert plan is not None and plan.revision == ids["plan_revision"]
    assert item is not None and item.start_hour == 14.0


@pytest.mark.parametrize("change", ["plan_revision", "planning_facts_revision"])
def test_pydantic_durable_options_are_reconstructed_then_rejected_when_stale(test_engine, change: str):
    """A2: a persisted Pydantic Option is usable now but stale after either revision changes."""

    ids = _seed_committed_trip(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        conversation = conversation_service.create_or_discover(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            scope="plan_assistant",
            context_key=f"item:{ids['item_id']}",
        )
        conversation_id = conversation.id

    calls = 0
    suggestion = {
        "item_id": str(ids["item_id"]),
        "label": "Move museum earlier",
        "title": "Move museum earlier",
        "body": "Create more afternoon space.",
        "tradeoff": "The museum starts earlier.",
        "start_hour": 12.0,
    }

    def scripted_model(_messages, _info):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart("propose_options", {
                "conflict_description": "The museum should leave more afternoon time.",
                "day": "all",
                "conflict_item_ids": [str(ids["item_id"])],
                "suggestions": [suggestion],
            })])
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {
            "output_kind": "reply_only", "reply": "Here are safe advisory options.",
        })])

    submission = _submit(
        factory,
        ids,
        conversation_id=conversation_id,
        key="durable-options",
        text="Show safe options.",
        selected_plan_item_id=str(ids["item_id"]),
    )
    result = _coordinator(test_engine, lambda: _runtime(scripted_model)).execute(
        identity=_identity(ids, conversation_id, submission), now=conversation_service.utcnow()
    )

    with factory() as db:
        history = conversation_service.history_projection(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id,
            before=None,
            limit=20,
        )
        plan = db.get(Plan, str(ids["plan_id"]))
        item = db.get(PlanItem, str(ids["item_id"]))
        assistant = next(message for message in history if message.role == "assistant")
        editable = next(option for option in assistant.options if option.safe_patch)
        resolved = conversation_service.resolve_historical_option(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id,
            assistant_message_id=assistant.id,
            option_id=editable.id,
        )

    assert result.state == "completed"
    assert calls == 3
    assert assistant.options
    assert all(option.target_plan_item_id == ids["item_id"] for option in assistant.options)
    assert any(option.safe_patch == {"start_hour": 12.0} for option in assistant.options)
    assert resolved.ready is True
    assert resolved.code == "historical_option_ready"
    assert resolved.patch == {"start_hour": 12.0}
    assert plan is not None and plan.revision == ids["plan_revision"]
    assert item is not None and item.start_hour == 14.0
    original_option_revisions = (editable.plan_revision, editable.planning_facts_revision)

    with factory.begin() as db:
        if change == "plan_revision":
            plan = db.get(Plan, str(ids["plan_id"]))
            assert plan is not None
            plan.revision += 1
        else:
            trip = db.get(Trip, str(ids["trip_id"]))
            assert trip is not None
            trip.planning_facts_revision += 1

    with factory() as db:
        stale = conversation_service.resolve_historical_option(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id,
            assistant_message_id=assistant.id,
            option_id=editable.id,
        )
        persisted_option = db.get(ConversationMessageOption, editable.id)
        plan = db.get(Plan, str(ids["plan_id"]))
        trip = db.get(Trip, str(ids["trip_id"]))
        item = db.get(PlanItem, str(ids["item_id"]))

    assert stale.ready is False
    assert stale.code == "historical_option_stale"
    assert persisted_option is not None
    assert (persisted_option.plan_revision, persisted_option.planning_facts_revision) == original_option_revisions
    assert plan is not None and plan.revision == ids["plan_revision"] + (change == "plan_revision")
    assert trip is not None and trip.planning_facts_revision == ids["facts_revision"] + (change == "planning_facts_revision")
    assert item is not None and item.start_hour == 14.0


def test_pydantic_revocation_before_context_admission_never_calls_model(test_engine):
    """A3: revoked ownership stops before Pydantic can observe conversation data."""

    ids = _seed_committed_trip(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        conversation = conversation_service.create_or_discover(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            scope="personal",
            context_key="personal",
        )
        conversation_id = conversation.id
    submission = _submit(factory, ids, conversation_id=conversation_id, key="revoked-before", text="Private turn.")

    with factory.begin() as db:
        membership = db.get(TripMembership, str(ids["membership_id"]))
        assert membership is not None
        membership.status = "removed"

    calls = 0

    def scripted_model(_messages, _info):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {
            "output_kind": "reply_only", "reply": "This must never be observed.",
        })])

    with pytest.raises(conversation_service.ConversationUnavailable):
        _coordinator(test_engine, lambda: _runtime(scripted_model)).execute(
            identity=_identity(ids, conversation_id, submission), now=conversation_service.utcnow()
        )

    with factory() as db:
        assistant_count = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "assistant",
        ).count()
        option_count = _conversation_option_count(db, conversation_id)
        plan = db.get(Plan, str(ids["plan_id"]))

    assert calls == 0
    assert assistant_count == option_count == 0
    assert plan is not None and plan.revision == ids["plan_revision"]


def test_pydantic_revocation_during_runtime_discards_late_durable_result(
    test_engine, independent_postgres_sessions
):
    """A4: an independently committed revocation defeats a blocked FunctionModel result."""

    factory, trace = independent_postgres_sessions
    ids = _seed_committed_trip(test_engine, session_factory=factory)
    with factory.begin() as db:
        conversation = conversation_service.create_or_discover(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            scope="personal",
            context_key="personal",
        )
        conversation_id = conversation.id
    submission = _submit(factory, ids, conversation_id=conversation_id, key="revoked-during", text="Private turn.")

    model_entered = threading.Event()
    release_model = threading.Event()
    result_holder: dict[str, object] = {}
    worker_errors: list[BaseException] = []

    def scripted_model(_messages, _info):  # type: ignore[no-untyped-def]
        model_entered.set()
        assert release_model.wait(timeout=5), "test coordinator did not release the FunctionModel"
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {
            "output_kind": "reply_only", "reply": "Late revoked reply.",
        })])

    def execute() -> None:
        try:
            with trace.phase("worker"):
                result_holder["result"] = _coordinator(
                    test_engine, lambda: _runtime(scripted_model), session_factory=factory
                ).execute(identity=_identity(ids, conversation_id, submission), now=conversation_service.utcnow())
        except BaseException as error:  # pragma: no cover - asserted below
            worker_errors.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert model_entered.wait(timeout=5)
        with trace.phase("mutator"):
            with factory.begin() as db:
                membership = db.get(TripMembership, str(ids["membership_id"]))
                assert membership is not None
                membership.status = "removed"
        trace.mutation_committed.set()
        release_model.set()
        worker.join(timeout=5)
    finally:
        release_model.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not worker_errors
    result = result_holder["result"]
    with trace.phase("observer"):
        with factory() as db:
            assistant_count = db.query(ConversationMessage).filter(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "assistant",
            ).count()
            option_count = _conversation_option_count(db, conversation_id)
            membership = db.get(TripMembership, str(ids["membership_id"]))
            plan = db.get(Plan, str(ids["plan_id"]))

    assert getattr(result, "state") == "discarded"
    assert getattr(result, "failure_category") == "membership_revoked"
    assert assistant_count == option_count == 0
    assert membership is not None and membership.status == "removed"
    assert plan is not None and plan.revision == ids["plan_revision"]
    _assert_independent_postgres_race(trace)


def test_pydantic_cross_trip_conversation_is_denied_before_model_execution(test_engine):
    """A5: a foreign Trip identity cannot turn another Trip's history into model input."""

    owner_ids = _seed_committed_trip(test_engine)
    foreign_ids = _seed_committed_trip(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        conversation = conversation_service.create_or_discover(
            db,
            trip_id=str(owner_ids["trip_id"]),
            membership_id=str(owner_ids["membership_id"]),
            scope="personal",
            context_key="personal",
        )
        conversation_id = conversation.id
    submission = _submit(factory, owner_ids, conversation_id=conversation_id, key="cross-trip", text="Owner-only history.")

    calls = 0

    def scripted_model(_messages, _info):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {
            "output_kind": "reply_only", "reply": "Foreign reply must not exist.",
        })])

    foreign_identity = conversation_context.ExecutionIdentity(
        trip_id=str(foreign_ids["trip_id"]),
        owner_membership_id=str(foreign_ids["membership_id"]),
        conversation_id=conversation_id,
        submission_id=submission.submission_id,
        attempt_number=submission.attempt_number,
        fence=submission.fence,
    )
    with pytest.raises(conversation_service.ConversationUnavailable):
        _coordinator(test_engine, lambda: _runtime(scripted_model)).execute(
            identity=foreign_identity, now=conversation_service.utcnow()
        )

    with factory() as db:
        assistant_count = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "assistant",
        ).count()
        owner_plan = db.get(Plan, str(owner_ids["plan_id"]))
        foreign_plan = db.get(Plan, str(foreign_ids["plan_id"]))

    assert calls == assistant_count == 0
    assert owner_plan is not None and owner_plan.revision == owner_ids["plan_revision"]
    assert foreign_plan is not None and foreign_plan.revision == foreign_ids["plan_revision"]


@pytest.mark.parametrize("change", ["plan_revision", "planning_facts_revision"])
def test_pydantic_preview_rejects_revision_changed_during_model_execution(
    test_engine, independent_postgres_sessions, change: str
):
    """A6: Preview discards a Pydantic candidate after either captured revision changes."""

    factory, trace = independent_postgres_sessions
    ids = _seed_committed_trip(test_engine, session_factory=factory)
    with factory.begin() as db:
        conversation = conversation_service.create_or_discover(
            db,
            trip_id=str(ids["trip_id"]),
            membership_id=str(ids["membership_id"]),
            scope="plan_assistant",
            context_key=f"item:{ids['item_id']}",
        )
        conversation_id = conversation.id
    submission = _submit(
        factory,
        ids,
        conversation_id=conversation_id,
        key=f"stale-{change}",
        text="Move the museum later.",
        selected_plan_item_id=str(ids["item_id"]),
    )

    model_entered = threading.Event()
    release_model = threading.Event()
    calls = 0
    result_holder: dict[str, object] = {}
    worker_errors: list[BaseException] = []

    def scripted_model(_messages, _info):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        model_entered.set()
        assert release_model.wait(timeout=5), "test coordinator did not release the FunctionModel"
        return ModelResponse(parts=[ToolCallPart("runtime_suggested_change", {
            "output_kind": "suggested_change",
            "reply": "A stale candidate.",
            "item_ref": str(ids["item_id"]),
            "safe_patch": {"start_hour": 15.0},
        })])

    def execute() -> None:
        try:
            with trace.phase("worker"):
                result_holder["result"] = _coordinator(
                    test_engine, lambda: _runtime(scripted_model), session_factory=factory
                ).execute(identity=_identity(ids, conversation_id, submission), now=conversation_service.utcnow())
        except BaseException as error:  # pragma: no cover - asserted below
            worker_errors.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert model_entered.wait(timeout=5)
        with trace.phase("mutator"):
            with factory.begin() as db:
                if change == "plan_revision":
                    plan = db.get(Plan, str(ids["plan_id"]))
                    assert plan is not None
                    plan.revision += 1
                else:
                    trip = db.get(Trip, str(ids["trip_id"]))
                    assert trip is not None
                    trip.planning_facts_revision += 1
        trace.mutation_committed.set()
        release_model.set()
        worker.join(timeout=5)
    finally:
        release_model.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not worker_errors
    result = result_holder["result"]
    with trace.phase("observer"):
        with factory() as db:
            assistant_count = db.query(ConversationMessage).filter(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "assistant",
            ).count()
            option_count = _conversation_option_count(db, conversation_id)
            plan = db.get(Plan, str(ids["plan_id"]))
            trip = db.get(Trip, str(ids["trip_id"]))
            item = db.get(PlanItem, str(ids["item_id"]))

    assert getattr(result, "state") == "failed"
    assert getattr(result, "failure_category") == "stale_context"
    assert calls == 2
    assert assistant_count == option_count == 0
    assert plan is not None and plan.revision == ids["plan_revision"] + (change == "plan_revision")
    assert trip is not None and trip.planning_facts_revision == ids["facts_revision"] + (change == "planning_facts_revision")
    assert item is not None and item.start_hour == 14.0
    _assert_independent_postgres_race(trace)
