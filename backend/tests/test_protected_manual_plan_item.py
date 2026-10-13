from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from app.api import main as api
from app.db import session as db_session
from app.db.models import AuthSession, GuestSession, MutationIdempotency, Plan, PlanItem, Trip, TripMembership, User
from app.domain import auth as auth_service
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
from app.domain.transactions.manual_plan_item import (
    ProtectedManualPlanItemAdd,
    StaticHmacKeyring,
    execute_protected_manual_plan_item_add,
)
from app.domain.transactions import manual_plan_item as protected_manual_items
from app.domain.transactions.durable import HmacSigningKey

_seed_records: list[tuple[str, str]] = []

@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str
    membership_id: str
    user_id: str
    token: str
    guest_membership_id: str
    guest_token: str
    guest_session_id: str
    after_id: str
    before_id: str


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(engine) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    token = f"protected-manual-{stamp}"
    with _factory(engine).begin() as db:
        user = User(name="Protected writer", email=f"protected-{stamp}@example.test")
        db.add(user)
        db.flush()
        trip = Trip(name="Protected trip", destination="Chicago", created_by_user_id=user.id)
        db.add(trip)
        db.flush()
        membership = TripMembership(trip_id=trip.id, user_id=user.id, role="participant", status="joined")
        guest_membership = TripMembership(trip_id=trip.id, user_id=None, guest_display_name="Manual guest", role="participant", join_method="invite_guest", status="joined")
        plan = Plan(trip_id=trip.id)
        db.add_all((membership, guest_membership, plan))
        db.flush()
        db.add(
            AuthSession(
                user_id=user.id,
                token_hash=auth_service.token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        guest_token = f"gst_protected-manual-{stamp}"
        guest_session = GuestSession(
            membership_id=guest_membership.id,
            trip_id=trip.id,
            token_hash=auth_service.token_hash(guest_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db.add(guest_session)
        after = PlanItem(
            plan_id=plan.id, day_index=1, day_date=date(2026, 10, 8), start_hour=10,
            duration_min=60, title="Before", place="Before", settledness="loose",
        )
        before = PlanItem(
            plan_id=plan.id, day_index=1, day_date=date(2026, 10, 8), start_hour=13,
            duration_min=60, title="After", place="After", settledness="loose",
        )
        db.add_all((after, before))
        db.flush()
        _seed_records.append((trip.id, user.id))
        return Seed(trip.id, plan.id, membership.id, user.id, token, guest_membership.id, guest_token, guest_session.id, after.id, before.id)


@pytest.fixture(autouse=True)
def _cleanup_seeded_rows(test_engine):
    _seed_records.clear()
    yield
    records = list(_seed_records)
    _seed_records.clear()
    if not records:
        return
    trip_ids = [trip_id for trip_id, _user_id in records]
    user_ids = [user_id for _trip_id, user_id in records]
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False)
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
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.membership_id,
        credential_kind="account",
        expected_user_id=seed.user_id,
    )


def _command(seed: Seed, *, key: str = "manual-item-key", **changes: object) -> ProtectedManualPlanItemAdd:
    values: dict[str, object] = {
        "plan_id": seed.plan_id,
        "idempotency_key": key,
        "expected_plan_revision": 0,
        "title": "Protected coffee stop",
        "after_item_id": seed.after_id,
        "before_item_id": seed.before_id,
        "start_hour": None,
    }
    values.update(changes)
    return ProtectedManualPlanItemAdd(**values)  # type: ignore[arg-type]


def _execute(engine, seed: Seed, command: ProtectedManualPlanItemAdd, kind: str = "account"):
    return execute_protected_manual_plan_item_add(
        session_factory=_factory(engine),
        scope=_scope(seed, kind),
        credential=CredentialProof.from_bearer_token(seed.guest_token if kind == "guest" else seed.token),
        command=command,
        keyring=StaticHmacKeyring(HmacSigningKey("test-v1", b"protected-manual-test-key")),
    )


def test_real_manual_item_effect_revision_and_replay(test_engine):
    seed = _seed(test_engine)
    command = _command(seed)
    first = _execute(test_engine, seed, command)
    with _factory(test_engine).begin() as db:
        item = db.get(PlanItem, first.payload["id"])
        assert item is not None
        db.delete(item)
    replay = _execute(test_engine, seed, command)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.payload == first.payload
    with _factory(test_engine)() as db:
        plan = db.get(Plan, seed.plan_id)
        assert plan is not None and plan.revision == 1
        assert db.get(PlanItem, first.payload["id"]) is None
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_real_manual_item_conflict_and_stale_command_leave_no_effect(test_engine):
    seed = _seed(test_engine)
    _execute(test_engine, seed, _command(seed))
    with pytest.raises(IdempotencyKeyReused):
        _execute(test_engine, seed, _command(seed, title="Changed title"))
    with pytest.raises(StalePlanRevision):
        _execute(test_engine, seed, _command(seed, key="stale-key"))
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(PlanItem).filter(PlanItem.plan_id == seed.plan_id).count() == 3
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_real_manual_item_invalid_gap_rolls_back_item_revision_and_ledger(test_engine):
    seed = _seed(test_engine)
    with pytest.raises(Exception, match="These stops are no longer adjacent"):
        _execute(test_engine, seed, _command(seed, before_item_id=seed.after_id))
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanItem).filter(PlanItem.plan_id == seed.plan_id).count() == 2
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_manual_same_gap_concurrency_proves_trip_contention_and_one_outcome(test_engine, monkeypatch):
    seed, entered, release, contender = _seed(test_engine), Event(), Event(), Event()
    original = protected_manual_items.add_manual_plan_item

    def paused(*args, **kwargs):
        inserted = original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "coordinator did not release manual writer"
        return inserted

    monkeypatch.setattr(protected_manual_items, "add_manual_plan_item", paused)
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if current_thread().name == "manual-contender" and "from trip" in normalized and "for update" in normalized:
            contender.set()

    outcomes: Queue[object] = Queue()
    def first():
        try: outcomes.put(_execute(test_engine, seed, _command(seed, key="same-gap-a", title="A")))
        except BaseException as exc: outcomes.put(exc)
    def second():
        try: outcomes.put(_execute(test_engine, seed, _command(seed, key="same-gap-b", title="B")))
        except BaseException as exc: outcomes.put(exc)
    event.listen(test_engine, "before_cursor_execute", capture)
    try:
        a = Thread(target=first, name="manual-first", daemon=True); a.start()
        assert entered.wait(5)
        b = Thread(target=second, name="manual-contender", daemon=True); b.start()
        assert contender.wait(5), "contender did not reach Trip FOR UPDATE"
        release.set(); a.join(5); b.join(5)
    finally:
        event.remove(test_engine, "before_cursor_execute", capture)
    results = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert any(getattr(result, "replayed", None) is False for result in results)
    assert any(isinstance(result, StalePlanRevision) for result in results)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(PlanItem).filter(PlanItem.plan_id == seed.plan_id).count() == 3
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_guest_manual_route_uses_real_auth_replay_revocation_and_membership(test_engine, monkeypatch):
    seed, factory = _seed(test_engine), _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "manual-route-test-key")
    headers = {"Authorization": f"Bearer {seed.guest_token}", "X-Trip-Id": seed.trip_id, "Idempotency-Key": "guest-manual", "X-Expected-Plan-Revision": "0"}
    body = {"title": "Guest manual", "after_item_id": seed.after_id, "before_item_id": seed.before_id}
    with TestClient(api.app) as client:
        first = client.post(f"/api/plans/{seed.plan_id}/items", headers=headers, json=body)
        replay = client.post(f"/api/plans/{seed.plan_id}/items", headers=headers, json=body)
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    with factory.begin() as db:
        db.get(GuestSession, seed.guest_session_id).revoked_at = datetime.now(timezone.utc)
    with TestClient(api.app) as client:
        revoked = client.post(f"/api/plans/{seed.plan_id}/items", headers={**headers, "Idempotency-Key": "guest-manual-revoked"}, json=body)
    assert revoked.status_code == 401
    with factory.begin() as db:
        db.get(GuestSession, seed.guest_session_id).revoked_at = None
        db.get(TripMembership, seed.guest_membership_id).status = "removed"
    with TestClient(api.app) as client:
        removed = client.post(f"/api/plans/{seed.plan_id}/items", headers={**headers, "Idempotency-Key": "guest-manual-removed"}, json=body)
    assert removed.status_code in {401, 403}


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
def test_manual_route_maps_typed_kernel_errors_without_diagnostics(test_engine, monkeypatch, failure, status):
    seed, factory = _seed(test_engine), _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", "1")
        monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "manual-route-test-key")
        monkeypatch.setattr(protected_manual_items, "execute_protected_manual_plan_item_add", lambda **_kwargs: (_ for _ in ()).throw(failure))
        try:
            with TestClient(api.app) as client:
                response = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    headers={"Authorization": f"Bearer {seed.token}", "Idempotency-Key": "typed-route", "X-Expected-Plan-Revision": "0"},
                    json={"title": "Typed route", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == status
    assert "secret-db-detail" not in response.text


def test_feature_off_route_retains_existing_header_free_behavior(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.delenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", raising=False)
        try:
            with TestClient(api.app) as client:
                response = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    json={"title": "Legacy manual stop", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == 200
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 0


def test_feature_off_route_ignores_malformed_optional_revision_header(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.delenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", raising=False)
        try:
            with TestClient(api.app) as client:
                response = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    headers={"X-Expected-Plan-Revision": "not-an-integer"},
                    json={"title": "Legacy malformed header", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == 200


def test_production_rejects_requested_protected_manual_item_gate(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", "1")
        monkeypatch.setenv("APP_ENV", "production")
        try:
            with TestClient(api.app) as client:
                response = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    headers={"Idempotency-Key": "prod-rejected", "X-Expected-Plan-Revision": "0"},
                    json={"title": "Must not activate", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["detail"] == "Protected manual-item writes are not available in production"


def test_feature_gated_route_uses_actual_protected_writer(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.setattr(api, "SessionLocal", factory)
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", "1")
        monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "isolated-test-key")
        headers = {
            "Authorization": f"Bearer {seed.token}",
            "Idempotency-Key": "route-protected-key",
            "X-Expected-Plan-Revision": "0",
        }
        try:
            with TestClient(api.app) as client:
                first = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    headers=headers,
                    json={"title": "Protected route stop", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
                replay = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    headers=headers,
                    json={"title": "Protected route stop", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_feature_gated_route_fails_closed_without_new_protocol_inputs(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory() as request_db:
        membership = request_db.get(TripMembership, seed.membership_id)
        api.app.dependency_overrides[api.get_session] = lambda: request_db
        api.app.dependency_overrides[api.current_membership] = lambda: membership
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES", "1")
        try:
            with TestClient(api.app) as client:
                response = client.post(
                    f"/api/plans/{seed.plan_id}/items",
                    json={"title": "Must not fall back", "after_item_id": seed.after_id, "before_item_id": seed.before_id},
                )
        finally:
            api.app.dependency_overrides.clear()
    assert response.status_code == 428
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.query(PlanItem).filter(PlanItem.plan_id == seed.plan_id).count() == 2
