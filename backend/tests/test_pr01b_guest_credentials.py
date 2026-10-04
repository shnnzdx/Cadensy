"""PR-01B secure Guest credentials at the public FastAPI boundary."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import main as api
from app.db.models import AuthSession, GuestSession, Trip, TripMembership, User
from app.domain import auth


@contextmanager
def _client(db: Session):
    api.app.dependency_overrides[api.get_session] = lambda: db
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def _organizer_trip(db: Session) -> tuple[User, Trip, TripMembership]:
    organizer = User(
        name="Credential Organizer",
        email="credential-organizer@example.test",
        password_hash=auth.hash_password("correct-horse"),
    )
    db.add(organizer)
    db.flush()
    trip = Trip(
        name="Credential Trip",
        destination="Synthetic City",
        created_by_user_id=organizer.id,
    )
    db.add(trip)
    db.flush()
    membership = TripMembership(
        trip_id=trip.id,
        user_id=organizer.id,
        role="organizer",
        status="joined",
    )
    db.add(membership)
    db.flush()
    return organizer, trip, membership


def _login(client: TestClient, organizer: User) -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": organizer.email, "password": "correct-horse"},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _guest_join(client: TestClient, organizer: User, trip: Trip, name: str) -> tuple[str, dict]:
    organizer_token = _login(client, organizer)
    invite = client.post(
        f"/api/trips/{trip.id}/invite",
        headers={
            "Authorization": f"Bearer {organizer_token}",
            "X-Trip-Id": trip.id,
        },
    ).json()
    joined = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": name},
    )
    assert joined.status_code == 200
    return organizer_token, {"invite": invite, "joined": joined.json()}


def test_guest_join_issues_hashed_bound_credential_that_reopens_its_trip(
    db: Session,
):
    organizer, trip, _ = _organizer_trip(db)

    with _client(db) as client:
        organizer_token = _login(client, organizer)
        invite = client.post(
            f"/api/trips/{trip.id}/invite",
            headers={
                "Authorization": f"Bearer {organizer_token}",
                "X-Trip-Id": trip.id,
            },
        ).json()
        joined = client.post(
            f"/api/invites/{invite['token']}/join",
            json={"display_name": "Credential Guest"},
        )
        assert joined.status_code == 200
        body = joined.json()
        reopened = client.get(
            f"/api/trips/{trip.id}",
            headers={
                "Authorization": f"Bearer {body['guest_token']}",
                "X-Trip-Id": trip.id,
            },
        )

    saved = db.scalar(
        select(GuestSession).where(
            GuestSession.membership_id == body["membership_id"]
        )
    )
    assert body["trip_id"] == trip.id
    assert body["role"] == "participant"
    assert body["guest_token"].startswith("gst_")
    assert body["guest_token"] != saved.token_hash
    assert saved.trip_id == trip.id
    assert saved.revoked_at is None
    assert saved.expires_at > datetime.now(timezone.utc)
    assert reopened.status_code == 200


def test_forged_expired_and_revoked_guest_credentials_fail_closed(db: Session):
    organizer, trip, _ = _organizer_trip(db)

    with _client(db) as client:
        _, result = _guest_join(client, organizer, trip, "Lifecycle Guest")
        token = result["joined"]["guest_token"]
        forged = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": "Bearer gst_forged", "X-Trip-Id": trip.id},
        )
        saved = db.scalar(select(GuestSession).where(GuestSession.token_hash == auth.token_hash(token)))
        assert saved is not None
        saved.expires_at = datetime.now(timezone.utc).replace(year=2020)
        db.flush()
        expired = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": f"Bearer {token}", "X-Trip-Id": trip.id},
        )
        saved.expires_at = datetime.now(timezone.utc).replace(year=2030)
        db.flush()
        logout = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        replay = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": f"Bearer {token}", "X-Trip-Id": trip.id},
        )

    assert forged.status_code == 401
    assert expired.status_code == 401
    assert logout.status_code == 200
    assert saved.revoked_at is not None
    assert replay.status_code == 401


def test_guest_credential_is_trip_scoped_and_cannot_read_account_data(db: Session):
    organizer, trip, _ = _organizer_trip(db)
    other_trip = Trip(name="Other Trip", destination="Elsewhere", created_by_user_id=organizer.id)
    db.add(other_trip)
    db.flush()

    with _client(db) as client:
        _, result = _guest_join(client, organizer, trip, "Scoped Guest")
        token = result["joined"]["guest_token"]
        other_trip_request = client.get(
            f"/api/trips/{other_trip.id}",
            headers={"Authorization": f"Bearer {token}", "X-Trip-Id": other_trip.id},
        )
        account = client.get("/api/account", headers={"Authorization": f"Bearer {token}"})

    assert other_trip_request.status_code == 403
    assert account.status_code == 401


def test_invite_revocation_blocks_future_join_without_revoking_joined_guest(db: Session):
    organizer, trip, _ = _organizer_trip(db)

    with _client(db) as client:
        organizer_token, result = _guest_join(client, organizer, trip, "Invite Lifecycle Guest")
        invite = result["invite"]
        guest_token = result["joined"]["guest_token"]
        revoked = client.post(
            f"/api/invites/{invite['invite_id']}/revoke",
            headers={"Authorization": f"Bearer {organizer_token}", "X-Trip-Id": trip.id},
        )
        preview = client.get(f"/api/invites/{invite['token']}")
        repeat_join = client.post(
            f"/api/invites/{invite['token']}/join",
            json={"display_name": "Late Guest"},
        )
        existing_guest = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": f"Bearer {guest_token}", "X-Trip-Id": trip.id},
        )

    assert revoked.status_code == 200
    assert preview.status_code == 404
    assert repeat_join.status_code == 404
    assert existing_guest.status_code == 200


def test_membership_removal_revokes_all_bound_guest_sessions(db: Session):
    organizer, trip, _ = _organizer_trip(db)

    with _client(db) as client:
        organizer_token, result = _guest_join(client, organizer, trip, "Removed Guest")
        guest = result["joined"]
        removed = client.delete(
            f"/api/trips/{trip.id}/members/{guest['membership_id']}",
            headers={"Authorization": f"Bearer {organizer_token}", "X-Trip-Id": trip.id},
        )
        after_removal = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": f"Bearer {guest['guest_token']}", "X-Trip-Id": trip.id},
        )

    saved_membership = db.get(TripMembership, guest["membership_id"])
    saved_session = db.scalar(
        select(GuestSession).where(GuestSession.membership_id == guest["membership_id"])
    )
    assert removed.status_code == 200
    assert saved_membership.status == "removed"
    assert saved_session.revoked_at is not None
    assert after_removal.status_code == 401


def test_removed_account_membership_keeps_account_session_but_loses_trip_access(db: Session):
    organizer, trip, _ = _organizer_trip(db)
    participant = User(
        name="Removed Account Member",
        email="removed-account@example.test",
        password_hash=auth.hash_password("correct-horse"),
    )
    db.add(participant)
    db.flush()
    participant_membership = TripMembership(
        trip_id=trip.id,
        user_id=participant.id,
        role="participant",
        status="joined",
    )
    db.add(participant_membership)
    db.flush()

    with _client(db) as client:
        organizer_token = _login(client, organizer)
        participant_token = _login(client, participant)
        before_removal = client.get(
            "/api/trips", headers={"Authorization": f"Bearer {participant_token}"}
        )
        removed = client.delete(
            f"/api/trips/{trip.id}/members/{participant_membership.id}",
            headers={"Authorization": f"Bearer {organizer_token}", "X-Trip-Id": trip.id},
        )
        dashboard_after = client.get(
            "/api/trips", headers={"Authorization": f"Bearer {participant_token}"}
        )
        trip_after = client.get(
            f"/api/trips/{trip.id}",
            headers={"Authorization": f"Bearer {participant_token}", "X-Trip-Id": trip.id},
        )

    assert before_removal.status_code == 200
    assert {item["id"] for item in before_removal.json()} == {trip.id}
    assert removed.status_code == 200
    assert dashboard_after.status_code == 200
    assert dashboard_after.json() == []
    assert trip_after.status_code == 403


def test_account_bearer_wins_over_membership_header_and_repeated_guest_join_never_reuses_a_credential(
    db: Session,
):
    organizer, trip, organizer_membership = _organizer_trip(db)

    with _client(db) as client:
        organizer_token = _login(client, organizer)
        # Guest tokens carry a prefix, but account tokens are random and could
        # theoretically have the same spelling. Account lookup must take
        # precedence over token-prefix routing.
        account_token_with_guest_prefix = "gst_account_precedence_test_token"
        account_session = db.scalar(
            select(AuthSession).where(AuthSession.token_hash == auth.token_hash(organizer_token))
        )
        assert account_session is not None
        account_session.token_hash = auth.token_hash(account_token_with_guest_prefix)
        db.flush()
        invite = client.post(
            f"/api/trips/{trip.id}/invite",
            headers={
                "Authorization": f"Bearer {account_token_with_guest_prefix}",
                "X-Trip-Id": trip.id,
            },
        ).json()
        first = client.post(
            f"/api/invites/{invite['token']}/join", json={"display_name": "First Guest"}
        ).json()
        second = client.post(
            f"/api/invites/{invite['token']}/join", json={"display_name": "Second Guest"}
        ).json()
        account_me = client.get(
            "/api/me",
            headers={
                "Authorization": f"Bearer {account_token_with_guest_prefix}",
                "X-Trip-Id": trip.id,
                "X-Membership-Id": first["membership_id"],
            },
        )

    assert first["membership_id"] != second["membership_id"]
    assert first["guest_token"] != second["guest_token"]
    assert account_me.status_code == 200
    assert account_me.json()["membership_id"] == organizer_membership.id
