"""PR-06B.3A server-owned context adapter contracts."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.domain.conversations import context, service


def _running_submission_with_completed_turn(db: Session, trip_setup: dict):
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        scope="personal", context_key="personal", now=now,
    )
    first = service.submit_message(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, idempotency_key="first", text="first user turn",
        selected_plan_item_id=None, now=now,
    )
    service.complete_submission(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, submission_id=first.submission_id,
        attempt_number=1, fence=1, content="first assistant turn", now=now + timedelta(seconds=1),
    )
    second = service.submit_message(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, idempotency_key="second", text="current user turn",
        selected_plan_item_id=None, now=now + timedelta(seconds=2),
    )
    return owner, conversation, second, now + timedelta(seconds=2)


def test_context_is_server_owned_chronological_and_current_message_once(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission_with_completed_turn(db, trip_setup)
    built = context.build_context(
        db,
        identity=context.ExecutionIdentity(
            trip_id=trip_setup["trip"].id,
            owner_membership_id=owner.id,
            conversation_id=conversation.id,
            submission_id=submission.submission_id,
            attempt_number=1,
            fence=1,
        ),
        budget=context.ContextBudget(max_chars=200, system_reserve_chars=20, output_reserve_chars=20),
        now=now,
    )
    assert [(turn.role, turn.text) for turn in built.runtime_history] == [
        ("user", "first user turn"), ("assistant", "first assistant turn"),
    ]
    assert built.current_user_message == "current user turn"
    assert sum(turn.text == "current user turn" for turn in built.runtime_history) == 0
    assert built.metadata.included_turn_count == 2
    assert built.metadata.excluded_turn_count == 0


def test_context_fails_instead_of_dropping_mandatory_current_input(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission_with_completed_turn(db, trip_setup)
    with pytest.raises(context.ContextBudgetExceeded):
        context.build_context(
            db,
            identity=context.ExecutionIdentity(
                trip_id=trip_setup["trip"].id, owner_membership_id=owner.id,
                conversation_id=conversation.id, submission_id=submission.submission_id,
                attempt_number=1, fence=1,
            ),
            budget=context.ContextBudget(max_chars=10, system_reserve_chars=5, output_reserve_chars=5),
            now=now,
        )
