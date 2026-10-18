"""Bounded, content-free physical purge for PR-06B conversations.

This is an explicit job entry point, not a Provider queue.  Scheduling remains
an operational deployment decision; the existing scheduler can opt in through
an environment gate after an owner/leader policy is configured.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db.models import (
    Conversation,
    ConversationMessage,
    ConversationMessageOption,
    ConversationSubmission,
    ConversationSubmissionRecovery,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _expire_due_locked(db: Session, *, now: datetime, limit: int) -> int:
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.status == "active", Conversation.retention_expires_at.is_not(None),
               Conversation.retention_expires_at <= now)
        .order_by(Conversation.retention_expires_at, Conversation.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        row.status = "expired"
        row.purge_due_at = now
    db.flush()
    return len(rows)


def _purge_one_locked(db: Session, conversation: Conversation) -> None:
    """Delete children before their root; never touches Membership or Trip."""
    message_ids = select(ConversationMessage.id).where(ConversationMessage.conversation_id == conversation.id)
    submission_ids = select(ConversationSubmission.id).where(ConversationSubmission.conversation_id == conversation.id)
    db.execute(delete(ConversationSubmissionRecovery).where(ConversationSubmissionRecovery.submission_id.in_(submission_ids)))
    db.execute(delete(ConversationMessageOption).where(ConversationMessageOption.assistant_message_id.in_(message_ids)))
    db.execute(delete(ConversationSubmission).where(ConversationSubmission.conversation_id == conversation.id))
    db.execute(delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id))
    db.delete(conversation)
    db.flush()


def run_once(db: Session, *, now: datetime | None = None, batch_size: int = 100) -> dict[str, int]:
    """Expire then purge at most ``batch_size`` roots under row locks.

    Counters intentionally contain no identifiers or message content.  A
    concurrent job uses ``SKIP LOCKED``; retrying after a crash is idempotent
    because only rows still due are selected.
    """
    if batch_size < 1 or batch_size > 1_000:
        raise ValueError("batch_size must be between 1 and 1000")
    now = now or utcnow()
    expired = _expire_due_locked(db, now=now, limit=batch_size)
    overdue = int(db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.purge_due_at.is_not(None), Conversation.purge_due_at < now
    )) or 0)
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.purge_due_at.is_not(None), Conversation.purge_due_at <= now,
               Conversation.status.in_(("deleted", "expired")))
        .order_by(Conversation.purge_due_at, Conversation.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    for conversation in rows:
        _purge_one_locked(db, conversation)
    return {"expired": expired, "purged": len(rows), "overdue": overdue}
