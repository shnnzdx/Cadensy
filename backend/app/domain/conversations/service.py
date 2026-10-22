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
from ..decisions import orchestrator as decision_orchestrator


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
    # A recovery replay never becomes authority for a newer parent attempt.
    # These immutable fields expose the recovery operation's own identity while
    # attempt_number/fence above continue to describe current parent state.
    recovery_accepted_attempt_number: int | None = None
    recovery_accepted_fence: int | None = None
    recovery_state: str | None = None
    # Internal-only admission fact for the request-bound Runtime bridge.  It
    # is never true for an active idempotency replay or a status read.
    execution_claimed: bool = False


@dataclass(frozen=True)
class ConversationOptionProjection:
    """A persisted assistant option belonging to an owner-authorized message."""

    id: str
    ordinal: int
    title: str
    summary: str | None
    target_plan_item_id: str | None
    safe_patch: dict[str, Any]
    plan_revision: int | None
    planning_facts_revision: int | None


@dataclass(frozen=True)
class ConversationHistoryEntry:
    """Owner-authorized durable history and its non-secret status projection."""

    id: str
    sequence: int
    role: str
    content: str
    created_at: datetime
    selected_plan_item_id: str | None
    options: tuple[ConversationOptionProjection, ...]
    submission: SubmissionResult | None


@dataclass(frozen=True)
class HistoricalOptionResolution:
    """Fresh, owner-scoped result for one persisted assistant option."""

    ready: bool
    code: str
    item_id: str | None = None
    item_title: str | None = None
    patch: dict[str, Any] | None = None
    classification: Any | None = None


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


def production_signing_material_ready() -> bool:
    """Report only presence of production HMAC material, never its value."""
    material = os.getenv("CONVERSATION_SUBMISSION_HMAC_KEY")
    return bool(material and material.strip())


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


def _validated_completion_options(
    db: Session, *, trip_id: str, options: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate all safe projections before any Assistant row is added.

    The returned detached dictionaries are safe to persist only after every
    candidate has passed.  This protects callers that catch validation errors
    without rolling their Session back: no orphan assistant row can exist.
    """
    result = list(options)
    if len(result) > MAX_OPTIONS or any(not isinstance(option, dict) for option in result):
        raise ConversationValidationError("invalid option projection")
    normalized: list[dict[str, Any]] = []
    for option in result:
        title = option.get("title")
        summary = option.get("summary")
        patch = option.get("safe_patch", {})
        target = option.get("target_plan_item_id")
        plan_revision = option.get("plan_revision")
        facts_revision = option.get("planning_facts_revision")
        if (
            not isinstance(title, str) or not title.strip() or len(title.strip()) > 300
            or summary is not None and (not isinstance(summary, str) or len(summary) > 4_000)
            or not isinstance(patch, dict) or not set(patch).issubset(SAFE_OPTION_PATCH_KEYS)
            or target is not None and (not isinstance(target, str) or len(target) > 32)
            or plan_revision is not None and (isinstance(plan_revision, bool) or not isinstance(plan_revision, int) or plan_revision < 0)
            or facts_revision is not None and (isinstance(facts_revision, bool) or not isinstance(facts_revision, int) or facts_revision < 0)
        ):
            raise ConversationValidationError("invalid option projection")
        try:
            json.dumps(patch, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ConversationValidationError("invalid option projection") from exc
        _validate_trip_item(db, trip_id=trip_id, item_id=target)
        normalized.append({
            "title": title.strip(), "summary": summary, "safe_patch": patch,
            "target_plan_item_id": target, "plan_revision": plan_revision,
            "planning_facts_revision": facts_revision,
        })
    return normalized


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


def history_projection(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, before: int | None, limit: int,
    now: datetime | None = None, bearer_token: str | None = None,
) -> list[ConversationHistoryEntry]:
    """Read durable history without allowing an API caller to widen owner scope."""
    rows = history(
        db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
        before=before, limit=limit, now=now, bearer_token=bearer_token,
    )
    user_ids = [row.id for row in rows if row.role == "user"]
    submissions_by_user: dict[str, ConversationSubmission] = {}
    if user_ids:
        submissions_by_user = {
            submission.user_message_id: submission
            for submission in db.scalars(
                select(ConversationSubmission).where(
                    ConversationSubmission.conversation_id == conversation_id,
                    ConversationSubmission.trip_id == trip_id,
                    ConversationSubmission.owner_membership_id == membership_id,
                    ConversationSubmission.user_message_id.in_(user_ids),
                )
            ).all()
        }
    assistant_ids = [row.id for row in rows if row.role == "assistant"]
    options_by_message: dict[str, list[ConversationOptionProjection]] = {}
    if assistant_ids:
        for option in db.scalars(
            select(ConversationMessageOption)
            .where(ConversationMessageOption.assistant_message_id.in_(assistant_ids))
            .order_by(ConversationMessageOption.assistant_message_id, ConversationMessageOption.ordinal)
        ).all():
            options_by_message.setdefault(option.assistant_message_id, []).append(
                ConversationOptionProjection(
                    id=option.id,
                    ordinal=option.ordinal,
                    title=option.title,
                    summary=option.summary,
                    target_plan_item_id=option.target_plan_item_id,
                    safe_patch=option.safe_patch,
                    plan_revision=option.plan_revision,
                    planning_facts_revision=option.planning_facts_revision,
                )
            )
    return [
        ConversationHistoryEntry(
            id=row.id,
            sequence=row.sequence,
            role=row.role,
            content=row.content,
            created_at=row.created_at,
            selected_plan_item_id=row.selected_plan_item_id,
            options=tuple(options_by_message.get(row.id, ())),
            submission=_result(submissions_by_user[row.id], replayed=True)
            if row.role == "user" and row.id in submissions_by_user else None,
        )
        for row in rows
    ]


def get_submission(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, submission_id: str,
    now: datetime | None = None, bearer_token: str | None = None,
) -> SubmissionResult:
    """Return one owner-scoped Submission status, or the same unavailable result."""
    conversation = get_conversation(
        db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
        now=now, bearer_token=bearer_token,
    )
    submission = db.scalar(
        select(ConversationSubmission).where(
            ConversationSubmission.id == submission_id,
            ConversationSubmission.conversation_id == conversation.id,
            ConversationSubmission.owner_membership_id == membership_id,
            ConversationSubmission.trip_id == trip_id,
        )
    )
    if submission is None:
        raise ConversationUnavailable()
    return _result(submission, replayed=True)


def resolve_historical_option(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, assistant_message_id: str,
    option_id: str, now: datetime | None = None, bearer_token: str | None = None,
) -> HistoricalOptionResolution:
    """Resolve one persisted option through current ownership and planning facts.

    A browser-supplied option projection is never authoritative.  This boundary
    reloads the parent Conversation, Assistant Message, and Option together,
    then rejects stale, deleted, wrong-scope, no-op, and no-longer-current
    suggestions before returning a fresh deterministic classification.
    """
    conversation = _locked_owned_conversation(
        db, trip_id=trip_id, membership_id=membership_id, conversation_id=conversation_id,
        now=now or utcnow(), bearer_token=bearer_token,
    )
    assistant = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == assistant_message_id,
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.state == "completed",
            ConversationMessage.deleted_at.is_(None),
        )
    )
    if assistant is None:
        raise ConversationUnavailable()
    option = db.scalar(
        select(ConversationMessageOption).where(
            ConversationMessageOption.id == option_id,
            ConversationMessageOption.assistant_message_id == assistant.id,
        )
    )
    if option is None:
        raise ConversationUnavailable()
    if conversation.scope != "plan_assistant":
        return HistoricalOptionResolution(False, "historical_option_scope")
    context_item_id = _item_id_from_key(conversation.context_key)
    if context_item_id is not None and option.target_plan_item_id != context_item_id:
        return HistoricalOptionResolution(False, "historical_option_scope")
    try:
        normalized = _validated_completion_options(
            db,
            trip_id=trip_id,
            options=[{
                "title": option.title,
                "summary": option.summary,
                "safe_patch": option.safe_patch,
                "target_plan_item_id": option.target_plan_item_id,
                "plan_revision": option.plan_revision,
                "planning_facts_revision": option.planning_facts_revision,
            }],
        )[0]
    except ConversationValidationError:
        return HistoricalOptionResolution(False, "historical_option_invalid")
    patch = normalized["safe_patch"]
    target_item_id = normalized["target_plan_item_id"]
    if target_item_id is None or not patch:
        return HistoricalOptionResolution(False, "historical_option_noop")
    trip = db.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
    plans = list(db.scalars(select(Plan).where(Plan.trip_id == trip_id).with_for_update()).all())
    if trip is None or len(plans) != 1:
        return HistoricalOptionResolution(False, "historical_option_unavailable")
    plan = plans[0]
    if (
        option.plan_revision is None
        or option.planning_facts_revision is None
        or option.plan_revision != plan.revision
        or option.planning_facts_revision != trip.planning_facts_revision
    ):
        return HistoricalOptionResolution(False, "historical_option_stale")
    item = db.scalar(
        select(PlanItem).where(
            PlanItem.id == target_item_id,
            PlanItem.plan_id == plan.id,
            PlanItem.settledness != "removed",
        )
    )
    if item is None:
        return HistoricalOptionResolution(False, "historical_option_unavailable")
    try:
        # Classifying is intentionally read-only and therefore does not impose
        # the writer's pending-decision guard itself.  A historical option is
        # different: the UI may expose Apply after this call, so fail closed at
        # selection time rather than deferring the conflict to a later write.
        decision_orchestrator.require_no_pending_decision(db, item)
        classification = decision_orchestrator.classify_change(db, item, patch, membership_id)
    except decision_orchestrator.AlreadyPending:
        return HistoricalOptionResolution(False, "historical_option_conflict")
    return HistoricalOptionResolution(
        True,
        "historical_option_ready",
        item_id=item.id,
        item_title=item.title,
        patch=patch,
        classification=classification,
    )


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
        execution_claimed = (
            same_key.state == "running"
            and same_key.lease_expires_at is not None
            and _aware(same_key.lease_expires_at) <= now
        )
        if execution_claimed:
            same_key.attempt_number += 1
            same_key.fence += 1
            same_key.lease_expires_at = now + LEASE
            db.flush()
        return _result(same_key, replayed=True, execution_claimed=execution_claimed)
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
    return _result(submission, replayed=False, execution_claimed=True)


def _result(
    submission: ConversationSubmission, *, replayed: bool,
    recovery: ConversationSubmissionRecovery | None = None,
    execution_claimed: bool = False,
) -> SubmissionResult:
    return SubmissionResult(submission.id, submission.state, submission.attempt_number, submission.fence,
                            submission.lease_expires_at, replayed, submission.assistant_message_id,
                            recovery.accepted_attempt_number if recovery is not None else None,
                            recovery.accepted_fence if recovery is not None else None,
                            recovery.state if recovery is not None else None,
                            execution_claimed)


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
        return _result(submission, replayed=True, recovery=recovery)
    if submission.state != "running" or submission.lease_expires_at is None or _aware(submission.lease_expires_at) > now:
        raise ConversationBusy()
    # The older recovery's external attempt is no longer current once a newer
    # fenced attempt is granted.  Its immutable accepted fence remains audit
    # evidence; ``failed`` means its execution authority expired/revoked, not
    # that the original user submission was replaced or failed.
    db.query(ConversationSubmissionRecovery).filter(
        ConversationSubmissionRecovery.submission_id == submission.id,
        ConversationSubmissionRecovery.state == "running",
    ).update({ConversationSubmissionRecovery.state: "failed"}, synchronize_session=False)
    submission.attempt_number += 1
    submission.fence += 1
    submission.lease_expires_at = now + LEASE
    accepted_recovery = ConversationSubmissionRecovery(
        submission_id=submission.id, recovery_key=recovery_key,
        operation_version=OPERATION_VERSION, accepted_attempt_number=submission.attempt_number,
        accepted_fence=submission.fence, state="running", accepted_at=now,
    )
    db.add(accepted_recovery)
    db.flush()
    return _result(submission, replayed=False, recovery=accepted_recovery, execution_claimed=True)


def complete_submission(
    db: Session, *, trip_id: str, membership_id: str, conversation_id: str, submission_id: str,
    attempt_number: int, fence: int, content: str, options: Iterable[dict[str, Any]] = (), now: datetime | None = None
) -> SubmissionResult:
    """Internal-only result persistence; there is deliberately no HTTP route."""
    now = now or utcnow()
    content = content.strip()
    if not content or len(content) > MAX_MESSAGE_CHARS:
        raise ConversationValidationError("invalid assistant completion")
    # Must finish all input validation before the first persistent Assistant
    # write. See _validated_completion_options for rollback-independent safety.
    safe_options = _validated_completion_options(db, trip_id=trip_id, options=options)
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
        db.add(ConversationMessageOption(assistant_message_id=assistant.id, ordinal=ordinal, title=option["title"],
                                         summary=option["summary"], safe_patch=option["safe_patch"],
                                         target_plan_item_id=option["target_plan_item_id"],
                                         plan_revision=option["plan_revision"],
                                         planning_facts_revision=option["planning_facts_revision"]))
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
    if (submission.state != "running" or submission.lease_expires_at is None
            or _aware(submission.lease_expires_at) <= now
            or submission.attempt_number != attempt_number or submission.fence != fence):
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
