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


def _execution_identity(trip_setup: dict, owner, conversation, submission):
    return context.ExecutionIdentity(
        trip_id=trip_setup["trip"].id,
        owner_membership_id=owner.id,
        conversation_id=conversation.id,
        submission_id=submission.submission_id,
        attempt_number=submission.attempt_number,
        fence=submission.fence,
    )


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


def test_context_excludes_a_previous_failed_submission_from_history(db: Session, trip_setup: dict):
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        scope="personal", context_key="personal", now=now,
    )
    failed = service.submit_message(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, idempotency_key="failed", text="do not replay",
        selected_plan_item_id=None, now=now,
    )
    service.fail_submission(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, submission_id=failed.submission_id,
        attempt_number=failed.attempt_number, fence=failed.fence,
        category="provider_timeout", now=now + timedelta(seconds=1),
    )
    current = service.submit_message(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, idempotency_key="current", text="new request",
        selected_plan_item_id=None, now=now + timedelta(seconds=2),
    )

    built = context.build_context(
        db,
        identity=_execution_identity(trip_setup, owner, conversation, current),
        budget=context.ContextBudget(200, 20, 20), now=now + timedelta(seconds=2),
    )

    assert built.runtime_history == ()
    assert built.metadata.excluded_turn_count == 1


def test_context_rejects_another_membership_even_if_organizer(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission_with_completed_turn(db, trip_setup)
    other = trip_setup["participant"]
    other.status = "joined"
    with pytest.raises(service.ConversationUnavailable):
        context.build_context(
            db,
            identity=context.ExecutionIdentity(
                trip_id=trip_setup["trip"].id, owner_membership_id=other.id,
                conversation_id=conversation.id, submission_id=submission.submission_id,
                attempt_number=submission.attempt_number, fence=submission.fence,
            ),
            budget=context.ContextBudget(200, 20, 20), now=now,
        )
