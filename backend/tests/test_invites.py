from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api import main as api
from app.db.models import (
    InviteLink,
    MemberConstraint,
    MemberConstraintPrivate,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
    User,
)
from app.domain import auth


@pytest.fixture
def api_session(test_engine):
    connection = test_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(
        bind=connection,
        future=True,
        join_transaction_mode="create_savepoint",
    )()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def client(api_session: Session):
    api.app.dependency_overrides[api.get_session] = lambda: api_session
    with TestClient(api.app) as test_client:
        test_client.app_state["cadensy_test_db"] = api_session
        yield test_client
    api.app.dependency_overrides.clear()


def _user(db: Session, name: str, email: str | None = None) -> User:
    user = User(name=name, email=email or f"{name.lower()}-{uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    return user


def _trip(db: Session) -> tuple[Trip, TripMembership, TripMembership]:
    organizer_user = _user(db, "Mia")
    participant_user = _user(db, "Sam")
    trip = Trip(
        name="Chicago birthday",
        destination="Chicago",
        preferred_start_date=datetime(2026, 8, 14).date(),
        preferred_end_date=datetime(2026, 8, 17).date(),
        created_by_user_id=organizer_user.id,
    )
    db.add(trip)
    db.flush()
    organizer = TripMembership(
        trip_id=trip.id,
        user_id=organizer_user.id,
        role="organizer",
        status="joined",
    )
    participant = TripMembership(
        trip_id=trip.id,
        user_id=participant_user.id,
        role="participant",
        status="joined",
    )
    db.add_all([organizer, participant])
    db.flush()
    return trip, organizer, participant


def _member_headers(client: TestClient, membership: TripMembership, trip: Trip) -> dict:
    db = client.app_state["cadensy_test_db"]
    user = db.get(User, membership.user_id)
    assert user is not None
    user.password_hash = auth.hash_password("correct-horse")
    db.flush()
    login = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "correct-horse"},
    )
    assert login.status_code == 200
    return {
        "Authorization": f"Bearer {login.json()['token']}",
        "X-Trip-Id": trip.id,
    }


def _create_invite(client: TestClient, organizer: TripMembership, trip: Trip) -> dict:
    response = client.post(
        f"/api/trips/{trip.id}/invite",
        headers=_member_headers(client, organizer, trip),
    )
    assert response.status_code == 200
    return response.json()


def test_opening_invite_does_not_create_membership(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    before = api_session.scalar(
        select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
    )

    response = client.get(f"/api/invites/{invite['token']}")

    assert response.status_code == 200
    after = api_session.scalar(
        select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
    )
    assert after == before


def test_invite_preview_excludes_plan_items_and_preferences(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    trip.cover_image_url = "https://images.unsplash.com/chicago?ixid=test"
    trip.cover_image_source = "unsplash"
    trip.cover_attribution_name = "Trip Photographer"
    trip.cover_attribution_url = "https://unsplash.com/@trip-photo?utm_source=cadensy"
    trip.cover_source_url = "https://unsplash.com/photos/trip-cover?utm_source=cadensy"
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()
    api_session.add(
        PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=datetime(2026, 8, 14).date(),
            start_hour=10,
            title="Art Institute of Chicago",
            place="Michigan Avenue",
        )
    )
    constraint = MemberConstraint(
        trip_membership_id=organizer.id,
        kind="time_window",
        importance="required",
        params={"earliest_hour": 9.0},
    )
    api_session.add(constraint)
    api_session.flush()
    api_session.add(
        MemberConstraintPrivate(
            constraint_id=constraint.id,
            original_text="Do not leak this preference",
            visibility="planning_only",
        )
    )
    invite = _create_invite(client, organizer, trip)

    body = client.get(f"/api/invites/{invite['token']}").json()

    assert body == {
        "name": "Chicago birthday",
        "destination": "Chicago",
        "preferred_start_date": "2026-08-14",
        "preferred_end_date": "2026-08-17",
        "member_count": 2,
        "organizer_name": "Mia",
        "cover_image_url": "https://images.unsplash.com/chicago?ixid=test",
        "cover_image_source": "unsplash",
        "cover_attribution_name": "Trip Photographer",
        "cover_attribution_url": "https://unsplash.com/@trip-photo?utm_source=cadensy",
        "cover_source_url": "https://unsplash.com/photos/trip-cover?utm_source=cadensy",
    }
    assert "Art Institute" not in str(body)
    assert "preference" not in str(body).lower()


def test_plain_invite_token_is_never_stored(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)

    saved = api_session.scalar(select(InviteLink).where(InviteLink.id == invite["invite_id"]))

    assert saved.token_hash != invite["token"]
    assert saved.token_hash == hashlib.sha256(invite["token"].encode("utf-8")).hexdigest()


def test_revoked_invite_returns_404(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)

    revoke = client.post(
        f"/api/invites/{invite['invite_id']}/revoke",
        headers=_member_headers(client, organizer, trip),
    )
    assert revoke.status_code == 200

    assert client.get(f"/api/invites/{invite['token']}").status_code == 404
    assert client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "Lee"},
    ).status_code == 404


def test_expired_invite_returns_404(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    saved = api_session.scalar(select(InviteLink).where(InviteLink.id == invite["invite_id"]))
    saved.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    api_session.flush()

    assert client.get(f"/api/invites/{invite['token']}").status_code == 404


def test_guest_join_creates_participant_without_user(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)

    response = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "Guest Lee"},
    )

    assert response.status_code == 200
    body = response.json()
    membership = api_session.get(TripMembership, body["membership_id"])
    assert body["membership_id"] == membership.id
    assert body["trip_id"] == trip.id
    assert body["role"] == "participant"
    assert body["guest_token"].startswith("gst_")
    assert body["guest_expires_at"]
    assert membership.user_id is None
    assert membership.guest_display_name == "Guest Lee"
    assert membership.role == "participant"
    assert membership.join_method == "invite_guest"


def test_account_join_reuses_existing_user(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    existing_user = _user(api_session, "Alex", email="alex@example.com")
    invite = _create_invite(client, organizer, trip)
    before = api_session.scalar(select(func.count()).select_from(User))

    response = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "Alex New", "email": "Alex@Example.com"},
    )

    assert response.status_code == 200
    membership = api_session.get(TripMembership, response.json()["membership_id"])
    after = api_session.scalar(select(func.count()).select_from(User))
    assert after == before
    assert membership.user_id == existing_user.id
    assert membership.role == "participant"
    assert membership.join_method == "invite_login"


def test_account_join_returns_existing_membership_on_repeat(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)

    first = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "Alex", "email": "alex-repeat@example.com"},
    ).json()
    second = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "Alex", "email": "alex-repeat@example.com"},
    ).json()

    assert second["membership_id"] == first["membership_id"]


def test_non_organizer_cannot_create_invite(client: TestClient, api_session: Session):
    trip, _, participant = _trip(api_session)

    response = client.post(
        f"/api/trips/{trip.id}/invite",
        headers=_member_headers(client, participant, trip),
    )

    assert response.status_code == 403
