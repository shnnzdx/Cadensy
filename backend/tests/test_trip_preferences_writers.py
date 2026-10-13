"""Real PostgreSQL vertical contracts for the four Trip planning-fact writers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Event, Lock, Thread
from time import monotonic

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.api import main as api
from app.db.models import AuthSession, GuestSession, MemberConstraint, MemberConstraintPrivate, MutationIdempotency, Plan, PlanItem, Preference, Trip, TripMembership, User
from app.domain import auth as auth_service
from app.domain.transactions import CredentialProof, DuplicatePlans, HmacSigningKey, PreliminaryActorScope, StalePlanRevision
from app.domain.transactions import UnsafeDurableOutcome
from app.domain.transactions import constraint_create, constraint_delete, constraint_update, preferences_save
from app.domain.transactions import booking as protected_booking
from app.domain.transactions.trip_durable import StaleTripPlanningFactsRevision, TripDurableCommandKernel
from app.domain.transactions.trip_preferences_common import StaticHmacKeyring


@dataclass(frozen=True)
class Seed:
    trip_id: str
    plan_id: str | None
    membership_id: str
    user_id: str | None
    token: str


_records: list[tuple[str, tuple[str, ...]]] = []


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(engine, *, plan: bool = False, item: bool = False) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    token = f"phase4-{stamp}"
    with _factory(engine).begin() as session:
        actor = User(name="Phase four actor", email=f"phase4-{stamp}@test.local")
        owner = User(name="Phase four owner", email=f"phase4-owner-{stamp}@test.local")
        session.add_all((actor, owner)); session.flush()
        trip = Trip(name="Phase four trip", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip); session.flush()
        membership = TripMembership(trip_id=trip.id, user_id=actor.id, role="participant", status="joined")
        session.add(membership)
        session.add(AuthSession(user_id=actor.id, token_hash=auth_service.token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        row = Plan(trip_id=trip.id) if plan else None
        if row is not None:
            session.add(row)
        session.flush()
        if item:
            assert row is not None
            session.add(PlanItem(
                plan_id=row.id, day_index=1, day_date=date(2026, 8, 15),
                start_hour=12.0, duration_min=60, title="Booked writer fixture", place="Test place",
            ))
            session.flush()
        _records.append((trip.id, (actor.id, owner.id)))
        return Seed(trip.id, row.id if row else None, membership.id, actor.id, token)


def _guest_seed(engine) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    with _factory(engine).begin() as session:
        owner = User(name="Phase four guest owner", email=f"phase4-guest-owner-{stamp}@test.local")
        session.add(owner); session.flush()
        trip = Trip(name="Phase four guest trip", destination="Chicago", created_by_user_id=owner.id)
        session.add(trip); session.flush()
        membership = TripMembership(trip_id=trip.id, guest_display_name="Guest", role="participant", status="joined")
        session.add(membership); session.flush()
        token = auth_service.start_guest_session(session, membership).token
        _records.append((trip.id, (owner.id,)))
        return Seed(trip.id, None, membership.id, None, token)


def _scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.membership_id,
        credential_kind="account" if seed.user_id is not None else "guest",
        expected_user_id=seed.user_id,
    )


def _proof(seed: Seed) -> CredentialProof:
    return CredentialProof.from_bearer_token(seed.token)


def _keyring():
    return StaticHmacKeyring(HmacSigningKey("phase4-v1", b"phase4-test-key"))


def _cleanup(engine):
    records, _records[:] = list(_records), []
    if not records: return
    trips = [record[0] for record in records]
    users = [user for _, pair in records for user in pair]
    with _factory(engine).begin() as session:
        session.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trips)).delete(synchronize_session=False)
        session.query(GuestSession).filter(GuestSession.trip_id.in_(trips)).delete(synchronize_session=False)
        session.query(MemberConstraintPrivate).filter(MemberConstraintPrivate.constraint_id.in_(select(MemberConstraint.id).where(MemberConstraint.trip_membership_id.in_(select(TripMembership.id).where(TripMembership.trip_id.in_(trips)))))).delete(synchronize_session=False)
        session.query(MemberConstraint).filter(MemberConstraint.trip_membership_id.in_(select(TripMembership.id).where(TripMembership.trip_id.in_(trips)))).delete(synchronize_session=False)
        session.query(Preference).filter(Preference.trip_membership_id.in_(select(TripMembership.id).where(TripMembership.trip_id.in_(trips)))).delete(synchronize_session=False)
        session.query(PlanItem).filter(PlanItem.plan_id.in_(select(Plan.id).where(Plan.trip_id.in_(trips)))).delete(synchronize_session=False)
        session.query(Plan).filter(Plan.trip_id.in_(trips)).delete(synchronize_session=False)
        session.query(TripMembership).filter(TripMembership.trip_id.in_(trips)).delete(synchronize_session=False)
        session.query(Trip).filter(Trip.id.in_(trips)).delete(synchronize_session=False)
        session.query(AuthSession).filter(AuthSession.user_id.in_(users)).delete(synchronize_session=False)
        session.query(User).filter(User.id.in_(users)).delete(synchronize_session=False)


@contextmanager
def _http_client(engine):
    session = _factory(engine)()
    api.app.dependency_overrides[api.get_session] = lambda: session
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()
        session.close()


@pytest.fixture(autouse=True)
def _cleanup_rows(test_engine):
    _records.clear()
    yield
    _cleanup(test_engine)


def test_create_replays_original_id_and_never_stores_private_text(test_engine):
    seed = _seed(test_engine)
    command = constraint_create.ProtectedConstraintCreate("create-key", 0, "dietary", {"required_tags": ["vegan"]}, "required", "private medical wording", "planning_only")
    first = constraint_create.execute_protected_constraint_create(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    replay = constraint_create.execute_protected_constraint_create(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    assert first.payload == replay.payload and replay.replayed is True
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        assert "private medical wording" not in repr(row.outcome_json)
        assert session.query(MemberConstraintPrivate).count() >= 1


def test_create_with_one_plan_advances_both_revisions(test_engine):
    seed = _seed(test_engine, plan=True)
    result = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("create-plan", 0, "walk_limit", {"max_km_per_day": 3}, "required", "", "planning_only"),
    )
    assert result.trip_planning_facts_revision == 1
    with _factory(test_engine)() as session:
        assert session.get(Plan, seed.plan_id).revision == 1


def test_update_empty_patch_is_genuine_noop_without_plan_refresh_effect(test_engine):
    seed = _seed(test_engine)
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("create-for-update", 0, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
    )
    result = constraint_update.execute_protected_constraint_update(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_update.ProtectedConstraintUpdate(created.payload["id"], "empty-patch", 1, {}),
    )
    assert result.replayed is False and result.trip_planning_facts_revision == 1
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1


def test_delete_replays_after_row_has_been_deleted(test_engine):
    seed = _seed(test_engine)
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("create-for-delete", 0, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
    )
    command = constraint_delete.ProtectedConstraintDelete(created.payload["id"], "delete-key", 1)
    first = constraint_delete.execute_protected_constraint_delete(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    replay = constraint_delete.execute_protected_constraint_delete(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    assert first.payload == {"deleted": True} and replay.replayed is True
    with _factory(test_engine)() as session:
        assert session.get(MemberConstraint, created.payload["id"]) is None


def test_preferences_save_is_minimal_replayable_and_updates_membership(test_engine):
    seed = _seed(test_engine)
    fields = {"preferred_start_date": None, "preferred_end_date": None, "available_start_date": None, "available_end_date": None, "ideal_budget": 15.0, "maximum_budget": 20.0, "currency": "USD", "budget_visibility": "planning_only", "travel_style": "food", "top_interests": ["food"]}
    command = preferences_save.ProtectedPreferencesSave("preferences-key", 0, fields)
    first = preferences_save.execute_protected_preferences_save(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    replay = preferences_save.execute_protected_preferences_save(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    assert first.payload == {"submitted": True} and replay.replayed is True
    with _factory(test_engine)() as session:
        assert session.get(TripMembership, seed.membership_id).status == "preferences_submitted"
        row = session.scalar(select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id))
        assert "ideal_budget" not in repr(row.outcome_json)


def _create_command(key: str = "outcome-create") -> constraint_create.ProtectedConstraintCreate:
    return constraint_create.ProtectedConstraintCreate(
        key, 0, "dietary", {"required_tags": ["vegan"]}, "required", "private", "planning_only"
    )


def _replace_outcome(engine, seed: Seed, outcome: object, *, idempotency_key: str) -> None:
    with _factory(engine).begin() as session:
        ledger = session.scalar(
            select(MutationIdempotency).where(
                MutationIdempotency.trip_id == seed.trip_id,
                MutationIdempotency.idempotency_key == idempotency_key,
            )
        )
        assert ledger is not None
        ledger.outcome_json = outcome


@pytest.mark.parametrize(
    "outcome",
    (
        {"id": "opaque", "conflicts": [{"constraint_id": "opaque", "code": "DIETARY", "text": "safe", "scope": "item", "item_id": "item", "item_title": "title", "day_date": "2026-02-31", "settledness": "loose"}]},
        {"id": "opaque", "conflicts": [{"constraint_id": "opaque", "code": "DIETARY", "text": "safe", "scope": "item", "item_id": "item", "item_title": "title", "day_date": None, "settledness": "loose", "private_note": "secret"}]},
        {"id": "opaque", "conflicts": [{"constraint_id": "opaque", "code": "DIETARY", "text": "safe", "scope": "elsewhere", "item_id": "item", "item_title": "title", "day_date": None, "settledness": "loose"}]},
        {"id": "opaque", "conflicts": [{"constraint_id": "opaque", "code": "DIETARY", "text": "safe", "scope": "item", "item_id": 1, "item_title": "title", "day_date": None, "settledness": "loose"}]},
        {"id": "opaque", "conflicts": [{"constraint_id": "opaque", "code": "DIETARY", "text": "safe", "scope": "item", "item_id": "item", "item_title": "title", "day_date": None}]},
        {"id": 7, "conflicts": []},
        {"id": "opaque", "conflicts": {"not": "a list"}},
    ),
)
def test_constraint_create_replay_rejects_tampered_nested_outcome_without_callback(test_engine, outcome):
    seed = _seed(test_engine)
    command = _create_command()
    constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
    )
    _replace_outcome(test_engine, seed, outcome, idempotency_key=command.idempotency_key)
    with _factory(test_engine)() as session:
        before_revision = session.get(Trip, seed.trip_id).planning_facts_revision
        before_constraints = session.query(MemberConstraint).count()
    with pytest.raises(UnsafeDurableOutcome):
        constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
        )
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == before_revision
        assert session.query(MemberConstraint).count() == before_constraints


def test_valid_update_outcome_replays_under_the_same_strict_codec(test_engine):
    seed = _seed(test_engine)
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=_create_command(), keyring=_keyring()
    )
    command = constraint_update.ProtectedConstraintUpdate(
        created.payload["id"], "outcome-update", 1, {"importance": "flexible"}
    )
    first = constraint_update.execute_protected_constraint_update(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
    )
    replay = constraint_update.execute_protected_constraint_update(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
    )
    assert first.payload == replay.payload == {"id": created.payload["id"], "conflicts": []}
    assert replay.replayed is True


@pytest.mark.parametrize(
    "writer, command_factory, outcome",
    (
        (
            "preferences",
            lambda _created: preferences_save.ProtectedPreferencesSave(
                "outcome-preferences", 0,
                {"preferred_start_date": None, "preferred_end_date": None, "available_start_date": None, "available_end_date": None, "ideal_budget": None, "maximum_budget": None, "currency": "USD", "budget_visibility": "planning_only", "travel_style": None, "top_interests": []},
            ),
            {"submitted": False},
        ),
        (
            "preferences-extra",
            lambda _created: preferences_save.ProtectedPreferencesSave(
                "outcome-preferences-extra", 0,
                {"preferred_start_date": None, "preferred_end_date": None, "available_start_date": None, "available_end_date": None, "ideal_budget": None, "maximum_budget": None, "currency": "USD", "budget_visibility": "planning_only", "travel_style": None, "top_interests": []},
            ),
            {"submitted": True, "ideal_budget": 1},
        ),
        (
            "delete",
            lambda created: constraint_delete.ProtectedConstraintDelete(created.payload["id"], "outcome-delete", 1),
            {"deleted": False},
        ),
        (
            "delete-extra",
            lambda created: constraint_delete.ProtectedConstraintDelete(created.payload["id"], "outcome-delete-extra", 1),
            {"deleted": True, "diagnostic": "secret"},
        ),
    ),
)
def test_literal_outcomes_reject_tampered_replay(test_engine, writer, command_factory, outcome):
    seed = _seed(test_engine)
    created = None
    if writer.startswith("delete"):
        created = constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=_create_command(), keyring=_keyring()
        )
    command = command_factory(created)
    execute = preferences_save.execute_protected_preferences_save if writer.startswith("preferences") else constraint_delete.execute_protected_constraint_delete
    execute(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())
    _replace_outcome(test_engine, seed, outcome, idempotency_key=command.idempotency_key)
    with pytest.raises(UnsafeDurableOutcome):
        execute(session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring())


def test_unsupported_historical_outcome_contract_fails_closed_without_a_callback(test_engine):
    seed = _seed(test_engine)
    command = _create_command("outcome-version")
    constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
    )
    with _factory(test_engine).begin() as session:
        ledger = session.scalar(select(MutationIdempotency).where(MutationIdempotency.idempotency_key == command.idempotency_key))
        assert ledger is not None
        ledger.outcome_contract_version = 999
    with pytest.raises(Exception) as error:
        constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
        )
    assert error.type.__name__ == "UnsupportedOutcomeContract"


def _headers(seed: Seed, *, key: str | None = None, revision: int | str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {seed.token}",
        "X-Trip-Id": seed.trip_id,
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    if revision is not None:
        headers["X-Expected-Trip-Facts-Revision"] = str(revision)
    return headers


def _constraint_body() -> dict[str, object]:
    return {
        "kind": "dietary",
        "params": {"required_tags": ["vegan"]},
        "importance": "required",
        "original_text": "private wording",
        "visibility": "planning_only",
    }


def _preference_body() -> dict[str, object]:
    return {
        "preferred_start_date": None, "preferred_end_date": None,
        "available_start_date": None, "available_end_date": None,
        "ideal_budget": None, "maximum_budget": None, "currency": "USD",
        "budget_visibility": "planning_only", "travel_style": None, "top_interests": [],
    }


def test_cors_exposes_trip_revision_headers_and_protected_create_status(test_engine, monkeypatch):
    seed = _seed(test_engine)
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "phase4-http-key")
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES", "1")
    origin = "http://localhost:5173"
    with _http_client(test_engine) as client:
        discovery = client.get(
            f"/api/trips/{seed.trip_id}/preferences/me",
            headers={**_headers(seed), "Origin": origin},
        )
        assert discovery.status_code == 200
        assert discovery.headers["x-trip-facts-revision"] == "0"
        assert discovery.headers["access-control-allow-origin"] == origin
        exposed = discovery.headers["access-control-expose-headers"].lower()
        assert "x-trip-facts-revision" in exposed and "x-current-trip-facts-revision" in exposed
        assert "authorization" not in exposed and "credential" not in exposed
        denied_origin = client.get(
            f"/api/trips/{seed.trip_id}/preferences/me",
            headers={**_headers(seed), "Origin": "https://untrusted.example"},
        )
        assert "access-control-allow-origin" not in denied_origin.headers

        first = client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(),
            headers={**_headers(seed, key="http-create", revision=0), "Origin": origin},
        )
        assert first.status_code == 200
        assert first.headers["x-trip-facts-revision"] == "1"
        assert "private wording" not in repr(first.json())
        replay = client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(),
            headers={**_headers(seed, key="http-create", revision=0), "Origin": origin},
        )
        assert replay.status_code == 200
        assert replay.json() == first.json()
        stale = client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(),
            headers={**_headers(seed, key="http-stale", revision=0), "Origin": origin},
        )
        assert stale.status_code == 409
        assert stale.headers["x-current-trip-facts-revision"] == "1"
        assert "x-current-trip-facts-revision" in stale.headers["access-control-expose-headers"].lower()


def test_feature_off_ignores_malformed_protected_headers_and_production_fails_closed(test_engine, monkeypatch):
    seed = _seed(test_engine)
    with _http_client(test_engine) as client:
        legacy = client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(),
            headers=_headers(seed, key="ignored", revision="not-an-integer"),
        )
        assert legacy.status_code == 200
    monkeypatch.setenv("CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES", "1")
    monkeypatch.setenv("APP_ENV", "production")
    with _http_client(test_engine) as client:
        response = client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(), headers=_headers(seed)
        )
        assert response.status_code == 503


def test_concurrent_constraint_creates_prove_trip_root_contention_and_one_commit(test_engine, monkeypatch):
    """Two real adapters contend on Trip FOR UPDATE; the loser observes staleness."""
    seed = _seed(test_engine)
    first_at_checkpoint = Event()
    release_first = Event()
    second_root_attempt = Event()
    checkpoint_count = Lock()
    calls = [0]
    backend_pids: list[int] = []
    original_checkpoint = TripDurableCommandKernel._checkpoint

    def checkpoint(self, stage):
        if stage != "after_domain_effect":
            return original_checkpoint(self, stage)
        with checkpoint_count:
            ordinal = calls[0]
            calls[0] += 1
        if ordinal == 0:
            first_at_checkpoint.set()
            assert release_first.wait(5), "test did not release the first Trip root lock"

    def observe_root_lock(connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lower()
        if "from trip" not in normalized or "for update" not in normalized:
            return
        pid = connection.connection.driver_connection.info.backend_pid
        if pid not in backend_pids:
            backend_pids.append(pid)
        if len(backend_pids) == 2:
            second_root_attempt.set()

    monkeypatch.setattr(TripDurableCommandKernel, "_checkpoint", checkpoint)
    event.listen(test_engine, "before_cursor_execute", observe_root_lock)
    first_result: list[object] = []
    second_result: list[object] = []
    try:
        def execute(key: str, tag: str, sink: list[object]) -> None:
            try:
                sink.append(constraint_create.execute_protected_constraint_create(
                    session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
                    command=constraint_create.ProtectedConstraintCreate(
                        key, 0, "dietary", {"required_tags": [tag]}, "required", "", "planning_only"
                    ),
                ))
            except Exception as exc:  # asserted after threads are joined
                sink.append(exc)

        first = Thread(target=execute, args=("concurrent-first", "vegan", first_result))
        second = Thread(target=execute, args=("concurrent-second", "vegetarian", second_result))
        first.start()
        assert first_at_checkpoint.wait(5), "first command did not acquire and retain the Trip root"
        second.start()
        assert second_root_attempt.wait(5), "second command never attempted Trip FOR UPDATE"
        assert len(backend_pids) >= 2 and backend_pids[0] != backend_pids[1]
        deadline = monotonic() + 5
        blockers: list[int] = []
        while monotonic() < deadline:
            with test_engine.connect() as observer:
                blockers = observer.scalar(text("select pg_blocking_pids(:pid)"), {"pid": backend_pids[1]})
            if backend_pids[0] in blockers:
                break
            Event().wait(0.01)
        assert backend_pids[0] in blockers
        release_first.set()
        first.join(5); second.join(5)
        assert not first.is_alive() and not second.is_alive()
    finally:
        release_first.set()
        event.remove(test_engine, "before_cursor_execute", observe_root_lock)

    assert len(first_result) == len(second_result) == 1
    assert getattr(first_result[0], "replayed", None) is False
    assert isinstance(second_result[0], StaleTripPlanningFactsRevision)
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
        membership_constraints = session.query(MemberConstraint).join(TripMembership).filter(TripMembership.trip_id == seed.trip_id).count()
        completed = session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count()
        assert membership_constraints == completed == 1


def test_constraint_writer_blocks_real_protected_booking_then_stales_it(test_engine, monkeypatch):
    """A Trip writer and the accepted Booking writer contend on the same root."""
    seed = _seed(test_engine, plan=True, item=True)
    first_at_checkpoint, release_first, booking_root_attempt = Event(), Event(), Event()
    seen_pids: list[int] = []
    calls, calls_lock = [0], Lock()

    def checkpoint(_self, stage):
        if stage != "after_domain_effect":
            return
        with calls_lock:
            ordinal = calls[0]
            calls[0] += 1
        if ordinal == 0:
            first_at_checkpoint.set()
            assert release_first.wait(5), "test did not release the Constraint transaction"

    def observe_trip_root(connection, _cursor, statement, _parameters, _context, _executemany):
        if "from trip" not in statement.lower() or "for update" not in statement.lower():
            return
        pid = connection.connection.driver_connection.info.backend_pid
        if pid not in seen_pids:
            seen_pids.append(pid)
        if len(seen_pids) >= 2:
            booking_root_attempt.set()

    monkeypatch.setattr(TripDurableCommandKernel, "_checkpoint", checkpoint)
    event.listen(test_engine, "before_cursor_execute", observe_trip_root)
    constraint_result: list[object] = []
    booking_result: list[object] = []
    try:
        def run_constraint():
            try:
                constraint_result.append(constraint_create.execute_protected_constraint_create(
                    session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
                    command=constraint_create.ProtectedConstraintCreate("cross-kernel-constraint", 0, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
                ))
            except Exception as exc:
                constraint_result.append(exc)

        def run_booking():
            try:
                with _factory(test_engine)() as session:
                    item_id = session.scalar(select(PlanItem.id).where(PlanItem.plan_id == seed.plan_id))
                booking_result.append(protected_booking.execute_protected_booking(
                    session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed),
                    keyring=protected_booking.StaticHmacKeyring(HmacSigningKey("phase4-v1", b"phase4-test-key")),
                    command=protected_booking.ProtectedBooking(item_id, "cross-kernel-booking", 0, True),
                ))
            except Exception as exc:
                booking_result.append(exc)

        constraint_thread = Thread(target=run_constraint)
        booking_thread = Thread(target=run_booking)
        constraint_thread.start()
        assert first_at_checkpoint.wait(5), "Constraint writer did not retain Trip root lock"
        booking_thread.start()
        assert booking_root_attempt.wait(5), "Booking writer did not attempt Trip FOR UPDATE"
        assert len(seen_pids) >= 2 and seen_pids[0] != seen_pids[1]
        deadline = monotonic() + 5
        blockers: list[int] = []
        while monotonic() < deadline:
            with test_engine.connect() as observer:
                blockers = observer.scalar(text("select pg_blocking_pids(:pid)"), {"pid": seen_pids[1]})
            if seen_pids[0] in blockers:
                break
            # The second listener runs just before its DBAPI query starts; this
            # bounded observer poll waits for PostgreSQL itself to report the
            # blocking relation, rather than treating scheduling as evidence.
            Event().wait(0.01)
        assert seen_pids[0] in blockers
        release_first.set()
        constraint_thread.join(5); booking_thread.join(5)
        assert not constraint_thread.is_alive() and not booking_thread.is_alive()
    finally:
        release_first.set()
        event.remove(test_engine, "before_cursor_execute", observe_trip_root)

    assert getattr(constraint_result[0], "replayed", None) is False
    assert isinstance(booking_result[0], StalePlanRevision)
    with _factory(test_engine)() as session:
        trip, plan = session.get(Trip, seed.trip_id), session.get(Plan, seed.plan_id)
        assert trip.planning_facts_revision == 1 and plan.revision == 1
        assert session.query(MemberConstraint).join(TripMembership).filter(TripMembership.trip_id == seed.trip_id).count() == 1
        assert session.query(MutationIdempotency).filter(
            MutationIdempotency.trip_id == seed.trip_id,
            MutationIdempotency.idempotency_key == "cross-kernel-booking",
        ).count() == 0
        assert session.query(MutationIdempotency).filter(
            MutationIdempotency.trip_id == seed.trip_id,
            MutationIdempotency.idempotency_key == "cross-kernel-constraint",
        ).count() == 1


@pytest.mark.parametrize("writer", ("create", "update", "delete", "preferences"))
def test_each_trip_writer_rejects_duplicate_plans_before_mutation_or_replay(test_engine, writer):
    seed = _seed(test_engine)
    base_constraint_id: str | None = None
    expected_revision = 0
    if writer in {"update", "delete"}:
        created = constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=_create_command(f"duplicate-base-{writer}"),
        )
        base_constraint_id = str(created.payload["id"])
        expected_revision = 1
    with _factory(test_engine).begin() as session:
        session.add_all((Plan(trip_id=seed.trip_id), Plan(trip_id=seed.trip_id)))
    if writer == "create":
        execute = lambda: constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=constraint_create.ProtectedConstraintCreate("duplicate-create", expected_revision, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
        )
    elif writer == "update":
        execute = lambda: constraint_update.execute_protected_constraint_update(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=constraint_update.ProtectedConstraintUpdate(base_constraint_id, "duplicate-update", expected_revision, {"importance": "flexible"}),
        )
    elif writer == "delete":
        execute = lambda: constraint_delete.execute_protected_constraint_delete(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=constraint_delete.ProtectedConstraintDelete(base_constraint_id, "duplicate-delete", expected_revision),
        )
    else:
        execute = lambda: preferences_save.execute_protected_preferences_save(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=preferences_save.ProtectedPreferencesSave("duplicate-preferences", expected_revision, _preference_body()),
        )
    with pytest.raises(DuplicatePlans):
        execute()
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == expected_revision
        assert session.query(MutationIdempotency).filter(
            MutationIdempotency.trip_id == seed.trip_id,
        ).count() == (1 if writer in {"update", "delete"} else 0)
        if base_constraint_id is not None:
            assert session.get(MemberConstraint, base_constraint_id) is not None


@pytest.mark.parametrize(
    "plan_mode, patch, expected_changed, expected_refresh, case_key",
    (
        ("none", {}, False, None, "empty-no-plan"),
        ("empty", {}, False, False, "empty-no-items"),
        ("items-clean", {}, True, True, "empty-refresh"),
        ("items-stale", {}, False, True, "empty-already-refresh"),
        ("none", {"importance": "required"}, False, None, "same-importance"),
        ("none", {"importance": "flexible"}, True, None, "new-importance"),
        ("none", {"params": {"tags": ["park"]}}, True, None, "new-params"),
        ("none", {"params": None}, False, None, "explicit-null"),
    ),
)
def test_constraint_update_noop_refresh_and_patch_matrix(test_engine, plan_mode, patch, expected_changed, expected_refresh, case_key):
    seed = _seed(test_engine, plan=plan_mode != "none", item=plan_mode.startswith("items"))
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("matrix-base", 0, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
    )
    with _factory(test_engine).begin() as session:
        if seed.plan_id is not None and plan_mode == "items-clean":
            session.get(Plan, seed.plan_id).needs_refresh = False
    result = constraint_update.execute_protected_constraint_update(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_update.ProtectedConstraintUpdate(created.payload["id"], f"matrix-{case_key}", 1, patch),
    )
    with _factory(test_engine)() as session:
        trip = session.get(Trip, seed.trip_id)
        plan = session.get(Plan, seed.plan_id) if seed.plan_id else None
        assert trip.planning_facts_revision == (2 if expected_changed else 1)
        assert result.replayed is False
        if plan is not None:
            assert plan.revision == (2 if expected_changed else 1)
            assert plan.needs_refresh is expected_refresh
        row = session.get(MemberConstraint, created.payload["id"])
        if "importance" in patch and patch["importance"] is not None:
            assert row.importance == patch["importance"]
        if "params" in patch and patch["params"] is not None:
            assert row.params == patch["params"]


def test_constraint_update_replay_is_stable_after_unrelated_change_and_stale_new_key_fails(test_engine):
    seed = _seed(test_engine)
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(), command=_create_command("edge-base"),
    )
    update = constraint_update.ProtectedConstraintUpdate(created.payload["id"], "edge-update", 1, {"importance": "flexible"})
    first = constraint_update.execute_protected_constraint_update(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=update, keyring=_keyring()
    )
    constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("edge-unrelated", 2, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
    )
    replay = constraint_update.execute_protected_constraint_update(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=update, keyring=_keyring()
    )
    assert replay.replayed is True and replay.trip_planning_facts_revision == first.trip_planning_facts_revision == 2
    with pytest.raises(Exception) as error:
        constraint_update.execute_protected_constraint_update(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=constraint_update.ProtectedConstraintUpdate(created.payload["id"], "edge-stale", 1, {"importance": "required"}),
        )
    assert error.type.__name__ == "StaleTripPlanningFactsRevision"


def test_preferences_fresh_keys_timestamp_replay_and_one_plan_revisions(test_engine):
    seed = _seed(test_engine)
    fields = _preference_body()
    first_command = preferences_save.ProtectedPreferencesSave("timestamp-first", 0, fields)
    first = preferences_save.execute_protected_preferences_save(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=first_command, keyring=_keyring()
    )
    with _factory(test_engine)() as session:
        pref = session.scalar(select(Preference).where(Preference.trip_membership_id == seed.membership_id))
        first_timestamp = pref.submitted_at
        assert session.get(TripMembership, seed.membership_id).status == "preferences_submitted"
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
    second = preferences_save.execute_protected_preferences_save(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=preferences_save.ProtectedPreferencesSave("timestamp-second", 1, fields),
    )
    with _factory(test_engine)() as session:
        pref = session.scalar(select(Preference).where(Preference.trip_membership_id == seed.membership_id))
        assert pref.submitted_at >= first_timestamp
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 2
    replay = preferences_save.execute_protected_preferences_save(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=first_command, keyring=_keyring()
    )
    assert first.trip_planning_facts_revision == replay.trip_planning_facts_revision == 1
    assert second.trip_planning_facts_revision == 2 and replay.replayed is True
    with pytest.raises(Exception) as error:
        preferences_save.execute_protected_preferences_save(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=preferences_save.ProtectedPreferencesSave("timestamp-stale", 1, fields),
        )
    assert error.type.__name__ == "StaleTripPlanningFactsRevision"

    planned = _seed(test_engine, plan=True)
    planned_result = preferences_save.execute_protected_preferences_save(
        session_factory=_factory(test_engine), scope=_scope(planned), credential=_proof(planned), keyring=_keyring(),
        command=preferences_save.ProtectedPreferencesSave("timestamp-plan", 0, fields),
    )
    with _factory(test_engine)() as session:
        assert planned_result.trip_planning_facts_revision == 1
        assert session.get(Trip, planned.trip_id).planning_facts_revision == 1
        assert session.get(Plan, planned.plan_id).revision == 1
        outcome = session.scalar(select(MutationIdempotency.outcome_json).where(MutationIdempotency.idempotency_key == "timestamp-plan"))
        assert outcome == {"submitted": True}


def test_constraint_delete_with_one_plan_advances_both_revisions_and_replay_does_not(test_engine):
    seed = _seed(test_engine, plan=True)
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(), command=_create_command("delete-plan-base"),
    )
    command = constraint_delete.ProtectedConstraintDelete(created.payload["id"], "delete-plan", 1)
    first = constraint_delete.execute_protected_constraint_delete(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(), command=command,
    )
    replay = constraint_delete.execute_protected_constraint_delete(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(), command=command,
    )
    assert first.trip_planning_facts_revision == replay.trip_planning_facts_revision == 2 and replay.replayed is True
    with _factory(test_engine)() as session:
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 2
        assert session.get(Plan, seed.plan_id).revision == 2
        assert session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 2


def _second_account_seed(engine, original: Seed) -> Seed:
    stamp = str(datetime.now(timezone.utc).timestamp())
    token = f"phase4-second-{stamp}"
    with _factory(engine).begin() as session:
        user = User(name="Phase four second", email=f"phase4-second-{stamp}@test.local")
        session.add(user); session.flush()
        membership = TripMembership(trip_id=original.trip_id, user_id=user.id, role="participant", status="joined")
        session.add(membership)
        session.add(AuthSession(user_id=user.id, token_hash=auth_service.token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        session.flush()
        trip_id, users = _records[-1]
        _records[-1] = (trip_id, (*users, user.id))
        return Seed(original.trip_id, original.plan_id, membership.id, user.id, token)


def test_cross_member_and_cross_trip_constraint_ownership_is_non_enumerating(test_engine):
    owner = _seed(test_engine)
    other = _second_account_seed(test_engine, owner)
    created_owner = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(owner), credential=_proof(owner), keyring=_keyring(), command=_create_command("owner-constraint"),
    )
    created_other = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(other), credential=_proof(other), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("other-constraint", 1, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
    )
    assert created_owner.replayed is False and created_other.replayed is False
    for command in (
        lambda: constraint_update.execute_protected_constraint_update(
            session_factory=_factory(test_engine), scope=_scope(other), credential=_proof(other), keyring=_keyring(),
            command=constraint_update.ProtectedConstraintUpdate(created_owner.payload["id"], "foreign-update", 2, {"importance": "flexible"}),
        ),
        lambda: constraint_delete.execute_protected_constraint_delete(
            session_factory=_factory(test_engine), scope=_scope(other), credential=_proof(other), keyring=_keyring(),
            command=constraint_delete.ProtectedConstraintDelete(created_owner.payload["id"], "foreign-delete", 2),
        ),
    ):
        with pytest.raises(Exception) as error:
            command()
        assert error.type.__name__ == "ProtectedConstraintMissing"
    foreign_trip = _seed(test_engine)
    with pytest.raises(Exception) as error:
        constraint_update.execute_protected_constraint_update(
            session_factory=_factory(test_engine), scope=_scope(foreign_trip), credential=_proof(foreign_trip), keyring=_keyring(),
            command=constraint_update.ProtectedConstraintUpdate(created_owner.payload["id"], "cross-trip-id", 0, {"importance": "flexible"}),
        )
    assert error.type.__name__ == "ProtectedConstraintMissing"
    with _factory(test_engine)() as session:
        assert session.get(Trip, owner.trip_id).planning_facts_revision == 2
        assert session.query(MutationIdempotency).filter(MutationIdempotency.idempotency_key.in_(("foreign-update", "foreign-delete", "cross-trip-id"))).count() == 0


def _raise_after_domain_effect(monkeypatch) -> None:
    def fail(_self, stage):
        if stage == "after_domain_effect":
            raise RuntimeError("phase-four injected failure")
    monkeypatch.setattr(TripDurableCommandKernel, "_checkpoint", fail)


def test_constraint_create_failure_rolls_back_public_private_revisions_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine)
    _raise_after_domain_effect(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=_create_command("rollback-create"), keyring=_keyring()
        )
    with _factory(test_engine)() as session:
        assert session.query(MemberConstraint).join(TripMembership).filter(TripMembership.trip_id == seed.trip_id).count() == 0
        assert session.query(MemberConstraintPrivate).count() == 0
        assert session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0


def test_constraint_update_and_delete_failure_roll_back_their_business_rows(test_engine, monkeypatch):
    seed = _seed(test_engine)
    created = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=_create_command("rollback-base"), keyring=_keyring()
    )
    _raise_after_domain_effect(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        constraint_update.execute_protected_constraint_update(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=constraint_update.ProtectedConstraintUpdate(created.payload["id"], "rollback-update", 1, {"importance": "flexible"}),
        )
    with _factory(test_engine)() as session:
        assert session.get(MemberConstraint, created.payload["id"]).importance == "required"
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 1
    with pytest.raises(RuntimeError, match="injected"):
        constraint_delete.execute_protected_constraint_delete(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
            command=constraint_delete.ProtectedConstraintDelete(created.payload["id"], "rollback-delete", 1),
        )
    with _factory(test_engine)() as session:
        assert session.get(MemberConstraint, created.payload["id"]) is not None
        assert session.get(MemberConstraintPrivate, created.payload["id"]) is not None
        assert session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def test_preferences_failure_rolls_back_preference_membership_revision_and_ledger(test_engine, monkeypatch):
    seed = _seed(test_engine)
    _raise_after_domain_effect(monkeypatch)
    command = preferences_save.ProtectedPreferencesSave("rollback-preferences", 0, _preference_body())
    with pytest.raises(RuntimeError, match="injected"):
        preferences_save.execute_protected_preferences_save(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
        )
    with _factory(test_engine)() as session:
        assert session.scalar(select(Preference).where(Preference.trip_membership_id == seed.membership_id)) is None
        assert session.get(TripMembership, seed.membership_id).status == "joined"
        assert session.get(Trip, seed.trip_id).planning_facts_revision == 0
        assert session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 0


def test_guest_preferences_submission_remains_authorized_but_revoked_replay_fails_closed(test_engine):
    seed = _guest_seed(test_engine)
    command = preferences_save.ProtectedPreferencesSave("guest-preferences", 0, _preference_body())
    first = preferences_save.execute_protected_preferences_save(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
    )
    assert first.replayed is False
    # preferences_submitted remains an active Guest status under the frozen
    # policy, so the same credential can issue the next protected command.
    second = constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), keyring=_keyring(),
        command=constraint_create.ProtectedConstraintCreate("guest-constraint", 1, "avoid_tag", {"tags": ["museum"]}, "required", "", "planning_only"),
    )
    assert second.replayed is False
    with _factory(test_engine).begin() as session:
        guest_session = session.scalar(select(GuestSession).where(GuestSession.membership_id == seed.membership_id))
        assert guest_session is not None
        guest_session.revoked_at = datetime.now(timezone.utc)
    with pytest.raises(Exception) as error:
        preferences_save.execute_protected_preferences_save(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=command, keyring=_keyring()
        )
    assert error.type.__name__ == "InvalidCredential"


def test_all_four_http_gates_require_headers_then_preserve_success_statuses(test_engine, monkeypatch):
    seed = _seed(test_engine)
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "phase4-http-key")
    with _http_client(test_engine) as client:
        # Create gate
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES", "1")
        assert client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(), headers=_headers(seed)
        ).status_code == 428
        created = client.post(
            f"/api/trips/{seed.trip_id}/constraints", json=_constraint_body(),
            headers=_headers(seed, key="gate-create", revision=0),
        )
        assert created.status_code == 200 and created.headers["x-trip-facts-revision"] == "1"
        constraint_id = created.json()["id"]

        # Update gate
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_CONSTRAINT_UPDATE_WRITES", "1")
        assert client.patch(f"/api/constraints/{constraint_id}", json={}, headers=_headers(seed)).status_code == 428
        updated = client.patch(
            f"/api/constraints/{constraint_id}", json={"importance": "flexible"},
            headers=_headers(seed, key="gate-update", revision=1),
        )
        assert updated.status_code == 200 and updated.headers["x-trip-facts-revision"] == "2"

        # Preferences gate
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_PREFERENCES_WRITES", "1")
        assert client.put(f"/api/trips/{seed.trip_id}/preferences/me", json=_preference_body(), headers=_headers(seed)).status_code == 428
        preferences = client.put(
            f"/api/trips/{seed.trip_id}/preferences/me", json=_preference_body(),
            headers=_headers(seed, key="gate-preferences", revision=2),
        )
        assert preferences.status_code == 200 and preferences.json() == {"submitted": True}
        assert preferences.headers["x-trip-facts-revision"] == "3"

        # Delete gate
        monkeypatch.setenv("CADENSY_B2C_PROTECTED_CONSTRAINT_DELETE_WRITES", "1")
        assert client.delete(f"/api/constraints/{constraint_id}", headers=_headers(seed)).status_code == 428
        deleted = client.delete(
            f"/api/constraints/{constraint_id}", headers=_headers(seed, key="gate-delete", revision=3)
        )
        assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
        assert deleted.headers["x-trip-facts-revision"] == "4"


def test_same_key_changed_constraint_payload_is_conflict_without_duplicate_rows(test_engine):
    seed = _seed(test_engine)
    first = _create_command("same-key-conflict")
    constraint_create.execute_protected_constraint_create(
        session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=first, keyring=_keyring()
    )
    changed = constraint_create.ProtectedConstraintCreate(
        "same-key-conflict", 0, "dietary", {"required_tags": ["vegetarian"]}, "required", "private", "planning_only"
    )
    with pytest.raises(Exception) as error:
        constraint_create.execute_protected_constraint_create(
            session_factory=_factory(test_engine), scope=_scope(seed), credential=_proof(seed), command=changed, keyring=_keyring()
        )
    assert error.type.__name__ == "IdempotencyKeyReused"
    with _factory(test_engine)() as session:
        assert session.query(MemberConstraint).join(TripMembership).filter(TripMembership.trip_id == seed.trip_id).count() == 1
        assert session.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1
