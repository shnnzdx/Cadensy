from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.agents import base, planner
from app.db.models import (
    MemberConstraint,
    MemberConstraintPrivate,
    Plan,
    PlanChange,
    PlanItem,
    Preference,
    Trip,
    TripMembership,
    User,
)
from app.domain.plans import generator
from app.domain.plans.generator_lifecycle import (
    BlockedDraft,
    GeneratedDraft,
    PreparedGeneration,
    calculate_draft,
    prepare_generation,
)
from app.domain.places.service import PlannerPlace
from app.domain.transactions.kernel import DuplicatePlans


def _places() -> tuple[PlannerPlace, ...]:
    sights = tuple(
        PlannerPlace(
            candidate_id=f"sight-{index}",
            name=f"Museum {index}",
            location="New York City",
            latitude=40.70 + index / 1000,
            longitude=-74.00 - index / 1000,
            category="tourism.museum",
            address="Museum Mile",
            image_url=None,
            opening_hours=None,
            price=12.0,
            duration_min=90,
            opens=9.0,
            closes=21.0,
            tags=("tourism", "museum", "culture"),
        )
        for index in range(8)
    )
    meals = tuple(
        PlannerPlace(
            candidate_id=f"meal-{index}",
            name=f"Restaurant {index}",
            location="New York City",
            latitude=40.71 + index / 1000,
            longitude=-74.01 - index / 1000,
            category="catering.restaurant",
            address="Food Street",
            image_url=None,
            opening_hours=None,
            price=14.0,
            duration_min=60,
            opens=11.0,
            closes=22.0,
            diet=("vegetarian",),
            tags=("catering", "restaurant", "food"),
        )
        for index in range(6)
    )
    return sights + meals


def _valid_planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
    sightseeing = tuple(
        candidate
        for candidate in payload.candidates
        if "restaurant" not in {tag.casefold() for tag in candidate.tags}
    )
    return planner.PlanDayResult(
        picks=tuple(
            planner.Pick(candidate_id=candidate.candidate_id, start_hour=start)
            for candidate, start in zip(sightseeing[:3], (10.0, 14.0, 16.0), strict=True)
        ),
        used_ai=True,
        planner_note="Injected deterministic planner result.",
    )


@pytest.fixture
def lifecycle_store(test_engine):
    """Committed rows visible to the snapshot's owned, independent Session."""

    factory = sessionmaker(bind=test_engine, future=True, expire_on_commit=False)
    trip_ids: list[str] = []

    def seed(
        *,
        with_plan: bool = True,
        plan_items: int = 0,
        organizer_preferences: bool = True,
        destination: str = "nyc",
        extra_plan: bool = False,
        participant_availability_start: date | None = None,
    ) -> dict[str, object]:
        session = factory()
        try:
            stamp = uuid4().hex
            organizer_user = User(name="Organizer", email=f"organizer-{stamp}@example.com")
            participant_user = User(name="Participant", email=f"participant-{stamp}@example.com")
            session.add_all((organizer_user, participant_user))
            session.flush()

            start = date(2027, 5, 10)
            trip = Trip(
                name="Detached generator lifecycle",
                destination=destination,
                preferred_start_date=start,
                preferred_end_date=start + timedelta(days=1),
                currency="USD",
                created_by_user_id=organizer_user.id,
            )
            session.add(trip)
            session.flush()

            organizer = TripMembership(
                trip_id=trip.id,
                user_id=organizer_user.id,
                role="organizer",
                status="preferences_submitted" if organizer_preferences else "joined",
            )
            participant = TripMembership(
                trip_id=trip.id,
                user_id=participant_user.id,
                role="participant",
                status="joined",
            )
            session.add_all((organizer, participant))
            session.flush()

            if organizer_preferences:
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
            if participant_availability_start is not None:
                session.add(
                    Preference(
                        trip_membership_id=participant.id,
                        available_start_date=participant_availability_start,
                        available_end_date=start + timedelta(days=1),
                        maximum_budget=120.0,
                        top_interests=["history"],
                        submitted_at=datetime.now(timezone.utc),
                    )
                )
            session.add_all(
                (
                    MemberConstraint(
                        trip_membership_id=organizer.id,
                        kind="time_window",
                        importance="required",
                        params={"earliest_hour": 9.0, "latest_hour": 22.0},
                    ),
                    MemberConstraint(
                        trip_membership_id=participant.id,
                        kind="budget_ceiling",
                        importance="required",
                        params={"max_total_per_person": 120.0},
                    ),
                    MemberConstraint(
                        trip_membership_id=participant.id,
                        kind="dietary",
                        importance="required",
                        params={"required_tags": ["vegetarian"]},
                    ),
                )
            )
            session.flush()
            dietary = session.scalar(
                select(MemberConstraint).where(
                    MemberConstraint.trip_membership_id == participant.id,
                    MemberConstraint.kind == "dietary",
                )
            )
            assert dietary is not None
            session.add(
                MemberConstraintPrivate(
                    constraint_id=dietary.id,
                    original_text="I have a private dietary sentence that must not leave this boundary.",
                    visibility="planning_only",
                )
            )

            plan = None
            if with_plan:
                plan = Plan(trip_id=trip.id, currency="USD", revision=0)
                session.add(plan)
                session.flush()
                for item_index in range(plan_items):
                    session.add(
                        PlanItem(
                            plan_id=plan.id,
                            day_index=1,
                            day_date=start,
                            start_hour=9.0 + item_index,
                            duration_min=60,
                            title=f"Existing {item_index}",
                            place="Existing Place",
                        )
                    )
                if extra_plan:
                    session.add(Plan(trip_id=trip.id, currency="USD", revision=0))

            session.commit()
            trip_ids.append(trip.id)
            return {
                "trip_id": trip.id,
                "organizer_id": organizer.id,
                "participant_id": participant.id,
                "plan_id": None if plan is None else plan.id,
                "start": start,
            }
        finally:
            session.close()

    yield factory, seed

    cleanup = factory()
    try:
        for trip_id in trip_ids:
            plan_ids = list(cleanup.scalars(select(Plan.id).where(Plan.trip_id == trip_id)))
            membership_ids = list(
                cleanup.scalars(select(TripMembership.id).where(TripMembership.trip_id == trip_id))
            )
            user_ids = list(
                cleanup.scalars(
                    select(TripMembership.user_id).where(
                        TripMembership.trip_id == trip_id,
                        TripMembership.user_id.is_not(None),
                    )
                )
            )
            if plan_ids:
                cleanup.execute(delete(PlanChange).where(PlanChange.plan_id.in_(plan_ids)))
                cleanup.execute(delete(PlanItem).where(PlanItem.plan_id.in_(plan_ids)))
                cleanup.execute(delete(Plan).where(Plan.id.in_(plan_ids)))
            constraint_ids = list(
                cleanup.scalars(
                    select(MemberConstraint.id).where(
                        MemberConstraint.trip_membership_id.in_(membership_ids)
                    )
                )
            ) if membership_ids else []
            if constraint_ids:
                cleanup.execute(
                    delete(MemberConstraintPrivate).where(
                        MemberConstraintPrivate.constraint_id.in_(constraint_ids)
                    )
                )
                cleanup.execute(delete(MemberConstraint).where(MemberConstraint.id.in_(constraint_ids)))
            if membership_ids:
                cleanup.execute(
                    delete(Preference).where(Preference.trip_membership_id.in_(membership_ids))
                )
                cleanup.execute(delete(TripMembership).where(TripMembership.id.in_(membership_ids)))
            cleanup.execute(delete(Trip).where(Trip.id == trip_id))
            if user_ids:
                cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        cleanup.commit()
    finally:
        cleanup.close()


def test_generation_uses_only_current_participants_and_preserves_inactive_history(
    lifecycle_store, monkeypatch,
):
    """Removed/quarantined data is historical, never a new planning input."""

    factory, seed = lifecycle_store
    data = seed()
    with factory.begin() as db:
        organizer = db.get(TripMembership, data["organizer_id"])
        trip = db.get(Trip, data["trip_id"])
        assert organizer is not None and trip is not None
        removed_user = User(name="Removed", email=f"removed-{uuid4().hex}@example.com")
        quarantined_user = User(
            name="Quarantined", email=f"quarantined-{uuid4().hex}@example.com"
        )
        db.add_all((removed_user, quarantined_user))
        db.flush()
        removed = TripMembership(
            trip_id=trip.id,
            user_id=removed_user.id,
            role="participant",
            status="removed",
        )
        quarantined = TripMembership(
            trip_id=trip.id,
            user_id=quarantined_user.id,
            role="participant",
            status="preferences_submitted",
            quarantined_at=datetime.now(timezone.utc),
            quarantine_reason="security_incident",
            quarantined_by_membership_id=organizer.id,
        )
        db.add_all((removed, quarantined))
        db.flush()
        for membership, marker in ((removed, "removed-only"), (quarantined, "quarantined-only")):
            db.add(
                Preference(
                    trip_membership_id=membership.id,
                    available_start_date=data["start"] + timedelta(days=1),
                    available_end_date=data["start"] + timedelta(days=1),
                    maximum_budget=1.0,
                    top_interests=[marker],
                    submitted_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                MemberConstraint(
                    trip_membership_id=membership.id,
                    kind="budget_ceiling",
                    importance="required",
                    params={"max_total_per_person": 1.0},
                )
            )
        removed_id, quarantined_id = removed.id, quarantined.id

    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    assert prepared.inputs.membership_count == 2
    assert prepared.inputs.interests == ("culture", "food")
    assert prepared.inputs.meal_budget_cap == 150.0
    assert prepared.inputs.day_slots == (
        (1, data["start"]),
        (2, data["start"] + timedelta(days=1)),
    )
    assert {constraint.membership_id for constraint in prepared.inputs.required_constraints} == {
        data["organizer_id"],
        data["participant_id"],
    }
    assert all(
        marker not in repr(prepared)
        for marker in ("removed-only", "quarantined-only")
    )

    # The legacy synchronous public generator reads the same current facts,
    # rather than becoming a second path that revives inactive preferences.
    monkeypatch.setattr(generator.place_service, "places_for_planner", lambda *_args: _places())
    monkeypatch.setattr(planner, "plan_day", _valid_planner)
    session = factory()
    try:
        organizer = session.get(TripMembership, data["organizer_id"])
        assert organizer is not None
        result = generator.generate_plan(session, data["trip_id"], organizer)
        assert result.status == "active"
        session.commit()
    finally:
        session.close()

    # Membership-owned records remain historical rows; filtering planning facts
    # neither deletes nor reparents them.
    verify = factory()
    try:
        assert verify.get(TripMembership, removed_id).status == "removed"
        assert verify.get(TripMembership, quarantined_id).quarantined_at is not None
        assert verify.scalar(
            select(func.count())
            .select_from(Preference)
            .where(Preference.trip_membership_id.in_((removed_id, quarantined_id)))
        ) == 2
    finally:
        verify.close()


def test_prepare_generation_materializes_a_detached_read_only_snapshot(lifecycle_store, test_engine):
    factory, seed = lifecycle_store
    data = seed(participant_availability_start=date(2027, 5, 11))
    statements: list[str] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.casefold())

    event.listen(test_engine, "before_cursor_execute", observe)
    try:
        prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    finally:
        event.remove(test_engine, "before_cursor_execute", observe)

    assert isinstance(prepared, PreparedGeneration)
    assert prepared.trip.destination == "New York City"
    assert prepared.trip.persisted_destination == "nyc"
    assert prepared.plan is not None and prepared.plan.item_count == 0
    assert prepared.inputs.membership_count == 2
    assert prepared.inputs.interests == ("culture", "food", "history")
    assert prepared.inputs.day_slots == ((2, date(2027, 5, 11)),)
    assert prepared.inputs.meal_budget_cap == 120.0
    assert {constraint.kind.value for constraint in prepared.inputs.required_constraints} == {
        "time_window",
        "budget_ceiling",
        "dietary",
    }
    assert all("private dietary sentence" not in repr(value) for value in (prepared, *prepared.inputs.required_constraints))
    with pytest.raises(TypeError):
        prepared.inputs.required_constraints[0].params["earliest_hour"] = 10.0
    assert not any(
        statement.lstrip().startswith(("insert", "update", "delete", "merge"))
        for statement in statements
    )

    verify = factory()
    try:
        trip = verify.get(Trip, data["trip_id"])
        plan = verify.get(Plan, data["plan_id"])
        assert trip is not None and trip.destination == "nyc"
        assert plan is not None and plan.revision == 0
        assert verify.scalar(select(PlanItem.id).where(PlanItem.plan_id == plan.id)) is None
    finally:
        verify.close()


def test_prepare_generation_handles_zero_one_duplicate_and_existing_item_lifecycles(lifecycle_store):
    factory, seed = lifecycle_store
    no_plan = seed(with_plan=False)
    one_empty = seed(with_plan=True)
    existing_item = seed(with_plan=True, plan_items=1)
    duplicate = seed(with_plan=True, extra_plan=True)

    assert prepare_generation(factory, no_plan["trip_id"], no_plan["organizer_id"]).plan is None
    assert prepare_generation(factory, one_empty["trip_id"], one_empty["organizer_id"]).plan is not None
    populated = prepare_generation(factory, existing_item["trip_id"], existing_item["organizer_id"])
    assert populated.plan is not None and populated.plan.item_count == 1
    assert populated.generation_eligible is False
    with pytest.raises(DuplicatePlans):
        prepare_generation(factory, duplicate["trip_id"], duplicate["organizer_id"])
    verify = factory()
    try:
        assert verify.scalar(
            select(func.count()).select_from(Plan).where(Plan.trip_id == no_plan["trip_id"])
        ) == 0
        plan = verify.get(Plan, existing_item["plan_id"])
        assert plan is not None and plan.revision == 0
    finally:
        verify.close()


def test_prepare_generation_enforces_existing_generator_admission_rules(lifecycle_store):
    factory, seed = lifecycle_store
    missing_preferences = seed(organizer_preferences=False)
    valid = seed()

    with pytest.raises(generator.TripNotFound):
        prepare_generation(factory, "does-not-exist", valid["organizer_id"])
    with pytest.raises(generator.OrganizerPreferencesRequired):
        prepare_generation(
            factory,
            missing_preferences["trip_id"],
            missing_preferences["organizer_id"],
        )
    with pytest.raises(generator.OrganizerRequired):
        prepare_generation(factory, valid["trip_id"], valid["participant_id"])


def test_prepared_fingerprint_is_stable_and_changes_with_relevant_facts(lifecycle_store):
    factory, seed = lifecycle_store
    data = seed()
    first = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    second = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    assert first.planning_input_fingerprint == second.planning_input_fingerprint

    writer = factory()
    try:
        trip = writer.get(Trip, data["trip_id"])
        assert trip is not None
        trip.currency = "EUR"
        writer.commit()
    finally:
        writer.close()

    changed = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    assert changed.planning_input_fingerprint != first.planning_input_fingerprint


def test_repeatable_read_snapshot_stays_coherent_during_concurrent_commit(lifecycle_store, test_engine):
    factory, seed = lifecycle_store
    data = seed()
    first_read = Event()
    release = Event()
    result: dict[str, PreparedGeneration] = {}
    errors: list[BaseException] = []

    def pause_after_trip_read(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.casefold().split())
        if 'from trip' in normalized and not first_read.is_set():
            first_read.set()
            assert release.wait(timeout=5.0)

    def capture() -> None:
        try:
            result["prepared"] = prepare_generation(
                factory, data["trip_id"], data["organizer_id"]
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    event.listen(test_engine, "after_cursor_execute", pause_after_trip_read)
    worker = Thread(target=capture)
    worker.start()
    assert first_read.wait(timeout=5.0)
    writer = factory()
    try:
        trip = writer.get(Trip, data["trip_id"])
        preference = writer.scalar(
            select(Preference).where(Preference.trip_membership_id == data["organizer_id"])
        )
        assert trip is not None and preference is not None
        trip.currency = "EUR"
        preference.maximum_budget = 90.0
        writer.commit()
    finally:
        writer.close()
        release.set()
    worker.join(timeout=5.0)
    event.remove(test_engine, "after_cursor_execute", pause_after_trip_read)

    assert not worker.is_alive()
    assert not errors
    assert result["prepared"].trip.currency == "USD"
    assert result["prepared"].inputs.meal_budget_cap == 150.0
    fresh = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    assert fresh.trip.currency == "EUR"
    assert fresh.inputs.meal_budget_cap == 90.0


def test_calculate_draft_is_detached_and_matches_legacy_semantics(lifecycle_store, monkeypatch):
    factory, seed = lifecycle_store
    data = seed()
    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    candidates = _places()

    draft = calculate_draft(prepared, candidates, planner_call=_valid_planner)
    assert isinstance(draft, GeneratedDraft)
    assert draft.status == "active"
    assert draft.generated_by == "planner"
    assert draft.used_ai is True
    assert all(
        item.change_origin
        == ("ai_generate" if item.generated_by == "planner" else "rule_generate")
        for item in draft.items
    )

    monkeypatch.setattr(generator.place_service, "places_for_planner", lambda *_args: candidates)
    monkeypatch.setattr(planner, "plan_day", _valid_planner)
    legacy_session = factory()
    try:
        organizer = legacy_session.get(TripMembership, data["organizer_id"])
        assert organizer is not None
        legacy = generator.generate_plan(legacy_session, data["trip_id"], organizer)
        draft_projection = tuple(
            (item.day_index, item.day_date, item.start_hour, item.poi.title, item.generated_by)
            for item in draft.items
        )
        persisted_projection = tuple(
            (item.day_index, item.day_date, item.start_hour, item.title, item.source)
            for item in legacy.items
        )
        assert legacy.status == draft.status
        assert tuple(row[:4] for row in draft_projection) == tuple(
            row[:4] for row in persisted_projection
        )
        persisted_changes = tuple(
            legacy_session.scalars(
                select(PlanChange)
                .where(PlanChange.plan_id == data["plan_id"])
                .order_by(PlanChange.id)
            ).all()
        )
        assert sorted((item.poi.title, item.change_origin) for item in draft.items) == sorted(
            (change.patch["title"], change.origin) for change in persisted_changes
        )
        assert legacy.plan.estimated_total_per_person == draft.estimated_total_per_person
        legacy_session.commit()
    finally:
        legacy_session.close()


def test_calculate_draft_falls_back_and_blocks_without_writes(lifecycle_store):
    factory, seed = lifecycle_store
    data = seed()
    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])

    def unavailable(_payload: planner.PlanDayInput) -> planner.PlanDayResult:
        raise base.AgentUnavailable("injected provider unavailable")

    fallback = calculate_draft(prepared, _places(), planner_call=unavailable)
    assert isinstance(fallback, GeneratedDraft)
    assert fallback.generated_by == "rules"
    assert fallback.used_ai is False
    blocked = calculate_draft(prepared, (), planner_call=unavailable)
    assert isinstance(blocked, BlockedDraft)
    assert blocked.blocked_code == generator.NO_PLACE_CANDIDATES_CODE
    assert blocked.items == ()

    verify = factory()
    try:
        plan = verify.get(Plan, data["plan_id"])
        assert plan is not None and plan.revision == 0 and plan.status == "active"
        assert verify.scalar(select(PlanItem.id).where(PlanItem.plan_id == plan.id)) is None
    finally:
        verify.close()


@pytest.mark.parametrize(
    "planner_call",
    (
        lambda _payload: planner.PlanDayResult(
            picks=(planner.Pick(candidate_id="not-a-candidate", start_hour=10.0),),
            used_ai=True,
            planner_note="Invalid candidate id.",
        ),
        lambda payload: planner.PlanDayResult(
            picks=tuple(
                planner.Pick(candidate_id=candidate.candidate_id, start_hour=3.0)
                for candidate in payload.candidates[:3]
            ),
            used_ai=True,
            planner_note="Unusable time slots.",
        ),
    ),
    ids=("invalid-candidate", "unusable-time"),
)
def test_calculate_draft_preserves_planner_validation_then_rules_fallback(
    lifecycle_store, planner_call
):
    factory, seed = lifecycle_store
    data = seed()
    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])

    result = calculate_draft(prepared, _places(), planner_call=planner_call)

    assert isinstance(result, GeneratedDraft)
    assert result.generated_by == "rules"
    assert result.used_ai is False
    assert {item.change_origin for item in result.items} == {"rule_generate"}


def test_calculate_draft_handles_destination_failure_and_availability_block(lifecycle_store):
    factory, seed = lifecycle_store
    data = seed(participant_availability_start=date(2027, 5, 12))
    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])

    unavailable_destination = calculate_draft(
        prepared,
        _places(),
        planner_call=_valid_planner,
        candidate_failure_code=generator.DESTINATION_NOT_FOUND_CODE,
    )
    availability = calculate_draft(prepared, _places(), planner_call=_valid_planner)

    assert isinstance(unavailable_destination, BlockedDraft)
    assert unavailable_destination.blocked_code == generator.DESTINATION_NOT_FOUND_CODE
    assert isinstance(availability, BlockedDraft)
    assert availability.blocked_code == "AVAILABILITY_BLOCKED"


def test_calculate_draft_owns_no_candidate_source_and_returns_immutable_values(
    lifecycle_store, monkeypatch
):
    factory, seed = lifecycle_store
    data = seed()
    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    candidates = list(_places())
    candidates[0] = PlannerPlace(
        candidate_id="mutable-tags",
        name="Mutable Source Candidate",
        location="New York City",
        latitude=40.70,
        longitude=-74.00,
        category="tourism.attraction",
        address="Mutable Avenue",
        image_url=None,
        opening_hours=None,
        price=12.0,
        duration_min=90,
        opens=9.0,
        closes=21.0,
        tags=["tourism", "attraction"],  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        generator.place_service,
        "places_for_planner",
        lambda *_args: pytest.fail("draft calculation must not fetch place candidates"),
    )

    result = calculate_draft(prepared, candidates, planner_call=_valid_planner)
    assert isinstance(result, GeneratedDraft)
    first_title = result.items[0].poi.title
    assert all(isinstance(item.poi.tags, tuple) for item in result.items)
    candidates[0].tags.append("caller-mutated")
    candidates[0] = PlannerPlace(
        candidate_id="replaced",
        name="Caller mutation",
        location="Elsewhere",
        latitude=0.0,
        longitude=0.0,
        category="tourism.attraction",
        address=None,
        image_url=None,
        opening_hours=None,
    )
    assert result.items[0].poi.title == first_title
    assert all("caller-mutated" not in item.poi.tags for item in result.items)
    with pytest.raises((AttributeError, TypeError)):
        result.items += ()


def test_calculate_draft_rejects_existing_items_and_never_uses_place_service(lifecycle_store, monkeypatch):
    factory, seed = lifecycle_store
    data = seed(plan_items=1)
    prepared = prepare_generation(factory, data["trip_id"], data["organizer_id"])
    monkeypatch.setattr(
        generator.place_service,
        "places_for_planner",
        lambda *_args: pytest.fail("pure draft calculation must not fetch place candidates"),
    )
    with pytest.raises(generator.PlanAlreadyHasItems):
        calculate_draft(prepared, _places(), planner_call=_valid_planner)
