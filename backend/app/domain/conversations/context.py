"""Server-owned, bounded Conversation context reconstruction (PR-06B.3A)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...agents.runtime_contract import RuntimeHistoryTurn
from ...db.models import ConversationMessage, ConversationMessageOption, ConversationSubmission
from . import service as lifecycle


class ContextError(lifecycle.ConversationError):
    """Content-free failure while constructing a durable runtime context."""


class ContextBudgetExceeded(ContextError):
    pass


class ContextStaleExecution(ContextError):
    pass


@dataclass(frozen=True)
class ExecutionIdentity:
    """Server-issued identity from an accepted Submission/Recovery attempt."""

    trip_id: str
    owner_membership_id: str
    conversation_id: str
    submission_id: str
    attempt_number: int
    fence: int


@dataclass(frozen=True)
class ContextBudget:
    """Conservative character accounting, not a tokenizer guarantee."""

    max_chars: int
    system_reserve_chars: int
    output_reserve_chars: int

    def __post_init__(self) -> None:
        if min(self.max_chars, self.system_reserve_chars, self.output_reserve_chars) < 0:
            raise ValueError("context budgets must be nonnegative")


@dataclass(frozen=True)
class PersistedOption:
    id: str
    ordinal: int
    title: str
    summary: str | None
    target_plan_item_id: str | None
    safe_patch: dict[str, object]
    plan_revision: int | None
    planning_facts_revision: int | None


@dataclass(frozen=True)
class DeterministicHistoryTurn:
    message_id: str
    sequence: int
    role: str
    text: str
    options: tuple[PersistedOption, ...]


@dataclass(frozen=True)
class ContextMetadata:
    included_turn_count: int
    excluded_turn_count: int
    truncated: bool
    budget_category: str


@dataclass(frozen=True)
class ConversationContext:
    """Detached common source for deterministic and runtime projections."""

    current_user_message: str
    selected_plan_item_id: str | None
    deterministic_history: tuple[DeterministicHistoryTurn, ...]
    runtime_history: tuple[RuntimeHistoryTurn, ...]
    metadata: ContextMetadata


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _turn_cost(message: ConversationMessage) -> int:
    # Conservative framing reserve for role serialization. Options deliberately
    # do not enter textual history or the model context budget.
    return len(message.content) + 16


def build_context(
    db: Session,
    *,
    identity: ExecutionIdentity,
    budget: ContextBudget,
    now: datetime,
) -> ConversationContext:
    """Return detached server-owned projections in one short transaction.

    Callers must finish/commit their transaction before invoking a Runtime. No
    caller text, candidate options, or client history is accepted here.
    """
    conversation = lifecycle._locked_owned_conversation(
        db,
        trip_id=identity.trip_id,
        membership_id=identity.owner_membership_id,
        conversation_id=identity.conversation_id,
        now=now,
    )
    submission = db.scalar(
        select(ConversationSubmission)
        .where(
            ConversationSubmission.id == identity.submission_id,
            ConversationSubmission.conversation_id == conversation.id,
            ConversationSubmission.trip_id == identity.trip_id,
            ConversationSubmission.owner_membership_id == identity.owner_membership_id,
        )
        .with_for_update()
    )
    if (
        submission is None
        or submission.state != "running"
        or submission.attempt_number != identity.attempt_number
        or submission.fence != identity.fence
        or submission.lease_expires_at is None
        or _aware(submission.lease_expires_at) <= now
    ):
        raise ContextStaleExecution("submission attempt is not current")
    current = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.id == submission.user_message_id,
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "user",
            ConversationMessage.state == "completed",
            ConversationMessage.deleted_at.is_(None),
        )
    )
    if current is None:
        raise ContextStaleExecution("original user message is unavailable")
    mandatory = budget.system_reserve_chars + budget.output_reserve_chars + _turn_cost(current)
    if mandatory > budget.max_chars:
        raise ContextBudgetExceeded("mandatory context exceeds configured budget")

    messages = list(
        db.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.id != current.id,
                ConversationMessage.sequence < current.sequence,
                ConversationMessage.state == "completed",
                ConversationMessage.deleted_at.is_(None),
            )
            .order_by(ConversationMessage.sequence.desc())
        ).all()
    )
    # A persisted User row is created at admission, before the corresponding
    # Submission finishes. Historical failed/running attempts are not valid
    # conversational turns and must not be replayed as if an Assistant had
    # answered them. The current running User row is handled separately above.
    historical_user_ids = [row.id for row in messages if row.role == "user"]
    user_submission_state = {
        row.user_message_id: row.state
        for row in db.scalars(
            select(ConversationSubmission).where(
                ConversationSubmission.conversation_id == conversation.id,
                ConversationSubmission.user_message_id.in_(historical_user_ids),
            )
        ).all()
    }
    remaining = budget.max_chars - mandatory
    selected_newest: list[ConversationMessage] = []
    excluded = 0
    for index, message in enumerate(messages):
        if message.role == "user" and user_submission_state.get(message.id) != "completed":
            excluded += 1
            continue
        cost = _turn_cost(message)
        if cost > remaining:
            # Keep a contiguous newest suffix. Once the next eligible turn
            # cannot fit, older text is less relevant and must not leapfrog it.
            excluded += len(messages) - index
            break
        selected_newest.append(message)
        remaining -= cost
    selected = list(reversed(selected_newest))
    deterministic: list[DeterministicHistoryTurn] = []
    for message in selected:
        option_rows = ()
        if message.role == "assistant":
            option_rows = tuple(
                db.scalars(
                    select(ConversationMessageOption)
                    .where(ConversationMessageOption.assistant_message_id == message.id)
                    .order_by(ConversationMessageOption.ordinal)
                ).all()
            )
        deterministic.append(
            DeterministicHistoryTurn(
                message_id=message.id,
                sequence=message.sequence,
                role=message.role,
                text=message.content,
                options=tuple(
                    PersistedOption(
                        id=row.id, ordinal=row.ordinal, title=row.title, summary=row.summary,
                        target_plan_item_id=row.target_plan_item_id, safe_patch=dict(row.safe_patch),
                        plan_revision=row.plan_revision, planning_facts_revision=row.planning_facts_revision,
                    )
                    for row in option_rows
                ),
            )
        )
    return ConversationContext(
        current_user_message=current.content,
        selected_plan_item_id=current.selected_plan_item_id,
        deterministic_history=tuple(deterministic),
        runtime_history=tuple(RuntimeHistoryTurn(role=turn.role, text=turn.text) for turn in deterministic),
        metadata=ContextMetadata(
            included_turn_count=len(deterministic),
            excluded_turn_count=excluded,
            truncated=excluded > 0,
            budget_category="within_budget" if excluded == 0 else "history_truncated",
        ),
    )
