"""PR-06B.3B durable Runtime coordinator contracts."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.agents.execution import AgentExecutionConfig
from app.agents.runtime_contract import (
    AgentCandidateOption,
    AgentReplyOnly,
    AgentSuggestedChange,
    RuntimeObservation,
    RuntimeResult,
)
from app.db.models import ConversationMessage, ConversationMessageOption
from app.domain.conversations import context, service
from app.domain.conversations.runtime import ConversationRuntimeCoordinator


def _running_submission(db: Session, trip_setup: dict):
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
                                              scope="personal", context_key="personal", now=now)
    first = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
                                   conversation_id=conversation.id, idempotency_key="first", text="first user turn",
                                   selected_plan_item_id=None, now=now)
    service.complete_submission(db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
                                conversation_id=conversation.id, submission_id=first.submission_id,
                                attempt_number=1, fence=1, content="first assistant turn", now=now + timedelta(seconds=1))
    current = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
                                     conversation_id=conversation.id, idempotency_key="second", text="current user turn",
                                     selected_plan_item_id=None, now=now + timedelta(seconds=2))
    return owner, conversation, current, now + timedelta(seconds=2)


def _identity(trip_setup, owner, conversation, submission):
    return context.ExecutionIdentity(trip_setup["trip"].id, owner.id, conversation.id,
                                     submission.submission_id, submission.attempt_number, submission.fence)


def _factory(db: Session):
    return sessionmaker(bind=db.connection(), future=True, join_transaction_mode="create_savepoint")


class _ReplyingRuntime:
    def __init__(self) -> None:
        self.requests = []
        self.executions = []

    def run(self, request, capability, *, execution):
        self.requests.append(request)
        self.executions.append(execution)
        return RuntimeResult("durably completed", AgentReplyOnly(), (), RuntimeObservation(None, 1, 1, 1.0))


class _CrashingRuntime:
    def run(self, request, capability, *, execution):
        raise RuntimeError("provider details must not persist")


class _SuggestedChangeRuntime:
    def __init__(
        self,
        *,
        item_id: str,
        patch: dict[str, object],
        candidate_options: tuple[AgentCandidateOption, ...] = (),
    ) -> None:
        self.item_id = item_id
        self.patch = patch
        self.candidate_options = candidate_options

    def run(self, request, capability, *, execution):
        return RuntimeResult(
            "I prepared a safe itinerary suggestion for review.",
            AgentSuggestedChange(item_ref=self.item_id, safe_patch=self.patch),
            self.candidate_options,
            RuntimeObservation(None, 1, 1, 1.0),
        )


class _CandidateOptionsRuntime:
    def __init__(self, candidate_options: tuple[AgentCandidateOption, ...]) -> None:
        self.candidate_options = candidate_options

    def run(self, request, capability, *, execution):
        return RuntimeResult(
            "Here are safe options for review.",
            AgentReplyOnly(),
            self.candidate_options,
            RuntimeObservation(None, 1, 1, 1.0),
        )


def _candidate_option(
    *, option_id: str, item_id: str, patch: dict[str, object], title: str = "Later start",
) -> AgentCandidateOption:
    return AgentCandidateOption(
        id=option_id,
        label=option_id,
        title=title,
        body="A reviewed itinerary alternative.",
        tradeoff="Keeps the rest of the itinerary unchanged.",
        item_ref=item_id,
        safe_patch=patch,
    )


def _coordinator(db, runtime_factory):
    return ConversationRuntimeCoordinator(
        session_factory=_factory(db), runtime_factory=runtime_factory,
        execution=AgentExecutionConfig(30.0, 20.0, 5.0), budget=context.ContextBudget(200, 20, 20),
    )


def _plan_assistant_submission(db: Session, trip_setup: dict):
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        scope="plan_assistant",
        context_key=f'item:{trip_setup["item"].id}',
        now=now,
    )
    submission = service.submit_message(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        idempotency_key="direct-candidate",
        text="Could this item start at 3 PM?",
        selected_plan_item_id=trip_setup["item"].id,
        now=now,
    )
    return owner, conversation, submission, now


def test_runtime_coordinator_persists_only_fenced_completion_and_server_history(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission(db, trip_setup)
    fake = _ReplyingRuntime()
    result = _coordinator(db, lambda: fake).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)
    assert result.state == "completed"
    assert [(turn.role, turn.text) for turn in fake.requests[0].history] == [("user", "first user turn"), ("assistant", "first assistant turn")]
    assert fake.requests[0].message == "current user turn"
    assert fake.requests[0].request_id == submission.submission_id
    assert fake.executions[0].telemetry_context.request_id == submission.submission_id
    assert vars(fake.executions[0].telemetry_context) == {
        "request_id": submission.submission_id,
        "trace_id": None,
        "execution_id": None,
    }
    history = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )
    assert all(not message.options for message in history)


def test_runtime_coordinator_persists_direct_candidate_as_durable_option_without_mutating_plan(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    plan = trip_setup["plan"]
    item = trip_setup["item"]
    captured_plan_revision = plan.revision
    captured_facts_revision = trip_setup["trip"].planning_facts_revision
    original_item = (item.start_hour, item.day_date, item.title, item.place)

    result = _coordinator(
        db,
        lambda: _SuggestedChangeRuntime(item_id=item.id, patch={"start_hour": 15.0}),
    ).execute(
        identity=_identity(trip_setup, owner, conversation, submission),
        now=now,
    )

    history = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )
    assistant = next(message for message in history if message.role == "assistant")

    assert result.state == "completed"
    assert len(assistant.options) == 1
    option = assistant.options[0]
    assert option.target_plan_item_id == item.id
    assert option.safe_patch == {"start_hour": 15.0}
    assert option.plan_revision == captured_plan_revision
    assert option.planning_facts_revision == captured_facts_revision
    assert 0 < len(option.title) <= 300

    db.refresh(plan)
    db.refresh(item)
    assert plan.revision == captured_plan_revision
    assert (item.start_hour, item.day_date, item.title, item.place) == original_item


def test_runtime_coordinator_deduplicates_direct_candidate_against_existing_options(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    item = trip_setup["item"]
    duplicate = _candidate_option(
        option_id="duplicate", item_id=item.id, patch={"start_hour": 15},
    )
    distinct = _candidate_option(
        option_id="distinct",
        item_id=item.id,
        patch={"start_hour": 16.0},
        title="Even later start",
    )

    result = _coordinator(
        db,
        lambda: _SuggestedChangeRuntime(
            item_id=item.id,
            patch={"start_hour": 15.0},
            candidate_options=(duplicate, distinct),
        ),
    ).execute(
        identity=_identity(trip_setup, owner, conversation, submission),
        now=now,
    )

    assistant = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )[-1]
    assert result.state == "completed"
    assert [option.safe_patch for option in assistant.options] == [
        {"start_hour": 15.0},
        {"start_hour": 16.0},
    ]
    assert assistant.options[0].title.startswith("Suggested change for ")


def test_runtime_coordinator_normalizes_direct_candidate_date_for_json_persistence(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    item = trip_setup["item"]

    result = _coordinator(
        db,
        lambda: _SuggestedChangeRuntime(
            item_id=item.id,
            patch={"day_date": date(2026, 8, 16)},
        ),
    ).execute(
        identity=_identity(trip_setup, owner, conversation, submission),
        now=now,
    )

    assistant = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )[-1]
    assert result.state == "completed"
    assert assistant.options[0].safe_patch == {"day_date": "2026-08-16"}


def test_runtime_coordinator_preserves_existing_candidate_options_path(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    item = trip_setup["item"]
    existing = _candidate_option(
        option_id="existing", item_id=item.id, patch={"start_hour": 15.0},
    )

    result = _coordinator(
        db, lambda: _CandidateOptionsRuntime((existing,)),
    ).execute(
        identity=_identity(trip_setup, owner, conversation, submission),
        now=now,
    )

    assistant = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )[-1]
    assert result.state == "completed"
    assert len(assistant.options) == 1
    assert assistant.options[0].title == existing.title
    assert assistant.options[0].summary == existing.body
    assert assistant.options[0].safe_patch == existing.safe_patch


def test_runtime_coordinator_omits_unsafe_option_metadata_without_failing_completion(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    unsafe = _candidate_option(
        option_id="unsafe-title",
        item_id=trip_setup["item"].id,
        patch={"start_hour": 15.0},
        title="x" * 301,
    )

    result = _coordinator(
        db, lambda: _CandidateOptionsRuntime((unsafe,)),
    ).execute(
        identity=_identity(trip_setup, owner, conversation, submission),
        now=now,
    )

    assistant = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )[-1]
    assert result.state == "completed"
    assert assistant.options == ()


def test_runtime_coordinator_applies_deterministic_max_options_after_merge(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    item = trip_setup["item"]
    existing = tuple(
        _candidate_option(
            option_id=f"option-{index}",
            item_id=item.id,
            patch={"duration_min": index + 1},
            title=f"Duration option {index + 1}",
        )
        for index in range(service.MAX_OPTIONS)
    )

    result = _coordinator(
        db,
        lambda: _SuggestedChangeRuntime(
            item_id=item.id,
            patch={"start_hour": 15.0},
            candidate_options=existing,
        ),
    ).execute(
        identity=_identity(trip_setup, owner, conversation, submission),
        now=now,
    )

    assistant = service.history_projection(
        db,
        trip_id=trip_setup["trip"].id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        before=None,
        limit=20,
        now=now,
    )[-1]
    assert result.state == "completed"
    assert len(assistant.options) == service.MAX_OPTIONS
    assert assistant.options[0].safe_patch == {"start_hour": 15.0}
    assert assistant.options[-1].safe_patch == {"duration_min": 19}


def test_runtime_exception_becomes_redacted_fenced_failure(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission(db, trip_setup)
    result = _coordinator(db, _CrashingRuntime).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)
    assert (result.state, result.failure_category) == ("failed", "unexpected_exception")


def test_membership_revocation_during_runtime_discards_uncommitted_reply(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    item = trip_setup["item"]

    class _RevokingRuntime(_SuggestedChangeRuntime):
        def run(self, request, capability, *, execution):
            owner.status = "removed"
            db.flush()
            return super().run(request, capability, execution=execution)

    result = _coordinator(
        db,
        lambda: _RevokingRuntime(item_id=item.id, patch={"start_hour": 15.0}),
    ).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)
    assert result.state == "discarded"
    assert db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation.id,
                                                ConversationMessage.role == "assistant").count() == 0
    assert db.query(ConversationMessageOption).count() == 0


def test_conversation_deletion_during_runtime_discards_uncommitted_candidate_option(
    db: Session, trip_setup: dict,
):
    owner, conversation, submission, now = _plan_assistant_submission(db, trip_setup)
    item = trip_setup["item"]

    class _DeletingRuntime(_SuggestedChangeRuntime):
        def run(self, request, capability, *, execution):
            service.delete_conversation(
                db,
                trip_id=trip_setup["trip"].id,
                membership_id=owner.id,
                conversation_id=conversation.id,
                now=now,
            )
            db.flush()
            return super().run(request, capability, execution=execution)

    result = _coordinator(
        db,
        lambda: _DeletingRuntime(item_id=item.id, patch={"start_hour": 15.0}),
    ).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)

    assert result.state == "discarded"
    assert db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == "assistant",
    ).count() == 0
    assert db.query(ConversationMessageOption).count() == 0
