from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Event, Thread, current_thread
from typing import Callable

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuthSession, GuestSession, Plan, Trip, TripMembership, User
from app.domain import auth as auth_service
from app.domain.transactions import (
    CredentialProof,
    ContextExpired,
    DuplicatePlans,
    InsufficientRole,
    InvalidCredential,
    LockUnavailable,
    MembershipNotAuthorized,
    PlanTransactionKernel,
    PreliminaryActorScope,
    TripTransactionKernel,
    TripNotFound,
    UnsafeCallbackResult,
)


def _factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False, future=True)


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str | None
    account_user_id: str
    account_membership_id: str
    account_token: str
    guest_membership_id: str
    guest_token: str
    guest_session_id: str
    auth_session_id: str


_seed_records: list[tuple[str, tuple[str, ...]]] = []


def _seed(
    test_engine,
    *,
    plan_count: int = 1,
    guest_status: str = "joined",
    account_token: str | None = None,
) -> Seed:
    factory = _factory(test_engine)
    stamp = datetime.now(timezone.utc).timestamp()
    account_token = account_token or f"account-trip-kernel-{stamp}"
    guest_token = f"gst_guest-trip-kernel-{stamp}"
    with factory.begin() as session:
        account = User(name="Trip account", email=f"account-{stamp}@example.test")
        owner = User(name="Trip owner", email=f"owner-{stamp}@example.test")
        session.add_all([account, owner])
        session.flush()
        trip = Trip(name="Trip kernel trip", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip)
        session.flush()
        account_membership = TripMembership(
            trip_id=trip.id, user_id=account.id, role="participant", status="joined"
        )
        guest_membership = TripMembership(
            trip_id=trip.id,
            user_id=None,
            guest_display_name="Trip guest",
            role="participant",
            status=guest_status,
        )
        session.add_all([account_membership, guest_membership])
        session.flush()
        auth_session = AuthSession(
            user_id=account.id,
            token_hash=auth_service.token_hash(account_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        guest_session = GuestSession(
            membership_id=guest_membership.id,
            trip_id=trip.id,
            token_hash=auth_service.token_hash(guest_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        session.add_all([auth_session, guest_session])
        plans = [Plan(trip_id=trip.id) for _ in range(plan_count)]
        session.add_all(plans)
        session.flush()
        _seed_records.append((trip.id, (account.id, owner.id)))
        return Seed(
            trip_id=trip.id,
            plan_id=plans[0].id if plans else None,
            account_user_id=account.id,
            account_membership_id=account_membership.id,
            account_token=account_token,
            guest_membership_id=guest_membership.id,
            guest_token=guest_token,
            guest_session_id=guest_session.id,
            auth_session_id=auth_session.id,
        )


@pytest.fixture(autouse=True)
def _cleanup_seed_rows(test_engine):
    _seed_records.clear()
    yield
    records = list(_seed_records)
    _seed_records.clear()
    if not records:
        return
    trip_ids = [trip_id for trip_id, _ in records]
    user_ids = [user_id for _, users in records for user_id in users]
    with _factory(test_engine).begin() as session:
        session.query(GuestSession).filter(GuestSession.trip_id.in_(trip_ids)).delete(
            synchronize_session=False
        )
        session.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(
            synchronize_session=False
        )
        session.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False)
        session.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)


def _account_scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.account_membership_id,
        credential_kind="account",
        expected_user_id=seed.account_user_id,
    )


def _guest_scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.guest_membership_id,
        credential_kind="guest",
    )


def _account_proof(seed: Seed) -> CredentialProof:
    return CredentialProof.from_bearer_token(seed.account_token)


def _guest_proof(seed: Seed) -> CredentialProof:
    return CredentialProof.from_bearer_token(seed.guest_token)


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


def test_trip_kernel_accepts_a_trip_with_zero_plans_without_creating_one(test_engine) -> None:
    factory = _factory(test_engine)
    token = f"trip-kernel-account-{datetime.now(timezone.utc).timestamp()}"
    with factory.begin() as session:
        user = User(name="Trip kernel account", email=f"{token}@example.test")
        owner = User(name="Trip kernel owner", email=f"owner-{token}@example.test")
        session.add_all([user, owner])
        session.flush()
        trip = Trip(name="Zero plan trip", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip)
        session.flush()
        membership = TripMembership(
            trip_id=trip.id,
            user_id=user.id,
            role="participant",
            status="joined",
        )
        session.add(membership)
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=auth_service.token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        session.flush()
        trip_id, membership_id, user_id = trip.id, membership.id, user.id

    try:
        result = TripTransactionKernel(factory).execute(
            scope=PreliminaryActorScope(
                trip_id=trip_id,
                membership_id=membership_id,
                credential_kind="account",
                expected_user_id=user_id,
            ),
            credential=CredentialProof.from_bearer_token(token),
            operation=lambda context: {
                "trip_id": context.trip.id,
                "plan_id": context.plan_id,
                "trip_revision": context.planning_facts_revision,
                "plan_revision": context.plan_revision,
            },
        )
        assert result == {
            "trip_id": trip_id,
            "plan_id": None,
            "trip_revision": 0,
            "plan_revision": None,
        }
        with factory() as session:
            assert session.query(Trip).filter_by(id=trip_id).one().planning_facts_revision == 0
            assert session.query(Plan).filter_by(trip_id=trip_id).count() == 0
    finally:
        with factory.begin() as session:
            session.query(TripMembership).filter_by(trip_id=trip_id).delete(synchronize_session=False)
            session.query(Trip).filter_by(id=trip_id).delete(synchronize_session=False)
            session.query(AuthSession).filter_by(user_id=user_id).delete(synchronize_session=False)
            session.query(User).filter(User.id.in_([user_id, owner.id])).delete(synchronize_session=False)


def test_trip_kernel_locks_one_plan_without_changing_either_revision(test_engine) -> None:
    seed = _seed(test_engine)
    context_holder = []
    result = TripTransactionKernel(_factory(test_engine)).execute(
        scope=_account_scope(seed),
        credential=_account_proof(seed),
        operation=lambda context: (
            context_holder.append(context),
            {
                "trip_id": context.trip_id,
                "plan_id": context.plan_id,
                "trip_revision": context.planning_facts_revision,
                "plan_revision": context.plan_revision,
            },
        )[1],
    )

    assert result == {
        "trip_id": seed.trip_id,
        "plan_id": seed.plan_id,
        "trip_revision": 0,
        "plan_revision": 0,
    }
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        assert session.get(Plan, seed.plan_id).revision == 0
    with pytest.raises(ContextExpired):
        _ = context_holder[0].trip_id


def test_trip_kernel_rejects_duplicate_plans_without_callback_or_mutation(test_engine) -> None:
    seed = _seed(test_engine, plan_count=2)
    callback_called = False

    def operation(_context):
        nonlocal callback_called
        callback_called = True

    with pytest.raises(DuplicatePlans):
        TripTransactionKernel(_factory(test_engine)).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=operation
        )
    assert callback_called is False
    with _factory(test_engine)() as session:
        assert session.query(Plan).filter_by(trip_id=seed.trip_id).count() == 2
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0


def test_preferences_submitted_guest_is_active_in_legacy_plan_and_trip_paths(test_engine) -> None:
    seed = _seed(test_engine, guest_status="preferences_submitted")
    factory = _factory(test_engine)
    with factory() as session:
        assert auth_service.membership_for_guest_token(session, seed.guest_token).id == seed.guest_membership_id

    assert PlanTransactionKernel(factory).execute(
        scope=_guest_scope(seed), credential=_guest_proof(seed), operation=lambda context: context.actor.membership_id
    ) == seed.guest_membership_id
    assert TripTransactionKernel(factory).execute(
        scope=_guest_scope(seed), credential=_guest_proof(seed), operation=lambda context: context.actor.membership_id
    ) == seed.guest_membership_id


@pytest.mark.parametrize("guest_status", ["removed", "invited"])
def test_inactive_guest_status_never_reaches_trip_callback(test_engine, guest_status) -> None:
    seed = _seed(test_engine, guest_status=guest_status)
    called = False

    def operation(_context):
        nonlocal called
        called = True

    with pytest.raises(MembershipNotAuthorized):
        TripTransactionKernel(_factory(test_engine)).execute(
            scope=_guest_scope(seed), credential=_guest_proof(seed), operation=operation
        )
    assert called is False


def test_account_token_with_guest_prefix_remains_an_account_in_trip_kernel(test_engine) -> None:
    seed = _seed(test_engine, account_token=f"gst_account-trip-{datetime.now(timezone.utc).timestamp()}")
    assert TripTransactionKernel(_factory(test_engine)).execute(
        scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda context: context.actor.credential_kind
    ) == "account"


def test_trip_callback_cannot_return_or_nest_its_transaction_context(test_engine) -> None:
    seed = _seed(test_engine)
    kernel = TripTransactionKernel(_factory(test_engine))

    with pytest.raises(UnsafeCallbackResult):
        kernel.execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda context: context
        )
    with pytest.raises(UnsafeCallbackResult):
        kernel.execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda context: {"context": [context]},
        )


class ControlledFailure(Exception):
    pass


class TrackingSession(Session):
    was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


def test_trip_kernel_owns_a_fresh_session_rolls_back_and_expires_context(test_engine) -> None:
    seed = _seed(test_engine)
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True, class_=TrackingSession)
    request_session = _factory(test_engine)()
    request_connection_identity = id(request_session.connection().connection)
    sessions: list[TrackingSession] = []
    context_holder = []

    def owned_factory() -> TrackingSession:
        session = factory()
        sessions.append(session)
        return session

    def fail(context) -> None:
        context_holder.append(context)
        context.trip.name = "must roll back"
        raise ControlledFailure()

    try:
        with pytest.raises(ControlledFailure):
            TripTransactionKernel(owned_factory).execute(
                scope=_account_scope(seed), credential=_account_proof(seed), operation=fail
            )
    finally:
        request_session.close()

    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).name == "Trip kernel trip"
    assert sessions and sessions[0].was_closed is True
    assert sessions[0] is not request_session
    assert context_holder[0].connection_identity != request_connection_identity
    with pytest.raises(ContextExpired):
        _ = context_holder[0].planning_facts_revision


def test_trip_kernel_locks_trip_before_optional_plan(test_engine) -> None:
    seed = _seed(test_engine)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        TripTransactionKernel(_factory(test_engine)).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda _context: None
        )
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    trip_lock = next(
        i for i, statement in enumerate(statements) if "from trip" in statement and "for update" in statement
    )
    plan_lock = next(
        i for i, statement in enumerate(statements) if "from plan" in statement and "for update" in statement
    )
    assert trip_lock < plan_lock


def test_mismatched_guest_trip_scope_rejects_before_callback_and_never_locks_a_plan(test_engine) -> None:
    source = _seed(test_engine)
    foreign = _seed(test_engine)
    callback_called = False
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(InvalidCredential):
            TripTransactionKernel(_factory(test_engine)).execute(
                scope=PreliminaryActorScope(
                    trip_id=foreign.trip_id,
                    membership_id=source.guest_membership_id,
                    credential_kind="guest",
                ),
                credential=_guest_proof(source),
                operation=lambda _context: callback_called,
            )
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    assert callback_called is False
    assert not any("from plan" in statement and "for update" in statement for statement in statements)


def test_trip_root_lock_blocks_second_trip_kernel_with_postgresql_evidence(test_engine) -> None:
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    first_locked, release_first, second_root_attempt, second_entered = Event(), Event(), Event(), Event()
    first_thread_id: list[int] = []
    second_thread_id: list[int] = []
    first_pid: list[int] = []
    second_pid: list[int] = []

    def capture(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "from trip" not in statement.lower() or "for update" not in statement.lower():
            return
        pid = connection.connection.driver_connection.info.backend_pid
        if first_thread_id and current_thread().ident == first_thread_id[0]:
            first_pid.append(pid)
        if second_thread_id and current_thread().ident == second_thread_id[0]:
            second_pid.append(pid)
            second_root_attempt.set()

    def first() -> None:
        first_thread_id.append(current_thread().ident or 0)

        def hold(_context) -> None:
            first_locked.set()
            assert release_first.wait(5), "coordinator did not release first command"

        TripTransactionKernel(factory).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=hold
        )

    def second() -> None:
        second_thread_id.append(current_thread().ident or 0)
        TripTransactionKernel(factory).execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda _context: second_entered.set(),
        )

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        first_worker, first_errors = _thread(first)
        assert first_locked.wait(5)
        second_worker, second_errors = _thread(second)
        assert second_root_attempt.wait(5)
        with factory() as observer:
            blockers: list[int] = []
            # ``before_cursor_execute`` proves the competing root statement was
            # issued from this worker, but it fires just before driver dispatch.
            # Re-read PostgreSQL's authoritative lock state with a bounded,
            # sleep-free query loop rather than assuming Python scheduling.
            for _ in range(100):
                blockers = observer.execute(
                    text("select unnest(pg_blocking_pids(:pid))"), {"pid": second_pid[0]}
                ).scalars().all()
                if first_pid and first_pid[0] in blockers:
                    break
        assert first_pid and second_pid and first_pid[0] != second_pid[0]
        assert first_pid[0] in blockers
        assert not second_entered.is_set()
        release_first.set()
        _join(first_worker, first_errors)
        _join(second_worker, second_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
        release_first.set()

    assert second_entered.is_set()


def test_plan_and_trip_kernels_serialize_on_the_same_trip_root(test_engine) -> None:
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    trip_locked, release_trip, plan_root_attempt, plan_entered = Event(), Event(), Event(), Event()
    plan_thread_id: list[int] = []
    plan_pid: list[int] = []

    def capture(connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if (
            plan_thread_id
            and current_thread().ident == plan_thread_id[0]
            and "from trip" in statement.lower()
            and "for update" in statement.lower()
        ):
            plan_pid.append(connection.connection.driver_connection.info.backend_pid)
            plan_root_attempt.set()

    def trip_command() -> None:
        def hold(_context) -> None:
            trip_locked.set()
            assert release_trip.wait(5), "coordinator did not release Trip kernel"

        TripTransactionKernel(factory).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=hold
        )

    def plan_command() -> None:
        plan_thread_id.append(current_thread().ident or 0)
        PlanTransactionKernel(factory).execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda _context: plan_entered.set(),
        )

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        trip_worker, trip_errors = _thread(trip_command)
        assert trip_locked.wait(5)
        plan_worker, plan_errors = _thread(plan_command)
        assert plan_root_attempt.wait(5)
        with factory() as observer:
            blocker_count = 0
            for _ in range(100):
                blocker_count = observer.scalar(
                    text("select count(*) from pg_blocking_pids(:pid)"), {"pid": plan_pid[0]}
                )
                if blocker_count:
                    break
            assert blocker_count >= 1
        assert not plan_entered.is_set()
        release_trip.set()
        _join(trip_worker, trip_errors)
        _join(plan_worker, plan_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
        release_trip.set()

    assert plan_entered.is_set()


def test_credential_only_guest_revocation_waits_then_denies_trip_callback(test_engine) -> None:
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    revoker_locked, release_revoker, credential_attempt, callback_called = Event(), Event(), Event(), Event()
    kernel_thread_id: list[int] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if (
            kernel_thread_id
            and current_thread().ident == kernel_thread_id[0]
            and "from guest_session" in statement.lower()
            and "for update" in statement.lower()
        ):
            credential_attempt.set()

    def revoke() -> None:
        with factory.begin() as session:
            credential = session.scalar(
                select(GuestSession).where(GuestSession.id == seed.guest_session_id).with_for_update()
            )
            assert credential is not None
            revoker_locked.set()
            assert release_revoker.wait(5), "coordinator did not release revoker"
            credential.revoked_at = datetime.now(timezone.utc)

    def command() -> None:
        kernel_thread_id.append(current_thread().ident or 0)
        with pytest.raises(InvalidCredential):
            TripTransactionKernel(factory).execute(
                scope=_guest_scope(seed),
                credential=_guest_proof(seed),
                operation=lambda _context: callback_called.set(),
            )

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        revoker, revoker_errors = _thread(revoke)
        assert revoker_locked.wait(5)
        command_worker, command_errors = _thread(command)
        assert credential_attempt.wait(5)
        assert not callback_called.is_set()
        release_revoker.set()
        _join(revoker, revoker_errors)
        _join(command_worker, command_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
        release_revoker.set()

    assert not callback_called.is_set()


@pytest.mark.parametrize("credential_state", ["revoked", "expired"])
def test_invalid_guest_credential_never_reaches_trip_callback(test_engine, credential_state) -> None:
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as session:
        credential = session.get(GuestSession, seed.guest_session_id)
        assert credential is not None
        if credential_state == "revoked":
            credential.revoked_at = datetime.now(timezone.utc)
        else:
            credential.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    callback_called = False

    def operation(_context) -> None:
        nonlocal callback_called
        callback_called = True

    with pytest.raises(InvalidCredential):
        TripTransactionKernel(_factory(test_engine)).execute(
            scope=_guest_scope(seed), credential=_guest_proof(seed), operation=operation
        )
    assert callback_called is False


def test_wrong_guest_binding_and_account_linked_guest_fail_closed(test_engine) -> None:
    source = _seed(test_engine)
    foreign = _seed(test_engine)
    with pytest.raises(InvalidCredential):
        TripTransactionKernel(_factory(test_engine)).execute(
            scope=PreliminaryActorScope(
                trip_id=foreign.trip_id,
                membership_id=foreign.guest_membership_id,
                credential_kind="guest",
            ),
            credential=_guest_proof(source),
            operation=lambda _context: None,
        )

    with _factory(test_engine).begin() as session:
        # Use an Account from a different Trip so this malformed Guest state
        # does not violate the existing per-Trip account-membership uniqueness.
        session.get(TripMembership, source.guest_membership_id).user_id = foreign.account_user_id
    with pytest.raises(MembershipNotAuthorized):
        TripTransactionKernel(_factory(test_engine)).execute(
            scope=_guest_scope(source), credential=_guest_proof(source), operation=lambda _context: None
        )


def test_membership_removed_after_preliminary_read_fails_before_callback(test_engine) -> None:
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    preliminary = factory()
    try:
        assert preliminary.get(TripMembership, seed.guest_membership_id).status == "joined"
    finally:
        preliminary.close()
    with factory.begin() as session:
        session.get(TripMembership, seed.guest_membership_id).status = "removed"

    with pytest.raises(MembershipNotAuthorized):
        TripTransactionKernel(factory).execute(
            scope=_guest_scope(seed), credential=_guest_proof(seed), operation=lambda _context: None
        )


def test_trip_not_found_role_and_credential_kind_mismatch_are_controlled(test_engine) -> None:
    seed = _seed(test_engine)
    kernel = TripTransactionKernel(_factory(test_engine))
    with pytest.raises(TripNotFound):
        kernel.execute(
            scope=PreliminaryActorScope(
                trip_id="missing-trip",
                membership_id=seed.account_membership_id,
                credential_kind="account",
                expected_user_id=seed.account_user_id,
            ),
            credential=_account_proof(seed),
            operation=lambda _context: None,
        )
    with pytest.raises(InsufficientRole):
        kernel.execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            required_role="organizer",
            operation=lambda _context: None,
        )
    with pytest.raises(InvalidCredential):
        kernel.execute(
            scope=_account_scope(seed), credential=_guest_proof(seed), operation=lambda _context: None
        )


def test_trip_kernel_public_use_is_limited_to_the_default_off_generator_adapter() -> None:
    main_source = Path(__file__).resolve().parents[1] / "app" / "api" / "main.py"
    source = main_source.read_text(encoding="utf-8")
    # B.2D5 authorizes exactly one explicit public composition: the default-off
    # Generator gate.  Other writers continue to compose the kernel inside
    # their reviewed domain adapters rather than opening ad hoc route paths.
    assert "CADENSY_B2D_PROTECTED_GENERATOR_WRITES" in source
    assert "execute_protected_plan_generation" in source
    assert source.count("TripTransactionKernel(SessionLocal)") == 1
    assert source.count("TripTransactionKernel(") == 1


def test_trip_kernel_classifies_root_lock_timeout(test_engine) -> None:
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    locked, release = Event(), Event()

    def holder() -> None:
        TripTransactionKernel(factory).execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda _context: (locked.set(), release.wait(5)),
        )

    worker, errors = _thread(holder)
    assert locked.wait(5)
    try:
        with pytest.raises(LockUnavailable):
            TripTransactionKernel(factory, lock_timeout_ms=100).execute(
                scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda _context: None
            )
    finally:
        release.set()
        _join(worker, errors)
