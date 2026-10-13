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
from app.db.models import AuthSession, ChangeProposal, DecisionRound, GuestSession, MutationIdempotency, Plan, PlanChange, PlanItem, ProposalDecision, Trip, TripMembership, UpdateNotice, User, Vote
from app.domain import auth as auth_service
from app.domain.transactions import CredentialProof, IdempotencyKeyReused, PreliminaryActorScope, StalePlanRevision
from app.domain.transactions import notice_objection as protected_notices
from app.domain.transactions.durable import HmacSigningKey

_records: list[tuple[str, tuple[str, ...]]] = []

@dataclass(frozen=True)
class Seed:
    trip_id: str; plan_id: str; item_id: str; notice_id: str
    account_membership_id: str; account_user_id: str; account_token: str
    guest_membership_id: str; guest_token: str; guest_session_id: str

def _factory(engine): return sessionmaker(bind=engine, expire_on_commit=False, future=True)

def _seed(engine) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp()); account_token, guest_token = f"notice-account-{stamp}", f"gst_notice-{stamp}"
    with _factory(engine).begin() as db:
        account = User(name="Notice account", email=f"notice-account-{stamp}@example.test"); owner = User(name="Notice owner", email=f"notice-owner-{stamp}@example.test")
        db.add_all((account, owner)); db.flush(); trip = Trip(name="Notice trip", destination="Chicago", created_by_user_id=owner.id); db.add(trip); db.flush()
        account_member = TripMembership(trip_id=trip.id, user_id=account.id, role="participant", status="joined")
        guest_member = TripMembership(trip_id=trip.id, user_id=None, guest_display_name="Notice guest", role="participant", join_method="invite_guest", status="joined"); plan = Plan(trip_id=trip.id)
        db.add_all((account_member, guest_member, plan)); db.flush()
        guest_session = GuestSession(membership_id=guest_member.id, trip_id=trip.id, token_hash=auth_service.token_hash(guest_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        db.add_all((AuthSession(user_id=account.id, token_hash=auth_service.token_hash(account_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)), guest_session))
        item = PlanItem(plan_id=plan.id, day_index=1, day_date=date(2026, 10, 8), start_hour=12, duration_min=60, title="Notice item", place="Chicago", settledness="touched"); db.add(item); db.flush()
        notice = UpdateNotice(trip_id=trip.id, plan_item_id=item.id, kind="plan", title="Plan changed", body="A change was applied.", can_object=True); db.add(notice); db.flush()
        _records.append((trip.id, (account.id, owner.id))); return Seed(trip.id, plan.id, item.id, notice.id, account_member.id, account.id, account_token, guest_member.id, guest_token, guest_session.id)

@pytest.fixture(autouse=True)
def _cleanup(test_engine):
    _records.clear(); yield
    rows, _records[:] = list(_records), []
    if not rows: return
    trip_ids = [trip_id for trip_id, _ in rows]; user_ids = [user_id for _, users in rows for user_id in users]
    plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids)); item_ids = select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids)); round_ids = select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(item_ids)); proposal_ids = select(ChangeProposal.id).where(ChangeProposal.plan_item_id.in_(item_ids))
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(UpdateNotice).filter(UpdateNotice.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(ProposalDecision).filter(ProposalDecision.proposal_id.in_(proposal_ids)).delete(synchronize_session=False); db.query(Vote).filter(Vote.round_id.in_(round_ids)).delete(synchronize_session=False); db.query(DecisionRound).filter(DecisionRound.plan_item_id.in_(item_ids)).delete(synchronize_session=False); db.query(ChangeProposal).filter(ChangeProposal.plan_item_id.in_(item_ids)).delete(synchronize_session=False); db.query(PlanChange).filter(PlanChange.plan_id.in_(plan_ids)).delete(synchronize_session=False); db.query(GuestSession).filter(GuestSession.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(PlanItem).filter(PlanItem.plan_id.in_(plan_ids)).delete(synchronize_session=False); db.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(synchronize_session=False); db.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False); db.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False); db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)

def _scope(seed, actor="account"):
    if actor == "guest": return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.guest_membership_id, credential_kind="guest")
    return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.account_membership_id, credential_kind="account", expected_user_id=seed.account_user_id)
def _execute(engine, seed, key="notice-object", revision=0, actor="account", notice_id=None):
    token = seed.guest_token if actor == "guest" else seed.account_token
    return protected_notices.execute_protected_notice_objection(session_factory=_factory(engine), scope=_scope(seed, actor), credential=CredentialProof.from_bearer_token(token), command=protected_notices.ProtectedNoticeObjection(notice_id or seed.notice_id, key, revision), keyring=protected_notices.StaticHmacKeyring(HmacSigningKey("test-v1", b"protected-notice-objection-key")))

def test_notice_objection_adapter_exposes_a_versioned_operation(): assert protected_notices.NOTICE_OBJECTION_OPERATION == "notice.object.v1"

def test_eligible_notice_creates_one_normal_round_and_exact_durable_effect(test_engine):
    seed = _seed(test_engine); result = _execute(test_engine, seed)
    assert not result.replayed and result.plan_revision == 1 and result.payload["path"] == "round" and result.payload["applied"] is False
    with _factory(test_engine)() as db:
        notice, item = db.get(UpdateNotice, seed.notice_id), db.get(PlanItem, seed.item_id); rounds = db.scalars(select(DecisionRound).where(DecisionRound.plan_item_id == seed.item_id)).all()
        assert not notice.can_object and notice.body == "Escalated to a group round after an objection." and item.title == "Notice item" and item.settledness == "touched" and len(rounds) == 1
        assert rounds[0].kind == "normal" and rounds[0].status == "open" and rounds[0].deadline > datetime.now(timezone.utc) and [option["id"] for option in rounds[0].options] == ["keep", "requested", "split"]
        assert db.get(Plan, seed.plan_id).revision == 1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1

def test_replay_key_conflict_stale_and_new_key_after_success_are_safe(test_engine):
    seed = _seed(test_engine); first = _execute(test_engine, seed, "same"); replay = _execute(test_engine, seed, "same")
    assert replay.replayed and replay.payload == first.payload
    with pytest.raises(IdempotencyKeyReused): _execute(test_engine, seed, "same", revision=1)
    with pytest.raises(StalePlanRevision): _execute(test_engine, seed, "stale", revision=0)
    with pytest.raises(protected_notices.NoticeObjectionIneligible): _execute(test_engine, seed, "new-key", revision=1)
    with _factory(test_engine)() as db: assert db.get(Plan, seed.plan_id).revision == 1 and db.query(DecisionRound).filter(DecisionRound.plan_item_id == seed.item_id).count() == db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1

@pytest.mark.parametrize("pending_kind", ["round", "proposal"])
def test_pending_round_or_proposal_rejects_without_notice_or_ledger_effect(test_engine, pending_kind):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        if pending_kind == "round": db.add(DecisionRound(plan_item_id=seed.item_id, kind="normal", options=[], deadline=datetime.now(timezone.utc) + timedelta(days=1), status="open"))
        else: db.add(ChangeProposal(plan_item_id=seed.item_id, action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed.account_membership_id, status="waiting_affected_members"))
    with pytest.raises(Exception, match="already|waiting"): _execute(test_engine, seed)
    with _factory(test_engine)() as db: assert db.get(UpdateNotice, seed.notice_id).can_object and db.get(Plan, seed.plan_id).revision == 0 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0

def test_cross_trip_and_mismatched_notice_child_are_rejected_without_effect(test_engine):
    seed, factory = _seed(test_engine), _factory(test_engine)
    with factory.begin() as db:
        owner = db.get(User, seed.account_user_id); foreign_trip = Trip(name="Foreign notice trip", destination="Detroit", created_by_user_id=owner.id); db.add(foreign_trip); db.flush(); foreign_plan = Plan(trip_id=foreign_trip.id); db.add(foreign_plan); db.flush(); foreign_item = PlanItem(plan_id=foreign_plan.id, day_index=1, day_date=date(2026, 10, 9), start_hour=10, duration_min=60, title="Foreign item", place="Detroit"); db.add(foreign_item); db.flush()
        foreign_notice = UpdateNotice(trip_id=foreign_trip.id, plan_item_id=foreign_item.id, kind="plan", title="Foreign", body="Foreign", can_object=True); mismatch = UpdateNotice(trip_id=seed.trip_id, plan_item_id=foreign_item.id, kind="plan", title="Mismatch", body="Mismatch", can_object=True); db.add_all((foreign_notice, mismatch)); db.flush(); _records.append((foreign_trip.id, (owner.id,))); foreign_id, mismatch_id = foreign_notice.id, mismatch.id
    for notice_id in (foreign_id, mismatch_id):
        with pytest.raises(protected_notices.NoticeObjectionNoticeMissing): _execute(test_engine, seed, key=f"foreign-{notice_id}", notice_id=notice_id)
    with factory() as db: assert db.get(Plan, seed.plan_id).revision == 0 and db.get(UpdateNotice, mismatch_id).can_object and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0

def test_real_guest_route_replay_revocation_and_removed_membership(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine); monkeypatch.setattr(api, "SessionLocal", factory); monkeypatch.setattr(db_session, "SessionLocal", factory); monkeypatch.setenv("CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES", "1"); monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "notice-route-key")
    account_headers = {"Authorization": f"Bearer {seed.account_token}", "X-Trip-Id": seed.trip_id, "Idempotency-Key": "account", "X-Expected-Plan-Revision": "0"}
    with TestClient(api.app) as client: account = client.post(f"/api/updates/{seed.notice_id}/object", headers=account_headers)
    assert account.status_code == 200
    seed = _seed(test_engine)
    headers = {"Authorization": f"Bearer {seed.guest_token}", "X-Trip-Id": seed.trip_id, "Idempotency-Key": "guest", "X-Expected-Plan-Revision": "0"}
    with TestClient(api.app) as client: first = client.post(f"/api/updates/{seed.notice_id}/object", headers=headers); replay = client.post(f"/api/updates/{seed.notice_id}/object", headers=headers)
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    with factory.begin() as db: db.get(GuestSession, seed.guest_session_id).revoked_at = datetime.now(timezone.utc)
    with TestClient(api.app) as client: revoked = client.post(f"/api/updates/{seed.notice_id}/object", headers={**headers, "Idempotency-Key": "revoked", "X-Expected-Plan-Revision": "1"})
    assert revoked.status_code == 401
    with factory.begin() as db: db.get(GuestSession, seed.guest_session_id).revoked_at = None; db.get(TripMembership, seed.guest_membership_id).status = "removed"
    with TestClient(api.app) as client: removed = client.post(f"/api/updates/{seed.notice_id}/object", headers={**headers, "Idempotency-Key": "removed", "X-Expected-Plan-Revision": "1"})
    assert removed.status_code in {401, 403}

def test_feature_off_malformed_header_production_rejection_and_protected_header_errors(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.account_membership_id); api.app.dependency_overrides[api.get_session] = lambda: request_db; api.app.dependency_overrides[api.current_membership] = lambda: membership
        try:
            with TestClient(api.app) as client: legacy = client.post(f"/api/updates/{seed.notice_id}/object", headers={"X-Expected-Plan-Revision": "not-an-int"})
        finally: api.app.dependency_overrides.clear()
    assert legacy.status_code == 200
    seed = _seed(test_engine); monkeypatch.setattr(api, "SessionLocal", factory); monkeypatch.setattr(db_session, "SessionLocal", factory); monkeypatch.setenv("CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES", "1"); monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "notice-route-key")
    headers = {"Authorization": f"Bearer {seed.account_token}", "X-Trip-Id": seed.trip_id, "Idempotency-Key": "production", "X-Expected-Plan-Revision": "0"}; monkeypatch.setenv("APP_ENV", "production")
    with TestClient(api.app) as client: production = client.post(f"/api/updates/{seed.notice_id}/object", headers=headers)
    assert production.status_code == 503
    monkeypatch.setenv("APP_ENV", "development")
    with TestClient(api.app) as client: missing = client.post(f"/api/updates/{seed.notice_id}/object", headers={"Authorization": f"Bearer {seed.account_token}", "X-Trip-Id": seed.trip_id}); malformed = client.post(f"/api/updates/{seed.notice_id}/object", headers={**headers, "Idempotency-Key": "malformed", "X-Expected-Plan-Revision": "nope"})
    assert missing.status_code == 428 and malformed.status_code == 422

def test_failure_after_authoritative_mutation_rolls_back_notice_round_revision_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine); original = protected_notices.DurableCommandKernel._checkpoint
    def fail(self, stage):
        if stage == "after_domain_effect": raise RuntimeError("injected notice failure")
        return original(self, stage)
    monkeypatch.setattr(protected_notices.DurableCommandKernel, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="injected notice failure"): _execute(test_engine, seed)
    with _factory(test_engine)() as db:
        notice = db.get(UpdateNotice, seed.notice_id); assert notice.can_object and notice.body == "A change was applied." and db.query(DecisionRound).filter(DecisionRound.plan_item_id == seed.item_id).count() == 0 and db.get(Plan, seed.plan_id).revision == 0 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0

def test_concurrent_same_notice_proves_postgresql_trip_block_then_one_stale_result(test_engine, monkeypatch):
    seed = _seed(test_engine); entered, release, first_lock, second_reached, second_acquired = Event(), Event(), Event(), Event(), Event(); original = protected_notices.orch.object_to_notice; pids = {}
    def paused(session, notice, request=""):
        outcome = original(session, notice, request); entered.set(); assert release.wait(5); return outcome
    def root_lock(statement):
        normalized = " ".join(statement.lower().split()); return "from trip" in normalized and "trip_membership" not in normalized and "for update" in normalized
    def backend_pid(conn):
        raw = conn.connection; return getattr(raw, "driver_connection", raw).info.backend_pid
    def before(conn, _cursor, statement, _parameters, _context, _executemany):
        if root_lock(statement):
            if current_thread().name == "notice-first": pids["first"] = backend_pid(conn)
            elif current_thread().name == "notice-second": pids["second"] = backend_pid(conn); second_reached.set()
    def after(_conn, _cursor, statement, _parameters, _context, _executemany):
        if root_lock(statement):
            if current_thread().name == "notice-first": first_lock.set()
            elif current_thread().name == "notice-second": second_acquired.set()
    outcomes = Queue()
    def run(actor, key):
        try: outcomes.put(_execute(test_engine, seed, key, 0, actor))
        except BaseException as exc: outcomes.put(exc)
    monkeypatch.setattr(protected_notices.orch, "object_to_notice", paused); event.listen(test_engine, "before_cursor_execute", before); event.listen(test_engine, "after_cursor_execute", after)
    try:
        first = Thread(target=lambda: run("account", "first"), name="notice-first", daemon=True); first.start(); assert first_lock.wait(5) and entered.wait(5)
        second = Thread(target=lambda: run("guest", "second"), name="notice-second", daemon=True); second.start(); assert second_reached.wait(5) and not second_acquired.is_set() and pids["first"] != pids["second"]
        observed = False; deadline = monotonic() + 5
        while monotonic() < deadline:
            with _factory(test_engine)() as observer: blockers = observer.scalar(text("select pg_blocking_pids(:blocked)"), {"blocked": pids["second"]})
            if pids["first"] in (blockers or []): observed = True; break
        assert observed, "PostgreSQL did not report the first Trip lock as blocker"
        release.set(); first.join(5); second.join(5)
    finally:
        event.remove(test_engine, "before_cursor_execute", before); event.remove(test_engine, "after_cursor_execute", after)
    assert not first.is_alive() and not second.is_alive() and second_acquired.is_set(); results = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert any(getattr(result, "plan_revision", None) == 1 and not result.replayed for result in results) and any(isinstance(result, StalePlanRevision) for result in results)
    with _factory(test_engine)() as db: assert db.get(Plan, seed.plan_id).revision == 1 and db.query(DecisionRound).filter(DecisionRound.plan_item_id == seed.item_id).count() == db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1
