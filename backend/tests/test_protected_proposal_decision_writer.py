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
from app.db.models import AuthSession, ChangeProposal, GuestSession, MutationIdempotency, Plan, PlanChange, PlanItem, ProposalDecision, Trip, TripMembership, UpdateNotice, User
from app.domain import auth as auth_service
from app.domain.transactions import CredentialProof, IdempotencyKeyReused, PreliminaryActorScope, StalePlanRevision
from app.domain.transactions import proposal_decision as protected_decisions
from app.domain.transactions.durable import HmacSigningKey

_records: list[tuple[str, tuple[str, ...]]] = []

@dataclass(frozen=True)
class Seed:
    trip_id: str; plan_id: str; item_id: str; proposal_id: str
    account_membership_id: str; account_user_id: str; account_token: str
    guest_membership_id: str; guest_token: str
    extra_membership_id: str; extra_user_id: str; extra_token: str

def _factory(engine): return sessionmaker(bind=engine, expire_on_commit=False, future=True)

def _seed(engine) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp()); account_token, guest_token, extra_token = f"proposal-account-{stamp}", f"gst_proposal-{stamp}", f"proposal-extra-{stamp}"
    with _factory(engine).begin() as db:
        account = User(name="Proposal account", email=f"proposal-account-{stamp}@example.test"); extra = User(name="Proposal extra", email=f"proposal-extra-{stamp}@example.test")
        db.add_all((account, extra)); db.flush(); trip = Trip(name="Proposal trip", destination="Chicago", created_by_user_id=account.id); db.add(trip); db.flush()
        account_member = TripMembership(trip_id=trip.id, user_id=account.id, role="participant", status="joined")
        guest_member = TripMembership(trip_id=trip.id, user_id=None, guest_display_name="Proposal guest", role="participant", join_method="invite_guest", status="joined")
        extra_member = TripMembership(trip_id=trip.id, user_id=extra.id, role="participant", status="joined"); plan = Plan(trip_id=trip.id)
        db.add_all((account_member, guest_member, extra_member, plan)); db.flush()
        db.add_all((AuthSession(user_id=account.id, token_hash=auth_service.token_hash(account_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)), AuthSession(user_id=extra.id, token_hash=auth_service.token_hash(extra_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)), GuestSession(membership_id=guest_member.id, trip_id=trip.id, token_hash=auth_service.token_hash(guest_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1))))
        item = PlanItem(plan_id=plan.id, day_index=1, day_date=date(2026, 10, 8), start_hour=10, duration_min=60, title="Original proposal item", place="Chicago", settledness="booked"); db.add(item); db.flush()
        proposal = ChangeProposal(plan_item_id=item.id, action_type="edit", before_json={"title": item.title, "place": item.place, "start_hour": item.start_hour, "day_date": item.day_date.isoformat()}, after_json={"title": "Confirmed proposal item"}, requested_by_membership_id=account_member.id, status="waiting_affected_members", deadline=datetime.now(timezone.utc) + timedelta(days=1)); db.add(proposal); db.flush()
        db.add_all((ProposalDecision(proposal_id=proposal.id, trip_membership_id=account_member.id, status="accepted"), ProposalDecision(proposal_id=proposal.id, trip_membership_id=guest_member.id, status="pending"), ProposalDecision(proposal_id=proposal.id, trip_membership_id=extra_member.id, status="pending"))); db.flush()
        _records.append((trip.id, (account.id, extra.id)))
        return Seed(trip.id, plan.id, item.id, proposal.id, account_member.id, account.id, account_token, guest_member.id, guest_token, extra_member.id, extra.id, extra_token)

@pytest.fixture(autouse=True)
def _cleanup(test_engine):
    _records.clear(); yield
    rows, _records[:] = list(_records), []
    if not rows: return
    trip_ids = [trip_id for trip_id, _ in rows]; user_ids = [user_id for _, ids in rows for user_id in ids]
    plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids)); item_ids = select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids)); proposal_ids = select(ChangeProposal.id).where(ChangeProposal.plan_item_id.in_(item_ids))
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(UpdateNotice).filter(UpdateNotice.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(ProposalDecision).filter(ProposalDecision.proposal_id.in_(proposal_ids)).delete(synchronize_session=False); db.query(ChangeProposal).filter(ChangeProposal.plan_item_id.in_(item_ids)).delete(synchronize_session=False); db.query(PlanChange).filter(PlanChange.plan_id.in_(plan_ids)).delete(synchronize_session=False); db.query(GuestSession).filter(GuestSession.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(PlanItem).filter(PlanItem.plan_id.in_(plan_ids)).delete(synchronize_session=False); db.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False); db.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False); db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)

def _scope(seed, actor):
    if actor == "guest": return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.guest_membership_id, credential_kind="guest")
    if actor == "extra": return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.extra_membership_id, credential_kind="account", expected_user_id=seed.extra_user_id)
    return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.account_membership_id, credential_kind="account", expected_user_id=seed.account_user_id)
def _token(seed, actor): return {"account": seed.account_token, "guest": seed.guest_token, "extra": seed.extra_token}[actor]
def _command(seed, key="proposal-decision", revision=0, status="accepted"): return protected_decisions.ProtectedProposalDecision(seed.proposal_id, key, revision, status)
def _execute(engine, seed, command, actor="guest"):
    return protected_decisions.execute_protected_proposal_decision(session_factory=_factory(engine), scope=_scope(seed, actor), credential=CredentialProof.from_bearer_token(_token(seed, actor)), command=command, keyring=protected_decisions.StaticHmacKeyring(HmacSigningKey("test-v1", b"protected-proposal-decision-key")))
def _accept_extra(engine, seed):
    with _factory(engine).begin() as db: db.scalar(select(ProposalDecision).where(ProposalDecision.proposal_id == seed.proposal_id, ProposalDecision.trip_membership_id == seed.extra_membership_id)).status = "accepted"

def test_partial_acceptance_remains_pending_and_advances_once(test_engine):
    seed = _seed(test_engine); result = _execute(test_engine, seed, _command(seed, "guest-accept"))
    assert result.plan_revision == 1 and result.payload["status"] == "waiting_affected_members" and result.payload["applied"] is False
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1 and db.get(ChangeProposal, seed.proposal_id).status == "waiting_affected_members" and db.get(PlanItem, seed.item_id).title == "Original proposal item"
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1

def test_final_unanimous_acceptance_applies_once_and_replays(test_engine):
    seed = _seed(test_engine); _accept_extra(test_engine, seed); command = _command(seed, "final"); first = _execute(test_engine, seed, command); replay = _execute(test_engine, seed, command)
    assert first.payload["applied"] is True and first.payload["status"] == "applied" and replay.replayed is True
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1 and db.get(PlanItem, seed.item_id).title == "Confirmed proposal item"
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1

def test_decline_terminates_without_plan_application(test_engine):
    seed = _seed(test_engine); result = _execute(test_engine, seed, _command(seed, status="declined"))
    assert result.payload["status"] == "declined" and result.payload["applied"] is False and result.plan_revision == 1
    with _factory(test_engine)() as db: assert db.get(PlanItem, seed.item_id).title == "Original proposal item" and db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 0

def test_final_acceptance_conflict_escalates_without_applying(test_engine):
    seed = _seed(test_engine); _accept_extra(test_engine, seed)
    with _factory(test_engine).begin() as db:
        proposal = db.get(ChangeProposal, seed.proposal_id); proposal.after_json = {"title": "Conflicting proposal", "start_hour": 14.0, "duration_min": 60}; db.add(PlanItem(plan_id=seed.plan_id, day_index=1, day_date=date(2026, 10, 8), start_hour=14, duration_min=60, title="Existing block", place="Chicago"))
    result = _execute(test_engine, seed, _command(seed, "conflict")); assert result.payload["status"] == "escalated" and result.payload["applied"] is False
    with _factory(test_engine)() as db: assert db.get(PlanItem, seed.item_id).title == "Original proposal item" and db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0 and db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 1

def test_repeated_no_op_replay_changed_key_and_stale_revision_are_safe(test_engine):
    seed = _seed(test_engine); _execute(test_engine, seed, _command(seed, "accept")); no_op = _execute(test_engine, seed, _command(seed, "same-again", 1)); replay = _execute(test_engine, seed, _command(seed, "same-again", 1))
    with pytest.raises(IdempotencyKeyReused): _execute(test_engine, seed, _command(seed, "accept", status="declined"))
    with pytest.raises(StalePlanRevision): _execute(test_engine, seed, _command(seed, "stale", 0))
    assert no_op.plan_revision == 1 and replay.replayed is True
    with _factory(test_engine)() as db: assert db.get(Plan, seed.plan_id).revision == 1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 2

def test_guest_route_and_privacy_safe_response(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine); monkeypatch.setattr(api, "SessionLocal", factory); monkeypatch.setattr(db_session, "SessionLocal", factory); monkeypatch.setenv("CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES", "1"); monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "proposal-route-key")
    headers = {"Authorization": f"Bearer {seed.guest_token}", "X-Trip-Id": seed.trip_id, "Idempotency-Key": "guest", "X-Expected-Plan-Revision": "0"}
    with TestClient(api.app) as client: response = client.post(f"/api/proposals/{seed.proposal_id}/decisions", headers=headers, json={"status": "accepted"})
    assert response.status_code == 200; serialized = str(response.json()); assert seed.account_membership_id not in serialized and seed.extra_membership_id not in serialized and seed.account_user_id not in serialized

def test_proposal_decision_gate_is_default_off_and_rejects_production(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine); monkeypatch.setattr(api, "SessionLocal", factory); monkeypatch.setattr(db_session, "SessionLocal", factory)
    headers = {"Authorization": f"Bearer {seed.guest_token}", "X-Trip-Id": seed.trip_id, "X-Expected-Plan-Revision": "ignored-while-off"}
    with TestClient(api.app) as client: legacy = client.post(f"/api/proposals/{seed.proposal_id}/decisions", headers=headers, json={"status": "accepted"})
    assert legacy.status_code == 200
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES", "1"); monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "proposal-route-key"); monkeypatch.setenv("APP_ENV", "production")
    with TestClient(api.app) as client: production = client.post(f"/api/proposals/{seed.proposal_id}/decisions", headers={**headers, "Idempotency-Key": "production", "X-Expected-Plan-Revision": "0"}, json={"status": "accepted"})
    assert production.status_code == 503

def test_nonaffected_and_cross_trip_proposals_cannot_change_authoritative_plan(test_engine):
    seed = _seed(test_engine); factory = _factory(test_engine)
    with factory.begin() as db:
        outsider = User(name="Proposal outsider", email=f"proposal-outsider-{datetime.now(timezone.utc).timestamp()}@example.test"); db.add(outsider); db.flush()
        outsider_member = TripMembership(trip_id=seed.trip_id, user_id=outsider.id, role="participant", status="joined"); db.add(outsider_member); db.flush()
        db.add(AuthSession(user_id=outsider.id, token_hash=auth_service.token_hash("proposal-outsider-token"), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        source_trip = db.get(Trip, seed.trip_id); foreign_trip = Trip(name="Foreign proposal trip", destination="Detroit", created_by_user_id=source_trip.created_by_user_id); db.add(foreign_trip); db.flush()
        foreign_plan = Plan(trip_id=foreign_trip.id); db.add(foreign_plan); db.flush()
        foreign_item = PlanItem(plan_id=foreign_plan.id, day_index=1, day_date=date(2026, 10, 9), start_hour=10, duration_min=60, title="Foreign item", place="Detroit"); db.add(foreign_item); db.flush()
        foreign_proposal = ChangeProposal(plan_item_id=foreign_item.id, action_type="edit", before_json={}, after_json={"title": "Must not apply"}, requested_by_membership_id=seed.account_membership_id, status="waiting_affected_members"); db.add(foreign_proposal); db.flush()
        _records.append((foreign_trip.id, (outsider.id,)))
        outsider_scope = PreliminaryActorScope(trip_id=seed.trip_id, membership_id=outsider_member.id, credential_kind="account", expected_user_id=outsider.id)
        foreign_id = foreign_proposal.id
    result = protected_decisions.execute_protected_proposal_decision(session_factory=factory, scope=outsider_scope, credential=CredentialProof.from_bearer_token("proposal-outsider-token"), command=protected_decisions.ProtectedProposalDecision(seed.proposal_id, "outsider", 0, "accepted"), keyring=protected_decisions.StaticHmacKeyring(HmacSigningKey("test-v1", b"protected-proposal-decision-key")))
    assert result.plan_revision == 0 and result.payload["can_decide"] is False
    with pytest.raises(protected_decisions.ProposalDecisionProposalMissing):
        _execute(test_engine, seed, protected_decisions.ProtectedProposalDecision(foreign_id, "foreign", 0, "accepted"))
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 0 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1

def test_concurrent_accept_and_decline_leave_one_terminal_consistent_outcome(test_engine, monkeypatch):
    seed = _seed(test_engine); entered, release = Event(), Event(); original = protected_decisions.orch.decide_proposal; outcomes = Queue()
    def paused(session, proposal, membership_id, status):
        result = original(session, proposal, membership_id, status)
        if status == "declined": entered.set(); assert release.wait(5)
        return result
    monkeypatch.setattr(protected_decisions.orch, "decide_proposal", paused)
    def run(actor, key, status):
        try: outcomes.put(_execute(test_engine, seed, _command(seed, key, 0, status), actor))
        except BaseException as exc: outcomes.put(exc)
    decline = Thread(target=lambda: run("extra", "decline", "declined"), daemon=True); decline.start(); assert entered.wait(5)
    accept = Thread(target=lambda: run("guest", "accept", "accepted"), daemon=True); accept.start(); release.set(); decline.join(5); accept.join(5)
    results = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert any(getattr(value, "replayed", None) is False for value in results) and any(isinstance(value, StalePlanRevision) for value in results)
    with _factory(test_engine)() as db:
        assert db.get(ChangeProposal, seed.proposal_id).status == "declined" and db.get(PlanItem, seed.item_id).title == "Original proposal item" and db.get(Plan, seed.plan_id).revision == 1

def test_injected_failure_rolls_back_final_application_revision_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine); _accept_extra(test_engine, seed); original = protected_decisions.DurableCommandKernel._checkpoint
    def fail(self, stage):
        if stage == "after_domain_effect": raise RuntimeError("injected proposal decision failure")
        return original(self, stage)
    monkeypatch.setattr(protected_decisions.DurableCommandKernel, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="injected proposal decision failure"): _execute(test_engine, seed, _command(seed))
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0 and db.get(ChangeProposal, seed.proposal_id).status == "waiting_affected_members" and db.get(PlanItem, seed.item_id).title == "Original proposal item"
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0

def test_concurrent_final_attempts_use_independent_connections_and_apply_once(test_engine, monkeypatch):
    seed = _seed(test_engine); _accept_extra(test_engine, seed); entered, release, first_lock, second_reached, second_acquired = Event(), Event(), Event(), Event(), Event(); original = protected_decisions.orch.decide_proposal; connections = {}
    def paused(*args, **kwargs): outcome = original(*args, **kwargs); entered.set(); assert release.wait(5); return outcome
    monkeypatch.setattr(protected_decisions.orch, "decide_proposal", paused)
    def root_lock(statement): normalized = " ".join(statement.lower().split()); return "from trip " in normalized and "trip_membership" not in normalized and "for update" in normalized
    def before(conn, _cursor, statement, _parameters, _context, _executemany):
        if root_lock(statement):
            raw = conn.connection; ident = id(getattr(raw, "driver_connection", raw))
            if current_thread().name == "proposal-first": connections["first"] = ident
            if current_thread().name == "proposal-second": connections["second"] = ident; second_reached.set()
    def after(_conn, _cursor, statement, _parameters, _context, _executemany):
        if root_lock(statement):
            if current_thread().name == "proposal-first": first_lock.set()
            if current_thread().name == "proposal-second": second_acquired.set()
    outcomes = Queue()
    def run(key):
        try: outcomes.put(_execute(test_engine, seed, _command(seed, key)))
        except BaseException as exc: outcomes.put(exc)
    event.listen(test_engine, "before_cursor_execute", before); event.listen(test_engine, "after_cursor_execute", after)
    try:
        first = Thread(target=lambda: run("first"), name="proposal-first", daemon=True); first.start(); assert first_lock.wait(5) and entered.wait(5)
        second = Thread(target=lambda: run("second"), name="proposal-second", daemon=True); second.start(); assert second_reached.wait(5); assert not second_acquired.is_set() and connections["first"] != connections["second"]
        release.set(); first.join(5); second.join(5)
    finally: event.remove(test_engine, "before_cursor_execute", before); event.remove(test_engine, "after_cursor_execute", after)
    results = [outcomes.get_nowait(), outcomes.get_nowait()]; assert second_acquired.is_set() and any(getattr(value, "replayed", None) is False for value in results) and any(isinstance(value, StalePlanRevision) for value in results)
    with _factory(test_engine)() as db: assert db.get(Plan, seed.plan_id).revision == 1 and db.get(ChangeProposal, seed.proposal_id).status == "applied" and db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1

def test_two_distinct_acceptors_prove_trip_blocking_then_explicit_retry_applies_once(test_engine, monkeypatch):
    """One partial approval wins revision 0; the other explicitly retries at 1."""
    seed = _seed(test_engine)
    entered, release, first_lock, second_reached, second_acquired = Event(), Event(), Event(), Event(), Event()
    original = protected_decisions.orch.decide_proposal
    connections: dict[str, int] = {}

    def paused(session, proposal, membership_id, status):
        outcome = original(session, proposal, membership_id, status)
        if membership_id == seed.guest_membership_id and status == "accepted":
            entered.set()
            assert release.wait(5)
        return outcome

    monkeypatch.setattr(protected_decisions.orch, "decide_proposal", paused)

    def is_trip_root_lock(statement: str) -> bool:
        normalized = " ".join(statement.lower().split())
        return "from trip " in normalized and "trip_membership" not in normalized and "for update" in normalized

    def driver_identity(conn) -> int:
        raw = conn.connection
        return id(getattr(raw, "driver_connection", raw))

    def backend_pid(conn) -> int:
        raw = conn.connection
        return getattr(raw, "driver_connection", raw).info.backend_pid

    def before(conn, _cursor, statement, _parameters, _context, _executemany):
        if not is_trip_root_lock(statement): return
        if current_thread().name == "proposal-guest":
            connections["guest"] = driver_identity(conn); connections["guest_pid"] = backend_pid(conn)
        elif current_thread().name == "proposal-extra":
            connections["extra"] = driver_identity(conn); connections["extra_pid"] = backend_pid(conn); second_reached.set()

    def after(_conn, _cursor, statement, _parameters, _context, _executemany):
        if not is_trip_root_lock(statement): return
        if current_thread().name == "proposal-guest": first_lock.set()
        elif current_thread().name == "proposal-extra": second_acquired.set()

    outcomes: Queue[object] = Queue()
    def run(actor: str, key: str):
        try: outcomes.put(_execute(test_engine, seed, _command(seed, key, 0), actor))
        except BaseException as exc: outcomes.put(exc)

    event.listen(test_engine, "before_cursor_execute", before); event.listen(test_engine, "after_cursor_execute", after)
    try:
        guest = Thread(target=lambda: run("guest", "guest-accept"), name="proposal-guest", daemon=True); guest.start()
        assert first_lock.wait(5) and entered.wait(5)
        extra = Thread(target=lambda: run("extra", "extra-stale"), name="proposal-extra", daemon=True); extra.start()
        assert second_reached.wait(5) and not second_acquired.is_set() and connections["guest"] != connections["extra"]
        observed = False; deadline = monotonic() + 5
        while monotonic() < deadline:
            with _factory(test_engine)() as observer:
                blockers = observer.scalar(text("select pg_blocking_pids(:blocked)"), {"blocked": connections["extra_pid"]})
            if connections["guest_pid"] in (blockers or []): observed = True; break
        assert observed, "PostgreSQL did not report Guest as Extra's Trip-lock blocker"
        release.set(); guest.join(5); extra.join(5)
    finally:
        event.remove(test_engine, "before_cursor_execute", before); event.remove(test_engine, "after_cursor_execute", after)
    assert not guest.is_alive() and not extra.is_alive() and second_acquired.is_set()
    results = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert any(getattr(value, "plan_revision", None) == 1 and not value.replayed for value in results)
    assert any(isinstance(value, StalePlanRevision) for value in results)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1 and db.get(ChangeProposal, seed.proposal_id).status == "waiting_affected_members"
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1
    retry = _execute(test_engine, seed, _command(seed, "extra-retry", 1), "extra")
    assert retry.plan_revision == 2 and retry.payload["applied"] is True
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 2 and db.get(ChangeProposal, seed.proposal_id).status == "applied"
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 2
