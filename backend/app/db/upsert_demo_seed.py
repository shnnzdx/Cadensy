"""Upsert the local demo dataset into a shared database without dropping data.

This is the cloud-safe companion to ``seed.py``. It reuses the same demo trip,
members, preferences, constraints, and itinerary shape, but it never deletes
rows or drops tables. Re-running it updates the known demo records in place.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..domain.auth import hash_password, normalize_email
from .models import (
    MemberConstraint,
    MemberConstraintPrivate,
    Plan,
    PlanItem,
    Preference,
    Trip,
    TripMembership,
    User,
)
from .seed import DEMO_END, DEMO_START, ITEMS, MEMBERS, PHOTOS
from .session import SessionLocal
from .upsert_demo_login import ensure_cloud_schema

ORGANIZER_EMAIL = normalize_email(os.getenv("SEED_ORGANIZER_EMAIL", MEMBERS[0][1]))
ORGANIZER_PASSWORD = os.getenv("SEED_ORGANIZER_PASSWORD", "12345678")
DEMO_TRIP_NAME = os.getenv("SEED_DEMO_TRIP_NAME", "Mia's 30th in Chicago")
DEMO_DESTINATION = os.getenv("SEED_DEMO_DESTINATION", "Chicago")


def _user_by_email(db, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == normalize_email(email)))


def _ensure_user(
    db,
    *,
    name: str,
    email: str,
    password: str | None,
) -> tuple[User, bool]:
    user = _user_by_email(db, email)
    created = user is None
    if user is None:
        user = User(name=name, email=normalize_email(email), avatar=None)
        db.add(user)
        db.flush()
    else:
        user.name = name
        user.email = normalize_email(email)
    if password is not None:
        user.password_hash = hash_password(password)
    return user, created


def _ensure_trip(db, organizer: User) -> tuple[Trip, bool]:
    trip = db.scalar(
        select(Trip)
        .where(
            Trip.created_by_user_id == organizer.id,
            Trip.name == DEMO_TRIP_NAME,
        )
        .order_by(Trip.created_at)
    )
    created = trip is None
    if trip is None:
        trip = Trip(
            name=DEMO_TRIP_NAME,
            destination=DEMO_DESTINATION,
            preferred_start_date=DEMO_START,
            preferred_end_date=DEMO_END,
            expected_group_size=len(MEMBERS),
            currency="USD",
            preferences_deadline=datetime.now(timezone.utc),
            status="traveling",
            created_by_user_id=organizer.id,
        )
        db.add(trip)
        db.flush()
    else:
        trip.destination = DEMO_DESTINATION
        trip.preferred_start_date = DEMO_START
        trip.preferred_end_date = DEMO_END
        trip.expected_group_size = len(MEMBERS)
        trip.currency = "USD"
        trip.status = "traveling"
        trip.created_by_user_id = organizer.id
    return trip, created


def _ensure_membership(
    db,
    *,
    trip_id: str,
    user_id: str,
    role: str,
    join_method: str,
    status: str,
) -> tuple[TripMembership, bool]:
    membership = db.scalar(
        select(TripMembership)
        .where(TripMembership.trip_id == trip_id, TripMembership.user_id == user_id)
        .order_by(TripMembership.created_at)
    )
    created = membership is None
    if membership is None:
        membership = TripMembership(
            trip_id=trip_id,
            user_id=user_id,
            role=role,
            join_method=join_method,
            status=status,
        )
        db.add(membership)
        db.flush()
    else:
        membership.role = role
        membership.join_method = join_method
        membership.status = status
    return membership, created


def _ensure_preference(db, membership: TripMembership) -> None:
    preference = db.scalar(
        select(Preference).where(Preference.trip_membership_id == membership.id)
    )
    values = {
        "preferred_start_date": DEMO_START,
        "preferred_end_date": DEMO_END,
        "available_start_date": DEMO_START,
        "available_end_date": DEMO_END,
        "ideal_budget": 500.0,
        "maximum_budget": 650.0,
        "currency": "USD",
        "budget_visibility": "planning_only",
        "travel_style": "Relaxed",
        "top_interests": ["Food", "Culture", "Relaxed"],
        "submitted_at": datetime.now(timezone.utc),
    }
    if preference is None:
        db.add(Preference(trip_membership_id=membership.id, **values))
        return
    for key, value in values.items():
        setattr(preference, key, value)


def _ensure_constraint(
    db,
    *,
    membership_id: str,
    kind: str,
    importance: str,
    params: dict,
    private_text: str | None = None,
) -> None:
    constraint = db.scalar(
        select(MemberConstraint)
        .where(
            MemberConstraint.trip_membership_id == membership_id,
            MemberConstraint.kind == kind,
        )
        .order_by(MemberConstraint.created_at)
    )
    if constraint is None:
        constraint = MemberConstraint(
            trip_membership_id=membership_id,
            kind=kind,
            importance=importance,
            params=params,
        )
        db.add(constraint)
        db.flush()
    else:
        constraint.importance = importance
        constraint.params = params

    if private_text is None:
        return

    private_row = db.get(MemberConstraintPrivate, constraint.id)
    if private_row is None:
        db.add(
            MemberConstraintPrivate(
                constraint_id=constraint.id,
                original_text=private_text,
                visibility="planning_only",
            )
        )
        return
    private_row.original_text = private_text
    private_row.visibility = "planning_only"


def _ensure_plan(db, trip_id: str) -> tuple[Plan, bool]:
    plan = db.scalar(
        select(Plan).where(Plan.trip_id == trip_id).order_by(Plan.created_at)
    )
    created = plan is None
    if plan is None:
        plan = Plan(
            trip_id=trip_id,
            status="active",
            blocked_reason=None,
            estimated_total_per_person=310.0,
            currency="USD",
        )
        db.add(plan)
        db.flush()
    return plan, created


def _item_identity(item_tuple: tuple) -> tuple:
    day, when, hour, _mins, title, _place, _price, _is_meal, _settledness, _lat, _lng = item_tuple
    return (day, when, hour, title)


def _existing_items_map(db, plan_id: str) -> dict[tuple, PlanItem]:
    rows = db.scalars(select(PlanItem).where(PlanItem.plan_id == plan_id)).all()
    return {
        (row.day_index, row.day_date, row.start_hour, row.title): row
        for row in rows
    }


def _upsert_items(db, plan: Plan) -> tuple[int, int]:
    # S-1a: lock every existing item in id order before mutating several rows.
    db.scalars(
        select(PlanItem)
        .where(PlanItem.plan_id == plan.id)
        .order_by(PlanItem.id)
        .with_for_update()
    ).all()
    existing = _existing_items_map(db, plan.id)
    created = 0
    updated = 0
    for index, item_tuple in enumerate(ITEMS):
        day, when, hour, mins, title, place, price, is_meal, settledness, lat, lng = item_tuple
        key = _item_identity(item_tuple)
        row = existing.get(key)
        if row is None:
            row = PlanItem(plan_id=plan.id)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.day_index = day
        row.day_date = when
        row.start_hour = hour
        row.duration_min = mins
        row.title = title
        row.place = place
        row.price_per_person = price
        row.is_meal = is_meal
        row.settledness = settledness
        row.lat = lat
        row.lng = lng
        row.photo_url = PHOTOS[index % len(PHOTOS)]
        row.source = "mock"
    return created, updated


def _count(iterable: Iterable[object]) -> int:
    return sum(1 for _ in iterable)


def upsert_demo_seed() -> dict:
    ensure_cloud_schema()

    with SessionLocal() as db:
        users_by_email: dict[str, User] = {}
        created_users = 0
        for name, email, role in MEMBERS:
            password = ORGANIZER_PASSWORD if normalize_email(email) == ORGANIZER_EMAIL else None
            user, created = _ensure_user(
                db,
                name=name,
                email=ORGANIZER_EMAIL if role == "organizer" else email,
                password=password,
            )
            users_by_email[normalize_email(user.email)] = user
            created_users += int(created)

        organizer = users_by_email[ORGANIZER_EMAIL]
        trip, created_trip = _ensure_trip(db, organizer)

        memberships_by_email: dict[str, TripMembership] = {}
        created_memberships = 0
        for name, email, role in MEMBERS:
            user = users_by_email[ORGANIZER_EMAIL if role == "organizer" else normalize_email(email)]
            membership, created = _ensure_membership(
                db,
                trip_id=trip.id,
                user_id=user.id,
                role=role,
                join_method="creator" if role == "organizer" else "invite_login",
                status="preferences_submitted",
            )
            memberships_by_email[normalize_email(user.email)] = membership
            created_memberships += int(created)
            _ensure_preference(db, membership)

        _ensure_constraint(
            db,
            membership_id=memberships_by_email[ORGANIZER_EMAIL].id,
            kind="time_window",
            importance="required",
            params={"earliest_hour": 9.0},
            private_text="No activities before 9:00 AM",
        )
        _ensure_constraint(
            db,
            membership_id=memberships_by_email["sam@example.com"].id,
            kind="budget_ceiling",
            importance="required",
            params={"max_total_per_person": 650.0},
        )
        _ensure_constraint(
            db,
            membership_id=memberships_by_email["tom@example.com"].id,
            kind="walk_limit",
            importance="required",
            params={"max_km_per_day": 3.0},
        )

        plan, created_plan = _ensure_plan(db, trip.id)
        created_items, updated_items = _upsert_items(db, plan)
        db.flush()
        plan.status = "active"
        plan.blocked_reason = None
        plan.estimated_total_per_person = 310.0
        plan.currency = "USD"

        db.commit()
        return {
            "updated": True,
            "trip_id": trip.id,
            "plan_id": plan.id,
            "created_trip": created_trip,
            "created_plan": created_plan,
            "created_users": created_users,
            "created_memberships": created_memberships,
            "created_items": created_items,
            "updated_items": updated_items,
            "members": _count(memberships_by_email.values()),
            "items": len(ITEMS),
        }


if __name__ == "__main__":
    print("demo seed ready:", upsert_demo_seed())
