from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from threading import Event, Thread, current_thread
from typing import Callable

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuthSession, GuestSession, Plan, Trip, TripMembership, User
from app.domain import auth as auth_service
from app.domain.transactions import (
    ContextExpired,
    CredentialProof,
    DuplicatePlans,
    InsufficientRole,
    InvalidCredential,
    LockUnavailable,
    MembershipNotAuthorized,
    MissingPlan,
    PlanTransactionKernel,
    PreliminaryActorScope,
    SafeCommandResult,
    TripNotFound,
    UnsafeCallbackResult,
)


_seed_records: list[tuple[str, tuple[str, ...]]] = []


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str
    account_membership_id: str
    account_user_id: str
    account_token: str
    guest_membership_id: str
    guest_token: str
    guest_session_id: str
    auth_session_id: str


@dataclass(frozen=True)
class PlanResult(SafeCommandResult):
    plan_id: str
    refreshed: bool


def _factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False, future=True)


def _seed_committed(
    test_engine,
    *,
    with_plan: bool = True,
    account_status: str = "joined",
    account_token: str | None = None,
) -> Seed:
    factory = _factory(test_engine)
    account_token = account_token or f"account-kernel-{datetime.now(timezone.utc).timestamp()}"
    guest_token = f"gst_guest-kernel-{datetime.now(timezone.utc).timestamp()}"
    with factory.begin() as session:
        account_user = User(name="Kernel account", email=f"account-{account_token}@example.test")
        owner = User(name="Kernel owner", email=f"owner-{account_token}@example.test")
        session.add_all([account_user, owner])
        session.flush()
        trip = Trip(name="Kernel trip", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip)
        session.flush()
        account_membership = TripMembership(
            trip_id=trip.id,
            user_id=account_user.id,
            role="participant",
            join_method="invite_login",
            status=account_status,
        )
        guest_membership = TripMembership(
            trip_id=trip.id,
            user_id=None,
            guest_display_name="Guest",
            role="participant",
            join_method="invite_guest",
            status="joined",
        )
        session.add_all([account_membership, guest_membership])
        session.flush()
        auth_session = AuthSession(
            user_id=account_user.id,
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
        plan = Plan(trip_id=trip.id) if with_plan else None
        if plan is not None:
            session.add(plan)
        session.flush()
        _seed_records.append((trip.id, (account_user.id, owner.id)))
        return Seed(
            trip_id=trip.id,
            plan_id=plan.id if plan is not None else "",
            account_membership_id=account_membership.id,
            account_user_id=account_user.id,
            account_token=account_token,
            guest_membership_id=guest_membership.id,
            guest_token=guest_token,
            guest_session_id=guest_session.id,
            auth_session_id=auth_session.id,
        )


@pytest.fixture(autouse=True)
def _clean_committed_kernel_rows(test_engine):
    """Remove only rows committed by this module's multi-connection fixtures.

    The normal ``db`` fixture rolls back its work, but independent PostgreSQL
    connections must commit seed rows for other connections to observe them.
    Record those rows explicitly so legacy tests that expect an empty database
    remain isolated.
    """

    _seed_records.clear()
    yield
    records = list(_seed_records)
    _seed_records.clear()
    if not records:
        return
    factory = _factory(test_engine)
    trip_ids = [trip_id for trip_id, _user_ids in records]
    user_ids = [user_id for _trip_id, user_ids in records for user_id in user_ids]
    with factory.begin() as session:
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
        except BaseException as exc:  # surfaced to the coordinator assertion
            errors.put(exc)

    worker = Thread(target=run, daemon=True)
    worker.start()
    return worker, errors


def _join(worker: Thread, errors: Queue[BaseException]) -> None:
    worker.join(timeout=5)
    assert not worker.is_alive(), "worker did not finish"
    if not errors.empty():
        raise errors.get()


def test_fresh_session_and_connection_are_owned_by_kernel(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    request_session = factory()
    request_membership = request_session.get(TripMembership, seed.account_membership_id)
    request_connection_identity = id(request_session.connection().connection)
    kernel = PlanTransactionKernel(factory)
    try:
        observed = kernel.execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda context: (
                context.session_identity,
                context.connection_identity,
                context.actor.membership_id,
            ),
        )
    finally:
        request_session.close()

    assert request_membership.id == seed.account_membership_id
    assert observed[0] != id(request_session)
    assert observed[1] != request_connection_identity
    assert observed[2] == seed.account_membership_id
    assert not hasattr(_account_proof(seed), "token")
    assert not hasattr(_account_proof(seed), "credential_kind")


def test_account_first_scope_accepts_an_account_token_with_guest_prefix(test_engine):
    seed = _seed_committed(
        test_engine,
        account_token=f"gst_account-kernel-{datetime.now(timezone.utc).timestamp()}",
    )
    kernel = PlanTransactionKernel(_factory(test_engine))

    assert kernel.execute(
        scope=_account_scope(seed),
        credential=_account_proof(seed),
        operation=lambda context: context.actor.credential_kind,
    ) == "account"
    assert kernel.execute(
        scope=_guest_scope(seed),
        credential=_guest_proof(seed),
        operation=lambda context: context.actor.credential_kind,
    ) == "guest"


def test_credential_proof_cannot_cross_account_and_guest_scope_boundaries(test_engine):
    seed = _seed_committed(test_engine)
    kernel = PlanTransactionKernel(_factory(test_engine))

    with pytest.raises(InvalidCredential):
        kernel.execute(
            scope=_account_scope(seed), credential=_guest_proof(seed), operation=lambda _context: None
        )
    with pytest.raises(InvalidCredential):
        kernel.execute(
            scope=_guest_scope(seed), credential=_account_proof(seed), operation=lambda _context: None
        )
    with pytest.raises(InvalidCredential):
        kernel.execute(
            scope=_account_scope(seed),
            credential=CredentialProof.from_bearer_token("invalid-account-token"),
            operation=lambda _context: None,
        )


def test_trip_lock_precedes_plan_lock_and_context_expires_after_callback(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    kernel = PlanTransactionKernel(factory)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    event.listen(test_engine, "before_cursor_execute", capture)
    context_holder = []
    try:
        kernel.execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda context: context_holder.append(context),
        )
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    trip_lock = next(i for i, statement in enumerate(statements) if "from trip" in statement and "for update" in statement)
    plan_lock = next(i for i, statement in enumerate(statements) if "from plan" in statement and "for update" in statement)
    assert trip_lock < plan_lock
    with pytest.raises(ContextExpired):
        _ = context_holder[0].plan


def test_second_writer_waits_until_first_commits_then_reads_committed_state(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    first_kernel = PlanTransactionKernel(factory)
    second_kernel = PlanTransactionKernel(factory)
    first_locked, release_first, second_query, second_entered = Event(), Event(), Event(), Event()
    second_thread_id: list[int] = []
    second_seen: list[bool] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if (
            second_thread_id
            and current_thread().ident == second_thread_id[0]
            and "from trip" in statement.lower()
            and "for update" in statement.lower()
        ):
            second_query.set()

    def first() -> None:
        def hold(context):
            context.plan.needs_refresh = True
            first_locked.set()
            assert release_first.wait(5), "coordinator did not release first writer"

        first_kernel.execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=hold
        )

    def second() -> None:
        second_thread_id.append(current_thread().ident or 0)

        def observe(context):
            second_seen.append(context.plan.needs_refresh)
            second_entered.set()

        second_kernel.execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=observe
        )

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        first_worker, first_errors = _thread(first)
        assert first_locked.wait(5)
        second_worker, second_errors = _thread(second)
        assert second_query.wait(5)
        assert not second_entered.is_set()
        release_first.set()
        _join(first_worker, first_errors)
        _join(second_worker, second_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    assert second_seen == [True]


def test_stale_request_identity_map_cannot_contaminate_fresh_kernel(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    stale_session = factory()
    stale_plan = stale_session.get(Plan, seed.plan_id)
    assert stale_plan.needs_refresh is False
    kernel = PlanTransactionKernel(factory)
    try:
        kernel.execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda context: setattr(context.plan, "needs_refresh", True),
        )
        seen = kernel.execute(
            scope=_account_scope(seed),
            credential=_account_proof(seed),
            operation=lambda context: context.plan.needs_refresh,
        )
    finally:
        stale_session.close()

    assert stale_plan.needs_refresh is False
    assert seen is True


def test_removed_membership_after_preliminary_read_is_rejected(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    preliminary = factory()
    try:
        assert preliminary.get(TripMembership, seed.account_membership_id).status == "joined"
    finally:
        preliminary.close()
    with factory.begin() as session:
        session.get(TripMembership, seed.account_membership_id).status = "removed"

    with pytest.raises(MembershipNotAuthorized):
        PlanTransactionKernel(factory).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda _context: None
        )


def test_compliant_membership_removal_waits_for_authorized_kernel(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    kernel = PlanTransactionKernel(factory)
    authorized, release, removal_query, removal_done = Event(), Event(), Event(), Event()
    remover_thread_id: list[int] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if (
            remover_thread_id
            and current_thread().ident == remover_thread_id[0]
            and "from trip" in statement.lower()
            and "for update" in statement.lower()
        ):
            removal_query.set()

    def writer() -> None:
        def hold(_context):
            authorized.set()
            assert release.wait(5)

        kernel.execute(scope=_account_scope(seed), credential=_account_proof(seed), operation=hold)

    def remove() -> None:
        remover_thread_id.append(current_thread().ident or 0)
        with factory.begin() as session:
            session.scalar(select(Trip).where(Trip.id == seed.trip_id).with_for_update())
            membership = session.scalar(
                select(TripMembership)
                .where(TripMembership.id == seed.account_membership_id)
                .with_for_update()
            )
            membership.status = "removed"
        removal_done.set()

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        writer_thread, writer_errors = _thread(writer)
        assert authorized.wait(5)
        remover_thread, remover_errors = _thread(remove)
        assert removal_query.wait(5)
        assert not removal_done.is_set()
        release.set()
        _join(writer_thread, writer_errors)
        _join(remover_thread, remover_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    with factory() as session:
        assert session.get(TripMembership, seed.account_membership_id).status == "removed"


@pytest.mark.parametrize("kind", ["guest", "account"])
def test_revoked_credential_before_kernel_authorization_is_rejected(test_engine, kind):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as session:
        if kind == "guest":
            session.get(GuestSession, seed.guest_session_id).revoked_at = datetime.now(timezone.utc)
            scope, proof = _guest_scope(seed), _guest_proof(seed)
        else:
            session.get(AuthSession, seed.auth_session_id).revoked_at = datetime.now(timezone.utc)
            scope, proof = _account_scope(seed), _account_proof(seed)

    with pytest.raises(InvalidCredential):
        PlanTransactionKernel(factory).execute(scope=scope, credential=proof, operation=lambda _context: None)


@pytest.mark.parametrize("kind", ["account", "guest"])
def test_credential_only_logout_lock_waits_then_kernel_fails_closed(test_engine, kind):
    """Match the public logout's credential-only update without changing it.

    The revoker intentionally takes no Trip lock. The kernel has already
    acquired its canonical Trip lock and must then wait on the credential row;
    after logout commits, fresh authorization must reject the command.
    """

    seed = _seed_committed(test_engine)
    tracking_factory = sessionmaker(
        bind=test_engine, expire_on_commit=False, future=True, class_=TrackingSession
    )
    revoker_sessions: list[TrackingSession] = []
    kernel_sessions: list[TrackingSession] = []
    revoker_locked, release_revoker = Event(), Event()
    credential_query, callback_entered, rejected, revoker_committed = (
        Event(),
        Event(),
        Event(),
        Event(),
    )
    kernel_thread_id: list[int] = []

    def revoker_factory() -> TrackingSession:
        session = tracking_factory()
        revoker_sessions.append(session)
        return session

    def kernel_factory() -> TrackingSession:
        session = tracking_factory()
        kernel_sessions.append(session)
        return session

    model, credential_id, scope, proof = (
        (AuthSession, seed.auth_session_id, _account_scope(seed), _account_proof(seed))
        if kind == "account"
        else (GuestSession, seed.guest_session_id, _guest_scope(seed), _guest_proof(seed))
    )

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = statement.lower()
        table = "auth_session" if kind == "account" else "guest_session"
        if (
            kernel_thread_id
            and current_thread().ident == kernel_thread_id[0]
            and f"from {table}" in normalized
            and "for update" in normalized
        ):
            credential_query.set()

    def revoke_like_existing_logout() -> None:
        session = revoker_factory()
        try:
            with session.begin():
                credential = session.scalar(select(model).where(model.id == credential_id).with_for_update())
                assert credential is not None
                revoker_locked.set()
                assert release_revoker.wait(5), "coordinator did not release logout revoker"
                credential.revoked_at = datetime.now(timezone.utc)
            revoker_committed.set()
        finally:
            session.close()

    def command() -> None:
        kernel_thread_id.append(current_thread().ident or 0)
        try:
            PlanTransactionKernel(kernel_factory).execute(
                scope=scope,
                credential=proof,
                operation=lambda _context: callback_entered.set(),
            )
        except InvalidCredential:
            rejected.set()
        else:
            raise AssertionError("kernel command must not survive credential revocation")

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        revoker_worker, revoker_errors = _thread(revoke_like_existing_logout)
        assert revoker_locked.wait(5)
        kernel_worker, kernel_errors = _thread(command)
        assert credential_query.wait(5)
        assert kernel_worker.is_alive()
        assert not callback_entered.is_set()
        release_revoker.set()
        _join(revoker_worker, revoker_errors)
        _join(kernel_worker, kernel_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    assert revoker_committed.is_set()
    assert rejected.is_set()
    assert not callback_entered.is_set()
    assert revoker_sessions and revoker_sessions[0].was_closed is True
    assert kernel_sessions and kernel_sessions[0].was_closed is True


@pytest.mark.parametrize("status", ["joined", "preferences_submitted"])
def test_account_invited_and_joined_memberships_match_current_api_policy(test_engine, status):
    seed = _seed_committed(test_engine, account_status=status)
    factory = _factory(test_engine)
    with factory() as session:
        user = session.get(User, seed.account_user_id)
        assert auth_service.membership_for_trip(session, user, seed.trip_id).status == status

    assert PlanTransactionKernel(factory).execute(
        scope=_account_scope(seed),
        credential=_account_proof(seed),
        operation=lambda context: context.actor.membership_id,
    ) == seed.account_membership_id


def test_removed_account_membership_matches_current_api_policy(test_engine):
    seed = _seed_committed(test_engine, account_status="removed")
    factory = _factory(test_engine)
    with factory() as session:
        user = session.get(User, seed.account_user_id)
        with pytest.raises(auth_service.TripMembershipRequired):
            auth_service.membership_for_trip(session, user, seed.trip_id)

    with pytest.raises(MembershipNotAuthorized):
        PlanTransactionKernel(factory).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda _context: None
        )


@pytest.mark.parametrize("kind", ["guest", "account"])
def test_compliant_credential_revocation_orders_after_kernel_then_denies_later_write(test_engine, kind):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    kernel = PlanTransactionKernel(factory)
    scope, proof = (_guest_scope(seed), _guest_proof(seed)) if kind == "guest" else (_account_scope(seed), _account_proof(seed))
    locked, release, revoke_query, revoked = Event(), Event(), Event(), Event()
    revoker_thread_id: list[int] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if (
            revoker_thread_id
            and current_thread().ident == revoker_thread_id[0]
            and "from trip" in statement.lower()
            and "for update" in statement.lower()
        ):
            revoke_query.set()

    def writer() -> None:
        def hold(_context):
            locked.set()
            assert release.wait(5)

        kernel.execute(scope=scope, credential=proof, operation=hold)

    def revoke() -> None:
        revoker_thread_id.append(current_thread().ident or 0)
        with factory.begin() as session:
            session.scalar(select(Trip).where(Trip.id == seed.trip_id).with_for_update())
            model, identifier = (
                (GuestSession, seed.guest_session_id)
                if kind == "guest"
                else (AuthSession, seed.auth_session_id)
            )
            credential = session.scalar(select(model).where(model.id == identifier).with_for_update())
            credential.revoked_at = datetime.now(timezone.utc)
        revoked.set()

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        writer_thread, writer_errors = _thread(writer)
        assert locked.wait(5)
        revoker_thread, revoker_errors = _thread(revoke)
        assert revoke_query.wait(5)
        assert not revoked.is_set()
        release.set()
        _join(writer_thread, writer_errors)
        _join(revoker_thread, revoker_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    with pytest.raises(InvalidCredential):
        kernel.execute(scope=scope, credential=proof, operation=lambda _context: None)


def test_missing_trip_missing_plan_duplicate_plans_and_role_are_controlled(test_engine):
    factory = _factory(test_engine)
    missing = _seed_committed(test_engine, with_plan=False)
    kernel = PlanTransactionKernel(factory)
    with pytest.raises(MissingPlan):
        kernel.execute(scope=_account_scope(missing), credential=_account_proof(missing), operation=lambda _context: None)
    with pytest.raises(TripNotFound):
        kernel.execute(
            scope=PreliminaryActorScope(
                trip_id="missing-trip",
                membership_id=missing.account_membership_id,
                credential_kind="account",
                expected_user_id=missing.account_user_id,
            ),
            credential=_account_proof(missing),
            operation=lambda _context: None,
        )

    duplicate = _seed_committed(test_engine)
    with factory.begin() as session:
        session.add(Plan(trip_id=duplicate.trip_id))
    with pytest.raises(DuplicatePlans):
        kernel.execute(scope=_account_scope(duplicate), credential=_account_proof(duplicate), operation=lambda _context: None)
    role_seed = _seed_committed(test_engine)
    with pytest.raises(InsufficientRole):
        kernel.execute(
            scope=_account_scope(role_seed),
            credential=_account_proof(role_seed),
            required_role="organizer",
            operation=lambda _context: None,
        )


def test_callback_failure_rolls_back_and_session_closes(test_engine):
    seed = _seed_committed(test_engine)
    closed_sessions: list[TrackingSession] = []
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True, class_=TrackingSession)

    def tracked_factory() -> TrackingSession:
        session = factory()
        closed_sessions.append(session)
        return session

    def fail(context) -> None:
        context.plan.needs_refresh = True
        raise ControlledFailure()

    with pytest.raises(ControlledFailure):
        PlanTransactionKernel(tracked_factory).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=fail
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).needs_refresh is False
    assert closed_sessions and closed_sessions[0].was_closed is True


def test_callback_can_return_detached_primitives_containers_and_explicit_dto(test_engine):
    seed = _seed_committed(test_engine)
    kernel = PlanTransactionKernel(_factory(test_engine))

    primitive_result = kernel.execute(
        scope=_account_scope(seed),
        credential=_account_proof(seed),
        operation=lambda context: {
            "plan_id": context.plan.id,
            "values": [1, (True, "safe")],
        },
    )
    dto_result = kernel.execute(
        scope=_account_scope(seed),
        credential=_account_proof(seed),
        operation=lambda context: PlanResult(plan_id=context.plan.id, refreshed=context.plan.needs_refresh),
    )

    assert primitive_result == {"plan_id": seed.plan_id, "values": [1, (True, "safe")]}
    assert dto_result == PlanResult(plan_id=seed.plan_id, refreshed=False)


@pytest.mark.parametrize("result_kind", ["orm", "context", "nested"])
def test_callback_cannot_return_transaction_owned_state(test_engine, result_kind):
    seed = _seed_committed(test_engine)

    def operation(context):
        if result_kind == "orm":
            return context.plan
        if result_kind == "context":
            return context
        return {"nested": ["safe", (context.plan,)]}

    with pytest.raises(UnsafeCallbackResult):
        PlanTransactionKernel(_factory(test_engine)).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=operation
        )


def test_unsafe_callback_result_rolls_back_staged_changes_and_closes_session(test_engine):
    seed = _seed_committed(test_engine)
    closed_sessions: list[TrackingSession] = []
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True, class_=TrackingSession)

    def tracked_factory() -> TrackingSession:
        session = factory()
        closed_sessions.append(session)
        return session

    def leak_orm(context):
        context.plan.needs_refresh = True
        return context.plan

    with pytest.raises(UnsafeCallbackResult):
        PlanTransactionKernel(tracked_factory).execute(
            scope=_account_scope(seed), credential=_account_proof(seed), operation=leak_orm
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).needs_refresh is False
    assert closed_sessions and closed_sessions[0].was_closed is True


def test_lock_timeout_is_typed_and_failed_session_is_closed(test_engine):
    seed = _seed_committed(test_engine)
    factory = _factory(test_engine)
    holder = PlanTransactionKernel(factory)
    locked, release = Event(), Event()

    def hold() -> None:
        def operation(_context):
            locked.set()
            assert release.wait(5)

        holder.execute(scope=_account_scope(seed), credential=_account_proof(seed), operation=operation)

    worker, errors = _thread(hold)
    assert locked.wait(5)
    closed_sessions: list[TrackingSession] = []
    tracking_factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True, class_=TrackingSession)

    def tracked_factory() -> TrackingSession:
        session = tracking_factory()
        closed_sessions.append(session)
        return session

    try:
        with pytest.raises(LockUnavailable):
            PlanTransactionKernel(tracked_factory, lock_timeout_ms=100).execute(
                scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda _context: None
            )
    finally:
        release.set()
        _join(worker, errors)

    assert closed_sessions and closed_sessions[0].was_closed is True
    assert PlanTransactionKernel(factory).execute(
        scope=_account_scope(seed), credential=_account_proof(seed), operation=lambda context: context.plan.id
    ) == seed.plan_id


def test_kernel_has_no_default_on_public_route_activation():
    api_source = Path(__file__).resolve().parents[1] / "app" / "api" / "main.py"
    text = api_source.read_text(encoding="utf-8")
    assert "PlanTransactionKernel" not in text
    assert 'CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES' in text
    assert 'os.getenv(_B2C_MANUAL_ITEM_GATE, "0")' in text
    assert 'if _b2c_manual_item_gate_enabled()' in text


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [("40P01", "RetriableDeadlock"), ("40001", "RetriableSerializationFailure")],
)
def test_postgresql_retryable_error_classification(sqlstate, expected):
    class PgFailure(Exception):
        pass

    original = PgFailure()
    original.sqlstate = sqlstate
    error = OperationalError("statement", {}, original)
    classified = __import__(
        "app.domain.transactions.kernel", fromlist=["classify_database_error"]
    ).classify_database_error(error)
    assert type(classified).__name__ == expected


class ControlledFailure(Exception):
    pass


class TrackingSession(Session):
    was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()
