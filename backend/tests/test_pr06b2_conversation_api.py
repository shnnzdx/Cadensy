"""PR-06B.2A/B owner-scoped durable Conversation HTTP/service tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import main as api
from app.db.models import Conversation, ConversationMessage, ConversationSubmission, TripMembership, User
from app.domain import auth
from app.domain.conversations import service
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


def test_request_bound_runtime_runs_once_for_a_new_claim_not_a_replay(
    db: Session, trip_setup: dict, monkeypatch: pytest.MonkeyPatch
):
    """PR-06B.3 keeps 202 admission semantics while preventing double runs."""
    calls = []

    class _Coordinator:
        def execute(self, *, identity, now):
            calls.append(identity)

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
            headers={**headers, "Idempotency-Key": "runtime-once"}, json={"text": "hello"},
        )
        replay = client.post(
            f"/api/trips/{trip_id}/conversations/{conversation_id}/messages",
            headers={**headers, "Idempotency-Key": "runtime-once"}, json={"text": "hello"},
        )

    assert accepted.status_code == replay.status_code == 202
    assert len(calls) == 1
    assert calls[0].submission_id == accepted.json()["submission_id"]


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
    recovered = service.recover_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                           conversation_id=conversation.id, submission_id=submission.submission_id,
                                           recovery_key="recovery", now=now + timedelta(minutes=6))
    assert recovered.fence == 2 and recovered.attempt_number == 2
    with pytest.raises(service.StaleFence):
        service.complete_submission(db, trip_id=trip_setup["trip"].id, membership_id=organizer.id,
                                    conversation_id=conversation.id, submission_id=submission.submission_id,
                                    attempt_number=1, fence=1, content="late", now=now + timedelta(minutes=6))
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
