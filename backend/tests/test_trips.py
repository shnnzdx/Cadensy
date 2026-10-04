from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents import planner
from app.api import main as api
from app.db.models import (
    ChangeProposal,
    DecisionRound,
    InviteLink,
    Plan,
    PlanChange,
    PlanItem,
    PlanItemComment,
    Preference,
    ProposalDecision,
    Trip,
    TripMembership,
    UpdateNotice,
    User,
    Vote,
)
from app.domain.trips import service as trip_service
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
        yield test_client
    api.app.dependency_overrides.clear()


def _account_headers(
    client: TestClient, db: Session, membership: TripMembership
) -> dict[str, str]:
    """Authenticate an HTTP fixture through public account login, not an ID header."""
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
        "X-Trip-Id": membership.trip_id,
    }


def _guest_headers(
    client: TestClient,
    db: Session,
    organizer: TripMembership,
    *,
    display_name: str,
) -> tuple[dict[str, str], dict]:
    """Join through a real invite and return its bounded Guest bearer."""
    invite = client.post(
        f"/api/trips/{organizer.trip_id}/invite",
        headers=_account_headers(client, db, organizer),
    )
    assert invite.status_code == 200
    joined = client.post(
        f"/api/invites/{invite.json()['token']}/join",
        json={"display_name": display_name},
    )
    assert joined.status_code == 200
    body = joined.json()
    return (
        {
            "Authorization": f"Bearer {body['guest_token']}",
            "X-Trip-Id": organizer.trip_id,
        },
        body,
    )


def _user(db: Session, name: str) -> User:
    user = User(name=name, email=f"{name.lower()}-{uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    return user


def _trip_with_member(
    db: Session,
    user: User,
    *,
    name: str = "Existing trip",
    role: str = "participant",
) -> tuple[Trip, TripMembership]:
    trip = Trip(name=name, destination="Chicago", created_by_user_id=user.id)
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


def _add_member(
    db: Session, trip: Trip, name: str, *, role: str = "participant"
) -> TripMembership:
    user = _user(db, name)
    membership = TripMembership(
        trip_id=trip.id,
        user_id=user.id,
        role=role,
        status="joined",
    )
    db.add(membership)
    db.flush()
    return membership


def _plan_item(db: Session, trip: Trip) -> tuple[Plan, PlanItem]:
    plan = Plan(trip_id=trip.id)
    db.add(plan)
    db.flush()
    item = PlanItem(
        plan_id=plan.id,
        day_index=1,
        day_date=date.today() + timedelta(days=30),
        start_hour=14.0,
        duration_min=90,
        title="Art Institute of Chicago",
        place="Michigan Avenue",
        settledness="loose",
    )
    db.add(item)
    db.flush()
    return plan, item


def _round_and_proposal(
    db: Session, trip: Trip, membership: TripMembership
) -> tuple[Plan, PlanItem, DecisionRound, ChangeProposal]:
    plan, item = _plan_item(db, trip)
    round_ = DecisionRound(
        plan_item_id=item.id,
        options=[{"id": "keep", "label": "Keep it"}],
        deadline=datetime.now(timezone.utc) + timedelta(hours=2),
        status="open",
    )
    proposal = ChangeProposal(
        plan_item_id=item.id,
        action_type="edit",
        before_json={"title": item.title},
        after_json={"title": "Updated title"},
        requested_by_membership_id=membership.id,
        deadline=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add_all([round_, proposal])
    db.flush()
    return plan, item, round_, proposal


def _objectable_notice(
    db: Session, trip: Trip
) -> tuple[Plan, PlanItem, UpdateNotice]:
    plan, item = _plan_item(db, trip)
    notice = UpdateNotice(
        trip_id=trip.id,
        plan_item_id=item.id,
        kind="plan",
        recipient_membership_id=None,
        title=f"{item.title} updated",
        body="Applied directly.",
        can_object=True,
    )
    db.add(notice)
    db.flush()
    return plan, item, notice


def _invite(db: Session, trip: Trip) -> InviteLink:
    invite = InviteLink(
        trip_id=trip.id,
        token_hash=f"hash-{uuid4().hex}",
        is_primary=True,
    )
    db.add(invite)
    db.flush()
    return invite


def test_create_trip_makes_creator_organizer_and_empty_plan(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    _, auth_membership = _trip_with_member(api_session, user)

    response = client.post(
        "/api/trips",
        headers=_account_headers(client, api_session, auth_membership),
        json={
            "name": "Paris birthday",
            "destination": "Paris",
            "preferred_start_date": "2026-10-01",
            "preferred_end_date": "2026-10-05",
            "expected_group_size": 6,
            "currency": "EUR",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Paris birthday"
    assert body["destination"] == "Paris"
    assert body["status"] == "planning"
    assert body["preferred_start_date"] == "2026-10-01"
    assert body["preferred_end_date"] == "2026-10-05"
    assert body["plan_id"]

    membership = api_session.scalar(
        select(TripMembership).where(
            TripMembership.trip_id == body["id"],
            TripMembership.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.role == "organizer"
    assert membership.join_method == "creator"

    plan = api_session.get(Plan, body["plan_id"])
    assert plan is not None
    assert plan.trip_id == body["id"]
    assert api_session.scalar(
        select(PlanItem).where(PlanItem.plan_id == body["plan_id"])
    ) is None


def test_list_trips_returns_only_current_users_trips(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    other_user = _user(api_session, "Sam")
    first_trip, auth_membership = _trip_with_member(
        api_session, user, name="Mine with plan", role="organizer"
    )
    second_trip, _ = _trip_with_member(api_session, user, name="Mine empty")
    _trip_with_member(api_session, other_user, name="Someone else's")
    first_trip.cover_image_url = "https://images.unsplash.com/chicago?ixid=test"
    first_trip.cover_image_source = "unsplash"
    first_trip.cover_attribution_name = "A Photographer"
    first_trip.cover_attribution_url = "https://unsplash.com/@photographer?utm_source=cadensy&utm_medium=referral"
    first_trip.cover_source_url = "https://unsplash.com/photos/chicago?utm_source=cadensy&utm_medium=referral"

    plan = Plan(trip_id=first_trip.id)
    api_session.add(plan)
    api_session.flush()
    today = date.today()
    api_session.add_all(
        [
            PlanItem(
                plan_id=plan.id,
                day_index=2,
                day_date=today + timedelta(days=2),
                start_hour=14.0,
                title="Later museum",
                place="Museum",
            ),
            PlanItem(
                plan_id=plan.id,
                day_index=1,
                day_date=today + timedelta(days=1),
                start_hour=9.0,
                title="Morning coffee",
                place="Cafe",
            ),
        ]
    )
    api_session.flush()

    response = client.get(
        "/api/trips",
        headers=_account_headers(client, api_session, auth_membership),
    )

    assert response.status_code == 200
    trips = {trip["id"]: trip for trip in response.json()}
    assert set(trips) == {first_trip.id, second_trip.id}
    assert trips[first_trip.id]["next_item_title"] == "Morning coffee"
    assert trips[first_trip.id]["my_role"] == "organizer"
    assert trips[first_trip.id]["cover_image_url"].startswith("https://images.unsplash.com/")
    assert trips[first_trip.id]["cover_attribution_name"] == "A Photographer"
    assert trips[first_trip.id]["cover_image_source"] == "unsplash"
    assert trips[second_trip.id]["next_item_title"] is None


def test_list_trips_fetches_covers_in_stable_trip_id_order_even_when_priority_changes(
    client: TestClient, api_session: Session, monkeypatch
):
    user = _user(api_session, "Mia")
    first_trip, auth_membership = _trip_with_member(
        api_session, user, name="Alpha", role="organizer"
    )
    second_trip, _ = _trip_with_member(api_session, user, name="Beta")

    seen_trip_ids: list[str] = []

    def fake_ensure_trip_cover(_db: Session, trip: Trip) -> bool:
        seen_trip_ids.append(trip.id)
        return False

    monkeypatch.setattr(trip_service.cover_service, "ensure_trip_cover", fake_ensure_trip_cover)

    response = client.get(
        f"/api/trips?priority_trip_id={second_trip.id}",
        headers=_account_headers(client, api_session, auth_membership),
    )

    assert response.status_code == 200
    assert seen_trip_ids == sorted([first_trip.id, second_trip.id])


def test_guest_cannot_list_trips(client: TestClient, api_session: Session):
    user = _user(api_session, "Mia")
    _, organizer = _trip_with_member(api_session, user, role="organizer")
    guest_headers, _ = _guest_headers(
        client, api_session, organizer, display_name="Guest"
    )

    response = client.get(
        "/api/trips",
        headers=guest_headers,
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"destination": "Paris"},
        {"name": "Paris birthday"},
    ],
)
def test_create_trip_requires_name_and_destination(
    client: TestClient, api_session: Session, payload: dict
):
    user = _user(api_session, "Mia")
    _, auth_membership = _trip_with_member(api_session, user)

    response = client.post(
        "/api/trips",
        headers=_account_headers(client, api_session, auth_membership),
        json=payload,
    )

    assert response.status_code == 422


def test_next_item_skips_days_that_already_passed(
    client: TestClient, api_session: Session
):
    """The card says "next". After travel starts, it must not keep showing day-one items."""
    from datetime import timedelta

    user = _user(api_session, "Mia")
    trip, auth_membership = _trip_with_member(api_session, user, role="organizer")
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()

    today = date.today()
    api_session.add_all(
        [
            PlanItem(
                plan_id=plan.id, day_index=1, day_date=today - timedelta(days=2),
                start_hour=9.0, title="Already happened", place="Yesterday",
            ),
            PlanItem(
                plan_id=plan.id, day_index=2, day_date=today + timedelta(days=1),
                start_hour=10.0, title="Still ahead", place="Tomorrow",
            ),
        ]
    )
    api_session.flush()

    response = client.get(
        "/api/trips", headers=_account_headers(client, api_session, auth_membership)
    )
    trips = {t["id"]: t for t in response.json()}
    assert trips[trip.id]["next_item_title"] == "Still ahead"


def test_a_finished_trip_has_no_next_item(client: TestClient, api_session: Session):
    from datetime import timedelta

    user = _user(api_session, "Mia")
    trip, auth_membership = _trip_with_member(api_session, user)
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()
    api_session.add(
        PlanItem(
            plan_id=plan.id, day_index=1, day_date=date.today() - timedelta(days=5),
            start_hour=9.0, title="Long gone", place="Past",
        )
    )
    api_session.flush()

    response = client.get(
        "/api/trips", headers=_account_headers(client, api_session, auth_membership)
    )
    trips = {t["id"]: t for t in response.json()}
    assert trips[trip.id]["next_item_title"] is None


def test_a_guest_cannot_create_a_trip(client: TestClient, api_session: Session):
    """Guests have the same rights inside this trip, but cannot create new trips; that is account-level."""
    user = _user(api_session, "Mia")
    _, organizer = _trip_with_member(api_session, user, role="organizer")
    guest_headers, _ = _guest_headers(
        client, api_session, organizer, display_name="Guest"
    )

    response = client.post(
        "/api/trips",
        headers=guest_headers,
        json={"name": "Sneaky trip", "destination": "Paris"},
    )
    assert response.status_code == 401


# ————————————————————— GET /api/me —————————————————————


def test_me_returns_the_role_of_this_trip_not_the_account(
    client: TestClient, api_session: Session
):
    """The same person can organize trip A and participate in trip B; the answer depends on which trip is queried."""
    user = _user(api_session, "Mia Chen")
    _, organizer_here = _trip_with_member(
        api_session, user, name="Trip A", role="organizer"
    )
    _, participant_there = _trip_with_member(
        api_session, user, name="Trip B", role="participant"
    )

    a = client.get(
        "/api/me", headers=_account_headers(client, api_session, organizer_here)
    ).json()
    b = client.get(
        "/api/me", headers=_account_headers(client, api_session, participant_there)
    ).json()

    assert a["role"] == "organizer"
    assert b["role"] == "participant"
    assert a["id"] == b["id"] == user.id     # Same person.
    assert a["name"] == b["name"] == "Mia Chen"
    assert a["initials"] == "MC"


def test_me_reports_a_guest_as_guest_with_no_email(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia Chen")
    _, organizer = _trip_with_member(api_session, user, role="organizer")
    guest_headers, joined = _guest_headers(
        client, api_session, organizer, display_name="Sam"
    )

    body = client.get("/api/me", headers=guest_headers).json()

    assert body["role"] == "guest"      # Report guest anyway; no account means guest.
    assert body["is_guest"] is True
    assert body["name"] == "Sam"
    assert body["email"] is None
    assert body["id"] == joined["membership_id"]


def test_me_needs_an_identity(client: TestClient):
    assert client.get("/api/me").status_code == 401


def test_creating_a_trip_returns_the_new_membership(client: TestClient, api_session: Session):
    """Without returning it, the frontend cannot switch identity and later calls for this trip will 403."""
    user = _user(api_session, "Mia")
    _, auth = _trip_with_member(api_session, user)

    body = client.post(
        "/api/trips",
        headers=_account_headers(client, api_session, auth),
        json={"name": "Paris", "destination": "Paris"},
    ).json()

    assert body["membership_id"]
    assert body["membership_id"] != auth.id          # This is the membership in the new trip.
    membership = api_session.get(TripMembership, body["membership_id"])
    assert membership.trip_id == body["id"]
    assert membership.role == "organizer"


def test_an_identity_from_another_trip_is_refused_not_silently_answered(
    client: TestClient, api_session: Session
):
    """Using trip A identity to ask for trip B members must return 403.

    Silently returning A's data would be worse: the user thinks they are viewing B but sees another trip's members and progress."""
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")

    headers = _account_headers(client, api_session, auth_a)
    ok = client.get(f"/api/trips/{trip_a.id}/members", headers=headers)
    wrong = client.get(f"/api/trips/{trip_b.id}/members", headers=headers)

    assert ok.status_code == 200
    assert wrong.status_code == 403


def test_trip_detail_now_requires_authenticated_trip_membership(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)

    assert client.get(f"/api/trips/{trip.id}").status_code == 401

    response = client.get(
        f"/api/trips/{trip.id}",
        headers=_account_headers(client, api_session, membership),
    )
    assert response.status_code == 200
    assert response.json()["id"] == trip.id


def test_trip_detail_rejects_foreign_trip_path(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")

    headers = _account_headers(client, api_session, auth_a)
    ok = client.get(f"/api/trips/{trip_a.id}", headers=headers)
    wrong = client.get(f"/api/trips/{trip_b.id}", headers=headers)

    assert ok.status_code == 200
    assert wrong.status_code == 403


def test_current_plan_now_requires_authenticated_trip_membership(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()

    assert client.get(f"/api/trips/{trip.id}/plans/current").status_code == 401

    response = client.get(
        f"/api/trips/{trip.id}/plans/current",
        headers=_account_headers(client, api_session, membership),
    )
    assert response.status_code == 200
    assert response.json()["plan_id"] == plan.id


def test_current_plan_exposes_meal_semantics_and_refresh_state(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    plan = Plan(trip_id=trip.id, needs_refresh=True)
    api_session.add(plan)
    api_session.flush()
    api_session.add(PlanItem(
        plan_id=plan.id, day_index=1, day_date=date(2026, 8, 19),
        start_hour=12.5, title="Flexible lunch break", place="Choose nearby",
        is_meal=True,
    ))
    api_session.flush()

    response = client.get(
        f"/api/trips/{trip.id}/plans/current",
        headers=_account_headers(client, api_session, membership),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["needs_refresh"] is True
    assert body["days"][0]["items"][0]["is_meal"] is True
    assert body["days"][0]["items"][0]["meal_type"] == "lunch"


def test_current_plan_rejects_foreign_trip_path(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    api_session.add_all([Plan(trip_id=trip_a.id), Plan(trip_id=trip_b.id)])
    api_session.flush()

    ok = client.get(
        f"/api/trips/{trip_a.id}/plans/current",
        headers=_account_headers(client, api_session, auth_a),
    )
    wrong = client.get(
        f"/api/trips/{trip_b.id}/plans/current",
        headers=_account_headers(client, api_session, auth_a),
    )

    assert ok.status_code == 200
    assert wrong.status_code == 403


def test_item_comments_are_saved_and_read_back(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia Chen")
    trip, membership = _trip_with_member(api_session, user)
    _, item = _plan_item(api_session, trip)

    created = client.post(
        f"/api/plans/items/{item.id}/comments",
        headers=_account_headers(client, api_session, membership),
        json={"text": "Meet by the main entrance."},
    )
    assert created.status_code == 200
    assert api_session.query(PlanItemComment).count() == 1

    rows = client.get(
        f"/api/trips/{trip.id}/comments",
        headers=_account_headers(client, api_session, membership),
    ).json()
    assert rows[0]["plan_item_id"] == item.id
    assert rows[0]["text"] == "Meet by the main entrance."
    assert rows[0]["name"] == "Mia Chen"


def test_classify_access_is_scoped_to_plan_item(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    _, item_a = _plan_item(api_session, trip_a)
    _, item_b = _plan_item(api_session, trip_b)

    payload = {"start_hour": 15.5, "request": "Move this to 3:30 PM"}

    assert client.post(f"/api/plans/items/{item_a.id}/classify", json=payload).status_code == 401

    same = client.post(
        f"/api/plans/items/{item_a.id}/classify",
        headers=_account_headers(client, api_session, auth_a),
        json=payload,
    )
    foreign = client.post(
        f"/api/plans/items/{item_b.id}/classify",
        headers=_account_headers(client, api_session, auth_a),
        json=payload,
    )
    missing = client.post(
        "/api/plans/items/missing-item/classify",
        headers=_account_headers(client, api_session, auth_a),
        json=payload,
    )

    assert same.status_code == 200
    assert same.json()["path"] == "notice"
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert api_session.query(PlanChange).count() == 0
    assert api_session.query(DecisionRound).count() == 0
    assert api_session.query(ChangeProposal).count() == 0


def test_submit_change_access_is_scoped_to_plan_item(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    _, item_a = _plan_item(api_session, trip_a)
    _, item_b = _plan_item(api_session, trip_b)

    payload = {"start_hour": 15.5, "request": "Move this to 3:30 PM"}

    assert client.post(f"/api/plans/items/{item_a.id}/changes", json=payload).status_code == 401

    same = client.post(
        f"/api/plans/items/{item_a.id}/changes",
        headers=_account_headers(client, api_session, auth_a),
        json=payload,
    )
    persisted_changes = api_session.query(PlanChange).count()
    foreign = client.post(
        f"/api/plans/items/{item_b.id}/changes",
        headers=_account_headers(client, api_session, auth_a),
        json=payload,
    )
    missing = client.post(
        "/api/plans/items/missing-item/changes",
        headers=_account_headers(client, api_session, auth_a),
        json=payload,
    )

    assert same.status_code == 200
    assert same.json()["path"] == "notice"
    assert persisted_changes == 1
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert api_session.query(PlanChange).count() == persisted_changes
    assert api_session.query(DecisionRound).count() == 0
    assert api_session.query(ChangeProposal).count() == 0
    api_session.refresh(item_b)
    assert item_b.start_hour == 14.0


def test_submit_change_accepts_validated_round_alternatives(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    _, item = _plan_item(api_session, trip)
    item.settledness = "touched"
    api_session.flush()

    response = client.post(
        f"/api/plans/items/{item.id}/changes",
        headers=_account_headers(client, api_session, membership),
        json={
            "title": "Shopping",
            "request": "Replace this with shopping",
            "options": [
                {
                    "item_id": item.id,
                    "label": "Move later",
                    "title": "Move it to 4:00 PM",
                    "body": "Shift this block later on the same day.",
                    "tradeoff": "Keeps the same activity.",
                    "start_hour": 16.0,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["path"] == "round"
    round_ = api_session.scalars(select(DecisionRound)).one()
    requested = next(option for option in round_.options if option["id"] == "requested")
    assert requested["label"] == "Proposed change"
    assert requested["title"] == "Shopping"
    assert "Michigan Avenue" in requested["body"]
    assert "Day 1" in requested["body"]

    alternative = next(
        option for option in round_.options if option["id"] == "alternative-1"
    )
    assert alternative["title"] == "Art Institute of Chicago"
    assert "Michigan Avenue" in alternative["body"]
    assert "4:00 PM" in alternative["body"]
    assert alternative["patch"] == {"start_hour": 16.0}


def test_submit_change_deduplicates_requested_round_alternative(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    _, item = _plan_item(api_session, trip)
    item.settledness = "touched"
    api_session.flush()

    response = client.post(
        f"/api/plans/items/{item.id}/changes",
        headers=_account_headers(client, api_session, membership),
        json={
            "start_hour": 13.0,
            "request": "Move this to 1 PM",
            "options": [
                {
                    "item_id": item.id,
                    "label": "Move to 1 PM",
                    "title": "Move this to 1 PM",
                    "body": "Same as the submitted proposal.",
                    "start_hour": 13.0,
                }
            ],
        },
    )

    assert response.status_code == 200
    round_ = api_session.scalars(select(DecisionRound)).one()
    assert [option["id"] for option in round_.options] == [
        "keep",
        "requested",
        "split",
    ]
    requested = next(option for option in round_.options if option["id"] == "requested")
    assert requested["label"] == "Proposed change"
    assert requested["title"] == "Art Institute of Chicago"
    assert "1:00 PM" in requested["body"]


def test_submit_change_with_day_date_writes_json_safe_plan_change(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    _, item = _plan_item(api_session, trip)
    new_day = (item.day_date + timedelta(days=1)).isoformat()

    response = client.post(
        f"/api/plans/items/{item.id}/changes",
        headers=_account_headers(client, api_session, membership),
        json={"day_date": new_day, "request": "Move this to the next day"},
    )

    assert response.status_code == 200
    assert response.json()["path"] == "notice"
    api_session.refresh(item)
    assert item.day_date.isoformat() == new_day
    change = api_session.scalars(
        select(PlanChange).where(PlanChange.plan_item_id == item.id)
    ).one()
    assert change.patch["day_date"] == new_day


def test_submit_change_with_day_date_moves_item_to_matching_day_index(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    trip.preferred_start_date = date(2026, 8, 19)
    trip.preferred_end_date = date(2026, 8, 20)
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()
    first_day = PlanItem(
        plan_id=plan.id,
        day_index=1,
        day_date=date(2026, 8, 19),
        start_hour=9.0,
        title="First day coffee",
        place="Cafe",
    )
    moved = PlanItem(
        plan_id=plan.id,
        day_index=2,
        day_date=date(2026, 8, 20),
        start_hour=14.0,
        title="Logan Square walk",
        place="Logan Square",
    )
    api_session.add_all([first_day, moved])
    api_session.flush()

    response = client.post(
        f"/api/plans/items/{moved.id}/changes",
        headers=_account_headers(client, api_session, membership),
        json={"day_date": "2026-08-19", "request": "Move this to August 19"},
    )

    assert response.status_code == 200
    api_session.refresh(moved)
    assert moved.day_date == date(2026, 8, 19)
    assert moved.day_index == 1
    plan_response = client.get(
        f"/api/trips/{trip.id}/plans/current",
        headers=_account_headers(client, api_session, membership),
    )
    assert plan_response.status_code == 200
    days = plan_response.json()["days"]
    assert [day["day_index"] for day in days] == [1]
    assert {item["title"] for item in days[0]["items"]} == {
        "First day coffee",
        "Logan Square walk",
    }


def test_current_plan_reports_canonical_day_dates_from_trip_window(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    trip.preferred_start_date = date(2026, 8, 19)
    trip.preferred_end_date = date(2026, 8, 20)
    plan = Plan(trip_id=trip.id)
    api_session.add(plan)
    api_session.flush()
    api_session.add(
        PlanItem(
            plan_id=plan.id,
            day_index=2,
            # Historical bad data: an item can be left with a stale date after an old move-day change.
            day_date=date(2026, 8, 19),
            start_hour=10.0,
            title="Shedd Aquarium",
            place="Museum Campus",
        )
    )
    api_session.flush()

    response = client.get(
        f"/api/trips/{trip.id}/plans/current",
        headers=_account_headers(client, api_session, membership),
    )

    assert response.status_code == 200
    day = response.json()["days"][0]
    assert day["day_index"] == 2
    assert day["day_date"] == "2026-08-20"
    assert day["items"][0]["day_date"] == "2026-08-19"


def test_item_comment_access_is_scoped_to_plan_item(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    _, item_a = _plan_item(api_session, trip_a)
    _, item_b = _plan_item(api_session, trip_b)

    assert client.post(
        f"/api/plans/items/{item_a.id}/comments",
        json={"text": "Meet by the entrance."},
    ).status_code == 401

    same = client.post(
        f"/api/plans/items/{item_a.id}/comments",
        headers=_account_headers(client, api_session, auth_a),
        json={"text": "Meet by the entrance."},
    )
    persisted_comments = api_session.query(PlanItemComment).count()
    foreign = client.post(
        f"/api/plans/items/{item_b.id}/comments",
        headers=_account_headers(client, api_session, auth_a),
        json={"text": "Wrong trip"},
    )
    missing = client.post(
        "/api/plans/items/missing-item/comments",
        headers=_account_headers(client, api_session, auth_a),
        json={"text": "Missing"},
    )

    assert same.status_code == 200
    assert same.json()["plan_item_id"] == item_a.id
    assert persisted_comments == 1
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert api_session.query(PlanItemComment).count() == persisted_comments


def test_booking_access_is_scoped_to_plan_item(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    _, item_a = _plan_item(api_session, trip_a)
    _, item_b = _plan_item(api_session, trip_b)

    assert client.patch(
        f"/api/plans/items/{item_a.id}/booking",
        json={"booked": True},
    ).status_code == 401

    same = client.patch(
        f"/api/plans/items/{item_a.id}/booking",
        headers=_account_headers(client, api_session, auth_a),
        json={"booked": True},
    )
    persisted_changes = api_session.query(PlanChange).count()
    foreign = client.patch(
        f"/api/plans/items/{item_b.id}/booking",
        headers=_account_headers(client, api_session, auth_a),
        json={"booked": True},
    )
    missing = client.patch(
        "/api/plans/items/missing-item/booking",
        headers=_account_headers(client, api_session, auth_a),
        json={"booked": True},
    )

    api_session.refresh(item_a)
    api_session.refresh(item_b)
    assert same.status_code == 200
    assert same.json()["settledness"] == "booked"
    assert item_a.settledness == "booked"
    assert persisted_changes == 1
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert item_b.settledness == "loose"
    assert api_session.query(PlanChange).count() == persisted_changes


def test_notice_object_access_is_scoped_to_notice(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    _, _, notice_a = _objectable_notice(api_session, trip_a)
    _, _, notice_b = _objectable_notice(api_session, trip_b)

    assert client.post(f"/api/updates/{notice_a.id}/object").status_code == 401

    same = client.post(
        f"/api/updates/{notice_a.id}/object",
        headers=_account_headers(client, api_session, auth_a),
    )
    persisted_rounds = api_session.query(DecisionRound).count()
    foreign = client.post(
        f"/api/updates/{notice_b.id}/object",
        headers=_account_headers(client, api_session, auth_a),
    )
    missing = client.post(
        "/api/updates/missing-notice/object",
        headers=_account_headers(client, api_session, auth_a),
    )

    api_session.refresh(notice_a)
    api_session.refresh(notice_b)
    assert same.status_code == 200
    assert same.json()["path"] == "round"
    assert notice_a.can_object is False
    assert persisted_rounds == 1
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert notice_b.can_object is True
    assert api_session.query(DecisionRound).count() == persisted_rounds


def test_updates_reject_a_membership_from_another_trip(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    api_session.add(
        UpdateNotice(
            trip_id=trip_b.id,
            plan_item_id=None,
            kind="plan",
            recipient_membership_id=None,
            title="Trip B changed",
            body="Visible only inside Trip B",
            can_object=False,
        )
    )
    api_session.flush()

    ok = client.get(
        f"/api/trips/{trip_a.id}/updates",
        headers=_account_headers(client, api_session, auth_a),
    )
    wrong = client.get(
        f"/api/trips/{trip_b.id}/updates",
        headers=_account_headers(client, api_session, auth_a),
    )

    assert ok.status_code == 200
    assert wrong.status_code == 403


def test_change_log_now_requires_authenticated_trip_membership(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    plan, item = _plan_item(api_session, trip)
    change = PlanChange(
        plan_id=plan.id,
        plan_item_id=item.id,
        origin="notice",
        patch={"title": "Updated"},
        reason="Need a backup option",
    )
    api_session.add(change)
    api_session.flush()

    assert client.get(f"/api/plans/{plan.id}/changes").status_code == 401

    response = client.get(
        f"/api/plans/{plan.id}/changes",
        headers=_account_headers(client, api_session, membership),
    )
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": change.id,
            "plan_item_id": item.id,
            "origin": "notice",
            "patch": {"title": "Updated"},
            "reason": "Need a backup option",
            "applied_at": change.applied_at.isoformat(),
        }
    ]


def test_change_log_hides_foreign_and_missing_plans_as_404(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    plan_a, item_a = _plan_item(api_session, trip_a)
    plan_b, item_b = _plan_item(api_session, trip_b)
    api_session.add_all(
        [
            PlanChange(plan_id=plan_a.id, plan_item_id=item_a.id, origin="notice", patch={}),
            PlanChange(plan_id=plan_b.id, plan_item_id=item_b.id, origin="notice", patch={}),
        ]
    )
    api_session.flush()

    ok = client.get(
        f"/api/plans/{plan_a.id}/changes",
        headers=_account_headers(client, api_session, auth_a),
    )
    foreign = client.get(
        f"/api/plans/{plan_b.id}/changes",
        headers=_account_headers(client, api_session, auth_a),
    )
    missing = client.get(
        "/api/plans/missing-plan/changes",
        headers=_account_headers(client, api_session, auth_a),
    )

    assert ok.status_code == 200
    assert foreign.status_code == 404
    assert missing.status_code == 404


def test_round_read_now_requires_authenticated_trip_membership(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    _, _, round_, _ = _round_and_proposal(api_session, trip, membership)
    api_session.add(
        Vote(round_id=round_.id, trip_membership_id=membership.id, option_id="keep")
    )
    api_session.flush()

    assert client.get(f"/api/rounds/{round_.id}").status_code == 401

    response = client.get(
        f"/api/rounds/{round_.id}",
        headers=_account_headers(client, api_session, membership),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["id"] == round_.id
    assert body["my_vote"] == "keep"
    assert body["plan_item_id"] == round_.plan_item_id


def test_round_read_hides_foreign_and_missing_rounds_as_404(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, auth_b = _trip_with_member(api_session, user, name="Trip B")
    _, _, round_a, _ = _round_and_proposal(api_session, trip_a, auth_a)
    _, _, round_b, _ = _round_and_proposal(api_session, trip_b, auth_b)

    ok = client.get(
        f"/api/rounds/{round_a.id}",
        headers=_account_headers(client, api_session, auth_a),
    )
    foreign = client.get(
        f"/api/rounds/{round_b.id}",
        headers=_account_headers(client, api_session, auth_a),
    )
    missing = client.get(
        "/api/rounds/missing-round",
        headers=_account_headers(client, api_session, auth_a),
    )

    assert ok.status_code == 200
    assert foreign.status_code == 404
    assert missing.status_code == 404


def test_proposal_read_now_requires_authenticated_trip_membership(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    _, _, _, proposal = _round_and_proposal(api_session, trip, membership)
    api_session.add(ProposalDecision(proposal_id=proposal.id, trip_membership_id=membership.id, status="accepted"))
    api_session.flush()

    assert client.get(f"/api/proposals/{proposal.id}").status_code == 401

    response = client.get(
        f"/api/proposals/{proposal.id}",
        headers=_account_headers(client, api_session, membership),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["id"] == proposal.id
    assert body["status"] == proposal.status
    assert body["plan_item_id"] == proposal.plan_item_id
    assert body["members"] == [
        {"label": "Member A", "status": "accepted", "is_me": True}
    ]


def test_proposal_read_hides_foreign_and_missing_proposals_as_404(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, auth_b = _trip_with_member(api_session, user, name="Trip B")
    _, _, _, proposal_a = _round_and_proposal(api_session, trip_a, auth_a)
    _, _, _, proposal_b = _round_and_proposal(api_session, trip_b, auth_b)

    ok = client.get(
        f"/api/proposals/{proposal_a.id}",
        headers=_account_headers(client, api_session, auth_a),
    )
    foreign = client.get(
        f"/api/proposals/{proposal_b.id}",
        headers=_account_headers(client, api_session, auth_a),
    )
    missing = client.get(
        "/api/proposals/missing-proposal",
        headers=_account_headers(client, api_session, auth_a),
    )

    assert ok.status_code == 200
    assert foreign.status_code == 404
    assert missing.status_code == 404


def test_preference_dates_are_saved_and_read_back(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=3)
    trip.preferred_start_date = start
    trip.preferred_end_date = end
    api_session.flush()

    saved = client.put(
        f"/api/trips/{trip.id}/preferences/me",
        headers=_account_headers(client, api_session, membership),
        json={
            "preferred_start_date": start.isoformat(),
            "preferred_end_date": end.isoformat(),
            "available_start_date": start.isoformat(),
            "available_end_date": end.isoformat(),
        },
    )
    assert saved.status_code == 200

    row = api_session.scalar(select(Preference).where(Preference.trip_membership_id == membership.id))
    assert row.preferred_start_date == start
    assert row.preferred_end_date == end
    assert row.available_start_date == start
    assert row.available_end_date == end

    body = client.get(
        f"/api/trips/{trip.id}/preferences/me",
        headers=_account_headers(client, api_session, membership),
    ).json()
    assert body["preference"]["preferred_start_date"] == start.isoformat()
    assert body["preference"]["available_end_date"] == end.isoformat()


def test_preference_dates_outside_trip_window_are_rejected_for_any_role(
    client: TestClient, api_session: Session
):
    organizer_user = _user(api_session, "Mia")
    trip, organizer = _trip_with_member(api_session, organizer_user, role="organizer")
    participant = _add_member(api_session, trip, "Sam")
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=3)
    trip.preferred_start_date = start
    trip.preferred_end_date = end
    api_session.flush()

    for membership in (organizer, participant):
        response = client.put(
            f"/api/trips/{trip.id}/preferences/me",
            headers=_account_headers(client, api_session, membership),
            json={
                "preferred_start_date": (start - timedelta(days=1)).isoformat(),
                "preferred_end_date": end.isoformat(),
                "available_start_date": start.isoformat(),
                "available_end_date": (end + timedelta(days=1)).isoformat(),
            },
        )

        assert response.status_code == 422

    assert api_session.scalars(select(Preference)).all() == []


def test_foreign_preference_path_is_rejected_instead_of_using_my_trip(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, auth_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, _ = _trip_with_member(api_session, user, name="Trip B")
    start = date(2026, 9, 1)
    end = start + timedelta(days=2)

    saved = client.put(
        f"/api/trips/{trip_b.id}/preferences/me",
        headers=_account_headers(client, api_session, auth_a),
        json={
            "preferred_start_date": start.isoformat(),
            "preferred_end_date": end.isoformat(),
            "available_start_date": start.isoformat(),
            "available_end_date": end.isoformat(),
        },
    )
    read = client.get(
        f"/api/trips/{trip_b.id}/preferences/me",
        headers=_account_headers(client, api_session, auth_a),
    )

    assert saved.status_code == 403
    assert read.status_code == 403
    assert api_session.scalar(
        select(Preference).where(Preference.trip_membership_id == auth_a.id)
    ) is None


def test_foreign_constraint_path_is_rejected_instead_of_using_my_trip(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    _trip_with_member(api_session, user, name="Trip A")
    trip_b, auth_b = _trip_with_member(api_session, user, name="Trip B")
    trip_c, _ = _trip_with_member(api_session, user, name="Trip C")

    response = client.post(
        f"/api/trips/{trip_c.id}/constraints",
        headers=_account_headers(client, api_session, auth_b),
        json={
            "kind": "time_window",
            "importance": "required",
            "params": {"earliest_hour": 9.0},
        },
    )

    assert response.status_code == 403


def test_constraint_api_rejects_non_executable_required_payload(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user, role="organizer")

    response = client.post(
        f"/api/trips/{trip.id}/constraints",
        headers=_account_headers(client, api_session, membership),
        json={
            "kind": "avoid_tag",
            "importance": "required",
            "params": {},
            "original_text": "Required: avoid museums",
        },
    )

    assert response.status_code == 422
    assert "at least one tag" in response.text


def test_marking_an_item_booked_and_unbooked_is_persistent(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, membership = _trip_with_member(api_session, user)
    _, item = _plan_item(api_session, trip)

    booked = client.patch(
        f"/api/plans/items/{item.id}/booking",
        headers=_account_headers(client, api_session, membership),
        json={"booked": True},
    )
    assert booked.status_code == 200
    api_session.refresh(item)
    assert item.settledness == "booked"
    assert booked.json()["settledness"] == "booked"

    unbooked = client.patch(
        f"/api/plans/items/{item.id}/booking",
        headers=_account_headers(client, api_session, membership),
        json={"booked": False},
    )
    assert unbooked.status_code == 200
    api_session.refresh(item)
    assert item.settledness == "settled"
    assert unbooked.json()["settledness"] == "settled"

    changes = api_session.scalars(
        select(PlanChange)
        .where(PlanChange.plan_item_id == item.id)
        .order_by(PlanChange.applied_at, PlanChange.id)
    ).all()
    assert sorted(change.patch["settledness"] for change in changes) == ["booked", "settled"]


def test_round_vote_and_settle_routes_are_trip_scoped(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, member_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, member_b = _trip_with_member(api_session, user, name="Trip B")
    _, _, round_a, _ = _round_and_proposal(api_session, trip_a, member_a)
    _, _, round_b, _ = _round_and_proposal(api_session, trip_b, member_b)

    assert client.post(
        f"/api/rounds/{round_a.id}/votes", json={"option_id": "keep"}
    ).status_code == 401
    assert client.post(f"/api/rounds/{round_a.id}/settle").status_code == 401

    vote = client.post(
        f"/api/rounds/{round_a.id}/votes",
        headers=_account_headers(client, api_session, member_a),
        json={"option_id": "keep"},
    )
    foreign_vote = client.post(
        f"/api/rounds/{round_b.id}/votes",
        headers=_account_headers(client, api_session, member_a),
        json={"option_id": "keep"},
    )
    missing_vote = client.post(
        "/api/rounds/missing-round/votes",
        headers=_account_headers(client, api_session, member_a),
        json={"option_id": "keep"},
    )

    assert vote.status_code == 200
    assert vote.json()["my_vote"] == "keep"
    assert foreign_vote.status_code == 404
    assert missing_vote.status_code == 404
    assert api_session.scalar(select(Vote).where(Vote.round_id == round_a.id)) is not None
    assert api_session.scalar(select(Vote).where(Vote.round_id == round_b.id)) is None

    settle = client.post(
        f"/api/rounds/{round_a.id}/settle",
        headers=_account_headers(client, api_session, member_a),
    )
    foreign_settle = client.post(
        f"/api/rounds/{round_b.id}/settle",
        headers=_account_headers(client, api_session, member_a),
    )
    missing_settle = client.post(
        "/api/rounds/missing-round/settle",
        headers=_account_headers(client, api_session, member_a),
    )

    assert settle.status_code == 200
    assert settle.json()["id"] == round_a.id
    assert foreign_settle.status_code == 404
    assert missing_settle.status_code == 404

    api_session.refresh(round_a)
    api_session.refresh(round_b)
    assert round_a.status == "closed"
    assert round_b.status == "open"


def test_round_auto_settles_when_every_member_has_voted(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip, member = _trip_with_member(api_session, user, name="Trip A")
    _, item, round_, _ = _round_and_proposal(api_session, trip, member)
    round_.options = [
        {"id": "keep", "label": "Keep current", "title": item.title},
        {
            "id": "requested",
            "label": "Suggested change",
            "title": "Move to 3 AM",
            "patch": {"start_hour": 3.0},
        },
        {"id": "split", "label": "Split up", "title": "Split for this block"},
    ]
    api_session.flush()

    vote = client.post(
        f"/api/rounds/{round_.id}/votes",
        headers=_account_headers(client, api_session, member),
        json={"option_id": "requested"},
    )

    assert vote.status_code == 200
    assert vote.json()["status"] == "closed"
    assert vote.json()["winning_option_id"] == "requested"
    api_session.refresh(round_)
    api_session.refresh(item)
    assert round_.status == "closed"
    assert item.start_hour == 3.0


def test_round_extend_route_is_trip_scoped_and_keeps_organizer_policy(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, organizer_a = _trip_with_member(
        api_session, user, name="Trip A", role="organizer"
    )
    participant_a = _add_member(api_session, trip_a, "Sam")
    trip_b, organizer_b = _trip_with_member(
        api_session, user, name="Trip B", role="organizer"
    )
    _, _, round_a, _ = _round_and_proposal(api_session, trip_a, organizer_a)
    _, _, round_b, _ = _round_and_proposal(api_session, trip_b, organizer_b)
    original_deadline = round_a.deadline

    assert client.post(f"/api/rounds/{round_a.id}/extend").status_code == 401

    participant = client.post(
        f"/api/rounds/{round_a.id}/extend",
        headers=_account_headers(client, api_session, participant_a),
    )
    success = client.post(
        f"/api/rounds/{round_a.id}/extend",
        headers=_account_headers(client, api_session, organizer_a),
    )
    foreign = client.post(
        f"/api/rounds/{round_b.id}/extend",
        headers=_account_headers(client, api_session, organizer_a),
    )
    missing = client.post(
        "/api/rounds/missing-round/extend",
        headers=_account_headers(client, api_session, organizer_a),
    )

    assert participant.status_code == 403
    assert success.status_code == 200
    assert foreign.status_code == 404
    assert missing.status_code == 404

    api_session.refresh(round_a)
    api_session.refresh(round_b)
    assert round_a.extended_at is not None
    assert round_a.deadline > original_deadline
    assert round_b.extended_at is None


def test_proposal_decision_and_escalation_routes_are_trip_scoped(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, member_a = _trip_with_member(api_session, user, name="Trip A")
    trip_b, organizer_b = _trip_with_member(
        api_session, user, name="Trip B", role="organizer"
    )
    _, item_decide, _, proposal_decide = _round_and_proposal(api_session, trip_a, member_a)
    _, _, _, proposal_escalate = _round_and_proposal(api_session, trip_a, member_a)
    _, _, _, proposal_foreign = _round_and_proposal(api_session, trip_a, member_a)
    api_session.add(
        ProposalDecision(
            proposal_id=proposal_decide.id,
            trip_membership_id=member_a.id,
            status="pending",
        )
    )
    api_session.flush()

    assert client.post(
        f"/api/proposals/{proposal_decide.id}/decisions",
        json={"status": "accepted"},
    ).status_code == 401
    assert client.post(f"/api/proposals/{proposal_escalate.id}/escalate").status_code == 401

    decide = client.post(
        f"/api/proposals/{proposal_decide.id}/decisions",
        headers=_account_headers(client, api_session, member_a),
        json={"status": "accepted"},
    )
    escalate = client.post(
        f"/api/proposals/{proposal_escalate.id}/escalate",
        headers=_account_headers(client, api_session, member_a),
    )
    foreign_decide = client.post(
        f"/api/proposals/{proposal_foreign.id}/decisions",
        headers=_account_headers(client, api_session, organizer_b),
        json={"status": "accepted"},
    )
    foreign_escalate = client.post(
        f"/api/proposals/{proposal_foreign.id}/escalate",
        headers=_account_headers(client, api_session, organizer_b),
    )
    missing_decide = client.post(
        "/api/proposals/missing-proposal/decisions",
        headers=_account_headers(client, api_session, member_a),
        json={"status": "accepted"},
    )
    missing_escalate = client.post(
        "/api/proposals/missing-proposal/escalate",
        headers=_account_headers(client, api_session, member_a),
    )

    assert decide.status_code == 200
    decided = decide.json()
    assert decided["status"] == "applied"
    assert decided["applied"] is True
    assert decided["can_decide"] is False
    assert escalate.status_code == 200
    assert escalate.json()["status"] == "escalated"
    assert foreign_decide.status_code == 404
    assert foreign_escalate.status_code == 404
    assert missing_decide.status_code == 404
    assert missing_escalate.status_code == 404

    api_session.refresh(proposal_decide)
    api_session.refresh(proposal_escalate)
    api_session.refresh(proposal_foreign)
    api_session.refresh(item_decide)
    assert proposal_decide.status == "applied"
    assert proposal_escalate.status == "escalated"
    assert proposal_foreign.status == "waiting_affected_members"
    assert item_decide.settledness == "settled"


def test_deadlock_route_is_trip_scoped_and_keeps_organizer_policy(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, organizer_a = _trip_with_member(
        api_session, user, name="Trip A", role="organizer"
    )
    participant_a = _add_member(api_session, trip_a, "Sam")
    trip_b, organizer_b = _trip_with_member(
        api_session, user, name="Trip B", role="organizer"
    )
    _, item_success, _, proposal_success = _round_and_proposal(
        api_session, trip_a, organizer_a
    )
    _, item_foreign, _, proposal_foreign = _round_and_proposal(
        api_session, trip_a, organizer_a
    )
    _, item_keep, _, proposal_keep = _round_and_proposal(
        api_session, trip_a, organizer_a
    )
    proposal_success.status = "escalated"
    proposal_foreign.status = "escalated"
    proposal_keep.status = "escalated"
    keep_snapshot = (
        item_keep.title,
        item_keep.place,
        item_keep.start_hour,
        item_keep.settledness,
    )
    api_session.flush()

    assert client.post(
        f"/api/proposals/{proposal_success.id}/deadlock",
        json={"action": "clear"},
    ).status_code == 401

    # `remove` is an internal historical audit origin, not a public organizer
    # action. Public callers may choose only keep, split, or clear.
    assert client.post(
        f"/api/proposals/{proposal_success.id}/deadlock",
        headers=_account_headers(client, api_session, organizer_a),
        json={"action": "remove"},
    ).status_code == 422

    participant = client.post(
        f"/api/proposals/{proposal_success.id}/deadlock",
        headers=_account_headers(client, api_session, participant_a),
        json={"action": "clear"},
    )
    success = client.post(
        f"/api/proposals/{proposal_success.id}/deadlock",
        headers=_account_headers(client, api_session, organizer_a),
        json={"action": "clear"},
    )
    keep = client.post(
        f"/api/proposals/{proposal_keep.id}/deadlock",
        headers=_account_headers(client, api_session, organizer_a),
        json={"action": "keep"},
    )
    foreign = client.post(
        f"/api/proposals/{proposal_foreign.id}/deadlock",
        headers=_account_headers(client, api_session, organizer_b),
        json={"action": "clear"},
    )
    missing = client.post(
        "/api/proposals/missing-proposal/deadlock",
        headers=_account_headers(client, api_session, organizer_a),
        json={"action": "clear"},
    )

    assert participant.status_code == 403
    assert success.status_code == 200
    assert success.json()["item_id"] == item_success.id
    assert keep.status_code == 200
    assert keep.json() == {
        "item_id": item_keep.id,
        "title": keep_snapshot[0],
        "action": "keep",
    }
    assert foreign.status_code == 404
    assert missing.status_code == 404

    api_session.refresh(proposal_success)
    api_session.refresh(proposal_foreign)
    api_session.refresh(proposal_keep)
    api_session.refresh(item_success)
    api_session.refresh(item_foreign)
    api_session.refresh(item_keep)
    assert proposal_success.status == "resolved_by_organizer"
    assert proposal_foreign.status == "escalated"
    assert proposal_keep.status == "resolved_by_organizer"
    assert item_success.title == "Art Institute of Chicago"
    assert item_success.settledness == "removed"
    assert item_foreign.title == "Art Institute of Chicago"
    assert (
        item_keep.title,
        item_keep.place,
        item_keep.start_hour,
        item_keep.settledness,
    ) == keep_snapshot
    assert (
        api_session.query(PlanChange)
        .filter_by(source_proposal_id=proposal_keep.id)
        .one()
        .origin
        == "deadlock_keep"
    )


def test_remind_route_enforces_full_trip_scope_invariant(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, organizer_a = _trip_with_member(
        api_session, user, name="Trip A", role="organizer"
    )
    target_a = _add_member(api_session, trip_a, "Sam")
    trip_b, organizer_b = _trip_with_member(
        api_session, user, name="Trip B", role="organizer"
    )
    target_b = _add_member(api_session, trip_b, "Lee")

    assert client.post(
        f"/api/trips/{trip_a.id}/members/{target_a.id}/remind"
    ).status_code == 401

    success = client.post(
        f"/api/trips/{trip_a.id}/members/{target_a.id}/remind",
        headers=_account_headers(client, api_session, organizer_a),
    )
    foreign_path = client.post(
        f"/api/trips/{trip_b.id}/members/{target_b.id}/remind",
        headers=_account_headers(client, api_session, organizer_a),
    )
    foreign_target = client.post(
        f"/api/trips/{trip_a.id}/members/{target_b.id}/remind",
        headers=_account_headers(client, api_session, organizer_a),
    )
    missing = client.post(
        f"/api/trips/{trip_a.id}/members/missing-membership/remind",
        headers=_account_headers(client, api_session, organizer_a),
    )

    assert success.status_code == 200
    assert foreign_path.status_code == 403
    assert foreign_target.status_code == 404
    assert missing.status_code == 404

    notices = api_session.scalars(
        select(UpdateNotice).where(UpdateNotice.recipient_membership_id == target_a.id)
    ).all()
    assert len(notices) == 1
    assert notices[0].trip_id == trip_a.id


def test_revoke_invite_route_is_trip_scoped_and_keeps_organizer_policy(
    client: TestClient, api_session: Session
):
    user = _user(api_session, "Mia")
    trip_a, organizer_a = _trip_with_member(
        api_session, user, name="Trip A", role="organizer"
    )
    participant_a = _add_member(api_session, trip_a, "Sam")
    trip_b, organizer_b = _trip_with_member(
        api_session, user, name="Trip B", role="organizer"
    )
    invite_a = _invite(api_session, trip_a)
    invite_b = _invite(api_session, trip_b)

    assert client.post(f"/api/invites/{invite_a.id}/revoke").status_code == 401

    participant = client.post(
        f"/api/invites/{invite_a.id}/revoke",
        headers=_account_headers(client, api_session, participant_a),
    )
    success = client.post(
        f"/api/invites/{invite_a.id}/revoke",
        headers=_account_headers(client, api_session, organizer_a),
    )
    foreign = client.post(
        f"/api/invites/{invite_b.id}/revoke",
        headers=_account_headers(client, api_session, organizer_a),
    )
    missing = client.post(
        "/api/invites/missing-invite/revoke",
        headers=_account_headers(client, api_session, organizer_a),
    )

    assert participant.status_code == 403
    assert success.status_code == 200
    assert success.json() == {"revoked": True}
    assert foreign.status_code == 404
    assert missing.status_code == 404

    api_session.refresh(invite_a)
    api_session.refresh(invite_b)
    assert invite_a.revoked_at is not None
    assert invite_b.revoked_at is None
