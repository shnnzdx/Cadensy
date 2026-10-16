"""Owner-scoped durable Conversation lifecycle.

This module intentionally knows nothing about a model Provider.  It owns the
short database transactions before and after a future Provider invocation;
PR-06B.3 may call ``complete_submission`` only through this internal boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...db.models import (
    Conversation,
    ConversationMessage,
    ConversationMessageOption,
    ConversationSubmission,
    ConversationSubmissionRecovery,
    AuthSession,
    GuestSession,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
)
from ..membership_policy import is_membership_access_authorized
from .. import auth as auth_service


RETENTION = timedelta(days=180)
DELETE_PURGE_MAX = timedelta(days=30)
LEASE = timedelta(minutes=5)
OPERATION_VERSION = 1
HMAC_KEY_VERSION = "pr06b2-v1"
MAX_MESSAGE_CHARS = 12_000
MAX_OPTIONS = 20
SAFE_OPTION_PATCH_KEYS = frozenset({
    "title", "local_title", "place", "start_hour", "day_date", "duration_min",
    "price_per_person", "remove", "settledness", "lat", "lng", "photo_url", "tags",
})


class ConversationError(Exception):
    """Base, content-free lifecycle error."""


class ConversationUnavailable(ConversationError):
    """Do not disclose a private/deleted/expired conversation."""


class ConversationValidationError(ConversationError):
    pass


class ConversationBusy(ConversationError):
    pass


class IdempotencyConflict(ConversationError):
    pass


class StaleFence(ConversationError):
    pass


@dataclass(frozen=True)
class SubmissionResult:
    submission_id: str
    state: str
    attempt_number: int
    fence: int
    lease_expires_at: datetime | None
    replayed: bool
    assistant_message_id: str | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _hmac_key() -> bytes:
    """Obtain server-owned material without placing it in request data.

    A development-only deterministic fallback keeps the repository testable.
    Deployments must set the environment variable; production refuses the
    fallback so this module can never silently use a known key in production.
    """
    material = os.getenv("CONVERSATION_SUBMISSION_HMAC_KEY")
    if material and material.strip():
        return material.encode("utf-8", "strict")
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
        raise ConversationError("conversation signing material is unavailable")
    return b"cadensy-pr06b2-development-only-not-a-production-secret"


def _canonical_hmac(*, conversation: Conversation, text: str, selected_plan_item_id: str | None) -> str:
    payload = {
        "conversation_id": conversation.id,
        "context_key": conversation.context_key,
        "operation_version": OPERATION_VERSION,
        "owner_membership_id": conversation.owner_membership_id,
        "scope": conversation.scope,
        "selected_plan_item_id": selected_plan_item_id,
        "text": text,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(_hmac_key(), encoded, hashlib.sha256).hexdigest()


def _validate_identity(scope: str, context_key: str) -> None:
    if scope == "personal" and context_key == "personal":
        return
    if scope == "plan_assistant" and (context_key == "global" or _item_id_from_key(context_key)):
        return
    raise ConversationValidationError("invalid conversation scope or context key")


def _item_id_from_key(context_key: str) -> str | None:
    if len(context_key) == 37 and context_key.startswith("item:"):
        candidate = context_key[5:]
        if all(ch in "0123456789abcdef" for ch in candidate):
            return candidate
    return None


def _require_locked_actor(
    db: Session, *, trip_id: str, membership_id: str, bearer_token: str | None = None
) -> TripMembership:
    """Canonical lock prefix: Trip -> credential -> Membership -> child."""
    trip = db.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
    if trip is None:
        raise ConversationUnavailable()
    token_hash = auth_service.token_hash(bearer_token) if bearer_token else None
    account = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash).with_for_update()) if token_hash else None
    guest = None
    if token_hash and account is None:
        guest = db.scalar(select(GuestSession).where(GuestSession.token_hash == token_hash).with_for_update())
    membership = db.scalar(
        select(TripMembership)
        .where(TripMembership.id == membership_id, TripMembership.trip_id == trip_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if membership is None or not is_membership_access_authorized(membership):
        raise ConversationUnavailable()
    if bearer_token is not None:
        now = utcnow()
        if account is not None:
            valid = account.revoked_at is None and _aware(account.expires_at) > now and membership.user_id == account.user_id
        elif guest is not None:
            valid = (guest.revoked_at is None and _aware(guest.expires_at) > now and guest.trip_id == trip_id
                     and guest.membership_id == membership_id and membership.user_id is None)
        else:
            valid = False
        if not valid:
            raise ConversationUnavailable()
    return membership


def _expire_locked(conversation: Conversation, now: datetime) -> bool:
    expiry = conversation.retention_expires_at
    if conversation.status == "active" and expiry is not None and _aware(expiry) <= now:
        conversation.status = "expired"
        # Physical expiry is eligible immediately.  Authorization is already
        # fail-closed regardless of whether a worker subsequently runs.
        conversation.purge_due_at = now
        return True
    return conversation.status != "active" or (expiry is not None and _aware(expiry) <= now)


def _validate_trip_item(db: Session, *, trip_id: str, item_id: str | None) -> None:
    if item_id is None:
        return
    item = db.scalar(select(PlanItem).join(Plan).where(PlanItem.id == item_id, Plan.trip_id == trip_id))
    if item is None:
        raise ConversationValidationError("plan item does not belong to trip")


def _locked_owned_conversation(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, now: datetime,
    bearer_token: str | None = None
) -> Conversation:
    _require_locked_actor(db, trip_id=trip_id, membership_id=membership_id, bearer_token=bearer_token)
    conversation = db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.trip_id == trip_id,
            Conversation.owner_membership_id == membership_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if conversation is None or _expire_locked(conversation, now):
        raise ConversationUnavailable()
    return conversation


def create_or_discover(
    db: Session, *, trip_id: str, membership_id: str, scope: str, context_key: str, now: datetime | None = None,
    bearer_token: str | None = None
) -> Conversation:
    now = now or utcnow()
    _validate_identity(scope, context_key)
    _require_locked_actor(db, trip_id=trip_id, membership_id=membership_id, bearer_token=bearer_token)
    item_id = _item_id_from_key(context_key)
    _validate_trip_item(db, trip_id=trip_id, item_id=item_id)
    existing = db.scalar(
        select(Conversation)
        .where(Conversation.trip_id == trip_id, Conversation.owner_membership_id == membership_id,
               Conversation.scope == scope, Conversation.context_key == context_key, Conversation.status == "active")
        .execution_options(populate_existing=True).with_for_update()
    )
    if existing is not None and not _expire_locked(existing, now):
        return existing
    created = Conversation(trip_id=trip_id, owner_membership_id=membership_id, scope=scope, context_key=context_key,
                           status="active")
    db.add(created)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent creator won the partial natural identity.  The caller's
        # outer transaction stays usable via a savepoint in HTTP/service tests.
        raise ConversationBusy("concurrent creation must retry")
    return created


def list_conversations(
    db: Session, *, trip_id: str, membership_id: str, scope: str | None = None, context_key: str | None = None,
    now: datetime | None = None, bearer_token: str | None = None
) -> list[Conversation]:
    now = now or utcnow()
    # Read authorization is revalidated from current database state.  A lock
    # is unnecessary because no result survives a subsequent mutation txn.
    _require_locked_actor(db, trip_id=trip_id, membership_id=membership_id, bearer_token=bearer_token)
    statement = select(Conversation).where(Conversation.trip_id == trip_id, Conversation.owner_membership_id == membership_id,
                                             Conversation.status == "active")
    if scope is not None:
        statement = statement.where(Conversation.scope == scope)
    if context_key is not None:
        statement = statement.where(Conversation.context_key == context_key)
    rows = list(db.scalars(statement.order_by(Conversation.created_at.desc(), Conversation.id.desc())).all())
    return [row for row in rows if row.retention_expires_at is None or _aware(row.retention_expires_at) > now]


def get_conversation(db: Session, *, trip_id: str, membership_id: str, conversation_id: str, now: datetime | None = None,
                     bearer_token: str | None = None) -> Conversation:
    return _locked_owned_conversation(db, trip_id=trip_id, membership_id=membership_id,
                                      conversation_id=conversation_id, now=now or utcnow(), bearer_token=bearer_token)


def history(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, before: int | None, limit: int,
    now: datetime | None = None, bearer_token: str | None = None
) -> list[ConversationMessage]:
    conversation = get_conversation(db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
                                    now=now, bearer_token=bearer_token)
    statement = select(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id,
                                                   ConversationMessage.state == "completed", ConversationMessage.deleted_at.is_(None))
    if before is not None:
        statement = statement.where(ConversationMessage.sequence < before)
    rows = list(db.scalars(statement.order_by(ConversationMessage.sequence.desc()).limit(limit)).all())
    return list(reversed(rows))


def _next_sequence(db: Session, conversation_id: str) -> int:
    current = db.scalar(select(func.max(ConversationMessage.sequence)).where(ConversationMessage.conversation_id == conversation_id))
    return 0 if current is None else int(current) + 1


def submit_message(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, idempotency_key: str,
    text: str, selected_plan_item_id: str | None, now: datetime | None = None, bearer_token: str | None = None
) -> SubmissionResult:
    now = now or utcnow()
    text = text.strip()
    if not idempotency_key or len(idempotency_key) > 128 or not text or len(text) > MAX_MESSAGE_CHARS:
        raise ConversationValidationError("invalid submission")
    conversation = _locked_owned_conversation(db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
                                              now=now, bearer_token=bearer_token)
    _validate_trip_item(db, trip_id=trip_id, item_id=selected_plan_item_id)
    digest = _canonical_hmac(conversation=conversation, text=text, selected_plan_item_id=selected_plan_item_id)
    same_key = db.scalar(select(ConversationSubmission).where(ConversationSubmission.conversation_id == conversation.id,
                                                                ConversationSubmission.idempotency_key == idempotency_key).with_for_update())
    if same_key is not None:
        if not hmac.compare_digest(same_key.canonical_request_hmac, digest):
            raise IdempotencyConflict()
        # The original key is itself a durable recovery namespace.  It may
        # reclaim only its own expired lease and never creates another message.
        if (
            same_key.state == "running"
            and same_key.lease_expires_at is not None
            and _aware(same_key.lease_expires_at) <= now
        ):
            same_key.attempt_number += 1
            same_key.fence += 1
            same_key.lease_expires_at = now + LEASE
            db.flush()
        return _result(same_key, replayed=True)
    running = db.scalar(select(ConversationSubmission).where(ConversationSubmission.conversation_id == conversation.id,
                                                               ConversationSubmission.state == "running").with_for_update())
    if running is not None:
        raise ConversationBusy()
    user = ConversationMessage(conversation_id=conversation.id, sequence=_next_sequence(db, conversation.id), role="user",
                               content=text, state="completed", selected_plan_item_id=selected_plan_item_id)
    db.add(user)
    db.flush()
    submission = ConversationSubmission(conversation_id=conversation.id, trip_id=trip_id, owner_membership_id=membership_id,
                                        idempotency_key=idempotency_key, canonical_request_hmac=digest,
                                        fingerprint_version=OPERATION_VERSION, hmac_key_version=HMAC_KEY_VERSION,
                                        user_message_id=user.id, state="running", attempt_number=1, fence=1,
                                        lease_expires_at=now + LEASE, response_metadata={})
    db.add(submission)
    conversation.last_qualifying_activity_at = now
    conversation.retention_expires_at = now + RETENTION
    db.flush()
    return _result(submission, replayed=False)


def _result(submission: ConversationSubmission, *, replayed: bool) -> SubmissionResult:
    return SubmissionResult(submission.id, submission.state, submission.attempt_number, submission.fence,
                            submission.lease_expires_at, replayed, submission.assistant_message_id)


def recover_submission(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, submission_id: str, recovery_key: str,
    now: datetime | None = None, bearer_token: str | None = None
) -> SubmissionResult:
    now = now or utcnow()
    if not recovery_key or len(recovery_key) > 128:
        raise ConversationValidationError("invalid recovery key")
    conversation = _locked_owned_conversation(db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
                                              now=now, bearer_token=bearer_token)
    submission = db.scalar(select(ConversationSubmission).where(ConversationSubmission.id == submission_id,
                                                                   ConversationSubmission.conversation_id == conversation.id).with_for_update())
    if submission is None:
        raise ConversationUnavailable()
    recovery = db.scalar(select(ConversationSubmissionRecovery).where(ConversationSubmissionRecovery.submission_id == submission.id,
                                                                        ConversationSubmissionRecovery.recovery_key == recovery_key).with_for_update())
    if recovery is not None:
        return _result(submission, replayed=True)
    if submission.state != "running" or submission.lease_expires_at is None or _aware(submission.lease_expires_at) > now:
        raise ConversationBusy()
    submission.attempt_number += 1
    submission.fence += 1
    submission.lease_expires_at = now + LEASE
    db.add(ConversationSubmissionRecovery(submission_id=submission.id, recovery_key=recovery_key,
                                           operation_version=OPERATION_VERSION, accepted_attempt_number=submission.attempt_number,
                                           accepted_fence=submission.fence, state="running", accepted_at=now))
    db.flush()
    return _result(submission, replayed=False)


def complete_submission(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, submission_id: str,
    attempt_number: int, fence: int, content: str, options: Iterable[dict[str, Any]] = (), now: datetime | None = None
) -> SubmissionResult:
    """Internal-only result persistence; there is deliberately no HTTP route."""
    now = now or utcnow()
    content = content.strip()
    if not content or len(content) > MAX_MESSAGE_CHARS:
        raise ConversationValidationError("invalid assistant completion")
    safe_options = list(options)
    if len(safe_options) > MAX_OPTIONS or any(not isinstance(option, dict) for option in safe_options):
        raise ConversationValidationError("invalid option projection")
    conversation = _locked_owned_conversation(db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id, now=now)
    submission = db.scalar(select(ConversationSubmission).where(ConversationSubmission.id == submission_id,
                                                                   ConversationSubmission.conversation_id == conversation.id).with_for_update())
    if submission is None:
        raise ConversationUnavailable()
    if (submission.state != "running" or submission.lease_expires_at is None
            or _aware(submission.lease_expires_at) <= now
            or submission.attempt_number != attempt_number or submission.fence != fence):
        raise StaleFence()
    assistant = ConversationMessage(conversation_id=conversation.id, sequence=_next_sequence(db, conversation.id), role="assistant",
                                    content=content, state="completed")
    db.add(assistant)
    db.flush()
    for ordinal, option in enumerate(safe_options):
        title = option.get("title")
        patch = option.get("safe_patch", {})
        target = option.get("target_plan_item_id")
        if (not isinstance(title, str) or not title.strip() or not isinstance(patch, dict)
                or not set(patch).issubset(SAFE_OPTION_PATCH_KEYS)):
            raise ConversationValidationError("invalid option projection")
        _validate_trip_item(db, trip_id=trip_id, item_id=target)
        db.add(ConversationMessageOption(assistant_message_id=assistant.id, ordinal=ordinal, title=title.strip(),
                                         summary=option.get("summary") if isinstance(option.get("summary"), str) else None,
                                         safe_patch=patch, target_plan_item_id=target,
                                         plan_revision=option.get("plan_revision"),
                                         planning_facts_revision=option.get("planning_facts_revision")))
    submission.assistant_message_id = assistant.id
    submission.state = "completed"
    submission.lease_expires_at = None
    submission.failure_category = None
    db.query(ConversationSubmissionRecovery).filter(
        ConversationSubmissionRecovery.submission_id == submission.id,
        ConversationSubmissionRecovery.accepted_attempt_number == attempt_number,
        ConversationSubmissionRecovery.accepted_fence == fence,
        ConversationSubmissionRecovery.state == "running",
    ).update({ConversationSubmissionRecovery.state: "completed"}, synchronize_session=False)
    conversation.last_qualifying_activity_at = now
    conversation.retention_expires_at = now + RETENTION
    db.flush()
    return _result(submission, replayed=False)


def fail_submission(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, submission_id: str,
    attempt_number: int, fence: int, category: str, now: datetime | None = None
) -> SubmissionResult:
    """Internal categorical failure persistence; never stores provider text."""
    now = now or utcnow()
    if not category or len(category) > 64 or not category.replace("_", "").isalnum():
        raise ConversationValidationError("invalid failure category")
    conversation = _locked_owned_conversation(db, trip_id=trip_id, membership_id=membership_id,
                                              conversation_id=conversation_id, now=now)
    submission = db.scalar(select(ConversationSubmission).where(
        ConversationSubmission.id == submission_id, ConversationSubmission.conversation_id == conversation.id
    ).with_for_update())
    if submission is None:
        raise ConversationUnavailable()
    if submission.state != "running" or submission.attempt_number != attempt_number or submission.fence != fence:
        raise StaleFence()
    submission.state = "failed"
    submission.lease_expires_at = None
    submission.failure_category = category
    db.query(ConversationSubmissionRecovery).filter(
        ConversationSubmissionRecovery.submission_id == submission.id,
        ConversationSubmissionRecovery.accepted_attempt_number == attempt_number,
        ConversationSubmissionRecovery.accepted_fence == fence,
        ConversationSubmissionRecovery.state == "running",
    ).update({ConversationSubmissionRecovery.state: "failed"}, synchronize_session=False)
    db.flush()
    return _result(submission, replayed=False)


def delete_conversation(db: Session, *, trip_id: str, membership_id: str, conversation_id: str, now: datetime | None = None,
                        bearer_token: str | None = None) -> None:
    now = now or utcnow()
    conversation = _locked_owned_conversation(db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
                                              now=now, bearer_token=bearer_token)
    conversation.status = "deleted"
    conversation.deleted_at = now
    conversation.purge_due_at = now + DELETE_PURGE_MAX
    # A running execution cannot be allowed to complete after deletion.  It is
    # terminally failed without revealing a provider payload.
    db.query(ConversationSubmission).filter(ConversationSubmission.conversation_id == conversation.id,
                                             ConversationSubmission.state == "running").update(
        {ConversationSubmission.state: "failed", ConversationSubmission.lease_expires_at: None,
         ConversationSubmission.failure_category: "conversation_deleted"}, synchronize_session=False)
    db.flush()
