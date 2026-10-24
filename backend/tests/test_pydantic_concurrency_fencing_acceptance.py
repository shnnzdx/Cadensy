"""Stage 03B direct Pydantic durable-conversation concurrency acceptance."""

from __future__ import annotations

from contextlib import contextmanager
import threading

import pytest

pytest.importorskip("pydantic_ai", reason="Stage 03B requires the Pydantic test dependency")

from pydantic_ai import ModelResponse, ToolCallPart

from app.agents import execution as execution_module
from app.agents.execution import AgentExecutionConfig
from app.agents.runtime_contract import RuntimeLimits
from app.db.models import (
    ConversationMessage,
    ConversationSubmission,
    ConversationSubmissionRecovery,
    Plan,
)
from app.domain.conversations import context as conversation_context
from app.domain.conversations import service
from app.domain.conversations.runtime import ConversationRuntimeCoordinator
from app.observability.telemetry import (
    AgentDeadlineExceeded,
    AgentLateCompletion,
    DeadlineScope,
    FailureCategory,
    LateCompletionKind,
    SafeTelemetryEmitter,
)

# Reuse the Stage 03A public-seam harness.  It supplies real FunctionModel,
# Preview, Coordinator, PostgreSQL setup, and no provider network access.
from tests.test_pydantic_durable_runtime_acceptance import (
    _assert_independent_postgres_race,
    _coordinator,
    _conversation_option_count,
    _factory,
    _identity,
    _runtime,
    _submit,
    committed_stage03_seed_graph,
    independent_postgres_sessions,
)


def _conversation(factory, ids: dict[str, str | int]) -> str:
    with factory.begin() as db:
        return service.create_or_discover(
            db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
            scope="personal", context_key="personal",
        ).id


def _counts(factory, conversation_id: str) -> tuple[int, int, int]:
    with factory() as db:
        user = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "user",
        ).count()
        assistant = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "assistant",
        ).count()
        return user, assistant, _conversation_option_count(db, conversation_id)


def _reply_model(
    reply: str,
    *,
    entered: threading.Event | None = None,
    release: threading.Event | None = None,
    returned: threading.Event | None = None,
):
    calls = {"count": 0}

    def scripted(_messages, _info):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if entered is not None:
            entered.set()
        if release is not None:
            assert release.wait(timeout=5), "test did not release FunctionModel"
        if returned is not None:
            returned.set()
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {"output_kind": "reply_only", "reply": reply})])

    return scripted, calls


@contextmanager
def _capture_execution_lifecycle(monkeypatch):  # type: ignore[no-untyped-def]
    """Capture existing typed lifecycle events without changing product flow."""

    events: list[object] = []
    lock = threading.Lock()
    late_worker_finished = threading.Event()
    original_emit = execution_module.emit_event_safely
    local_emitter = SafeTelemetryEmitter(sink=lambda _line: None)

    def capture(event_factory, *, emitter=None):  # type: ignore[no-untyped-def]
        event = event_factory()
        with lock:
            events.append(event)
        emitted = original_emit(lambda: event, emitter=local_emitter)
        if isinstance(event, AgentLateCompletion):
            late_worker_finished.set()
        return emitted

    monkeypatch.setattr(execution_module, "emit_event_safely", capture)
    yield events, late_worker_finished


def test_pydantic_same_key_replay_and_different_content_conflict(
    test_engine, committed_stage03_seed_graph
):
    """B1: only an execution claim runs Pydantic; same-key replay stays durable-idempotent."""

    ids, factory = committed_stage03_seed_graph.seed(), _factory(test_engine)
    conversation_id = _conversation(factory, ids)
    first = _submit(factory, ids, conversation_id=conversation_id, key="same-key", text="Safe request.")
    entered, release = threading.Event(), threading.Event()
    scripted, calls = _reply_model("One durable reply.", entered=entered, release=release)
    holder: dict[str, object] = {}
    worker_errors: list[BaseException] = []

    def run() -> None:
        try:
            holder["result"] = _coordinator(test_engine, lambda: _runtime(scripted)).execute(
                identity=_identity(ids, conversation_id, first), now=service.utcnow()
            )
        except BaseException as error:  # pragma: no cover - asserted below
            worker_errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        replay = _submit(factory, ids, conversation_id=conversation_id, key="same-key", text="Safe request.")
        assert replay.replayed and not replay.execution_claimed and replay.submission_id == first.submission_id
        with factory.begin() as db:
            with pytest.raises(service.IdempotencyConflict):
                service.submit_message(
                    db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
                    conversation_id=conversation_id, idempotency_key="same-key", text="Different canonical request.",
                    selected_plan_item_id=None,
                )
        release.set()
        worker.join(timeout=5)
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive() and not worker_errors
    assert getattr(holder["result"], "state") == "completed"
    completed = _submit(factory, ids, conversation_id=conversation_id, key="same-key", text="Safe request.")
    assert completed.replayed and not completed.execution_claimed and completed.assistant_message_id is not None
    assert calls["count"] == 1
    assert _counts(factory, conversation_id) == (1, 1, 0)
    with factory() as db:
        plan = db.get(Plan, str(ids["plan_id"]))
    assert plan is not None and plan.revision == ids["plan_revision"]


def test_pydantic_different_key_is_busy_while_first_execution_runs(
    test_engine, independent_postgres_sessions, committed_stage03_seed_graph
):
    """B2: a different key cannot create a second running durable submission."""

    factory, trace = independent_postgres_sessions
    ids = committed_stage03_seed_graph.seed(session_factory=factory)
    conversation_id = _conversation(factory, ids)
    first = _submit(factory, ids, conversation_id=conversation_id, key="first", text="First request.")
    entered, release = threading.Event(), threading.Event()
    scripted, calls = _reply_model("First reply.", entered=entered, release=release)
    holder: dict[str, object] = {}
    worker_errors: list[BaseException] = []

    def run() -> None:
        try:
            with trace.phase("worker"):
                holder["result"] = _coordinator(
                    test_engine, lambda: _runtime(scripted), session_factory=factory
                ).execute(identity=_identity(ids, conversation_id, first), now=service.utcnow())
        except BaseException as error:  # pragma: no cover - asserted below
            worker_errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        with trace.phase("competing_submit"):
            with factory.begin() as db:
                with pytest.raises(service.ConversationBusy):
                    service.submit_message(
                        db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
                        conversation_id=conversation_id, idempotency_key="second", text="Second request.",
                        selected_plan_item_id=None,
                    )
        release.set()
        worker.join(timeout=5)
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive() and not worker_errors
    assert getattr(holder["result"], "state") == "completed"
    assert calls["count"] == 1
    with trace.phase("observer"):
        counts = _counts(factory, conversation_id)
        with factory() as db:
            plan = db.get(Plan, str(ids["plan_id"]))
    assert counts == (1, 1, 0)
    assert plan is not None and plan.revision == ids["plan_revision"]
    worker_pids = trace.backend_pids("worker_before_mutation")
    competing_pids = trace.backend_pids("competing_submit")
    observer_pids = trace.backend_pids("observer")
    assert worker_pids and competing_pids and observer_pids
    assert worker_pids.isdisjoint(competing_pids)
    assert competing_pids.isdisjoint(observer_pids)
    print(
        "stage03b_busy_postgres_backends "
        f"worker={sorted(worker_pids)} competing={sorted(competing_pids)} observer={sorted(observer_pids)}"
    )


def test_expired_lease_replay_and_recovery_key_advance_authority(
    test_engine, committed_stage03_seed_graph
):
    """B3: service-owned expiry transitions preserve one submission and one user row."""

    ids, factory = committed_stage03_seed_graph.seed(), _factory(test_engine)
    conversation_id = _conversation(factory, ids)
    admitted = _submit(factory, ids, conversation_id=conversation_id, key="lease-key", text="Lease request.")
    assert admitted.execution_claimed and (admitted.attempt_number, admitted.fence) == (1, 1)
    with factory.begin() as db:
        active_replay = service.submit_message(
            db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id, idempotency_key="lease-key", text="Lease request.",
            selected_plan_item_id=None, now=service.utcnow(),
        )
    assert active_replay.replayed and not active_replay.execution_claimed
    reclaim_at = admitted.lease_expires_at
    assert reclaim_at is not None
    with factory.begin() as db:
        reclaimed = service.submit_message(
            db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id, idempotency_key="lease-key", text="Lease request.",
            selected_plan_item_id=None, now=reclaim_at,
        )
    assert reclaimed.replayed and reclaimed.execution_claimed and (reclaimed.attempt_number, reclaimed.fence) == (2, 2)
    second_expiry = reclaimed.lease_expires_at
    assert second_expiry is not None
    with factory.begin() as db:
        recovered = service.recover_submission(
            db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id, submission_id=reclaimed.submission_id, recovery_key="recovery-key",
            now=second_expiry,
        )
        replayed_recovery = service.recover_submission(
            db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
            conversation_id=conversation_id, submission_id=reclaimed.submission_id, recovery_key="recovery-key",
            now=second_expiry,
        )
    assert (recovered.attempt_number, recovered.fence, recovered.recovery_accepted_attempt_number) == (3, 3, 3)
    assert replayed_recovery.replayed and not replayed_recovery.execution_claimed
    assert replayed_recovery.recovery_accepted_fence == 3
    assert _counts(factory, conversation_id) == (1, 0, 0)
    with factory() as db:
        plan = db.get(Plan, str(ids["plan_id"]))
    assert plan is not None and plan.revision == ids["plan_revision"]


def test_concurrent_recovery_claims_grant_only_one_current_attempt(
    test_engine, independent_postgres_sessions, committed_stage03_seed_graph
):
    """B5 support: only one independently connected recovery claim wins an expired lease."""

    factory, trace = independent_postgres_sessions
    ids = committed_stage03_seed_graph.seed(session_factory=factory)
    conversation_id = _conversation(factory, ids)
    admitted = _submit(factory, ids, conversation_id=conversation_id, key="race", text="Recover once.")
    reclaim_at = admitted.lease_expires_at
    assert reclaim_at is not None
    start = threading.Barrier(2)
    outcomes: list[object] = []
    errors: list[BaseException] = []

    def claim(recovery_key: str) -> None:
        try:
            start.wait(timeout=5)
            with trace.phase(f"recovery_claimant_{recovery_key}"):
                with factory.begin() as db:
                    outcomes.append(service.recover_submission(
                        db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
                        conversation_id=conversation_id, submission_id=admitted.submission_id,
                        recovery_key=recovery_key, now=reclaim_at,
                    ))
        except service.ConversationBusy:
            outcomes.append("busy")
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    claimants = [threading.Thread(target=claim, args=(f"claim-{index}",)) for index in range(2)]
    for claimant in claimants:
        claimant.start()
    for claimant in claimants:
        claimant.join(timeout=5)
    assert all(not claimant.is_alive() for claimant in claimants)
    assert not errors
    accepted = [outcome for outcome in outcomes if outcome != "busy"]
    assert len(accepted) == 1 and outcomes.count("busy") == 1
    winner = accepted[0]
    assert getattr(winner, "execution_claimed") and (getattr(winner, "attempt_number"), getattr(winner, "fence")) == (2, 2)
    with trace.phase("observer"):
        with factory() as db:
            persisted = db.get(ConversationSubmission, admitted.submission_id)
            recoveries = list(db.query(ConversationSubmissionRecovery).filter(
                ConversationSubmissionRecovery.submission_id == admitted.submission_id,
            ).all())
        counts = _counts(factory, conversation_id)
        with factory() as db:
            plan = db.get(Plan, str(ids["plan_id"]))
    assert persisted is not None
    assert (persisted.id, persisted.attempt_number, persisted.fence, persisted.state) == (
        admitted.submission_id, 2, 2, "running",
    )
    assert persisted.assistant_message_id is None
    assert len(recoveries) == 1
    recovery = recoveries[0]
    assert (recovery.accepted_attempt_number, recovery.accepted_fence, recovery.state) == (2, 2, "running")
    assert recovery.recovery_key in {"claim-0", "claim-1"}
    losing_key = next(key for key in {"claim-0", "claim-1"} if key != recovery.recovery_key)
    winner_pids = trace.backend_pids(f"recovery_claimant_{recovery.recovery_key}")
    loser_pids = trace.backend_pids(f"recovery_claimant_{losing_key}")
    assert len(winner_pids) == len(loser_pids) == 1
    assert winner_pids.isdisjoint(loser_pids)
    observer_pids = trace.backend_pids("observer")
    assert observer_pids and observer_pids.isdisjoint(winner_pids | loser_pids)
    print(
        "stage03b_recovery_postgres_backends "
        f"winner={sorted(winner_pids)} loser={sorted(loser_pids)} observer={sorted(observer_pids)}"
    )
    assert counts == (1, 0, 0)
    assert plan is not None and plan.revision == ids["plan_revision"]


def _run_recovery_fence_scenario(
    test_engine, independent_postgres_sessions, committed_stage03_seed_graph, *, old_model, expected_old_state: str
):  # type: ignore[no-untyped-def]
    """Run a real old Pydantic execution around an independently committed recovery."""

    factory, trace = independent_postgres_sessions
    ids = committed_stage03_seed_graph.seed(session_factory=factory)
    conversation_id = _conversation(factory, ids)
    first = _submit(factory, ids, conversation_id=conversation_id, key="recover", text="Recover safely.")
    entered, release = threading.Event(), threading.Event()
    calls = {"old": 0}
    holder: dict[str, object] = {}
    worker_errors: list[BaseException] = []

    def wrapped_old_model(messages, info):  # type: ignore[no-untyped-def]
        calls["old"] += 1
        entered.set()
        assert release.wait(timeout=5), "test did not release stale FunctionModel"
        return old_model(messages, info)

    def old_run() -> None:
        try:
            with trace.phase("worker"):
                holder["old"] = _coordinator(
                    test_engine, lambda: _runtime(wrapped_old_model), session_factory=factory
                ).execute(identity=_identity(ids, conversation_id, first), now=service.utcnow())
        except BaseException as error:  # pragma: no cover - asserted below
            worker_errors.append(error)

    worker = threading.Thread(target=old_run)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        reclaim_at = first.lease_expires_at
        assert reclaim_at is not None
        with trace.phase("mutator"):
            with factory.begin() as db:
                current = service.recover_submission(
                    db, trip_id=str(ids["trip_id"]), membership_id=str(ids["membership_id"]),
                    conversation_id=conversation_id, submission_id=first.submission_id,
                    recovery_key="new-owner", now=reclaim_at,
                )
        trace.mutation_committed.set()
        release.set()
        worker.join(timeout=5)
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive() and not worker_errors and calls["old"] == 1
    assert getattr(holder["old"], "state") == expected_old_state
    assert _counts(factory, conversation_id) == (1, 0, 0)
    new_model, new_calls = _reply_model("Recovered current reply.")
    with trace.phase("worker"):
        current_result = _coordinator(
            test_engine, lambda: _runtime(new_model), session_factory=factory
        ).execute(identity=_identity(ids, conversation_id, current), now=reclaim_at)
    with trace.phase("observer"):
        with factory() as db:
            submission = db.get(ConversationSubmission, first.submission_id)
            plan = db.get(Plan, str(ids["plan_id"]))
        counts = _counts(factory, conversation_id)
    assert current_result.state == "completed" and new_calls["count"] == 1
    assert submission is not None and (submission.attempt_number, submission.fence, submission.state) == (2, 2, "completed")
    assert counts == (1, 1, 0)
    assert plan is not None and plan.revision == ids["plan_revision"]
    _assert_independent_postgres_race(trace)


def test_stale_pydantic_completion_cannot_overwrite_recovery(
    test_engine, independent_postgres_sessions, committed_stage03_seed_graph
):
    """B4: the old real FunctionModel result is fenced before durable completion."""

    def old_success(_messages, _info):  # type: ignore[no-untyped-def]
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {
            "output_kind": "reply_only", "reply": "Stale reply.",
        })])

    _run_recovery_fence_scenario(
        test_engine, independent_postgres_sessions, committed_stage03_seed_graph,
        old_model=old_success, expected_old_state="discarded"
    )


def test_stale_pydantic_failure_cannot_overwrite_recovery(
    test_engine, independent_postgres_sessions, committed_stage03_seed_graph
):
    """B5: a resumed old Pydantic failure cannot mark the recovered attempt failed."""

    def old_failure(_messages, _info):  # type: ignore[no-untyped-def]
        raise RuntimeError("controlled stale failure")

    _run_recovery_fence_scenario(
        test_engine, independent_postgres_sessions, committed_stage03_seed_graph,
        old_model=old_failure, expected_old_state="discarded"
    )


def test_pydantic_request_timeout_discards_late_function_model_result(
    test_engine, monkeypatch, committed_stage03_seed_graph
):
    """B6: a timed-out Pydantic worker has no late durable completion."""

    ids, factory = committed_stage03_seed_graph.seed(), _factory(test_engine)
    conversation_id = _conversation(factory, ids)
    submission = _submit(factory, ids, conversation_id=conversation_id, key="timeout", text="Bounded request.")
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    scripted, calls = _reply_model("Late result.", entered=entered, release=release, returned=returned)
    coordinator = ConversationRuntimeCoordinator(
        session_factory=factory,
        runtime_factory=lambda: _runtime(scripted),
        execution=AgentExecutionConfig(
            request_timeout_seconds=0.05,
            provider_timeout_seconds=0.05,
            tool_timeout_seconds=0.05,
        ),
        budget=conversation_context.ContextBudget(10_000, 100, 100),
        runtime_limits=RuntimeLimits(max_rounds=4, max_total_tokens=1_000),
    )
    with _capture_execution_lifecycle(monkeypatch) as (events, late_worker_finished):
        try:
            result = coordinator.execute(identity=_identity(ids, conversation_id, submission), now=service.utcnow())
            assert entered.wait(timeout=1)
        finally:
            release.set()

        assert returned.wait(timeout=5), "late FunctionModel did not return after deadline release"
        assert late_worker_finished.wait(timeout=5), "late worker lifecycle did not terminate"

    assert result.state == "failed"
    assert result.failure_category == "request_timeout"
    deadline_events = [event for event in events if isinstance(event, AgentDeadlineExceeded)]
    late_events = [event for event in events if isinstance(event, AgentLateCompletion)]
    assert len(deadline_events) == len(late_events) == 1
    deadline, late = deadline_events[0], late_events[0]
    assert (deadline.deadline_scope, deadline.failure_category, deadline.result_consumed) == (
        DeadlineScope.REQUEST, FailureCategory.REQUEST_TIMEOUT, False,
    )
    assert (late.late_completion_kind, late.result_consumed) == (
        LateCompletionKind.RUNNING_LATE_COMPLETION, False,
    )
    assert deadline.context.execution_id == late.context.execution_id
    with factory() as db:
        persisted = db.get(ConversationSubmission, submission.submission_id)
        plan = db.get(Plan, str(ids["plan_id"]))
    assert persisted is not None
    assert (persisted.state, persisted.failure_category, persisted.assistant_message_id) == (
        "failed", "request_timeout", None,
    )
    assert _counts(factory, conversation_id) == (1, 0, 0)
    assert calls["count"] == 1
    assert plan is not None and plan.revision == ids["plan_revision"]
    print(
        "stage03b_timeout_lifecycle "
        "deadline=request_timeout late=running_late_completion "
        "result_consumed=false same_execution=true worker_finished=true"
    )
