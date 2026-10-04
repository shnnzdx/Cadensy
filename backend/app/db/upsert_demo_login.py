"""Upsert the legacy organizer login without recreating demo trip data.

This helper is kept for compatibility with older local/admin workflows. It
creates missing schema objects and creates or updates the organizer account,
but it no longer recreates demo trips or memberships by default.
"""

from __future__ import annotations

import os

from sqlalchemy import func, select

from ..domain.auth import hash_password, normalize_email
from .init_schema import init_schema
from .models import TripMembership, User
from .session import SessionLocal

ORGANIZER_EMAIL = normalize_email(
    os.getenv("SEED_ORGANIZER_EMAIL", "organizer@cadensy.local")
)
ORGANIZER_NAME = os.getenv("SEED_ORGANIZER_NAME", "Mia Chen")
ORGANIZER_PASSWORD = os.getenv("SEED_ORGANIZER_PASSWORD", "12345678")
DEMO_TRIP_NAME = os.getenv("SEED_DEMO_TRIP_NAME", "TripSync Cloud Demo")
DEMO_DESTINATION = os.getenv("SEED_DEMO_DESTINATION", "Chicago")


def ensure_cloud_schema() -> None:
    # The helper no longer applies ad-hoc DDL. Existing legacy
    # ``change_proposal.extended_at`` columns remain untouched; they must be
    # reconciled by a reviewed migration rather than silently dropped here.
    init_schema()


def upsert_demo_login() -> dict:
    ensure_cloud_schema()

    with SessionLocal() as db:
        user = db.scalar(
            select(User).where(func.lower(User.email) == ORGANIZER_EMAIL)
        )
        created_user = user is None
        if user is None:
            user = User(
                name=ORGANIZER_NAME,
                email=ORGANIZER_EMAIL,
                avatar=None,
            )
            db.add(user)
            db.flush()
        else:
            user.name = user.name or ORGANIZER_NAME
            user.email = ORGANIZER_EMAIL

        user.password_hash = hash_password(ORGANIZER_PASSWORD)

        membership = db.scalar(
            select(TripMembership)
            .where(
                TripMembership.user_id == user.id,
                TripMembership.role == "organizer",
            )
            .order_by(TripMembership.created_at)
        )

        db.commit()

        return {
            "updated": True,
            "email": ORGANIZER_EMAIL,
            "created_user": created_user,
            "created_trip": False,
            "created_membership": False,
            "trip_id": membership.trip_id if membership else None,
            "membership_id": membership.id if membership else None,
        }


if __name__ == "__main__":
    result = upsert_demo_login()
    print(
        "legacy organizer login ready:",
        {
            "updated": result["updated"],
            "email": result["email"],
            "created_user": result["created_user"],
            "created_trip": result["created_trip"],
            "created_membership": result["created_membership"],
            "trip_id": result["trip_id"],
            "membership_id": result["membership_id"],
        },
    )
