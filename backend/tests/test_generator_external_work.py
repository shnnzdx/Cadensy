"""PostgreSQL contracts for the B.2D2 generator external-work boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Event, Lock, Thread
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import base, planner
from app.db.models import (
    AuthSession,
    MemberConstraint,
    MemberConstraintPrivate,
    MutationIdempotency,
    Place,
    Plan,
    PlanChange,
    PlanItem,
    Preference,
    Trip,
    TripMembership,
    User,
)
from app.domain import auth as auth_service
from app.domain.places import geoapify, service
from app.domain.plans.generator import NO_PLACE_CANDIDATES_CODE
from app.domain.plans.generator_external_work import (
    CachePersistenceFailure,
    ProviderResultInvalid,
    execute_generator_external_work,
)
from app.domain.plans.generator_lifecycle import BlockedDraft, GeneratedDraft, prepare_generation
from app.domain.transactions import CredentialProof, HmacSigningKey, PreliminaryActorScope
from app.domain.transactions import constraint_create
from app.domain.transactions.trip_preferences_common import StaticHmacKeyring


class TrackingSession(Session):
    """Records closure without relying on Session's re-entrant state helpers."""

    closed_by_owner = False

    def close(self) -> None:
        self.closed_by_owner = True
        super().close()


@dataclass(frozen=True)
class Seed:
    trip_id: str
    organizer_id: str
    plan_id: str
    user_id: str
    token: str
    city: str


def _factory(engine, *, tracked: list[TrackingSession] | None = None):
    if tracked is None:
        return sessionmaker(bind=engine, future=True, expire_on_commit=False)

    maker = sessionmaker(
        bind=engine,
        class_=TrackingSession,
        future=True,
        expire_on_commit=False,
    )

    def create() -> TrackingSession:
        session = maker()
        tracked.append(session)
        return session

    return create


def _provider_places(city: str, *, prefix: str = "place") -> tuple[geoapify.GeoapifyPlace, ...]:
    sights = tuple(
        geoapify.GeoapifyPlace(
            provider_place_id=f"{prefix}-sight-{index}",
            name=f"Museum {index}",
            english_name=f"Museum {index}",
            local_name=None,
            city=city,
            country="United States",
            latitude=40.70 + index / 1000,
            longitude=-74.00 - index / 1000,
            category="tourism.museum",
            address=f"Museum {index}, {city}, United States",
            image_url=None,
            opening_hours=None,
        )
        for index in range(8)
    )
    meals = tuple(
        geoapify.GeoapifyPlace(
            provider_place_id=f"{prefix}-meal-{index}",
            name=f"Restaurant {index}",
            english_name=f"Restaurant {index}",
            local_name=None,
            city=city,
            country="United States",
            latitude=40.71 + index / 1000,
            longitude=-74.01 - index / 1000,
            category="catering.restaurant",
            address=f"Restaurant {index}, {city}, United States",
            image_url=None,
            opening_hours=None,
        )
        for index in range(6)
    )
    return sights + meals


def _planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
    sights = tuple(
        candidate
        for candidate in payload.candidates
        if "restaurant" not in {tag.casefold() for tag in candidate.tags}
    )
    return planner.PlanDayResult(
        picks=tuple(
            planner.Pick(candidate_id=candidate.candidate_id, start_hour=start)
            for candidate, start in zip(sights[:3], (10.0, 14.0, 16.0), strict=True)
        ),
        used_ai=True,
        planner_note="B2D2 deterministic planner.",
    )


@pytest.fixture
def external_store(test_engine):
    """Committed rows, deliberately outside the normal rollback-only db fixture."""

    trip_ids: list[str] = []
    user_ids: list[str] = []
    cities: list[str] = []
    factory = _factory(test_engine)

    def seed(*, destination: str | None = None) -> Seed:
        stamp = uuid4().hex
        city = destination or f"External City {stamp[:8]}"
        token = f"external-work-{stamp}"
        start = date(2027, 6, 10)
        with factory.begin() as session:
            user = User(name="External organizer", email=f"external-{stamp}@example.test")
            session.add(user)
            session.flush()
            trip = Trip(
                name="External work",
                destination=city,
                preferred_start_date=start,
                preferred_end_date=start + timedelta(days=1),
                currency="USD",
                created_by_user_id=user.id,
            )
            session.add(trip)
            session.flush()
            organizer = TripMembership(
                trip_id=trip.id,
                user_id=user.id,
                role="organizer",
                status="preferences_submitted",
            )
            session.add(organizer)
            session.flush()
            session.add(
                Preference(
                    trip_membership_id=organizer.id,
                    preferred_start_date=start,
                    preferred_end_date=start + timedelta(days=1),
                    available_start_date=start,
                    available_end_date=start + timedelta(days=1),
                    ideal_budget=100.0,
                    maximum_budget=150.0,
                    currency="USD",
                    top_interests=["culture", "food"],
                    submitted_at=datetime.now(timezone.utc),
                )
            )
            plan = Plan(trip_id=trip.id, currency="USD", revision=0)
            session.add(plan)
            session.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=auth_service.token_hash(token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                )
            )
            session.flush()
            trip_ids.append(trip.id)
            user_ids.append(user.id)
            cities.append(city)
            return Seed(trip.id, organizer.id, plan.id, user.id, token, city)

    yield factory, seed

    with factory.begin() as session:
        if trip_ids:
            plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids))
            memberships = select(TripMembership.id).where(TripMembership.trip_id.in_(trip_ids))
            constraint_ids = select(MemberConstraint.id).where(
                MemberConstraint.trip_membership_id.in_(memberships)
            )
            session.execute(delete(MutationIdempotency).where(MutationIdempotency.trip_id.in_(trip_ids)))
            session.execute(delete(MemberConstraintPrivate).where(MemberConstraintPrivate.constraint_id.in_(constraint_ids)))
            session.execute(delete(MemberConstraint).where(MemberConstraint.trip_membership_id.in_(memberships)))
            session.execute(delete(Preference).where(Preference.trip_membership_id.in_(memberships)))
            session.execute(delete(PlanChange).where(PlanChange.plan_id.in_(plan_ids)))
            session.execute(delete(PlanItem).where(PlanItem.plan_id.in_(plan_ids)))
            session.execute(delete(Plan).where(Plan.trip_id.in_(trip_ids)))
            session.execute(delete(TripMembership).where(TripMembership.trip_id.in_(trip_ids)))
            session.execute(delete(Trip).where(Trip.id.in_(trip_ids)))
        if user_ids:
            session.execute(delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
            session.execute(delete(User).where(User.id.in_(user_ids)))
        if cities:
            session.execute(delete(Place).where(Place.city.in_(cities)))


def _prepared(factory, seed: Seed):
    return prepare_generation(factory, seed.trip_id, seed.organizer_id)


def _cache(factory, places: tuple[geoapify.GeoapifyPlace, ...]) -> None:
    with factory.begin() as session:
        service._upsert_geoapify(session, places)


def _scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.organizer_id,
        credential_kind="account",
        expected_user_id=seed.user_id,
    )


def _keyring() -> StaticHmacKeyring:
    return StaticHmacKeyring(HmacSigningKey("b2d2-test", b"b2d2-test-key"))


def test_cache_reader_returns_detached_candidates_with_legacy_order(external_store):
    factory, seed = external_store
    data = seed()
    places = _provider_places(data.city)
    _cache(factory, places)

    snapshot = service.read_planner_cache(factory, data.city, minimum=1)
    with factory() as session:
        legacy = service.places_for_planner(session, data.city, minimum=1)

    assert snapshot.needs_refresh is False
    assert snapshot.candidates == legacy
    assert all(type(candidate).__name__ == "PlannerPlace" for candidate in snapshot.candidates)
    assert all(not hasattr(candidate, "_sa_instance_state") for candidate in snapshot.candidates)


def test_external_work_fresh_cache_skips_provider_and_closes_sessions_before_planner(external_store):
    base_factory, seed = external_store
    data = seed()
    _cache(base_factory, _provider_places(data.city))
    prepared = _prepared(base_factory, data)
    sessions: list[TrackingSession] = []
    factory = _factory(base_factory.kw["bind"], tracked=sessions)
    provider_calls: list[str] = []

    def provider(destination: str):
        provider_calls.append(destination)
        raise AssertionError("fresh cache must not call provider")

    def observing_planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
        assert len(sessions) == 1
        assert all(session.closed_by_owner and not session.in_transaction() for session in sessions)
        return _planner(payload)

    result = execute_generator_external_work(
        prepared,
        session_factory=factory,
        provider_fetch=provider,
        planner_call=observing_planner,
        minimum=1,
    )

    assert isinstance(result, GeneratedDraft)
    assert provider_calls == []
    assert all(session.closed_by_owner and not session.in_transaction() for session in sessions)


def test_stale_refresh_preserves_upsert_identity_and_duplicate_last_value(external_store):
    factory, seed = external_store
    data = seed()
    old = _provider_places(data.city, prefix="old")
    _cache(factory, old[:1])
    with factory() as session:
        original_created_at = session.scalar(
            select(Place.created_at).where(Place.provider_place_id == "old-sight-0")
        )
    refreshed = list(_provider_places(data.city, prefix="old"))
    refreshed[0] = geoapify.GeoapifyPlace(
        **{**refreshed[0].__dict__, "name": "First update", "english_name": "First update"}
    )
    refreshed.append(
        geoapify.GeoapifyPlace(
            **{**refreshed[0].__dict__, "name": "Final update", "english_name": "Final update"}
        )
    )

    result = execute_generator_external_work(
        _prepared(factory, data),
        session_factory=factory,
        provider_fetch=lambda _destination: tuple(refreshed),
        planner_call=_planner,
        minimum=14,
    )

    assert isinstance(result, GeneratedDraft)
    with factory() as session:
        rows = session.scalars(
            select(Place).where(Place.provider_place_id == "old-sight-0")
        ).all()
        assert len(rows) == 1
        assert rows[0].name == "Final update"
        assert rows[0].created_at == original_created_at


@pytest.mark.parametrize("failure", ("timeout", "http"))
def test_provider_failure_uses_stale_cache_fallback(external_store, failure):
    factory, seed = external_store
    data = seed()
    _cache(factory, _provider_places(data.city))

    def unavailable(_destination: str):
        raise geoapify.GeoapifyUnavailable(failure)

    result = execute_generator_external_work(
        _prepared(factory, data),
        session_factory=factory,
        provider_fetch=unavailable,
        planner_call=_planner,
        minimum=99,
    )

    assert isinstance(result, GeneratedDraft)
    assert result.used_ai is True


def test_missing_cache_provider_failure_and_empty_result_keep_legacy_block(external_store):
    factory, seed = external_store
    data = seed()
    prepared = _prepared(factory, data)

    unavailable = execute_generator_external_work(
        prepared,
        session_factory=factory,
        provider_fetch=lambda _destination: (_ for _ in ()).throw(geoapify.GeoapifyUnavailable("timeout")),
        planner_call=_planner,
        minimum=1,
    )
    empty = execute_generator_external_work(
        prepared,
        session_factory=factory,
        provider_fetch=lambda _destination: (),
        planner_call=_planner,
        minimum=1,
    )
    not_found = execute_generator_external_work(
        prepared,
        session_factory=factory,
        provider_fetch=lambda _destination: geoapify.GeoapifyPlaces(destination_found=False),
        planner_call=_planner,
        minimum=1,
    )

    assert isinstance(unavailable, BlockedDraft) and unavailable.blocked_code == NO_PLACE_CANDIDATES_CODE
    assert isinstance(empty, BlockedDraft) and empty.blocked_code == NO_PLACE_CANDIDATES_CODE
    assert isinstance(not_found, BlockedDraft) and not_found.blocked_code == "DESTINATION_NOT_FOUND"


def test_malformed_provider_result_fails_closed_without_cache_write(external_store):
    factory, seed = external_store
    data = seed()

    with pytest.raises(ProviderResultInvalid):
        execute_generator_external_work(
            _prepared(factory, data),
            session_factory=factory,
            provider_fetch=lambda _destination: (object(),),  # type: ignore[return-value]
            planner_call=_planner,
            minimum=1,
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Place).where(Place.city == data.city)) == 0


def test_unusable_provider_candidates_are_detached_then_blocked(external_store):
    factory, seed = external_store
    data = seed()
    unusable = geoapify.GeoapifyPlace(
        provider_place_id="non-latin-only",
        name="没有英文名称",
        english_name=None,
        local_name="没有英文名称",
        city=data.city,
        country="United States",
        latitude=40.7,
        longitude=-74.0,
        category="tourism.attraction",
        address=None,
        image_url=None,
        opening_hours=None,
    )

    result = execute_generator_external_work(
        _prepared(factory, data),
        session_factory=factory,
        provider_fetch=lambda _destination: (unusable,),
        planner_call=_planner,
        minimum=1,
    )

    assert isinstance(result, BlockedDraft)
    assert result.blocked_code == NO_PLACE_CANDIDATES_CODE
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Place).where(Place.city == data.city)) == 1


def test_persistence_failure_rolls_back_cache_and_never_mutates_generator_aggregates(
    external_store, monkeypatch
):
    factory, seed = external_store
    data = seed()
    original = service._upsert_geoapify

    def fail_after_flush(session, fetched):
        original(session, fetched)
        raise OperationalError("INSERT INTO place", {}, Exception("injected database failure"))

    monkeypatch.setattr(service, "_upsert_geoapify", fail_after_flush)
    with pytest.raises(CachePersistenceFailure):
        execute_generator_external_work(
            _prepared(factory, data),
            session_factory=factory,
            provider_fetch=lambda _destination: _provider_places(data.city),
            planner_call=_planner,
            minimum=1,
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Place).where(Place.city == data.city)) == 0
        trip = session.get(Trip, data.trip_id)
        plan = session.get(Plan, data.plan_id)
        assert trip is not None and trip.planning_facts_revision == 0
        assert plan is not None and plan.revision == 0
        assert session.scalar(select(PlanItem.id).where(PlanItem.plan_id == data.plan_id)) is None
        assert session.scalar(select(MutationIdempotency.id).where(MutationIdempotency.trip_id == data.trip_id)) is None


def test_concurrent_refreshes_use_unique_cache_identity_without_authoritative_writes(external_store):
    factory, seed = external_store
    data = seed()
    prepared = _prepared(factory, data)
    first_entered = Event()
    both_entered = Event()
    release = Event()
    persistence_selects_ready = Event()
    release_persistence_selects = Event()
    provider_lock = Lock()
    select_lock = Lock()
    provider_calls = 0
    persistence_selects = 0
    observe_persistence = False
    errors: list[BaseException] = []
    results: list[GeneratedDraft | BlockedDraft] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal persistence_selects
        normalized = " ".join(statement.casefold().split())
        if not observe_persistence or not normalized.startswith("select") or " from place" not in normalized:
            return
        with select_lock:
            if persistence_selects >= 2:
                return
            persistence_selects += 1
            if persistence_selects == 2:
                persistence_selects_ready.set()
        assert release_persistence_selects.wait(timeout=5.0)

    def provider(_destination: str):
        nonlocal provider_calls
        with provider_lock:
            provider_calls += 1
            if provider_calls == 1:
                first_entered.set()
            if provider_calls == 2:
                both_entered.set()
        assert release.wait(timeout=5.0)
        return _provider_places(data.city, prefix="concurrent")

    def run() -> None:
        try:
            results.append(
                execute_generator_external_work(
                    prepared,
                    session_factory=factory,
                    provider_fetch=provider,
                    planner_call=_planner,
                    minimum=1,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    event.listen(factory.kw["bind"], "after_cursor_execute", observe)
    first = Thread(target=run)
    second = Thread(target=run)
    try:
        first.start()
        assert first_entered.wait(timeout=5.0)
        second.start()
        assert both_entered.wait(timeout=5.0)
        observe_persistence = True
        release.set()
        assert persistence_selects_ready.wait(timeout=5.0)
        release_persistence_selects.set()
        first.join(timeout=10.0)
        second.join(timeout=10.0)
    finally:
        release.set()
        release_persistence_selects.set()
        event.remove(factory.kw["bind"], "after_cursor_execute", observe)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert provider_calls == 2
    assert len(results) == 2 and all(isinstance(result, GeneratedDraft) for result in results)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Place).where(Place.city == data.city)) == 14
        assert session.get(Trip, data.trip_id).planning_facts_revision == 0
        assert session.get(Plan, data.plan_id).revision == 0
        assert session.scalar(select(PlanItem.id).where(PlanItem.plan_id == data.plan_id)) is None


def test_provider_and_persistence_sessions_close_before_planner(external_store):
    base_factory, seed = external_store
    data = seed()
    prepared = _prepared(base_factory, data)
    sessions: list[TrackingSession] = []
    factory = _factory(base_factory.kw["bind"], tracked=sessions)

    def provider(destination: str):
        assert destination == data.city
        assert len(sessions) == 1
        assert sessions[0].closed_by_owner and not sessions[0].in_transaction()
        return _provider_places(data.city)

    def observing_planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
        assert len(sessions) == 2
        assert all(session.closed_by_owner and not session.in_transaction() for session in sessions)
        return _planner(payload)

    result = execute_generator_external_work(
        prepared,
        session_factory=factory,
        provider_fetch=provider,
        planner_call=observing_planner,
        minimum=1,
    )

    assert isinstance(result, GeneratedDraft)
    assert all(session.closed_by_owner and not session.in_transaction() for session in sessions)


def test_provider_block_does_not_hold_a_trip_lock_or_block_real_constraint_writer(external_store):
    factory, seed = external_store
    data = seed()
    prepared = _prepared(factory, data)
    provider_entered = Event()
    release_provider = Event()
    result: dict[str, GeneratedDraft | BlockedDraft] = {}
    errors: list[BaseException] = []

    def provider(_destination: str):
        provider_entered.set()
        assert release_provider.wait(timeout=5.0)
        return _provider_places(data.city)

    def run_external_work() -> None:
        try:
            result["draft"] = execute_generator_external_work(
                prepared,
                session_factory=factory,
                provider_fetch=provider,
                planner_call=_planner,
                minimum=1,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = Thread(target=run_external_work)
    worker.start()
    assert provider_entered.wait(timeout=5.0)

    writer = constraint_create.execute_protected_constraint_create(
        session_factory=factory,
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=constraint_create.ProtectedConstraintCreate(
            "b2d2-concurrent-constraint",
            0,
            "avoid_tag",
            {"tags": ["museum"]},
            "required",
            "private test wording",
            "planning_only",
        ),
        keyring=_keyring(),
    )
    assert writer.trip_planning_facts_revision == 1
    with factory() as session:
        trip = session.get(Trip, data.trip_id)
        plan = session.get(Plan, data.plan_id)
        assert trip is not None and trip.planning_facts_revision == 1
        assert plan is not None and plan.revision == 1
        assert session.scalar(select(PlanItem.id).where(PlanItem.plan_id == data.plan_id)) is None
        assert session.scalar(select(PlanChange.id).where(PlanChange.plan_id == data.plan_id)) is None
        ledgers = session.scalars(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == data.trip_id)
        ).all()
        assert [ledger.operation_code for ledger in ledgers] == ["constraint.create.v1"]

    release_provider.set()
    worker.join(timeout=10.0)
    assert not worker.is_alive() and errors == []
    assert isinstance(result["draft"], GeneratedDraft)
    assert not hasattr(result["draft"], "_sa_instance_state")


def test_external_work_preserves_planner_unavailable_rules_fallback(external_store):
    factory, seed = external_store
    data = seed()
    _cache(factory, _provider_places(data.city))

    def unavailable(_payload: planner.PlanDayInput) -> planner.PlanDayResult:
        raise base.AgentUnavailable("planner unavailable")

    result = execute_generator_external_work(
        _prepared(factory, data),
        session_factory=factory,
        provider_fetch=lambda _destination: pytest.fail("fresh cache should not call provider"),
        planner_call=unavailable,
        minimum=1,
    )

    assert isinstance(result, GeneratedDraft)
    assert result.generated_by == "rules"
    assert result.used_ai is False
