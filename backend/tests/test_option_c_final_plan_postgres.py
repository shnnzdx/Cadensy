"""Option C slice 2: itinerary revision, readiness, and early close on PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.api import main as api
from app.db.itinerary_revision_trigger import FUNCTION_NAMES, TRIGGER_NAMES
from app.db.models import (
    Base,
    ChangeProposal,
    DecisionRound,
    Plan,
    PlanChange,
    PlanItem,
    Preference,
    Trip,
    TripMembership,
    User,
)
from app.domain.preferences.service import PreferenceData, save_mine
from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api_session(test_engine):
    connection = test_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False, future=True)
    try:
        yield db
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def client(api_session: Session, monkeypatch):
    factory = sessionmaker(
        bind=api_session.connection(),
        future=True,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(api, "SessionLocal", factory)
    api.app.dependency_overrides[api.get_session] = lambda: api_session
    with TestClient(api.app) as test_client:
        test_client.app_state["cadensy_test_db"] = api_session
        yield test_client
    api.app.dependency_overrides.clear()


def _revision(db: Session, plan_id: str) -> int:
    db.expire_all()
    return db.get(Plan, plan_id).itinerary_revision


def _item(db: Session, plan: Plan, title: str = "Museum") -> PlanItem:
    row = PlanItem(
        plan_id=plan.id,
        day_index=1,
        day_date=datetime(2026, 8, 14).date(),
        start_hour=10,
        duration_min=60,
        title=title,
        place="Chicago",
        tags=["walk"],
        dietary_tags=["none"],
        settledness="loose",
    )
    db.add(row)
    db.flush()
    return row


def _seed(db: Session):
    user = User(name="Final", email=f"final-{time.time_ns()}@example.invalid")
    db.add(user)
    db.flush()
    trip = Trip(
        name="Final",
        destination="Chicago",
        created_by_user_id=user.id,
        expected_group_size=2,
        currency="USD",
    )
    db.add(trip)
    db.flush()
    organizer = TripMembership(
        trip_id=trip.id, user_id=user.id, role="organizer", join_method="creator", status="joined"
    )
    db.add(organizer)
    db.flush()
    plan = Plan(trip_id=trip.id, currency="USD", status="active")
    db.add(plan)
    db.flush()
    return trip, organizer, plan


def test_itinerary_trigger_bumps_content_and_ignores_non_content(test_engine):
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True)
    with factory() as db:
        trip, _organizer, plan = _seed(db)
        other = Plan(trip_id=trip.id, currency="USD", status="active")
        db.add(other)
        db.flush()
        item = _item(db, plan)
        assert _revision(db, plan.id) == 1

        item.title = "Riverwalk"
        db.flush()
        assert _revision(db, plan.id) == 2

        item.tags = ["walk"]
        db.flush()
        assert _revision(db, plan.id) == 2
        item.tags = ["food"]
        db.flush()
        assert _revision(db, plan.id) == 3
        item.dietary_tags = ["none"]
        db.flush()
        assert _revision(db, plan.id) == 3
        item.dietary_tags = ["vegetarian"]
        db.flush()
        assert _revision(db, plan.id) == 4

        item.settledness = "touched"
        db.flush()
        assert _revision(db, plan.id) == 4
        item.settled_at = datetime.now(timezone.utc)
        db.flush()
        assert _revision(db, plan.id) == 4
        item.settledness = "settled"
        db.flush()
        assert _revision(db, plan.id) == 5

        item.plan_id = other.id
        db.flush()
        assert _revision(db, plan.id) == 6
        assert _revision(db, other.id) == 1

        db.delete(item)
        db.flush()
        assert _revision(db, other.id) == 2
        db.rollback()


def test_bulk_delete_bumps_once_per_row(test_engine):
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True)
    with factory() as db:
        _trip, _organizer, plan = _seed(db)
        _item(db, plan, "One")
        _item(db, plan, "Two")
        before = _revision(db, plan.id)
        db.execute(delete(PlanItem).where(PlanItem.plan_id == plan.id))
        db.flush()
        assert _revision(db, plan.id) == before + 2
        db.rollback()


def test_readiness_reasons_and_early_close(client: TestClient, api_session: Session):
    from tests.test_invites import _member_headers, _trip

    trip, organizer, participant = _trip(api_session)
    headers = _member_headers(client, organizer, trip)
    participant_headers = _member_headers(client, participant, trip)
    missing = client.get(f"/api/trips/{trip.id}/final-plan", headers=headers)
    assert missing.status_code == 200
    assert missing.json()["ready"] is False
    assert missing.json()["reasons"] == ["plan_not_active", "no_plan_items"]

    plan = Plan(trip_id=trip.id, currency="USD", status="blocked")
    api_session.add(plan)
    api_session.flush()
    blocked = client.post(
        f"/api/trips/{trip.id}/final-plan/approve",
        headers=headers,
        json={"itinerary_revision": 0},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "final_plan_not_ready"
    assert "plan_not_active" in blocked.json()["reasons"]

    plan.status = "active"
    item = _item(api_session, plan)
    api_session.flush()
    revision = _revision(api_session, plan.id)
    opened = DecisionRound(
        plan_item_id=item.id,
        options=[{"id": "keep", "label": "Keep"}],
        deadline=datetime.now(timezone.utc) + timedelta(hours=2),
        status="open",
    )
    api_session.add(opened)
    api_session.flush()
    open_round = client.get(f"/api/trips/{trip.id}/final-plan", headers=headers)
    assert "open_decision_round" in open_round.json()["reasons"]
    opened.status = "closed"
    proposal = ChangeProposal(
        plan_item_id=item.id,
        action_type="edit",
        requested_by_membership_id=organizer.id,
        status="waiting_affected_members",
    )
    api_session.add(proposal)
    api_session.flush()
    pending = client.get(f"/api/trips/{trip.id}/final-plan", headers=headers)
    assert pending.json()["reasons"] == ["pending_change_proposal"]
    proposal.status = "withdrawn"
    api_session.flush()
    ready = client.get(f"/api/trips/{trip.id}/final-plan", headers=headers)
    assert ready.json()["ready"] is True
    assert ready.json()["participants"]
    assert "membership_id" not in ready.json()["participants"][0]

    stale = client.post(
        f"/api/trips/{trip.id}/final-plan/approve",
        headers=headers,
        json={"itinerary_revision": revision + 99},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "itinerary_revision_stale"

    first = client.post(
        f"/api/trips/{trip.id}/final-plan/approve",
        headers=headers,
        json={"itinerary_revision": revision},
    )
    assert first.status_code == 200, first.text
    assert first.json()["final_plan_approved"] is False
    again = client.post(
        f"/api/trips/{trip.id}/final-plan/approve",
        headers=headers,
        json={"itinerary_revision": revision},
    )
    assert again.status_code == 200
    created = client.post(f"/api/trips/{trip.id}/invite", headers=headers)
    assert created.status_code == 200
    second = client.post(
        f"/api/trips/{trip.id}/final-plan/approve",
        headers=participant_headers,
        json={"itinerary_revision": revision},
    )
    assert second.status_code == 200, second.text
    assert second.json()["final_plan_approved"] is True
    closed = client.post(
        f"/api/trips/{trip.id}/final-plan/approve",
        headers=headers,
        json={"itinerary_revision": revision},
    )
    assert closed.status_code == 409
    assert closed.json()["detail"] == "invite_closed"
    joined = client.post(
        f"/api/invites/{created.json()['token']}/join",
        json={"display_name": "Late Guest"},
    )
    assert joined.status_code == 410
    assert joined.json()["detail"] == "invite_closed"
    assert (
        api_session.scalar(
            select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
        )
        == 2
    )


def _database_url(suffix: str) -> URL:
    source_url = make_url(os.environ["TEST_DATABASE_URL"])
    database = f"{source_url.database}_{suffix}"
    require_safe_test_database_name(database)
    return source_url.set(database=database)


def _recreate(url: URL) -> None:
    admin = create_engine(url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
    database = quote_ident(url.database or "")
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": url.database},
            )
            connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
            connection.execute(
                text(f"CREATE DATABASE {database} TEMPLATE template0 ENCODING 'UTF8'")
            )
    finally:
        admin.dispose()


def _defs(engine) -> dict[str, str]:
    found = {}
    with engine.connect() as connection:
        for name in FUNCTION_NAMES:
            found[f"fn:{name}"] = connection.execute(
                text("SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname = :name"),
                {"name": name},
            ).scalar_one()
        for trigger, table in TRIGGER_NAMES:
            found[f"tg:{trigger}"] = connection.execute(
                text("SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relname = :table AND t.tgname = :name"),
                {"table": table, "name": trigger},
            ).scalar_one()
    return {key: " ".join(value.split()) for key, value in found.items()}


def test_alembic_and_create_all_trigger_definitions_match():
    alembic_url = _database_url("option_c_alembic_test")
    create_url = _database_url("option_c_create_all_test")
    _recreate(alembic_url)
    _recreate(create_url)
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": alembic_url.render_as_string(hide_password=False),
            "DISABLE_SCHEDULER": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    create_engine_obj = create_engine(create_url, future=True)
    alembic_engine = create_engine(alembic_url, future=True)
    try:
        Base.metadata.create_all(create_engine_obj)
        assert _defs(alembic_engine) == _defs(create_engine_obj)
    finally:
        create_engine_obj.dispose()
        alembic_engine.dispose()


def _wait_for_lock(engine, needle: str) -> str:
    deadline = time.time() + 5
    seen: list[str] = []
    while time.time() < deadline:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT pid, wait_event_type, state, left(query, 180) "
                    "FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid()"
                )
            ).all()
            seen = [f"{row[0]} {row[1]} {row[2]} {row[3]}" for row in rows]
            query = connection.execute(
                text(
                    "SELECT query FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND query ILIKE :needle "
                    "AND pid <> pg_backend_pid()"
                ),
                {"needle": f"%{needle}%"},
            ).scalar()
        if query:
            return query
        time.sleep(0.05)
    raise AssertionError(f"no session waited on {needle}; activity={seen}")


def test_n3_n4_and_n5a_wait_on_the_trip_lock_without_deadlock(test_engine):
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True)
    with factory.begin() as db:
        trip, organizer, plan = _seed(db)
        item = _item(db, plan)
        trip_id = trip.id
        plan_id = plan.id
        item_id = item.id
        membership_id = organizer.id

    holder = test_engine.connect()
    transaction = holder.begin()
    holder.execute(text("SELECT id FROM trip WHERE id = :id FOR UPDATE"), {"id": trip_id})
    errors: list[BaseException] = []

    def _insert_item() -> None:
        connection = test_engine.connect()
        try:
            with connection.begin():
                connection.execute(text("SET lock_timeout = '8s'"))
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO plan_item (id, plan_id, day_index, day_date, start_hour, "
                        "title, place, tags, dietary_tags, is_meal, settledness, source, created_at) "
                        "VALUES ('n3item00000000000000000000000001', :plan, 1, DATE '2026-08-15', "
                        "11, 'Insert', 'Chicago', '[]'::json, '[]'::json, false, 'loose', 'mock', now())"
                    ),
                    {"plan": plan_id},
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    def _insert_change() -> None:
        connection = test_engine.connect()
        try:
            with connection.begin():
                connection.execute(text("SET lock_timeout = '8s'"))
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO plan_change (id, plan_id, plan_item_id, origin, patch, applied_at) "
                        "VALUES ('n4change0000000000000000000001', :plan, :item, 'notice', '{}'::json, now())"
                    ),
                    {"plan": plan_id, "item": item_id},
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    def _save_preferences() -> None:
        try:
            with factory() as db:
                db.execute(text("SET lock_timeout = '8s'"))
                membership = db.get(TripMembership, membership_id)
                save_mine(
                    db,
                    membership,
                    PreferenceData(preferred_start_date=datetime(2026, 8, 14).date()),
                )
                db.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    item_thread = threading.Thread(target=_insert_item)
    item_thread.start()
    item_thread.join(0.4)
    if not item_thread.is_alive():
        raise AssertionError(errors)
    _wait_for_lock(test_engine, "INSERT INTO plan_item")
    change_thread = threading.Thread(target=_insert_change)
    change_thread.start()
    _wait_for_lock(test_engine, "INSERT INTO plan_change")
    preference_thread = threading.Thread(target=_save_preferences)
    preference_thread.start()
    waiting = _wait_for_lock(test_engine, "FOR KEY SHARE")
    assert "trip" in waiting.lower()
    transaction.commit()
    holder.close()
    item_thread.join(15)
    change_thread.join(15)
    preference_thread.join(15)
    assert errors == []
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Preference).where(Preference.trip_membership_id == membership_id)) == 1
    with test_engine.begin() as connection:
        connection.execute(text("DELETE FROM activation_event WHERE trip_id = :trip"), {"trip": trip_id})
        connection.execute(text("DELETE FROM plan_change WHERE plan_id = :plan"), {"plan": plan_id})
        connection.execute(text("DELETE FROM preference WHERE trip_membership_id = :member"), {"member": membership_id})
        connection.execute(text("DELETE FROM plan_item WHERE plan_id = :plan"), {"plan": plan_id})
        connection.execute(text("DELETE FROM plan WHERE trip_id = :trip"), {"trip": trip_id})
        connection.execute(text("DELETE FROM trip_membership WHERE trip_id = :trip"), {"trip": trip_id})
        user_id = connection.execute(
            text("SELECT created_by_user_id FROM trip WHERE id = :trip"),
            {"trip": trip_id},
        ).scalar_one()
        connection.execute(text("DELETE FROM trip WHERE id = :trip"), {"trip": trip_id})
        connection.execute(text("DELETE FROM user_account WHERE id = :user"), {"user": user_id})
