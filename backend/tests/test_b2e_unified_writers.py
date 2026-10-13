"""PostgreSQL seams for the new B.2E writer coordinators."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread, current_thread
from time import monotonic

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.api import main as api
from app.db import session as db_session
from app.jobs import scheduler as scheduler_job
from app.db.models import AuthSession, ChangeProposal, DecisionRound, GuestSession, InviteLink, MutationIdempotency, Plan, PlanChange, PlanItem, ProposalDecision, Trip, TripMembership, UpdateNotice, User, Vote
from app.domain import auth as auth_service
from app.domain.decisions import organizer as legacy_organizer
from app.domain.transactions import CredentialProof, HmacSigningKey, PreliminaryActorScope, StalePlanRevision
from app.domain.transactions import booking as protected_booking
from app.domain.transactions import membership_join, membership_remove, organizer_actions
from app.domain.transactions import proposal_decision as protected_proposal_decision
from app.domain.transactions import scheduler as protected_scheduler
from app.domain.transactions.membership_join import join_invite_under_trip_root
from app.domain.transactions.scheduler import expire_due_proposal, settle_due_round
from app.domain.transactions.trip_preferences_common import StaticHmacKeyring


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


_seeded: list[tuple[str, tuple[str, str]]] = []


@pytest.fixture(autouse=True)
def _cleanup_seeded_rows(test_engine):
    _seeded.clear()
    yield
    for trip_id, user_ids in _seeded:
        with _factory(test_engine).begin() as db:
            plan_ids = select(Plan.id).where(Plan.trip_id == trip_id)
            item_ids = select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids))
            round_ids = select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(item_ids))
            proposal_ids = select(ChangeProposal.id).where(ChangeProposal.plan_item_id.in_(item_ids))
            db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == trip_id).delete(synchronize_session=False)
            db.query(UpdateNotice).filter(UpdateNotice.trip_id == trip_id).delete(synchronize_session=False)
            db.query(Vote).filter(Vote.round_id.in_(round_ids)).delete(synchronize_session=False)
            db.query(ProposalDecision).filter(ProposalDecision.proposal_id.in_(proposal_ids)).delete(synchronize_session=False)
            db.query(DecisionRound).filter(DecisionRound.plan_item_id.in_(item_ids)).delete(synchronize_session=False)
            db.query(ChangeProposal).filter(ChangeProposal.plan_item_id.in_(item_ids)).delete(synchronize_session=False)
            db.query(PlanChange).filter(PlanChange.plan_id.in_(plan_ids)).delete(synchronize_session=False)
            db.query(GuestSession).filter(GuestSession.trip_id == trip_id).delete(synchronize_session=False)
            db.query(PlanItem).filter(PlanItem.plan_id.in_(plan_ids)).delete(synchronize_session=False)
            db.query(Plan).filter(Plan.trip_id == trip_id).delete(synchronize_session=False)
            db.query(InviteLink).filter(InviteLink.trip_id == trip_id).delete(synchronize_session=False)
            db.query(TripMembership).filter(TripMembership.trip_id == trip_id).delete(synchronize_session=False)
            db.query(Trip).filter(Trip.id == trip_id).delete(synchronize_session=False)
            db.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)

def _seed(engine):
    stamp = str(datetime.now(timezone.utc).timestamp())
    token = f"b2e-{stamp}"
    with _factory(engine).begin() as db:
        owner = User(name="Owner", email=f"owner-{stamp}@test.local")
        member = User(name="Member", email=f"member-{stamp}@test.local")
        db.add_all((owner, member)); db.flush()
        trip = Trip(name="B2E", destination="Chicago", created_by_user_id=owner.id)
        db.add(trip); db.flush()
        organizer = TripMembership(trip_id=trip.id, user_id=owner.id, role="organizer", status="joined")
        participant = TripMembership(trip_id=trip.id, user_id=member.id, role="participant", status="joined")
        guest = TripMembership(trip_id=trip.id, guest_display_name="Guest", role="participant", status="joined")
        db.add_all((organizer, participant, guest)); db.flush()
        db.add(AuthSession(user_id=owner.id, token_hash=auth_service.token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        guest_token = auth_service.start_guest_session(db, guest).token
        plan = Plan(trip_id=trip.id); db.add(plan); db.flush()
        item = PlanItem(plan_id=plan.id, day_index=1, day_date=date(2026, 8, 15), start_hour=12, duration_min=60, title="Item", place="Place")
        db.add(item); db.flush()
        _seeded.append((trip.id, (owner.id, member.id)))
        return {"trip": trip.id, "plan": plan.id, "organizer": organizer.id, "organizer_user": owner.id, "participant": participant.id, "participant_user": member.id, "guest": guest.id, "token": token, "guest_token": guest_token, "item": item.id}


def _scope(seed):
    return PreliminaryActorScope(trip_id=seed["trip"], membership_id=seed["organizer"], credential_kind="account", expected_user_id=seed["organizer_user"])


def _keyring():
    return StaticHmacKeyring(HmacSigningKey("b2e-test", b"b2e-test-key"))


def _root_lock_contention(engine, trip_id, operation):
    """Prove a real protected operation waits on the authoritative Trip lock.

    The holder is deliberately test-owned, but the contender is always the
    actual public protected writer.  PID observation is made from a third
    connection, so an elapsed-time assertion cannot accidentally substitute
    for PostgreSQL lock evidence.
    """
    factory = _factory(engine)
    reached_root = Event()
    acquired_root = Event()
    finished = Event()
    result = Queue()
    pids: dict[str, int] = {}

    def is_trip_root(statement: str) -> bool:
        normalized = " ".join(statement.lower().split())
        return "from trip" in normalized and "from trip_membership" not in normalized and "for update" in normalized

    def backend_pid(connection) -> int:
        raw = connection.connection
        return getattr(raw, "driver_connection", raw).info.backend_pid

    def before(connection, _cursor, statement, _parameters, _context, _many):
        if current_thread().name == "b2e-root-contender" and is_trip_root(statement):
            pids["contender"] = backend_pid(connection)
            reached_root.set()

    def after(_connection, _cursor, statement, _parameters, _context, _many):
        if current_thread().name == "b2e-root-contender" and is_trip_root(statement):
            acquired_root.set()

    def run() -> None:
        try:
            result.put(operation())
        except BaseException as exc:  # asserted by the caller after cleanup
            result.put(exc)
        finally:
            finished.set()

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    worker = Thread(target=run, name="b2e-root-contender", daemon=True)
    try:
        with factory() as holder:
            with holder.begin():
                pids["holder"] = holder.scalar(text("select pg_backend_pid()"))
                holder.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
                worker.start()
                assert reached_root.wait(5)
                assert not acquired_root.is_set()
                observed = False
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    with factory() as observer:
                        blockers = observer.scalar(text("select pg_blocking_pids(:pid)"), {"pid": pids["contender"]})
                    if pids["holder"] in (blockers or []):
                        observed = True
                        break
                assert observed, f"contender PID {pids['contender']} was not blocked by {pids['holder']}"
                assert pids["holder"] != pids["contender"]
        assert finished.wait(5)
        assert acquired_root.is_set()
    finally:
        if worker.is_alive():
            worker.join(5)
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
    assert not worker.is_alive()
    outcome = result.get_nowait()
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome, pids


def _real_writer_overlap(engine, first_operation, second_operation):
    """Pause real writer A at its owned commit and prove real writer B waits."""
    factory = _factory(engine)
    entered, release, second_root = Event(), Event(), Event()
    pids: dict[str, int] = {}
    results: Queue[object] = Queue()

    def root(statement: str) -> bool:
        normalized = " ".join(statement.lower().split())
        return "from trip" in normalized and "from trip_membership" not in normalized and "for update" in normalized

    def capture(connection, _cursor, statement, _parameters, _context, _many):
        if not root(statement):
            return
        pid = connection.connection.driver_connection.info.backend_pid
        if current_thread().name == "b2e-overlap-first":
            pids["first"] = pid
        elif current_thread().name == "b2e-overlap-second":
            pids["second"] = pid
            second_root.set()

    def pause(_session):
        if current_thread().name == "b2e-overlap-first":
            entered.set()
            assert release.wait(5), "test did not release real writer A"

    def run(operation):
        try:
            results.put(operation())
        except BaseException as exc:
            results.put(exc)

    event.listen(engine, "before_cursor_execute", capture)
    event.listen(membership_join.Session, "before_commit", pause)
    first = Thread(target=run, args=(first_operation,), name="b2e-overlap-first", daemon=True)
    second = Thread(target=run, args=(second_operation,), name="b2e-overlap-second", daemon=True)
    try:
        first.start()
        assert entered.wait(5)
        second.start()
        assert second_root.wait(5)
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with factory() as observer:
                if pids["first"] in (observer.scalar(text("select pg_blocking_pids(:pid)"), {"pid": pids["second"]}) or []):
                    break
        else:
            raise AssertionError("real writer B did not block behind real writer A")
    finally:
        release.set()
        first.join(5)
        second.join(5)
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(membership_join.Session, "before_commit", pause)
    assert not first.is_alive() and not second.is_alive()
    return [results.get_nowait(), results.get_nowait()], pids


def test_member_removal_revokes_guest_and_advances_both_revisions(test_engine):
    seed = _seed(test_engine)
    result = membership_remove.execute_protected_membership_remove(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
        command=membership_remove.ProtectedMembershipRemove(seed["guest"], "remove-key", 0), keyring=_keyring(),
    )
    assert result.payload == {"removed": True} and result.trip_planning_facts_revision == 1
    with _factory(test_engine)() as db:
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 1
        assert db.get(Plan, seed["plan"]).revision == 1
        assert db.get(TripMembership, seed["guest"]).status == "removed"
        assert db.scalar(select(GuestSession.revoked_at).where(GuestSession.membership_id == seed["guest"])) is not None


def test_guest_invite_join_uses_trip_root_and_advances_both_revisions(test_engine):
    seed = _seed(test_engine)
    token = "b2e-invite-token"
    with _factory(test_engine).begin() as db:
        db.add(InviteLink(trip_id=seed["trip"], token_hash=auth_service.token_hash(token), is_primary=True))
    joined = join_invite_under_trip_root(
        session_factory=_factory(test_engine), token=token, display_name="New guest", email=None
    )
    assert joined.guest_session is not None
    with _factory(test_engine)() as db:
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 1
        assert db.get(Plan, seed["plan"]).revision == 1
        assert db.get(TripMembership, joined.membership.id).status == "joined"


def test_scheduler_terminal_state_is_retry_safe_and_advances_plan_revision(test_engine):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="due", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
        db.add(round_); db.flush(); round_id = round_.id
    assert settle_due_round(session_factory=_factory(test_engine), trip_id=seed["trip"], round_id=round_id, now=datetime.now(timezone.utc)) is True
    assert settle_due_round(session_factory=_factory(test_engine), trip_id=seed["trip"], round_id=round_id, now=datetime.now(timezone.utc)) is False
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed["plan"]).revision == 1
        assert db.get(DecisionRound, round_id).status == "closed"


def test_scheduler_expiry_and_nonaffected_member_escalation_preserve_policy(test_engine):
    seed = _seed(test_engine)
    with _factory(test_engine).begin() as db:
        proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add(proposal); db.flush(); proposal_id = proposal.id
    scope = PreliminaryActorScope(trip_id=seed["trip"], membership_id=seed["participant"], credential_kind="account", expected_user_id=seed["participant_user"])
    # The participant is deliberately not an affected ProposalDecision row.
    with _factory(test_engine).begin() as db:
        db.add(AuthSession(user_id=seed["participant_user"], token_hash=auth_service.token_hash("member-token"), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    result = organizer_actions.execute_protected_organizer_action(
        session_factory=_factory(test_engine), scope=scope, credential=CredentialProof.from_bearer_token("member-token"),
        command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_ESCALATE_OPERATION, proposal_id, "escalate-key", 0), keyring=_keyring(),
    )
    assert result.payload["status"] == "escalated"
    assert expire_due_proposal(session_factory=_factory(test_engine), trip_id=seed["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc) + timedelta(days=2)) is True
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed["plan"]).revision == 2
        assert db.get(ChangeProposal, proposal_id).status == "expired"


@pytest.mark.parametrize(
    "case",
    ("account_join", "guest_join", "remove", "settle", "expire", "extend", "escalate", "deadlock"),
)
def test_every_b2e_writer_waits_on_the_real_postgresql_trip_root(test_engine, case):
    """E01--E08: actual writers, independent PIDs, and pg_blocking_pids()."""
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    expected_revision = 1
    if case == "account_join":
        invite_token = f"account-invite-{seed['trip']}"
        with factory.begin() as db:
            joiner = User(name="Account joiner", email=f"joiner-{seed['trip']}@test.local")
            db.add(joiner)
            db.add(InviteLink(trip_id=seed["trip"], token_hash=auth_service.token_hash(invite_token), is_primary=True))
            db.flush()
            _seeded[-1] = (seed["trip"], (*_seeded[-1][1], joiner.id))
        operation = lambda: join_invite_under_trip_root(
            session_factory=factory, token=invite_token, display_name="Account joiner", email=f"joiner-{seed['trip']}@test.local"
        )
    elif case == "guest_join":
        invite_token = f"guest-invite-{seed['trip']}"
        with factory.begin() as db:
            db.add(InviteLink(trip_id=seed["trip"], token_hash=auth_service.token_hash(invite_token), is_primary=True))
        operation = lambda: join_invite_under_trip_root(
            session_factory=factory, token=invite_token, display_name="Guest joiner", email=None
        )
    elif case == "remove":
        operation = lambda: membership_remove.execute_protected_membership_remove(
            session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
            command=membership_remove.ProtectedMembershipRemove(seed["guest"], "root-remove", 0), keyring=_keyring(),
        )
    elif case == "settle":
        with factory.begin() as db:
            round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="due", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
            db.add(round_)
            db.flush()
            resource_id = round_.id
        operation = lambda: settle_due_round(session_factory=factory, trip_id=seed["trip"], round_id=resource_id, now=datetime.now(timezone.utc))
    elif case == "expire":
        with factory.begin() as db:
            proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
            db.add(proposal)
            db.flush()
            resource_id = proposal.id
        operation = lambda: expire_due_proposal(session_factory=factory, trip_id=seed["trip"], proposal_id=resource_id, now=datetime.now(timezone.utc))
    elif case == "extend":
        with factory.begin() as db:
            round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="extend", options=[], deadline=datetime.now(timezone.utc) + timedelta(hours=1), status="open")
            db.add(round_)
            db.flush()
            resource_id = round_.id
        operation = lambda: organizer_actions.execute_protected_organizer_action(
            session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
            command=organizer_actions.ProtectedOrganizerAction(organizer_actions.ROUND_EXTEND_OPERATION, resource_id, "root-extend", 0), keyring=_keyring(),
        )
    elif case == "escalate":
        member_token = f"participant-{seed['trip']}"
        with factory.begin() as db:
            db.add(AuthSession(user_id=seed["participant_user"], token_hash=auth_service.token_hash(member_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
            proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
            db.add(proposal)
            db.flush()
            resource_id = proposal.id
        scope = PreliminaryActorScope(trip_id=seed["trip"], membership_id=seed["participant"], credential_kind="account", expected_user_id=seed["participant_user"])
        operation = lambda: organizer_actions.execute_protected_organizer_action(
            session_factory=factory, scope=scope, credential=CredentialProof.from_bearer_token(member_token),
            command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_ESCALATE_OPERATION, resource_id, "root-escalate", 0), keyring=_keyring(),
        )
    else:
        with factory.begin() as db:
            proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="escalated", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
            db.add(proposal)
            db.flush()
            resource_id = proposal.id
        operation = lambda: organizer_actions.execute_protected_organizer_action(
            session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
            command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_DEADLOCK_OPERATION, resource_id, "root-deadlock", 0, "keep"), keyring=_keyring(),
        )

    outcome, pids = _root_lock_contention(test_engine, seed["trip"], operation)
    assert pids["holder"] != pids["contender"]
    if case in {"settle", "expire"}:
        assert outcome is True
    with factory() as db:
        assert db.get(Plan, seed["plan"]).revision == expected_revision


@pytest.mark.parametrize("email", (None, "account"))
def test_join_final_commit_failure_leaves_no_member_guest_or_revision_partial(test_engine, monkeypatch, email):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    invite_token = f"rollback-invite-{seed['trip']}-{email or 'guest'}"
    with factory.begin() as db:
        db.add(InviteLink(trip_id=seed["trip"], token_hash=auth_service.token_hash(invite_token), is_primary=True))
        if email:
            joiner = User(name="Rollback account", email=f"rollback-{seed['trip']}@test.local")
            db.add(joiner)
            db.flush()
            _seeded[-1] = (seed["trip"], (*_seeded[-1][1], joiner.id))
    before_commit = lambda _session: (_ for _ in ()).throw(RuntimeError("final commit fault"))
    event.listen(membership_join.Session, "before_commit", before_commit)
    try:
        with pytest.raises(RuntimeError, match="final commit fault"):
            join_invite_under_trip_root(
                session_factory=factory, token=invite_token, display_name="Rollback",
                email=f"rollback-{seed['trip']}@test.local" if email else None,
            )
    finally:
        event.remove(membership_join.Session, "before_commit", before_commit)
    with factory() as db:
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 0
        assert db.get(Plan, seed["plan"]).revision == 0
        assert db.query(TripMembership).filter(TripMembership.trip_id == seed["trip"]).count() == 3
        assert db.query(GuestSession).filter(GuestSession.trip_id == seed["trip"]).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 0


def test_removal_final_commit_failure_rolls_back_revocation_revision_and_ledger(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    original = membership_remove.TripDurableCommandKernel._checkpoint
    membership_remove.TripDurableCommandKernel._checkpoint = lambda self, stage: (_ for _ in ()).throw(RuntimeError("ledger fault")) if stage == "after_ledger_insert" else original(self, stage)
    try:
        with pytest.raises(RuntimeError, match="ledger fault"):
            membership_remove.execute_protected_membership_remove(
                session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
                command=membership_remove.ProtectedMembershipRemove(seed["guest"], "rollback-remove", 0), keyring=_keyring(),
            )
    finally:
        membership_remove.TripDurableCommandKernel._checkpoint = original
    with factory() as db:
        assert db.get(TripMembership, seed["guest"]).status == "joined"
        assert db.scalar(select(GuestSession.revoked_at).where(GuestSession.membership_id == seed["guest"])) is None
        assert db.get(Trip, seed["trip"]).planning_facts_revision == db.get(Plan, seed["plan"]).revision == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 0


@pytest.mark.parametrize("case", ("settle", "expire"))
def test_scheduler_final_commit_failure_restores_due_terminal_state(test_engine, case):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        if case == "settle":
            row = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="rollback", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
        else:
            row = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
        db.add(row)
        db.flush()
        resource_id = row.id
    before_commit = lambda _session: (_ for _ in ()).throw(RuntimeError("scheduler final commit fault"))
    event.listen(membership_join.Session, "before_commit", before_commit)
    try:
        with pytest.raises(RuntimeError, match="scheduler final commit fault"):
            if case == "settle":
                settle_due_round(session_factory=factory, trip_id=seed["trip"], round_id=resource_id, now=datetime.now(timezone.utc))
            else:
                expire_due_proposal(session_factory=factory, trip_id=seed["trip"], proposal_id=resource_id, now=datetime.now(timezone.utc))
    finally:
        event.remove(membership_join.Session, "before_commit", before_commit)
    with factory() as db:
        assert db.get(Plan, seed["plan"]).revision == 0
        assert (db.get(DecisionRound, resource_id).status if case == "settle" else db.get(ChangeProposal, resource_id).status) == ("open" if case == "settle" else "waiting_affected_members")
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed["trip"]).count() == 0


@pytest.mark.parametrize(
    ("operation", "action"),
    (
        (organizer_actions.ROUND_EXTEND_OPERATION, None),
        (organizer_actions.PROPOSAL_ESCALATE_OPERATION, None),
        (organizer_actions.PROPOSAL_DEADLOCK_OPERATION, "keep"),
        (organizer_actions.PROPOSAL_DEADLOCK_OPERATION, "split"),
        (organizer_actions.PROPOSAL_DEADLOCK_OPERATION, "clear"),
    ),
)
def test_organizer_ledger_failure_rolls_back_every_action_family(test_engine, operation, action):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        if operation == organizer_actions.ROUND_EXTEND_OPERATION:
            row = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="rollback", options=[], deadline=datetime.now(timezone.utc) + timedelta(hours=1), status="open")
        else:
            row = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="escalated" if operation == organizer_actions.PROPOSAL_DEADLOCK_OPERATION else "waiting_affected_members", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add(row)
        db.flush()
        resource_id = row.id
    original = organizer_actions.DurableCommandKernel._checkpoint
    organizer_actions.DurableCommandKernel._checkpoint = lambda self, stage: (_ for _ in ()).throw(RuntimeError("organizer ledger fault")) if stage == "after_ledger_insert" else original(self, stage)
    try:
        with pytest.raises(RuntimeError, match="organizer ledger fault"):
            organizer_actions.execute_protected_organizer_action(
                session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
                command=organizer_actions.ProtectedOrganizerAction(operation, resource_id, f"rollback-{operation}", 0, action), keyring=_keyring(),
            )
    finally:
        organizer_actions.DurableCommandKernel._checkpoint = original
    with factory() as db:
        assert db.get(Plan, seed["plan"]).revision == 0
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 0
        if operation == organizer_actions.ROUND_EXTEND_OPERATION:
            assert db.get(DecisionRound, resource_id).extended_at is None
        else:
            assert db.get(ChangeProposal, resource_id).status == ("escalated" if action else "waiting_affected_members")
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed["trip"]).count() == 0


@pytest.mark.parametrize(
    ("action", "expected_title", "expected_settledness", "expected_origin"),
    (
        ("keep", "Item", "loose", "deadlock_keep"),
        ("split", "Split for this block", "settled", "deadlock_split"),
        ("clear", "Item", "removed", "deadlock_remove"),
    ),
)
def test_protected_deadlock_branches_commit_once_and_replay_without_duplicate_effects(test_engine, action, expected_title, expected_settledness, expected_origin):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="escalated", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add(proposal)
        db.flush()
        proposal_id = proposal.id
    command = organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_DEADLOCK_OPERATION, proposal_id, f"deadlock-{action}", 0, action)
    first = organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]), command=command, keyring=_keyring())
    replay = organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]), command=command, keyring=_keyring())
    assert first.payload == replay.payload and replay.replayed is True
    with factory() as db:
        item = db.get(PlanItem, seed["item"])
        assert item.title == expected_title and item.settledness == expected_settledness
        assert db.get(Plan, seed["plan"]).revision == 1
        assert db.scalar(select(PlanChange.origin).where(PlanChange.plan_id == seed["plan"])) == expected_origin
        assert db.query(UpdateNotice).filter(UpdateNotice.trip_id == seed["trip"]).count() == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1


def test_membership_routes_preserve_legacy_off_and_use_protected_transaction_when_enabled(test_engine, monkeypatch):
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    legacy = _seed(test_engine)
    legacy_invite = f"http-legacy-{legacy['trip']}"
    with factory.begin() as db:
        db.add(InviteLink(trip_id=legacy["trip"], token_hash=auth_service.token_hash(legacy_invite), is_primary=True))
    with TestClient(api.app) as client:
        response = client.post(f"/api/invites/{legacy_invite}/join", json={"display_name": "Legacy guest"})
    assert response.status_code == 200 and "guest_token" in response.json()
    with factory() as db:
        assert db.get(Trip, legacy["trip"]).planning_facts_revision == 0
        assert db.get(Plan, legacy["plan"]).revision == 0

    protected = _seed(test_engine)
    protected_invite = f"http-protected-{protected['trip']}"
    with factory.begin() as db:
        db.add(InviteLink(trip_id=protected["trip"], token_hash=auth_service.token_hash(protected_invite), is_primary=True))
    monkeypatch.setenv("CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES", "1")
    monkeypatch.setenv("APP_ENV", "test")
    with TestClient(api.app) as client:
        joined = client.post(f"/api/invites/{protected_invite}/join", json={"display_name": "Protected guest"})
    assert joined.status_code == 200 and "guest_token" in joined.json()
    with factory() as db:
        assert db.get(Trip, protected["trip"]).planning_facts_revision == 1
        assert db.get(Plan, protected["plan"]).revision == 1

    monkeypatch.setenv("APP_ENV", "production")
    with TestClient(api.app) as client:
        denied = client.post(f"/api/invites/{protected_invite}/join", json={"display_name": "Never writes"})
    assert denied.status_code == 503


def test_membership_remove_http_requires_protected_headers_and_replays_without_second_revocation(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "b2e-http-key")
    monkeypatch.setenv("APP_ENV", "test")
    headers = {
        "Authorization": f"Bearer {seed['token']}", "X-Trip-Id": seed["trip"],
        "Idempotency-Key": "remove-http", "X-Expected-Trip-Facts-Revision": "0",
    }
    with TestClient(api.app) as client:
        missing = client.delete(f"/api/trips/{seed['trip']}/members/{seed['guest']}", headers={"Authorization": headers["Authorization"], "X-Trip-Id": seed["trip"]})
        first = client.delete(f"/api/trips/{seed['trip']}/members/{seed['guest']}", headers=headers)
        replay = client.delete(f"/api/trips/{seed['trip']}/members/{seed['guest']}", headers=headers)
    assert missing.status_code == 428
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"removed": True}
    with factory() as db:
        assert db.get(TripMembership, seed["guest"]).status == "removed"
        assert db.scalar(select(GuestSession.revoked_at).where(GuestSession.membership_id == seed["guest"])) is not None
        assert db.get(Trip, seed["trip"]).planning_facts_revision == db.get(Plan, seed["plan"]).revision == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1


def test_organizer_http_gate_is_default_off_protected_on_and_production_closed(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    with factory.begin() as db:
        round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="http", options=[], deadline=datetime.now(timezone.utc) + timedelta(hours=1), status="open")
        db.add(round_)
        db.flush()
        round_id = round_.id
    base_headers = {"Authorization": f"Bearer {seed['token']}", "X-Trip-Id": seed["trip"]}
    with TestClient(api.app) as client:
        legacy = client.post(f"/api/rounds/{round_id}/extend", headers={**base_headers, "X-Expected-Plan-Revision": "malformed-ignored-off"})
    assert legacy.status_code == 200
    with factory.begin() as db:
        # The schema permits one open Round per item.  Closing the exercised
        # Legacy fixture lets the protected branch use a distinct real Round.
        db.get(DecisionRound, round_id).status = "closed"
        second = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="protected", options=[], deadline=datetime.now(timezone.utc) + timedelta(hours=1), status="open")
        db.add(second)
        db.flush()
        protected_round_id = second.id
    monkeypatch.setenv("CADENSY_B2E_PROTECTED_ORGANIZER_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "b2e-http-key")
    monkeypatch.setenv("APP_ENV", "test")
    protected_headers = {**base_headers, "Idempotency-Key": "extend-http", "X-Expected-Plan-Revision": "0"}
    with TestClient(api.app) as client:
        first = client.post(f"/api/rounds/{protected_round_id}/extend", headers=protected_headers)
        replay = client.post(f"/api/rounds/{protected_round_id}/extend", headers=protected_headers)
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json()
    with factory() as db:
        assert db.get(Plan, seed["plan"]).revision == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1
    monkeypatch.setenv("APP_ENV", "production")
    with TestClient(api.app) as client:
        denied = client.post(f"/api/rounds/{protected_round_id}/extend", headers={**protected_headers, "Idempotency-Key": "production"})
    assert denied.status_code == 503


def test_protected_escalation_and_deadlock_routes_keep_same_trip_policy_and_replay(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2E_PROTECTED_ORGANIZER_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "b2e-http-key")
    monkeypatch.setenv("APP_ENV", "test")
    participant_token = f"http-participant-{seed['trip']}"
    with factory.begin() as db:
        db.add(AuthSession(user_id=seed["participant_user"], token_hash=auth_service.token_hash(participant_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        deadlock = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="escalated", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add_all((proposal, deadlock))
        db.flush()
        proposal_id, deadlock_id = proposal.id, deadlock.id
    participant_headers = {"Authorization": f"Bearer {participant_token}", "X-Trip-Id": seed["trip"], "Idempotency-Key": "http-escalate", "X-Expected-Plan-Revision": "0"}
    organizer_headers = {"Authorization": f"Bearer {seed['token']}", "X-Trip-Id": seed["trip"], "Idempotency-Key": "http-deadlock", "X-Expected-Plan-Revision": "1"}
    with TestClient(api.app) as client:
        missing = client.post(f"/api/proposals/{proposal_id}/escalate", headers={"Authorization": participant_headers["Authorization"], "X-Trip-Id": seed["trip"]})
        first = client.post(f"/api/proposals/{proposal_id}/escalate", headers=participant_headers)
        replay = client.post(f"/api/proposals/{proposal_id}/escalate", headers=participant_headers)
        resolved = client.post(f"/api/proposals/{deadlock_id}/deadlock", headers=organizer_headers, json={"action": "keep"})
        resolved_replay = client.post(f"/api/proposals/{deadlock_id}/deadlock", headers=organizer_headers, json={"action": "keep"})
    assert missing.status_code == 428
    assert first.status_code == replay.status_code == 200 and first.json() == replay.json() == {"id": proposal_id, "status": "escalated"}
    assert resolved.status_code == resolved_replay.status_code == 200 and resolved.json() == resolved_replay.json()
    with factory() as db:
        assert db.get(ChangeProposal, proposal_id).status == "escalated"
        assert db.get(ChangeProposal, deadlock_id).status == "resolved_by_organizer"
        assert db.get(Plan, seed["plan"]).revision == 2
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 2


def test_scheduler_job_gate_selects_protected_due_claim_and_retries_terminal_state(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="job", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
        db.add(round_)
        db.flush()
        round_id = round_.id
    monkeypatch.setattr(scheduler_job, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2E_PROTECTED_SCHEDULER_WRITES", "1")
    monkeypatch.setenv("APP_ENV", "test")
    first = scheduler_job.run_once()
    second = scheduler_job.run_once()
    assert first == {"settled": [round_id], "expired": []}
    assert second == {"settled": [], "expired": []}
    monkeypatch.setenv("APP_ENV", "production")
    assert scheduler_job._protected_scheduler_enabled() is False
    monkeypatch.setenv("APP_ENV", "unapproved")
    assert scheduler_job._protected_scheduler_enabled() is False


def test_due_settlement_and_real_booking_serialize_then_stale_booking_is_rejected(test_engine, monkeypatch):
    """B1: two real writers contend; only the Scheduler terminal change wins."""
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="due", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
        db.add(round_)
        db.flush()
        round_id = round_.id
    entered, release, booking_root = Event(), Event(), Event()
    pids: dict[str, int] = {}
    outcomes: Queue[object] = Queue()
    original = protected_scheduler.orch.settle_round

    def paused_settle(*args):
        value = original(*args)
        entered.set()
        assert release.wait(5)
        return value

    def root(statement: str) -> bool:
        normalized = " ".join(statement.lower().split())
        return "from trip" in normalized and "from trip_membership" not in normalized and "for update" in normalized

    def capture(connection, _cursor, statement, _parameters, _context, _many):
        if not root(statement):
            return
        pid = connection.connection.driver_connection.info.backend_pid
        if current_thread().name == "b2e-scheduler-settle":
            pids["scheduler"] = pid
        elif current_thread().name == "b2e-booking":
            pids["booking"] = pid
            booking_root.set()

    def run_scheduler():
        try:
            outcomes.put(settle_due_round(session_factory=factory, trip_id=seed["trip"], round_id=round_id, now=datetime.now(timezone.utc)))
        except BaseException as exc:
            outcomes.put(exc)

    def run_booking():
        try:
            outcomes.put(protected_booking.execute_protected_booking(
                session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]),
                command=protected_booking.ProtectedBooking(seed["item"], "settlement-race-booking", 0, True),
                keyring=protected_booking.StaticHmacKeyring(HmacSigningKey("b2e-race", b"b2e-race")),
            ))
        except BaseException as exc:
            outcomes.put(exc)

    monkeypatch.setattr(protected_scheduler.orch, "settle_round", paused_settle)
    event.listen(test_engine, "before_cursor_execute", capture)
    first = Thread(target=run_scheduler, name="b2e-scheduler-settle", daemon=True)
    second = Thread(target=run_booking, name="b2e-booking", daemon=True)
    try:
        first.start()
        assert entered.wait(5)
        second.start()
        assert booking_root.wait(5)
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with factory() as observer:
                if pids["scheduler"] in (observer.scalar(text("select pg_blocking_pids(:pid)"), {"pid": pids["booking"]}) or []):
                    break
        else:
            raise AssertionError("Booking did not block behind real Scheduler Trip lock")
    finally:
        release.set()
        first.join(5)
        second.join(5)
        event.remove(test_engine, "before_cursor_execute", capture)
    assert not first.is_alive() and not second.is_alive()
    observed = [outcomes.get_nowait(), outcomes.get_nowait()]
    assert True in observed and any(isinstance(value, StalePlanRevision) for value in observed)
    with factory() as db:
        assert db.get(DecisionRound, round_id).status == "closed"
        assert db.get(Plan, seed["plan"]).revision == 1
        # The Scheduler's existing keep settlement is authoritative; the
        # rejected Booking must not overwrite its resulting settled state.
        assert db.get(PlanItem, seed["item"]).settledness == "settled"
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 0


def test_expiry_and_proposal_decision_terminal_orders_do_not_mix_transitions(test_engine):
    """Case 1: each real writer rechecks the Proposal after root serialization."""
    factory = _factory(test_engine)
    expiry = _seed(test_engine)
    decision_token = f"expiry-decision-{expiry['trip']}"
    with factory.begin() as db:
        db.add(AuthSession(user_id=expiry["participant_user"], token_hash=auth_service.token_hash(decision_token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        proposal = ChangeProposal(plan_item_id=expiry["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=expiry["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
        db.add(proposal); db.flush()
        db.add(ProposalDecision(proposal_id=proposal.id, trip_membership_id=expiry["participant"], status="pending"))
        proposal_id = proposal.id
    assert expire_due_proposal(session_factory=factory, trip_id=expiry["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc)) is True
    scope = PreliminaryActorScope(trip_id=expiry["trip"], membership_id=expiry["participant"], credential_kind="account", expected_user_id=expiry["participant_user"])
    no_op = protected_proposal_decision.execute_protected_proposal_decision(session_factory=factory, scope=scope, credential=CredentialProof.from_bearer_token(decision_token), command=protected_proposal_decision.ProtectedProposalDecision(proposal_id, "expired-decision", 1, "accepted"), keyring=protected_proposal_decision.StaticHmacKeyring(HmacSigningKey("race", b"race")))
    assert no_op.plan_revision == 1
    with factory() as db:
        assert db.get(ChangeProposal, proposal_id).status == "expired"
        assert db.get(Plan, expiry["plan"]).revision == 1
        assert db.scalar(select(ProposalDecision.status).where(ProposalDecision.proposal_id == proposal_id)) == "pending"

    accepted = _seed(test_engine)
    token = f"accepted-decision-{accepted['trip']}"
    with factory.begin() as db:
        db.add(AuthSession(user_id=accepted["participant_user"], token_hash=auth_service.token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        proposal = ChangeProposal(plan_item_id=accepted["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=accepted["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
        db.add(proposal); db.flush(); db.add(ProposalDecision(proposal_id=proposal.id, trip_membership_id=accepted["participant"], status="pending")); proposal_id = proposal.id
    scope = PreliminaryActorScope(trip_id=accepted["trip"], membership_id=accepted["participant"], credential_kind="account", expected_user_id=accepted["participant_user"])
    result = protected_proposal_decision.execute_protected_proposal_decision(session_factory=factory, scope=scope, credential=CredentialProof.from_bearer_token(token), command=protected_proposal_decision.ProtectedProposalDecision(proposal_id, "decision-before-expiry", 0, "accepted"), keyring=protected_proposal_decision.StaticHmacKeyring(HmacSigningKey("race", b"race")))
    assert result.plan_revision == 1
    assert expire_due_proposal(session_factory=factory, trip_id=accepted["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc)) is False


def test_extension_and_settlement_orders_recheck_round_eligibility(test_engine):
    """Case 2: extension removes due eligibility; closed rounds cannot reopen."""
    factory = _factory(test_engine)
    first = _seed(test_engine)
    with factory.begin() as db:
        round_ = DecisionRound(plan_item_id=first["item"], kind="normal", reason="race", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
        db.add(round_); db.flush(); round_id = round_.id
    extended = organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(first), credential=CredentialProof.from_bearer_token(first["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.ROUND_EXTEND_OPERATION, round_id, "extend-first", 0), keyring=_keyring())
    assert extended.plan_revision == 1
    assert settle_due_round(session_factory=factory, trip_id=first["trip"], round_id=round_id, now=datetime.now(timezone.utc)) is False
    with factory() as db:
        assert db.get(DecisionRound, round_id).status == "open" and db.get(Plan, first["plan"]).revision == 1

    second = _seed(test_engine)
    with factory.begin() as db:
        round_ = DecisionRound(plan_item_id=second["item"], kind="normal", reason="race", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open")
        db.add(round_); db.flush(); round_id = round_.id
    assert settle_due_round(session_factory=factory, trip_id=second["trip"], round_id=round_id, now=datetime.now(timezone.utc)) is True
    with pytest.raises(legacy_organizer.NothingToDo):
        organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(second), credential=CredentialProof.from_bearer_token(second["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.ROUND_EXTEND_OPERATION, round_id, "extend-after", 1), keyring=_keyring())
    with factory() as db:
        assert db.get(DecisionRound, round_id).status == "closed" and db.get(Plan, second["plan"]).revision == 1


def test_escalation_and_expiry_orders_preserve_terminal_proposal_state(test_engine):
    """Case 3: same-Trip escalation and server expiry are serialized facts."""
    factory = _factory(test_engine)
    first = _seed(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=first["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=first["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
        db.add(proposal); db.flush(); proposal_id = proposal.id
    escalated = organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(first), credential=CredentialProof.from_bearer_token(first["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_ESCALATE_OPERATION, proposal_id, "escalate-first", 0), keyring=_keyring())
    replay = organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(first), credential=CredentialProof.from_bearer_token(first["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_ESCALATE_OPERATION, proposal_id, "escalate-first", 0), keyring=_keyring())
    assert escalated.payload == replay.payload and replay.replayed
    assert expire_due_proposal(session_factory=factory, trip_id=first["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc)) is True
    with factory() as db:
        assert db.get(ChangeProposal, proposal_id).status == "expired"
        assert db.get(Plan, first["plan"]).revision == 2
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == first["trip"]).count() == 1

    second = _seed(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=second["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=second["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1))
        db.add(proposal); db.flush(); proposal_id = proposal.id
    assert expire_due_proposal(session_factory=factory, trip_id=second["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc)) is True
    with pytest.raises(legacy_organizer.NothingToDo):
        organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(second), credential=CredentialProof.from_bearer_token(second["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_ESCALATE_OPERATION, proposal_id, "escalate-after", 1), keyring=_keyring())
    with factory() as db:
        assert db.get(ChangeProposal, proposal_id).status == "expired" and db.get(Plan, second["plan"]).revision == 1


def test_deadlock_and_booking_orders_reject_stale_plan_content_writer(test_engine):
    """Case 4: a real booking cannot overwrite an organizer deadlock result."""
    factory = _factory(test_engine)
    first = _seed(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=first["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=first["guest"], status="escalated", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add(proposal); db.flush(); proposal_id = proposal.id
    organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(first), credential=CredentialProof.from_bearer_token(first["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_DEADLOCK_OPERATION, proposal_id, "deadlock-first", 0, "clear"), keyring=_keyring())
    with pytest.raises(StalePlanRevision):
        protected_booking.execute_protected_booking(session_factory=factory, scope=_scope(first), credential=CredentialProof.from_bearer_token(first["token"]), command=protected_booking.ProtectedBooking(first["item"], "booking-after-deadlock", 0, True), keyring=protected_booking.StaticHmacKeyring(HmacSigningKey("race", b"race")))
    with factory() as db:
        assert db.get(PlanItem, first["item"]).settledness == "removed" and db.get(Plan, first["plan"]).revision == 1

    second = _seed(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=second["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=second["guest"], status="escalated", deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        db.add(proposal); db.flush(); proposal_id = proposal.id
    protected_booking.execute_protected_booking(session_factory=factory, scope=_scope(second), credential=CredentialProof.from_bearer_token(second["token"]), command=protected_booking.ProtectedBooking(second["item"], "booking-first", 0, True), keyring=protected_booking.StaticHmacKeyring(HmacSigningKey("race", b"race")))
    with pytest.raises(StalePlanRevision):
        organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(second), credential=CredentialProof.from_bearer_token(second["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_DEADLOCK_OPERATION, proposal_id, "deadlock-after", 0, "keep"), keyring=_keyring())
    with factory() as db:
        assert db.get(PlanItem, second["item"]).settledness == "booked" and db.get(Plan, second["plan"]).revision == 1


def test_real_overlap_expiry_blocks_then_stales_proposal_decision(test_engine):
    seed = _seed(test_engine); factory = _factory(test_engine); token = f"overlap-decision-{seed['trip']}"
    with factory.begin() as db:
        db.add(AuthSession(user_id=seed["participant_user"], token_hash=auth_service.token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1)); db.add(proposal); db.flush(); db.add(ProposalDecision(proposal_id=proposal.id, trip_membership_id=seed["participant"], status="pending")); proposal_id = proposal.id
    scope = PreliminaryActorScope(trip_id=seed["trip"], membership_id=seed["participant"], credential_kind="account", expected_user_id=seed["participant_user"])
    outcomes, pids = _real_writer_overlap(test_engine,
        lambda: expire_due_proposal(session_factory=factory, trip_id=seed["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc)),
        lambda: protected_proposal_decision.execute_protected_proposal_decision(session_factory=factory, scope=scope, credential=CredentialProof.from_bearer_token(token), command=protected_proposal_decision.ProtectedProposalDecision(proposal_id, "overlap-expiry-decision", 0, "accepted"), keyring=protected_proposal_decision.StaticHmacKeyring(HmacSigningKey("race", b"race"))),
    )
    assert pids["first"] != pids["second"] and True in outcomes and any(isinstance(x, StalePlanRevision) for x in outcomes)
    with factory() as db: assert db.get(ChangeProposal, proposal_id).status == "expired" and db.get(Plan, seed["plan"]).revision == 1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 0


def test_real_overlap_extension_blocks_then_scheduler_rechecks_deadline(test_engine):
    seed = _seed(test_engine); factory = _factory(test_engine)
    with factory.begin() as db:
        round_ = DecisionRound(plan_item_id=seed["item"], kind="normal", reason="overlap", options=[{"id": "keep", "title": "Keep", "patch": {}}], deadline=datetime.now(timezone.utc) - timedelta(minutes=1), status="open"); db.add(round_); db.flush(); round_id = round_.id
    outcomes, pids = _real_writer_overlap(test_engine,
        lambda: organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.ROUND_EXTEND_OPERATION, round_id, "overlap-extend", 0), keyring=_keyring()),
        lambda: settle_due_round(session_factory=factory, trip_id=seed["trip"], round_id=round_id, now=datetime.now(timezone.utc)),
    )
    assert pids["first"] != pids["second"] and False in outcomes
    with factory() as db: assert db.get(DecisionRound, round_id).status == "open" and db.get(Plan, seed["plan"]).revision == 1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1


def test_real_overlap_escalation_blocks_then_expiry_commits_terminal_state(test_engine):
    seed = _seed(test_engine); factory = _factory(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="waiting_affected_members", deadline=datetime.now(timezone.utc) - timedelta(minutes=1)); db.add(proposal); db.flush(); proposal_id = proposal.id
    outcomes, pids = _real_writer_overlap(test_engine,
        lambda: organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_ESCALATE_OPERATION, proposal_id, "overlap-escalate", 0), keyring=_keyring()),
        lambda: expire_due_proposal(session_factory=factory, trip_id=seed["trip"], proposal_id=proposal_id, now=datetime.now(timezone.utc)),
    )
    assert pids["first"] != pids["second"] and True in outcomes
    with factory() as db: assert db.get(ChangeProposal, proposal_id).status == "expired" and db.get(Plan, seed["plan"]).revision == 2 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1


def test_real_overlap_deadlock_blocks_then_stales_booking(test_engine):
    seed = _seed(test_engine); factory = _factory(test_engine)
    with factory.begin() as db:
        proposal = ChangeProposal(plan_item_id=seed["item"], action_type="edit", before_json={}, after_json={}, requested_by_membership_id=seed["guest"], status="escalated", deadline=datetime.now(timezone.utc) + timedelta(hours=1)); db.add(proposal); db.flush(); proposal_id = proposal.id
    outcomes, pids = _real_writer_overlap(test_engine,
        lambda: organizer_actions.execute_protected_organizer_action(session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]), command=organizer_actions.ProtectedOrganizerAction(organizer_actions.PROPOSAL_DEADLOCK_OPERATION, proposal_id, "overlap-deadlock", 0, "clear"), keyring=_keyring()),
        lambda: protected_booking.execute_protected_booking(session_factory=factory, scope=_scope(seed), credential=CredentialProof.from_bearer_token(seed["token"]), command=protected_booking.ProtectedBooking(seed["item"], "overlap-booking", 0, True), keyring=protected_booking.StaticHmacKeyring(HmacSigningKey("race", b"race"))),
    )
    assert pids["first"] != pids["second"] and any(isinstance(x, StalePlanRevision) for x in outcomes)
    with factory() as db: assert db.get(PlanItem, seed["item"]).settledness == "removed" and db.get(Plan, seed["plan"]).revision == 1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1
