from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread
from typing import Callable

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AuthSession,
    GuestSession,
    MutationIdempotency,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
    User,
)
from app.domain import auth as auth_service
from app.domain.transactions import (
    CommandResponse,
    CredentialProof,
    DurableCommandKernel,
    FingerprintVersionMismatch,
    HmacSigningKey,
    IdempotencyKeyReused,
    IdempotencyRecordExpired,
    IdempotentCommand,
    InvalidDurableCommand,
    InvalidIdempotencyKey,
    InvalidCredential,
    MembershipNotAuthorized,
    OutcomeContract,
    PlanTransactionKernel,
    PlanRevisionOverflow,
    PreliminaryActorScope,
    StalePlanRevision,
    StaticOutcomeContractRegistry,
    UnknownHmacKeyVersion,
    UnsafeDurableOutcome,
    UnsupportedFingerprintVersion,
    UnsupportedNoOp,
    UnsupportedOutcomeContract,
    UnsafeNoOpMutation,
)


_seed_records: list[tuple[str, tuple[str, ...]]] = []
_OPERATION_CODE = "plan.item.test-change.v1"
_DEFAULT_OUTCOME_FIELDS = frozenset(
    {
        "item_id",
        "kind",
        "legacy_field",
        "message",
        "ok",
        "private_debug",
        "safe",
        "state",
        "unsafe",
        "winner",
    }
)


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str
    item_id: str
    account_membership_id: str
    account_user_id: str
    account_token: str
    auth_session_id: str
    guest_membership_id: str
    guest_token: str
    guest_session_id: str


class FixtureKeyring:
    def __init__(self, active: HmacSigningKey, *verification: HmacSigningKey):
        self.active = active
        self.keys = {item.version: item for item in (active, *verification)}

    def active_key(self) -> HmacSigningKey:
        return self.active

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self.keys.get(version)


def _factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False, future=True)


def _seed(test_engine) -> Seed:
    factory = _factory(test_engine)
    stamp = str(datetime.now(timezone.utc).timestamp())
    account_token = f"account-durable-{stamp}"
    guest_token = f"gst_durable-{stamp}"
    with factory.begin() as session:
        account = User(name="Durable account", email=f"account-{stamp}@example.test")
        owner = User(name="Durable owner", email=f"owner-{stamp}@example.test")
        session.add_all([account, owner])
        session.flush()
        trip = Trip(name="Durable trip", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip)
        session.flush()
        account_membership = TripMembership(
            trip_id=trip.id, user_id=account.id, role="participant", status="joined"
        )
        guest_membership = TripMembership(
            trip_id=trip.id,
            user_id=None,
            guest_display_name="Durable guest",
            role="participant",
            join_method="invite_guest",
            status="joined",
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
        plan = Plan(trip_id=trip.id)
        session.add_all([auth_session, guest_session, plan])
        session.flush()
        item = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=date(2026, 10, 8),
            start_hour=12,
            duration_min=60,
            title="Durable item",
            place="Chicago",
        )
        session.add(item)
        session.flush()
        _seed_records.append((trip.id, (account.id, owner.id)))
        return Seed(
            trip_id=trip.id,
            plan_id=plan.id,
            item_id=item.id,
            account_membership_id=account_membership.id,
            account_user_id=account.id,
            account_token=account_token,
            auth_session_id=auth_session.id,
            guest_membership_id=guest_membership.id,
            guest_token=guest_token,
            guest_session_id=guest_session.id,
        )


@pytest.fixture(autouse=True)
def _cleanup_committed_rows(test_engine):
    _seed_records.clear()
    yield
    records = list(_seed_records)
    _seed_records.clear()
    if not records:
        return
    factory = _factory(test_engine)
    trip_ids = [trip_id for trip_id, _ in records]
    user_ids = [user_id for _, ids in records for user_id in ids]
    with factory.begin() as session:
        session.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(
            synchronize_session=False
        )
        session.query(GuestSession).filter(GuestSession.trip_id.in_(trip_ids)).delete(
            synchronize_session=False
        )
        session.query(PlanItem).filter(PlanItem.plan_id.in_(select(Plan.id).where(Plan.trip_id.in_(trip_ids)))).delete(
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


def _scope(seed: Seed, *, kind: str = "account") -> PreliminaryActorScope:
    if kind == "guest":
        return PreliminaryActorScope(
            trip_id=seed.trip_id, membership_id=seed.guest_membership_id, credential_kind="guest"
        )
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.account_membership_id,
        credential_kind="account",
        expected_user_id=seed.account_user_id,
    )


def _proof(seed: Seed, *, kind: str = "account") -> CredentialProof:
    return CredentialProof.from_bearer_token(seed.guest_token if kind == "guest" else seed.account_token)


def _command(seed: Seed, *, key: str = "durable-key", **changes: object) -> IdempotentCommand:
    values: dict[str, object] = {
        "operation_code": _OPERATION_CODE,
        "resource_kind": "plan_item",
        "resource_id": seed.item_id,
        "idempotency_key": key,
        "expected_plan_revision": 0,
        "command_fields": {"title": "Durable update", "hours": 15},
        "fingerprint_version": 1,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
    }
    values.update(changes)
    return IdempotentCommand(**values)  # type: ignore[arg-type]


def _outcome_contracts(*contracts: OutcomeContract) -> StaticOutcomeContractRegistry:
    return StaticOutcomeContractRegistry(contracts)


def _default_outcome_contracts() -> StaticOutcomeContractRegistry:
    return _outcome_contracts(OutcomeContract(_OPERATION_CODE, 1, _DEFAULT_OUTCOME_FIELDS))


def _kernel(
    test_engine,
    keyring: FixtureKeyring | None = None,
    outcome_contracts: StaticOutcomeContractRegistry | None = None,
) -> DurableCommandKernel:
    keyring = keyring or FixtureKeyring(HmacSigningKey("fixture-v1", b"fixture-key-v1"))
    return DurableCommandKernel(
        PlanTransactionKernel(_factory(test_engine)),
        keyring,
        outcome_contracts or _default_outcome_contracts(),
    )


def _mutation(payload: dict[str, object], *, entered: Event | None = None, release: Event | None = None):
    def run(context, _fields) -> CommandResponse:
        context.plan.needs_refresh = True
        if entered is not None:
            entered.set()
        if release is not None:
            assert release.wait(5), "coordinator did not release mutation"
        return CommandResponse(status=201, payload=payload)

    return run


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


def test_first_success_commits_effect_one_revision_and_completed_ledger(test_engine):
    seed = _seed(test_engine)
    result = _kernel(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(seed),
        mutation=_mutation({"kind": "updated", "item_id": seed.item_id}),
    )

    assert result.status == 201
    assert result.payload == {"kind": "updated", "item_id": seed.item_id}
    assert result.plan_revision == 1
    assert result.replayed is False
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        assert row is not None
        assert row.resulting_plan_revision == 1
        assert row.outcome_json == result.payload


def test_identical_retry_replays_stored_snapshot_without_second_mutation(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    calls = 0

    def mutation(context, _fields) -> CommandResponse:
        nonlocal calls
        calls += 1
        context.plan.needs_refresh = True
        return CommandResponse(201, {"item_id": seed.item_id, "state": "original"})

    command = _command(seed)
    first = kernel.execute(scope=_scope(seed), credential=_proof(seed), command=command, mutation=mutation)
    with _factory(test_engine).begin() as session:
        session.get(PlanItem, seed.item_id).title = "Later state"
        session.delete(session.get(PlanItem, seed.item_id))
    replay = kernel.execute(scope=_scope(seed), credential=_proof(seed), command=command, mutation=mutation)

    assert calls == 1
    assert replay == type(replay)(201, {"item_id": seed.item_id, "state": "original"}, 1, True)
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


def test_same_key_conflicts_for_changed_operation_resource_body_and_expected_revision(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    first = _command(seed)
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=first, mutation=_mutation({"ok": True})
    )
    variants = [
        _command(seed, operation_code="plan.item.other.v1"),
        _command(seed, resource_id="different-item"),
        _command(seed, command_fields={"title": "Different"}),
        _command(seed, expected_plan_revision=1),
    ]
    for variant in variants:
        with pytest.raises(IdempotencyKeyReused):
            kernel.execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=variant,
                mutation=lambda _context, _fields: pytest.fail("conflicting callback ran"),
            )


def test_new_key_stale_revision_is_rejected_without_effect_or_ledger(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    with _factory(test_engine).begin() as session:
        session.get(Plan, seed.plan_id).revision = 1

    with pytest.raises(StalePlanRevision):
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, key="new-stale-key"),
            mutation=lambda _context, _fields: pytest.fail("stale callback ran"),
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 0


def test_plan_revision_overflow_rolls_back_callback_and_ledger_write(test_engine):
    seed = _seed(test_engine)
    maximum = 9_223_372_036_854_775_807
    with _factory(test_engine).begin() as session:
        session.get(Plan, seed.plan_id).revision = maximum
    called = False
    def mutation(context, _fields):
        nonlocal called
        called = True
        context.plan.needs_refresh = True
        return CommandResponse(200, {"ok": True})
    with pytest.raises(PlanRevisionOverflow):
        _kernel(test_engine).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, expected_plan_revision=maximum),
            mutation=mutation,
        )
    assert called is True
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == maximum
        assert session.query(MutationIdempotency).count() == 0


def test_fingerprint_version_contract_supports_only_format_one_and_fails_closed(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    command = _command(seed, fingerprint_version=1)
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command, mutation=_mutation({"ok": True})
    )
    assert kernel.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(seed, fingerprint_version=1),
        mutation=lambda _context, _fields: pytest.fail("supported replay callback ran"),
    ).replayed is True
    with pytest.raises(UnsupportedFingerprintVersion):
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, key="unsupported-incoming", fingerprint_version=2),
            mutation=lambda _context, _fields: pytest.fail("unsupported callback ran"),
        )
    with _factory(test_engine).begin() as session:
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        row.fingerprint_version = 2
    with pytest.raises(UnsupportedFingerprintVersion):
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, fingerprint_version=1),
            mutation=lambda _context, _fields: pytest.fail("historical-format callback ran"),
        )


def test_command_fields_are_immutable_snapshot_used_by_fingerprint_and_callback(test_engine):
    seed = _seed(test_engine)
    supplied = {"nested": {"label": "Cafe\u0301", "values": [15.0, None]}}
    command = _command(seed, command_fields=supplied)
    supplied["nested"]["label"] = "caller mutation before execution"
    supplied["nested"]["values"].append("caller mutation before execution")
    seen = []

    def mutation(context, fields) -> CommandResponse:
        context.plan.needs_refresh = True
        seen.append(fields)
        assert fields["nested"]["label"] == "Café"
        assert fields["nested"]["values"] == (15, None)
        supplied["nested"]["label"] = "caller mutation during callback"
        with pytest.raises(TypeError):
            fields["nested"]["label"] = "not allowed"
        with pytest.raises(AttributeError):
            fields["nested"]["values"].append("not allowed")
        return CommandResponse(201, {"ok": True})

    _kernel(test_engine).execute(
        scope=_scope(seed), credential=_proof(seed), command=command, mutation=mutation
    )
    assert seen[0]["nested"]["label"] == "Café"
    equivalent = _command(
        seed, command_fields={"nested": {"label": "Café", "values": [15, None]}}
    )
    assert _kernel(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=equivalent,
        mutation=lambda _context, _fields: pytest.fail("snapshot-equivalent callback ran"),
    ).replayed is True


def test_concurrent_identical_commands_execute_once_then_replay(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    entered, release, second_started, second_trip_lock_query = Event(), Event(), Event(), Event()
    calls: list[str] = []
    results = []
    second_thread_id: list[int] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = statement.lower()
        if (
            second_thread_id
            and current_thread().ident == second_thread_id[0]
            and "from trip" in normalized
            and "for update" in normalized
        ):
            second_trip_lock_query.set()

    def first() -> None:
        def mutation(context, _fields) -> CommandResponse:
            calls.append("first")
            context.plan.needs_refresh = True
            entered.set()
            assert release.wait(5)
            return CommandResponse(201, {"item_id": seed.item_id, "message": "once"})

        results.append(
            kernel.execute(
                scope=_scope(seed), credential=_proof(seed), command=_command(seed), mutation=mutation
            )
        )

    def second() -> None:
        second_thread_id.append(current_thread().ident or 0)
        second_started.set()
        results.append(
            kernel.execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=_command(seed),
                mutation=lambda _context, _fields: pytest.fail("replayed callback ran"),
            )
        )

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        first_worker, first_errors = _thread(first)
        assert entered.wait(5)
        second_worker, second_errors = _thread(second)
        assert second_started.wait(5)
        assert second_trip_lock_query.wait(5)
        assert second_worker.is_alive()
        release.set()
        _join(first_worker, first_errors)
        _join(second_worker, second_errors)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)

    assert calls == ["first"]
    assert {result.replayed for result in results} == {False, True}
    assert {result.payload["message"] for result in results} == {"once"}
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


def test_concurrent_same_key_different_payload_waits_then_conflicts(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    entered, release, conflict = Event(), Event(), Event()

    def first() -> None:
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed),
            mutation=_mutation({"winner": True}, entered=entered, release=release),
        )

    def conflicting() -> None:
        try:
            kernel.execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=_command(seed, command_fields={"title": "different"}),
                mutation=lambda _context, _fields: pytest.fail("conflict callback ran"),
            )
        except IdempotencyKeyReused:
            conflict.set()
        else:
            raise AssertionError("same key with changed payload must conflict")

    first_worker, first_errors = _thread(first)
    assert entered.wait(5)
    second_worker, second_errors = _thread(conflicting)
    assert second_worker.is_alive()
    release.set()
    _join(first_worker, first_errors)
    _join(second_worker, second_errors)
    assert conflict.is_set()
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


@pytest.mark.parametrize("kind", ["account", "guest"])
def test_current_authorization_is_required_before_replay(test_engine, kind):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    command = _command(seed)
    kernel.execute(
        scope=_scope(seed, kind=kind),
        credential=_proof(seed, kind=kind),
        command=command,
        mutation=_mutation({"ok": True}),
    )
    with _factory(test_engine).begin() as session:
        if kind == "account":
            session.get(AuthSession, seed.auth_session_id).revoked_at = datetime.now(timezone.utc)
        else:
            session.get(GuestSession, seed.guest_session_id).revoked_at = datetime.now(timezone.utc)

    with pytest.raises(InvalidCredential):
        kernel.execute(
            scope=_scope(seed, kind=kind),
            credential=_proof(seed, kind=kind),
            command=command,
            mutation=lambda _context, _fields: pytest.fail("unauthorized replay callback ran"),
        )


def test_removed_membership_never_receives_historical_replay(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    command = _command(seed)
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command, mutation=_mutation({"ok": True})
    )
    with _factory(test_engine).begin() as session:
        session.get(TripMembership, seed.account_membership_id).status = "removed"
    with pytest.raises(MembershipNotAuthorized):
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=command,
            mutation=lambda _context, _fields: pytest.fail("removed replay callback ran"),
        )


class CheckpointFailure(DurableCommandKernel):
    def __init__(self, *args, stage: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage = stage

    def _checkpoint(self, stage: str) -> None:
        if stage == self.stage:
            raise RuntimeError(f"injected {stage}")


@pytest.mark.parametrize("stage", ["after_domain_effect", "after_revision_increment", "after_ledger_insert"])
def test_injected_failures_roll_back_effect_revision_and_ledger(test_engine, stage):
    seed = _seed(test_engine)
    durable = CheckpointFailure(
        PlanTransactionKernel(_factory(test_engine)),
        FixtureKeyring(HmacSigningKey("fixture-v1", b"fixture-key-v1")),
        _default_outcome_contracts(),
        stage=stage,
    )
    with pytest.raises(RuntimeError):
        durable.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed),
            mutation=_mutation({"ok": True}),
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).needs_refresh is False
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).count() == 0


def test_hmac_rotation_replays_with_stored_key_and_unknown_version_fails_closed(test_engine):
    seed = _seed(test_engine)
    old = HmacSigningKey("fixture-v1", b"old-key")
    new = HmacSigningKey("fixture-v2", b"new-key")
    command = _command(seed)
    original = _kernel(test_engine, FixtureKeyring(old)).execute(
        scope=_scope(seed), credential=_proof(seed), command=command, mutation=_mutation({"safe": "old"})
    )
    rotated = _kernel(test_engine, FixtureKeyring(new, old)).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=lambda _context, _fields: pytest.fail("rotation replay callback ran"),
    )
    assert rotated.payload == original.payload
    with pytest.raises(UnknownHmacKeyVersion):
        _kernel(test_engine, FixtureKeyring(new)).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=command,
            mutation=lambda _context, _fields: pytest.fail("unknown-key callback ran"),
        )


def test_safe_response_rejects_unsafe_values_and_rolls_back(test_engine):
    seed = _seed(test_engine)
    unsafe_values = [
        lambda context: context.plan,
        lambda _context: CredentialProof.from_bearer_token("secret"),
        lambda context: {"nested": [context.plan]},
        lambda _context: float("nan"),
        lambda _context: b"binary",
    ]
    for index, value_for in enumerate(unsafe_values):
        def mutation(context, _fields, value_for=value_for) -> CommandResponse:
            context.plan.needs_refresh = True
            return CommandResponse(201, {"unsafe": value_for(context)})

        with pytest.raises(UnsafeDurableOutcome):
            _kernel(test_engine).execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=_command(seed, key=f"unsafe-{index}"),
                mutation=mutation,
            )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.get(Plan, seed.plan_id).needs_refresh is False
        assert session.query(MutationIdempotency).count() == 0


def test_safe_response_rejects_dynamic_dto_and_retains_noop_without_revision_increment(test_engine):
    seed = _seed(test_engine)

    def dynamic(context, _fields) -> CommandResponse:
        context.plan.needs_refresh = True
        response = CommandResponse(201, {"ok": True})
        object.__setattr__(response, "debug", "undeclared")
        return response

    with pytest.raises(UnsafeDurableOutcome):
        _kernel(test_engine).execute(
            scope=_scope(seed), credential=_proof(seed), command=_command(seed), mutation=dynamic
        )
    noop = _kernel(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(seed, key="no-op"),
        mutation=lambda _context, _fields: CommandResponse(200, {"ok": True}, state_changed=False),
    )
    assert noop.plan_revision == 0
    assert noop.replayed is False
    replay = _kernel(test_engine).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=_command(seed, key="no-op"),
        mutation=lambda _context, _fields: pytest.fail("no-op replay callback ran"),
    )
    assert replay.replayed is True and replay.plan_revision == 0
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).count() == 1


@pytest.mark.parametrize(
    "kind", ["dirty", "flushed", "insert", "delete", "commented_update", "cte_update"]
)
def test_false_noop_with_domain_mutation_rolls_back_effect_and_ledger(test_engine, kind):
    seed = _seed(test_engine)
    durable = _kernel(test_engine)

    def mutation(session, _context, _fields) -> CommandResponse:
        item = session.get(PlanItem, seed.item_id)
        assert item is not None
        if kind in {"dirty", "flushed"}:
            item.title = "must roll back"
            if kind == "flushed":
                session.flush()
        elif kind == "insert":
            session.add(
                PlanItem(
                    plan_id=seed.plan_id,
                    day_index=2,
                    day_date=date(2026, 10, 9),
                    start_hour=15,
                    duration_min=30,
                    title="must roll back",
                    place="test",
                )
            )
            session.flush()
        elif kind == "delete":
            session.delete(item)
            session.flush()
        elif kind == "commented_update":
            session.execute(
                text("/* callback comment */ UPDATE plan_item SET title = :title WHERE id = :item_id"),
                {"title": "must roll back", "item_id": seed.item_id},
            )
        else:
            session.execute(
                text(
                    "WITH callback_value AS (SELECT CAST(:title AS text) AS title) "
                    "UPDATE plan_item SET title = callback_value.title "
                    "FROM callback_value WHERE plan_item.id = :item_id"
                ),
                {"title": "must roll back", "item_id": seed.item_id},
            )
        return CommandResponse(200, {"ok": True}, state_changed=False)

    with pytest.raises(UnsafeNoOpMutation):
        durable._execute_with_session_mutation(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, key=f"false-noop-{kind}"),
            mutation=mutation,
        )
    with _factory(test_engine)() as session:
        item = session.get(PlanItem, seed.item_id)
        assert item is not None and item.title == "Durable item"
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(PlanItem).filter(PlanItem.plan_id == seed.plan_id).count() == 1
        assert session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_server_owned_outcome_contract_rejects_extra_top_level_fields(test_engine):
    seed = _seed(test_engine)
    strict_contracts = _outcome_contracts(
        OutcomeContract(_OPERATION_CODE, 1, frozenset({"item_id"}))
    )
    assert "allowed_response_fields" not in IdempotentCommand.__dataclass_fields__
    with pytest.raises(UnsafeDurableOutcome):
        _kernel(test_engine, outcome_contracts=strict_contracts).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed),
            mutation=_mutation({"item_id": seed.item_id, "private_debug": "reject"}),
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).count() == 0


def test_replay_rejects_stored_payload_outside_trusted_outcome_contract(test_engine):
    seed = _seed(test_engine)
    strict_contracts = _outcome_contracts(
        OutcomeContract(_OPERATION_CODE, 1, frozenset({"item_id"}))
    )
    command = _command(seed)
    _kernel(test_engine, outcome_contracts=strict_contracts).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=_mutation({"item_id": seed.item_id}),
    )
    with _factory(test_engine).begin() as session:
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        assert row is not None
        row.outcome_json = {"item_id": seed.item_id, "legacy_field": "stored earlier"}
    with pytest.raises(UnsafeDurableOutcome):
        _kernel(test_engine, outcome_contracts=strict_contracts).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed),
            mutation=lambda _context, _fields: pytest.fail("disallowed stored payload was replayed"),
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


def test_replay_rejects_historical_outcome_disallowed_by_current_policy(test_engine):
    seed = _seed(test_engine)
    v1 = OutcomeContract(_OPERATION_CODE, 1, frozenset({"item_id", "legacy_field"}))
    v2 = OutcomeContract(_OPERATION_CODE, 2, frozenset({"item_id"}))
    command = _command(seed)
    _kernel(test_engine, outcome_contracts=_outcome_contracts(v1)).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=_mutation({"item_id": seed.item_id, "legacy_field": "v1-safe"}),
    )

    with pytest.raises(UnsafeDurableOutcome):
        _kernel(test_engine, outcome_contracts=_outcome_contracts(v1, v2)).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=command,
            mutation=lambda _context, _fields: pytest.fail("historical replay callback ran"),
        )

    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


def test_replay_accepts_historical_outcome_that_also_satisfies_current_policy(test_engine):
    seed = _seed(test_engine)
    v1 = OutcomeContract(_OPERATION_CODE, 1, frozenset({"item_id"}))
    v2 = OutcomeContract(_OPERATION_CODE, 2, frozenset({"item_id", "future_safe_field"}))
    command = _command(seed)
    _kernel(test_engine, outcome_contracts=_outcome_contracts(v1)).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=_mutation({"item_id": seed.item_id}),
    )

    replay = _kernel(test_engine, outcome_contracts=_outcome_contracts(v1, v2)).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=lambda _context, _fields: pytest.fail("historical replay callback ran"),
    )
    assert replay.replayed is True
    assert replay.payload == {"item_id": seed.item_id}


def test_replay_fails_closed_when_stored_outcome_contract_is_not_registered(test_engine):
    seed = _seed(test_engine)
    v1 = OutcomeContract(_OPERATION_CODE, 1, frozenset({"item_id"}))
    v2 = OutcomeContract(_OPERATION_CODE, 2, frozenset({"item_id"}))
    command = _command(seed)
    _kernel(test_engine, outcome_contracts=_outcome_contracts(v1)).execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=_mutation({"item_id": seed.item_id}),
    )

    with pytest.raises(UnsupportedOutcomeContract):
        _kernel(test_engine, outcome_contracts=_outcome_contracts(v2)).execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=command,
            mutation=lambda _context, _fields: pytest.fail("unsupported historical callback ran"),
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


def test_new_command_fails_closed_without_a_server_owned_outcome_contract(test_engine):
    seed = _seed(test_engine)
    called = False

    def mutation(_context, _fields) -> CommandResponse:
        nonlocal called
        called = True
        return CommandResponse(201, {"item_id": seed.item_id})

    with pytest.raises(UnsupportedOutcomeContract):
        _kernel(test_engine, outcome_contracts=_outcome_contracts()).execute(
            scope=_scope(seed), credential=_proof(seed), command=_command(seed), mutation=mutation
        )
    assert called is False
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).count() == 0


def test_equivalent_canonical_command_forms_replay_but_semantic_change_conflicts(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    first = _command(seed, command_fields={"label": "Café", "hour": 15})
    equivalent = _command(seed, command_fields={"label": "Cafe\u0301", "hour": 15.0})
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=first, mutation=_mutation({"ok": "same"})
    )
    replay = kernel.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=equivalent,
        mutation=lambda _context, _fields: pytest.fail("equivalent canonical command ran twice"),
    )
    assert replay.replayed is True
    with pytest.raises(IdempotencyKeyReused):
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, command_fields={"label": "Cafe", "hour": 15}),
            mutation=lambda _context, _fields: pytest.fail("changed semantic command ran"),
        )


class CommitFailure(RuntimeError):
    pass


class FailingCommitSession(Session):
    pass


def test_commit_failure_rolls_back_effect_revision_and_ledger(test_engine):
    seed = _seed(test_engine)
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True, class_=FailingCommitSession)

    def fail_commit(_session) -> None:
        raise CommitFailure("injected before commit")

    event.listen(FailingCommitSession, "before_commit", fail_commit)
    try:
        with pytest.raises(CommitFailure):
            DurableCommandKernel(
                PlanTransactionKernel(factory),
                FixtureKeyring(HmacSigningKey("fixture-v1", b"fixture-key-v1")),
                _default_outcome_contracts(),
            ).execute(
                scope=_scope(seed),
                credential=_proof(seed),
                command=_command(seed),
                mutation=_mutation({"ok": True}),
            )
    finally:
        event.remove(FailingCommitSession, "before_commit", fail_commit)
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).needs_refresh is False
        assert session.get(Plan, seed.plan_id).revision == 0
        assert session.query(MutationIdempotency).count() == 0


def test_expired_retained_row_fails_closed_without_reexecution(test_engine):
    seed = _seed(test_engine)
    kernel = _kernel(test_engine)
    command = _command(seed, expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
    kernel.execute(
        scope=_scope(seed), credential=_proof(seed), command=command, mutation=_mutation({"ok": True})
    )
    with _factory(test_engine).begin() as session:
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(IdempotencyRecordExpired):
        kernel.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=command,
            mutation=lambda _context, _fields: pytest.fail("expired record callback ran"),
        )
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1
        assert session.query(MutationIdempotency).count() == 1


def test_new_command_rejects_already_expired_server_selected_retention(test_engine):
    seed = _seed(test_engine)
    with pytest.raises(InvalidDurableCommand):
        _command(seed, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))


def test_b2c_manual_item_integration_is_explicitly_feature_off_by_default() -> None:
    from pathlib import Path

    api_source = Path(__file__).resolve().parents[1] / "app" / "api" / "main.py"
    source = api_source.read_text(encoding="utf-8")
    assert "DurableCommandKernel" not in source
    assert 'CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES' in source
    assert 'os.getenv(_B2C_MANUAL_ITEM_GATE, "0")' in source
    assert 'if _b2c_manual_item_gate_enabled()' in source


class TrackingSession(Session):
    was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


def test_owned_sessions_close_for_success_replay_conflict_and_stale_paths(test_engine):
    seed = _seed(test_engine)
    tracked: list[TrackingSession] = []
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True, class_=TrackingSession)

    def tracked_factory() -> TrackingSession:
        session = factory()
        tracked.append(session)
        return session

    durable = DurableCommandKernel(
        PlanTransactionKernel(tracked_factory),
        FixtureKeyring(HmacSigningKey("fixture-v1", b"fixture-key-v1")),
        _default_outcome_contracts(),
    )
    command = _command(seed)
    durable.execute(
        scope=_scope(seed), credential=_proof(seed), command=command, mutation=_mutation({"ok": True})
    )
    durable.execute(
        scope=_scope(seed),
        credential=_proof(seed),
        command=command,
        mutation=lambda _context, _fields: pytest.fail("replay callback ran"),
    )
    with pytest.raises(IdempotencyKeyReused):
        durable.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, command_fields={"changed": True}),
            mutation=lambda _context, _fields: pytest.fail("conflict callback ran"),
        )
    with pytest.raises(StalePlanRevision):
        durable.execute(
            scope=_scope(seed),
            credential=_proof(seed),
            command=_command(seed, key="stale-after-success"),
            mutation=lambda _context, _fields: pytest.fail("stale callback ran"),
        )
    assert len(tracked) == 4
    assert all(session.was_closed for session in tracked)


@pytest.mark.parametrize("key", ["", "contains space", "x" * 129, "slash/not-allowed"])
def test_invalid_idempotency_keys_fail_before_database_use(test_engine, key):
    seed = _seed(test_engine)
    with pytest.raises(InvalidIdempotencyKey):
        _command(seed, key=key)
