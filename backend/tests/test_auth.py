from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import main as api
from app.db.models import Trip, TripMembership, User
from app.domain import auth


@contextmanager
def _client(db: Session):
    api.app.dependency_overrides[api.get_session] = lambda: db
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def test_login_returns_token_and_trip_membership(db: Session, full_trip: dict):
    user = db.get(User, full_trip["members"][0].user_id)
    full_trip["members"][0].status = "joined"
    user.email = "organizer@cadensy.local"
    user.password_hash = auth.hash_password("12345678")
    db.flush()

    with _client(db) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "organizer@cadensy.local", "password": "12345678"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["default_membership"]["trip_id"] == full_trip["trip"].id
    assert body["default_membership"]["role"] == "organizer"


def test_login_returns_no_memberships_for_account_without_trips(db: Session):
    user = User(
        name="Solo User",
        email="solo@cadensy.local",
        password_hash=auth.hash_password("12345678"),
    )
    db.add(user)
    db.flush()

    with _client(db) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "solo@cadensy.local", "password": "12345678"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["memberships"] == []
    assert body["default_membership"] is None


def test_bearer_token_uses_membership_for_requested_trip(db: Session, full_trip: dict):
    user = db.get(User, full_trip["members"][0].user_id)
    user.email = "organizer@cadensy.local"
    user.password_hash = auth.hash_password("12345678")
    other_trip = full_trip["trip"].__class__(
        name="Other trip",
        destination="New York",
        created_by_user_id=user.id,
    )
    db.add(other_trip)
    db.flush()
    other_membership = TripMembership(
        trip_id=other_trip.id,
        user_id=user.id,
        role="participant",
        join_method="invite_login",
        status="joined",
    )
    db.add(other_membership)
    db.flush()

    with _client(db) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "organizer@cadensy.local", "password": "12345678"},
        ).json()
        response = client.get(
            "/api/me",
            headers={
                "Authorization": f"Bearer {login['token']}",
                "X-Trip-Id": other_trip.id,
            },
        )

    assert response.status_code == 200
    assert response.json()["membership_id"] == other_membership.id
    assert response.json()["role"] == "participant"


def test_bad_password_is_rejected(db: Session, full_trip: dict):
    user = db.get(User, full_trip["members"][0].user_id)
    user.email = "organizer@cadensy.local"
    user.password_hash = auth.hash_password("12345678")
    db.flush()

    with _client(db) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "organizer@cadensy.local", "password": "wrong"},
        )

    assert response.status_code == 401


def test_register_creates_normalized_account_and_authenticated_session(db: Session):
    with _client(db) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "name": "  Jiayi Chen  ",
                "email": "  Jiayi@Example.COM ",
                "password": "correct-horse",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["token"]
    assert body["user"]["name"] == "Jiayi Chen"
    assert body["user"]["email"] == "jiayi@example.com"
    assert body["memberships"] == []
    user = db.get(User, body["user"]["id"])
    assert auth.verify_password("correct-horse", user.password_hash)


def test_register_rejects_duplicate_email_case_insensitively(db: Session):
    db.add(
        User(
            name="Existing",
            email="traveler@example.com",
            password_hash=auth.hash_password("12345678"),
        )
    )
    db.flush()

    with _client(db) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Another",
                "email": "TRAVELER@example.com",
                "password": "12345678",
            },
        )

    assert response.status_code == 409


def test_new_account_can_read_profile_and_create_first_trip(db: Session):
    with _client(db) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "name": "First Timer",
                "email": "first@example.com",
                "password": "12345678",
            },
        ).json()
        headers = {"Authorization": f"Bearer {registered['token']}"}

        account = client.get("/api/account", headers=headers)
        empty_trips = client.get("/api/trips", headers=headers)
        created = client.post(
            "/api/trips",
            headers=headers,
            json={"name": "First trip", "destination": "Chicago"},
        )

    assert account.status_code == 200
    assert account.json()["name"] == "First Timer"
    assert empty_trips.status_code == 200
    assert empty_trips.json() == []
    assert created.status_code == 200
    body = created.json()
    assert body["member"]["role"] == "organizer"
    assert body["member"]["membership_id"] == body["membership_id"]
    assert db.get(Trip, body["id"]).created_by_user_id == registered["user"]["id"]
