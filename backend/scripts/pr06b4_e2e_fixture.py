"""Seed and inspect the explicitly disposable PR-06B.4 browser-E2E database.

The runner owns database creation and deletion. This helper may create only
initial synthetic fixture data and otherwise performs read-only assertions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.models import (
    Conversation,
    ConversationMessage,
    ConversationMessageOption,
    ConversationSubmission,
    GuestSession,
    InviteLink,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
    User,
)
from app.db.seed import seed
from app.db.session import SessionLocal
from app.domain.auth import hash_password


ORGANIZER_EMAIL = "organizer@cadensy.local"
ORGANIZER_PASSWORD = "12345678"
MEMBER_EMAIL = "elena@example.com"
MEMBER_PASSWORD = "E2E-member-123"
OWNER_EMAIL = "owner-a@e2e.cadensy.local"
OWNER_PASSWORD = "E2E-owner-A-123"


def _one(db, model, identifier: str):
    row = db.get(model, identifier)
    if row is None:
        raise AssertionError(f"missing {model.__name__} {identifier}")
    return row


def seed_fixture(output: Path) -> None:
    result = seed()
    with SessionLocal.begin() as db:
        member = db.scalar(select(User).where(User.email == MEMBER_EMAIL))
        organizer = db.scalar(select(User).where(User.email == ORGANIZER_EMAIL))
        if member is None or organizer is None:
            raise RuntimeError("demo seed did not create the E2E Accounts")
        member.password_hash = hash_password(MEMBER_PASSWORD)
        trip = _one(db, Trip, result["trip_id"])
        plan = db.scalar(select(Plan).where(Plan.trip_id == trip.id))
        if plan is None:
            raise RuntimeError("demo seed did not create the E2E Plan")
        item = db.scalar(select(PlanItem).where(PlanItem.plan_id == plan.id).order_by(PlanItem.id))
        member_membership = db.scalar(
            select(TripMembership).where(
                TripMembership.trip_id == trip.id, TripMembership.user_id == member.id
            )
        )
        if item is None or member_membership is None:
            raise RuntimeError("demo seed lacks the member or plan item required by E2E")

        owner = User(
            name="E2E Account Owner A",
            email=OWNER_EMAIL,
            password_hash=hash_password(OWNER_PASSWORD),
        )
        db.add(owner)
        db.flush()
        owner_membership = TripMembership(
            trip_id=trip.id,
            user_id=owner.id,
            role="participant",
            join_method="invite_account",
            status="joined",
        )
        db.add(owner_membership)

        # J05 proves that an Account session is global while Trip authority is
        # local. This is initial fixture data, not a lifecycle bypass.
        unrelated_trip = Trip(
            name="E2E unrelated authorized trip",
            destination="Madison",
            created_by_user_id=organizer.id,
        )
        db.add(unrelated_trip)
        db.flush()
        unrelated_membership = TripMembership(
            trip_id=unrelated_trip.id,
            user_id=member.id,
            role="participant",
            join_method="invite_account",
            status="joined",
        )
        db.add(unrelated_membership)
        db.flush()
        payload = {
            "trip_id": trip.id,
            "trip_facts_revision": trip.planning_facts_revision,
            "plan_id": plan.id,
            "plan_item_id": item.id,
            "plan_revision": plan.revision,
            "organizer": {"email": ORGANIZER_EMAIL, "password": ORGANIZER_PASSWORD, "user_id": organizer.id},
            "member": {"email": MEMBER_EMAIL, "password": MEMBER_PASSWORD, "membership_id": member_membership.id, "user_id": member.id},
            "owner_a": {"email": OWNER_EMAIL, "password": OWNER_PASSWORD, "membership_id": owner_membership.id, "user_id": owner.id},
            "unrelated_trip_id": unrelated_trip.id,
            "unrelated_membership_id": unrelated_membership.id,
        }
    output.write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps(payload))


def assert_submission(conversation_id: str, key: str, expected: int) -> None:
    with SessionLocal() as db:
        conversation = _one(db, Conversation, conversation_id)
        submissions = list(db.scalars(select(ConversationSubmission).where(
            ConversationSubmission.conversation_id == conversation.id,
            ConversationSubmission.idempotency_key == key,
        )).all())
        user_count = db.scalar(select(func.count()).select_from(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.id.in_([row.user_message_id for row in submissions] or ["__none__"]),
            ConversationMessage.role == "user",
        ))
        if len(submissions) != expected or user_count != expected:
            raise AssertionError(f"idempotency key {key} has submissions={len(submissions)} user_messages={user_count}, expected={expected}")
        print(json.dumps({"conversation_id": conversation.id, "idempotency_key": key, "submissions": len(submissions), "user_messages": user_count}))


def assert_scope(conversation_id: str, membership_id: str, expected_text: str) -> None:
    with SessionLocal() as db:
        conversation = _one(db, Conversation, conversation_id)
        messages = list(db.scalars(select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id
        ).order_by(ConversationMessage.sequence)).all())
        content = [row.content for row in messages]
        if conversation.owner_membership_id != membership_id or expected_text not in content:
            raise AssertionError("conversation scope does not retain its expected owner/content")
        print(json.dumps({"conversation_id": conversation.id, "owner_membership_id": conversation.owner_membership_id, "messages": content}))


def assert_lifecycle(trip_id: str, owner_a_id: str, owner_b_id: str, user_id: str, conversation_id: str) -> None:
    with SessionLocal() as db:
        old = _one(db, TripMembership, owner_a_id)
        new = _one(db, TripMembership, owner_b_id)
        conversation = _one(db, Conversation, conversation_id)
        active = db.scalar(select(func.count()).select_from(TripMembership).where(
            TripMembership.trip_id == trip_id,
            TripMembership.user_id == user_id,
            TripMembership.status != "removed",
        ))
        if old.status != "removed" or new.id == old.id or new.status == "removed" or conversation.owner_membership_id != old.id or active != 1:
            raise AssertionError("membership incarnation or historical conversation ownership is invalid")
        print(json.dumps({"old_membership": old.id, "old_status": old.status, "new_membership": new.id, "active_account_memberships": active, "conversation_owner": conversation.owner_membership_id}))


def assert_option(conversation_id: str, target_item_id: str) -> None:
    with SessionLocal() as db:
        rows = list(db.execute(select(ConversationMessage, ConversationMessageOption).join(
            ConversationMessageOption,
            ConversationMessageOption.assistant_message_id == ConversationMessage.id,
        ).where(ConversationMessage.conversation_id == conversation_id)).all())
        if len(rows) != 1:
            raise AssertionError(f"expected exactly one persisted option, found {len(rows)}")
        message, option = rows[0]
        if (
            message.role != "assistant" or option.target_plan_item_id != target_item_id
            or option.safe_patch != {"start_hour": 15.0}
            or option.plan_revision is None or option.planning_facts_revision is None
        ):
            raise AssertionError("persisted historical option has the wrong parent, target, patch, or revisions")
        print(json.dumps({"assistant_message_id": message.id, "option_id": option.id, "target_item_id": option.target_plan_item_id, "safe_patch": option.safe_patch, "plan_revision": option.plan_revision, "planning_facts_revision": option.planning_facts_revision}))


def assert_quarantine(trip_id: str, membership_id: str, conversation_id: str, invite_id: str) -> None:
    with SessionLocal() as db:
        membership = _one(db, TripMembership, membership_id)
        conversation = _one(db, Conversation, conversation_id)
        invite = _one(db, InviteLink, invite_id)
        assistants = db.scalar(select(func.count()).select_from(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
        ))
        options = db.scalar(select(func.count()).select_from(ConversationMessageOption).join(
            ConversationMessage, ConversationMessageOption.assistant_message_id == ConversationMessage.id,
        ).where(ConversationMessage.conversation_id == conversation.id))
        guests = db.scalar(select(func.count()).select_from(GuestSession).where(
            GuestSession.membership_id == membership.id,
            GuestSession.revoked_at.is_(None),
        ))
        if membership.quarantined_at is None or membership.quarantine_reason != "security_incident" or invite.revoked_at is None or assistants != 0 or options != 0 or guests != 0:
            raise AssertionError("quarantine did not preserve the required access-revocation and late-completion boundary")
        print(json.dumps({"membership_id": membership.id, "quarantined": True, "invite_revoked": True, "active_bound_guest_sessions": guests, "assistant_messages": assistants, "options": options, "conversation_owner": conversation.owner_membership_id, "trip_id": trip_id}))


def count_conversations(trip_id: str, membership_id: str) -> None:
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Conversation).where(
            Conversation.trip_id == trip_id, Conversation.owner_membership_id == membership_id,
        ))
        print(json.dumps({"trip_id": trip_id, "membership_id": membership_id, "conversation_count": count}))


def assert_plan_revision(plan_id: str, previous_revision: int) -> None:
    with SessionLocal() as db:
        plan = _one(db, Plan, plan_id)
        if plan.revision <= previous_revision:
            raise AssertionError(f"Plan revision did not advance beyond {previous_revision}")
        print(json.dumps({"plan_id": plan.id, "previous_revision": previous_revision, "current_revision": plan.revision}))


def main() -> None:
    command = sys.argv[1]
    if command == "seed":
        seed_fixture(Path(sys.argv[2]))
    elif command == "assert-submission":
        assert_submission(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif command == "assert-scope":
        assert_scope(sys.argv[2], sys.argv[3], sys.argv[4])
    elif command == "assert-lifecycle":
        assert_lifecycle(*sys.argv[2:7])
    elif command == "assert-option":
        assert_option(sys.argv[2], sys.argv[3])
    elif command == "assert-quarantine":
        assert_quarantine(*sys.argv[2:6])
    elif command == "count-conversations":
        count_conversations(sys.argv[2], sys.argv[3])
    elif command == "assert-plan-revision":
        assert_plan_revision(sys.argv[2], int(sys.argv[3]))
    else:
        raise SystemExit("unknown PR-06B.4 E2E fixture assertion")


if __name__ == "__main__":
    main()
