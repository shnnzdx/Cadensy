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
from app.db.models import AuthSession, GuestSession, MutationIdempotency, Plan, PlanChange, PlanItem, Trip, TripMembership, UpdateNotice, User
from app.domain import auth as auth_service
from app.domain.transactions import (
    CredentialProof,
    DuplicatePlans,
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
from app.domain.transactions import booking as protected_booking
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
    foreign_item_id: str | None = None


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(engine, *, with_foreign: bool = False) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    account_token, guest_token = f"booking-account-{stamp}", f"gst_booking-{stamp}"
    with _factory(engine).begin() as db:
        account = User(name="Booking account", email=f"booking-account-{stamp}@example.test")
        owner = User(name="Booking owner", email=f"booking-owner-{stamp}@example.test")
        db.add_all((account, owner)); db.flush()
        trip = Trip(name="Booking trip", destination="Chicago", created_by_user_id=owner.id)
        db.add(trip); db.flush()
        account_member = TripMembership(trip_id=trip.id, user_id=account.id, role="participant", status="joined")
        guest_member = TripMembership(trip_id=trip.id, user_id=None, guest_display_name="Booking guest", role="participant", join_method="invite_guest", status="joined")
        plan = Plan(trip_id=trip.id)
        db.add_all((account_member, guest_member, plan)); db.flush()
        guest_session = GuestSession(membership_id=guest_member.id, trip_id=trip.id, token_hash=auth_service.token_hash(guest_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        db.add_all((
            AuthSession(user_id=account.id, token_hash=auth_service.token_hash(account_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
            guest_session,
        ))
        item = PlanItem(plan_id=plan.id, day_index=1, day_date=date(2026, 10, 8), start_hour=12, duration_min=60, title="Booking item", place="Chicago", settledness="loose")
        db.add(item); db.flush()
        foreign_item_id = None
        if with_foreign:
            foreign_trip = Trip(name="Foreign booking trip", destination="Detroit", created_by_user_id=owner.id)
            db.add(foreign_trip); db.flush()
            foreign_plan = Plan(trip_id=foreign_trip.id)
            db.add(foreign_plan); db.flush()
            foreign_item = PlanItem(plan_id=foreign_plan.id, day_index=1, day_date=date(2026, 10, 9), start_hour=12, duration_min=60, title="Foreign booking item", place="Detroit", settledness="loose")
            db.add(foreign_item); db.flush()
            foreign_item_id = foreign_item.id
            _records.append((foreign_trip.id, (owner.id,)))
        _records.append((trip.id, (account.id, owner.id)))
        return Seed(trip.id, plan.id, item.id, account_member.id, account.id, account_token, guest_member.id, guest_token, guest_session.id, foreign_item_id)


@pytest.fixture(autouse=True)
def _cleanup(test_engine):
    _records.clear()
    yield
    rows, _records[:] = list(_records), []
    if not rows:
        return
    trip_ids = [row[0] for row in rows]
    user_ids = [user_id for _, users in rows for user_id in users]
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(UpdateNotice).filter(UpdateNotice.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(PlanChange).filter(PlanChange.plan_id.in_(select(Plan.id).where(Plan.trip_id.in_(trip_ids)))).delete(synchronize_session=False)
        db.query(GuestSession).filter(GuestSession.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(PlanItem).filter(PlanItem.plan_id.in_(select(Plan.id).where(Plan.trip_id.in_(trip_ids)))).delete(synchronize_session=False)
        db.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)


def _scope(seed: Seed, kind: str = "account") -> PreliminaryActorScope:
    if kind == "guest":
        return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.guest_membership_id, credential_kind="guest")
    return PreliminaryActorScope(trip_id=seed.trip_id, membership_id=seed.account_membership_id, credential_kind="account", expected_user_id=seed.account_user_id)


def _execute(engine, seed: Seed, *, key="booking-key", revision=0, booked=True, kind="account", item_id: str | None = None):
    token = seed.guest_token if kind == "guest" else seed.account_token
    return protected_booking.execute_protected_booking(
        session_factory=_factory(engine), scope=_scope(seed, kind), credential=CredentialProof.from_bearer_token(token),
        command=protected_booking.ProtectedBooking(item_id or seed.item_id, key, revision, booked),
        keyring=protected_booking.StaticHmacKeyring(HmacSigningKey("test-v1", b"protected-booking-test-key")),
    )


def test_real_booking_book_replay_unbook_and_noop_are_atomic(test_engine):
    seed = _seed(test_engine)
    first = _execute(test_engine, seed, booked=True)
    replay = _execute(test_engine, seed, booked=True)
    unbook = _execute(test_engine, seed, key="booking-unbook", revision=1, booked=False)
    noop = _execute(test_engine, seed, key="booking-noop", revision=2, booked=False)
    assert first.replayed is False and replay.replayed is True and replay.payload == first.payload
    assert unbook.plan_revision == 2 and noop.plan_revision == 2
    with _factory(test_engine)() as db:
        assert db.get(PlanItem, seed.item_id).settledness == "settled"
        assert db.get(Plan, seed.plan_id).revision == 2
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 2
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 2
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 3


def test_booking_same_key_replay_different_payload_conflict_and_stale_are_safe(test_engine):
    seed = _seed(test_engine)
    _execute(test_engine, seed)
    with pytest.raises(Exception, match="Idempotency key"):
        _execute(test_engine, seed, booked=False)
    with pytest.raises(StalePlanRevision):
        _execute(test_engine, seed, key="booking-stale", revision=0, booked=False)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1


def test_booking_foreign_item_is_not_locked_or_modified(test_engine):
    seed = _seed(test_engine, with_foreign=True)
    assert seed.foreign_item_id is not None
    child_probes: list[str] = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if "from plan_item" in normalized and "for update" in normalized and seed.foreign_item_id in str(parameters):
            child_probes.append(normalized)

    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(protected_booking.BookingItemMissing):
            _execute(test_engine, seed, key="foreign-item", item_id=seed.foreign_item_id)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    assert len(child_probes) == 1
    assert "plan_item.id" in child_probes[0] and "plan_item.plan_id" in child_probes[0]
    with _factory(test_engine)() as db:
        foreign_item = db.get(PlanItem, seed.foreign_item_id)
        assert foreign_item is not None and foreign_item.settledness == "loose"
        assert db.get(Plan, foreign_item.plan_id).revision == 0
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanChange).count() == 0
        assert db.query(UpdateNotice).count() == 0
        assert db.query(MutationIdempotency).count() == 0


def test_booking_failure_rolls_back_item_change_notice_revision_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine)
    original = protected_booking.DurableCommandKernel._checkpoint
    def fail_after_effect(self, point):
        if point == "after_domain_effect":
            raise RuntimeError("injected local failure")
        return original(self, point)
    monkeypatch.setattr(protected_booking.DurableCommandKernel, "_checkpoint", fail_after_effect)
    with pytest.raises(RuntimeError, match="injected local failure"):
        _execute(test_engine, seed)
    with _factory(test_engine)() as db:
        assert db.get(PlanItem, seed.item_id).settledness == "loose"
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 0
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed.trip_id).count() == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_booking_concurrent_commands_observe_trip_lock_and_only_one_effect(test_engine, monkeypatch):
    seed, entered, release, contender = _seed(test_engine), Event(), Event(), Event()
    original = protected_booking.orch.set_booking_status_for_actor

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "coordinator did not release first booking"
        return result

    monkeypatch.setattr(protected_booking.orch, "set_booking_status_for_actor", paused)
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if current_thread().name == "booking-contender" and "from trip" in normalized and "for update" in normalized:
            contender.set()

    results: Queue[object] = Queue()
    def run_a():
        try: results.put(_execute(test_engine, seed, key="booking-first"))
        except BaseException as exc: results.put(exc)
    def run_b():
        try: results.put(_execute(test_engine, seed, key="booking-second"))
        except BaseException as exc: results.put(exc)
    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        first = Thread(target=run_a, name="booking-first", daemon=True); first.start()
        assert entered.wait(5)
        second = Thread(target=run_b, name="booking-contender", daemon=True); second.start()
        assert contender.wait(5), "contender did not reach Trip FOR UPDATE"
        release.set(); first.join(5); second.join(5)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    outcomes = [results.get_nowait(), results.get_nowait()]
    assert any(getattr(value, "replayed", None) is False for value in outcomes)
    assert any(isinstance(value, StalePlanRevision) for value in outcomes)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(PlanChange).filter(PlanChange.plan_id == seed.plan_id).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_guest_booking_route_uses_real_auth_and_revocation_checks(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_BOOKING_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "booking-route-test-key")
    headers = {"Authorization": f"Bearer {seed.guest_token}", "X-Trip-Id": seed.trip_id, "Idempotency-Key": "guest-booking", "X-Expected-Plan-Revision": "0"}
    with TestClient(api.app) as client:
        first = client.patch(f"/api/plans/items/{seed.item_id}/booking", headers=headers, json={"booked": True})
        replay = client.patch(f"/api/plans/items/{seed.item_id}/booking", headers=headers, json={"booked": True})
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    with factory.begin() as db:
        db.get(GuestSession, seed.guest_session_id).revoked_at = datetime.now(timezone.utc)
    with TestClient(api.app) as client:
        revoked = client.patch(f"/api/plans/items/{seed.item_id}/booking", headers={**headers, "Idempotency-Key": "guest-revoked"}, json={"booked": False})
    assert revoked.status_code == 401
    with factory.begin() as db:
        db.get(GuestSession, seed.guest_session_id).revoked_at = None
        db.get(TripMembership, seed.guest_membership_id).status = "removed"
    with TestClient(api.app) as client:
        removed = client.patch(f"/api/plans/items/{seed.item_id}/booking", headers={**headers, "Idempotency-Key": "guest-removed"}, json={"booked": False})
    assert removed.status_code in {401, 403}


def test_booking_feature_off_retains_legacy_route_without_protocol_headers(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.account_membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.delenv("CADENSY_B2C_PROTECTED_BOOKING_WRITES", raising=False)
        try:
            with TestClient(api.app) as client:
                response = client.patch(f"/api/plans/items/{seed.item_id}/booking", json={"booked": True})
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == 200
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 0


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (LockUnavailable("secret-db-detail"), 503),
        (RetriableDeadlock("secret-db-detail"), 503),
        (MissingPlan("secret-db-detail"), 404),
        (DuplicatePlans("secret-db-detail"), 409),
        (UnknownHmacKeyVersion("secret-db-detail"), 409),
        (UnsupportedOutcomeContract("secret-db-detail"), 409),
        (PlanRevisionOverflow("secret-db-detail"), 409),
        (UnsafeDurableOutcome("secret-db-detail"), 409),
        (TransactionDatabaseFailure("secret-db-detail"), 503),
    ],
)
def test_booking_route_maps_typed_kernel_errors_without_diagnostics(test_engine, monkeypatch, failure, status):
    seed, factory = _seed(test_engine), _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.account_membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_BOOKING_WRITES", "1")
        monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "booking-route-test-key")
        monkeypatch.setattr(protected_booking, "execute_protected_booking", lambda **_kwargs: (_ for _ in ()).throw(failure))
        try:
            with TestClient(api.app) as client:
                response = client.patch(
                    f"/api/plans/items/{seed.item_id}/booking",
                    headers={"Authorization": f"Bearer {seed.account_token}", "Idempotency-Key": "typed-route", "X-Expected-Plan-Revision": "0"},
                    json={"booked": True},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == status
    assert "secret-db-detail" not in response.text
