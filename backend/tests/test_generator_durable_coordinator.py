"""B.2D3 contract tests for the Generator durable coordinator.

The public internal seams are the frozen identity helpers,
``GeneratorDurableCoordinator.preflight()``, and
``GeneratorDurableCoordinator.execute(..., synthetic_mutation=...)``.  Real
Generator draft persistence remains intentionally outside this test module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread
from time import monotonic
from typing import Callable

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    AuthSession,
    MutationIdempotency,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
    User,
)
from app.domain import auth as auth_service
from app.domain.plans.generator_lifecycle import (
    OrganizerReadiness,
    PreparedGeneration,
    PreparedPlan,
    PreparedPlanningInputs,
    PreparedTrip,
)
from app.domain.transactions import (
    CommandResponse,
    CredentialProof,
    DuplicatePlans,
    HmacSigningKey,
    IdempotencyKeyReused,
    IdempotencyRecordExpired,
    InvalidCredential,
    MembershipNotAuthorized,
    OutcomeContract,
    PreliminaryActorScope,
    StaticOutcomeContractRegistry,
    TripDurableCommandKernel,
    TripIdempotentCommand,
    TripTransactionKernel,
    TransactionIntegrityViolation,
    UnknownHmacKeyVersion,
    UnsafeDurableOutcome,
    UnsafeNoOpMutation,
)
from app.domain.transactions.generator_durable import (
    GENERATOR_COMMAND_NAMESPACE_V1,
    GENERATOR_FIRST_PLAN_NAMESPACE_V1,
    GENERATOR_OPERATION_CODE,
    GeneratorDurableCoordinator,
    GeneratorExecutionPreconditions,
    GeneratorIdempotentCommand,
    StaleGeneratorPreconditions,
    derive_generator_identity,
    deterministic_first_plan_id,
    generator_outcome_contract,
    generator_identity_name,
)


_seed_records: list[tuple[str, str]] = []


class FixtureKeyring:
    def __init__(self, active: HmacSigningKey, *verification: HmacSigningKey):
        self._active = active
        self._keys = {key.version: key for key in (active, *verification)}

    def active_key(self) -> HmacSigningKey:
        return self._active

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._keys.get(version)


@dataclass(frozen=True)
class Seed:
    trip_id: str
    membership_id: str
    user_id: str
    token: str
    plan_id: str | None


def _factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False, future=True)


def _seed(test_engine, *, with_plan: bool = False, currency: str = "USD") -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    token = f"generator-durable-{stamp}"
    with _factory(test_engine).begin() as session:
        user = User(name="Generator organizer", email=f"generator-{stamp}@example.test")
        session.add(user)
        session.flush()
        trip = Trip(
            name="Generator durable",
            destination="Chicago",
            created_by_user_id=user.id,
            currency=currency,
        )
        session.add(trip)
        session.flush()
        membership = TripMembership(
            trip_id=trip.id,
            user_id=user.id,
            role="organizer",
            status="preferences_submitted",
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
        _seed_records.append((trip.id, user.id))
        return Seed(trip.id, membership.id, user.id, token, plan.id if plan is not None else None)


@pytest.fixture(autouse=True)
def _cleanup_seed_rows(test_engine):
    _seed_records.clear()
    yield
    records = tuple(_seed_records)
    _seed_records.clear()
    if not records:
        return
    trip_ids = [trip_id for trip_id, _user_id in records]
    user_ids = [user_id for _trip_id, user_id in records]
    with _factory(test_engine).begin() as session:
        session.query(MutationIdempotency).filter(
            MutationIdempotency.trip_id.in_(trip_ids)
        ).delete(synchronize_session=False)
        session.query(PlanItem).filter(
            PlanItem.plan_id.in_(select(Plan.id).where(Plan.trip_id.in_(trip_ids)))
        ).delete(synchronize_session=False)
        session.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(
            synchronize_session=False
        )
        session.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)


def _scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.membership_id,
        credential_kind="account",
        expected_user_id=seed.user_id,
    )


def _proof(seed: Seed) -> CredentialProof:
    return CredentialProof.from_bearer_token(seed.token)


def _command(*, key: str = "generator-durable-key") -> GeneratorIdempotentCommand:
    return GeneratorIdempotentCommand(
        idempotency_key=key,
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )


def _coordinator(test_engine, *, keyring: FixtureKeyring | None = None) -> GeneratorDurableCoordinator:
    return GeneratorDurableCoordinator(
        TripTransactionKernel(_factory(test_engine)),
        keyring or FixtureKeyring(HmacSigningKey("generator-v1", b"generator-test-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
    )


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


def _await_pg_blocking(test_engine, *, first_pid: int, second_pid: int) -> None:
    """Observe a real PostgreSQL lock wait without a timing sleep heuristic."""

    deadline = monotonic() + 5
    with test_engine.connect() as observer:
        while monotonic() < deadline:
            if observer.scalar(
                text("SELECT :first_pid = ANY(pg_blocking_pids(:second_pid))"),
                {"first_pid": first_pid, "second_pid": second_pid},
            ):
                return
    raise AssertionError("second PostgreSQL connection never blocked on the Trip root lock")


def _zero_plan_preconditions() -> GeneratorExecutionPreconditions:
    return GeneratorExecutionPreconditions(
        trip_planning_facts_revision=0,
        plan_id=None,
        plan_revision=None,
        plan_status=None,
        plan_currency=None,
        plan_needs_refresh=None,
        plan_item_count=0,
        planning_input_fingerprint="0" * 64,
    )


def _existing_plan_preconditions(seed: Seed) -> GeneratorExecutionPreconditions:
    assert seed.plan_id is not None
    return GeneratorExecutionPreconditions(
        trip_planning_facts_revision=0,
        plan_id=seed.plan_id,
        plan_revision=0,
        plan_status="active",
        plan_currency="USD",
        plan_needs_refresh=False,
        plan_item_count=0,
        planning_input_fingerprint="1" * 64,
    )


def _safe_outcome(plan_id: str) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "status": "active",
        "days": [],
        "blocked_reason": None,
        "blocked_code": None,
        "generated_by": "rules",
        "used_ai": False,
        "planner_note": None,
    }


def test_frozen_generator_uuid_identity_golden_vectors() -> None:
    vector_a = (
        "plan.generate.v1",
        "0123456789abcdef0123456789abcdef",
        "fedcba9876543210fedcba9876543210",
        "gen-retry-001",
    )
    assert generator_identity_name(vector_a) == (
        '["plan.generate.v1","0123456789abcdef0123456789abcdef",'
        '"fedcba9876543210fedcba9876543210","gen-retry-001"]'
    )
    assert derive_generator_identity(GENERATOR_COMMAND_NAMESPACE_V1, vector_a) == (
        "691bbfea177855d29f9a99d461752085"
    )
    assert derive_generator_identity(GENERATOR_FIRST_PLAN_NAMESPACE_V1, vector_a) == (
        "ca750cd46b0f55a5abb0d566f0adb167"
    )

    composed = ("plan.generate.v1", "café", "memberé", "retry-01")
    decomposed = ("plan.generate.v1", "cafe\u0301", "membere\u0301", "retry-01")
    assert generator_identity_name(composed) == generator_identity_name(decomposed)
    assert derive_generator_identity(GENERATOR_COMMAND_NAMESPACE_V1, decomposed) == (
        "9b6560b5530757098a0210cdb416ebb6"
    )
    assert derive_generator_identity(GENERATOR_FIRST_PLAN_NAMESPACE_V1, decomposed) == (
        "c219395c22a658a0bb7d8023be303e86"
    )


def test_execution_preconditions_adapt_the_detached_b2d1_snapshot_without_projection_copy() -> None:
    prepared = PreparedGeneration(
        trip=PreparedTrip(
            id="1" * 32,
            persisted_destination="Québec",
            destination="Québec",
            preferred_start_date=None,
            preferred_end_date=None,
            dates=(),
            currency="CAD",
            planning_facts_revision=3,
        ),
        organizer=OrganizerReadiness(
            membership_id="2" * 32,
            role="organizer",
            status="preferences_submitted",
            has_submitted_preference=True,
        ),
        plan=PreparedPlan(
            id="a" * 32,
            revision=4,
            status="blocked",
            currency="EUR",
            blocked_reason="No candidates",
            needs_refresh=True,
            item_count=0,
        ),
        inputs=PreparedPlanningInputs(
            membership_count=1,
            required_constraints=(),
            preferences=(),
            interests=(),
            meal_budget_cap=None,
            availability=(),
            day_slots=(),
            preferred_activity_counts=(),
        ),
        generation_eligible=True,
        planning_input_fingerprint="b" * 64,
    )

    captured = GeneratorExecutionPreconditions.from_prepared(prepared)

    assert captured.trip_planning_facts_revision == 3
    assert captured.plan_id == "a" * 32
    assert captured.plan_revision == 4
    assert captured.plan_currency == "EUR"
    assert captured.plan_blocked_reason == "No candidates"
    assert captured.planning_input_fingerprint == "b" * 64


def test_preflight_missing_ledger_returns_new_command_without_reserving_key(test_engine) -> None:
    seed = _seed(test_engine)

    decision = _coordinator(test_engine).preflight(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(),
    )

    assert decision.is_new is True
    assert decision.result is None
    with _factory(test_engine)() as session:
        assert session.scalars(select(Plan).where(Plan.trip_id == seed.trip_id)).all() == []
        assert session.scalars(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id)
        ).all() == []


def test_zero_plan_final_execution_creates_one_plan_then_preflight_replays(test_engine) -> None:
    seed = _seed(test_engine, currency="CAD")
    coordinator = _coordinator(test_engine)
    command = _command()
    validations: list[str] = []
    mutations: list[str] = []

    result = coordinator.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_zero_plan_preconditions(),
        validate_preconditions=lambda _session, _context, _captured: validations.append("validated"),
        synthetic_mutation=lambda context: (
            mutations.append(context.plan.id),
            setattr(context.plan, "needs_refresh", True),
            CommandResponse(200, _safe_outcome(context.plan.id)),
        )[2],
    )

    expected_plan_id = derive_generator_identity(
        GENERATOR_FIRST_PLAN_NAMESPACE_V1,
        (GENERATOR_OPERATION_CODE, seed.trip_id, seed.membership_id, command.idempotency_key),
    )
    assert validations == ["validated"]
    assert mutations == [expected_plan_id]
    assert result.replayed is False
    assert result.plan_revision == 1
    assert result.payload == _safe_outcome(expected_plan_id)

    replay = coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=command
    )
    assert replay.is_new is False
    assert replay.result is not None and replay.result.replayed is True
    assert replay.result.payload == result.payload

    with _factory(test_engine)() as session:
        plan = session.get(Plan, expected_plan_id)
        assert plan is not None and plan.revision == 1 and plan.needs_refresh is True
        assert plan.currency == "CAD"
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        row = session.scalar(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id)
        )
        assert row is not None
        assert row.aggregate_scope == "plan"
        assert row.resulting_plan_revision == 1
        assert row.resulting_trip_planning_facts_revision is None


def test_preflight_replays_before_current_plan_or_trip_facts_stale_checks(test_engine) -> None:
    seed = _seed(test_engine)
    coordinator = _coordinator(test_engine)
    command = _command(key="replay-before-stale")
    original = coordinator.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_zero_plan_preconditions(),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
    )
    with _factory(test_engine).begin() as session:
        plan = session.get(Plan, original.payload["plan_id"])
        assert plan is not None
        plan.revision = 7
        session.get(Trip, seed.trip_id).planning_facts_revision = 4
        session.add(
            PlanItem(
                plan_id=plan.id,
                day_index=1,
                day_date=date(2026, 10, 11),
                start_hour=9,
                duration_min=60,
                title="Later item",
                place="Chicago",
            )
        )

    replay = coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=command
    )

    assert replay.result is not None and replay.result.replayed is True
    assert replay.result.plan_revision == 1
    assert replay.result.payload == original.payload


def test_existing_empty_plan_true_noop_keeps_revision_and_replays_safe_outcome(test_engine) -> None:
    seed = _seed(test_engine, with_plan=True)
    assert seed.plan_id is not None
    coordinator = _coordinator(test_engine)
    command = _command(key="existing-noop")

    first = coordinator.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_existing_plan_preconditions(seed),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: CommandResponse(
            200, _safe_outcome(context.plan.id), state_changed=False
        ),
    )
    replay = coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=command
    )

    assert first.replayed is False and first.plan_revision == 0
    assert replay.result is not None and replay.result.replayed is True
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 1


def test_existing_empty_plan_state_change_advances_only_plan_revision_once(test_engine) -> None:
    seed = _seed(test_engine, with_plan=True)
    assert seed.plan_id is not None

    result = _coordinator(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(key="existing-plan-change"),
        preconditions=_existing_plan_preconditions(seed),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: (
            setattr(context.plan, "needs_refresh", True),
            CommandResponse(200, _safe_outcome(context.plan.id)),
        )[1],
    )

    assert result.replayed is False and result.plan_revision == 1
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.get(Plan, seed.plan_id).needs_refresh is True
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        row = session.scalar(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id)
        )
        assert row is not None and row.resulting_plan_revision == 1
        assert row.resulting_trip_planning_facts_revision is None


def test_false_noop_plan_mutation_rolls_back_effect_and_ledger(test_engine) -> None:
    seed = _seed(test_engine, with_plan=True)
    assert seed.plan_id is not None

    with pytest.raises(UnsafeNoOpMutation):
        _coordinator(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(key="dirty-noop"),
            preconditions=_existing_plan_preconditions(seed),
            validate_preconditions=lambda _session, _context, _captured: None,
            synthetic_mutation=lambda context: (
                setattr(context.plan, "needs_refresh", True),
                CommandResponse(200, _safe_outcome(context.plan.id), state_changed=False),
            )[1],
        )

    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).needs_refresh is False
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_final_execution_rejects_stale_captured_facts_before_mutation_or_ledger(test_engine) -> None:
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as session:
        session.get(Trip, seed.trip_id).planning_facts_revision = 1
    called: list[str] = []

    with pytest.raises(StaleGeneratorPreconditions):
        _coordinator(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(key="stale-facts"),
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, _context, _captured: called.append("validator"),
            synthetic_mutation=lambda _context: pytest.fail("stale mutation ran"),
        )

    assert called == []
    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_concurrent_same_key_rechecks_ledger_then_replays_after_trip_lock_contention(test_engine) -> None:
    seed = _seed(test_engine)
    coordinator = _coordinator(test_engine)
    command = _command(key="same-key-concurrency")
    assert coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=command
    ).is_new is True
    assert coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=command
    ).is_new is True

    entered, release, second_trip_query = Event(), Event(), Event()
    first_thread_id: list[int] = []
    second_thread_id: list[int] = []
    backend_pids: dict[str, int] = {}
    results = []
    mutations: list[str] = []

    def capture(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = statement.lower()
        if "from trip" not in normalized or "for update" not in normalized:
            return
        connection_id = current_thread().ident or 0
        pid = connection.connection.driver_connection.info.backend_pid
        if first_thread_id and connection_id == first_thread_id[0]:
            backend_pids["first"] = pid
        if second_thread_id and connection_id == second_thread_id[0]:
            backend_pids["second"] = pid
            second_trip_query.set()

    def first() -> None:
        first_thread_id.append(current_thread().ident or 0)

        def mutation(context) -> CommandResponse:
            mutations.append("first")
            entered.set()
            assert release.wait(5)
            return CommandResponse(200, _safe_outcome(context.plan.id))

        results.append(
            coordinator.execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=command,
                preconditions=_zero_plan_preconditions(),
                validate_preconditions=lambda _session, _context, _captured: None,
                synthetic_mutation=mutation,
            )
        )

    def second() -> None:
        second_thread_id.append(current_thread().ident or 0)
        results.append(
            coordinator.execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=command,
                preconditions=_zero_plan_preconditions(),
                validate_preconditions=lambda *_args: pytest.fail("replay validator ran"),
                synthetic_mutation=lambda _context: pytest.fail("replay mutation ran"),
            )
        )

    event.listen(test_engine, "before_cursor_execute", capture)
    first_worker: Thread | None = None
    second_worker: Thread | None = None
    try:
        first_worker, first_errors = _thread(first)
        assert entered.wait(5)
        second_worker, second_errors = _thread(second)
        assert second_trip_query.wait(5)
        assert {"first", "second"} <= set(backend_pids)
        _await_pg_blocking(
            test_engine,
            first_pid=backend_pids["first"],
            second_pid=backend_pids["second"],
        )
        assert second_worker.is_alive()
        release.set()
        _join(first_worker, first_errors)
        _join(second_worker, second_errors)
    finally:
        release.set()
        if first_worker is not None:
            first_worker.join(timeout=5)
        if second_worker is not None:
            second_worker.join(timeout=5)
        event.remove(test_engine, "before_cursor_execute", capture)

    assert mutations == ["first"]
    assert {result.replayed for result in results} == {False, True}
    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 1
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 1


def test_concurrent_different_keys_block_then_second_fails_stale_without_ledger(test_engine) -> None:
    seed = _seed(test_engine)
    coordinator = _coordinator(test_engine)
    first_command = _command(key="different-key-first")
    second_command = _command(key="different-key-second")
    assert coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=first_command
    ).is_new is True
    assert coordinator.preflight(
        scope=_scope(seed), credential=_proof(seed), command=second_command
    ).is_new is True

    entered, release, second_trip_query = Event(), Event(), Event()
    first_thread_id: list[int] = []
    second_thread_id: list[int] = []
    backend_pids: dict[str, int] = {}
    stale = Event()

    def capture(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = statement.lower()
        if "from trip" not in normalized or "for update" not in normalized:
            return
        thread_id = current_thread().ident or 0
        pid = connection.connection.driver_connection.info.backend_pid
        if first_thread_id and thread_id == first_thread_id[0]:
            backend_pids["first"] = pid
        if second_thread_id and thread_id == second_thread_id[0]:
            backend_pids["second"] = pid
            second_trip_query.set()

    def first() -> None:
        first_thread_id.append(current_thread().ident or 0)
        coordinator.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=first_command,
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, _context, _captured: None,
            synthetic_mutation=lambda context: (
                entered.set(),
                release.wait(5),
                CommandResponse(200, _safe_outcome(context.plan.id)),
            )[2],
        )

    def second() -> None:
        second_thread_id.append(current_thread().ident or 0)
        with pytest.raises(StaleGeneratorPreconditions):
            coordinator.execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=second_command,
                preconditions=_zero_plan_preconditions(),
                validate_preconditions=lambda *_args: pytest.fail("stale validator ran"),
                synthetic_mutation=lambda _context: pytest.fail("stale mutation ran"),
            )
        stale.set()

    event.listen(test_engine, "before_cursor_execute", capture)
    first_worker: Thread | None = None
    second_worker: Thread | None = None
    try:
        first_worker, first_errors = _thread(first)
        assert entered.wait(5)
        second_worker, second_errors = _thread(second)
        assert second_trip_query.wait(5)
        _await_pg_blocking(
            test_engine,
            first_pid=backend_pids["first"],
            second_pid=backend_pids["second"],
        )
        release.set()
        _join(first_worker, first_errors)
        _join(second_worker, second_errors)
    finally:
        release.set()
        if first_worker is not None:
            first_worker.join(timeout=5)
        if second_worker is not None:
            second_worker.join(timeout=5)
        event.remove(test_engine, "before_cursor_execute", capture)

    assert stale.is_set()
    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 1
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 1


@pytest.mark.parametrize("invalidator", ["credential", "membership"])
def test_historical_replay_requires_fresh_organizer_authorization(test_engine, invalidator) -> None:
    seed = _seed(test_engine)
    coordinator = _coordinator(test_engine)
    command = _command(key=f"fresh-authorization-{invalidator}")
    coordinator.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_zero_plan_preconditions(),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
    )
    with _factory(test_engine).begin() as session:
        if invalidator == "credential":
            auth_session = session.scalar(select(AuthSession).where(AuthSession.user_id == seed.user_id))
            assert auth_session is not None
            auth_session.revoked_at = datetime.now(timezone.utc)
        else:
            session.get(TripMembership, seed.membership_id).status = "removed"

    expected_error = InvalidCredential if invalidator == "credential" else MembershipNotAuthorized
    with pytest.raises(expected_error):
        coordinator.preflight(scope=_scope(seed), credential=_proof(seed), command=command)


def test_duplicate_plans_fail_before_historical_generator_replay(test_engine) -> None:
    seed = _seed(test_engine)
    coordinator = _coordinator(test_engine)
    command = _command(key="duplicate-plans-before-replay")
    coordinator.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_zero_plan_preconditions(),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
    )
    with _factory(test_engine).begin() as session:
        session.add(Plan(trip_id=seed.trip_id))

    with pytest.raises(DuplicatePlans):
        coordinator.preflight(scope=_scope(seed), credential=_proof(seed), command=command)


def test_expired_generator_ledger_record_fails_closed(test_engine) -> None:
    seed = _seed(test_engine)
    coordinator = _coordinator(test_engine)
    command = _command(key="expired-generator-key")
    coordinator.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_zero_plan_preconditions(),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
    )
    with _factory(test_engine).begin() as session:
        row = session.scalar(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id)
        )
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(IdempotencyRecordExpired):
        coordinator.preflight(scope=_scope(seed), credential=_proof(seed), command=command)


def test_generator_outcome_rejects_extra_private_field_and_rolls_back_first_plan(test_engine) -> None:
    seed = _seed(test_engine)

    with pytest.raises(UnsafeDurableOutcome):
        _coordinator(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(key="unsafe-generator-outcome"),
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, _context, _captured: None,
            synthetic_mutation=lambda context: CommandResponse(
                200,
                {**_safe_outcome(context.plan.id), "private_constraint": "never persist"},
            ),
        )

    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_generator_replay_survives_hmac_key_rotation_but_missing_retained_key_fails(test_engine) -> None:
    seed = _seed(test_engine)
    command = _command(key="generator-key-rotation")
    old_key = HmacSigningKey("generator-old", b"old-generator-key")
    new_key = HmacSigningKey("generator-new", b"new-generator-key")
    original = _coordinator(test_engine, keyring=FixtureKeyring(old_key)).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        preconditions=_zero_plan_preconditions(),
        validate_preconditions=lambda _session, _context, _captured: None,
        synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
    )

    replay = _coordinator(test_engine, keyring=FixtureKeyring(new_key, old_key)).preflight(
        scope=_scope(seed), credential=_proof(seed), command=command
    )
    assert replay.result is not None and replay.result.payload == original.payload

    with pytest.raises(UnknownHmacKeyVersion):
        _coordinator(test_engine, keyring=FixtureKeyring(new_key)).preflight(
            scope=_scope(seed), credential=_proof(seed), command=command
        )


def test_generator_key_cannot_reinterpret_trip_scoped_completed_ledger(test_engine) -> None:
    seed = _seed(test_engine)
    key = "cross-aggregate-key"
    trip_operation = "trip.generator-collision.v1"
    keyring = FixtureKeyring(HmacSigningKey("shared-key", b"shared-test-key"))
    trip_durable = TripDurableCommandKernel(
        TripTransactionKernel(_factory(test_engine)),
        keyring,
        StaticOutcomeContractRegistry((OutcomeContract(trip_operation, 1, frozenset({"ok"})),)),
    )
    trip_durable.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=TripIdempotentCommand(
            operation_code=trip_operation,
            resource_kind="trip_planning_facts",
            resource_id=seed.trip_id,
            idempotency_key=key,
            expected_trip_facts_revision=0,
            command_fields={"reason": "cross aggregate test"},
            fingerprint_version=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        mutation=lambda context, _fields: (
            setattr(context.trip, "name", "Trip durable winner"),
            CommandResponse(200, {"ok": True}),
        )[1],
    )

    with pytest.raises(IdempotencyKeyReused):
        _coordinator(test_engine, keyring=keyring).preflight(
            scope=_scope(seed), credential=_proof(seed), command=_command(key=key)
        )


def test_first_plan_identity_collision_fails_closed_without_ledger(test_engine) -> None:
    seed = _seed(test_engine)
    command = _command(key="first-plan-collision")
    candidate_id = deterministic_first_plan_id(
        trip_id=seed.trip_id,
        actor_membership_id=seed.membership_id,
        idempotency_key=command.idempotency_key,
    )
    with _factory(test_engine).begin() as session:
        foreign_trip = Trip(
            name="Foreign collision holder",
            destination="Chicago",
            created_by_user_id=seed.user_id,
        )
        session.add(foreign_trip)
        session.flush()
        session.add(Plan(id=candidate_id, trip_id=foreign_trip.id))
        _seed_records.append((foreign_trip.id, seed.user_id))

    with pytest.raises(TransactionIntegrityViolation):
        _coordinator(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=command,
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, _context, _captured: None,
            synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
        )

    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.get(Plan, candidate_id).trip_id != seed.trip_id


class _CheckpointFailure(GeneratorDurableCoordinator):
    def __init__(self, *args, stage: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._stage = stage

    def _checkpoint(self, stage: str) -> None:
        if stage == self._stage:
            raise RuntimeError(f"injected {stage}")


@pytest.mark.parametrize(
    "stage", ["after_domain_effect", "after_revision_increment", "after_ledger_insert"]
)
def test_checkpoint_failure_rolls_back_created_plan_revision_and_ledger(test_engine, stage) -> None:
    seed = _seed(test_engine)
    failing = _CheckpointFailure(
        TripTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("generator-v1", b"generator-test-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
        stage=stage,
    )

    with pytest.raises(RuntimeError, match=stage):
        failing.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(key=f"checkpoint-{stage}"),
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, _context, _captured: None,
            synthetic_mutation=lambda context: CommandResponse(200, _safe_outcome(context.plan.id)),
        )

    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_precondition_validator_cannot_stage_writes(test_engine) -> None:
    seed = _seed(test_engine)

    with pytest.raises(UnsafeNoOpMutation):
        _coordinator(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(key="validator-write"),
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, context, _captured: setattr(
                context.trip, "name", "must roll back"
            ),
            synthetic_mutation=lambda _context: pytest.fail("mutation after validator write ran"),
        )

    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).name == "Generator durable"
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0


def test_generator_outcome_rejects_malformed_nested_item_and_rolls_back(test_engine) -> None:
    seed = _seed(test_engine)

    def malformed(context) -> CommandResponse:
        outcome = _safe_outcome(context.plan.id)
        outcome["days"] = [
            {
                "day_index": 1,
                "day_date": "2026-10-11",
                "items": [
                    {
                        "id": "a" * 32,
                        "day_index": 1,
                        "day_date": "2026-10-11",
                        "start_hour": 10,
                        "title": "Unsafe nested item",
                        "local_title": None,
                        "place": "Chicago",
                        "price_per_person": None,
                        "source": "mock",
                        "lat": None,
                        "lng": None,
                        "photo_url": None,
                        # `tags` intentionally missing from the exact wire shape.
                    }
                ],
            }
        ]
        return CommandResponse(200, outcome)

    with pytest.raises(UnsafeDurableOutcome):
        _coordinator(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(key="malformed-nested-outcome"),
            preconditions=_zero_plan_preconditions(),
            validate_preconditions=lambda _session, _context, _captured: None,
            synthetic_mutation=malformed,
        )

    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 0
        assert session.query(MutationIdempotency).filter_by(trip_id=seed.trip_id).count() == 0
