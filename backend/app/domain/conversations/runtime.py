"""Short-transaction bridge from durable Submissions to the approved Runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ...agents.execution import AgentExecutionConfig
from ...agents.runtime_contract import ChatAgentRuntime
from ..preview_apply import (
    PreviewAccessDenied,
    PreviewDuplicatePlans,
    PreviewItemNotFound,
    PreviewMissingPlan,
    PreviewRequest,
    PreviewTripNotFound,
    RevisionBracketedPreview,
    StalePreview,
)
from . import context, service


@dataclass(frozen=True)
class RuntimeExecutionResult:
    state: str
    submission_id: str
    failure_category: str | None = None


class ConversationRuntimeCoordinator:
    """Connect one fenced Submission to the existing protected Preview seam.

    The coordinator accepts only a server-issued ``ExecutionIdentity``. Its
    three phases never share ORM state: context admission, Runtime/Preview,
    and fenced durable completion each use a separate short transaction.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        runtime_factory: Callable[[], ChatAgentRuntime],
        execution: AgentExecutionConfig,
        budget: context.ContextBudget,
    ) -> None:
        self._sessions = session_factory
        self._runtime_factory = runtime_factory
        self._execution = execution
        self._budget = budget

    def execute(self, *, identity: context.ExecutionIdentity, now: datetime) -> RuntimeExecutionResult:
        # Phase 1: establish the sole owner-authorized history source. The
        # transaction is closed before a Runtime or Provider can be entered.
        try:
            with self._sessions.begin() as db:
                built = context.build_context(db, identity=identity, budget=self._budget, now=now)
        except context.ContextBudgetExceeded:
            return self._fail_or_discard(identity, category="context_budget_exceeded")
        except context.ContextError:
            # Stale/revoked/deleted authority is not revived merely to record a
            # failure; no Runtime call was made.
            return RuntimeExecutionResult("discarded", identity.submission_id, "context_unavailable")

        # Phase 2: RevisionBracketedPreview opens only fresh bounded sessions
        # for scoped read tools, classification, and final revision checking.
        # It receives the exact same text projection as the durable Runtime;
        # persisted options are therefore advisory Preview outputs, never raw
        # model patches or a new writer path.
        try:
            preview = RevisionBracketedPreview(
                session_factory=self._sessions,
                runtime=self._runtime_factory(),
                execution=self._execution,
            ).preview(
                PreviewRequest(
                    trip_id=identity.trip_id,
                    actor_membership_id=identity.owner_membership_id,
                    message=built.current_user_message,
                    selected_item_id=built.selected_plan_item_id,
                    history=built.runtime_history,
                )
            )
        except (PreviewAccessDenied, PreviewTripNotFound):
            return self._fail_or_discard(identity, category="membership_revoked")
        except (StalePreview, PreviewMissingPlan, PreviewDuplicatePlans, PreviewItemNotFound):
            return self._fail_or_discard(identity, category="stale_context")
        except Exception:
            return self._fail_or_discard(identity, category="unexpected_exception")

        if preview.runtime_failure_category is not None:
            return self._fail_or_discard(identity, category=preview.runtime_failure_category)

        options = tuple(
            {
                "title": option.title,
                "summary": option.body or None,
                "target_plan_item_id": option.item_id,
                "safe_patch": dict(option.patch),
                "plan_revision": preview.plan_revision,
                "planning_facts_revision": preview.planning_facts_revision,
            }
            for option in preview.candidate_options
        )
        # Phase 3: completion rechecks active ownership and the current fence.
        # A revision that changes after Preview produces a historical advisory
        # option with its captured revision; it cannot Apply anything directly.
        try:
            with self._sessions.begin() as db:
                completed = service.complete_submission(
                    db,
                    trip_id=identity.trip_id,
                    membership_id=identity.owner_membership_id,
                    conversation_id=identity.conversation_id,
                    submission_id=identity.submission_id,
                    attempt_number=identity.attempt_number,
                    fence=identity.fence,
                    content=preview.reply,
                    options=options,
                    now=service.utcnow(),
                )
                return RuntimeExecutionResult(completed.state, completed.submission_id)
        except service.ConversationError:
            # Deletion, revocation, expiry, or newer recovery wins. The output
            # is discarded and never claimed as a durable assistant response.
            return RuntimeExecutionResult("discarded", identity.submission_id, "completion_unavailable")

    def _fail_or_discard(
        self, identity: context.ExecutionIdentity, *, category: str
    ) -> RuntimeExecutionResult:
        try:
            with self._sessions.begin() as db:
                failed = service.fail_submission(
                    db,
                    trip_id=identity.trip_id,
                    membership_id=identity.owner_membership_id,
                    conversation_id=identity.conversation_id,
                    submission_id=identity.submission_id,
                    attempt_number=identity.attempt_number,
                    fence=identity.fence,
                    category=category,
                    now=service.utcnow(),
                )
                return RuntimeExecutionResult(failed.state, failed.submission_id, category)
        except service.ConversationError:
            return RuntimeExecutionResult("discarded", identity.submission_id, category)
