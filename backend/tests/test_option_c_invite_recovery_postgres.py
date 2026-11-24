"""Option C slice 1: HMAC recovery, lifecycle, and Alembic cleanup on PostgreSQL."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.api import main as api

from app.db.invite_cleanup import SCOPED_REVOKE_SQL
from app.db.models import InviteLink, Trip, TripMembership, User
from app.domain.trips.invite_hmac import (
    derive_invite_token,
    get_invite_keyring,
    reset_invite_hmac_keyring_cache,
    token_hash,
)
from app.domain.trips.invite_lifecycle import ensure_invite
from app.observability.invite_access_log import (
    install_invite_access_log_redaction,
    redact_invite_access_text,
)
from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REV1 = "c0a1e8d4b5f2"
REV2 = "c0b2f9e5a6d3"
PRIOR = "c8e1d4a90b27"


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


def _migration_database_url() -> URL:
    source_url = make_url(os.environ["TEST_DATABASE_URL"])
    if source_url.get_backend_name() != "postgresql" or not source_url.database:
        raise RuntimeError("Option C migration tests require TEST_DATABASE_URL")
    database = f"{source_url.database}_option_c_test"
    require_safe_test_database_name(database)
    return source_url.set(database=database)


def _recreate_database(url: URL) -> None:
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


def _run_alembic(url: URL, *arguments: str, succeeds: bool = True) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": url.render_as_string(hide_password=False),
            "DISABLE_SCHEDULER": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if succeeds:
        assert completed.returncode == 0, output
    else:
        assert completed.returncode != 0, output
    return output


def _engine(url: URL):
    return create_engine(url, future=True)


def _seed_trip(connection) -> tuple[str, str]:
    connection.execute(
        text(
            "INSERT INTO user_account (id, name, email, created_at) "
            "VALUES ('oc_user', 'Owner', 'oc-owner@example.invalid', now())"
        )
    )
    connection.execute(
        text(
            "INSERT INTO trip (id, name, destination, expected_group_size, currency, "
            "status, created_by_user_id, planning_facts_revision, created_at) "
            "VALUES ('oc_trip', 'Option C', 'Chicago', 2, 'USD', 'planning', "
            "'oc_user', 0, now())"
        )
    )
    connection.execute(
        text(
            "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, "
            "status, created_at) VALUES "
            "('oc_member', 'oc_trip', 'oc_user', 'organizer', 'creator', 'joined', now())"
        )
    )
    return "oc_trip", "oc_member"


def test_keyring_ignores_short_retired_keys_and_missing_ring_does_not_crash(monkeypatch):
    raw = "A" * 43
    monkeypatch.setenv(
        "CADENSY_INVITE_HMAC_KEY",
        json.dumps(
            {
                "v": 1,
                "active_key_id": 1,
                "keys": {"1": raw, "2": "aa", "nope": "%%%"},
            }
        ),
    )
    reset_invite_hmac_keyring_cache()
    ring = get_invite_keyring()
    assert ring is not None
    assert ring.active_key_id == 1
    assert 2 not in ring.keys
    token = derive_invite_token(ring, 1, "invite-id", "trip-id")
    assert token
    assert "=" not in token

    monkeypatch.delenv("CADENSY_INVITE_HMAC_KEY", raising=False)
    reset_invite_hmac_keyring_cache()
    assert get_invite_keyring() is None
    reset_invite_hmac_keyring_cache()


def test_malformed_keyring_is_absent():
    os.environ["CADENSY_INVITE_HMAC_KEY"] = "{not-json"
    reset_invite_hmac_keyring_cache()
    try:
        assert get_invite_keyring() is None
    finally:
        os.environ["CADENSY_INVITE_HMAC_KEY"] = (
            '{"v":1,"active_key_id":1,"keys":{"1":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}'
        )
        reset_invite_hmac_keyring_cache()


def test_scoped_revoke_sql_does_not_revoke_every_unrevoked_row():
    compact = " ".join(SCOPED_REVOKE_SQL.split())
    assert "NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)" in compact
    assert "WHERE revoked_at IS NULL;" not in compact
    assert compact.index("NOT (token_scheme") > compact.index("WHERE")


def test_access_log_filter_removes_the_token(caplog):
    del caplog
    install_invite_access_log_redaction()
    token = "super-secret-invite-token"
    preview = redact_invite_access_text(
        '127.0.0.1 "POST /api/invites/preview HTTP/1.1"'
    )
    assert preview.endswith("/api/invites/preview HTTP/1.1\"")
    redacted = redact_invite_access_text(
        f'127.0.0.1 "GET /api/invites/{token}/join?token={token} HTTP/1.1"'
    )
    assert token not in redacted
    assert "<redacted>" in redacted

    logger = logging.getLogger("uvicorn.access")
    assert any(type(item).__name__ == "InviteAccessLogRedactionFilter" for item in logger.filters)
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = _Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info(
            '%s - "%s" %s',
            "127.0.0.1",
            f"GET /api/invites/{token} HTTP/1.1",
            "200",
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    assert captured
    assert token not in captured[0]
    assert "<redacted>" in captured[0]


def test_ensure_replace_window_and_nostore(client: TestClient, api_session: Session):
    from tests.test_invites import _member_headers, _trip

    trip, organizer, _participant = _trip(api_session)
    headers = _member_headers(client, organizer, trip)
    created = client.post(f"/api/trips/{trip.id}/invite", headers=headers)
    assert created.status_code == 200, created.text
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["status"] == "active_hmac"
    assert body["token"]
    remaining = datetime.fromisoformat(body["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(days=179) < remaining < timedelta(days=181)

    again = client.post(f"/api/trips/{trip.id}/invite", headers=headers)
    assert again.status_code == 200
    assert again.json()["invite_id"] == body["invite_id"]
    assert again.json()["token"] == body["token"]

    listed = client.get(f"/api/trips/{trip.id}/invite", headers=headers)
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json()["token"] == body["token"]

    replaced = client.post(
        f"/api/trips/{trip.id}/invite/replace",
        headers={**headers, "Idempotency-Key": "replace-once"},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["invite_id"] != body["invite_id"]
    assert replaced.json()["expires_at"] == body["expires_at"]
    replay = client.post(
        f"/api/trips/{trip.id}/invite/replace",
        headers={**headers, "Idempotency-Key": "replace-once"},
    )
    assert replay.status_code == 200
    assert replay.json()["invite_id"] == replaced.json()["invite_id"]
    assert replay.json()["idempotent_replay"] is True

    old = client.get(f"/api/invites/{body['token']}")
    assert old.status_code == 410
    assert old.json()["detail"] == "invite_revoked"
    preview = client.post(
        "/api/invites/preview",
        json={"token": replaced.json()["token"]},
    )
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert "token" not in preview.text

    legacy = "legacy-open-token"
    api_session.add(
        InviteLink(
            trip_id=trip.id,
            token_hash=token_hash(legacy),
            is_primary=True,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
            revoked_at=datetime.now(timezone.utc),
        )
    )
    # The live HMAC row is the only unrevoked invite. A second unrevoked
    # non-HMAC row is rejected by the unique index, so classify that state
    # on a trip that has never minted.
    other, other_organizer, _ = _trip(api_session)
    api_session.add(
        InviteLink(
            trip_id=other.id,
            token_hash=token_hash("other-legacy-token"),
            is_primary=True,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
    )
    api_session.flush()
    other_headers = _member_headers(client, other_organizer, other)
    listed_other = client.get(f"/api/trips/{other.id}/invite", headers=other_headers)
    assert listed_other.status_code == 200
    assert listed_other.json()["status"] == "non_hmac"
    assert "token" not in listed_other.json()
    refused = client.get("/api/invites/other-legacy-token")
    assert refused.status_code == 410
    assert refused.json()["detail"] == "invite_revoked"


def test_missing_keyring_returns_503_without_stopping_the_process(client, api_session, monkeypatch):
    from tests.test_invites import _member_headers, _trip

    trip, organizer, _participant = _trip(api_session)
    headers = _member_headers(client, organizer, trip)
    monkeypatch.delenv("CADENSY_INVITE_HMAC_KEY", raising=False)
    reset_invite_hmac_keyring_cache()
    try:
        response = client.post(f"/api/trips/{trip.id}/invite", headers=headers)
        assert response.status_code == 503
        assert response.json()["detail"] == "invite_hmac_unavailable"
        listed = client.get(f"/api/trips/{trip.id}/invite", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["status"] == "none"
    finally:
        reset_invite_hmac_keyring_cache()


def test_concurrent_ensure_yields_one_active_invite(test_engine):
    factory = sessionmaker(bind=test_engine, expire_on_commit=False, future=True)
    with factory.begin() as db:
        user = User(name="Concurrent", email="concurrent-invite@example.invalid")
        db.add(user)
        db.flush()
        trip = Trip(
            name="Concurrent",
            destination="Chicago",
            expected_group_size=2,
            currency="USD",
            created_by_user_id=user.id,
        )
        db.add(trip)
        db.flush()
        organizer = TripMembership(
            trip_id=trip.id,
            user_id=user.id,
            role="organizer",
            join_method="creator",
            status="joined",
        )
        db.add(organizer)
        db.flush()
        trip_id = trip.id
        organizer_id = organizer.id

    errors: list[BaseException] = []
    ids: list[str] = []

    def _ensure() -> None:
        try:
            with factory() as db:
                membership = db.get(TripMembership, organizer_id)
                snapshot = ensure_invite(db, trip_id, membership)
                db.commit()
                if snapshot.invite_id:
                    ids.append(snapshot.invite_id)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=_ensure) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert len(ids) == 2
    assert ids[0] == ids[1]
    with factory() as db:
        count = db.scalar(
            select(func.count())
            .select_from(InviteLink)
            .where(InviteLink.trip_id == trip_id, InviteLink.revoked_at.is_(None))
        )
    assert count == 1


def test_alembic_upgrade_cleanup_rerun_and_downgrade_refusals():
    url = _migration_database_url()
    _recreate_database(url)
    engine = _engine(url)
    try:
        upgraded = _run_alembic(url, "upgrade", "head")
        assert f"{PRIOR} -> {REV1}" in upgraded
        assert f"{REV1} -> {REV2}" in upgraded
        current = _run_alembic(url, "current")
        assert REV2 in current
        with engine.begin() as connection:
            marker = connection.execute(
                text(
                    "SELECT name FROM cadensy_invite_cleanup_marker "
                    "WHERE name = 'rev2_first_apply'"
                )
            ).scalar_one()
            assert marker == "rev2_first_apply"
            _seed_trip(connection)
            connection.execute(
                text(
                    "INSERT INTO invite_link (id, trip_id, token_hash, is_primary, "
                    "token_scheme, key_id, created_at) VALUES "
                    "('hmac_live', 'oc_trip', :token_hash, true, 'hmac_v1', 1, now())"
                ),
                {"token_hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO plan (id, trip_id, revision, itinerary_revision, status, "
                    "currency, needs_refresh, created_at) VALUES "
                    "('oc_plan', 'oc_trip', 0, 0, 'active', 'USD', false, now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO final_plan_approval "
                    "(id, trip_id, membership_id, approved_at, bound_itinerary_revision) "
                    "VALUES ('oc_approval', 'oc_trip', 'oc_member', now(), 0)"
                )
            )

        _run_alembic(url, "downgrade", REV1)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO invite_link (id, trip_id, token_hash, is_primary, "
                    "token_scheme, created_at) VALUES "
                    "('legacy_live', 'oc_trip', :token_hash, false, 'legacy_random', now())"
                ),
                {"token_hash": "b" * 64},
            )
        rerun = _run_alembic(url, "upgrade", "head")
        assert "final_plan_approval is not empty" not in rerun
        with engine.begin() as connection:
            hmac_revoked = connection.execute(
                text("SELECT revoked_at FROM invite_link WHERE id = 'hmac_live'")
            ).scalar_one()
            legacy_revoked = connection.execute(
                text("SELECT revoked_at FROM invite_link WHERE id = 'legacy_live'")
            ).scalar_one()
            approvals = connection.execute(
                text("SELECT count(*) FROM final_plan_approval")
            ).scalar_one()
            assert hmac_revoked is None
            assert legacy_revoked is not None
            assert approvals == 1

        _run_alembic(url, "check")

        refused_hmac = _run_alembic(url, "downgrade", PRIOR, succeeds=False)
        assert "hmac_v1 invite rows exist" in refused_hmac

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM invite_link WHERE token_scheme = 'hmac_v1'"))
        refused_approval = _run_alembic(url, "downgrade", PRIOR, succeeds=False)
        assert "final_plan_approval rows exist" in refused_approval
    finally:
        engine.dispose()


def test_rev2_first_apply_abort_leaves_no_marker_row():
    url = _migration_database_url()
    _recreate_database(url)
    engine = _engine(url)
    try:
        _run_alembic(url, "upgrade", REV1)
        with engine.begin() as connection:
            _seed_trip(connection)
            connection.execute(
                text(
                    "INSERT INTO final_plan_approval "
                    "(id, trip_id, membership_id, approved_at, bound_itinerary_revision) "
                    "VALUES ('oc_approval', 'oc_trip', 'oc_member', now(), 0)"
                )
            )
        failed = _run_alembic(url, "upgrade", REV2, succeeds=False)
        assert "final_plan_approval is not empty" in failed
        with engine.begin() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            marker_rows = connection.execute(
                text("SELECT count(*) FROM cadensy_invite_cleanup_marker")
            ).scalar_one()
            approvals = connection.execute(
                text("SELECT count(*) FROM final_plan_approval")
            ).scalar_one()
        assert version == REV1
        assert marker_rows == 0
        assert approvals == 1
    finally:
        engine.dispose()


def test_empty_rev1_downgrade_drops_the_marker_table():
    url = _migration_database_url()
    _recreate_database(url)
    engine = _engine(url)
    try:
        _run_alembic(url, "upgrade", REV1)
        _run_alembic(url, "downgrade", PRIOR)
        with engine.begin() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
        assert "cadensy_invite_cleanup_marker" not in names
        assert "final_plan_approval" not in names
    finally:
        engine.dispose()
