"""PR-06B.3B durable Runtime coordinator contracts."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.agents.execution import AgentExecutionConfig
from app.agents.runtime_contract import AgentReplyOnly, RuntimeObservation, RuntimeResult
from app.db.models import ConversationMessage
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

    def run(self, request, capability, *, execution):
        self.requests.append(request)
        return RuntimeResult("durably completed", AgentReplyOnly(), (), RuntimeObservation(None, 1, 1, 1.0))


class _CrashingRuntime:
    def run(self, request, capability, *, execution):
        raise RuntimeError("provider details must not persist")


def _coordinator(db, runtime_factory):
    return ConversationRuntimeCoordinator(
        session_factory=_factory(db), runtime_factory=runtime_factory,
        execution=AgentExecutionConfig(30.0, 20.0, 5.0), budget=context.ContextBudget(200, 20, 20),
    )


def test_runtime_coordinator_persists_only_fenced_completion_and_server_history(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission(db, trip_setup)
    fake = _ReplyingRuntime()
    result = _coordinator(db, lambda: fake).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)
    assert result.state == "completed"
    assert [(turn.role, turn.text) for turn in fake.requests[0].history] == [("user", "first user turn"), ("assistant", "first assistant turn")]
    assert fake.requests[0].message == "current user turn"


def test_runtime_exception_becomes_redacted_fenced_failure(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission(db, trip_setup)
    result = _coordinator(db, _CrashingRuntime).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)
    assert (result.state, result.failure_category) == ("failed", "unexpected_exception")


def test_membership_revocation_during_runtime_discards_uncommitted_reply(db: Session, trip_setup: dict):
    owner, conversation, submission, now = _running_submission(db, trip_setup)

    class _RevokingRuntime(_ReplyingRuntime):
        def run(self, request, capability, *, execution):
            owner.status = "removed"
            db.flush()
            return super().run(request, capability, execution=execution)

    result = _coordinator(db, _RevokingRuntime).execute(identity=_identity(trip_setup, owner, conversation, submission), now=now)
    assert result.state == "discarded"
    assert db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation.id,
                                                ConversationMessage.role == "assistant").count() == 1
