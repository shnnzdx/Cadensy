"""Activation events: dedup, rollback, isolation, privacy, and decision mapping."""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api import main as api
from app.db.models import (
    ActivationEvent,
    ChangeProposal,
    DecisionRound,
    InviteLink,
    PlanItem,
    Preference,
    ProposalDecision,
    Trip,
    TripMembership,
    User,
    Vote,
)
from app.domain import auth
from app.domain.analytics import activation
from app.domain.analytics.activation import ActivationEventRejected
from app.domain.constraints.types import Classification
from app.domain.constraints.types import Path as DecisionPath
from app.domain.decisions import orchestrator
from app.domain.decisions.organizer import resolve_deadlock
from app.domain.plans import generator
from app.domain.preferences import service as pref_service
from app.domain.trips import service as trip_service

FUNNEL_SQL = (
    Path(__file__).resolve().parents[1] / "analytics" / "activation_funnel.sql"
).read_text(encoding="utf-8")


def _events(db: Session, name: str | None = None, trip_id: str | None = None) -> list[ActivationEvent]:
    query = select(ActivationEvent)
    if name is not None:
        query = query.where(ActivationEvent.event_name == name)
    if trip_id is not None:
        query = query.where(ActivationEvent.trip_id == trip_id)
    return list(db.scalars(query.order_by(ActivationEvent.occurred_at, ActivationEvent.dedup_key)))


def _user(db: Session, name: str = "Mia") -> User:
    user = User(name=name, email=f"{name.lower()}-{uuid4().hex}@example.com")
    db.add(user)
    db.flush()
    return user


def _create(db: Session, user: User | None = None):
    owner = user or _user(db)
    created = trip_service.create_trip(
        db,
        owner,
        trip_service.TripCreateData(
            name="Activation trip",
            destination="Chicago",
            preferred_start_date=date(2026, 8, 14),
            preferred_end_date=date(2026, 8, 17),
            expected_group_size=4,
            currency="USD",
        ),
    )
    db.flush()
    return created


def _invite_row(db: Session, trip_id: str, raw: str) -> InviteLink:
    invite = InviteLink(
        trip_id=trip_id,
        token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        is_primary=False,
    )
    db.add(invite)
    db.flush()
    return invite


def _guest(db: Session, trip_id: str, name: str) -> TripMembership:
    membership = TripMembership(
        trip_id=trip_id,
        guest_display_name=name,
        role="participant",
        join_method="invite_guest",
        status="joined",
    )
    db.add(membership)
    db.flush()
    return membership


def _round(db: Session, item: PlanItem, *, electorate: int | None, options: list[dict], status: str = "open", deadline: datetime | None = None) -> DecisionRound:
    row = DecisionRound(
        plan_item_id=item.id,
        kind="normal",
        options=options,
        deadline=deadline or datetime.now(timezone.utc) + timedelta(hours=4),
        status=status,
        electorate_count=electorate,
    )
    db.add(row)
    db.flush()
    return row


def _options(title: str = "Approved Group Choice ZZZ") -> list[dict]:
    return [
        {"id": "keep", "title": "Keep current"},
        {"id": "requested", "title": title, "patch": {"title": title}},
    ]


@pytest.fixture
def api_session(test_engine):
    connection = test_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, future=True, join_transaction_mode="create_savepoint")()
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


def test_activation_event_dedup_on_retry(db: Session):
    created = _create(db)
    activation.record_trip_created(
        db, trip_id=created.trip.id, membership_id=created.membership.id
    )
    db.flush()

    assert len(_events(db, "trip_created", created.trip.id)) == 1


def test_activation_event_rolls_back_with_business_action(db: Session):
    user = _user(db)
    nested = db.begin_nested()
    created = _create(db, user)
    trip_id = created.trip.id
    nested.rollback()

    assert _events(db, "trip_created", trip_id) == []
    assert db.get(Trip, trip_id) is None

    created = _create(db, user)
    db.flush()
    assert len(_events(db, "trip_created", created.trip.id)) == 1


def test_activation_events_are_isolated_across_trips(db: Session):
    first = _create(db)
    second = _create(db)
    with pytest.raises(ActivationEventRejected):
        activation.record_activation_event(
            db,
            event_name="preferences_submitted",
            trip_id=first.trip.id,
            membership_id=second.membership.id,
            actor_kind="account",
            dedup_key=f"preferences_submitted:{second.membership.id}",
        )

    assert _events(db, trip_id=first.trip.id) == [
        event for event in _events(db, trip_id=first.trip.id) if event.event_name == "trip_created"
    ]
    assert all(event.trip_id == first.trip.id for event in _events(db, trip_id=first.trip.id))
    assert all(event.membership_id in {None, first.membership.id} for event in _events(db, trip_id=first.trip.id))


def test_activation_event_rows_and_logs_exclude_sensitive_fields(db: Session, caplog):
    created = _create(db)
    sentinel = "leak-token-not-stored@example.com"
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ActivationEventRejected):
        activation.record_activation_event(
            db,
            event_name="member_joined",
            trip_id=created.trip.id,
            membership_id=created.membership.id,
            invite_id=None,
            actor_kind="account",
            dedup_key=f"member_joined:{created.membership.id}",
            metadata={
                "join_method": "invite_account",
                "email": sentinel,
                "token": "raw-invite-token",
                "original_text": "private constraint",
                "ideal_budget": "800",
            },
        )

    blob = " ".join(
        f"{event.event_name} {event.dedup_key} {event.event_metadata}"
        for event in _events(db)
    )
    assert sentinel not in blob
    assert "raw-invite-token" not in blob
    assert "private constraint" not in blob
    assert sentinel not in caplog.text
    assert "raw-invite-token" not in caplog.text

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO activation_event (
                        id, event_name, trip_id, actor_kind, occurred_at, dedup_key, metadata
                    ) VALUES (
                        :id, 'trip_created', :trip_id, 'system', now(), :dedup_key,
                        CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid4().hex,
                    "trip_id": created.trip.id,
                    "dedup_key": f"trip_created:privacy:{created.trip.id}",
                    "metadata": '{"email":"hidden@example.com"}',
                },
            )


def test_invite_sent_is_not_emitted(db: Session):
    created = _create(db)
    with pytest.raises(ActivationEventRejected):
        activation.record_activation_event(
            db,
            event_name="invite_sent",
            trip_id=created.trip.id,
            actor_kind="system",
            dedup_key=f"invite_sent:{created.trip.id}",
        )
    assert _events(db, "invite_sent") == []


def test_invite_opened_dedup_without_browser_id(db: Session, client: TestClient, api_session: Session):
    created = _create(api_session)
    user = api_session.get(User, created.membership.user_id)
    user.password_hash = auth.hash_password("correct-horse")
    api_session.flush()
    login = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "correct-horse"},
    )
    assert login.status_code == 200
    headers = {
        "Authorization": f"Bearer {login.json()['token']}",
        "X-Trip-Id": created.trip.id,
        "X-Membership-Id": created.membership.id,
    }
    created_invite = client.post(f"/api/trips/{created.trip.id}/invite", headers=headers)
    assert created_invite.status_code == 200
    token = created_invite.json()["token"]

    assert client.get(f"/api/invites/{token}").status_code == 200
    assert client.get(f"/api/invites/{token}").status_code == 200
    anonymous = _events(api_session, "invite_opened", created.trip.id)
    assert len(anonymous) == 1
    assert anonymous[0].dedup_key == f"invite_opened:{created_invite.json()['invite_id']}"
    assert anonymous[0].event_metadata == {"auth_state": "anonymous"}
    assert token not in anonymous[0].dedup_key
    assert token not in str(anonymous[0].event_metadata)

    authed = client.get(f"/api/invites/{token}", headers={"Authorization": f"Bearer {login.json()['token']}"})
    assert authed.status_code == 200
    client.get(f"/api/invites/{token}", headers={"Authorization": f"Bearer {login.json()['token']}"})
    opened = _events(api_session, "invite_opened", created.trip.id)
    assert len(opened) == 2
    assert {event.dedup_key for event in opened} == {
        f"invite_opened:{created_invite.json()['invite_id']}",
        f"invite_opened:{created_invite.json()['invite_id']}:account:{user.id}",
    }
    stored = " ".join(f"{event.dedup_key} {event.event_metadata}" for event in opened)
    assert token not in stored
    assert hashlib.sha256(token.encode("utf-8")).hexdigest() not in stored
    assert not hasattr(ActivationEvent, "browser_id")


def test_member_joined_only_for_new_membership(client: TestClient, api_session: Session):
    created = _create(api_session)
    user = api_session.get(User, created.membership.user_id)
    user.password_hash = auth.hash_password("correct-horse")
    api_session.flush()
    login = client.post("/api/auth/login", json={"email": user.email, "password": "correct-horse"})
    headers = {
        "Authorization": f"Bearer {login.json()['token']}",
        "X-Trip-Id": created.trip.id,
        "X-Membership-Id": created.membership.id,
    }
    invite = client.post(f"/api/trips/{created.trip.id}/invite", headers=headers).json()
    joiner = _user(api_session, "Alex")
    joiner.password_hash = auth.hash_password("correct-horse")
    api_session.flush()
    joiner_login = client.post(
        "/api/auth/login", json={"email": joiner.email, "password": "correct-horse"}
    )
    join_headers = {"Authorization": f"Bearer {joiner_login.json()['token']}"}
    first = client.post(
        f"/api/invites/{invite['token']}/account-join", headers=join_headers, json={}
    )
    second = client.post(
        f"/api/invites/{invite['token']}/account-join", headers=join_headers, json={}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["membership_id"] == second.json()["membership_id"]
    joined = _events(api_session, "member_joined", created.trip.id)
    assert len(joined) == 1
    assert joined[0].dedup_key == f"member_joined:{first.json()['membership_id']}"
    assert joined[0].invite_id == invite["invite_id"]
    assert joined[0].event_metadata == {"join_method": "invite_account"}

    guest_one = client.post(
        f"/api/invites/{invite['token']}/join", json={"display_name": "Guest One"}
    )
    guest_two = client.post(
        f"/api/invites/{invite['token']}/join", json={"display_name": "Guest Two"}
    )
    assert guest_one.json()["membership_id"] != guest_two.json()["membership_id"]
    assert len(_events(api_session, "member_joined", created.trip.id)) == 3


def test_preferences_submitted_once_and_first_submitted_at_preserved(db: Session):
    created = _create(db)
    first = pref_service.save_mine(
        db,
        created.membership,
        pref_service.PreferenceData(ideal_budget=100, maximum_budget=200),
    )
    pref = db.scalar(
        select(Preference).where(Preference.trip_membership_id == created.membership.id)
    )
    first_at = pref.first_submitted_at
    assert first_at is not None
    assert pref.submitted_at == first_at
    assert len(_events(db, "preferences_submitted", created.trip.id)) == 1
    assert _events(db, "preferences_updated", created.trip.id) == []

    pref.submitted_at = first_at - timedelta(days=1)
    db.flush()
    pref_service.save_mine(
        db,
        created.membership,
        pref_service.PreferenceData(ideal_budget=120, maximum_budget=220),
    )
    db.refresh(pref)
    assert pref.first_submitted_at == first_at
    assert pref.submitted_at > pref.first_submitted_at
    assert len(_events(db, "preferences_submitted", created.trip.id)) == 1
    assert len(_events(db, "preferences_updated", created.trip.id)) == 1
    assert "ideal_budget" not in str(_events(db, "preferences_submitted")[0].event_metadata)
    assert first["preference"]["ideal_budget"] == 100


def test_first_plan_created_once_per_trip(db: Session, trip_setup):
    item = trip_setup["item"]
    plan = trip_setup["plan"]
    orchestrator._log(db, item, "notice", {"title": "First change"})
    orchestrator._log(db, item, "booking", {"settledness": "booked"})
    db.flush()
    events = _events(db, "first_plan_created", trip_setup["trip"].id)
    assert len(events) == 1
    assert events[0].dedup_key == f"first_plan_created:{trip_setup['trip'].id}"
    assert events[0].event_metadata == {"origin": "notice"}

    poi = generator.Poi(
        candidate_id="poi-1",
        title="Generated stop",
        category="park",
        place="Lakefront",
        lat=41.88,
        lng=-87.62,
        price=0,
        duration_min=60,
        opens=9,
        closes=18,
        walk=None,
        access=(),
        diet=(),
        tags=(),
        source="mock",
    )
    draft = (
        generator.DraftItem(
            day_index=1,
            day_date=date(2026, 8, 15),
            start_hour=10,
            poi=poi,
            generated_by="rules",
        ),
        generator.DraftItem(
            day_index=1,
            day_date=date(2026, 8, 15),
            start_hour=12,
            poi=poi,
            generated_by="rules",
        ),
    )
    fresh = _create(db)
    generator._write_items(db, fresh.plan, draft)
    db.flush()
    generated = _events(db, "first_plan_created", fresh.trip.id)
    assert len(generated) == 1
    assert generated[0].event_metadata == {"origin": "rule_generate"}
    assert plan.id


def test_first_decision_completed_counts_approved_round(db: Session, trip_setup):
    item = trip_setup["item"]
    original = item.title
    row = _round(db, item, electorate=1, options=_options())
    db.add(
        Vote(
            round_id=row.id,
            trip_membership_id=trip_setup["organizer"].id,
            option_id="requested",
        )
    )
    db.flush()
    winner = orchestrator.settle_round(db, row)
    orchestrator.settle_round(db, row)

    assert winner == "requested"
    assert row.status == "closed"
    assert row.settled_at is not None
    assert row.winning_option_id == "requested"
    assert item.title == "Approved Group Choice ZZZ"
    assert item.title != original
    assert item.settledness == "settled"
    events = _events(db, "first_decision_completed", trip_setup["trip"].id)
    assert len(events) == 1
    assert events[0].event_metadata == {"outcome": "round_applied"}
    assert "Approved Group Choice" not in str(events[0].event_metadata)


def test_first_decision_completed_counts_rejected_round(db: Session, trip_setup):
    item = trip_setup["item"]
    original = item.title
    row = _round(db, item, electorate=2, options=_options())
    winner = orchestrator.settle_round(db, row)

    assert winner == "keep"
    assert row.status == "closed"
    assert row.settled_at is not None
    assert row.winning_option_id == "keep"
    assert item.title == original
    events = _events(db, "first_decision_completed", trip_setup["trip"].id)
    assert len(events) == 1
    assert events[0].event_metadata == {"outcome": "round_rejected"}

    other = _create(db)
    other_item = PlanItem(
        plan_id=other.plan.id,
        day_index=1,
        day_date=date(2026, 8, 15),
        start_hour=11,
        duration_min=60,
        title="Reopen block",
        place="Museum",
    )
    db.add(other_item)
    db.flush()
    reopen = DecisionRound(
        plan_item_id=other_item.id,
        kind="reopen",
        options=_options("Should not apply"),
        deadline=datetime.now(timezone.utc) + timedelta(hours=2),
        status="open",
        electorate_count=3,
        reason="Need another look",
    )
    db.add(reopen)
    db.flush()
    db.add(
        Vote(
            round_id=reopen.id,
            trip_membership_id=other.membership.id,
            option_id="requested",
        )
    )
    db.flush()
    assert orchestrator.settle_round(db, reopen) == "keep"
    assert other_item.title == "Reopen block"
    reopened = _events(db, "first_decision_completed", other.trip.id)
    assert len(reopened) == 1
    assert reopened[0].event_metadata == {"outcome": "round_rejected"}


def test_first_decision_completed_counts_deadlock_resolved(db: Session, trip_setup):
    item = trip_setup["item"]
    original = item.title
    proposal = ChangeProposal(
        plan_item_id=item.id,
        action_type="edit",
        before_json={"title": original},
        after_json={"title": "Blocked idea"},
        requested_by_membership_id=trip_setup["organizer"].id,
        status="escalated",
    )
    db.add(proposal)
    db.flush()
    resolve_deadlock(db, trip_setup["organizer"], proposal.id, "keep")

    assert proposal.status == "resolved_by_organizer"
    assert item.title == original
    events = _events(db, "first_decision_completed", trip_setup["trip"].id)
    assert len(events) == 1
    assert events[0].event_metadata == {"outcome": "deadlock_resolved"}

    split_trip = _create(db)
    split_item = PlanItem(
        plan_id=split_trip.plan.id,
        day_index=1,
        day_date=date(2026, 8, 16),
        start_hour=15,
        duration_min=60,
        title="Split block",
        place="Park",
    )
    db.add(split_item)
    db.flush()
    split_proposal = ChangeProposal(
        plan_item_id=split_item.id,
        action_type="edit",
        before_json={"title": "Split block"},
        after_json={"title": "Other idea"},
        requested_by_membership_id=split_trip.membership.id,
        status="escalated",
    )
    db.add(split_proposal)
    db.flush()
    resolve_deadlock(db, split_trip.membership, split_proposal.id, "split")
    assert split_proposal.status == "resolved_by_organizer"
    assert split_item.settledness == "settled"
    assert _events(db, "first_decision_completed", split_trip.trip.id)[0].event_metadata == {
        "outcome": "deadlock_resolved"
    }


def test_first_decision_completed_ignores_in_progress_round(db: Session, trip_setup):
    item = trip_setup["item"]
    row = _round(db, item, electorate=2, options=_options())
    orchestrator.cast_vote(db, row, trip_setup["participant"].id, "requested")

    assert row.status == "open"
    assert row.settled_at is None
    assert _events(db, "first_decision_completed", trip_setup["trip"].id) == []


def test_first_decision_completed_ignores_undetermined_round(db: Session, trip_setup):
    item = trip_setup["item"]
    legacy = _round(
        db,
        item,
        electorate=None,
        options=[{"id": "keep", "title": "Keep"}],
        deadline=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    settled_ids = orchestrator.settle_due_rounds(db)
    assert legacy.id not in settled_ids
    assert legacy.status == "open"
    assert legacy.settled_at is None

    closed = DecisionRound(
        plan_item_id=item.id,
        kind="normal",
        options=[],
        deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        status="closed",
        winning_option_id=None,
        settled_at=None,
        electorate_count=1,
    )
    db.add(closed)
    db.flush()
    orchestrator.settle_round(db, closed)
    assert closed.settled_at is None
    assert closed.winning_option_id is None

    proposal = ChangeProposal(
        plan_item_id=item.id,
        action_type="edit",
        before_json={"title": item.title},
        after_json={"title": "Late"},
        requested_by_membership_id=trip_setup["organizer"].id,
        status="waiting_affected_members",
        deadline=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(proposal)
    db.flush()
    orchestrator.expire_due_proposals(db)
    assert proposal.status == "expired"
    assert _events(db, "first_decision_completed", trip_setup["trip"].id) == []


def test_first_decision_completed_ignores_withdrawn_proposal(db: Session, trip_setup):
    proposal = ChangeProposal(
        plan_item_id=trip_setup["item"].id,
        action_type="edit",
        before_json={"title": trip_setup["item"].title},
        after_json={"title": "Withdrawn idea"},
        requested_by_membership_id=trip_setup["organizer"].id,
        status="waiting_affected_members",
    )
    db.add(proposal)
    db.flush()
    orchestrator.withdraw_proposal(db, proposal)

    assert proposal.status == "withdrawn"
    assert _events(db, "first_decision_completed", trip_setup["trip"].id) == []


def test_notice_auto_applied_is_not_first_decision_completed(db: Session, trip_setup):
    item = trip_setup["item"]
    verdict = Classification(path=DecisionPath.NOTICE, headline="Updated", detail="Direct")
    orchestrator._do_notice(
        db, item, {"title": "Direct edit"}, trip_setup["organizer"].id, verdict
    )
    second = PlanItem(
        plan_id=trip_setup["plan"].id,
        day_index=1,
        day_date=date(2026, 8, 16),
        start_hour=16,
        duration_min=30,
        title="Second block",
        place="Cafe",
    )
    db.add(second)
    db.flush()
    orchestrator._do_notice(
        db, second, {"title": "Another direct edit"}, trip_setup["organizer"].id, verdict
    )

    notices = _events(db, "notice_auto_applied", trip_setup["trip"].id)
    assert len(notices) == 1
    assert notices[0].dedup_key == f"notice_auto_applied:{trip_setup['trip'].id}"
    assert notices[0].event_metadata == {"source": "notice"}
    assert "Direct edit" not in str(notices[0].event_metadata)
    assert _events(db, "first_decision_completed", trip_setup["trip"].id) == []


def test_booking_and_confirm_paths_are_not_first_decision_completed(db: Session, trip_setup):
    item = trip_setup["item"]
    trip = trip_setup["trip"]
    organizer = trip_setup["organizer"]
    orchestrator.set_booking_status_for_actor(
        db,
        item,
        trip_id=trip.id,
        actor_membership_id=organizer.id,
        booked=True,
    )
    assert item.settledness == "booked"
    assert _events(db, "first_decision_completed", trip.id) == []

    declined = ChangeProposal(
        plan_item_id=item.id,
        action_type="edit",
        before_json={"title": item.title},
        after_json={"title": "Declined idea"},
        requested_by_membership_id=organizer.id,
        status="waiting_affected_members",
    )
    db.add(declined)
    db.flush()
    db.add(
        ProposalDecision(
            proposal_id=declined.id,
            trip_membership_id=organizer.id,
            status="pending",
        )
    )
    db.flush()
    assert orchestrator.decide_proposal(db, declined, organizer.id, "declined") is False
    assert declined.status == "declined"

    applied = ChangeProposal(
        plan_item_id=item.id,
        action_type="edit",
        before_json={"title": item.title},
        after_json={"title": "Confirmed title"},
        requested_by_membership_id=organizer.id,
        status="waiting_affected_members",
    )
    db.add(applied)
    db.flush()
    db.add(
        ProposalDecision(
            proposal_id=applied.id,
            trip_membership_id=organizer.id,
            status="pending",
        )
    )
    db.flush()
    assert orchestrator.decide_proposal(db, applied, organizer.id, "accepted") is True
    assert applied.status == "applied"
    assert _events(db, "first_decision_completed", trip.id) == []
    assert len(_events(db, "first_plan_created", trip.id)) == 1


def test_client_share_event_is_organizer_only_and_deduped(client: TestClient, api_session: Session):
    created = _create(api_session)
    other = _create(api_session)
    user = api_session.get(User, created.membership.user_id)
    user.password_hash = auth.hash_password("correct-horse")
    participant = TripMembership(
        trip_id=created.trip.id,
        user_id=_user(api_session, "Sam").id,
        role="participant",
        status="joined",
    )
    api_session.add(participant)
    api_session.flush()
    api_session.get(User, participant.user_id).password_hash = auth.hash_password("correct-horse")
    api_session.flush()
    organizer_login = client.post(
        "/api/auth/login", json={"email": user.email, "password": "correct-horse"}
    )
    organizer_headers = {
        "Authorization": f"Bearer {organizer_login.json()['token']}",
        "X-Trip-Id": created.trip.id,
        "X-Membership-Id": created.membership.id,
    }
    invite = client.post(f"/api/trips/{created.trip.id}/invite", headers=organizer_headers).json()
    foreign = _invite_row(api_session, other.trip.id, "foreign-invite-token")

    denied = client.post(
        f"/api/trips/{created.trip.id}/activation-events",
        headers={
            "Authorization": f"Bearer {client.post('/api/auth/login', json={'email': api_session.get(User, participant.user_id).email, 'password': 'correct-horse'}).json()['token']}",
            "X-Trip-Id": created.trip.id,
            "X-Membership-Id": participant.id,
        },
        json={"event_name": "invite_link_copied", "invite_id": invite["invite_id"]},
    )
    assert denied.status_code == 403

    leaked = client.post(
        f"/api/trips/{created.trip.id}/activation-events",
        headers=organizer_headers,
        json={"event_name": "invite_link_copied", "invite_id": foreign.id},
    )
    assert leaked.status_code == 404
    assert _events(api_session, "invite_link_copied", other.trip.id) == []

    extra = client.post(
        f"/api/trips/{created.trip.id}/activation-events",
        headers=organizer_headers,
        json={
            "event_name": "invite_link_copied",
            "invite_id": invite["invite_id"],
            "email": "nope@example.com",
        },
    )
    assert extra.status_code == 422

    copied = client.post(
        f"/api/trips/{created.trip.id}/activation-events",
        headers=organizer_headers,
        json={"event_name": "invite_link_copied", "invite_id": invite["invite_id"]},
    )
    again = client.post(
        f"/api/trips/{created.trip.id}/activation-events",
        headers=organizer_headers,
        json={"event_name": "invite_link_copied", "invite_id": invite["invite_id"]},
    )
    wechat = client.post(
        f"/api/trips/{created.trip.id}/activation-events",
        headers=organizer_headers,
        json={
            "event_name": "invite_shared_wechat_template",
            "invite_id": invite["invite_id"],
        },
    )
    assert copied.status_code == again.status_code == wechat.status_code == 200
    copies = _events(api_session, "invite_link_copied", created.trip.id)
    assert len(copies) == 1
    assert copies[0].event_metadata == {"channel": "clipboard"}
    assert copies[0].actor_kind == "account"
    assert _events(api_session, "invite_shared_wechat_template", created.trip.id)[0].event_metadata == {
        "channel": "wechat_template"
    }
    assert _events(api_session, "invite_sent") == []


def test_activation_funnel_sql_acceptance_and_preference_rates(db: Session):
    now = datetime.now(timezone.utc)
    trip_a = _create(db)
    trip_b = _create(db)
    invites_a = [_invite_row(db, trip_a.trip.id, f"a-{index}") for index in range(3)]
    invite_b = _invite_row(db, trip_b.trip.id, "b-0")
    unopened_b = _invite_row(db, trip_b.trip.id, "b-unopened")
    guest_a = _guest(db, trip_a.trip.id, "Guest A")
    account_a = TripMembership(
        trip_id=trip_a.trip.id,
        user_id=_user(db, "AccountA").id,
        role="participant",
        join_method="invite_account",
        status="joined",
    )
    recent = _guest(db, trip_a.trip.id, "Recent")
    guest_b = _guest(db, trip_b.trip.id, "Guest B")
    db.add(account_a)
    db.flush()

    def open_invite(invite: InviteLink):
        activation.record_invite_opened(
            db, trip_id=invite.trip_id, invite_id=invite.id, opener=None
        )

    def join(invite: InviteLink, membership: TripMembership, method: str, when: datetime):
        actor = "guest" if method == "invite_guest" else "account"
        activation.record_member_joined(
            db,
            trip_id=invite.trip_id,
            membership_id=membership.id,
            invite_id=invite.id,
            actor_kind=actor,
            join_method=method,
        )
        row = db.scalar(
            select(ActivationEvent).where(
                ActivationEvent.dedup_key == f"member_joined:{membership.id}"
            )
        )
        row.occurred_at = when

    def submit(membership: TripMembership, when: datetime):
        activation.record_preferences_submitted(db, membership)
        row = db.scalar(
            select(ActivationEvent).where(
                ActivationEvent.dedup_key == f"preferences_submitted:{membership.id}"
            )
        )
        row.occurred_at = when

    for invite in invites_a:
        open_invite(invite)
    open_invite(invite_b)
    join(invites_a[0], guest_a, "invite_guest", now - timedelta(hours=80))
    join(invites_a[1], account_a, "invite_account", now - timedelta(hours=80))
    join(invites_a[0], recent, "invite_guest", now - timedelta(hours=10))
    join(unopened_b, guest_b, "invite_guest", now - timedelta(hours=90))
    submit(guest_a, now - timedelta(hours=70))
    submit(account_a, now - timedelta(hours=1))
    submit(recent, now - timedelta(hours=9))
    activation.record_preferences_updated(db, account_a)
    updated = db.scalar(
        select(ActivationEvent).where(
            ActivationEvent.dedup_key == f"preferences_updated:{account_a.id}"
        )
    )
    updated.occurred_at = now - timedelta(hours=75)
    db.flush()

    rows = db.execute(text(FUNNEL_SQL)).mappings().all()
    indexed = {(row["metric"], row["scope_trip_id"], row["audience"]): row for row in rows}

    acceptance_a = indexed[("invitation_acceptance_rate", trip_a.trip.id, None)]
    acceptance_b = indexed[("invitation_acceptance_rate", trip_b.trip.id, None)]
    acceptance_all = indexed[("invitation_acceptance_rate", None, None)]
    assert (acceptance_a["numerator"], acceptance_a["denominator"]) == (2, 3)
    assert (acceptance_b["numerator"], acceptance_b["denominator"]) == (0, 1)
    assert (acceptance_all["numerator"], acceptance_all["denominator"]) == (2, 4)
    assert acceptance_all["sample_flag"] == "样本不足"

    guest_a_rate = indexed[("preference_completion_72h", trip_a.trip.id, "invite_guest")]
    account_a_rate = indexed[("preference_completion_72h", trip_a.trip.id, "invite_account")]
    combined_a = indexed[("preference_completion_72h", trip_a.trip.id, "combined")]
    combined_all = indexed[("preference_completion_72h", None, "combined")]
    guest_all = indexed[("preference_completion_72h", None, "invite_guest")]
    assert (guest_a_rate["numerator"], guest_a_rate["denominator"]) == (1, 1)
    assert (account_a_rate["numerator"], account_a_rate["denominator"]) == (0, 1)
    assert (combined_a["numerator"], combined_a["denominator"]) == (1, 2)
    assert (combined_all["numerator"], combined_all["denominator"]) == (1, 3)
    assert (guest_all["numerator"], guest_all["denominator"]) == (1, 2)
    assert combined_all["sample_flag"] == "样本不足"
    assert "Duplicate guest" in combined_all["notes"]
    assert acceptance_a["scope_trip_id"] != acceptance_b["scope_trip_id"]
