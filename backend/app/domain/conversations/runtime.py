"""Short-transaction bridge from durable Submissions to the approved Runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
import math

from sqlalchemy.orm import Session

from ...agents.execution import AgentExecutionConfig
from ...agents.runtime_contract import ChatAgentRuntime, RuntimeLimits
from ...observability.telemetry import TelemetryContext
from ..preview_apply import (
    PreviewAccessDenied,
    PreviewDuplicatePlans,
    PreviewItemNotFound,
    PreviewMissingPlan,
    PreviewRequest,
    PreviewResult,
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


_DURABLE_PREVIEW_PATCH_KEYS = frozenset({
    "title",
    "place",
    "start_hour",
    "day_date",
    "duration_min",
    "price_per_person",
    "lat",
    "lng",
})
_DURABLE_OPTION_TITLE_MAX = 300
_DURABLE_OPTION_SUMMARY_MAX = 4_000


def _durable_preview_patch(raw: dict[str, object]) -> dict[str, object] | None:
    """Convert only Preview-approved domain values to JSON-compatible data."""

    if not set(raw).issubset(_DURABLE_PREVIEW_PATCH_KEYS):
        return None
    normalized: dict[str, object] = {}
    for field, value in raw.items():
        if field in {"title", "place"}:
            if not isinstance(value, str) or not value.strip():
                return None
            normalized[field] = value
        elif field == "day_date":
            if isinstance(value, datetime):
                return None
            if isinstance(value, date):
                normalized[field] = value.isoformat()
            elif isinstance(value, str):
                try:
                    normalized[field] = date.fromisoformat(value).isoformat()
                except ValueError:
                    return None
            else:
                return None
        elif field == "duration_min":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return None
            normalized[field] = value
        else:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                return None
            normalized[field] = value
    return normalized


def _direct_candidate_title(item_title: str) -> str:
    safe_item_title = " ".join(item_title.split())
    if not safe_item_title:
        return "Suggested itinerary change"
    return f"Suggested change for {safe_item_title}"[:_DURABLE_OPTION_TITLE_MAX]


def _durable_preview_options(preview: PreviewResult) -> tuple[dict[str, object], ...]:
    """Merge safe Preview candidates into the existing durable option DTO."""

    candidates: list[tuple[str, str | None, str, dict[str, object], bool]] = []
    if preview.candidate is not None:
        candidates.append((
            _direct_candidate_title(preview.candidate.item_title),
            None,
            preview.candidate.item_id,
            preview.candidate.patch,
            True,
        ))
    candidates.extend(
        (option.title, option.body or None, option.item_id, option.patch, False)
        for option in preview.candidate_options
    )

    durable: list[dict[str, object]] = []
    seen: set[tuple[str, tuple[tuple[str, object], ...]]] = set()
    for title, summary, item_id, raw_patch, direct in candidates:
        if len(durable) >= service.MAX_OPTIONS:
            break
        patch = _durable_preview_patch(raw_patch)
        if patch is None or (direct and not patch):
            continue
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > _DURABLE_OPTION_TITLE_MAX
            or (summary is not None and (
                not isinstance(summary, str) or len(summary) > _DURABLE_OPTION_SUMMARY_MAX
            ))
            or not isinstance(item_id, str)
            or not item_id
        ):
            continue
        signature = (item_id, tuple(sorted(patch.items())))
        if signature in seen:
            continue
        seen.add(signature)
        durable.append({
            "title": title,
            "summary": summary,
            "target_plan_item_id": item_id,
            "safe_patch": patch,
            "plan_revision": preview.plan_revision,
            "planning_facts_revision": preview.planning_facts_revision,
        })
    return tuple(durable)


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
        runtime_limits: RuntimeLimits | None = None,
    ) -> None:
        self._sessions = session_factory
        self._runtime_factory = runtime_factory
        self._execution = execution
        self._budget = budget
        self._runtime_limits = runtime_limits

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
                runtime_limits=self._runtime_limits,
            ).preview(
                PreviewRequest(
                    trip_id=identity.trip_id,
                    actor_membership_id=identity.owner_membership_id,
                    message=built.current_user_message,
                    selected_item_id=built.selected_plan_item_id,
                    history=built.runtime_history,
                    # Submission ids are opaque server-issued identifiers. They
                    # correlate the existing safe Runtime events without adding
                    # message/history/tool-result data to telemetry.
                    telemetry_context=TelemetryContext(request_id=identity.submission_id),
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

        options = _durable_preview_options(preview)
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
