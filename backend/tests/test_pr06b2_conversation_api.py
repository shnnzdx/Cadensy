"""PR-06B.2A/B owner-scoped durable Conversation HTTP/service tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.execution import AgentExecutionConfig
from app.agents.runtime_contract import (
    AgentSuggestedChange,
    RuntimeObservation,
    RuntimeResult,
)
from app.api import main as api
from app.db.models import (
    ChangeProposal,
    Conversation,
    ConversationMessage,
    ConversationMessageOption,
    ConversationSubmission,
    DecisionRound,
    Plan,
    TripMembership,
    User,
)
from app.domain import auth
from app.domain.conversations import context, service
from app.domain.conversations.runtime import ConversationRuntimeCoordinator
from app.jobs import conversation_purge


@contextmanager
def _client(db: Session):
    api.app.dependency_overrides[api.get_session] = lambda: db
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def _headers(client: TestClient, db: Session, membership: TripMembership, trip_id: str) -> dict[str, str]:
    user = db.get(User, membership.user_id)
    assert user is not None
    membership.status = "joined"
    user.email = f"conversation-{membership.id}@example.test"
    user.password_hash = auth.hash_password("correct-horse")
    db.flush()
    response = client.post("/api/auth/login", json={"email": user.email, "password": "correct-horse"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}", "X-Trip-Id": trip_id}


class _DirectCandidateRuntime:
    def __init__(self, *, item_id: str, patch: dict[str, object]) -> None:
        self.item_id = item_id
        self.patch = patch

    def run(self, request, capability, *, execution):
        return RuntimeResult(
            "I prepared a safe itinerary suggestion for review.",
            AgentSuggestedChange(item_ref=self.item_id, safe_patch=self.patch),
            (),
            RuntimeObservation(None, 1, 1, 1.0),
        )


def _runtime_coordinator(db: Session, runtime) -> ConversationRuntimeCoordinator:
    return ConversationRuntimeCoordinator(
        session_factory=sessionmaker(
            bind=db.connection(), future=True, join_transaction_mode="create_savepoint",
        ),
        runtime_factory=lambda: runtime,
        execution=AgentExecutionConfig(30.0, 20.0, 5.0),
        budget=context.ContextBudget(200, 20, 20),
    )


def test_owner_scoped_creation_history_and_idempotent_submission(db: Session, trip_setup: dict):
    trip = trip_setup["trip"]
    trip_id = trip.id
    with _client(db) as client:
        headers = _headers(client, db, trip_setup["organizer"], trip_id)
        created = client.post(f"/api/trips/{trip_id}/conversations", headers=headers,
                              json={"scope": "personal", "context_key": "personal"})
        duplicate = client.post(f"/api/trips/{trip_id}/conversations", headers=headers,
                                json={"scope": "personal", "context_key": "personal"})
        assert created.status_code == duplicate.status_code == 201
        conversation_id = created.json()["id"]
        assert duplicate.json()["id"] == conversation_id

        accepted = client.post(f"/api/trips/{trip_id}/conversations/{conversation_id}/messages", headers={**headers, "Idempotency-Key": "first"}, json={"text": "Keep this private"})
        replay = client.post(f"/api/trips/{trip_id}/conversations/{conversation_id}/messages", headers={**headers, "Idempotency-Key": "first"}, json={"text": "Keep this private"})
        conflict = client.post(f"/api/trips/{trip_id}/conversations/{conversation_id}/messages", headers={**headers, "Idempotency-Key": "first"}, json={"text": "Different"})
        assert accepted.status_code == replay.status_code == 202
        assert replay.json()["replayed"] is True
        assert conflict.status_code == 409 and conflict.json()["detail"] == "idempotency_key_conflict"


def test_history_recovers_owner_pending_submission_after_refresh_without_disclosing_it_to_another_member(
    db: Session, trip_setup: dict
):
    """A new browser runtime can discover a pending owner Submission safely."""
    trip_id = trip_setup["trip"].id
    with _client(db) as client:
        owner_headers = _headers(client, db, trip_setup["organizer"], trip_id)
        other_headers = _headers(client, db, trip_setup["participant"], trip_id)
        created = client.post(
            f"/api/trips/{trip_id}/conversations", headers=owner_headers,
            json={"scope": "personal", "context_key": "personal"},
        )
        assert created.status_code == 201
        # Default-off Runtime means a browser must not offer a send that can
        # remain indefinitely pending without an explicitly enabled local gate.
        assert created.json()["runtime_available"] is False
        conversation_id = created.json()["id"]
        accepted = client.post(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages",
            headers={**owner_headers, "Idempotency-Key": "refresh-pending"}, json={"text": "saved once"},
        )
        assert accepted.status_code == 202

        history = client.get(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages", headers=owner_headers,
        )
        assert history.status_code == 200
        [message] = history.json()["messages"]
        assert message["content"] == "saved once"
        assert message["submission"]["submission_id"] == accepted.json()["submission_id"]
        assert message["submission"]["state"] == "running"
        assert message["submission"]["attempt_number"] == 1
        assert message["submission"]["assistant_message_id"] is None
        assert datetime.fromisoformat(message["submission"]["lease_expires_at"]).timestamp() == pytest.approx(
            datetime.fromisoformat(accepted.json()["lease_expires_at"]).timestamp()
        )

        # Simulate the original browser disappearing after admission. A later
        # Account session discovers the server ID above, waits for expiry, and
        # recovers without submitting replacement user text.
        submission = db.get(ConversationSubmission, accepted.json()["submission_id"])
        assert submission is not None
        submission.lease_expires_at = service.utcnow() - timedelta(seconds=1)
        db.commit()
        recovered = client.post(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/submissions/{submission.id}/recover",
            headers={**owner_headers, "Idempotency-Key": "refresh-recovery"},
        )
        assert recovered.status_code == 202
        assert recovered.json()["attempt_number"] == 2
        assert recovered.json()["replayed"] is False
        service.complete_submission(
            db, trip_id=trip_id, membership_id=trip_setup["organizer"].id,
            conversation_id=conversation_id, submission_id=submission.id,
            attempt_number=2, fence=2, content="one durable answer",
        )
        db.commit()
        completed_history = client.get(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages", headers=owner_headers,
        )
        assert [(row["role"], row["content"]) for row in completed_history.json()["messages"]] == [
            ("user", "saved once"), ("assistant", "one durable answer"),
        ]

        denied = client.get(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages", headers=other_headers,
        )
        assert denied.status_code == 404


def test_historical_option_resolution_rechecks_owner_parent_and_captured_revisions(db: Session, trip_setup: dict):
    """A displayed option is usable only through its persisted, current server parent."""
    trip = trip_setup["trip"]
    plan = trip_setup["plan"]
    item = trip_setup["item"]
    owner = trip_setup["organizer"]
    now = service.utcnow()
    owner.status = "joined"
    conversation = service.create_or_discover(
        db, trip_id=trip.id, membership_id=owner.id, scope="plan_assistant", context_key="global", now=now,
    )
    accepted = service.submit_message(
        db, trip_id=trip.id, membership_id=owner.id, conversation_id=conversation.id,
        idempotency_key="stored-option", text="Move it later", selected_plan_item_id=item.id, now=now,
    )
    completed = service.complete_submission(
        db, trip_id=trip.id, membership_id=owner.id, conversation_id=conversation.id,
        submission_id=accepted.submission_id, attempt_number=accepted.attempt_number, fence=accepted.fence,
        content="Here is a safe alternative.",
        options=({
            "title": "Move it later", "summary": "Avoids the morning.",
            "safe_patch": {"start_hour": 15.0}, "target_plan_item_id": item.id,
            "plan_revision": plan.revision, "planning_facts_revision": trip.planning_facts_revision,
        },),
        now=now + timedelta(seconds=1),
    )
    assert completed.assistant_message_id is not None
    assistant = db.get(ConversationMessage, completed.assistant_message_id)
    assert assistant is not None
    option = db.scalar(select(ConversationMessageOption).where(
        ConversationMessageOption.assistant_message_id == assistant.id,
    ))
    assert option is not None
    trip_id = trip.id
    owner_id = owner.id
    participant_id = trip_setup["participant"].id
    conversation_id = conversation.id
    assistant_id = assistant.id
    option_id = option.id
    item_id = item.id
    plan_id = plan.id
    db.commit()

    with _client(db) as client:
        owner_headers = _headers(client, db, db.get(TripMembership, owner_id), trip_id)
        path = (
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages/{assistant_id}"
            f"/options/{option_id}/resolve"
        )
        resolved = client.post(path, headers=owner_headers)
        assert resolved.status_code == 200
        assert resolved.json()["ready"] is True
        assert resolved.json()["item_id"] == item_id
        assert resolved.json()["patch"] == {"start_hour": 15.0}

        # A resolved stored suggestion must not invite an Apply while the
        # target is already in either kind of unfinished group decision.
        round_ = DecisionRound(
            plan_item_id=item_id,
            kind="normal",
            options=[],
            deadline=now + timedelta(hours=1),
            status="open",
            electorate_count=2,
        )
        db.add(round_)
        db.commit()
        round_conflict = client.post(path, headers=owner_headers)
        assert round_conflict.status_code == 200
        assert round_conflict.json() == {
            "ready": False,
            "code": "historical_option_conflict",
            "note": "That option conflicts with a current pending decision.",
        }
        db.delete(round_)
        db.commit()

        proposal = ChangeProposal(
            plan_item_id=item_id,
            action_type="edit",
            before_json={},
            after_json={},
            requested_by_membership_id=owner_id,
            status="waiting_affected_members",
            deadline=now + timedelta(hours=1),
        )
        db.add(proposal)
        db.commit()
        proposal_conflict = client.post(path, headers=owner_headers)
        assert proposal_conflict.status_code == 200
        assert proposal_conflict.json() == {
            "ready": False,
            "code": "historical_option_conflict",
            "note": "That option conflicts with a current pending decision.",
        }
        db.delete(proposal)
        db.commit()

        db.get(Plan, plan_id).revision += 1
        db.commit()
        stale = client.post(path, headers=owner_headers)
        assert stale.status_code == 200
        assert stale.json() == {
            "ready": False,
            "code": "historical_option_stale",
            "note": "That suggestion is out of date. Please ask Cadensy for a current option.",
        }

    with pytest.raises(service.ConversationUnavailable):
        service.resolve_historical_option(
            db, trip_id=trip_id, membership_id=participant_id, conversation_id=conversation_id,
            assistant_message_id=assistant_id, option_id=option_id,
        )
    with pytest.raises(service.ConversationUnavailable):
        service.resolve_historical_option(
            db, trip_id="f" * 32, membership_id=owner_id, conversation_id=conversation_id,
            assistant_message_id=assistant_id, option_id=option_id,
        )
    with pytest.raises(service.ConversationUnavailable):
        service.resolve_historical_option(
            db, trip_id=trip_id, membership_id=owner_id, conversation_id=conversation_id,
            assistant_message_id=assistant_id, option_id="f" * 32,
        )
    service.delete_conversation(
        db, trip_id=trip_id, membership_id=owner_id, conversation_id=conversation_id,
    )
    with pytest.raises(service.ConversationUnavailable):
        service.resolve_historical_option(
            db, trip_id=trip_id, membership_id=owner_id, conversation_id=conversation_id,
            assistant_message_id=assistant_id, option_id=option_id,
        )


def test_direct_candidate_is_returned_by_history_and_requires_historical_resolve(
    db: Session, trip_setup: dict,
):
    trip = trip_setup["trip"]
    plan = trip_setup["plan"]
    item = trip_setup["item"]
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(
        db,
        trip_id=trip.id,
        membership_id=owner.id,
        scope="plan_assistant",
        context_key=f"item:{item.id}",
        now=now,
    )
    accepted = service.submit_message(
        db,
        trip_id=trip.id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        idempotency_key="direct-history",
        text="Could this item start at 3 PM?",
        selected_plan_item_id=item.id,
        now=now,
    )
    result = _runtime_coordinator(
        db, _DirectCandidateRuntime(item_id=item.id, patch={"start_hour": 15.0}),
    ).execute(
        identity=context.ExecutionIdentity(
            trip.id,
            owner.id,
            conversation.id,
            accepted.submission_id,
            accepted.attempt_number,
            accepted.fence,
        ),
        now=now,
    )
    assert result.state == "completed"
    trip_id = trip.id
    owner_id = owner.id
    conversation_id = conversation.id
    item_id = item.id
    original_plan_revision = plan.revision
    original_start_hour = item.start_hour
    db.commit()

    with _client(db) as client:
        headers = _headers(client, db, db.get(TripMembership, owner_id), trip_id)
        history = client.get(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages",
            headers=headers,
        )
        assert history.status_code == 200
        assistant = next(
            message for message in history.json()["messages"] if message["role"] == "assistant"
        )
        [option] = assistant["options"]
        assert option["target_plan_item_id"] == item_id
        assert option["safe_patch"] == {"start_hour": 15.0}
        assert option["plan_revision"] == original_plan_revision
        assert option["planning_facts_revision"] == trip_setup["trip"].planning_facts_revision

        resolved = client.post(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages/{assistant['id']}"
            f"/options/{option['id']}/resolve",
            headers=headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["ready"] is True
        assert resolved.json()["item_id"] == item_id
        assert resolved.json()["patch"] == {"start_hour": 15.0}

    db.refresh(plan)
    db.refresh(item)
    assert plan.revision == original_plan_revision
    assert item.start_hour == original_start_hour


def test_personal_conversation_option_cannot_gain_apply_authority(
    db: Session, trip_setup: dict,
):
    trip = trip_setup["trip"]
    plan = trip_setup["plan"]
    item = trip_setup["item"]
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(
        db,
        trip_id=trip.id,
        membership_id=owner.id,
        scope="personal",
        context_key="personal",
        now=now,
    )
    accepted = service.submit_message(
        db,
        trip_id=trip.id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        idempotency_key="personal-option",
        text="Keep this personal.",
        selected_plan_item_id=item.id,
        now=now,
    )
    completed = service.complete_submission(
        db,
        trip_id=trip.id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        submission_id=accepted.submission_id,
        attempt_number=accepted.attempt_number,
        fence=accepted.fence,
        content="A private suggestion.",
        options=({
            "title": "Private suggestion",
            "summary": None,
            "safe_patch": {"start_hour": 15.0},
            "target_plan_item_id": item.id,
            "plan_revision": plan.revision,
            "planning_facts_revision": trip.planning_facts_revision,
        },),
        now=now,
    )
    option = db.scalar(select(ConversationMessageOption).where(
        ConversationMessageOption.assistant_message_id == completed.assistant_message_id,
    ))
    assert option is not None

    resolution = service.resolve_historical_option(
        db,
        trip_id=trip.id,
        membership_id=owner.id,
        conversation_id=conversation.id,
        assistant_message_id=completed.assistant_message_id,
        option_id=option.id,
        now=now,
    )
    assert resolution.ready is False
    assert resolution.code == "historical_option_scope"


def test_request_bound_runtime_runs_once_for_a_new_claim_not_a_replay(
    db: Session, trip_setup: dict, monkeypatch: pytest.MonkeyPatch
):
    """PR-06B.3 keeps 202 admission semantics while preventing double runs."""
    calls = []
    item = trip_setup["item"]
    plan = trip_setup["plan"]
    trip = trip_setup["trip"]

    class _Coordinator:
        def execute(self, *, identity, now):
            calls.append(identity)
            service.complete_submission(
                db,
                trip_id=identity.trip_id,
                membership_id=identity.owner_membership_id,
                conversation_id=identity.conversation_id,
                submission_id=identity.submission_id,
                attempt_number=identity.attempt_number,
                fence=identity.fence,
                content="one durable reply",
                options=({
                    "title": "One durable option",
                    "summary": None,
                    "safe_patch": {"start_hour": 15.0},
                    "target_plan_item_id": item.id,
                    "plan_revision": plan.revision,
                    "planning_facts_revision": trip.planning_facts_revision,
                },),
                now=now,
            )
            db.commit()

    monkeypatch.setattr(api, "_durable_conversation_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        api.chat_service, "build_durable_conversation_runtime_coordinator",
        lambda *, session_factory: _Coordinator(),
    )
    trip_id = trip_setup["trip"].id
    with _client(db) as client:
        headers = _headers(client, db, trip_setup["organizer"], trip_id)
        created = client.post(
            f"/api/trips/{trip_id}/conversations", headers=headers,
            json={"scope": "personal", "context_key": "personal"},
        )
        conversation_id = created.json()["id"]
        accepted = client.post(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages",
            headers={**headers, "Idempotency-Key": "runtime-once"},
            json={"text": "hello", "selected_plan_item_id": item.id},
        )
        replay = client.post(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages",
            headers={**headers, "Idempotency-Key": "runtime-once"},
            json={"text": "hello", "selected_plan_item_id": item.id},
        )

    assert accepted.status_code == replay.status_code == 202
    assert len(calls) == 1
    assert calls[0].submission_id == accepted.json()["submission_id"]
    assert db.query(ConversationSubmission).filter(
        ConversationSubmission.conversation_id == conversation_id,
    ).count() == 1
    assert db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id,
    ).count() == 2
    assert db.query(ConversationMessageOption).count() == 1


def test_owner_isolation_and_internal_recovery_fence_expiry(db: Session, trip_setup: dict):
    trip = trip_setup["trip"]
    trip_id = trip.id
    with _client(db) as client:
        owner_headers = _headers(client, db, trip_setup["organizer"], trip_id)
        other_headers = _headers(client, db, trip_setup["participant"], trip_id)
        created = client.post(f"/api/trips/{trip_id}/conversations", headers=owner_headers,
                              json={"scope": "plan_assistant", "context_key": "global"})
        conversation_id = created.json()["id"]
        denied = client.get(f"/api/trips/{trip_id}/conversations/{conversation_id}", headers=other_headers)
        assert denied.status_code == 404


def test_internal_recovery_fence_and_expiry_fail_closed(db: Session, trip_setup: dict):
    organizer = trip_setup["organizer"]
    trip = trip_setup["trip"]
    plan = trip_setup["plan"]
    item = trip_setup["item"]
    organizer.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                              scope="plan_assistant", context_key="global", now=now)
    submission = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                        conversation_id=conversation.id, idempotency_key="fence", text="Suggest", selected_plan_item_id=None, now=now)
    assert [(message.sequence, message.content) for message in service.history(
        db, trip_id=trip_setup["trip"].id, membership_id=organizer.id, conversation_id=conversation.id,
        before=None, limit=50, now=now
    )] == [(0, "Suggest")]
    service.get_conversation(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                             conversation_id=conversation.id).retention_expires_at = now + timedelta(days=1)
    with pytest.raises(service.ConversationBusy):
        service.recover_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                   conversation_id=conversation.id, submission_id=submission.submission_id,
                                   recovery_key="too-early", now=now + timedelta(minutes=1))
    option = {
        "title": "Late option",
        "summary": None,
        "safe_patch": {"start_hour": 15.0},
        "target_plan_item_id": item.id,
        "plan_revision": plan.revision,
        "planning_facts_revision": trip.planning_facts_revision,
    }
    with pytest.raises(service.StaleFence):
        service.complete_submission(
            db, trip_id=trip.id, membership_id=organizer.id,
            conversation_id=conversation.id, submission_id=submission.submission_id,
            attempt_number=1, fence=1, content="expired", options=(option,),
            now=now + timedelta(minutes=6),
        )
    assert db.query(ConversationMessageOption).count() == 0
    recovered = service.recover_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                           conversation_id=conversation.id, submission_id=submission.submission_id,
                                           recovery_key="recovery", now=now + timedelta(minutes=6))
    assert recovered.fence == 2 and recovered.attempt_number == 2
    with pytest.raises(service.StaleFence):
        service.complete_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                    conversation_id=conversation.id, submission_id=submission.submission_id,
                                    attempt_number=1, fence=1, content="late", options=(option,),
                                    now=now + timedelta(minutes=6))
    assert db.query(ConversationMessageOption).count() == 0
    completed = service.complete_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                            conversation_id=conversation.id, submission_id=submission.submission_id,
                                            attempt_number=2, fence=2, content="safe", now=now + timedelta(minutes=6))
    assert completed.state == "completed"
    conversation.last_qualifying_activity_at = now - timedelta(days=180, seconds=1)
    conversation.retention_expires_at = now - timedelta(seconds=1)
    with pytest.raises(service.ConversationUnavailable):
        service.get_conversation(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                 conversation_id=conversation.id, now=now)


def test_purge_deletes_private_children_but_not_membership_history(db: Session, trip_setup: dict):
    organizer = trip_setup["organizer"]
    organizer.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                              scope="personal", context_key="personal", now=now)
    accepted = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                      conversation_id=conversation.id, idempotency_key="purge", text="remove", selected_plan_item_id=None, now=now)
    service.delete_conversation(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                conversation_id=conversation.id, now=now)
    report = conversation_purge.run_once(db, now=now + timedelta(days=30), batch_size=10)
    assert report["purged"] == 1
    assert db.get(Conversation, conversation.id) is None
    assert db.get(ConversationSubmission, accepted.submission_id) is None
    assert db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation.id).count() == 0
    assert db.get(TripMembership, organizer.id) is not None


def test_recovery_replay_keeps_its_accepted_fence_after_newer_reclaim(db: Session, trip_setup: dict):
    organizer = trip_setup["organizer"]
    organizer.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                              scope="personal", context_key="personal", now=now)
    original = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                      conversation_id=conversation.id, idempotency_key="original", text="one",
                                      selected_plan_item_id=None, now=now)
    recovery_a = service.recover_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                             conversation_id=conversation.id, submission_id=original.submission_id,
                                             recovery_key="A", now=now + timedelta(minutes=6))
    recovery_b = service.recover_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                             conversation_id=conversation.id, submission_id=original.submission_id,
                                             recovery_key="B", now=now + timedelta(minutes=12))
    replay_a = service.recover_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                           conversation_id=conversation.id, submission_id=original.submission_id,
                                           recovery_key="A", now=now + timedelta(minutes=13))
    assert recovery_a.fence == 2 and recovery_b.fence == 3
    assert replay_a.recovery_accepted_fence == 2
    assert replay_a.recovery_accepted_attempt_number == 2
    assert replay_a.fence == 3


def test_completion_validates_all_options_before_writing_assistant_message(db: Session, trip_setup: dict):
    organizer = trip_setup["organizer"]
    organizer.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                              scope="personal", context_key="personal", now=now)
    accepted = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                      conversation_id=conversation.id, idempotency_key="completion", text="one",
                                      selected_plan_item_id=None, now=now)
    with pytest.raises(service.ConversationValidationError):
        service.complete_submission(
            db, trip_id=trip_setup["trip"].id, membership_id=organizer.id, conversation_id=conversation.id,
            submission_id=accepted.submission_id, attempt_number=1, fence=1, content="answer",
            options=({"title": "good", "safe_patch": {}}, {"title": "bad", "safe_patch": {"forbidden": 1}}),
            now=now + timedelta(minutes=1),
        )
    assert db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation.id, ConversationMessage.role == "assistant"
    ).count() == 0


def test_failure_requires_live_lease_and_does_not_refresh_retention(db: Session, trip_setup: dict):
    organizer = trip_setup["organizer"]
    organizer.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                              scope="personal", context_key="personal", now=now)
    accepted = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                      conversation_id=conversation.id, idempotency_key="failure", text="one",
                                      selected_plan_item_id=None, now=now)
    retained_until = conversation.retention_expires_at
    with pytest.raises(service.StaleFence):
        service.fail_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                conversation_id=conversation.id, submission_id=accepted.submission_id,
                                attempt_number=1, fence=1, category="provider_timeout", now=now + timedelta(minutes=6))
    assert conversation.retention_expires_at == retained_until


def test_failure_rejects_deleted_and_quarantined_owner(db: Session, trip_setup: dict):
    organizer = trip_setup["organizer"]
    organizer.status = "joined"
    now = service.utcnow()
    conversation = service.create_or_discover(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                              scope="personal", context_key="personal", now=now)
    accepted = service.submit_message(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                      conversation_id=conversation.id, idempotency_key="deleted-failure", text="one",
                                      selected_plan_item_id=None, now=now)
    service.delete_conversation(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                conversation_id=conversation.id, now=now + timedelta(minutes=1))
    with pytest.raises(service.ConversationUnavailable):
        service.fail_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                conversation_id=conversation.id, submission_id=accepted.submission_id,
                                attempt_number=1, fence=1, category="late", now=now + timedelta(minutes=1))

    organizer.status = "joined"
    organizer.quarantined_at = now
    organizer.quarantine_reason = "security_incident"
    organizer.quarantined_by_membership_id = organizer.id
    with pytest.raises(service.ConversationUnavailable):
        service.fail_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                conversation_id=conversation.id, submission_id=accepted.submission_id,
                                attempt_number=1, fence=1, category="late", now=now + timedelta(minutes=1))
