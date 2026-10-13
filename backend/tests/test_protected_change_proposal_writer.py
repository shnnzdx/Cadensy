from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app.api import main as api
from app.db import session as db_session
from app.db.models import (
    AuthSession,
    ChangeProposal,
    DecisionRound,
    GuestSession,
    MutationIdempotency,
    Plan,
    PlanChange,
    PlanItem,
    ProposalDecision,
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
from app.domain.transactions import change_proposal as protected_changes
from app.domain.transactions.durable import HmacSigningKey


_records: list[tuple[str, tuple[str, ...]]] = []


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str
    item_id: str
    account_membership_id: str
    account_user_id: str
    account_token: str
    guest_membership_id: str
    guest_token: str
    guest_session_id: str


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(engine, *, settledness: str = "loose") -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    account_token, guest_token = f"change-account-{stamp}", f"gst_change-{stamp}"
    with _factory(engine).begin() as db:
        account = User(name="Change account", email=f"change-account-{stamp}@example.test")
        owner = User(name="Change owner", email=f"change-owner-{stamp}@example.test")
        db.add_all((account, owner))
        db.flush()
        trip = Trip(name="Change trip", destination="Chicago", created_by_user_id=owner.id)
        db.add(trip)
        db.flush()
        account_member = TripMembership(
            trip_id=trip.id, user_id=account.id, role="participant", status="joined"
        )
        guest_member = TripMembership(
            trip_id=trip.id,
            user_id=None,
            guest_display_name="Change guest",
            role="participant",
            join_method="invite_guest",
            status="joined",
        )
        plan = Plan(trip_id=trip.id)
        db.add_all((account_member, guest_member, plan))
        db.flush()
        guest_session = GuestSession(
            membership_id=guest_member.id,
            trip_id=trip.id,
            token_hash=auth_service.token_hash(guest_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db.add_all(
            (
                AuthSession(
                    user_id=account.id,
                    token_hash=auth_service.token_hash(account_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
                guest_session,
            )
        )
        item = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=date(2026, 10, 8),
            start_hour=12,
            duration_min=60,
            title="Change item",
            place="Chicago",
            settledness=settledness,
        )
        db.add(item)
        db.flush()
        _records.append((trip.id, (account.id, owner.id)))
        return Seed(
            trip.id,
            plan.id,
            item.id,
            account_member.id,
            account.id,
            account_token,
            guest_member.id,
            guest_token,
            guest_session.id,
        )


@pytest.fixture(autouse=True)
def _cleanup(test_engine):
    _records.clear()
    yield
    rows, _records[:] = list(_records), []
    if not rows:
        return
    trip_ids = [row[0] for row in rows]
    user_ids = [user_id for _, users in rows for user_id in users]
    plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids))
    item_ids = select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids))
    round_ids = select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(item_ids))
    proposal_ids = select(ChangeProposal.id).where(ChangeProposal.plan_item_id.in_(item_ids))
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(UpdateNotice).filter(UpdateNotice.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(ProposalDecision).filter(ProposalDecision.proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
        db.query(Vote).filter(Vote.round_id.in_(round_ids)).delete(synchronize_session=False)
        db.query(DecisionRound).filter(DecisionRound.plan_item_id.in_(item_ids)).delete(synchronize_session=False)
        db.query(ChangeProposal).filter(ChangeProposal.plan_item_id.in_(item_ids)).delete(synchronize_session=False)
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
            trip_id=seed.trip_id,
            membership_id=seed.guest_membership_id,
            credential_kind="guest",
        )
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.account_membership_id,
        credential_kind="account",
        expected_user_id=seed.account_user_id,
    )


def _command(seed: Seed, *, key: str = "change-key", revision: int = 0, reason: str | None = None):
    return protected_changes.ProtectedChangeProposal(
        item_id=seed.item_id,
        idempotency_key=key,
        expected_plan_revision=revision,
        patch={"title": "Protected change"},
        request="Please update this plan item",
        reason=reason,
        alternatives=(
            {
                "item_id": seed.item_id,
                "label": "Later",
                "title": "Later title",
                "body": "Later body",
                "tradeoff": "Less time",
                "start_hour": 15.0,
                "day_date": date(2026, 10, 8),
                "duration_min": 60,
            },
        ),
    )


def _execute(engine, seed: Seed, command, *, kind: str = "account"):
    token = seed.guest_token if kind == "guest" else seed.account_token
    return protected_changes.execute_protected_change_proposal(
        session_factory=_factory(engine),
        scope=_scope(seed, kind),
        credential=CredentialProof.from_bearer_token(token),
        command=command,
        keyring=protected_changes.StaticHmacKeyring(
            HmacSigningKey("test-v1", b"protected-change-test-key")
        ),
    )


@pytest.mark.parametrize(
    ("settledness", "reason", "path", "round_kind", "proposal_count", "change_count"),
    [
        ("loose", None, "notice", None, 0, 1),
        ("touched", None, "round", "normal", 0, 0),
        ("settled", "New information", "reopen_round", "reopen", 0, 0),
        ("booked", None, "confirm", None, 1, 0),
    ],
)
def test_protected_change_preserves_all_submission_paths(
    test_engine, settledness, reason, path, round_kind, proposal_count, change_count
):
    seed = _seed(test_engine, settledness=settledness)
    result = _execute(test_engine, seed, _command(seed, reason=reason))
    assert result.replayed is False
    assert result.plan_revision == 1
    assert result.payload["path"] == path
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        item = db.get(PlanItem, seed.item_id)
        assert item is not None
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == change_count
        assert db.query(ChangeProposal).filter(ChangeProposal.plan_item_id == seed.item_id).count() == proposal_count
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1
        rounds = db.query(DecisionRound).filter(DecisionRound.plan_item_id == seed.item_id).all()
        assert len(rounds) == (1 if round_kind else 0)
        if round_kind:
            assert rounds[0].kind == round_kind
            assert rounds[0].options[2]["label"] == "Later"
            assert rounds[0].options[2]["tradeoff"] == "Less time"
        if path == "confirm":
            proposal = db.query(ChangeProposal).filter(ChangeProposal.plan_item_id == seed.item_id).one()
            assert proposal.status == "waiting_affected_members"
            assert db.query(ProposalDecision).filter(ProposalDecision.proposal_id == proposal.id).count() == 2
            assert item.title == "Change item"
        if path == "notice":
            assert item.title == "Protected change"
            assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 1


def test_reopen_without_reason_rolls_back_all_rows(test_engine):
    seed = _seed(test_engine, settledness="settled")
    with pytest.raises(orch.ReasonRequired):
        _execute(test_engine, seed, _command(seed))
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0
        assert db.query(DecisionRound).filter(DecisionRound.plan_item_id == seed.item_id).count() == 0
        assert db.query(ChangeProposal).filter(ChangeProposal.plan_item_id == seed.item_id).count() == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_change_replay_conflict_and_stale_revision_are_safe(test_engine):
    seed = _seed(test_engine)
    command = _command(seed)
    first = _execute(test_engine, seed, command)
    replay = _execute(test_engine, seed, command)
    with pytest.raises(IdempotencyKeyReused):
        _execute(test_engine, seed, _command(seed, reason="semantic difference"))
    with pytest.raises(StalePlanRevision):
        _execute(test_engine, seed, _command(seed, key="change-stale"))
    assert replay.replayed is True and replay.payload == first.payload
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_change_alternative_semantics_and_pending_guard_fail_closed(test_engine):
    seed = _seed(test_engine, settledness="touched")
    first = _execute(test_engine, seed, _command(seed, key="round-first"))
    changed_alternative = protected_changes.ProtectedChangeProposal(
        item_id=seed.item_id,
        idempotency_key="round-first",
        expected_plan_revision=0,
        patch={"title": "Protected change"},
        request="Please update this plan item",
        reason=None,
        alternatives=(
            {
                "item_id": seed.item_id,
                "label": "Later",
                "title": "Changed alternative title",
                "body": "Later body",
                "tradeoff": "Different tradeoff",
                "start_hour": 15.0,
                "day_date": date(2026, 10, 8),
                "duration_min": 60,
            },
        ),
    )
    with pytest.raises(IdempotencyKeyReused):
        _execute(test_engine, seed, changed_alternative)
    with pytest.raises(orch.AlreadyPending):
        _execute(test_engine, seed, _command(seed, key="round-second", revision=1))
    assert first.payload["path"] == "round"
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(DecisionRound).filter(DecisionRound.plan_item_id == seed.item_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_change_ledger_outcome_excludes_request_reason_and_alternative_text(test_engine):
    seed = _seed(test_engine)
    result = _execute(test_engine, seed, _command(seed))
    with _factory(test_engine)() as db:
        ledger = db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).one()
        serialized = str(ledger.outcome_json)
        assert ledger.outcome_json == result.payload
        assert "Please update this plan item" not in serialized
        assert "Later title" not in serialized
        assert "Less time" not in serialized
        assert ledger.outcome_json["path"] == "notice"


def test_change_checkpoint_failure_rolls_back_effect_revision_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine)
    original = protected_changes.DurableCommandKernel._checkpoint

    def fail_after_effect(self, stage):
        if stage == "after_domain_effect":
            raise RuntimeError("injected protected change failure")
        return original(self, stage)

    monkeypatch.setattr(protected_changes.DurableCommandKernel, "_checkpoint", fail_after_effect)
    with pytest.raises(RuntimeError, match="injected protected change failure"):
        _execute(test_engine, seed, _command(seed))
    with _factory(test_engine)() as db:
        assert db.get(PlanItem, seed.item_id).title == "Change item"
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_change_foreign_item_is_not_locked_or_recorded(test_engine):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        source_trip = db.get(Trip, seed.trip_id)
        assert source_trip is not None
        foreign_trip = Trip(
            name="Foreign change trip",
            destination="Detroit",
            created_by_user_id=source_trip.created_by_user_id,
        )
        db.add(foreign_trip)
        db.flush()
        foreign_plan = Plan(trip_id=foreign_trip.id)
        db.add(foreign_plan)
        db.flush()
        foreign_item = PlanItem(
            plan_id=foreign_plan.id,
            day_index=1,
            day_date=date(2026, 10, 9),
            start_hour=12,
            duration_min=60,
            title="Foreign change item",
            place="Detroit",
            settledness="loose",
        )
        db.add(foreign_item)
        db.flush()
        foreign_item_id, foreign_plan_id = foreign_item.id, foreign_plan.id
        _records.append((foreign_trip.id, ()))
    foreign_command = protected_changes.ProtectedChangeProposal(
        item_id=foreign_item_id,
        idempotency_key="foreign-change",
        expected_plan_revision=0,
        patch={"title": "Must not apply"},
        request="Foreign request",
        reason=None,
        alternatives=(),
    )
    child_probes: list[str] = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if "from plan_item" in normalized and "for update" in normalized and foreign_item_id in str(parameters):
            child_probes.append(normalized)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(protected_changes.ChangeProposalItemMissing):
            _execute(test_engine, seed, foreign_command)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    assert len(child_probes) == 1
    assert "plan_item.id" in child_probes[0] and "plan_item.plan_id" in child_probes[0]
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.get(Plan, foreign_plan_id).revision == 0
        assert db.get(PlanItem, foreign_item_id).title == "Foreign change item"
        assert db.query(PlanChange).count() == 0
        assert db.query(UpdateNotice).count() == 0
        assert db.query(MutationIdempotency).count() == 0


def test_change_kernel_durable_path_locks_electorate_before_plan_and_commits_outcome(test_engine):
    seed = _seed(test_engine, settledness="booked")
    lock_order: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if "for update" not in normalized:
            return
        if "from trip_membership" in normalized:
            lock_order.append("membership")
        elif "from plan " in normalized:
            lock_order.append("plan")

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        result = _execute(test_engine, seed, _command(seed))
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    assert "membership" in lock_order and "plan" in lock_order
    plan_index = lock_order.index("plan")
    assert lock_order[:plan_index].count("membership") >= 2
    assert result.replayed is False
    assert result.plan_revision == 1
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(ChangeProposal).filter(ChangeProposal.plan_item_id == seed.item_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_change_concurrency_blocks_at_trip_lock_and_commits_one_effect(test_engine, monkeypatch):
    seed = _seed(test_engine)
    entered, release = Event(), Event()
    first_trip_lock, contender_reached_trip_lock, contender_acquired_trip_lock = Event(), Event(), Event()
    root_connections: dict[str, int] = {}
    original = protected_changes.orch.propose_change

    def paused(*args, **kwargs):
        outcome = original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "coordinator did not release protected change"
        return outcome

    monkeypatch.setattr(protected_changes.orch, "propose_change", paused)

    def is_trip_root_lock(statement: str) -> bool:
        normalized = " ".join(statement.lower().split())
        return "from trip " in normalized and "from trip_membership" not in normalized and "for update" in normalized

    def connection_identity(connection) -> int:
        raw = connection.connection
        return id(getattr(raw, "driver_connection", raw))

    def before_cursor_execute(conn, _cursor, statement, _parameters, _context, _executemany):
        if not is_trip_root_lock(statement):
            return
        name = current_thread().name
        if name == "change-first":
            root_connections["first"] = connection_identity(conn)
        elif name == "change-contender":
            root_connections["second"] = connection_identity(conn)
            contender_reached_trip_lock.set()

    def after_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if current_thread().name == "change-first" and is_trip_root_lock(statement):
            first_trip_lock.set()
        elif current_thread().name == "change-contender" and is_trip_root_lock(statement):
            contender_acquired_trip_lock.set()

    outcomes: Queue[object] = Queue()

    def run(key: str):
        try:
            outcomes.put(_execute(test_engine, seed, _command(seed, key=key)))
        except BaseException as exc:
            outcomes.put(exc)

    event.listen(test_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(test_engine, "after_cursor_execute", after_cursor_execute)
    try:
        first = Thread(target=lambda: run("change-first"), name="change-first", daemon=True)
        first.start()
        assert first_trip_lock.wait(5), "first writer did not acquire Trip FOR UPDATE"
        assert entered.wait(5)
        second = Thread(target=lambda: run("change-second"), name="change-contender", daemon=True)
        second.start()
        assert contender_reached_trip_lock.wait(5), "contender did not reach Trip FOR UPDATE"
        assert not contender_acquired_trip_lock.is_set(), "contender acquired Trip lock before release"
        assert root_connections["first"] != root_connections["second"]
        release.set()
        first.join(5)
        second.join(5)
    finally:
        event.remove(test_engine, "before_cursor_execute", before_cursor_execute)
        event.remove(test_engine, "after_cursor_execute", after_cursor_execute)
    assert contender_acquired_trip_lock.is_set(), "contender did not acquire Trip lock after release"
    assert not first.is_alive() and not second.is_alive()
    results = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert any(getattr(result, "replayed", None) is False for result in results)
    assert any(isinstance(result, StalePlanRevision) for result in results)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_guest_change_route_replays_and_fails_after_revocation(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "change-route-test-key")
    headers = {
        "Authorization": f"Bearer {seed.guest_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "guest-change",
        "X-Expected-Plan-Revision": "0",
    }
    body = {"title": "Guest protected change", "request": "Move this"}
    with TestClient(api.app) as client:
        first = client.post(f"/api/plans/items/{seed.item_id}/changes", headers=headers, json=body)
        replay = client.post(f"/api/plans/items/{seed.item_id}/changes", headers=headers, json=body)
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    with factory.begin() as db:
        db.get(GuestSession, seed.guest_session_id).revoked_at = datetime.now(timezone.utc)
    with TestClient(api.app) as client:
        revoked = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers={**headers, "Idempotency-Key": "guest-change-revoked"},
            json=body,
        )
    assert revoked.status_code == 401
    with factory.begin() as db:
        db.get(GuestSession, seed.guest_session_id).revoked_at = None
        db.get(TripMembership, seed.guest_membership_id).status = "removed"
    with TestClient(api.app) as client:
        removed = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers={**headers, "Idempotency-Key": "guest-change-removed"},
            json=body,
        )
    assert removed.status_code in {401, 403}


def test_change_feature_gate_is_default_off_and_feature_on_requires_protocol_headers(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "X-Expected-Plan-Revision": "malformed-but-ignored-while-off",
    }
    body = {"title": "Legacy change"}
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    with TestClient(api.app) as client:
        legacy = client.post(f"/api/plans/items/{seed.item_id}/changes", headers=headers, json=body)
    assert legacy.status_code == 200
    with factory.begin() as db:
        db.get(Plan, seed.plan_id).revision = 0
        db.get(PlanItem, seed.item_id).settledness = "loose"
        db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).delete(synchronize_session=False)
        db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).delete(synchronize_session=False)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "change-route-test-key")
    with TestClient(api.app) as client:
        missing = client.post(f"/api/plans/items/{seed.item_id}/changes", headers=headers, json=body)
    assert missing.status_code == 428


def test_change_production_gate_rejects_before_legacy_or_protected_writer(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "change-route-test-key")
    monkeypatch.setenv("APP_ENV", "production")
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "production-change",
        "X-Expected-Plan-Revision": "0",
    }
    with TestClient(api.app) as client:
        response = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers=headers,
            json={"title": "Must not apply"},
        )
    assert response.status_code == 503
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0


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
def test_change_route_maps_typed_errors_without_diagnostics(test_engine, monkeypatch, failure, status):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "change-route-test-key")

    def fail(**_kwargs):
        raise failure

    monkeypatch.setattr(protected_changes, "execute_protected_change_proposal", fail)
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "typed-change-error",
        "X-Expected-Plan-Revision": "0",
    }
    with TestClient(api.app) as client:
        response = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers=headers,
            json={"title": "Safe error"},
        )
    assert response.status_code == status
    assert "private-db-detail" not in response.text
