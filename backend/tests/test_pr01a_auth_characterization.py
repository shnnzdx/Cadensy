"""PR-01A historical findings retained as PR-01B security regressions.

The legacy raw-membership-header behavior is documented in the PR-01A record;
these public-API tests now verify that PR-01B removed it. They use only
synthetic identities through the HTTP API seam and the disposable pytest DB.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import main as api
from app.db.models import AuthSession, Trip, TripMembership, User
from app.domain import auth


@contextmanager
def _client(db: Session):
    api.app.dependency_overrides[api.get_session] = lambda: db
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def _user(db: Session, *, label: str, password: str = "correct-horse") -> User:
    email_local_part = label.lower().replace(" ", "-")
    user = User(
        name=f"{label} User",
        email=f"{email_local_part}-{uuid4().hex}@example.test",
        password_hash=auth.hash_password(password),
    )
    db.add(user)
    db.flush()
    return user


def _trip_membership(
    db: Session,
    *,
    user: User,
    label: str,
    role: str = "participant",
) -> tuple[Trip, TripMembership]:
    trip = Trip(
        name=f"{label} trip",
        destination="Synthetic City",
        created_by_user_id=user.id,
    )
    db.add(trip)
    db.flush()
    membership = TripMembership(
        trip_id=trip.id,
        user_id=user.id,
        role=role,
        status="joined",
    )
    db.add(membership)
    db.flush()
    return trip, membership


def _login(client: TestClient, email: str, password: str = "correct-horse") -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def _create_invite(client: TestClient, trip: Trip, organizer_token: str) -> dict:
    response = client.post(
        f"/api/trips/{trip.id}/invite",
        headers={
            "Authorization": f"Bearer {organizer_token}",
            "X-Trip-Id": trip.id,
        },
    )
    assert response.status_code == 200
    return response.json()


def _guest_trip(db: Session) -> tuple[Trip, TripMembership, User]:
    organizer_user = _user(db, label="Guest Organizer")
    trip, membership = _trip_membership(
        db, user=organizer_user, label="Guest Flow", role="organizer"
    )
    return trip, membership, organizer_user


def test_account_login_selects_its_own_trip_and_rejects_another_accounts_trip(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "0")
    account = _user(db, label="Account Owner")
    own_trip, own_membership = _trip_membership(db, user=account, label="Own")
    foreign_account = _user(db, label="Foreign Owner")
    foreign_trip, _ = _trip_membership(db, user=foreign_account, label="Foreign")

    with _client(db) as client:
        token = _login(client, account.email)
        own_response = client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {token}", "X-Trip-Id": own_trip.id},
        )
        foreign_response = client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {token}", "X-Trip-Id": foreign_trip.id},
        )

    assert own_response.status_code == 200
    assert own_response.json()["membership_id"] == own_membership.id
    assert foreign_response.status_code == 403


def test_logout_revokes_account_bearer_for_subsequent_account_requests(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "0")
    account = _user(db, label="Logout Account")

    with _client(db) as client:
        token = _login(client, account.email)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/account", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        after_logout = client.get("/api/account", headers=headers)

    saved = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.token_hash(token)))
    assert saved is not None
    assert saved.revoked_at is not None
    assert after_logout.status_code == 401


def test_expired_account_bearer_is_rejected(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "0")
    account = _user(db, label="Expiry Account")

    with _client(db) as client:
        token = _login(client, account.email)
        saved = db.scalar(
            select(AuthSession).where(AuthSession.token_hash == auth.token_hash(token))
        )
        assert saved is not None
        saved.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        response = client.get("/api/account", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_guest_invite_preview_join_and_reopen_uses_a_bounded_bearer(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("DEV_ALLOW_MEMBERSHIP_HEADER", raising=False)
    trip, _, organizer_user = _guest_trip(db)

    with _client(db) as client:
        invite = _create_invite(client, trip, _login(client, organizer_user.email))
        preview = client.get(f"/api/invites/{invite['token']}")
        joined = client.post(
            f"/api/invites/{invite['token']}/join",
            json={"display_name": "Synthetic Guest"},
        )
        body = joined.json()
        reopened = client.get(
            f"/api/trips/{trip.id}",
            headers={
                "Authorization": f"Bearer {body['guest_token']}",
                "X-Trip-Id": trip.id,
            },
        )

    assert preview.status_code == 200
    assert joined.status_code == 200
    assert body["membership_id"]
    assert body["trip_id"] == trip.id
    assert body["role"] == "participant"
    assert body["guest_token"].startswith("gst_")
    assert body["guest_expires_at"]
    assert reopened.status_code == 200


def test_secure_default_rejects_bare_membership_id(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("DEV_ALLOW_MEMBERSHIP_HEADER", raising=False)
    trip, organizer, _ = _guest_trip(db)

    with _client(db) as client:
        response = client.get(
            f"/api/trips/{trip.id}", headers={"X-Membership-Id": organizer.id}
        )

    assert response.status_code == 401


def test_explicitly_disabling_membership_header_rejects_guest_reopen(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "0")
    trip, organizer, _ = _guest_trip(db)

    with _client(db) as client:
        response = client.get(
            f"/api/trips/{trip.id}", headers={"X-Membership-Id": organizer.id}
        )

    assert response.status_code == 401


def test_missing_or_forged_membership_header_is_rejected_even_in_compatibility_mode(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "1")
    trip, _, _ = _guest_trip(db)

    with _client(db) as client:
        missing = client.get(f"/api/trips/{trip.id}")
        forged = client.get(
            f"/api/trips/{trip.id}", headers={"X-Membership-Id": "f" * 32}
        )

    assert missing.status_code == 401
    assert forged.status_code == 401


def test_membership_header_is_rejected_even_when_compatibility_env_is_set(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "1")
    first_user = _user(db, label="First Header")
    first_trip, first_membership = _trip_membership(db, user=first_user, label="First")
    second_user = _user(db, label="Second Header")
    second_trip, _ = _trip_membership(db, user=second_user, label="Second")

    with _client(db) as client:
        own_trip = client.get(
            f"/api/trips/{first_trip.id}", headers={"X-Membership-Id": first_membership.id}
        )
        foreign_trip = client.get(
            f"/api/trips/{second_trip.id}", headers={"X-Membership-Id": first_membership.id}
        )

    assert own_trip.status_code == 401
    assert foreign_trip.status_code == 401


def test_invite_revocation_does_not_revoke_an_already_joined_guest_session(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DEV_ALLOW_MEMBERSHIP_HEADER", "1")
    trip, organizer, organizer_user = _guest_trip(db)

    with _client(db) as client:
        organizer_token = _login(client, organizer_user.email)
        invite = _create_invite(client, trip, organizer_token)
        joined = client.post(
            f"/api/invites/{invite['token']}/join",
            json={"display_name": "Revocation Characterization Guest"},
        )
        assert joined.status_code == 200
        guest_token = joined.json()["guest_token"]
        revoked = client.post(
            f"/api/invites/{invite['invite_id']}/revoke",
            headers={
                "Authorization": f"Bearer {organizer_token}",
                "X-Trip-Id": trip.id,
            },
        )
        preview_after_revoke = client.get(f"/api/invites/{invite['token']}")
        guest_after_revoke = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": f"Bearer {guest_token}", "X-Trip-Id": trip.id},
        )

    assert revoked.status_code == 200
    assert preview_after_revoke.status_code == 404
    assert guest_after_revoke.status_code == 200
