"""Seed and inspect the explicitly disposable PR-06B.4 browser-E2E database.

This helper intentionally never creates or drops a database.  The PowerShell
runner validates the local test-only database name before any destructive
operation; this module only writes rows after Alembic has reached ``head``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Conversation, ConversationMessage, ConversationSubmission, Plan, PlanItem, Trip, TripMembership, User
from app.db.seed import seed
from app.db.session import SessionLocal
from app.domain.auth import hash_password


ORGANIZER_EMAIL = "organizer@cadensy.local"
ORGANIZER_PASSWORD = "12345678"
MEMBER_EMAIL = "elena@example.com"
MEMBER_PASSWORD = "E2E-member-123"


def seed_fixture(output: Path) -> None:
    result = seed()
    with SessionLocal.begin() as db:
        member = db.scalar(select(User).where(User.email == MEMBER_EMAIL))
        if member is None:
            raise RuntimeError("demo seed did not create E2E member")
        member.password_hash = hash_password(MEMBER_PASSWORD)
        organizer = db.scalar(select(User).where(User.email == ORGANIZER_EMAIL))
        trip = db.get(Trip, result["trip_id"])
        plan = db.scalar(select(Plan).where(Plan.trip_id == result["trip_id"]))
        item = db.scalar(select(PlanItem).where(PlanItem.plan_id == plan.id).order_by(PlanItem.id))
        membership = db.scalar(select(TripMembership).where(TripMembership.trip_id == trip.id, TripMembership.user_id == member.id))
        payload = {
            "trip_id": trip.id,
            "plan_id": plan.id,
            "plan_item_id": item.id,
            "organizer": {"email": ORGANIZER_EMAIL, "password": ORGANIZER_PASSWORD, "user_id": organizer.id},
            "member": {"email": MEMBER_EMAIL, "password": MEMBER_PASSWORD, "membership_id": membership.id, "user_id": member.id},
        }
    output.write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps(payload))


def assert_counts(trip_id: str, membership_id: str, expected_user: int, expected_assistant: int) -> None:
    with SessionLocal() as db:
        conversation = db.scalar(select(Conversation).where(Conversation.trip_id == trip_id, Conversation.owner_membership_id == membership_id, Conversation.scope == "personal"))
        if conversation is None:
            raise AssertionError("expected personal conversation is absent")
        users = db.scalar(select(func.count()).select_from(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id, ConversationMessage.role == "user"))
        assistants = db.scalar(select(func.count()).select_from(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id, ConversationMessage.role == "assistant"))
        submissions = db.scalar(select(func.count()).select_from(ConversationSubmission).where(ConversationSubmission.conversation_id == conversation.id))
        if (users, assistants) != (expected_user, expected_assistant) or submissions != expected_user:
            raise AssertionError(f"unexpected durable rows users={users} assistants={assistants} submissions={submissions}")
        print(json.dumps({"conversation_id": conversation.id, "user_messages": users, "assistant_messages": assistants, "submissions": submissions}))


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "seed":
        seed_fixture(Path(sys.argv[2]))
    elif command == "assert-counts":
        assert_counts(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
    else:
        raise SystemExit("usage: pr06b4_e2e_fixture.py seed <fixture.json> | assert-counts <trip> <membership> <users> <assistants>")
