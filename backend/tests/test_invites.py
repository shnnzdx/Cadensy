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
    ActivationEvent,
    DecisionRound,
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

    preview = client.get(f"/api/invites/{invite['token']}")
    join = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "Lee"},
    )
    assert preview.status_code == join.status_code == 410
    assert preview.json()["detail"] == join.json()["detail"] == "invite_revoked"
    assert "Chicago" not in preview.text


def test_expired_invite_returns_404(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    saved = api_session.scalar(select(InviteLink).where(InviteLink.id == invite["invite_id"]))
    saved.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    api_session.flush()

    preview = client.get(f"/api/invites/{invite['token']}")
    assert preview.status_code == 410
    assert preview.json()["detail"] == "invite_expired"
    assert "Chicago" not in preview.text


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

    existing_user.password_hash = auth.hash_password("correct-horse")
    api_session.flush()
    login = client.post(
        "/api/auth/login",
        json={"email": "Alex@Example.com", "password": "correct-horse"},
    )
    response = client.post(
        f"/api/invites/{invite['token']}/account-join",
        headers={"Authorization": f"Bearer {login.json()['token']}"},
        json={},
    )

    assert response.status_code == 200
    membership = api_session.get(TripMembership, response.json()["membership_id"])
    after = api_session.scalar(select(func.count()).select_from(User))
    assert after == before
    assert membership.user_id == existing_user.id
    assert membership.role == "participant"
    assert membership.join_method == "invite_account"


def test_account_join_returns_existing_membership_on_repeat(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    account = _user(api_session, "Alex", email="alex-repeat@example.com")
    account.password_hash = auth.hash_password("correct-horse")
    api_session.flush()
    login = client.post(
        "/api/auth/login",
        json={"email": "alex-repeat@example.com", "password": "correct-horse"},
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    first = client.post(
        f"/api/invites/{invite['token']}/account-join", headers=headers, json={}
    ).json()
    second = client.post(
        f"/api/invites/{invite['token']}/account-join", headers=headers, json={}
    ).json()

    assert second["membership_id"] == first["membership_id"]
    assert first["already_member"] is False
    assert second["already_member"] is True


def test_guest_join_rejects_legacy_email_identity_claim(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    user_count_before = api_session.scalar(select(func.count()).select_from(User))
    membership_count_before = api_session.scalar(
        select(func.count()).select_from(TripMembership)
    )

    response = client.post(
        f"/api/invites/{invite['token']}/join",
        json={"display_name": "No Claim", "email": "claimed@example.com"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "account_join_requires_authenticated_session"
    assert api_session.scalar(select(func.count()).select_from(User)) == user_count_before
    assert (
        api_session.scalar(select(func.count()).select_from(TripMembership))
        == membership_count_before
    )


def test_non_organizer_cannot_create_invite(client: TestClient, api_session: Session):
    trip, _, participant = _trip(api_session)

    response = client.post(
        f"/api/trips/{trip.id}/invite",
        headers=_member_headers(client, participant, trip),
    )

    assert response.status_code == 403


def test_invite_page_reuses_active_invite_and_regenerate_revokes_the_old_one(
    client: TestClient, api_session: Session
):
    trip, organizer, _ = _trip(api_session)
    headers = _member_headers(client, organizer, trip)
    created = client.post(f"/api/trips/{trip.id}/invite", headers=headers).json()
    listed = client.get(f"/api/trips/{trip.id}/invite", headers=headers)

    assert listed.status_code == 200
    body = listed.json()
    assert body["invite_id"] == created["invite_id"]
    assert body["expires_at"] == created["expires_at"]
    assert body["status"] == "active_hmac"
    assert body["token"] == created["token"]
    assert body["can_replace"] is True
    assert body["can_create"] is False
    expires_at = datetime.fromisoformat(created["expires_at"])
    remaining = expires_at - datetime.now(timezone.utc)
    assert timedelta(days=179) < remaining < timedelta(days=181)
    assert (
        api_session.scalar(
            select(func.count())
            .select_from(InviteLink)
            .where(InviteLink.trip_id == trip.id, InviteLink.revoked_at.is_(None))
        )
        == 1
    )

    reused = client.post(f"/api/trips/{trip.id}/invite", headers=headers).json()
    assert reused["invite_id"] == created["invite_id"]
    assert reused["token"] == created["token"]
    regenerated = client.post(
        f"/api/trips/{trip.id}/invite/replace", headers=headers
    ).json()
    old = client.get(f"/api/invites/{created['token']}")
    current = client.get(f"/api/trips/{trip.id}/invite", headers=headers)

    assert regenerated["invite_id"] != created["invite_id"]
    assert regenerated["expires_at"] == created["expires_at"]
    assert old.status_code == 410
    assert old.json()["detail"] == "invite_revoked"
    assert "Chicago" not in old.text
    assert client.get(f"/api/invites/{regenerated['token']}").status_code == 200
    assert current.json()["invite_id"] == regenerated["invite_id"]


def test_unknown_invite_is_distinct_from_expired_and_revoked(client: TestClient):
    missing = client.get("/api/invites/not-a-real-token")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "invite_invalid"
    assert "name" not in missing.json()


def test_existing_invite_without_expiry_keeps_working(client: TestClient, api_session: Session):
    trip, _, _ = _trip(api_session)
    raw = "legacy-null-expiry-token"
    api_session.add(
        InviteLink(
            trip_id=trip.id,
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            is_primary=True,
            expires_at=None,
        )
    )
    api_session.flush()

    preview = client.get(f"/api/invites/{raw}")

    assert preview.status_code == 410
    assert preview.json()["detail"] == "invite_revoked"
    assert "name" not in preview.json()


def test_guest_rejoin_with_the_same_session_is_idempotent(
    client: TestClient, api_session: Session
):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    before = api_session.scalar(
        select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
    )
    first = client.post(
        f"/api/invites/{invite['token']}/join", json={"display_name": "Guest Lee"}
    )
    second = client.post(
        f"/api/invites/{invite['token']}/join",
        headers={"Authorization": f"Bearer {first.json()['guest_token']}"},
        json={"display_name": "Guest Lee Again"},
    )
    fresh = client.post(
        f"/api/invites/{invite['token']}/join", json={"display_name": "Someone Else"}
    )

    assert first.status_code == second.status_code == fresh.status_code == 200
    assert first.json()["already_member"] is False
    assert second.json()["already_member"] is True
    assert "guest_token" not in second.json()
    assert second.json()["membership_id"] == first.json()["membership_id"]
    assert fresh.json()["membership_id"] != first.json()["membership_id"]
    assert (
        api_session.scalar(
            select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
        )
        == before + 2
    )
    joined = api_session.scalars(
        select(ActivationEvent).where(
            ActivationEvent.trip_id == trip.id,
            ActivationEvent.event_name == "member_joined",
        )
    ).all()
    assert len(joined) == 2
    assert first.json()["guest_token"] not in str([event.dedup_key for event in joined])


def test_guest_session_for_one_trip_does_not_reuse_another_trip(
    client: TestClient, api_session: Session
):
    first_trip, first_organizer, _ = _trip(api_session)
    second_trip, second_organizer, _ = _trip(api_session)
    first_invite = _create_invite(client, first_organizer, first_trip)
    second_invite = _create_invite(client, second_organizer, second_trip)
    joined = client.post(
        f"/api/invites/{first_invite['token']}/join", json={"display_name": "Traveler"}
    )
    other = client.post(
        f"/api/invites/{second_invite['token']}/join",
        headers={"Authorization": f"Bearer {joined.json()['guest_token']}"},
        json={"display_name": "Traveler"},
    )

    assert other.status_code == 200
    assert other.json()["already_member"] is False
    assert other.json()["trip_id"] == second_trip.id
    assert other.json()["membership_id"] != joined.json()["membership_id"]
    assert other.json()["guest_token"].startswith("gst_")


def test_open_decision_still_blocks_a_new_guest_but_not_the_same_session(
    client: TestClient, api_session: Session
):
    trip, organizer, _ = _trip(api_session)
    invite = _create_invite(client, organizer, trip)
    joined = client.post(
        f"/api/invites/{invite['token']}/join", json={"display_name": "Returning Guest"}
    )
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()
    item = PlanItem(
        plan_id=plan.id,
        day_index=1,
        day_date=datetime(2026, 8, 14).date(),
        start_hour=10,
        duration_min=60,
        title="Open decision",
        place="Chicago",
    )
    api_session.add(item)
    api_session.flush()
    api_session.add(
        DecisionRound(
            plan_item_id=item.id,
            kind="normal",
            options=[{"id": "keep", "title": "Keep"}],
            deadline=datetime.now(timezone.utc) + timedelta(hours=2),
            status="open",
            electorate_count=2,
        )
    )
    api_session.flush()
    before = api_session.scalar(
        select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
    )

    repeat = client.post(
        f"/api/invites/{invite['token']}/join",
        headers={"Authorization": f"Bearer {joined.json()['guest_token']}"},
        json={"display_name": "Returning Guest"},
    )
    blocked = client.post(
        f"/api/invites/{invite['token']}/join", json={"display_name": "New Guest"}
    )

    assert repeat.status_code == 200
    assert repeat.json()["already_member"] is True
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "membership_change_pending_decision"
    assert (
        api_session.scalar(
            select(func.count()).select_from(TripMembership).where(TripMembership.trip_id == trip.id)
        )
        == before
    )


def test_active_invite_metadata_stays_inside_its_trip(client: TestClient, api_session: Session):
    trip_a, organizer_a, _ = _trip(api_session)
    trip_b, organizer_b, _ = _trip(api_session)
    headers_a = _member_headers(client, organizer_a, trip_a)
    headers_b = _member_headers(client, organizer_b, trip_b)
    invite_b = _create_invite(client, organizer_b, trip_b)

    foreign = client.get(f"/api/trips/{trip_b.id}/invite", headers=headers_a)
    own = client.get(f"/api/trips/{trip_a.id}/invite", headers=headers_a)
    listed_b = client.get(f"/api/trips/{trip_b.id}/invite", headers=headers_b)

    assert foreign.status_code == 403
    assert own.status_code == 200
    assert own.json()["status"] == "none"
    assert "token" not in own.json()
    assert listed_b.status_code == 200
    assert listed_b.json()["invite_id"] == invite_b["invite_id"]
    assert listed_b.json()["token"] == invite_b["token"]
    assert listed_b.json()["status"] == "active_hmac"


def test_preference_budget_over_maximum_returns_400(client: TestClient, api_session: Session):
    trip, organizer, _ = _trip(api_session)
    headers = _member_headers(client, organizer, trip)
    rejected = client.put(
        f"/api/trips/{trip.id}/preferences/me",
        headers=headers,
        json={"ideal_budget": 900, "maximum_budget": 100},
    )
    accepted = client.put(
        f"/api/trips/{trip.id}/preferences/me",
        headers=headers,
        json={"ideal_budget": 100, "maximum_budget": 100},
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "ideal_budget_exceeds_maximum"
    assert accepted.status_code == 200
    assert accepted.json()["preference"]["ideal_budget"] == 100
