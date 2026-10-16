"""Detached prepare and pure-draft seams for the legacy plan generator.

This module deliberately stops before candidate acquisition, provider calls, plan
creation, persistence, revisions, durable idempotency, and HTTP integration.  It
captures the database facts which the existing generator consumes, then reuses the
generator's established rule helpers over detached values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
import hashlib
from types import MappingProxyType
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...agents import planner
from ...db.models import MemberConstraint, Plan, PlanItem, Preference, Trip, TripMembership
from ..constraints.types import Constraint, ConstraintKind, Importance
from ..idempotency import canonical_request_json
from ..membership_policy import current_participant_membership_clause
from ..places.service import PlannerPlace, normalize_destination
from ..transactions.kernel import DuplicatePlans
from . import generator


PlannerCapability = Callable[[planner.PlanDayInput], planner.PlanDayResult]
SessionFactory = Callable[[], Session]


@dataclass(frozen=True, repr=False)
class PreparedTrip:
    id: str
    persisted_destination: str
    destination: str
    preferred_start_date: date | None
    preferred_end_date: date | None
    dates: tuple[date, ...]
    currency: str
    planning_facts_revision: int


@dataclass(frozen=True, repr=False)
class PreparedPlan:
    id: str
    revision: int
    status: str
    currency: str
    blocked_reason: str | None
    needs_refresh: bool
    item_count: int


@dataclass(frozen=True, repr=False)
class OrganizerReadiness:
    membership_id: str
    role: str
    status: str
    has_submitted_preference: bool


@dataclass(frozen=True, repr=False)
class PreparedConstraint:
    """Structured planning facts only; no private companion text is ever read."""

    id: str
    membership_id: str
    kind: ConstraintKind
    importance: Importance
    params: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, repr=False)
class PreparedPreference:
    """Only preference fields that current planning semantics actually consume."""

    submitted: bool
    top_interests: tuple[str, ...]
    available_start_date: date | None
    available_end_date: date | None
    maximum_budget: float | None


@dataclass(frozen=True, repr=False)
class AvailabilityFact:
    day_date: date
    available_members: int
    submitted_members: int


@dataclass(frozen=True, repr=False)
class PreparedPlanningInputs:
    membership_count: int
    required_constraints: tuple[PreparedConstraint, ...]
    preferences: tuple[PreparedPreference, ...]
    interests: tuple[str, ...]
    meal_budget_cap: float | None
    availability: tuple[AvailabilityFact, ...]
    day_slots: tuple[tuple[int, date], ...]
    preferred_activity_counts: tuple[int, ...]


@dataclass(frozen=True, repr=False)
class PreparedGeneration:
    """An immutable, Session-free view of every current generator read fact."""

    trip: PreparedTrip
    organizer: OrganizerReadiness
    plan: PreparedPlan | None
    inputs: PreparedPlanningInputs
    generation_eligible: bool
    planning_input_fingerprint: str


@dataclass(frozen=True)
class GeneratedItemDraft:
    day_index: int
    day_date: date
    start_hour: float
    poi: generator.Poi
    generated_by: str
    change_origin: str
    change_reason: str


@dataclass(frozen=True)
class GeneratedDayDraft:
    day_index: int
    day_date: date
    items: tuple[GeneratedItemDraft, ...]
    generated_by: str
    used_ai: bool
    planner_note: str | None


@dataclass(frozen=True)
class GeneratedDraft:
    status: str
    days: tuple[GeneratedDayDraft, ...]
    items: tuple[GeneratedItemDraft, ...]
    generated_by: str
    used_ai: bool
    planner_note: tuple[generator.DayPlannerNote, ...] | None
    estimated_total_per_person: float | None
    blocked_reason: None = None
    blocked_code: None = None


@dataclass(frozen=True)
class BlockedDraft:
    status: str
    days: tuple[GeneratedDayDraft, ...]
    items: tuple[GeneratedItemDraft, ...]
    blocked_reason: str
    blocked_code: str
    generated_by: str = "rules"
    used_ai: bool = False
    planner_note: None = None
    estimated_total_per_person: None = None


DraftCalculation = GeneratedDraft | BlockedDraft


def prepare_generation(
    session_factory: SessionFactory,
    trip_id: str,
    organizer_membership_id: str,
) -> PreparedGeneration:
    """Materialize a PostgreSQL-repeatable, read-only generation snapshot.

    The caller provides a factory, rather than a request Session, so no request
    identity map or transaction crosses this boundary.  Every ORM row is projected
    into immutable values before the owned Session is closed.
    """

    session = session_factory()
    try:
        if session.get_bind().dialect.name != "postgresql":
            raise RuntimeError("generator preparation requires PostgreSQL repeatable-read semantics")
        with session.begin():
            # This must be the transaction's first database statement.  It does not
            # change connection-global isolation and PostgreSQL rejects writes later.
            session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            return capture_generation_snapshot(session, trip_id, organizer_membership_id)
    finally:
        session.close()


def capture_generation_snapshot(
    session: Session,
    trip_id: str,
    organizer_membership_id: str,
) -> PreparedGeneration:
    """Project the B.2D1 generation facts from an already-owned Session.

    ``prepare_generation`` remains the only snapshot owner. B.2D4 calls this
    shared projection under its already locked final transaction solely to
    compare current authoritative facts with the detached snapshot; it does not
    open a second Session, begin a transaction, or persist anything.
    """

    trip = session.get(Trip, trip_id)
    if trip is None:
        raise generator.TripNotFound("Trip not found")

    organizer = session.scalar(
        select(TripMembership).where(
            TripMembership.id == organizer_membership_id,
            TripMembership.trip_id == trip_id,
            current_participant_membership_clause(),
        )
    )
    if organizer is None or organizer.role != "organizer":
        raise generator.OrganizerRequired("Only the organizer can generate a plan")
    organizer_pref = session.scalar(
        select(Preference).where(Preference.trip_membership_id == organizer.id)
    )
    organizer_ready = (
        organizer.status == "preferences_submitted"
        and organizer_pref is not None
        and organizer_pref.submitted_at is not None
    )
    if not organizer_ready:
        raise generator.OrganizerPreferencesRequired(
            "The organizer must submit preferences before generating a plan"
        )

    plans = tuple(
        session.scalars(
            select(Plan).where(Plan.trip_id == trip_id).order_by(Plan.created_at, Plan.id)
        ).all()
    )
    if len(plans) > 1:
        raise DuplicatePlans("Trip has multiple Plans")
    plan = plans[0] if plans else None
    item_count = (
        session.scalar(
            select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)
        )
        if plan is not None
        else 0
    ) or 0

    membership_count = session.scalar(
        select(func.count())
        .select_from(TripMembership)
        .where(
            TripMembership.trip_id == trip_id,
            current_participant_membership_clause(),
        )
    ) or 0
    constraint_rows = tuple(
        session.scalars(
            select(MemberConstraint)
            .join(TripMembership, TripMembership.id == MemberConstraint.trip_membership_id)
            .where(
                TripMembership.trip_id == trip_id,
                current_participant_membership_clause(),
                MemberConstraint.importance == Importance.REQUIRED.value,
            )
            .order_by(MemberConstraint.created_at)
        ).all()
    )
    enforce_budget_ceilings = membership_count > 1
    constraints = tuple(
        PreparedConstraint(
            id=row.id,
            membership_id=row.trip_membership_id,
            kind=ConstraintKind(row.kind),
            importance=Importance(row.importance),
            params=_freeze_value(generator._normalize_params(dict(row.params or {}))),
        )
        for row in constraint_rows
        if enforce_budget_ceilings or row.kind != ConstraintKind.BUDGET_CEILING.value
    )
    preference_rows = tuple(
        session.scalars(
            select(Preference)
            .join(TripMembership, TripMembership.id == Preference.trip_membership_id)
            .where(
                TripMembership.trip_id == trip_id,
                current_participant_membership_clause(),
            )
            .order_by(Preference.submitted_at)
        ).all()
    )
    preferences = tuple(
        PreparedPreference(
            submitted=row.submitted_at is not None,
            top_interests=tuple(row.top_interests or ()),
            available_start_date=(
                row.available_start_date if row.submitted_at is not None else None
            ),
            available_end_date=(
                row.available_end_date if row.submitted_at is not None else None
            ),
            maximum_budget=(
                float(row.maximum_budget)
                if row.submitted_at is not None and row.maximum_budget is not None
                else None
            ),
        )
        for row in preference_rows
    )
    detached_trip = PreparedTrip(
        id=trip.id,
        persisted_destination=trip.destination,
        destination=normalize_destination(trip.destination),
        preferred_start_date=trip.preferred_start_date,
        preferred_end_date=trip.preferred_end_date,
        dates=generator._trip_dates(trip),
        currency=trip.currency,
        planning_facts_revision=trip.planning_facts_revision,
    )
    inputs = _planning_inputs(
        dates=detached_trip.dates,
        membership_count=membership_count,
        constraints=constraints,
        preferences=preferences,
    )
    prepared = PreparedGeneration(
        trip=detached_trip,
        organizer=OrganizerReadiness(
            membership_id=organizer.id,
            role=organizer.role,
            status=organizer.status,
            has_submitted_preference=organizer_ready,
        ),
        plan=(
            None
            if plan is None
            else PreparedPlan(
                id=plan.id,
                revision=plan.revision,
                status=plan.status,
                currency=plan.currency,
                blocked_reason=plan.blocked_reason,
                needs_refresh=plan.needs_refresh,
                item_count=item_count,
            )
        ),
        inputs=inputs,
        generation_eligible=item_count == 0,
        planning_input_fingerprint="",
    )
    return PreparedGeneration(
        trip=prepared.trip,
        organizer=prepared.organizer,
        plan=prepared.plan,
        inputs=prepared.inputs,
        generation_eligible=prepared.generation_eligible,
        planning_input_fingerprint=_planning_input_fingerprint(prepared),
    )


def calculate_draft(
    prepared: PreparedGeneration,
    candidates: tuple[PlannerPlace, ...] | list[PlannerPlace],
    *,
    planner_call: PlannerCapability,
    candidate_failure_code: str | None = None,
) -> DraftCalculation:
    """Calculate a detached draft without owning database or provider lifecycle."""

    if not prepared.generation_eligible:
        raise generator.PlanAlreadyHasItems("This trip already has plan items")

    constraints = tuple(
        Constraint(
            id=constraint.id,
            membership_id=constraint.membership_id,
            kind=constraint.kind,
            importance=constraint.importance,
            params=_thaw_mapping(constraint.params),
        )
        for constraint in prepared.inputs.required_constraints
    )
    poi_pool = tuple(_detached_poi(candidate) for candidate in tuple(candidates))
    blocked_reason = generator._blocked_reason(
        prepared.trip.dates,
        constraints,
        prepared.inputs.day_slots,
    )
    blocked_code = generator._blocked_code(blocked_reason)
    if candidate_failure_code == generator.DESTINATION_NOT_FOUND_CODE:
        return _blocked(
            prepared.trip.dates,
            "We couldn't find this destination.",
            generator.DESTINATION_NOT_FOUND_CODE,
        )
    if not poi_pool:
        return _blocked(
            prepared.trip.dates,
            generator.NO_PLACES_BLOCKED_REASON,
            generator.NO_PLACE_CANDIDATES_CODE,
        )

    allow_reuse_across_days = not any(
        constraint.kind is ConstraintKind.BUDGET_CEILING for constraint in constraints
    )
    draft_items: list[generator.DraftItem] = []
    used: set[str] = set()
    accepted_days: list[generator.DayDraft] = []

    for slot_index, (day_index, day_date) in enumerate(prepared.inputs.day_slots, start=1):
        day = generator._build_day(
            slots_left=sum(prepared.inputs.preferred_activity_counts[slot_index - 1 :]),
            day_index=day_index,
            day_date=day_date,
            constraints=constraints,
            organizer_id=prepared.organizer.membership_id,
            already_selected=tuple(draft_items),
            already_used=used,
            interests=prepared.inputs.interests,
            preferred_activity_count=prepared.inputs.preferred_activity_counts[slot_index - 1],
            allow_reuse_across_days=allow_reuse_across_days,
            poi_pool=poi_pool,
            meal_budget_cap=prepared.inputs.meal_budget_cap,
            planner_call=planner_call,
        )
        if day is None:
            return _blocked(prepared.trip.dates, blocked_reason, blocked_code)
        draft_items.extend(day.items)
        accepted_days.append(day)
        used.update(
            item.poi.title
            for item in day.items
            if not generator._is_flexible_meal_break(item.poi)
        )

    complete_draft = tuple(draft_items)
    if not generator._is_complete(prepared.inputs.day_slots, complete_draft):
        return _blocked(prepared.trip.dates, blocked_reason, blocked_code)

    days = tuple(_generated_day(day) for day in accepted_days)
    items = tuple(item for day in days for item in day.items)
    return GeneratedDraft(
        status="active",
        days=days,
        items=items,
        generated_by=generator._aggregate_generated_by(tuple(accepted_days)),
        used_ai=any(day.used_ai for day in accepted_days),
        planner_note=generator._aggregate_planner_note(tuple(accepted_days)),
        estimated_total_per_person=generator._draft_total(complete_draft),
    )


def _planning_inputs(
    *,
    dates: tuple[date, ...],
    membership_count: int,
    constraints: tuple[PreparedConstraint, ...],
    preferences: tuple[PreparedPreference, ...],
) -> PreparedPlanningInputs:
    interests: list[str] = []
    seen: set[str] = set()
    for preference in preferences:
        for interest in preference.top_interests:
            if interest not in seen:
                interests.append(interest)
                seen.add(interest)
    submitted = tuple(preference for preference in preferences if preference.submitted)
    caps = tuple(
        preference.maximum_budget
        for preference in submitted
        if preference.maximum_budget is not None
    )
    availability = tuple(
        AvailabilityFact(
            day_date=day_date,
            available_members=sum(
                1
                for preference in submitted
                if (
                    preference.available_start_date is None
                    or preference.available_start_date <= day_date
                )
                and (
                    preference.available_end_date is None
                    or day_date <= preference.available_end_date
                )
            ),
            submitted_members=len(submitted),
        )
        for day_date in dates
    ) if submitted else ()
    availability_by_date = {
        item.day_date: (item.available_members, item.submitted_members)
        for item in availability
    }
    day_slots = generator._day_slots_for_generation(dates, availability_by_date)
    return PreparedPlanningInputs(
        membership_count=membership_count,
        required_constraints=constraints,
        preferences=preferences,
        interests=tuple(interests),
        meal_budget_cap=min(caps) if caps else None,
        availability=availability,
        day_slots=day_slots,
        preferred_activity_counts=tuple(
            generator._preferred_activity_count(
                day_index, availability_by_date.get(day_date)
            )
            for day_index, day_date in day_slots
        ),
    )


def _generated_day(day: generator.DayDraft) -> GeneratedDayDraft:
    return GeneratedDayDraft(
        day_index=day.day_index,
        day_date=day.items[0].day_date if day.items else date.min,
        items=tuple(
            GeneratedItemDraft(
                day_index=item.day_index,
                day_date=item.day_date,
                start_hour=item.start_hour,
                poi=item.poi,
                generated_by=item.generated_by,
                change_origin=(
                    "ai_generate" if item.generated_by == "planner" else "rule_generate"
                ),
                change_reason=(
                    "Generated by Planner agent."
                    if item.generated_by == "planner"
                    else "Generated by deterministic rules fallback."
                ),
            )
            for item in day.items
        ),
        generated_by=day.generated_by,
        used_ai=day.used_ai,
        planner_note=day.planner_note,
    )


def _blocked(dates: tuple[date, ...], reason: str, code: str) -> BlockedDraft:
    return BlockedDraft(
        status="blocked",
        days=tuple(
            GeneratedDayDraft(
                day_index=index,
                day_date=day_date,
                items=(),
                generated_by="rules",
                used_ai=False,
                planner_note=None,
            )
            for index, day_date in enumerate(dates, start=1)
        ),
        items=(),
        blocked_reason=reason,
        blocked_code=code,
    )


def _planning_input_fingerprint(prepared: PreparedGeneration) -> str:
    return hashlib.sha256(
        canonical_request_json(
            "generator.planning-input.v1",
            _fingerprint_projection(prepared),
            fingerprint_version=1,
        )
    ).hexdigest()


def _fingerprint_projection(prepared: PreparedGeneration) -> dict[str, Any]:
    return {
        "trip": {
            "id": prepared.trip.id,
            "destination": prepared.trip.destination,
            "preferred_start_date": prepared.trip.preferred_start_date,
            "preferred_end_date": prepared.trip.preferred_end_date,
            "dates": prepared.trip.dates,
            "currency": prepared.trip.currency,
            "planning_facts_revision": prepared.trip.planning_facts_revision,
        },
        "organizer": {
            "membership_id": prepared.organizer.membership_id,
            "role": prepared.organizer.role,
            "status": prepared.organizer.status,
            "has_submitted_preference": prepared.organizer.has_submitted_preference,
        },
        "plan": None if prepared.plan is None else {
            "id": prepared.plan.id,
            "revision": prepared.plan.revision,
            "status": prepared.plan.status,
            "blocked_reason": prepared.plan.blocked_reason,
            "needs_refresh": prepared.plan.needs_refresh,
            "item_count": prepared.plan.item_count,
        },
        "generation_eligible": prepared.generation_eligible,
        "inputs": {
            "membership_count": prepared.inputs.membership_count,
            "required_constraints": tuple(
                {
                    "id": item.id,
                    "membership_id": item.membership_id,
                    "kind": item.kind.value,
                    "importance": item.importance.value,
                    "params": item.params,
                }
                for item in prepared.inputs.required_constraints
            ),
            "preferences": tuple(
                {
                    "submitted": item.submitted,
                    "top_interests": item.top_interests,
                    "available_start_date": item.available_start_date,
                    "available_end_date": item.available_end_date,
                    "maximum_budget": item.maximum_budget,
                }
                for item in prepared.inputs.preferences
            ),
            "interests": prepared.inputs.interests,
            "meal_budget_cap": prepared.inputs.meal_budget_cap,
            "availability": tuple(
                {
                    "day_date": item.day_date,
                    "available_members": item.available_members,
                    "submitted_members": item.submitted_members,
                }
                for item in prepared.inputs.availability
            ),
            "day_slots": prepared.inputs.day_slots,
            "preferred_activity_counts": prepared.inputs.preferred_activity_counts,
        },
    }


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(child) for key, child in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(child) for child in value)
    return value


def _detached_poi(candidate: PlannerPlace) -> generator.Poi:
    """Copy candidate sequences so drafts never share caller-owned containers."""

    poi = generator._as_poi(candidate)
    return generator.Poi(
        candidate_id=poi.candidate_id,
        title=poi.title,
        category=poi.category,
        place=poi.place,
        lat=poi.lat,
        lng=poi.lng,
        price=poi.price,
        duration_min=poi.duration_min,
        opens=poi.opens,
        closes=poi.closes,
        walk=poi.walk,
        access=tuple(poi.access),
        diet=tuple(poi.diet),
        tags=tuple(poi.tags),
        local_title=poi.local_title,
        photo_url=poi.photo_url,
        source=poi.source,
        opening_hours=poi.opening_hours,
    )


def _thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _thaw_value(child) for key, child in value.items()}


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _thaw_mapping(value)
    if isinstance(value, tuple):
        return [_thaw_value(child) for child in value]
    return value
