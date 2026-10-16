"""Real PostgreSQL acceptance coverage for PR-06B.1A.2 Membership lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.api import main as api
from app.db import session as db_session
from app.db.models import (
    AuthSession,
    ChangeProposal,
    DecisionRound,
    GuestSession,
    InviteLink,
    MutationIdempotency,
    Plan,
    PlanChange,
    PlanItem,
    ProposalDecision,
    Trip,
    TripMembership,
    User,
    Vote,
)
from app.domain import auth
from app.domain.decisions import orchestrator as orch
from app.domain.trips import service as trip_service
from app.domain.transactions import (
    CredentialProof,
    HmacSigningKey,
    MembershipNotAuthorized,
    PlanTransactionKernel,
    PreliminaryActorScope,
)
from app.domain.transactions import IdempotencyKeyReused
from app.domain.transactions import membership_quarantine
from app.domain.transactions.membership_join import (
    GuestJoinAccountIdentityRejected,
    LegacyAccountIdentityDenied,
    MembershipQuarantined,
    join_account_invite_under_trip_root,
    join_guest_invite_under_trip_root,
    join_invite_under_trip_root,
)
from app.domain.transactions.membership_lifecycle import MembershipChangePendingDecision
from app.domain.transactions.membership_remove import (
    ProtectedMembershipRemove,
    execute_protected_membership_remove,
)
from app.domain.transactions.trip_preferences_common import StaticHmacKeyring


_seed_records: list[tuple[str, tuple[str, ...]]] = []


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _keyring() -> StaticHmacKeyring:
    return StaticHmacKeyring(HmacSigningKey("pr06b1a2", b"membership-lifecycle-test-key"))


def _seed(engine):
    factory = _factory(engine)
    stamp = datetime.now(timezone.utc).timestamp()
    account_token = f"account-join-{stamp}"
    target_token = f"target-account-{stamp}"
    guest_token = f"gst-guest-{stamp}"
    with factory.begin() as db:
        organizer_user = User(
            name="Organizer",
            email=f"organizer-{stamp}@example.test",
            password_hash=auth.hash_password("correct-horse"),
        )
        account_user = User(
            name="Account Joiner",
            email=f"account-{stamp}@example.test",
            password_hash=auth.hash_password("correct-horse"),
        )
        target_user = User(
            name="Target",
            email=f"target-{stamp}@example.test",
            password_hash=auth.hash_password("correct-horse"),
        )
        db.add_all((organizer_user, account_user, target_user))
        db.flush()
        trip = Trip(name="Lifecycle", destination="Chicago", created_by_user_id=organizer_user.id)
        db.add(trip)
        db.flush()
        organizer = TripMembership(
            trip_id=trip.id, user_id=organizer_user.id, role="organizer", status="joined"
        )
        target = TripMembership(
            trip_id=trip.id, user_id=target_user.id, role="participant", status="joined"
        )
        guest = TripMembership(
            trip_id=trip.id,
            guest_display_name="Guest",
            role="participant",
            status="joined",
        )
        plan = Plan(trip_id=trip.id)
        db.add_all((organizer, target, guest, plan))
        db.flush()
        item = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=datetime(2027, 1, 1).date(),
            start_hour=10,
            title="Museum",
            place="Chicago",
        )
        db.add(item)
        db.add_all(
            (
                AuthSession(
                    user_id=organizer_user.id,
                    token_hash=auth.token_hash(f"organizer-{stamp}"),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
                AuthSession(
                    user_id=account_user.id,
                    token_hash=auth.token_hash(account_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
                AuthSession(
                    user_id=target_user.id,
                    token_hash=auth.token_hash(target_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
                GuestSession(
                    membership_id=guest.id,
                    trip_id=trip.id,
                    token_hash=auth.token_hash(guest_token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                ),
            )
        )
        db.flush()
        _seed_records.append((trip.id, (organizer_user.id, account_user.id, target_user.id)))
        return {
            "trip": trip.id,
            "plan": plan.id,
            "item": item.id,
            "organizer": organizer.id,
            "organizer_user": organizer_user.id,
            "organizer_token": f"organizer-{stamp}",
            "account": account_user.id,
            "account_email": account_user.email,
            "account_token": account_token,
            "target": target.id,
            "target_user": target_user.id,
            "target_token": target_token,
            "guest": guest.id,
            "guest_token": guest_token,
        }


@pytest.fixture(autouse=True)
def _cleanup_lifecycle_seed_data(test_engine):
    """These real-PG tests use independent committed writer Sessions.

    Delete their complete ownership graph after every test so the session-scoped
    test engine cannot leak a previous lifecycle scenario into another test.
    """

    _seed_records.clear()
    yield
    records, _seed_records[:] = list(_seed_records), []
    if not records:
        return
    with _factory(test_engine).begin() as db:
        for trip_id, user_ids in records:
            plan_ids = select(Plan.id).where(Plan.trip_id == trip_id)
            item_ids = select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids))
            round_ids = select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(item_ids))
            proposal_ids = select(ChangeProposal.id).where(ChangeProposal.plan_item_id.in_(item_ids))
            db.execute(delete(MutationIdempotency).where(MutationIdempotency.trip_id == trip_id))
            db.execute(delete(GuestSession).where(GuestSession.trip_id == trip_id))
            db.execute(delete(InviteLink).where(InviteLink.trip_id == trip_id))
            db.execute(delete(Vote).where(Vote.round_id.in_(round_ids)))
            db.execute(delete(ProposalDecision).where(ProposalDecision.proposal_id.in_(proposal_ids)))
            db.execute(delete(DecisionRound).where(DecisionRound.plan_item_id.in_(item_ids)))
            db.execute(delete(ChangeProposal).where(ChangeProposal.plan_item_id.in_(item_ids)))
            db.execute(delete(PlanChange).where(PlanChange.plan_id.in_(plan_ids)))
            db.execute(delete(PlanItem).where(PlanItem.plan_id.in_(plan_ids)))
            db.execute(delete(Plan).where(Plan.id.in_(plan_ids)))
            db.execute(delete(TripMembership).where(TripMembership.trip_id == trip_id))
            db.execute(delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
            db.execute(delete(Trip).where(Trip.id == trip_id))
            db.execute(delete(User).where(User.id.in_(user_ids)))


def _invite(engine, trip_id: str, token: str) -> None:
    with _factory(engine).begin() as db:
        db.add(InviteLink(trip_id=trip_id, token_hash=auth.token_hash(token), is_primary=True))


def _scope(seed):
    return PreliminaryActorScope(
        trip_id=seed["trip"],
        membership_id=seed["organizer"],
        credential_kind="account",
        expected_user_id=seed["organizer_user"],
    )


def test_account_join_is_session_bound_and_rejoin_creates_new_incarnation(test_engine):
    seed = _seed(test_engine)
    first_token = f"account-first-{seed['trip']}"
    _invite(test_engine, seed["trip"], first_token)
    factory = _factory(test_engine)

    first = join_account_invite_under_trip_root(
        session_factory=factory,
        token=first_token,
        account_token=seed["account_token"],
        account_user_id=seed["account"],
    )
    replay = join_account_invite_under_trip_root(
        session_factory=factory,
        token=first_token,
        account_token=seed["account_token"],
        account_user_id=seed["account"],
    )
    assert replay.membership.id == first.membership.id
    with factory.begin() as db:
        trip = db.get(Trip, seed["trip"])
        plan = db.get(Plan, seed["plan"])
        assert trip.planning_facts_revision == plan.revision == 1
        db.get(TripMembership, first.membership.id).status = "removed"

    rejoin_token = f"account-rejoin-{seed['trip']}"
    _invite(test_engine, seed["trip"], rejoin_token)
    second = join_account_invite_under_trip_root(
        session_factory=factory,
        token=rejoin_token,
        account_token=seed["account_token"],
        account_user_id=seed["account"],
    )
    assert second.membership.id != first.membership.id
    with factory() as db:
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 2
        assert db.get(Plan, seed["plan"]).revision == 2


def test_concurrent_account_rejoin_creates_one_incarnation_and_one_revision(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    token = f"concurrent-account-{seed['trip']}"
    _invite(test_engine, seed["trip"], token)
    results: Queue[object] = Queue()
    errors: Queue[BaseException] = Queue()

    def join() -> None:
        try:
            results.put(
                join_account_invite_under_trip_root(
                    session_factory=factory,
                    token=token,
                    account_token=seed["account_token"],
                    account_user_id=seed["account"],
                ).membership.id
            )
        except BaseException as exc:  # test harness transports thread failures
            errors.put(exc)

    workers = [Thread(target=join) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert errors.empty()
    assert len({results.get_nowait(), results.get_nowait()}) == 1
    with factory() as db:
        memberships = db.scalars(
            select(TripMembership).where(
                TripMembership.trip_id == seed["trip"],
                TripMembership.user_id == seed["account"],
            )
        ).all()
        assert len(memberships) == 1
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 1
        assert db.get(Plan, seed["plan"]).revision == 1


def test_account_join_rejects_legacy_identity_and_guest_email_identity(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    token = f"legacy-{seed['trip']}"
    _invite(test_engine, seed["trip"], token)
    with factory.begin() as db:
        user = db.get(User, seed["account"])
        user.password_hash = None
    with pytest.raises(LegacyAccountIdentityDenied):
        join_account_invite_under_trip_root(
            session_factory=factory,
            token=token,
            account_token=seed["account_token"],
            account_user_id=seed["account"],
        )
    with pytest.raises(GuestJoinAccountIdentityRejected):
        join_invite_under_trip_root(
            session_factory=factory,
            token=token,
            display_name="not-an-account",
            email="claimed@example.test",
        )


def test_trip_service_exposes_no_direct_guest_membership_writer():
    """Guest joins have one public domain writer: the Trip-root transaction."""

    assert not hasattr(trip_service, "join_invite")


def test_decision_window_rejects_normal_join_and_removal_without_effect(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    token = f"window-{seed['trip']}"
    _invite(test_engine, seed["trip"], token)
    with factory.begin() as db:
        db.add(
            DecisionRound(
                plan_item_id=seed["item"],
                options=[{"id": "keep", "title": "Keep", "patch": {}}],
                deadline=datetime.now(timezone.utc) + timedelta(hours=1),
                status="open",
                electorate_count=3,
            )
        )
    with pytest.raises(MembershipChangePendingDecision):
        join_guest_invite_under_trip_root(session_factory=factory, token=token, display_name="Blocked")
    with pytest.raises(MembershipChangePendingDecision):
        execute_protected_membership_remove(
            session_factory=factory,
            scope=_scope(seed),
            credential=CredentialProof.from_bearer_token(seed["organizer_token"]),
            command=ProtectedMembershipRemove(seed["target"], "window-remove", 0),
            keyring=_keyring(),
        )
    with factory() as db:
        assert db.get(TripMembership, seed["target"]).status == "joined"
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 0
        assert db.scalar(select(TripMembership.id).where(TripMembership.guest_display_name == "Blocked")) is None


def test_rounds_capture_opening_electorate_and_legacy_open_rounds_fail_closed(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        item = db.get(PlanItem, seed["item"])
        outcome = orch._do_round(
            db,
            item,
            db.get(Trip, seed["trip"]),
            "opening electorate regression",
            orch.Classification(orch.Path.ROUND, "Round", "Regression coverage"),
            kind="normal",
            patch={"title": "Updated museum"},
        )
        new_round = db.get(DecisionRound, outcome.round_id)
        assert new_round.electorate_count == 3
        new_round.status = "closed"

        legacy = DecisionRound(
            plan_item_id=seed["item"],
            kind="normal",
            options=[{"id": "keep", "title": "Keep", "patch": {}}],
            deadline=datetime.now(timezone.utc) - timedelta(minutes=1),
            status="open",
            electorate_count=None,
        )
        db.add(legacy)
        db.flush()
        db.add(Vote(round_id=legacy.id, trip_membership_id=seed["organizer"], option_id="keep"))
        # Simulate a post-opening roster change that must never redefine this
        # legacy round's denominator. Production lifecycle writers reject it.
        db.get(TripMembership, seed["target"]).status = "removed"
        legacy_id = legacy.id

    with factory.begin() as db:
        legacy = db.get(DecisionRound, legacy_id)
        assert legacy_id not in orch.settle_due_rounds(db)
        with pytest.raises(orch.LegacyElectorateUnavailable):
            orch.settle_round(db, legacy)
        payload = api._round_out(db, legacy)
        assert payload["total_members"] is None
        assert payload["electorate_count_available"] is False
        assert legacy.status == "open"


def test_closed_legacy_round_never_recomputes_its_historical_electorate(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    with factory.begin() as db:
        legacy = DecisionRound(
            plan_item_id=seed["item"],
            kind="normal",
            options=[{"id": "keep", "title": "Keep", "patch": {}}],
            deadline=datetime.now(timezone.utc) - timedelta(days=1),
            status="closed",
            winning_option_id="keep",
            settled_at=datetime.now(timezone.utc) - timedelta(days=1),
            electorate_count=None,
        )
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id
        db.get(TripMembership, seed["target"]).status = "removed"
    with factory.begin() as db:
        legacy = db.get(DecisionRound, legacy_id)
        assert orch.settle_round(db, legacy) == "keep"
        payload = api._round_out(db, legacy)
        assert payload["total_members"] is None
        assert payload["electorate_count_available"] is False


def test_quarantine_revokes_trip_access_credentials_and_invites_once(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    invite_token = f"quarantine-invite-{seed['trip']}"
    _invite(test_engine, seed["trip"], invite_token)
    # Attach a Guest credential to the target to exercise the required
    # containment effect even for malformed/legacy target data.
    target_guest_token = f"gst-target-{seed['trip']}"
    with factory.begin() as db:
        round_ = DecisionRound(
                plan_item_id=seed["item"],
                options=[{"id": "keep", "title": "Keep", "patch": {}}],
                deadline=datetime.now(timezone.utc) + timedelta(hours=1),
                status="open",
                electorate_count=3,
            )
        db.add(round_)
        db.flush()
        db.add(Vote(round_id=round_.id, trip_membership_id=seed["target"], option_id="keep"))
        proposal = ChangeProposal(
            plan_item_id=seed["item"],
            action_type="edit",
            before_json={},
            after_json={},
            requested_by_membership_id=seed["organizer"],
            status="waiting_affected_members",
            deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(proposal)
        db.flush()
        db.add(
            ProposalDecision(
                proposal_id=proposal.id,
                trip_membership_id=seed["target"],
                status="pending",
            )
        )
        proposal_id = proposal.id
        round_id = round_.id
        db.add(
            GuestSession(
                membership_id=seed["target"],
                trip_id=seed["trip"],
                token_hash=auth.token_hash(target_guest_token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
    command = membership_quarantine.ProtectedMembershipQuarantine(
        seed["target"], "security_incident", "quarantine-once", 0
    )
    first = membership_quarantine.execute_protected_membership_quarantine(
        session_factory=factory,
        scope=_scope(seed),
        credential=CredentialProof.from_bearer_token(seed["organizer_token"]),
        command=command,
        keyring=_keyring(),
    )
    replay = membership_quarantine.execute_protected_membership_quarantine(
        session_factory=factory,
        scope=_scope(seed),
        credential=CredentialProof.from_bearer_token(seed["organizer_token"]),
        command=command,
        keyring=_keyring(),
    )
    assert first.payload == replay.payload == {"quarantined": True}
    assert replay.replayed is True
    with pytest.raises(IdempotencyKeyReused):
        membership_quarantine.execute_protected_membership_quarantine(
            session_factory=factory,
            scope=_scope(seed),
            credential=CredentialProof.from_bearer_token(seed["organizer_token"]),
            command=membership_quarantine.ProtectedMembershipQuarantine(
                seed["target"], "abuse", "quarantine-once", 0
            ),
            keyring=_keyring(),
        )
    with factory() as db:
        target = db.get(TripMembership, seed["target"])
        assert target.status == "joined" and target.quarantined_at is not None
        assert target.quarantine_reason == "security_incident"
        assert db.scalar(select(AuthSession.revoked_at).where(AuthSession.user_id == seed["organizer_user"])) is None
        assert db.scalar(select(GuestSession.revoked_at).where(GuestSession.token_hash == auth.token_hash(target_guest_token))) is not None
        assert db.scalar(select(InviteLink.revoked_at).where(InviteLink.token_hash == auth.token_hash(invite_token))) is not None
        assert db.get(Trip, seed["trip"]).planning_facts_revision == 1
        assert db.get(Plan, seed["plan"]).revision == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed["trip"]).count() == 1
        open_round = db.scalar(select(DecisionRound).where(DecisionRound.plan_item_id == seed["item"]))
        assert open_round.status == "open" and open_round.electorate_count == 3
        assert db.scalar(select(Vote.trip_membership_id).where(Vote.round_id == round_id)) == seed["target"]
        assert db.scalar(
            select(ProposalDecision.trip_membership_id).where(
                ProposalDecision.proposal_id == proposal_id
            )
        ) == seed["target"]
    with pytest.raises(auth.AuthRequired):
        with factory() as db:
            auth.membership_for_guest_token(db, target_guest_token)
    with pytest.raises(auth.TripMembershipRequired):
        with factory() as db:
            auth.membership_for_trip(db, db.get(User, seed["target_user"]), seed["trip"])
    with pytest.raises(MembershipNotAuthorized):
        PlanTransactionKernel(factory).execute(
            scope=PreliminaryActorScope(
                trip_id=seed["trip"],
                membership_id=seed["target"],
                credential_kind="account",
                expected_user_id=seed["target_user"],
            ),
            credential=CredentialProof.from_bearer_token(seed["target_token"]),
            operation=lambda _context: None,
        )
    with pytest.raises(trip_service.InviteNotFound):
        join_guest_invite_under_trip_root(
            session_factory=factory, token=invite_token, display_name="Blocked by containment"
        )
    replacement_invite = f"replacement-after-quarantine-{seed['trip']}"
    _invite(test_engine, seed["trip"], replacement_invite)
    with pytest.raises(MembershipQuarantined):
        join_account_invite_under_trip_root(
            session_factory=factory,
            token=replacement_invite,
            account_token=seed["target_token"],
            account_user_id=seed["target_user"],
        )


def test_account_and_guest_join_routes_enforce_the_public_identity_contract(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    guest_invite = f"guest-route-{seed['trip']}"
    account_invite = f"account-route-{seed['trip']}"
    _invite(test_engine, seed["trip"], guest_invite)
    _invite(test_engine, seed["trip"], account_invite)
    api.app.dependency_overrides[api.get_session] = lambda: factory()
    try:
        with TestClient(api.app) as client:
            rejected = client.post(
                f"/api/invites/{guest_invite}/join",
                json={"display_name": "No Claim", "email": seed["account_email"]},
            )
            guest = client.post(
                f"/api/invites/{guest_invite}/join", json={"display_name": "Guest Route"}
            )
            account = client.post(
                f"/api/invites/{account_invite}/account-join",
                headers={"Authorization": f"Bearer {seed['account_token']}"},
                json={},
            )
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "account_join_requires_authenticated_session"
        assert guest.status_code == 200 and guest.json()["guest_token"].startswith("gst_")
        assert account.status_code == 200
    finally:
        api.app.dependency_overrides.clear()


def test_quarantine_route_requires_durable_headers_and_revokes_only_trip_access(
    test_engine, monkeypatch
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "pr06b1a2-quarantine-route-key")
    api.app.dependency_overrides[api.get_session] = lambda: factory()
    try:
        with TestClient(api.app) as client:
            base_headers = {
                "Authorization": f"Bearer {seed['organizer_token']}",
                "X-Trip-Id": seed["trip"],
            }
            missing = client.post(
                f"/api/trips/{seed['trip']}/members/{seed['target']}/quarantine",
                headers=base_headers,
                json={"reason": "abuse"},
            )
            quarantined = client.post(
                f"/api/trips/{seed['trip']}/members/{seed['target']}/quarantine",
                headers={
                    **base_headers,
                    "Idempotency-Key": "quarantine-route",
                    "X-Expected-Trip-Facts-Revision": "0",
                },
                json={"reason": "abuse"},
            )
            denied_trip = client.get(
                f"/api/trips/{seed['trip']}",
                headers={
                    "Authorization": f"Bearer {seed['target_token']}",
                    "X-Trip-Id": seed["trip"],
                },
            )
            account_still_valid = client.get(
                "/api/account", headers={"Authorization": f"Bearer {seed['target_token']}"}
            )
        assert missing.status_code == 428
        assert quarantined.status_code == 200 and quarantined.json() == {"quarantined": True}
        assert denied_trip.status_code == 403
        assert account_still_valid.status_code == 200
    finally:
        api.app.dependency_overrides.clear()
