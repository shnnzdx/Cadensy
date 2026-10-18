from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread
from time import monotonic

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.api import main as api
from app.db import session as db_session
from app.db.models import (
    AuthSession,
    DecisionRound,
    GuestSession,
    MutationIdempotency,
    Plan,
    PlanChange,
    PlanItem,
    Trip,
    TripMembership,
    UpdateNotice,
    User,
    Vote,
)
from app.domain import auth as auth_service
from app.domain.decisions import orchestrator as orch
from app.domain.transactions import (
    CredentialProof,
    DuplicatePlans,
    IdempotencyKeyReused,
    LockUnavailable,
    MissingPlan,
    PlanRevisionOverflow,
    PreliminaryActorScope,
    RetriableDeadlock,
    StalePlanRevision,
    TransactionDatabaseFailure,
    UnknownHmacKeyVersion,
    UnsafeDurableOutcome,
    UnsupportedOutcomeContract,
)
from app.domain.transactions import vote as protected_vote
from app.domain.transactions.durable import HmacSigningKey


_records: list[tuple[str, tuple[str, ...]]] = []


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str
    item_id: str
    round_id: str
    account_membership_id: str
    account_user_id: str
    account_token: str
    guest_membership_id: str
    guest_token: str


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _options() -> list[dict[str, object]]:
    return [
        {"id": "keep", "title": "Keep current", "patch": {}},
        {"id": "requested", "title": "Voted title", "patch": {"title": "Voted title"}},
        {"id": "split", "title": "Split option", "patch": {}},
    ]


def _seed(engine, *, kind: str = "normal", extra_members: int = 0) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    account_token, guest_token = f"vote-account-{stamp}", f"gst_vote-{stamp}"
    with _factory(engine).begin() as db:
        account = User(name="Vote account", email=f"vote-account-{stamp}@example.test")
        db.add(account)
        db.flush()
        trip = Trip(name="Vote trip", destination="Chicago", created_by_user_id=account.id)
        db.add(trip)
        db.flush()
        account_member = TripMembership(
            trip_id=trip.id, user_id=account.id, role="participant", status="joined"
        )
        guest_member = TripMembership(
            trip_id=trip.id,
            user_id=None,
            guest_display_name="Vote guest",
            role="participant",
            join_method="invite_guest",
            status="joined",
        )
        extras: list[User] = []
        extra_memberships: list[TripMembership] = []
        for index in range(extra_members):
            user = User(name=f"Vote extra {index}", email=f"vote-extra-{index}-{stamp}@example.test")
            extras.append(user)
        db.add_all((account_member, guest_member, *extras))
        db.flush()
        for user in extras:
            extra_memberships.append(
                TripMembership(trip_id=trip.id, user_id=user.id, role="participant", status="joined")
            )
        plan = Plan(trip_id=trip.id)
        db.add_all((*extra_memberships, plan))
        db.flush()
        db.add_all(
            (
                AuthSession(
                    user_id=account.id,
                    token_hash=auth_service.token_hash(account_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
                GuestSession(
                    membership_id=guest_member.id,
                    trip_id=trip.id,
                    token_hash=auth_service.token_hash(guest_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
            )
        )
        item = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=date(2026, 10, 8),
            start_hour=12,
            duration_min=60,
            title="Vote item",
            place="Chicago",
            settledness="touched" if kind == "normal" else "settled",
        )
        db.add(item)
        db.flush()
        round_ = DecisionRound(
            plan_item_id=item.id,
            kind=kind,
            options=_options(),
            reason="A verified reason" if kind == "reopen" else None,
            deadline=datetime.now(timezone.utc) + timedelta(hours=24),
            status="open",
            electorate_count=2 + extra_members,
        )
        db.add(round_)
        db.flush()
        _records.append((trip.id, tuple(user.id for user in (account, *extras))))
        return Seed(
            trip.id,
            plan.id,
            item.id,
            round_.id,
            account_member.id,
            account.id,
            account_token,
            guest_member.id,
            guest_token,
        )


@pytest.fixture(autouse=True)
def _cleanup(test_engine):
    _records.clear()
    yield
    rows, _records[:] = list(_records), []
    if not rows:
        return
    trip_ids = [trip_id for trip_id, _ in rows]
    user_ids = [user_id for _, ids in rows for user_id in ids]
    plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids))
    item_ids = select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids))
    round_ids = select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(item_ids))
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(UpdateNotice).filter(UpdateNotice.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(Vote).filter(Vote.round_id.in_(round_ids)).delete(synchronize_session=False)
        db.query(DecisionRound).filter(DecisionRound.plan_item_id.in_(item_ids)).delete(synchronize_session=False)
        db.query(PlanChange).filter(PlanChange.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        db.query(GuestSession).filter(GuestSession.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(PlanItem).filter(PlanItem.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        db.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)


def _scope(seed: Seed, kind: str = "account") -> PreliminaryActorScope:
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


def _command(seed: Seed, *, key: str = "vote-key", revision: int = 0, option: str = "requested"):
    return protected_vote.ProtectedVote(
        round_id=seed.round_id,
        idempotency_key=key,
        expected_plan_revision=revision,
        option_id=option,
    )


def _execute(engine, seed: Seed, command, *, kind: str = "account"):
    token = seed.guest_token if kind == "guest" else seed.account_token
    return protected_vote.execute_protected_vote(
        session_factory=_factory(engine),
        scope=_scope(seed, kind),
        credential=CredentialProof.from_bearer_token(token),
        command=command,
        keyring=protected_vote.StaticHmacKeyring(HmacSigningKey("test-v1", b"protected-vote-test-key")),
    )


def test_first_changed_replayed_and_genuine_no_op_votes_have_correct_revision(test_engine):
    seed = _seed(test_engine)
    first = _execute(test_engine, seed, _command(seed, key="first", option="requested"))
    changed = _execute(test_engine, seed, _command(seed, key="changed", revision=1, option="keep"))
    replay = _execute(test_engine, seed, _command(seed, key="changed", revision=1, option="keep"))
    no_op = _execute(test_engine, seed, _command(seed, key="no-op", revision=2, option="keep"))
    assert (first.plan_revision, changed.plan_revision, replay.replayed, no_op.plan_revision) == (1, 2, True, 2)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 2
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).one().option_id == "keep"
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 3


def test_final_vote_atomically_settles_round_with_audit_notice_revision_and_ledger(test_engine):
    seed = _seed(test_engine)
    _execute(test_engine, seed, _command(seed, key="account", option="requested"))
    final = _execute(test_engine, seed, _command(seed, key="guest", revision=1, option="requested"), kind="guest")
    replay = _execute(test_engine, seed, _command(seed, key="guest", revision=1, option="requested"), kind="guest")
    assert final.payload["status"] == "closed" and final.payload["winning_option_id"] == "requested"
    assert replay.replayed is True and replay.payload == final.payload
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 2
        assert db.get(PlanItem, seed.item_id).title == "Voted title"
        assert db.get(DecisionRound, seed.round_id).status == "closed"
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 2


def test_ordinary_tie_keeps_current_and_non_voter_does_not_settle(test_engine):
    seed = _seed(test_engine)
    first = _execute(test_engine, seed, _command(seed, option="requested"))
    assert first.payload["status"] == "open" and first.payload["responded"] == 1 and first.payload["total_members"] == 2
    final = _execute(test_engine, seed, _command(seed, key="guest", revision=1, option="split"), kind="guest")
    assert final.payload["winning_option_id"] == "keep"
    with _factory(test_engine)() as db:
        assert db.get(PlanItem, seed.item_id).title == "Vote item"
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).one().patch == {}


def test_reopened_round_requires_majority_of_all_members(test_engine):
    seed = _seed(test_engine, kind="reopen", extra_members=2)
    with _factory(test_engine).begin() as db:
        memberships = db.scalars(
            select(TripMembership).where(TripMembership.trip_id == seed.trip_id).order_by(TripMembership.id)
        ).all()
        # Pre-existing legacy votes represent two other voters; with four total,
        # three requested votes are the strict majority needed to overturn.
        for membership in memberships:
            if membership.id not in {seed.account_membership_id, seed.guest_membership_id}:
                db.add(Vote(round_id=seed.round_id, trip_membership_id=membership.id, option_id="requested"))
    _execute(test_engine, seed, _command(seed, key="account", option="requested"))
    final = _execute(test_engine, seed, _command(seed, key="guest", revision=1, option="keep"), kind="guest")
    assert final.payload["status"] == "closed" and final.payload["winning_option_id"] == "requested"
    with _factory(test_engine)() as db:
        assert db.get(PlanItem, seed.item_id).title == "Voted title"


@pytest.mark.parametrize("kind", ("normal", "reopen"))
def test_protected_unknown_option_is_rejected_without_any_persistent_effect(test_engine, kind):
    seed = _seed(test_engine, kind=kind)
    with pytest.raises(orch.InvalidVoteOption, match="Vote option"):
        _execute(test_engine, seed, _command(seed, key=f"unknown-{kind}", option="unknown-option"))
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.get(DecisionRound, seed.round_id).status == "open"
        assert db.get(PlanItem, seed.item_id).title == "Vote item"
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).count() == 0
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_two_legacy_unknown_votes_return_422_without_settlement_exception(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    account_headers = {"Authorization": f"Bearer {seed.account_token}", "X-Trip-Id": seed.trip_id}
    guest_headers = {"Authorization": f"Bearer {seed.guest_token}", "X-Trip-Id": seed.trip_id}
    with TestClient(api.app, raise_server_exceptions=False) as client:
        account = client.post(f"/api/rounds/{seed.round_id}/votes", headers=account_headers, json={"option_id": "unknown-option"})
        guest = client.post(f"/api/rounds/{seed.round_id}/votes", headers=guest_headers, json={"option_id": "unknown-option"})
    assert account.status_code == guest.status_code == 422
    with factory() as db:
        assert db.get(DecisionRound, seed.round_id).status == "open"
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).count() == 0
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 0


@pytest.mark.parametrize("body", ({}, {"option_id": ""}, {"option_id": "  "}, {"option_id": 7}))
def test_legacy_missing_or_malformed_vote_option_is_controlled_422(test_engine, monkeypatch, body):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    headers = {"Authorization": f"Bearer {seed.account_token}", "X-Trip-Id": seed.trip_id}
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(f"/api/rounds/{seed.round_id}/votes", headers=headers, json=body)
    assert response.status_code == 422
    with factory() as db:
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).count() == 0
        assert db.get(Plan, seed.plan_id).revision == 0


def test_same_key_with_different_valid_option_conflicts_without_second_vote(test_engine):
    seed = _seed(test_engine)
    _execute(test_engine, seed, _command(seed, key="same-key", option="requested"))
    with pytest.raises(IdempotencyKeyReused):
        _execute(test_engine, seed, _command(seed, key="same-key", option="keep"))
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).one().option_id == "requested"
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_reopened_round_without_strict_majority_preserves_original_decision(test_engine):
    seed = _seed(test_engine, kind="reopen", extra_members=2)
    with _factory(test_engine).begin() as db:
        memberships = db.scalars(
            select(TripMembership).where(TripMembership.trip_id == seed.trip_id).order_by(TripMembership.id)
        ).all()
        extras = [member for member in memberships if member.id not in {seed.account_membership_id, seed.guest_membership_id}]
        db.add_all(
            (
                Vote(round_id=seed.round_id, trip_membership_id=extras[0].id, option_id="requested"),
                Vote(round_id=seed.round_id, trip_membership_id=extras[1].id, option_id="keep"),
            )
        )
    _execute(test_engine, seed, _command(seed, key="account", option="requested"))
    final = _execute(test_engine, seed, _command(seed, key="guest", revision=1, option="keep"), kind="guest")
    assert final.payload["status"] == "closed" and final.payload["winning_option_id"] == "keep"
    with _factory(test_engine)() as db:
        assert db.get(PlanItem, seed.item_id).title == "Vote item"


def test_cross_trip_round_is_rejected_without_child_lock_or_durable_effect(test_engine):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        source = db.get(Trip, seed.trip_id)
        assert source is not None
        foreign_trip = Trip(name="Foreign vote", destination="Detroit", created_by_user_id=source.created_by_user_id)
        db.add(foreign_trip)
        db.flush()
        plan = Plan(trip_id=foreign_trip.id)
        db.add(plan)
        db.flush()
        item = PlanItem(plan_id=plan.id, day_index=1, day_date=date(2026, 10, 9), start_hour=12, duration_min=60, title="Foreign", place="Detroit")
        db.add(item)
        db.flush()
        foreign_round = DecisionRound(plan_item_id=item.id, kind="normal", options=_options(), deadline=datetime.now(timezone.utc) + timedelta(hours=1), status="open")
        db.add(foreign_round)
        db.flush()
        _records.append((foreign_trip.id, ()))
        foreign_round_id, foreign_plan_id = foreign_round.id, plan.id
    locks: list[str] = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if "decision_round" in normalized and "for update" in normalized and foreign_round_id in str(parameters):
            locks.append(normalized)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(protected_vote.VoteRoundMissing):
            _execute(
                test_engine,
                seed,
                protected_vote.ProtectedVote(
                    round_id=foreign_round_id,
                    idempotency_key="foreign",
                    expected_plan_revision=0,
                    option_id="requested",
                ),
            )
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    assert locks and "plan_item.plan_id" in locks[0]
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == db.get(Plan, foreign_plan_id).revision == 0
        assert db.query(Vote).filter(Vote.round_id == foreign_round_id).count() == 0
        assert db.query(MutationIdempotency).count() == 0


def test_competing_final_votes_block_at_trip_lock_and_commit_exactly_once(test_engine, monkeypatch):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        db.add(Vote(round_id=seed.round_id, trip_membership_id=seed.account_membership_id, option_id="requested"))
    entered, release = Event(), Event()
    first_lock, contender_reached, contender_acquired = Event(), Event(), Event()
    connections: dict[str, int] = {}
    original = protected_vote.orch.settle_round

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(protected_vote.orch, "settle_round", paused)

    def root_lock(statement: str) -> bool:
        normalized = " ".join(statement.lower().split())
        return "from trip " in normalized and "trip_membership" not in normalized and "for update" in normalized

    def identity(connection) -> int:
        raw = connection.connection
        return id(getattr(raw, "driver_connection", raw))

    def backend_pid(connection) -> int:
        raw = connection.connection
        driver = getattr(raw, "driver_connection", raw)
        return driver.info.backend_pid

    def before(conn, _cursor, statement, _parameters, _context, _executemany):
        if root_lock(statement):
            if current_thread().name == "vote-first":
                connections["first"] = identity(conn)
                connections["first_pid"] = backend_pid(conn)
            elif current_thread().name == "vote-second":
                connections["second"] = identity(conn)
                connections["second_pid"] = backend_pid(conn)
                contender_reached.set()

    def after(_conn, _cursor, statement, _parameters, _context, _executemany):
        if root_lock(statement):
            if current_thread().name == "vote-first":
                first_lock.set()
            elif current_thread().name == "vote-second":
                contender_acquired.set()

    outcomes: Queue[object] = Queue()
    def run(key: str):
        try:
            outcomes.put(_execute(test_engine, seed, _command(seed, key=key), kind="guest"))
        except BaseException as exc:
            outcomes.put(exc)

    event.listen(test_engine, "before_cursor_execute", before)
    event.listen(test_engine, "after_cursor_execute", after)
    try:
        first = Thread(target=lambda: run("final-one"), name="vote-first", daemon=True)
        first.start()
        assert first_lock.wait(5) and entered.wait(5)
        second = Thread(target=lambda: run("final-two"), name="vote-second", daemon=True)
        second.start()
        assert contender_reached.wait(5)
        assert not contender_acquired.is_set() and connections["first"] != connections["second"]
        # This is database-state evidence, not a timing assumption: the
        # contender's backend must report the first backend as its current
        # blocker before the coordinator releases the first transaction.
        blocker_observed = False
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with _factory(test_engine)() as observer:
                blockers = observer.scalar(
                    text("select pg_blocking_pids(:blocked_pid)"),
                    {"blocked_pid": connections["second_pid"]},
                )
            if connections["first_pid"] in (blockers or []):
                blocker_observed = True
                break
        assert blocker_observed, "PostgreSQL did not report the Trip-lock blocker"
        release.set()
        first.join(5)
        second.join(5)
    finally:
        event.remove(test_engine, "before_cursor_execute", before)
        event.remove(test_engine, "after_cursor_execute", after)
    assert contender_acquired.is_set() and not first.is_alive() and not second.is_alive()
    results = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert any(getattr(value, "replayed", None) is False for value in results)
    assert any(isinstance(value, StalePlanRevision) for value in results)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).count() == 2
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_final_vote_failure_rolls_back_vote_settlement_effects_revision_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        db.add(Vote(round_id=seed.round_id, trip_membership_id=seed.account_membership_id, option_id="requested"))
    original = protected_vote.DurableCommandKernel._checkpoint

    def fail(self, stage):
        if stage == "after_domain_effect":
            raise RuntimeError("injected vote failure")
        return original(self, stage)

    monkeypatch.setattr(protected_vote.DurableCommandKernel, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="injected vote failure"):
        _execute(test_engine, seed, _command(seed), kind="guest")
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.get(PlanItem, seed.item_id).title == "Vote item"
        assert db.get(DecisionRound, seed.round_id).status == "open"
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).count() == 1
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_guest_route_replay_revocation_and_feature_off_compatibility(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    headers = {
        "Authorization": f"Bearer {seed.guest_token}",
        "X-Trip-Id": seed.trip_id,
        "X-Expected-Plan-Revision": "malformed-ignored-while-off",
    }
    with TestClient(api.app) as client:
        legacy = client.post(f"/api/rounds/{seed.round_id}/votes", headers=headers, json={"option_id": "requested"})
    assert legacy.status_code == 200
    with factory.begin() as db:
        db.query(Vote).filter(Vote.round_id == seed.round_id).delete(synchronize_session=False)
        db.get(Plan, seed.plan_id).revision = 0
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_VOTE_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "vote-route-key")
    protected_headers = {**headers, "Idempotency-Key": "guest-vote", "X-Expected-Plan-Revision": "0"}
    with TestClient(api.app) as client:
        first = client.post(f"/api/rounds/{seed.round_id}/votes", headers=protected_headers, json={"option_id": "requested"})
        replay = client.post(f"/api/rounds/{seed.round_id}/votes", headers=protected_headers, json={"option_id": "requested"})
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    with factory.begin() as db:
        session = db.scalar(select(GuestSession).where(GuestSession.membership_id == seed.guest_membership_id))
        assert session is not None
        session.revoked_at = datetime.now(timezone.utc)
    with TestClient(api.app) as client:
        revoked = client.post(f"/api/rounds/{seed.round_id}/votes", headers={**protected_headers, "Idempotency-Key": "revoked"}, json={"option_id": "keep"})
    assert revoked.status_code == 401
    with factory.begin() as db:
        guest = db.get(TripMembership, seed.guest_membership_id)
        session = db.scalar(select(GuestSession).where(GuestSession.membership_id == seed.guest_membership_id))
        assert guest is not None and session is not None
        session.revoked_at = None
        guest.status = "removed"
    with TestClient(api.app) as client:
        removed = client.post(f"/api/rounds/{seed.round_id}/votes", headers={**protected_headers, "Idempotency-Key": "removed"}, json={"option_id": "keep"})
    assert removed.status_code in {401, 403}


def test_vote_production_gate_rejects_before_legacy_or_protected_mutation(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_VOTE_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "vote-route-key")
    monkeypatch.setenv("APP_ENV", "production")
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "production-vote",
        "X-Expected-Plan-Revision": "0",
    }
    with TestClient(api.app) as client:
        response = client.post(f"/api/rounds/{seed.round_id}/votes", headers=headers, json={"option_id": "requested"})
    assert response.status_code == 503
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(Vote).filter(Vote.round_id == seed.round_id).count() == 0


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (LockUnavailable("private-db-detail"), 503),
        (RetriableDeadlock("private-db-detail"), 503),
        (MissingPlan("private-db-detail"), 404),
        (DuplicatePlans("private-db-detail"), 409),
        (UnknownHmacKeyVersion("private-db-detail"), 409),
        (UnsupportedOutcomeContract("private-db-detail"), 409),
        (PlanRevisionOverflow("private-db-detail"), 409),
        (UnsafeDurableOutcome("private-db-detail"), 409),
        (TransactionDatabaseFailure("private-db-detail"), 503),
    ],
)
def test_vote_route_maps_typed_errors_without_diagnostics(test_engine, monkeypatch, failure, status):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_VOTE_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "vote-route-key")

    def fail(**_kwargs):
        raise failure

    monkeypatch.setattr(protected_vote, "execute_protected_vote", fail)
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "typed-vote-error",
        "X-Expected-Plan-Revision": "0",
    }
    with TestClient(api.app) as client:
        response = client.post(f"/api/rounds/{seed.round_id}/votes", headers=headers, json={"option_id": "requested"})
    assert response.status_code == status
    assert "private-db-detail" not in response.text
