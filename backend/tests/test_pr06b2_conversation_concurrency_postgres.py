"""Independent-Session PostgreSQL races for the PR-06B.2 lifecycle.

Every worker builds its own Session/connection.  Barriers bound interleavings;
the assertions inspect committed rows rather than treating an HTTP status as
proof of a durable state.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Conversation, ConversationMessage, ConversationMessageOption, ConversationSubmission, ConversationSubmissionRecovery, Trip, TripMembership, User
from app.domain.conversations import service
from app.jobs import conversation_purge


_seed_records: list[tuple[str, str, str]] = []


@pytest.fixture(autouse=True)
def _cleanup_committed_race_records(test_engine):
    """These are true multi-connection commits, so remove only our graph.

    The shared PostgreSQL suite deliberately has a session-scoped database.
    Leaving committed race fixtures behind would contaminate unrelated seed
    idempotency tests and invalidate the full-suite signal.
    """
    _seed_records.clear()
    yield
    records, _seed_records[:] = list(_seed_records), []
    factory = _factory(test_engine)
    with factory.begin() as db:
        for trip_id, membership_id, user_id in records:
            conversation_ids = select(Conversation.id).where(Conversation.trip_id == trip_id)
            message_ids = select(ConversationMessage.id).where(ConversationMessage.conversation_id.in_(conversation_ids))
            submission_ids = select(ConversationSubmission.id).where(ConversationSubmission.conversation_id.in_(conversation_ids))
            db.execute(delete(ConversationSubmissionRecovery).where(ConversationSubmissionRecovery.submission_id.in_(submission_ids)))
            db.execute(delete(ConversationMessageOption).where(ConversationMessageOption.assistant_message_id.in_(message_ids)))
            db.execute(delete(ConversationSubmission).where(ConversationSubmission.conversation_id.in_(conversation_ids)))
            db.execute(delete(ConversationMessage).where(ConversationMessage.conversation_id.in_(conversation_ids)))
            db.execute(delete(Conversation).where(Conversation.trip_id == trip_id))
            db.execute(delete(TripMembership).where(TripMembership.id == membership_id))
            db.execute(delete(Trip).where(Trip.id == trip_id))
            db.execute(delete(User).where(User.id == user_id))


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(engine) -> dict[str, str]:
    factory = _factory(engine)
    with factory.begin() as db:
        user = User(name="Race owner", email=f"race-{datetime.now(timezone.utc).timestamp()}@example.test")
        db.add(user)
        db.flush()
        trip = Trip(name="Race trip", destination="Chicago", created_by_user_id=user.id)
        db.add(trip)
        db.flush()
        member = TripMembership(trip_id=trip.id, user_id=user.id, role="organizer", status="joined")
        db.add(member)
        db.flush()
        _seed_records.append((trip.id, member.id, user.id))
        return {"trip_id": trip.id, "membership_id": member.id}


def _run_two(factory, callback):
    barrier = Barrier(2, timeout=10)

    def worker(slot: int):
        with factory() as db:
            barrier.wait()
            try:
                with db.begin():
                    return ("ok", callback(db, slot))
            except Exception as exc:  # assertion classifies expected lifecycle result
                return ("error", type(exc).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        return [future.result(timeout=15) for future in (pool.submit(worker, 0), pool.submit(worker, 1))]


def _conversation(factory, ids: dict[str, str], now: datetime) -> str:
    with factory.begin() as db:
        return service.create_or_discover(
            db, trip_id=ids["trip_id"], membership_id=ids["membership_id"],
            scope="personal", context_key="personal", now=now,
        ).id


def _submission(factory, ids: dict[str, str], conversation_id: str, now: datetime) -> str:
    with factory.begin() as db:
        return service.submit_message(
            db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
            idempotency_key="original", text="original message", selected_plan_item_id=None, now=now,
        ).submission_id


def _revoke_membership(db, ids: dict[str, str], *, quarantine: bool) -> None:
    # Mirrors the mandatory root order for a race harness. Production removal
    # itself remains owned by its protected transaction writer.
    db.scalar(select(Trip).where(Trip.id == ids["trip_id"]).with_for_update())
    member = db.scalar(select(TripMembership).where(TripMembership.id == ids["membership_id"]).with_for_update())
    assert member is not None
    if quarantine:
        member.quarantined_at = datetime.now(timezone.utc)
        member.quarantine_reason = "security_incident"
        member.quarantined_by_membership_id = member.id
    else:
        member.status = "removed"


def test_two_independent_sessions_create_one_natural_conversation(test_engine):
    factory, ids, now = _factory(test_engine), _seed(test_engine), datetime.now(timezone.utc)
    results = _run_two(factory, lambda db, _: service.create_or_discover(
        db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], scope="personal", context_key="personal", now=now
    ).id)
    assert [kind for kind, _ in results] == ["ok", "ok"]
    assert results[0][1] == results[1][1]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Conversation).where(Conversation.trip_id == ids["trip_id"])) == 1


def test_independent_submission_races_preserve_one_user_message_and_running_slot(test_engine):
    factory, ids, now = _factory(test_engine), _seed(test_engine), datetime.now(timezone.utc)
    conversation_id = _conversation(factory, ids, now)
    same_key = _run_two(factory, lambda db, _: service.submit_message(
        db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
        idempotency_key="same", text="same", selected_plan_item_id=None, now=now,
    ).submission_id)
    assert [kind for kind, _ in same_key] == ["ok", "ok"] and same_key[0][1] == same_key[1][1]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id)) == 1
        assert db.scalar(select(func.count()).select_from(ConversationSubmission).where(
            ConversationSubmission.conversation_id == conversation_id, ConversationSubmission.state == "running"
        )) == 1
    conflict = _run_two(factory, lambda db, slot: service.submit_message(
        db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
        idempotency_key="same", text="conflict-a" if slot == 0 else "conflict-b", selected_plan_item_id=None, now=now,
    ).submission_id)
    assert {value for kind, value in conflict if kind == "error"} == {"IdempotencyConflict"}
    different = _run_two(factory, lambda db, slot: service.submit_message(
        db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
        idempotency_key=f"different-{slot}", text="different", selected_plan_item_id=None, now=now,
    ).submission_id)
    assert {value for kind, value in different if kind == "error"} == {"ConversationBusy"}


def test_independent_recovery_completion_failure_and_purge_races(test_engine):
    factory, ids, now = _factory(test_engine), _seed(test_engine), datetime.now(timezone.utc)
    conversation_id = _conversation(factory, ids, now)
    submission_id = _submission(factory, ids, conversation_id, now)
    reclaim_at = now + timedelta(minutes=6)
    recoveries = _run_two(factory, lambda db, slot: service.recover_submission(
        db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
        submission_id=submission_id, recovery_key=f"recovery-{slot}", now=reclaim_at,
    ).fence)
    assert sum(kind == "ok" for kind, _ in recoveries) == 1
    assert {value for kind, value in recoveries if kind == "error"} == {"ConversationBusy"}
    with factory() as db:
        current = db.get(ConversationSubmission, submission_id)
        assert current is not None and current.fence == 2 and current.attempt_number == 2
        assert db.scalar(select(func.count()).select_from(ConversationSubmissionRecovery).where(
            ConversationSubmissionRecovery.submission_id == submission_id
        )) == 1

    # An old fenced completion/failure cannot race past the recovery. Both
    # workers use separate connections and must observe a stale fence.
    stale = _run_two(factory, lambda db, slot: (
        service.complete_submission(
            db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
            submission_id=submission_id, attempt_number=1, fence=1, content="late", now=reclaim_at,
        ) if slot == 0 else service.fail_submission(
            db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
            submission_id=submission_id, attempt_number=1, fence=1, category="provider_timeout", now=reclaim_at,
        )
    ))
    assert {value for kind, value in stale if kind == "error"} == {"StaleFence"}
    with factory.begin() as db:
        service.delete_conversation(db, trip_id=ids["trip_id"], membership_id=ids["membership_id"],
                                    conversation_id=conversation_id, now=reclaim_at)
    purges = _run_two(factory, lambda db, _: conversation_purge.run_once(db, now=reclaim_at + timedelta(days=30), batch_size=10)["purged"])
    assert sum(value for kind, value in purges if kind == "ok") == 1
    with factory() as db:
        assert db.get(Conversation, conversation_id) is None
        assert db.get(TripMembership, ids["membership_id"]) is not None


def test_quarantine_or_removal_race_cannot_authorize_late_submission_or_completion(test_engine):
    factory, ids, now = _factory(test_engine), _seed(test_engine), datetime.now(timezone.utc)
    conversation_id = _conversation(factory, ids, now)
    accepted = _run_two(factory, lambda db, slot: (
        service.submit_message(
            db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
            idempotency_key="race-submit", text="race", selected_plan_item_id=None, now=now,
        ) if slot == 0 else _revoke_membership(db, ids, quarantine=True)
    ))
    with factory() as db:
        member = db.get(TripMembership, ids["membership_id"])
        assert member is not None and member.quarantined_at is not None
        submission = db.scalar(select(ConversationSubmission).where(
            ConversationSubmission.conversation_id == conversation_id
        ))
    if submission is not None:
        with factory.begin() as db:
            try:
                service.complete_submission(
                    db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
                    submission_id=submission.id, attempt_number=1, fence=1, content="late", now=now,
                )
            except service.ConversationUnavailable:
                pass
            else:
                raise AssertionError("quarantined Membership completed a Conversation")
    assert all(kind in {"ok", "error"} for kind, _ in accepted)

    # A completion concurrent with removal may finish before removal acquires
    # the root lock, but after the removal commits no later completion can
    # persist and ownership stays on the original Membership row.
    factory, ids, now = _factory(test_engine), _seed(test_engine), datetime.now(timezone.utc)
    conversation_id = _conversation(factory, ids, now)
    submission_id = _submission(factory, ids, conversation_id, now)
    _run_two(factory, lambda db, slot: (
        service.complete_submission(
            db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
            submission_id=submission_id, attempt_number=1, fence=1, content="maybe before removal", now=now + timedelta(minutes=1),
        ) if slot == 0 else _revoke_membership(db, ids, quarantine=False)
    ))
    with factory() as db:
        member = db.get(TripMembership, ids["membership_id"])
        root = db.get(Conversation, conversation_id)
        assert member is not None and member.status == "removed"
        assert root is not None and root.owner_membership_id == ids["membership_id"]
    with factory.begin() as db:
        try:
            service.fail_submission(
                db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
                submission_id=submission_id, attempt_number=1, fence=1, category="late", now=now + timedelta(minutes=2),
            )
        except service.ConversationUnavailable:
            pass
        else:
            raise AssertionError("removed Membership wrote a late terminal outcome")


def test_purge_history_and_submission_races_never_resurrect_a_deleted_root(test_engine):
    factory, ids, now = _factory(test_engine), _seed(test_engine), datetime.now(timezone.utc)
    conversation_id = _conversation(factory, ids, now)
    _submission(factory, ids, conversation_id, now)
    with factory.begin() as db:
        service.delete_conversation(db, trip_id=ids["trip_id"], membership_id=ids["membership_id"],
                                    conversation_id=conversation_id, now=now)
    history_race = _run_two(factory, lambda db, slot: (
        service.history(db, trip_id=ids["trip_id"], membership_id=ids["membership_id"], conversation_id=conversation_id,
                        before=None, limit=10, now=now + timedelta(days=30, seconds=1)) if slot == 0
        else conversation_purge.run_once(db, now=now + timedelta(days=30, seconds=1), batch_size=10)
    ))
    assert any(kind == "error" and value == "ConversationUnavailable" for kind, value in history_race)
    # A worker that skipped a row because the read briefly held its lock must
    # succeed on the next bounded run; retry is the purge crash/lock contract.
    with factory.begin() as db:
        conversation_purge.run_once(db, now=now + timedelta(days=30, seconds=1), batch_size=10)
    with factory() as db:
        assert db.get(Conversation, conversation_id) is None
