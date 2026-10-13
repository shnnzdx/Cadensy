"""PostgreSQL contract tests for the frozen Trip-bound durable protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread
from typing import Callable

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import AuthSession, MutationIdempotency, Plan, Trip, TripMembership, User
from app.domain import auth as auth_service
from app.domain.transactions import (
    CommandResponse,
    CredentialProof,
    DurableCommandKernel,
    HmacSigningKey,
    IdempotencyKeyReused,
    IdempotencyRecordExpired,
    IdempotentCommand,
    InvalidCredential,
    OutcomeContract,
    PreliminaryActorScope,
    PlanTransactionKernel,
    StaticOutcomeContractRegistry,
    StaleTripPlanningFactsRevision,
    TripPlanningFactsRevisionOverflow,
    TripDurableCommandKernel,
    TripIdempotentCommand,
    TripTransactionKernel,
    UnknownHmacKeyVersion,
    UnsafeNoOpMutation,
    UnsafeDurableOutcome,
    UnsupportedOutcomeContract,
)


_OPERATION = "trip.facts.test-change.v1"
_seed_records: list[tuple[str, tuple[str, ...]]] = []


class FixtureKeyring:
    def __init__(self, active: HmacSigningKey, *verification: HmacSigningKey):
        self.active = active
        self.keys = {key.version: key for key in (active, *verification)}

    def active_key(self) -> HmacSigningKey:
        return self.active

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self.keys.get(version)


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str | None
    membership_id: str
    user_id: str
    token: str


def _factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False, future=True)


def _seed(test_engine, *, with_plan: bool) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    token = f"trip-durable-{stamp}"
    with _factory(test_engine).begin() as session:
        user = User(name="Trip durable", email=f"trip-durable-{stamp}@example.test")
        owner = User(name="Trip owner", email=f"trip-owner-{stamp}@example.test")
        session.add_all([user, owner])
        session.flush()
        trip = Trip(name="Trip durable", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip)
        session.flush()
        membership = TripMembership(
            trip_id=trip.id, user_id=user.id, role="participant", status="joined"
        )
        session.add(membership)
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=auth_service.token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        plan = Plan(trip_id=trip.id) if with_plan else None
        if plan is not None:
            session.add(plan)
        session.flush()
        _seed_records.append((trip.id, (user.id, owner.id)))
        return Seed(trip.id, plan.id if plan is not None else None, membership.id, user.id, token)


@pytest.fixture(autouse=True)
def _cleanup_seed_rows(test_engine):
    _seed_records.clear()
    yield
    records = list(_seed_records)
    _seed_records.clear()
    if not records:
        return
    trip_ids = [trip_id for trip_id, _ in records]
    user_ids = [user_id for _, ids in records for user_id in ids]
    with _factory(test_engine).begin() as session:
        session.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False)
        session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)


def _scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.membership_id,
        credential_kind="account",
        expected_user_id=seed.user_id,
    )


def _command(seed: Seed, *, key: str = "trip-durable-key", **changes: object) -> TripIdempotentCommand:
    values: dict[str, object] = {
        "operation_code": _OPERATION,
        "resource_kind": "trip_planning_facts",
        "resource_id": seed.trip_id,
        "idempotency_key": key,
        "expected_trip_facts_revision": 0,
        "command_fields": {"field": "value"},
        "fingerprint_version": 1,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
    }
    values.update(changes)
    return TripIdempotentCommand(**values)  # type: ignore[arg-type]


def _kernel(test_engine) -> TripDurableCommandKernel:
    return TripDurableCommandKernel(
        TripTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("test-v1", b"trip-durable-test-key")),
        StaticOutcomeContractRegistry((OutcomeContract(_OPERATION, 1, frozenset({"ok"})),)),
    )


def _proof(seed: Seed) -> CredentialProof:
    return CredentialProof.from_bearer_token(seed.token)


def _thread(target: Callable[[], None]) -> tuple[Thread, Queue[BaseException]]:
    errors: Queue[BaseException] = Queue()

    def run() -> None:
        try:
            target()
        except BaseException as exc:
            errors.put(exc)

    worker = Thread(target=run, daemon=True)
    worker.start()
    return worker, errors


def _join(worker: Thread, errors: Queue[BaseException]) -> None:
    worker.join(timeout=5)
    assert not worker.is_alive(), "worker did not finish"
    if not errors.empty():
        raise errors.get()


def test_zero_plan_command_increments_trip_and_persists_trip_scope_ledger(test_engine):
    seed = _seed(test_engine, with_plan=False)

    result = _kernel(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(seed),
        mutation=lambda context, _fields: (
            setattr(context.trip, "name", "Updated trip"),
            CommandResponse(201, {"ok": True}),
        )[1],
    )

    assert result.trip_planning_facts_revision == 1
    assert result.replayed is False
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        assert row is not None
        assert row.aggregate_scope == "trip_planning_facts"
        assert row.resulting_plan_revision is None
        assert row.resulting_trip_planning_facts_revision == 1


def test_one_plan_command_increments_both_revisions_atomically(test_engine):
    seed = _seed(test_engine, with_plan=True)

    result = _kernel(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(seed),
        mutation=lambda context, _fields: (
            setattr(context.trip, "name", "Updated trip"),
            CommandResponse(200, {"ok": True}),
        )[1],
    )

    assert result.trip_planning_facts_revision == 1
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        assert session.get(Plan, seed.plan_id).revision == 1


def test_identical_retry_replays_without_a_second_callback_or_revision_change(test_engine):
    seed = _seed(test_engine, with_plan=False)
    kernel = _kernel(test_engine)
    calls = 0

    def mutation(context, _fields):
        nonlocal calls
        calls += 1
        context.trip.name = "Original result"
        return CommandResponse(201, {"ok": True})

    first = kernel.execute(scope=_scope(seed), credential=_proof(seed), command=_command(seed), mutation=mutation)
    replay = kernel.execute(scope=_scope(seed), credential=_proof(seed), command=_command(seed), mutation=mutation)

    assert calls == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.trip_planning_facts_revision == 1


def test_new_key_with_stale_trip_revision_fails_before_callback_or_ledger(test_engine):
    seed = _seed(test_engine, with_plan=False)
    with _factory(test_engine).begin() as session:
        session.get(Trip, seed.trip_id).planning_facts_revision = 1

    with pytest.raises(StaleTripPlanningFactsRevision):
        _kernel(test_engine).execute(
            scope=_scope(seed), credential=_proof(seed),
            command=_command(seed, key="stale-trip-key"),
            mutation=lambda _context, _fields: pytest.fail("stale callback ran"),
        )
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_changed_payload_under_same_key_is_a_conflict(test_engine):
    seed = _seed(test_engine, with_plan=False)
    kernel = _kernel(test_engine)
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=_command(seed),
        mutation=lambda context, _fields: (setattr(context.trip, "name", "first"), CommandResponse(200, {"ok": True}))[1],
    )
    with pytest.raises(IdempotencyKeyReused):
        kernel.execute(
            scope=_scope(seed), credential=_proof(seed),
            command=_command(seed, command_fields={"field": "other"}),
            mutation=lambda _context, _fields: pytest.fail("conflicting callback ran"),
        )


def test_genuine_noop_persists_replayable_outcome_without_revision_change(test_engine):
    seed = _seed(test_engine, with_plan=True)
    kernel = _kernel(test_engine)
    command = _command(seed)
    first = kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda _context, _fields: CommandResponse(200, {"ok": True}, state_changed=False),
    )
    replay = kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda _context, _fields: pytest.fail("no-op replay callback ran"),
    )
    assert (first.trip_planning_facts_revision, replay.replayed) == (0, True)
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 1


def test_false_noop_with_orm_write_fails_closed_and_rolls_back(test_engine):
    seed = _seed(test_engine, with_plan=False)
    with pytest.raises(UnsafeNoOpMutation):
        _kernel(test_engine).execute(
            scope=_scope(seed), credential=_proof(seed), command=_command(seed),
            mutation=lambda context, _fields: (
                setattr(context.trip, "name", "must roll back"),
                CommandResponse(200, {"ok": True}, state_changed=False),
            )[1],
        )
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).name == "Trip durable"
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_false_noop_with_commented_and_cte_dml_fails_closed(test_engine):
    seed = _seed(test_engine, with_plan=False)
    for key, statement in (
        ("commented-dml", "/* comment */ UPDATE trip SET name = 'unsafe' WHERE id = :trip_id"),
        ("cte-dml", "WITH changed AS (UPDATE trip SET name = 'unsafe' WHERE id = :trip_id RETURNING id) SELECT * FROM changed"),
    ):
        with pytest.raises(UnsafeNoOpMutation):
            _kernel(test_engine)._execute_with_session_mutation(
                scope=_scope(seed), credential=_proof(seed), command=_command(seed, key=key),
                mutation=lambda session, context, _fields, sql=statement: (
                    session.execute(text(sql), {"trip_id": context.trip_id}),
                    CommandResponse(200, {"ok": True}, state_changed=False),
                )[1],
            )
        with _factory(test_engine)() as session:
            assert session.get(Trip, seed.trip_id).name == "Trip durable"


def test_overflow_of_either_revision_rolls_back_callback_and_ledger(test_engine):
    maximum = 9_223_372_036_854_775_807
    for with_plan, attr in ((False, "trip"), (True, "plan")):
        seed = _seed(test_engine, with_plan=with_plan)
        with _factory(test_engine).begin() as session:
            if attr == "trip":
                session.get(Trip, seed.trip_id).planning_facts_revision = maximum
            else:
                session.get(Plan, seed.plan_id).revision = maximum
        expected = maximum if attr == "trip" else 0
        with pytest.raises(TripPlanningFactsRevisionOverflow):
            _kernel(test_engine).execute(
                scope=_scope(seed), credential=_proof(seed),
                command=_command(seed, key=f"overflow-{attr}", expected_trip_facts_revision=expected),
                mutation=lambda context, _fields: (setattr(context.trip, "name", "rollback"), CommandResponse(200, {"ok": True}))[1],
            )
        with _factory(test_engine)() as session:
            assert session.get(Trip, seed.trip_id).name == "Trip durable"
            assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_tampered_or_expired_historical_outcome_fails_closed(test_engine):
    seed = _seed(test_engine, with_plan=False)
    kernel = _kernel(test_engine)
    command = _command(seed)
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda context, _fields: (setattr(context.trip, "name", "first"), CommandResponse(200, {"ok": True}))[1],
    )
    with _factory(test_engine).begin() as session:
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(IdempotencyRecordExpired):
        kernel.execute(
            scope=_scope(seed), credential=_proof(seed), command=command,
            mutation=lambda _context, _fields: pytest.fail("expired replay callback ran"),
        )


def test_historical_and_current_outcome_policies_are_both_enforced(test_engine):
    seed = _seed(test_engine, with_plan=False)
    command = _command(seed)
    initial = _kernel(test_engine)
    initial.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda context, _fields: (setattr(context.trip, "name", "first"), CommandResponse(200, {"ok": True}))[1],
    )
    strict = TripDurableCommandKernel(
        TripTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("test-v1", b"trip-durable-test-key")),
        StaticOutcomeContractRegistry((OutcomeContract(_OPERATION, 1, frozenset({"different"})),)),
    )
    with pytest.raises(UnsafeDurableOutcome):
        strict.execute(
            scope=_scope(seed), credential=_proof(seed), command=command,
            mutation=lambda _context, _fields: pytest.fail("stored replay callback ran"),
        )


def test_replay_requires_fresh_authorization_and_retained_hmac_verification_key(test_engine):
    seed = _seed(test_engine, with_plan=False)
    command = _command(seed)
    old = HmacSigningKey("old", b"old-trip-key")
    _kernel_with_old = TripDurableCommandKernel(
        TripTransactionKernel(_factory(test_engine)), FixtureKeyring(old),
        StaticOutcomeContractRegistry((OutcomeContract(_OPERATION, 1, frozenset({"ok"})),)),
    )
    _kernel_with_old.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda context, _fields: (setattr(context.trip, "name", "first"), CommandResponse(200, {"ok": True}))[1],
    )
    rotated = TripDurableCommandKernel(
        TripTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("new", b"new-trip-key"), old),
        StaticOutcomeContractRegistry((OutcomeContract(_OPERATION, 1, frozenset({"ok"})),)),
    )
    assert rotated.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda _context, _fields: pytest.fail("rotation replay callback ran"),
    ).replayed is True
    unavailable = TripDurableCommandKernel(
        TripTransactionKernel(_factory(test_engine)), FixtureKeyring(HmacSigningKey("new", b"new-trip-key")),
        StaticOutcomeContractRegistry((OutcomeContract(_OPERATION, 1, frozenset({"ok"})),)),
    )
    with pytest.raises(UnknownHmacKeyVersion):
        unavailable.execute(
            scope=_scope(seed), credential=_proof(seed), command=command,
            mutation=lambda _context, _fields: pytest.fail("unknown-key replay callback ran"),
        )
    with _factory(test_engine).begin() as session:
        session.scalar(select(AuthSession).where(AuthSession.user_id == seed.user_id)).revoked_at = datetime.now(timezone.utc)
    with pytest.raises(InvalidCredential):
        rotated.execute(
            scope=_scope(seed), credential=_proof(seed), command=command,
            mutation=lambda _context, _fields: pytest.fail("revoked replay callback ran"),
        )


def test_failure_after_revision_increment_rolls_back_effect_revisions_and_ledger(test_engine):
    seed = _seed(test_engine, with_plan=True)

    class FailingCheckpoint(TripDurableCommandKernel):
        def _checkpoint(self, stage: str) -> None:
            if stage == "after_revision_increment":
                raise RuntimeError("injected failure")

    kernel = FailingCheckpoint(
        TripTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("test-v1", b"trip-durable-test-key")),
        StaticOutcomeContractRegistry((OutcomeContract(_OPERATION, 1, frozenset({"ok"})),)),
    )
    with pytest.raises(RuntimeError):
        kernel.execute(
            scope=_scope(seed), credential=_proof(seed), command=_command(seed),
            mutation=lambda context, _fields: (setattr(context.trip, "name", "rollback"), CommandResponse(200, {"ok": True}))[1],
        )
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).name == "Trip durable"
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_caller_owned_nested_input_is_immutable_for_callback_and_hmac(test_engine):
    seed = _seed(test_engine, with_plan=False)
    supplied = {"nested": {"label": "Cafe\u0301", "values": [15.0, None]}}
    command = _command(seed, command_fields=supplied)
    supplied["nested"]["label"] = "mutated before run"

    def mutation(context, fields):
        context.trip.name = "immutable input"
        assert fields["nested"]["label"] == "Café"
        assert fields["nested"]["values"] == (15, None)
        with pytest.raises(TypeError):
            fields["nested"]["label"] = "no"
        return CommandResponse(200, {"ok": True})

    _kernel(test_engine).execute(scope=_scope(seed), credential=_proof(seed), command=command, mutation=mutation)


def test_trip_and_plan_scope_collisions_fail_before_foreign_result_interpretation(test_engine):
    seed = _seed(test_engine, with_plan=True)
    trip = _kernel(test_engine)
    trip_command = _command(seed, key="shared-scope-key")
    trip.execute(
        scope=_scope(seed), credential=_proof(seed), command=trip_command,
        mutation=lambda context, _fields: (setattr(context.trip, "name", "trip result"), CommandResponse(200, {"ok": True}))[1],
    )
    plan_kernel = DurableCommandKernel(
        PlanTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("test-v1", b"trip-durable-test-key")),
        StaticOutcomeContractRegistry((OutcomeContract("plan.test.v1", 1, frozenset({"ok"})),)),
    )
    plan_command = IdempotentCommand(
        operation_code="plan.test.v1", resource_kind="plan", resource_id=seed.plan_id,
        idempotency_key="shared-scope-key", expected_plan_revision=1, command_fields={"x": 1},
        fingerprint_version=1, expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with pytest.raises(IdempotencyKeyReused):
        plan_kernel.execute(
            scope=_scope(seed), credential=_proof(seed), command=plan_command,
            mutation=lambda _context, _fields: pytest.fail("foreign-scope callback ran"),
        )


def test_plan_scope_record_is_rejected_by_trip_kernel_before_hmac_interpretation(test_engine):
    seed = _seed(test_engine, with_plan=True)
    plan_kernel = DurableCommandKernel(
        PlanTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("test-v1", b"trip-durable-test-key")),
        StaticOutcomeContractRegistry((OutcomeContract("plan.test.v1", 1, frozenset({"ok"})),)),
    )
    plan_kernel.execute(
        scope=_scope(seed), credential=_proof(seed),
        command=IdempotentCommand(
            operation_code="plan.test.v1", resource_kind="plan", resource_id=seed.plan_id,
            idempotency_key="plan-then-trip", expected_plan_revision=0, command_fields={"x": 1},
            fingerprint_version=1, expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        mutation=lambda context, _fields: (setattr(context.plan, "needs_refresh", True), CommandResponse(200, {"ok": True}))[1],
    )
    with pytest.raises(IdempotencyKeyReused):
        _kernel(test_engine).execute(
            scope=_scope(seed), credential=_proof(seed),
            command=_command(seed, key="plan-then-trip"),
            mutation=lambda _context, _fields: pytest.fail("foreign-scope callback ran"),
        )


def test_zero_plan_historical_replay_survives_later_plan_creation(test_engine):
    seed = _seed(test_engine, with_plan=False)
    kernel = _kernel(test_engine)
    command = _command(seed)
    first = kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda context, _fields: (setattr(context.trip, "name", "historical"), CommandResponse(201, {"ok": True}))[1],
    )
    with _factory(test_engine).begin() as session:
        plan = Plan(trip_id=seed.trip_id)
        session.add(plan)
        session.flush()
        plan_id = plan.id
    replay = kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command,
        mutation=lambda _context, _fields: pytest.fail("historical replay callback ran"),
    )
    assert replay == type(replay)(201, {"ok": True}, first.trip_planning_facts_revision, True)
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        assert session.get(Plan, plan_id).revision == 0


def test_concurrent_trip_commands_have_real_root_lock_contention_and_one_stale_result(test_engine):
    seed = _seed(test_engine, with_plan=False)
    kernel = _kernel(test_engine)
    first_entered, release, second_root_query = Event(), Event(), Event()
    first_pid: list[int] = []
    second_pid: list[int] = []
    second_thread_id: list[int] = []
    second_error: list[BaseException] = []

    def observe(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if second_thread_id and current_thread().ident == second_thread_id[0] and "from trip" in statement.lower() and "for update" in statement.lower():
            second_pid.append(connection.connection.driver_connection.info.backend_pid)
            second_root_query.set()

    def first() -> None:
        def mutation(session, context, _fields):
            first_pid.append(session.connection().connection.driver_connection.info.backend_pid)
            context.trip.name = "first"
            first_entered.set()
            assert release.wait(5), "coordinator did not release first command"
            return CommandResponse(200, {"ok": True})
        kernel._execute_with_session_mutation(
            scope=_scope(seed), credential=_proof(seed), command=_command(seed, key="first-key"), mutation=mutation,
        )

    def second() -> None:
        second_thread_id.append(current_thread().ident or 0)
        try:
            kernel.execute(
                scope=_scope(seed), credential=_proof(seed), command=_command(seed, key="second-key"),
                mutation=lambda _context, _fields: pytest.fail("stale callback ran"),
            )
        except BaseException as exc:
            second_error.append(exc)

    event.listen(test_engine, "before_cursor_execute", observe)
    try:
        first_worker, first_errors = _thread(first)
        assert first_entered.wait(5)
        second_worker, second_errors = _thread(second)
        assert second_root_query.wait(5)
        with _factory(test_engine)() as observer:
            blockers: list[int] = []
            # The SQLAlchemy event fires immediately before driver dispatch;
            # bounded PostgreSQL observations distinguish that scheduling point
            # from actual server-side lock waiting without a sleep heuristic.
            for _ in range(200):
                blockers = observer.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid[0]}
                ).scalar_one()
                if first_pid[0] in blockers:
                    break
        assert first_pid[0] in blockers
        release.set()
        _join(first_worker, first_errors)
        _join(second_worker, second_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", observe)
        release.set()
    assert len(second_error) == 1 and isinstance(second_error[0], StaleTripPlanningFactsRevision)
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 1
