"""End-to-end PostgreSQL contracts for B.2D4 protected Generator commits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.agents import base, planner
from app.db.models import (
    AuthSession,
    GuestSession,
    InviteLink,
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
from app.domain.places import geoapify
from app.domain.plans.generator import NO_PLACE_CANDIDATES_CODE, NO_PLACES_BLOCKED_REASON
from app.domain.transactions import (
    CredentialProof,
    DuplicatePlans,
    HmacSigningKey,
    InsufficientRole,
    InvalidCredential,
    PreliminaryActorScope,
    StaticOutcomeContractRegistry,
    TripTransactionKernel,
)
from app.domain.transactions.generator_durable import (
    GeneratorDurableCoordinator,
    GeneratorIdempotentCommand,
    StaleGeneratorPreconditions,
    deterministic_first_plan_id,
    generator_outcome_contract,
)
from app.domain.transactions.trip_preferences_common import StaticHmacKeyring
from app.domain.plans import generator
from app.domain.transactions import constraint_create, preferences_save
from app.domain.transactions import membership_remove
from app.domain.transactions.membership_join import join_invite_under_trip_root
from app.domain.places import service as place_service


@dataclass(frozen=True)
class Seed:
    trip_id: str
    organizer_id: str
    user_id: str
    token: str
    city: str


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _provider_places(city: str) -> tuple[geoapify.GeoapifyPlace, ...]:
    sights = tuple(
        geoapify.GeoapifyPlace(
            provider_place_id=f"generator-commit-sight-{index}",
            name=f"Generator Museum {index}",
            english_name=f"Generator Museum {index}",
            local_name=None,
            city=city,
            country="United States",
            latitude=40.70 + index / 1000,
            longitude=-74.00 - index / 1000,
            category="tourism.museum",
            address=f"Museum {index}, {city}",
            image_url=None,
            opening_hours=None,
        )
        for index in range(8)
    )
    meals = tuple(
        geoapify.GeoapifyPlace(
            provider_place_id=f"generator-commit-meal-{index}",
            name=f"Generator Restaurant {index}",
            english_name=f"Generator Restaurant {index}",
            local_name=None,
            city=city,
            country="United States",
            latitude=40.71 + index / 1000,
            longitude=-74.01 - index / 1000,
            category="catering.restaurant",
            address=f"Restaurant {index}, {city}",
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
        planner_note="B2D4 deterministic planner.",
    )


def _day_semantics(days: object) -> tuple[tuple[object, ...], ...]:
    """Compare Legacy and protected public days without their persisted IDs."""

    assert isinstance(days, list)
    return tuple(
        (
            day["day_index"],
            day["day_date"],
            tuple(
                (
                    item["start_hour"],
                    item["title"],
                    item["local_title"],
                    item["place"],
                    item["price_per_person"],
                    item["source"],
                    item["lat"],
                    item["lng"],
                    item["photo_url"],
                    tuple(item["tags"]),
                )
                for item in day["items"]
            ),
        )
        for day in days
    )


@pytest.fixture
def generator_commit_store(test_engine):
    factory = _factory(test_engine)
    trip_ids: list[str] = []
    user_ids: list[str] = []
    cities: list[str] = []

    def seed() -> Seed:
        stamp = uuid4().hex
        city = f"Generator Commit City {stamp[:8]}"
        token = f"generator-commit-{stamp}"
        start = date(2027, 7, 10)
        with factory.begin() as session:
            user = User(name="Generator organizer", email=f"generator-{stamp}@example.test")
            session.add(user)
            session.flush()
            trip = Trip(
                name="Generator commit",
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
            session.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=auth_service.token_hash(token),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                )
            )
            trip_ids.append(trip.id)
            user_ids.append(user.id)
            cities.append(city)
            return Seed(trip.id, organizer.id, user.id, token, city)

    yield factory, seed

    with factory.begin() as session:
        cleanup_user_ids = tuple(user_ids)
        if trip_ids:
            plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids))
            membership_ids = select(TripMembership.id).where(TripMembership.trip_id.in_(trip_ids))
            # Account invite joins create a User after ``seed()`` has returned.
            # Derive all account users from the fixture-owned Trips before
            # removing memberships so the fixture cannot leak a joined account
            # into later, otherwise isolated tests.
            cleanup_user_ids = tuple(
                set(cleanup_user_ids).union(
                    session.scalars(
                        select(TripMembership.user_id).where(
                            TripMembership.trip_id.in_(trip_ids),
                            TripMembership.user_id.is_not(None),
                        )
                    ).all()
                )
            )
            constraint_ids = select(MemberConstraint.id).where(
                MemberConstraint.trip_membership_id.in_(membership_ids)
            )
            session.execute(delete(MutationIdempotency).where(MutationIdempotency.trip_id.in_(trip_ids)))
            session.execute(delete(GuestSession).where(GuestSession.trip_id.in_(trip_ids)))
            session.execute(delete(InviteLink).where(InviteLink.trip_id.in_(trip_ids)))
            session.execute(delete(MemberConstraintPrivate).where(MemberConstraintPrivate.constraint_id.in_(constraint_ids)))
            session.execute(delete(MemberConstraint).where(MemberConstraint.trip_membership_id.in_(membership_ids)))
            session.execute(delete(PlanChange).where(PlanChange.plan_id.in_(plan_ids)))
            session.execute(delete(PlanItem).where(PlanItem.plan_id.in_(plan_ids)))
            session.execute(delete(Plan).where(Plan.trip_id.in_(trip_ids)))
            session.execute(delete(Preference).where(Preference.trip_membership_id.in_(select(TripMembership.id).where(TripMembership.trip_id.in_(trip_ids)))))
            session.execute(delete(TripMembership).where(TripMembership.trip_id.in_(trip_ids)))
            session.execute(delete(Trip).where(Trip.id.in_(trip_ids)))
        if cleanup_user_ids:
            session.execute(delete(AuthSession).where(AuthSession.user_id.in_(cleanup_user_ids)))
            session.execute(delete(User).where(User.id.in_(cleanup_user_ids)))
        if cities:
            session.execute(delete(Place).where(Place.city.in_(cities)))


def _scope(seed: Seed) -> PreliminaryActorScope:
    return PreliminaryActorScope(
        trip_id=seed.trip_id,
        membership_id=seed.organizer_id,
        credential_kind="account",
        expected_user_id=seed.user_id,
    )


def _coordinator(factory) -> GeneratorDurableCoordinator:
    return GeneratorDurableCoordinator(
        TripTransactionKernel(factory),
        StaticHmacKeyring(HmacSigningKey("generator-commit-v1", b"generator-commit-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
    )


def test_zero_plan_generated_draft_persists_real_effects_atomically(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-zero-plan",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    result = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: _provider_places(data.city),
        planner_call=_planner,
    )

    expected_plan_id = deterministic_first_plan_id(
        trip_id=data.trip_id,
        actor_membership_id=data.organizer_id,
        idempotency_key=command.idempotency_key,
    )
    assert result.replayed is False
    assert result.payload["plan_id"] == expected_plan_id
    assert result.payload["status"] == "active"
    assert set(result.payload) == {
        "plan_id",
        "status",
        "days",
        "blocked_reason",
        "blocked_code",
        "generated_by",
        "used_ai",
        "planner_note",
    }
    serialized_outcome = repr(result.payload)
    assert data.token not in serialized_outcome
    assert "maximum_budget" not in serialized_outcome
    assert "original_text" not in serialized_outcome
    with factory() as session:
        plan = session.get(Plan, expected_plan_id)
        assert plan is not None
        assert plan.revision == 1
        assert plan.status == "active"
        assert plan.needs_refresh is False
        assert plan.currency == "USD"
        assert plan.estimated_total_per_person is None
        items = session.scalars(
            select(PlanItem).where(PlanItem.plan_id == plan.id).order_by(PlanItem.day_index, PlanItem.start_hour)
        ).all()
        changes = session.scalars(select(PlanChange).where(PlanChange.plan_id == plan.id)).all()
        ledger = session.scalar(
            select(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id,
                MutationIdempotency.idempotency_key == command.idempotency_key,
            )
        )
        assert items
        assert len(changes) == len(items)
        # The existing Generator uses Planner picks for sightseeing and the
        # deterministic rules mapper for meal anchors. B.2D4 must retain that
        # mixed item provenance rather than relabeling all generated rows as AI.
        assert {change.origin for change in changes} == {"ai_generate", "rule_generate"}
        # Aggregate provenance follows the established Legacy day-level result:
        # the Planner constructed each accepted day even when rules add anchors.
        assert result.payload["generated_by"] == "planner"
        assert ledger is not None
        assert ledger.resulting_plan_revision == 1
        assert ledger.resulting_trip_planning_facts_revision is None
        assert {row["id"] for day in result.payload["days"] for row in day["items"]} == {
            item.id for item in items
        }
        for day in result.payload["days"]:
            starts = [row["start_hour"] for row in day["items"]]
            assert starts == sorted(starts)
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 1


def test_rules_fallback_preserves_rule_item_provenance(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-rules-fallback",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    def unavailable_planner(_payload: planner.PlanDayInput) -> planner.PlanDayResult:
        raise base.AgentUnavailable("injected planner outage")

    result = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: _provider_places(data.city),
        planner_call=unavailable_planner,
    )

    assert result.payload["generated_by"] == "rules"
    assert result.payload["used_ai"] is False
    assert result.payload["planner_note"] is None
    with factory() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert plan is not None and plan.status == "active" and plan.revision == 1
        assert {
            row.origin
            for row in session.scalars(select(PlanChange).where(PlanChange.plan_id == plan.id)).all()
        } == {"rule_generate"}


def test_same_key_replay_skips_prepare_provider_and_planner(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    coordinator = _coordinator(factory)
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-replay",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    first = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=coordinator,
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: _provider_places(data.city),
        planner_call=_planner,
    )

    def unexpected_prepare_session():
        raise AssertionError("historical replay must not open a B.2D1 snapshot Session")

    replay = execute_protected_plan_generation(
        session_factory=unexpected_prepare_session,
        coordinator=coordinator,
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: pytest.fail("historical replay called provider"),
        planner_call=lambda _payload: pytest.fail("historical replay called planner"),
    )

    assert replay.replayed is True
    assert replay.payload == first.payload
    assert replay.plan_revision == first.plan_revision == 1
    with factory() as session:
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(PlanItem).join(Plan).where(Plan.trip_id == data.trip_id)
        ) == sum(len(day["items"]) for day in first.payload["days"])


def test_same_key_replay_preserves_historical_outcome_after_later_plan_and_trip_changes(
    generator_commit_store,
):
    """Replay is ledger-derived, never reconstructed from the current aggregate."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    coordinator = _coordinator(factory)
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-historical-replay",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    first = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=coordinator,
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: _provider_places(data.city),
        planner_call=_planner,
    )
    with factory.begin() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        trip = session.get(Trip, data.trip_id)
        assert plan is not None and trip is not None
        plan.revision += 1
        trip.planning_facts_revision += 1

    replay = execute_protected_plan_generation(
        session_factory=lambda: pytest.fail("historical replay must not prepare after later writes"),
        coordinator=coordinator,
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: pytest.fail("historical replay called provider"),
        planner_call=lambda _payload: pytest.fail("historical replay called planner"),
    )

    assert replay.replayed is True
    assert replay.payload == first.payload
    assert replay.plan_revision == first.plan_revision == 1
    with factory() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        trip = session.get(Trip, data.trip_id)
        assert plan is not None and plan.revision == 2
        assert trip is not None and trip.planning_facts_revision == 1


def test_blocked_draft_creates_first_plan_without_items_or_changes(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-blocked",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    result = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: (),
        planner_call=lambda _payload: pytest.fail("blocked no-candidate result called planner"),
    )

    assert result.replayed is False
    assert result.payload["status"] == "blocked"
    assert result.payload["blocked_reason"] == NO_PLACES_BLOCKED_REASON
    assert result.payload["blocked_code"] == NO_PLACE_CANDIDATES_CODE
    with factory() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert plan is not None
        assert plan.revision == 1
        assert plan.status == "blocked"
        assert plan.blocked_reason == NO_PLACES_BLOCKED_REASON
        assert plan.estimated_total_per_person is None
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange).where(PlanChange.plan_id == plan.id)) == 0
        ledger = session.scalar(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == data.trip_id)
        )
        assert ledger is not None and ledger.resulting_plan_revision == 1


def test_identical_existing_blocked_draft_is_a_replayable_noop(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        session.add(
            Plan(
                trip_id=data.trip_id,
                currency="USD",
                status="blocked",
                blocked_reason=NO_PLACES_BLOCKED_REASON,
                estimated_total_per_person=None,
                needs_refresh=False,
                revision=0,
            )
        )
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-blocked-noop",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    first = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: (),
        planner_call=lambda _payload: pytest.fail("blocked no-candidate result called planner"),
    )
    replay = execute_protected_plan_generation(
        session_factory=lambda: pytest.fail("replay must not prepare"),
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
    )

    assert first.replayed is False
    assert first.plan_revision == 0
    assert replay.replayed is True
    assert replay.payload == first.payload
    with factory() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert plan is not None and plan.revision == 0
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange).where(PlanChange.plan_id == plan.id)) == 0
        ledger = session.scalar(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == data.trip_id)
        )
        assert ledger is not None and ledger.resulting_plan_revision == 0


def test_legacy_bypass_preference_change_stales_draft_without_generator_effects(
    generator_commit_store,
):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-stale-preference",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    def provider_after_legacy_preference_write(_destination: str):
        # Deliberately simulate an unmigrated legacy bypass: no revision is
        # advanced. The final B.2D4 projection comparison must still reject it.
        with factory.begin() as session:
            preference = session.scalar(
                select(Preference).where(Preference.trip_membership_id == data.organizer_id)
            )
            assert preference is not None
            preference.top_interests = ["changed-after-prepare"]
        return _provider_places(data.city)

    with pytest.raises(StaleGeneratorPreconditions):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=provider_after_legacy_preference_write,
            planner_call=_planner,
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id
            )
        ) == 0
        # The B.2D2 cache is intentionally independent and may remain.
        assert session.scalar(select(func.count()).select_from(Place).where(Place.city == data.city)) > 0


def test_real_protected_constraint_write_stales_draft_before_generator_commit(
    generator_commit_store,
):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-real-constraint-stale",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    def provider_after_constraint_write(_destination: str):
        result = constraint_create.execute_protected_constraint_create(
            session_factory=factory,
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=constraint_create.ProtectedConstraintCreate(
                idempotency_key="constraint-during-generator-work",
                expected_trip_facts_revision=0,
                kind="avoid_tag",
                params={"tags": ["museum"]},
                importance="required",
                original_text="private text must never reach the generator outcome",
                visibility="planning_only",
            ),
            keyring=StaticHmacKeyring(
                HmacSigningKey("constraint-during-generator", b"constraint-during-generator")
            ),
        )
        assert result.replayed is False
        return _provider_places(data.city)

    with pytest.raises(StaleGeneratorPreconditions):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=provider_after_constraint_write,
            planner_call=_planner,
        )

    with factory() as session:
        trip = session.get(Trip, data.trip_id)
        assert trip is not None and trip.planning_facts_revision == 1
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        ledgers = session.scalars(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == data.trip_id)
        ).all()
        assert len(ledgers) == 1
        assert ledgers[0].operation_code == "constraint.create.v1"


def test_real_protected_preference_write_stales_draft_before_generator_commit(
    generator_commit_store,
):
    """A Phase 4 preference command invalidates an external Generator draft."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-real-preference-stale",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    fields = {
        "preferred_start_date": date(2027, 7, 10),
        "preferred_end_date": date(2027, 7, 11),
        "available_start_date": date(2027, 7, 10),
        "available_end_date": date(2027, 7, 11),
        "ideal_budget": 100.0,
        "maximum_budget": 150.0,
        "currency": "USD",
        "budget_visibility": "planning_only",
        "travel_style": None,
        "top_interests": ("changed-after-prepare",),
    }

    def provider_after_preference_write(_destination: str):
        result = preferences_save.execute_protected_preferences_save(
            session_factory=factory,
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=preferences_save.ProtectedPreferencesSave(
                "preferences-during-generator-work", 0, fields
            ),
            keyring=StaticHmacKeyring(
                HmacSigningKey("preferences-during-generator", b"preferences-during-generator")
            ),
        )
        assert result.replayed is False
        return _provider_places(data.city)

    with pytest.raises(StaleGeneratorPreconditions):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=provider_after_preference_write,
            planner_call=_planner,
        )

    with factory() as session:
        trip = session.get(Trip, data.trip_id)
        assert trip is not None and trip.planning_facts_revision == 1
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        ledgers = session.scalars(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == data.trip_id)
        ).all()
        assert len(ledgers) == 1
        assert ledgers[0].operation_code == "preferences.save.v1"


@pytest.mark.parametrize("join_kind", ("account", "guest"))
def test_real_protected_invite_join_during_detached_generator_work_stales_existing_empty_plan(
    generator_commit_store,
    join_kind: str,
):
    """A1/A2: provider work owns no DB lock, but final validation is fresh."""
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="USD", revision=0)
        session.add(plan)
        invite_token = f"generator-{join_kind}-invite-{uuid4().hex}"
        session.add(InviteLink(trip_id=data.trip_id, token_hash=auth_service.token_hash(invite_token), is_primary=True))
        if join_kind == "account":
            joiner = User(name="Generator account joiner", email=f"generator-join-{uuid4().hex}@example.test")
            session.add(joiner)
            session.flush()
            email = joiner.email
        else:
            email = None

    provider_entered, release_provider = Event(), Event()
    outcomes: Queue[object] = Queue()

    def paused_provider(_destination: str):
        provider_entered.set()
        assert release_provider.wait(5), "test did not release detached Provider work"
        return _provider_places(data.city)

    def run_generator() -> None:
        try:
            outcomes.put(
                execute_protected_plan_generation(
                    session_factory=factory,
                    coordinator=_coordinator(factory),
                    scope=_scope(data),
                    credential=CredentialProof.from_bearer_token(data.token),
                    command=GeneratorIdempotentCommand(
                        idempotency_key=f"generator-stale-{join_kind}",
                        fingerprint_version=1,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    ),
                    provider_fetch=paused_provider,
                    planner_call=_planner,
                )
            )
        except BaseException as exc:
            outcomes.put(exc)

    worker = Thread(target=run_generator, daemon=True)
    try:
        worker.start()
        assert provider_entered.wait(5)
        # This is a real, separately-owned Trip transaction while the Provider
        # is paused.  No Generator Session or root lock survives Prepare.
        joined = join_invite_under_trip_root(
            session_factory=factory,
            token=invite_token,
            display_name=f"Generator {join_kind} joiner",
            email=email,
        )
        assert (joined.guest_session is not None) is (join_kind == "guest")
    finally:
        release_provider.set()
        worker.join(5)
    assert not worker.is_alive()
    assert isinstance(outcomes.get_nowait(), StaleGeneratorPreconditions)
    with factory() as session:
        trip = session.get(Trip, data.trip_id)
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert trip is not None and trip.planning_facts_revision == 1
        assert plan is not None and plan.revision == 1
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange).where(PlanChange.plan_id == plan.id)) == 0
        assert session.scalar(select(func.count()).select_from(MutationIdempotency).where(
            MutationIdempotency.trip_id == data.trip_id,
            MutationIdempotency.operation_code == "plan.generate.v1",
        )) == 0
        if join_kind == "guest":
            assert session.scalar(select(func.count()).select_from(GuestSession).where(GuestSession.trip_id == data.trip_id)) == 1


def test_real_protected_membership_removal_during_detached_generator_work_stales_draft(
    generator_commit_store,
):
    """A3: removal/revocation and dual revisions win over an obsolete draft."""
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="USD", revision=0)
        session.add(plan)
        target = TripMembership(trip_id=data.trip_id, guest_display_name="Generator removable guest", role="participant", status="joined")
        session.add(target)
        session.flush()
        guest_token = auth_service.start_guest_session(session, target).token
        target_id = target.id
    provider_entered, release_provider = Event(), Event()
    outcomes: Queue[object] = Queue()

    def paused_provider(_destination: str):
        provider_entered.set()
        assert release_provider.wait(5)
        return _provider_places(data.city)

    def run_generator() -> None:
        try:
            outcomes.put(execute_protected_plan_generation(
                session_factory=factory, coordinator=_coordinator(factory), scope=_scope(data),
                credential=CredentialProof.from_bearer_token(data.token),
                command=GeneratorIdempotentCommand("generator-stale-removal", 1, datetime.now(timezone.utc) + timedelta(days=1)),
                provider_fetch=paused_provider, planner_call=_planner,
            ))
        except BaseException as exc:
            outcomes.put(exc)

    worker = Thread(target=run_generator, daemon=True)
    try:
        worker.start()
        assert provider_entered.wait(5)
        removed = membership_remove.execute_protected_membership_remove(
            session_factory=factory, scope=_scope(data), credential=CredentialProof.from_bearer_token(data.token),
            command=membership_remove.ProtectedMembershipRemove(target_id, "generator-removal", 0),
            keyring=StaticHmacKeyring(HmacSigningKey("generator-removal", b"generator-removal")),
        )
        assert removed.trip_planning_facts_revision == 1
    finally:
        release_provider.set()
        worker.join(5)
    assert not worker.is_alive() and isinstance(outcomes.get_nowait(), StaleGeneratorPreconditions)
    with factory() as session:
        trip = session.get(Trip, data.trip_id)
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert trip is not None and trip.planning_facts_revision == 1
        assert plan is not None and plan.revision == 1
        assert session.get(TripMembership, target_id).status == "removed"
        assert session.scalar(select(GuestSession.revoked_at).where(GuestSession.membership_id == target_id)) is not None
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)) == 0
        assert session.scalar(select(func.count()).select_from(MutationIdempotency).where(
            MutationIdempotency.trip_id == data.trip_id,
            MutationIdempotency.operation_code == "plan.generate.v1",
        )) == 0
        assert session.scalar(select(func.count()).select_from(MutationIdempotency).where(
            MutationIdempotency.trip_id == data.trip_id,
            MutationIdempotency.operation_code == membership_remove.OPERATION,
        )) == 1


def test_generator_commit_then_real_invite_join_advances_existing_plan_revision(generator_commit_store):
    """Reverse order: a later membership fact write must still version the Plan."""
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="USD", revision=0)
        session.add(plan)
        invite_token = f"generator-after-join-{uuid4().hex}"
        session.add(InviteLink(trip_id=data.trip_id, token_hash=auth_service.token_hash(invite_token), is_primary=True))
    generated = execute_protected_plan_generation(
        session_factory=factory, coordinator=_coordinator(factory), scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=GeneratorIdempotentCommand("generator-before-join", 1, datetime.now(timezone.utc) + timedelta(days=1)),
        provider_fetch=lambda _destination: _provider_places(data.city), planner_call=_planner,
    )
    assert generated.plan_revision == 1
    join_invite_under_trip_root(session_factory=factory, token=invite_token, display_name="After generator", email=None)
    with factory() as session:
        trip = session.get(Trip, data.trip_id)
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert trip is not None and trip.planning_facts_revision == 1
        assert plan is not None and plan.revision == 2


def test_credential_revocation_during_external_work_fails_before_generator_effects(
    generator_commit_store,
):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-revoked-credential",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    def provider_after_revocation(_destination: str):
        with factory.begin() as session:
            auth_session = session.scalar(select(AuthSession).where(AuthSession.user_id == data.user_id))
            assert auth_session is not None
            auth_session.revoked_at = datetime.now(timezone.utc)
        return _provider_places(data.city)

    with pytest.raises(InvalidCredential):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=provider_after_revocation,
            planner_call=_planner,
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id
            )
        ) == 0


def test_existing_empty_plan_is_populated_without_replacement(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="USD", revision=0)
        session.add(plan)
        session.flush()
        original_plan_id = plan.id
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-existing-plan",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    result = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: _provider_places(data.city),
        planner_call=_planner,
    )

    assert result.payload["plan_id"] == original_plan_id
    assert result.plan_revision == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 1
        plan = session.get(Plan, original_plan_id)
        assert plan is not None and plan.revision == 1
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)) > 0


def test_existing_plan_currency_snapshot_uses_plan_currency_before_legacy_sync(
    generator_commit_store,
):
    """A valid existing Plan must not be rejected merely before Legacy syncs currency."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="EUR", revision=0)
        session.add(plan)
        session.flush()
        original_plan_id = plan.id
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-existing-plan-currency",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    result = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(data),
        credential=CredentialProof.from_bearer_token(data.token),
        command=command,
        provider_fetch=lambda _destination: _provider_places(data.city),
        planner_call=_planner,
    )

    assert result.replayed is False
    assert result.payload["plan_id"] == original_plan_id
    with factory() as session:
        plan = session.get(Plan, original_plan_id)
        assert plan is not None
        assert plan.revision == 1
        # Legacy _get_or_create_plan synchronizes existing Plan currency only
        # as part of the actual generator mutation, not during snapshot capture.
        assert plan.currency == "USD"


@pytest.mark.parametrize(
    ("lifecycle_change", "expected_error", "expected_plan_count"),
    [
        ("replacement", StaleGeneratorPreconditions, 1),
        ("deletion", StaleGeneratorPreconditions, 0),
        ("duplicate", DuplicatePlans, 2),
    ],
)
def test_plan_lifecycle_changes_during_external_work_leave_no_generator_effects(
    generator_commit_store,
    lifecycle_change: str,
    expected_error: type[BaseException],
    expected_plan_count: int,
):
    """Final Validate must never attach a draft to a changed Plan aggregate."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="USD", revision=0)
        session.add(plan)
        session.flush()
        original_plan_id = plan.id
    command = GeneratorIdempotentCommand(
        idempotency_key=f"generator-commit-plan-{lifecycle_change}",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    def provider_after_plan_lifecycle_change(_destination: str):
        with factory.begin() as session:
            current = session.get(Plan, original_plan_id)
            assert current is not None
            if lifecycle_change == "replacement":
                session.delete(current)
                session.flush()
                session.add(Plan(trip_id=data.trip_id, currency="USD", revision=0))
            elif lifecycle_change == "deletion":
                session.delete(current)
            else:
                session.add(Plan(trip_id=data.trip_id, currency="USD", revision=0))
        return _provider_places(data.city)

    with pytest.raises(expected_error):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=provider_after_plan_lifecycle_change,
            planner_call=_planner,
        )

    with factory() as session:
        plan_ids = select(Plan.id).where(Plan.trip_id == data.trip_id)
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == expected_plan_count
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id.in_(plan_ids))) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange).where(PlanChange.plan_id.in_(plan_ids))) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id,
                MutationIdempotency.operation_code == "plan.generate.v1",
            )
        ) == 0


@pytest.mark.parametrize(
    ("organizer_change", "expected_error"),
    [
        ("role_loss", InsufficientRole),
        ("readiness_loss", generator.OrganizerPreferencesRequired),
    ],
)
def test_organizer_changes_during_external_work_fail_before_generator_effects(
    generator_commit_store,
    organizer_change: str,
    expected_error: type[BaseException],
):
    """Final authorization and B.2D1 organizer readiness are both fresh facts."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key=f"generator-commit-organizer-{organizer_change}",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    def provider_after_organizer_change(_destination: str):
        with factory.begin() as session:
            organizer = session.get(TripMembership, data.organizer_id)
            assert organizer is not None
            if organizer_change == "role_loss":
                organizer.role = "member"
            else:
                organizer.status = "joined"
        return _provider_places(data.city)

    with pytest.raises(expected_error):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=provider_after_organizer_change,
            planner_call=_planner,
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id,
                MutationIdempotency.operation_code == "plan.generate.v1",
            )
        ) == 0


@pytest.mark.parametrize("scenario", ["planner", "invalid_planner", "no_candidates"])
def test_protected_generator_matches_legacy_business_outcome_for_key_scenarios(
    generator_commit_store,
    monkeypatch,
    scenario: str,
):
    """Compare public business semantics while excluding frozen identity differences."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    legacy_seed = seed()
    protected_seed = seed()
    # The two independent Plan aggregates deliberately share one destination so
    # B.2D4's detached cache write becomes the exact Legacy candidate source.
    with factory.begin() as session:
        protected_trip = session.get(Trip, protected_seed.trip_id)
        assert protected_trip is not None
        protected_trip.destination = legacy_seed.city

    def invalid_planner(_payload: planner.PlanDayInput) -> planner.PlanDayResult:
        return planner.PlanDayResult(
            picks=(planner.Pick(candidate_id="unknown-candidate", start_hour=10.0),),
            used_ai=True,
            planner_note="invalid candidate must use rules fallback",
        )

    if scenario == "no_candidates":
        planner_call = _planner
        monkeypatch.setattr(
            generator.place_service, "places_for_planner", lambda *_args, **_kwargs: ()
        )
        provider_fetch = lambda _destination: ()
    else:
        planner_call = _planner if scenario == "planner" else invalid_planner
        monkeypatch.setattr(generator.planner, "plan_day", planner_call)
        provider_fetch = lambda _destination: _provider_places(legacy_seed.city)

    command = GeneratorIdempotentCommand(
        idempotency_key=f"generator-commit-legacy-parity-{scenario}",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    protected_result = execute_protected_plan_generation(
        session_factory=factory,
        coordinator=_coordinator(factory),
        scope=_scope(protected_seed),
        credential=CredentialProof.from_bearer_token(protected_seed.token),
        command=command,
        provider_fetch=provider_fetch,
        planner_call=planner_call,
    )
    with factory.begin() as session:
        organizer = session.get(TripMembership, legacy_seed.organizer_id)
        assert organizer is not None
        legacy_result = generator.generate_plan(session, legacy_seed.trip_id, organizer)
        legacy_plan_id = legacy_result.plan.id
        legacy_outcome = {
            "status": legacy_result.status,
            "blocked_reason": legacy_result.blocked_reason,
            "blocked_code": legacy_result.blocked_code,
            "generated_by": legacy_result.generated_by,
            "used_ai": legacy_result.used_ai,
            "days": _day_semantics(legacy_result.days),
        }
    protected_outcome = {
        "status": protected_result.payload["status"],
        "blocked_reason": protected_result.payload["blocked_reason"],
        "blocked_code": protected_result.payload["blocked_code"],
        "generated_by": protected_result.payload["generated_by"],
        "used_ai": protected_result.payload["used_ai"],
        "days": _day_semantics(protected_result.payload["days"]),
    }

    assert protected_outcome == legacy_outcome
    with factory() as session:
        legacy_origins = tuple(sorted(
            session.scalars(
                select(PlanChange.origin).where(PlanChange.plan_id == legacy_plan_id)
            ).all()
        ))
        protected_origins = tuple(sorted(
            session.scalars(
                select(PlanChange.origin).where(
                    PlanChange.plan_id == protected_result.payload["plan_id"]
                )
            ).all()
        ))
        assert protected_origins == legacy_origins


def test_existing_item_refuses_before_external_work_or_generator_ledger(generator_commit_store):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, currency="USD", revision=0)
        session.add(plan)
        session.flush()
        session.add(
            PlanItem(
                plan_id=plan.id,
                day_index=1,
                day_date=date(2027, 7, 10),
                start_hour=10.0,
                title="Existing itinerary decision",
                place="Existing place",
            )
        )
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-existing-items",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    with pytest.raises(generator.PlanAlreadyHasItems):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=_coordinator(factory),
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=lambda _destination: pytest.fail("existing-item path called provider"),
            planner_call=lambda _payload: pytest.fail("existing-item path called planner"),
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MutationIdempotency).where(
            MutationIdempotency.trip_id == data.trip_id
        )) == 0


@pytest.mark.parametrize(
    "stage",
    [
        "after_plan_created",
        "after_safe_outcome",
        "after_domain_effect",
        "after_revision_increment",
        "after_ledger_insert",
    ],
)
def test_real_generator_failure_checkpoints_roll_back_plan_children_and_ledger(
    generator_commit_store, stage: str,
):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    class FailingCoordinator(GeneratorDurableCoordinator):
        def _checkpoint(self, current_stage: str) -> None:
            if current_stage == stage:
                raise RuntimeError(f"injected {stage}")

    factory, seed = generator_commit_store
    data = seed()
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-real-rollback",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    coordinator = FailingCoordinator(
        TripTransactionKernel(factory),
        StaticHmacKeyring(HmacSigningKey("generator-commit-v1", b"generator-commit-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
    )

    with pytest.raises(RuntimeError, match=stage):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=coordinator,
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=lambda _destination: _provider_places(data.city),
            planner_call=_planner,
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        assert session.scalar(select(func.count()).select_from(PlanItem)) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange)) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id
            )
        ) == 0


def test_generator_failure_restores_the_original_existing_plan_state(generator_commit_store):
    """A failed protected generation leaves an existing aggregate byte-for-byte semantic state."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    class FailingCoordinator(GeneratorDurableCoordinator):
        def _checkpoint(self, stage: str) -> None:
            if stage == "after_safe_outcome":
                raise RuntimeError("injected existing-plan rollback")

    factory, seed = generator_commit_store
    data = seed()
    original = {
        "status": "blocked",
        "blocked_reason": "Existing blocked state",
        "estimated_total_per_person": 42.5,
        "currency": "EUR",
        "needs_refresh": True,
        "revision": 7,
    }
    with factory.begin() as session:
        plan = Plan(trip_id=data.trip_id, **original)
        session.add(plan)
        session.flush()
        plan_id = plan.id
    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-existing-plan-rollback",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    coordinator = FailingCoordinator(
        TripTransactionKernel(factory),
        StaticHmacKeyring(HmacSigningKey("generator-commit-v1", b"generator-commit-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
    )

    with pytest.raises(RuntimeError, match="existing-plan rollback"):
        execute_protected_plan_generation(
            session_factory=factory,
            coordinator=coordinator,
            scope=_scope(data),
            credential=CredentialProof.from_bearer_token(data.token),
            command=command,
            provider_fetch=lambda _destination: _provider_places(data.city),
            planner_call=_planner,
        )

    with factory() as session:
        plan = session.get(Plan, plan_id)
        assert plan is not None
        assert {
            "status": plan.status,
            "blocked_reason": plan.blocked_reason,
            "estimated_total_per_person": plan.estimated_total_per_person,
            "currency": plan.currency,
            "needs_refresh": plan.needs_refresh,
            "revision": plan.revision,
        } == original
        assert session.scalar(select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan_id)) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange).where(PlanChange.plan_id == plan_id)) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id,
                MutationIdempotency.operation_code == "plan.generate.v1",
            )
        ) == 0


@pytest.mark.parametrize(
    ("failure_target", "expected_message"),
    [
        ("plan_item", "injected PlanItem flush"),
        ("plan_change", "injected PlanChange flush"),
        ("final_flush", "injected final ledger flush"),
    ],
)
def test_generator_flush_failures_roll_back_real_children_and_final_ledger(
    generator_commit_store,
    test_engine,
    failure_target: str,
    expected_message: str,
):
    """A database failure at any real persistence flush cannot leave an orphan row."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    class ArmingCoordinator(GeneratorDurableCoordinator):
        def _checkpoint(self, stage: str) -> None:
            if (
                failure_target in {"plan_item", "plan_change"} and stage == "after_plan_created"
            ) or (failure_target == "final_flush" and stage == "after_ledger_insert"):
                armed["value"] = True

    factory, seed = generator_commit_store
    data = seed()
    armed = {"value": False}
    insert_counts = {"plan_item": 0, "plan_change": 0, "ledger": 0}

    def fail_selected_flush(_connection, _cursor, statement, _parameters, _context, _executemany):
        if not armed["value"]:
            return
        normalized = statement.lower()
        if "insert into plan_item" in normalized:
            insert_counts["plan_item"] += 1
            if failure_target == "plan_item" and insert_counts["plan_item"] == 2:
                raise RuntimeError("injected PlanItem flush")
        elif "insert into plan_change" in normalized:
            insert_counts["plan_change"] += 1
            if failure_target == "plan_change" and insert_counts["plan_change"] == 1:
                raise RuntimeError("injected PlanChange flush")
        elif "insert into mutation_idempotency" in normalized:
            insert_counts["ledger"] += 1
            if failure_target == "final_flush" and insert_counts["ledger"] == 1:
                raise RuntimeError("injected final ledger flush")

    command = GeneratorIdempotentCommand(
        idempotency_key=f"generator-commit-flush-{failure_target}",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    coordinator = ArmingCoordinator(
        TripTransactionKernel(factory),
        StaticHmacKeyring(HmacSigningKey("generator-commit-v1", b"generator-commit-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
    )
    event.listen(test_engine, "before_cursor_execute", fail_selected_flush)
    try:
        with pytest.raises(RuntimeError, match=expected_message):
            execute_protected_plan_generation(
                session_factory=factory,
                coordinator=coordinator,
                scope=_scope(data),
                credential=CredentialProof.from_bearer_token(data.token),
                command=command,
                provider_fetch=lambda _destination: _provider_places(data.city),
                planner_call=_planner,
            )
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_selected_flush)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Plan).where(Plan.trip_id == data.trip_id)) == 0
        assert session.scalar(select(func.count()).select_from(PlanItem)) == 0
        assert session.scalar(select(func.count()).select_from(PlanChange)) == 0
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id,
                MutationIdempotency.operation_code == "plan.generate.v1",
            )
        ) == 0


def test_concurrent_same_key_generators_recheck_ledger_after_real_trip_lock_contention(
    generator_commit_store,
    test_engine,
):
    from app.domain.plans.generator_commit import execute_protected_plan_generation

    class BlockingCoordinator(GeneratorDurableCoordinator):
        def __init__(self, *args, entered: Event, release: Event, **kwargs):
            super().__init__(*args, **kwargs)
            self._entered = entered
            self._release = release
            self._blocked_once = False
            self.blocking_thread_id: int | None = None

        def _checkpoint(self, stage: str) -> None:
            if stage == "after_domain_effect" and not self._blocked_once:
                self._blocked_once = True
                self.blocking_thread_id = current_thread().ident or 0
                self._entered.set()
                assert self._release.wait(5), "test did not release first Generator commit"

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        place_service._upsert_geoapify(session, _provider_places(data.city))

    command = GeneratorIdempotentCommand(
        idempotency_key="generator-commit-concurrent-same-key",
        fingerprint_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    first_commit_entered, release_first_commit = Event(), Event()
    both_external, release_external = Event(), Event()
    external_threads: set[int] = set()
    external_lock = Lock()
    thread_ids: dict[str, int] = {}
    backend_pids: dict[int, int] = {}
    final_phase = Event()
    second_final_root_attempt = Event()
    results: Queue[object] = Queue()
    errors: Queue[BaseException] = Queue()
    coordinator = BlockingCoordinator(
        TripTransactionKernel(factory),
        StaticHmacKeyring(HmacSigningKey("generator-commit-v1", b"generator-commit-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
        entered=first_commit_entered,
        release=release_first_commit,
    )

    def barrier_planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
        thread_id = current_thread().ident or 0
        with external_lock:
            external_threads.add(thread_id)
            if len(external_threads) == 2:
                both_external.set()
        assert release_external.wait(5), "test did not release both detached external-work calls"
        return _planner(payload)

    def run(label: str) -> None:
        thread_ids[label] = current_thread().ident or 0
        try:
            results.put(
                execute_protected_plan_generation(
                    session_factory=factory,
                    coordinator=coordinator,
                    scope=_scope(data),
                    credential=CredentialProof.from_bearer_token(data.token),
                    command=command,
                    provider_fetch=lambda _destination: pytest.fail("fresh cache called provider"),
                    planner_call=barrier_planner,
                    minimum=1,
                )
            )
        except BaseException as exc:
            errors.put(exc)

    def capture_root_attempt(connection, _cursor, statement, _parameters, _context, _executemany):
        if not final_phase.is_set():
            return
        normalized = statement.lower()
        if "from trip" not in normalized or "for update" not in normalized:
            return
        thread_id = current_thread().ident or 0
        pid = connection.connection.driver_connection.info.backend_pid
        if thread_id in thread_ids.values():
            backend_pids[thread_id] = pid
        if len(backend_pids) == 2:
            second_final_root_attempt.set()

    event.listen(test_engine, "before_cursor_execute", capture_root_attempt)
    first = Thread(target=run, args=("first",), daemon=True)
    second = Thread(target=run, args=("second",), daemon=True)
    try:
        first.start()
        second.start()
        if not both_external.wait(5):
            release_external.set()
            first.join(5)
            second.join(5)
            if not errors.empty():
                raise errors.get()
            raise AssertionError("both commands did not finish replay-preflight and reach planner")
        final_phase.set()
        release_external.set()
        assert first_commit_entered.wait(5), "first command did not hold Final Commit root lock"
        assert second_final_root_attempt.wait(5), "second command did not attempt Final Commit root lock"
        assert coordinator.blocking_thread_id is not None
        assert len(backend_pids) == 2
        blocking_pid = backend_pids[coordinator.blocking_thread_id]
        waiting_pid = next(
            pid for thread_id, pid in backend_pids.items()
            if thread_id != coordinator.blocking_thread_id
        )
        deadline = monotonic() + 5
        blocked = False
        while monotonic() < deadline:
            with test_engine.connect() as observer:
                blocked = bool(
                    observer.scalar(
                        text("SELECT :first_pid = ANY(pg_blocking_pids(:second_pid))"),
                        {"first_pid": blocking_pid, "second_pid": waiting_pid},
                    )
                )
            if blocked:
                break
        assert blocked, "second Final Commit was not blocked by the first Trip root lock"
        release_first_commit.set()
        first.join(5)
        second.join(5)
    finally:
        release_external.set()
        release_first_commit.set()
        first.join(5)
        second.join(5)
        event.remove(test_engine, "before_cursor_execute", capture_root_attempt)

    assert not first.is_alive() and not second.is_alive()
    assert errors.empty(), errors.get() if not errors.empty() else ""
    observed = [results.get_nowait(), results.get_nowait()]
    assert {result.replayed for result in observed} == {False, True}
    with factory() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert plan is not None and plan.revision == 1
        assert session.scalar(select(func.count()).select_from(MutationIdempotency).where(
            MutationIdempotency.trip_id == data.trip_id
        )) == 1
        assert session.scalar(select(func.count()).select_from(PlanChange).where(PlanChange.plan_id == plan.id)) == session.scalar(
            select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)
        )


def test_concurrent_different_keys_serialize_then_reject_stale_generator_draft(
    generator_commit_store,
    test_engine,
):
    """A different command key must not turn a stale draft into a second plan write."""

    from app.domain.plans.generator_commit import execute_protected_plan_generation

    class BlockingCoordinator(GeneratorDurableCoordinator):
        def __init__(self, *args, entered: Event, release: Event, **kwargs):
            super().__init__(*args, **kwargs)
            self._entered = entered
            self._release = release
            self._blocked_once = False
            self.blocking_thread_id: int | None = None

        def _checkpoint(self, stage: str) -> None:
            if stage == "after_domain_effect" and not self._blocked_once:
                self._blocked_once = True
                self.blocking_thread_id = current_thread().ident or 0
                self._entered.set()
                assert self._release.wait(5), "test did not release first Generator commit"

    factory, seed = generator_commit_store
    data = seed()
    with factory.begin() as session:
        place_service._upsert_geoapify(session, _provider_places(data.city))

    first_commit_entered, release_first_commit = Event(), Event()
    both_external, release_external = Event(), Event()
    external_threads: set[int] = set()
    external_lock = Lock()
    thread_ids: dict[str, int] = {}
    backend_pids: dict[int, int] = {}
    final_phase = Event()
    second_final_root_attempt = Event()
    results: Queue[object] = Queue()
    errors: Queue[BaseException] = Queue()
    coordinator = BlockingCoordinator(
        TripTransactionKernel(factory),
        StaticHmacKeyring(HmacSigningKey("generator-commit-v1", b"generator-commit-key")),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
        entered=first_commit_entered,
        release=release_first_commit,
    )

    def barrier_planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
        thread_id = current_thread().ident or 0
        with external_lock:
            external_threads.add(thread_id)
            if len(external_threads) == 2:
                both_external.set()
        assert release_external.wait(5), "test did not release detached external work"
        return _planner(payload)

    def run(label: str, idempotency_key: str) -> None:
        thread_ids[label] = current_thread().ident or 0
        command = GeneratorIdempotentCommand(
            idempotency_key=idempotency_key,
            fingerprint_version=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        try:
            results.put(
                execute_protected_plan_generation(
                    session_factory=factory,
                    coordinator=coordinator,
                    scope=_scope(data),
                    credential=CredentialProof.from_bearer_token(data.token),
                    command=command,
                    provider_fetch=lambda _destination: pytest.fail("fresh cache called provider"),
                    planner_call=barrier_planner,
                    minimum=1,
                )
            )
        except BaseException as exc:
            errors.put(exc)

    def capture_root_attempt(connection, _cursor, statement, _parameters, _context, _executemany):
        if not final_phase.is_set():
            return
        normalized = statement.lower()
        if "from trip" not in normalized or "for update" not in normalized:
            return
        thread_id = current_thread().ident or 0
        pid = connection.connection.driver_connection.info.backend_pid
        if thread_id in thread_ids.values():
            backend_pids[thread_id] = pid
        if len(backend_pids) == 2:
            second_final_root_attempt.set()

    event.listen(test_engine, "before_cursor_execute", capture_root_attempt)
    first = Thread(target=run, args=("first", "generator-commit-concurrent-key-a"), daemon=True)
    second = Thread(target=run, args=("second", "generator-commit-concurrent-key-b"), daemon=True)
    try:
        first.start()
        second.start()
        if not both_external.wait(5):
            release_external.set()
            first.join(5)
            second.join(5)
            if not errors.empty():
                raise errors.get()
            raise AssertionError("both different-key commands did not reach detached external work")
        final_phase.set()
        release_external.set()
        assert first_commit_entered.wait(5), "first command did not hold Final Commit root lock"
        assert second_final_root_attempt.wait(5), "second command did not attempt Final Commit root lock"
        assert coordinator.blocking_thread_id is not None
        assert len(backend_pids) == 2
        blocking_pid = backend_pids[coordinator.blocking_thread_id]
        waiting_pid = next(
            pid for thread_id, pid in backend_pids.items()
            if thread_id != coordinator.blocking_thread_id
        )
        deadline = monotonic() + 5
        blocked = False
        while monotonic() < deadline:
            with test_engine.connect() as observer:
                blocked = bool(
                    observer.scalar(
                        text("SELECT :first_pid = ANY(pg_blocking_pids(:second_pid))"),
                        {"first_pid": blocking_pid, "second_pid": waiting_pid},
                    )
                )
            if blocked:
                break
        assert blocked, "second different-key Final Commit was not blocked by the first Trip lock"
        release_first_commit.set()
        first.join(5)
        second.join(5)
    finally:
        release_external.set()
        release_first_commit.set()
        first.join(5)
        second.join(5)
        event.remove(test_engine, "before_cursor_execute", capture_root_attempt)

    assert not first.is_alive() and not second.is_alive()
    assert results.qsize() == 1
    winner = results.get_nowait()
    assert winner.replayed is False
    assert errors.qsize() == 1
    assert isinstance(errors.get_nowait(), StaleGeneratorPreconditions)
    with factory() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == data.trip_id))
        assert plan is not None and plan.revision == 1
        assert session.scalar(
            select(func.count()).select_from(MutationIdempotency).where(
                MutationIdempotency.trip_id == data.trip_id
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(PlanChange).where(PlanChange.plan_id == plan.id)
        ) == session.scalar(
            select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)
        )
