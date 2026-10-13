"""Plan generation pipeline plus deterministic rules fallback."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...agents import base, planner
from ...db.models import (
    MemberConstraint,
    Plan,
    PlanChange,
    PlanItem,
    Preference,
    Trip,
    TripMembership,
)
from ..constraints.engine import violates
from ..constraints.types import (
    Constraint,
    ConstraintKind,
    Importance,
    ItemView,
    ProposedChange,
    Settledness,
)
from ..places import service as place_service
from ..places.service import normalize_destination

MIN_DAY_ACTIVITIES = planner.MIN_DAY_ACTIVITIES
MAX_DAY_ACTIVITIES = planner.MAX_DAY_ACTIVITIES
DAY_SIGHTSEEING_CANDIDATE_LIMIT = 24
DAY_MEAL_CANDIDATE_LIMIT = 18
MEAL_SEARCH_RADII_KM = (1.5, 3.0, 6.0)
MAX_MEAL_DETOUR_KM = MEAL_SEARCH_RADII_KM[-1]
DAY_ACTIVITY_PATTERN = (3, 2, 4, 3, 2)
RULE_TIME_OPTIONS = {
    "morning": (9.0, 9.5, 10.0, 10.5, 11.0),
    "lunch": (11.75, 12.0, 12.5, 13.0),
    "afternoon": (14.0, 14.5, 15.0, 15.5, 16.0),
    "dinner": (17.75, 18.0, 18.5, 19.0, 19.5),
    "evening": (20.25, 20.75, 21.25),
}
SIGHTSEEING_TIME_PATTERNS = {
    2: ((9.5, 16.0), (10.0, 16.5)),
    3: ((9.0, 14.25, 16.5), (9.5, 14.75, 16.75)),
    4: ((9.0, 11.0, 14.25, 16.5), (9.5, 11.0, 14.75, 16.75)),
}
VALIDATION_START_TIMES = tuple(
    start_hour for options in RULE_TIME_OPTIONS.values() for start_hour in options
)
BUDGET_BLOCKED_REASON = (
    "At least one member's budget ceiling cannot be met with the places available."
)
CONSTRAINTS_BLOCKED_REASON = (
    "Cadensy could not build a complete itinerary from the current dates and required constraints."
)
DATES_BLOCKED_REASON = "Trip dates are missing or invalid, so Cadensy cannot build an itinerary."
NO_PLACES_BLOCKED_REASON = (
    "No usable places were available for this destination. Check the destination name "
    "or try again when place data is available."
)
DESTINATION_NOT_FOUND_CODE = "DESTINATION_NOT_FOUND"
NO_PLACE_CANDIDATES_CODE = "NO_PLACE_CANDIDATES"
CONSTRAINTS_BLOCKED_CODE = "CONSTRAINTS_BLOCKED"
AVAILABILITY_BLOCKED_REASON = (
    "The submitted availability windows do not leave any shared trip dates to plan."
)
RULES_DAY_NOTE = "Deterministic rules fallback generated this day."


class TripNotFound(Exception):
    """The requested trip does not exist."""


class OrganizerRequired(Exception):
    """Only the organizer can generate a plan."""


class OrganizerPreferencesRequired(Exception):
    """The organizer must submit preferences before generation."""


class PlanAlreadyHasItems(Exception):
    """Do not regenerate over existing itinerary decisions."""


@dataclass(frozen=True)
class Poi:
    candidate_id: str
    title: str
    category: str | None
    place: str
    lat: float | None
    lng: float | None
    price: float | None
    duration_min: int | None
    opens: float | None
    closes: float | None
    walk: str | None
    access: tuple[str, ...]
    diet: tuple[str, ...]
    tags: tuple[str, ...]
    local_title: str | None = None
    photo_url: str | None = None
    source: str = "geoapify"
    opening_hours: str | None = None

    @property
    def is_meal(self) -> bool:
        return planner.is_reliable_meal_candidate(self.tags)


@dataclass(frozen=True)
class DraftItem:
    day_index: int
    day_date: date
    start_hour: float
    poi: Poi
    generated_by: str


@dataclass(frozen=True)
class DayDraft:
    day_index: int
    items: tuple[DraftItem, ...]
    generated_by: str
    used_ai: bool = False
    planner_note: str | None = None


@dataclass(frozen=True)
class DayPlannerNote:
    day_index: int
    source: str
    note: str
    used_ai: bool


@dataclass(frozen=True)
class GenerationResult:
    plan: Plan
    status: str
    days: list[dict]
    blocked_reason: str | None
    generated_by: str
    blocked_code: str | None = None
    used_ai: bool = False
    planner_note: tuple[DayPlannerNote, ...] | None = None
    items: tuple[PlanItem, ...] = ()


def generate_plan(db: Session, trip_id: str, organizer: TripMembership) -> GenerationResult:
    """Generate, validate, and persist a plan.

    The API layer intentionally delegates all business gates here: organizer
    authorization, organizer preference readiness, regeneration protection,
    candidate filtering, retry, validation, and write behavior.
    """
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise TripNotFound("Trip not found")
    if organizer.trip_id != trip_id or organizer.role != "organizer":
        raise OrganizerRequired("Only the organizer can generate a plan")
    if not _organizer_has_submitted_preferences(db, organizer):
        raise OrganizerPreferencesRequired(
            "The organizer must submit preferences before generating a plan"
        )
    if _trip_has_items(db, trip_id):
        raise PlanAlreadyHasItems("This trip already has plan items")

    # Normalize legacy trips as well as newly created trips before cache/provider lookup.
    trip.destination = normalize_destination(trip.destination)

    plan = _plan_for_trip(db, trip)
    constraints = _load_required_constraints(
        db,
        trip_id,
        enforce_budget_ceilings=_trip_membership_count(db, trip_id) > 1,
    )
    interests = _load_interests(db, trip_id)
    meal_budget_cap = _load_meal_budget_cap(db, trip_id)
    dates = _trip_dates(trip)
    availability_by_date = _load_availability_by_date(db, trip_id, dates)
    day_slots = _day_slots_for_generation(dates, availability_by_date)
    preferred_counts = tuple(
        _preferred_activity_count(day_index, availability_by_date.get(day_date))
        for day_index, day_date in day_slots
    )
    blocked_reason = _blocked_reason(dates, constraints, day_slots)
    blocked_code = _blocked_code(blocked_reason)
    destination_not_found = False
    try:
        poi_pool = tuple(
            _as_poi(place)
            for place in place_service.places_for_planner(db, trip.destination)
        )
    except place_service.DestinationNotFound:
        poi_pool = ()
        destination_not_found = True
    if destination_not_found:
        blocked_reason = "We couldn't find this destination."
        blocked_code = DESTINATION_NOT_FOUND_CODE
    elif not poi_pool:
        blocked_reason = NO_PLACES_BLOCKED_REASON
        blocked_code = NO_PLACE_CANDIDATES_CODE
    allow_reuse_across_days = not any(
        constraint.kind is ConstraintKind.BUDGET_CEILING for constraint in constraints
    )

    draft: list[DraftItem] = []
    used: set[str] = set()
    accepted_days: list[DayDraft] = []

    for slot_index, (day_index, day_date) in enumerate(day_slots, start=1):
        # Count remaining slots in the whole trip so fallback can reserve budget for later days.
        # Otherwise early days pick expensive options and the later days can make the plan blocked.
        slots_left = sum(preferred_counts[slot_index - 1:])
        day = _build_day(
            slots_left=slots_left,
            day_index=day_index,
            day_date=day_date,
            constraints=constraints,
            organizer_id=organizer.id,
            already_selected=tuple(draft),
            already_used=used,
            interests=interests,
            preferred_activity_count=preferred_counts[slot_index - 1],
            allow_reuse_across_days=allow_reuse_across_days,
            poi_pool=poi_pool,
            meal_budget_cap=meal_budget_cap,
        )
        if day is None:
            plan.status = "blocked"
            plan.blocked_reason = blocked_reason
            plan.estimated_total_per_person = None
            db.flush()
            return GenerationResult(
                plan=plan,
                status="blocked",
                days=_days_out(dates, ()),
                blocked_reason=blocked_reason,
                blocked_code=blocked_code,
                generated_by="rules",
                used_ai=False,
                planner_note=None,
            )
        draft.extend(day.items)
        accepted_days.append(day)
        used.update(
            item.poi.title for item in day.items if not _is_flexible_meal_break(item.poi)
        )

    if not _is_complete(day_slots, tuple(draft)):
        plan.status = "blocked"
        plan.blocked_reason = blocked_reason
        plan.estimated_total_per_person = None
        db.flush()
        return GenerationResult(
            plan=plan,
            status="blocked",
            days=_days_out(dates, ()),
            blocked_reason=blocked_reason,
            blocked_code=blocked_code,
            generated_by="rules",
            used_ai=False,
            planner_note=None,
        )

    items = _write_items(db, plan, tuple(draft))
    plan.status = "active"
    plan.blocked_reason = None
    plan.needs_refresh = False
    plan.estimated_total_per_person = _known_item_total(items)
    db.flush()
    return GenerationResult(
        plan=plan,
        status="active",
        days=_days_out(dates, items),
        blocked_reason=None,
        blocked_code=None,
        generated_by=_aggregate_generated_by(tuple(accepted_days)),
        used_ai=any(day.used_ai for day in accepted_days),
        planner_note=_aggregate_planner_note(tuple(accepted_days)),
        items=items,
    )


def _organizer_has_submitted_preferences(db: Session, organizer: TripMembership) -> bool:
    pref = db.scalar(
        select(Preference).where(Preference.trip_membership_id == organizer.id)
    )
    return (
        organizer.status == "preferences_submitted"
        and pref is not None
        and pref.submitted_at is not None
    )


def _trip_has_items(db: Session, trip_id: str) -> bool:
    return db.scalar(
        select(PlanItem.id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(Plan.trip_id == trip_id)
        .limit(1)
    ) is not None


def _plan_for_trip(db: Session, trip: Trip) -> Plan:
    plan = db.scalar(select(Plan).where(Plan.trip_id == trip.id).order_by(Plan.created_at))
    if plan is not None:
        plan.currency = trip.currency
        return plan
    plan = Plan(trip_id=trip.id, currency=trip.currency)
    db.add(plan)
    db.flush()
    return plan


def _trip_dates(trip: Trip) -> tuple[date, ...]:
    if trip.preferred_start_date is None or trip.preferred_end_date is None:
        return ()
    if trip.preferred_end_date < trip.preferred_start_date:
        return ()
    count = (trip.preferred_end_date - trip.preferred_start_date).days + 1
    return tuple(trip.preferred_start_date + timedelta(days=i) for i in range(count))


def _blocked_reason(
    dates: tuple[date, ...],
    constraints: tuple[Constraint, ...],
    day_slots: tuple[tuple[int, date], ...],
) -> str:
    if not dates:
        return DATES_BLOCKED_REASON
    if dates and not day_slots:
        return AVAILABILITY_BLOCKED_REASON
    if any(constraint.kind is ConstraintKind.BUDGET_CEILING for constraint in constraints):
        return BUDGET_BLOCKED_REASON
    return CONSTRAINTS_BLOCKED_REASON


def _blocked_code(reason: str) -> str:
    if reason == DATES_BLOCKED_REASON:
        return "DATES_INVALID"
    if reason == AVAILABILITY_BLOCKED_REASON:
        return "AVAILABILITY_BLOCKED"
    return CONSTRAINTS_BLOCKED_CODE


def _trip_membership_count(db: Session, trip_id: str) -> int:
    return db.scalar(
        select(func.count())
        .select_from(TripMembership)
        .where(TripMembership.trip_id == trip_id)
    ) or 0


def _load_required_constraints(
    db: Session, trip_id: str, *, enforce_budget_ceilings: bool = True
) -> tuple[Constraint, ...]:
    rows = db.scalars(
        select(MemberConstraint)
        .join(TripMembership, TripMembership.id == MemberConstraint.trip_membership_id)
        .where(
            TripMembership.trip_id == trip_id,
            MemberConstraint.importance == Importance.REQUIRED.value,
        )
        .order_by(MemberConstraint.created_at)
    ).all()
    return tuple(
        Constraint(
            id=row.id,
            membership_id=row.trip_membership_id,
            kind=ConstraintKind(row.kind),
            importance=Importance(row.importance),
            params=_normalize_params(row.params or {}),
        )
        for row in rows
        if enforce_budget_ceilings or row.kind != ConstraintKind.BUDGET_CEILING.value
    )


def _load_interests(db: Session, trip_id: str) -> tuple[str, ...]:
    prefs = db.scalars(
        select(Preference)
        .join(TripMembership, TripMembership.id == Preference.trip_membership_id)
        .where(TripMembership.trip_id == trip_id)
        .order_by(Preference.submitted_at)
    ).all()
    interests: list[str] = []
    seen: set[str] = set()
    for pref in prefs:
        for interest in pref.top_interests or ():
            if interest not in seen:
                interests.append(interest)
                seen.add(interest)
    return tuple(interests)


def _load_meal_budget_cap(db: Session, trip_id: str) -> float | None:
    """Use the tightest submitted maximum budget when candidate prices are known."""
    caps = db.scalars(
        select(Preference.maximum_budget)
        .join(TripMembership, TripMembership.id == Preference.trip_membership_id)
        .where(
            TripMembership.trip_id == trip_id,
            Preference.submitted_at.is_not(None),
            Preference.maximum_budget.is_not(None),
        )
    ).all()
    numeric = [float(cap) for cap in caps if cap is not None]
    return min(numeric) if numeric else None


def _load_availability_by_date(
    db: Session, trip_id: str, dates: tuple[date, ...]
) -> dict[date, tuple[int, int]]:
    """Return submitted-member availability by trip date.

    Missing availability means the member selected the full trip.
    Submitted limited windows later become hard generation-day filtering.
    """
    preferences = db.scalars(
        select(Preference)
        .join(TripMembership, TripMembership.id == Preference.trip_membership_id)
        .where(
            TripMembership.trip_id == trip_id,
            Preference.submitted_at.is_not(None),
        )
    ).all()
    total = len(preferences)
    if total == 0:
        return {}
    return {
        day_date: (
            sum(
                1
                for preference in preferences
                if (
                    preference.available_start_date is None
                    or preference.available_start_date <= day_date
                )
                and (
                    preference.available_end_date is None
                    or day_date <= preference.available_end_date
                )
            ),
            total,
        )
        for day_date in dates
    }


def _day_slots_for_generation(
    dates: tuple[date, ...],
    availability_by_date: dict[date, tuple[int, int]],
) -> tuple[tuple[int, date], ...]:
    slots: list[tuple[int, date]] = []
    for day_index, day_date in enumerate(dates, start=1):
        availability = availability_by_date.get(day_date)
        if availability is not None:
            available, submitted = availability
            if submitted and available < submitted:
                continue
        slots.append((day_index, day_date))
    return tuple(slots)


def _preferred_activity_count(
    day_index: int, availability: tuple[int, int] | None
) -> int:
    preferred = DAY_ACTIVITY_PATTERN[(day_index - 1) % len(DAY_ACTIVITY_PATTERN)]
    if availability is not None:
        available, submitted = availability
        if submitted and available < submitted:
            return MIN_DAY_ACTIVITIES
    return preferred


def _normalize_params(params: dict) -> dict:
    normalized = dict(params)
    for key in ("start", "end"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = date.fromisoformat(value)
    return normalized


def _build_day(
    *,
    slots_left: int,
    day_index: int,
    day_date: date,
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    already_selected: tuple[DraftItem, ...],
    already_used: set[str],
    interests: tuple[str, ...],
    preferred_activity_count: int,
    allow_reuse_across_days: bool,
    poi_pool: tuple[Poi, ...],
    meal_budget_cap: float | None,
    planner_call: Callable[[planner.PlanDayInput], planner.PlanDayResult] | None = None,
) -> DayDraft | None:
    for attempt in range(2):
        effective_already_used = (
            set()
            if allow_reuse_across_days and attempt > 0
            else set(already_used)
        )
        candidates = _day_candidates(
            day_index=day_index,
            day_date=day_date,
            constraints=constraints,
            organizer_id=organizer_id,
            already_selected=already_selected,
            already_used=effective_already_used,
            attempt=attempt,
            poi_pool=poi_pool,
            interests=interests,
        )
        if not candidates:
            continue

        day = _planner_day(
            day_index=day_index,
            day_date=day_date,
            candidates=candidates,
            constraints=constraints,
            organizer_id=organizer_id,
            already_selected=already_selected,
            already_used=effective_already_used,
            interests=interests,
            preferred_activity_count=preferred_activity_count,
            planner_call=planner_call,
        )
        if day is None:
            day = _rules_day(
                slots_left=slots_left,
                day_index=day_index,
                day_date=day_date,
                candidates=candidates,
                constraints=constraints,
                organizer_id=organizer_id,
                already_selected=already_selected,
                already_used=effective_already_used,
                attempt=attempt,
                preferred_activity_count=preferred_activity_count,
                interests=interests,
            )

        if day is not None:
            day = _add_meal_anchors(
                day,
                candidates=candidates,
                constraints=constraints,
                organizer_id=organizer_id,
                already_selected=already_selected,
                already_used=effective_already_used,
                meal_budget_cap=meal_budget_cap,
            )

        if day is not None and _day_complete(day) and _validate_items(
            already_selected + day.items,
            constraints,
            organizer_id,
            allow_reuse_across_days=allow_reuse_across_days,
        ):
            return day

    return None


def _day_candidates(
    *,
    day_index: int,
    day_date: date,
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    already_selected: tuple[DraftItem, ...],
    already_used: set[str],
    attempt: int,
    poi_pool: tuple[Poi, ...],
    interests: tuple[str, ...],
) -> tuple[Poi, ...]:
    current_total = _draft_total(already_selected)
    candidates: list[Poi] = []
    for poi in _candidate_order(poi_pool, attempt):
        if poi.title in already_used:
            continue
        if _legal_slots(
            poi,
            day_index=day_index,
            day_date=day_date,
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_before=current_total,
            day_walk_before=0.0,
        ):
            candidates.append(poi)
    return _clustered_candidate_pool(
        tuple(candidates), day_index=day_index, attempt=attempt, interests=interests
    )


def _planner_day(
    *,
    day_index: int,
    day_date: date,
    candidates: tuple[Poi, ...],
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    already_selected: tuple[DraftItem, ...],
    already_used: set[str],
    interests: tuple[str, ...],
    preferred_activity_count: int,
    planner_call: Callable[[planner.PlanDayInput], planner.PlanDayResult] | None = None,
) -> DayDraft | None:
    sightseeing_candidates = tuple(poi for poi in candidates if not poi.is_meal)
    candidate_by_id = {poi.candidate_id: poi for poi in sightseeing_candidates}
    if len(sightseeing_candidates) < MIN_DAY_ACTIVITIES:
        return None
    payload = planner.PlanDayInput(
        day_index=day_index,
        candidates=tuple(
            planner.PoiOption(
                candidate_id=poi.candidate_id,
                name=poi.title,
                local_name=poi.local_title,
                place=poi.place,
                category=poi.category,
                latitude=poi.lat,
                longitude=poi.lng,
                opening_hours=poi.opening_hours,
                price=poi.price,
                duration_min=poi.duration_min,
                opens=poi.opens,
                closes=poi.closes,
                tags=poi.tags,
            )
            for poi in sightseeing_candidates
        ),
        already_used=tuple(sorted(already_used)),
        budget_left=_budget_left(constraints, _draft_total(already_selected)),
        interests=interests,
        hard_constraints=_planner_constraint_summaries(constraints, day_date),
        preferred_activity_count=preferred_activity_count,
    )

    try:
        # Legacy callers intentionally resolve ``planner.plan_day`` at call time so
        # their existing monkeypatch/model behavior remains unchanged.  The new
        # detached lifecycle seam supplies this capability explicitly instead.
        day_result = (planner.plan_day if planner_call is None else planner_call)(payload)
    except (base.AgentUnavailable, planner.PlannerDayUnusable):
        return None

    items: list[DraftItem] = []
    day_used: set[str] = set()
    current_total = _draft_total(already_selected)
    day_walk = 0.0
    for pick in day_result.picks:
        poi = candidate_by_id.get(pick.candidate_id)
        if poi is None or poi.title in day_used:
            continue
        window = planner.time_window(pick.start_hour)
        if window is None or not planner.category_allows_window(poi.tags, window):
            continue
        item = DraftItem(
            day_index=day_index,
            day_date=day_date,
            start_hour=pick.start_hour,
            poi=poi,
            generated_by="planner",
        )
        if _draft_interval_conflicts(item, tuple(items)):
            continue
        if not _candidate_valid_after(
            item,
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_after=_add_known(current_total, poi.price),
            day_walk_after=_add_known(day_walk, _walk_km(poi)),
        ):
            continue
        items.append(item)
        day_used.add(poi.title)
        current_total = _add_known(current_total, poi.price)
        day_walk = _add_known(day_walk, _walk_km(poi))

    if not items:
        return None
    if any(
        _meal_start_hour(window, day_index, tuple(items)) is None
        for window in ("lunch", "dinner")
    ):
        return None
    return DayDraft(
        day_index=day_index,
        items=tuple(items),
        generated_by="planner",
        used_ai=day_result.used_ai,
        planner_note=day_result.planner_note,
    )


def _rules_day(
    *,
    slots_left: int,
    day_index: int,
    day_date: date,
    candidates: tuple[Poi, ...],
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    already_selected: tuple[DraftItem, ...],
    already_used: set[str],
    attempt: int,
    preferred_activity_count: int,
    interests: tuple[str, ...],
) -> DayDraft | None:
    items: list[DraftItem] = []
    day_used: set[str] = set()
    current_total = _draft_total(already_selected)
    day_walk = 0.0

    sightseeing_candidates = tuple(poi for poi in candidates if not poi.is_meal)
    patterns = SIGHTSEEING_TIME_PATTERNS.get(
        preferred_activity_count, SIGHTSEEING_TIME_PATTERNS[3]
    )
    preferred_times = patterns[(day_index + attempt) % len(patterns)]
    has_evening_candidate = any(
        planner.category_allows_window(poi.tags, "evening")
        for poi in sightseeing_candidates
    )
    if has_evening_candidate and day_index % 3 == 0 and preferred_times:
        preferred_times = preferred_times[:-1] + (20.25,)
    fallback_times = tuple(
        time
        for time in (9.0, 10.0, 14.0, 15.0, 16.0, 16.5)
        if time not in preferred_times
    )
    for candidate_time in preferred_times + fallback_times:
        if len(items) >= preferred_activity_count:
            break
        poi = _pick_rule_candidate(
            sightseeing_candidates,
            slots_left=slots_left - len(items),
            start_hour=candidate_time,
            day_index=day_index,
            day_date=day_date,
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_before=current_total,
            day_walk_before=day_walk,
            already_used=already_used | day_used,
            attempt=attempt,
            reference_poi=items[-1].poi if items else None,
            interests=interests,
            scheduled_items=tuple(items),
        )
        if poi is None:
            continue
        items.append(
            DraftItem(
                day_index=day_index,
                day_date=day_date,
                start_hour=candidate_time,
                poi=poi,
                generated_by="rules",
            )
        )
        day_used.add(poi.title)
        current_total = _add_known(current_total, poi.price)
        day_walk = _add_known(day_walk, _walk_km(poi))

    if len(items) < MIN_DAY_ACTIVITIES:
        return None
    return DayDraft(
        day_index=day_index,
        items=tuple(items),
        generated_by="rules",
        used_ai=False,
        planner_note=RULES_DAY_NOTE,
    )


def _add_meal_anchors(
    day: DayDraft,
    *,
    candidates: tuple[Poi, ...],
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    already_selected: tuple[DraftItem, ...],
    already_used: set[str],
    meal_budget_cap: float | None,
) -> DayDraft:
    """Add honest lunch/dinner opportunities without turning them into hard gates."""
    items = list(day.items)
    used = already_used | {item.poi.title for item in items}
    meal_candidates = tuple(
        poi
        for poi in candidates
        if poi.is_meal
        and poi.title
        and poi.place
        and poi.lat is not None
        and poi.lng is not None
    )

    for window in ("lunch", "dinner"):
        start_hour = _meal_start_hour(window, day.day_index, tuple(items))
        if start_hour is None:
            continue
        current_draft = already_selected + tuple(items)
        current_total = _draft_total(current_draft)
        day_walk = _day_walk(tuple(items))
        poi = _pick_meal_candidate(
            meal_candidates,
            window=window,
            start_hour=start_hour,
            day_index=day.day_index,
            day_date=items[0].day_date,
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_before=current_total,
            day_walk_before=day_walk,
            already_used=used,
            day_items=tuple(items),
            meal_budget_cap=meal_budget_cap,
        )
        if poi is None:
            poi = _flexible_meal_break(window)
            break_item = DraftItem(
                day_index=day.day_index,
                day_date=items[0].day_date,
                start_hour=start_hour,
                poi=poi,
                generated_by="rules",
            )
            if not _candidate_valid_after(
                break_item,
                constraints=constraints,
                organizer_id=organizer_id,
                trip_total_after=current_total,
                day_walk_after=day_walk,
            ):
                continue
        item = DraftItem(
            day_index=day.day_index,
            day_date=items[0].day_date,
            start_hour=start_hour,
            poi=poi,
            generated_by="rules",
        )
        items.append(item)
        used.add(poi.title)

    return DayDraft(
        day_index=day.day_index,
        items=tuple(sorted(items, key=lambda item: item.start_hour)),
        generated_by=day.generated_by,
        used_ai=day.used_ai,
        planner_note=day.planner_note,
    )


def _meal_start_hour(
    window: str, day_index: int, items: tuple[DraftItem, ...]
) -> float | None:
    options = RULE_TIME_OPTIONS[window]
    offset = day_index % len(options)
    varied = options[offset:] + options[:offset]
    for option in varied:
        meal_end = option + 1.0
        if all(
            meal_end <= item.start_hour
            or option >= item.start_hour + ((item.poi.duration_min or 90) / 60)
            for item in items
        ):
            return option
    return None


def _flexible_meal_break(window: str) -> Poi:
    return Poi(
        candidate_id=f"schedule:flexible-{window}",
        title=f"Flexible {window} break",
        category=None,
        place="Choose a convenient option nearby",
        lat=None,
        lng=None,
        price=None,
        duration_min=None,
        opens=None,
        closes=None,
        walk=None,
        access=(),
        diet=(),
        tags=("food", "meal_break"),
        source="schedule",
    )


def _is_flexible_meal_break(poi: Poi) -> bool:
    return "meal_break" in poi.tags


def _day_walk(items: tuple[DraftItem, ...]) -> float | None:
    total: float | None = 0.0
    for item in items:
        total = _add_known(total, _walk_km(item.poi))
    return total


def _budget_headroom(
    constraints: tuple[Constraint, ...], spent: float | None
) -> float | None:
    """Remaining spend, based on the tightest budget ceiling rather than the average.

    Return None when no one has set a budget ceiling."""
    if spent is None:
        return None
    ceilings = [
        c.params.get("max_total_per_person")
        for c in constraints
        if c.kind is ConstraintKind.BUDGET_CEILING
        and c.importance is Importance.REQUIRED
        and c.params.get("max_total_per_person") is not None
    ]
    if not ceilings:
        return None
    return max(0.0, min(ceilings) - spent)


def _pick_rule_candidate(
    candidates: tuple[Poi, ...],
    *,
    slots_left: int,
    start_hour: float,
    day_index: int,
    day_date: date,
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    trip_total_before: float | None,
    day_walk_before: float | None,
    already_used: set[str],
    attempt: int,
    reference_poi: Poi | None = None,
    interests: tuple[str, ...] = (),
    scheduled_items: tuple[DraftItem, ...] = (),
    preserve_order: bool = False,
) -> Poi | None:
    # Reserve budget for later slots. Without this, greedy selection front-loads cost.
    # If the first two days pick expensive places, the third day can run out of budget and block the plan.
    # Within the reserved budget, choose the most expensive viable option so the plan uses the budget instead of overusing free stops.
    headroom = _budget_headroom(constraints, trip_total_before)
    if headroom is not None and slots_left > 0:
        per_slot = headroom / slots_left
        affordable = [
            poi for poi in candidates
            if poi.price is not None and poi.price <= per_slot
        ]
        pool = sorted(affordable, key=lambda poi: -poi.price) if affordable \
            else sorted(candidates, key=lambda poi: poi.price if poi.price is not None else math.inf)
    else:
        pool = list(candidates)
    if preserve_order:
        pass
    elif reference_poi is not None:
        pool.sort(
            key=lambda poi: (
                _poi_distance_km(reference_poi, poi),
                -_poi_relevance_score(poi, interests),
                -_poi_quality_score(poi),
                poi.title.casefold(),
            )
        )
    else:
        pool.sort(
            key=lambda poi: (
                -_poi_relevance_score(poi, interests),
                -_poi_quality_score(poi),
                poi.title.casefold(),
            )
        )
    ordered = tuple(pool[attempt:] + pool[:attempt])
    for poi in ordered:
        if poi.title in already_used:
            continue
        window = planner.time_window(start_hour)
        if window is None or not planner.category_allows_window(poi.tags, window):
            continue
        if (
            not poi.is_meal
            and window == "afternoon"
            and start_hour >= 16.0
            and poi.duration_min is not None
            and start_hour + poi.duration_min / 60 > RULE_TIME_OPTIONS["dinner"][-1]
        ):
            continue
        proposed = DraftItem(
            day_index=day_index,
            day_date=day_date,
            start_hour=start_hour,
            poi=poi,
            generated_by="rules",
        )
        if _draft_interval_conflicts(proposed, scheduled_items):
            continue
        if not _candidate_valid_after(
            proposed,
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_after=_add_known(trip_total_before, poi.price),
            day_walk_after=_add_known(day_walk_before, _walk_km(poi)),
        ):
            continue
        return poi
    return None


def _draft_interval_conflicts(
    proposed: DraftItem, existing: tuple[DraftItem, ...]
) -> bool:
    proposed_end = proposed.start_hour + ((proposed.poi.duration_min or 90) / 60)
    for item in existing:
        item_end = item.start_hour + ((item.poi.duration_min or 90) / 60)
        if proposed.start_hour < item_end and item.start_hour < proposed_end:
            return True
    return False


def _candidate_order(pois: tuple[Poi, ...], attempt: int) -> tuple[Poi, ...]:
    if not pois:
        return ()
    offset = attempt % len(pois)
    return pois[offset:] + pois[:offset]


def _clustered_candidate_pool(
    candidates: tuple[Poi, ...],
    *,
    day_index: int,
    attempt: int,
    interests: tuple[str, ...],
) -> tuple[Poi, ...]:
    """Give each day a diverse anchor, then expose nearby candidates to Planner.

    This uses coordinate distance only. It does not claim a route or walking
    duration that the place provider did not supply.
    """
    sightseeing = [poi for poi in candidates if not poi.is_meal]
    meals = [poi for poi in candidates if poi.is_meal]
    if not sightseeing:
        return tuple(meals[:DAY_MEAL_CANDIDATE_LIMIT])
    anchors = _spatially_spread_pois(
        sorted(
            sightseeing,
            key=lambda poi: (
                -_poi_relevance_score(poi, interests),
                -_poi_quality_score(poi),
                poi.title.casefold(),
            ),
        )
    )[: min(14, len(sightseeing))]
    anchor = anchors[(day_index - 1 + attempt * 3) % len(anchors)]
    nearby_sightseeing = sorted(
        sightseeing,
        key=lambda poi: (
            0 if _can_host_late_activity(poi) else 1,
            _poi_distance_km(anchor, poi),
            -_poi_relevance_score(poi, interests),
            -_poi_quality_score(poi),
            poi.title.casefold(),
        ),
    )[:DAY_SIGHTSEEING_CANDIDATE_LIMIT]
    nearby_meals = sorted(
        meals,
        key=lambda poi: (
            _poi_distance_km(anchor, poi),
            -_poi_quality_score(poi),
            poi.title.casefold(),
        ),
    )[:DAY_MEAL_CANDIDATE_LIMIT]
    return tuple(nearby_sightseeing + nearby_meals)


def _can_host_late_activity(poi: Poi) -> bool:
    if poi.opens is not None and poi.opens > 16.0:
        return False
    end_hour = 16.0 + ((poi.duration_min or 90) / 60)
    if end_hour > RULE_TIME_OPTIONS["dinner"][-1]:
        return False
    return poi.closes is None or end_hour <= poi.closes


def _spatially_spread_pois(pois: list[Poi]) -> list[Poi]:
    if not pois:
        return []
    ordered = [pois[0]]
    remaining = pois[1:]
    while remaining:
        next_poi = max(
            remaining,
            key=lambda poi: (
                min(_poi_distance_km(poi, selected) for selected in ordered),
                _poi_quality_score(poi),
            ),
        )
        ordered.append(next_poi)
        remaining.remove(next_poi)
    return ordered


def _poi_distance_km(left: Poi, right: Poi) -> float:
    if left.lat is None or left.lng is None or right.lat is None or right.lng is None:
        return math.inf
    radius_km = 6371.0
    lat1, lat2 = math.radians(left.lat), math.radians(right.lat)
    dlat = math.radians(right.lat - left.lat)
    dlng = math.radians(right.lng - left.lng)
    haversine = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(haversine))


def _poi_quality_score(poi: Poi) -> float:
    text = " ".join((poi.title, poi.category or "", *poi.tags)).casefold()
    score = 2.0 if poi.title and len(poi.title.strip()) >= 4 else 0.0
    if poi.category and poi.category.casefold() not in {"other", "building"}:
        score += 2.0
    if any(marker in text for marker in (
        "tourism", "attraction", "museum", "heritage", "park", "garden",
        "aquarium", "planetarium", "zoo", "gallery", "restaurant",
    )):
        score += 2.0
    vague = {"building", "cafe", "coffee", "office", "place", "restaurant", "shop", "unnamed"}
    title_words = set(poi.title.casefold().replace("&", " ").split())
    if title_words and title_words.issubset(vague):
        score -= 5.0
    return score


def _poi_relevance_score(poi: Poi, interests: tuple[str, ...]) -> float:
    if not interests:
        return 0.0
    haystack = " ".join((poi.title, poi.category or "", *poi.tags)).casefold()
    aliases = {
        "art": ("art", "gallery", "museum", "culture"),
        "culture": ("culture", "museum", "heritage", "historic", "architecture"),
        "food": ("food", "restaurant", "catering"),
        "history": ("history", "historic", "heritage", "monument", "museum"),
        "museums": ("museum", "gallery", "culture"),
        "nature": ("nature", "park", "garden", "protected", "zoo"),
        "relaxed": ("park", "garden", "scenic", "nature"),
    }
    score = 0.0
    for interest in interests:
        key = interest.casefold().strip()
        markers = aliases.get(key, (key,))
        if any(marker and marker in haystack for marker in markers):
            score += 2.0
    return score


def _route_reference_for_hour(
    items: tuple[DraftItem, ...], start_hour: float
) -> Poi | None:
    located = [item for item in items if item.poi.lat is not None and item.poi.lng is not None]
    if not located:
        return None
    return min(located, key=lambda item: abs(item.start_hour - start_hour)).poi


def _meal_route_references(
    window: str, day_items: tuple[DraftItem, ...]
) -> tuple[Poi, ...]:
    sightseeing = sorted(
        (item for item in day_items if not item.poi.is_meal),
        key=lambda item: item.start_hour,
    )
    if not sightseeing:
        return ()
    if window == "lunch":
        before = [item for item in sightseeing if item.start_hour < 13.5]
        after = [item for item in sightseeing if item.start_hour >= 13.5]
    else:
        before = [item for item in sightseeing if item.start_hour < 17.5]
        after = [item for item in sightseeing if item.start_hour >= 20.25]
    references = []
    if before:
        references.append(before[-1].poi)
    if after:
        references.append(after[0].poi)
    if not references:
        references.append(sightseeing[-1].poi)
    return tuple(references)


def _meal_route_cost_km(poi: Poi, references: tuple[Poi, ...]) -> float:
    distances = [_poi_distance_km(poi, reference) for reference in references]
    finite = [distance for distance in distances if math.isfinite(distance)]
    return sum(finite) if finite else math.inf


def _meal_category_tier(poi: Poi, window: str) -> int | None:
    text = " ".join((poi.title, poi.category or "", *poi.tags)).casefold()
    cafe_only = any(marker in text for marker in ("cafe", "coffee", "tea")) \
        and not any(marker in text for marker in ("restaurant", "diner", "bistro", "dining"))
    restaurant = any(marker in text for marker in (
        "restaurant", "diner", "bistro", "dining", "cuisine", "brasserie",
    ))
    substantial_lunch = restaurant or any(marker in text for marker in (
        "casual", "brunch", "bakery", "food_court", "fast_food", "food hall",
    ))
    curated_food_venue = poi.source != "geoapify" and "food" in {tag.casefold() for tag in poi.tags}
    if window == "lunch":
        if substantial_lunch or curated_food_venue:
            return 0
        return 1 if cafe_only else None
    if restaurant or (curated_food_venue and not cafe_only):
        return 0
    if any(marker in text for marker in ("food_court", "fast_food", "food hall")):
        return 1
    return None


def _meal_candidate_sort_key(
    poi: Poi,
    *,
    references: tuple[Poi, ...],
    day_date: date,
    start_hour: float,
) -> tuple:
    opening_status = _opening_status(poi, day_date, start_hour)
    return (
        0 if opening_status is True else 1,
        _meal_route_cost_km(poi, references),
        -_poi_quality_score(poi),
        poi.price is None,
        poi.price if poi.price is not None else math.inf,
        poi.title.casefold(),
    )


def _pick_meal_candidate(
    candidates: tuple[Poi, ...],
    *,
    window: str,
    start_hour: float,
    day_index: int,
    day_date: date,
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    trip_total_before: float | None,
    day_walk_before: float | None,
    already_used: set[str],
    day_items: tuple[DraftItem, ...],
    meal_budget_cap: float | None,
) -> Poi | None:
    references = _meal_route_references(window, day_items)
    for category_tier in (0, 1):
        for radius in MEAL_SEARCH_RADII_KM:
            pool = [
                poi
                for poi in candidates
                if _meal_category_tier(poi, window) == category_tier
                and _meal_route_cost_km(poi, references) <= radius * max(1, len(references))
                and _opening_status(poi, day_date, start_hour) is not False
                and not (
                    meal_budget_cap is not None
                    and trip_total_before is not None
                    and poi.price is not None
                    and trip_total_before + poi.price > meal_budget_cap
                )
            ]
            pool.sort(
                key=lambda poi: _meal_candidate_sort_key(
                    poi,
                    references=references,
                    day_date=day_date,
                    start_hour=start_hour,
                )
            )
            picked = _pick_rule_candidate(
                tuple(pool),
                slots_left=1,
                start_hour=start_hour,
                day_index=day_index,
                day_date=day_date,
                constraints=constraints,
                organizer_id=organizer_id,
                trip_total_before=trip_total_before,
                day_walk_before=day_walk_before,
                already_used=already_used,
                attempt=0,
                scheduled_items=day_items,
                preserve_order=True,
            )
            if picked is not None:
                return picked
    return None


def _meal_candidates_for_day(
    candidates: tuple[Poi, ...],
    *,
    window: str,
    day_items: tuple[DraftItem, ...],
) -> tuple[Poi, ...]:
    references = _meal_route_references(window, day_items)
    suitable = [
        poi for poi in candidates
        if _meal_category_tier(poi, window) is not None
        and _meal_route_cost_km(poi, references)
        <= MAX_MEAL_DETOUR_KM * max(1, len(references))
    ]
    return tuple(sorted(
        suitable,
        key=lambda poi: (
            _meal_category_tier(poi, window),
            _meal_route_cost_km(poi, references),
            -_poi_quality_score(poi),
            poi.title.casefold(),
        ),
    ))


def _as_poi(raw: place_service.PlannerPlace) -> Poi:
    return Poi(
        candidate_id=raw.candidate_id or _synthetic_candidate_id(raw),
        title=raw.name,
        category=raw.category,
        local_title=raw.local_name,
        place=raw.address or raw.location,
        lat=raw.latitude,
        lng=raw.longitude,
        price=raw.price,
        duration_min=raw.duration_min,
        opens=raw.opens,
        closes=raw.closes,
        walk=raw.walking_level,
        access=raw.access,
        diet=raw.diet,
        tags=raw.tags,
        photo_url=raw.image_url,
        source=raw.source,
        opening_hours=raw.opening_hours,
    )


def _synthetic_candidate_id(raw: place_service.PlannerPlace) -> str:
    """Keep test/custom candidates opaque when a provider id is unavailable."""
    digest = hashlib.sha256(
        f"{raw.source}|{raw.name}|{raw.latitude}|{raw.longitude}".encode("utf-8")
    ).hexdigest()[:16]
    return f"synthetic:{digest}"


def _planner_constraint_summaries(
    constraints: tuple[Constraint, ...], day_date: date
) -> tuple[str, ...]:
    """Expose safe structured facts, never member identity or private wording."""
    summaries: list[str] = []
    for constraint in constraints:
        params = constraint.params
        if constraint.kind is ConstraintKind.TIME_WINDOW:
            earliest = params.get("earliest_hour")
            latest = params.get("latest_hour")
            parts = []
            if earliest is not None:
                parts.append(f"start no earlier than {float(earliest):g}")
            if latest is not None:
                parts.append(f"finish no later than {float(latest):g}")
            if parts:
                summaries.append("Required time window: " + ", ".join(parts))
        elif constraint.kind is ConstraintKind.DATE_RANGE:
            start, end = params.get("start"), params.get("end")
            if (start is not None and day_date < start) or (
                end is not None and day_date > end
            ):
                summaries.append("This day is outside a required availability range")
        elif (
            constraint.kind is ConstraintKind.WALK_LIMIT
            and params.get("max_km_per_day") is not None
        ):
            summaries.append(
                f"Required walking limit: {float(params['max_km_per_day']):g} km for the day"
            )
        elif constraint.kind is ConstraintKind.DIETARY:
            tags = tuple(params.get("required_tags", ()))
            if tags:
                summaries.append("Required meal tags: " + ", ".join(map(str, tags)))
        elif constraint.kind is ConstraintKind.AVOID_TAG:
            tags = tuple(params.get("tags", ()))
            if tags:
                summaries.append("Required avoid tags: " + ", ".join(map(str, tags)))
    return tuple(summaries)


def _legal_slots(
    poi: Poi,
    *,
    day_index: int,
    day_date: date,
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    trip_total_before: float | None,
    day_walk_before: float | None,
) -> tuple[float, ...]:
    return tuple(
        slot
        for slot in VALIDATION_START_TIMES
        if _candidate_valid_after(
            DraftItem(
                day_index=day_index,
                day_date=day_date,
                start_hour=slot,
                poi=poi,
                generated_by="rules",
            ),
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_after=_add_known(trip_total_before, poi.price),
            day_walk_after=_add_known(day_walk_before, _walk_km(poi)),
        )
    )


def _candidate_valid_after(
    item: DraftItem,
    *,
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    trip_total_after: float | None,
    day_walk_after: float | None,
) -> bool:
    if item.poi.title == "":
        return False
    window = planner.time_window(item.start_hour)
    if window is None or not planner.category_allows_window(item.poi.tags, window):
        return False
    if _opening_status(item.poi, item.day_date, item.start_hour) is False:
        return False
    if item.poi.opens is not None and item.start_hour < item.poi.opens:
        return False
    if item.poi.closes is not None and item.start_hour > item.poi.closes:
        return False
    if (
        item.poi.closes is not None
        and item.poi.duration_min is not None
        and item.start_hour + item.poi.duration_min / 60 > item.poi.closes
    ):
        return False
    change = _change_for(
        day_index=item.day_index,
        day_date=item.day_date,
        slot=item.start_hour,
        poi=item.poi,
        trip_total_after=trip_total_after,
        day_walk_after=day_walk_after,
        organizer_id=organizer_id,
    )
    return not any(
        violates(constraint, change)
        for constraint in constraints
        if not (
            _is_flexible_meal_break(item.poi)
            and constraint.kind is ConstraintKind.DIETARY
        )
    )


_WEEKDAY_CODES = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


def _opening_status(poi: Poi, day_date: date, start_hour: float) -> bool | None:
    """Return True/False only when provider hours are safely parseable."""
    if poi.opens is not None or poi.closes is not None:
        if poi.opens is not None and start_hour < poi.opens:
            return False
        if poi.closes is not None:
            end_hour = start_hour + ((poi.duration_min or 0) / 60)
            return end_hour <= poi.closes
        return True
    intervals = _opening_intervals_for_date(poi.opening_hours, day_date)
    if intervals is None:
        return None
    end_hour = start_hour + ((poi.duration_min or 0) / 60)
    return any(start <= start_hour and end_hour <= end for start, end in intervals)


def _opening_intervals_for_date(
    raw: str | None, day_date: date
) -> tuple[tuple[float, float], ...] | None:
    if not raw:
        return None
    value = raw.strip()
    if value == "24/7":
        return ((0.0, 24.0),)
    weekday = _WEEKDAY_CODES[day_date.weekday()]
    matched_day = False
    intervals: list[tuple[float, float]] = []
    for segment in value.split(";"):
        segment = segment.strip()
        match = re.match(r"^([A-Z][a-z](?:-[A-Z][a-z])?(?:,[A-Z][a-z])*)\s+(.+)$", segment)
        if match is None or not _weekday_spec_contains(match.group(1), weekday):
            continue
        matched_day = True
        hours = match.group(2).strip()
        if hours.casefold() in {"off", "closed"}:
            continue
        for start_text, end_text in re.findall(
            r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", hours
        ):
            start = _clock_hour(start_text)
            end = _clock_hour(end_text)
            if start is None or end is None:
                continue
            if end <= start:
                end += 24.0
            intervals.append((start, end))
    if not matched_day:
        return None
    return tuple(intervals)


def _weekday_spec_contains(spec: str, weekday: str) -> bool:
    for part in spec.split(","):
        if "-" not in part:
            if part == weekday:
                return True
            continue
        start, end = part.split("-", 1)
        if start not in _WEEKDAY_CODES or end not in _WEEKDAY_CODES:
            continue
        start_index = _WEEKDAY_CODES.index(start)
        end_index = _WEEKDAY_CODES.index(end)
        day_index = _WEEKDAY_CODES.index(weekday)
        if start_index <= end_index:
            if start_index <= day_index <= end_index:
                return True
        elif day_index >= start_index or day_index <= end_index:
            return True
    return False


def _clock_hour(value: str) -> float | None:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60) or (hour == 24 and minute):
        return None
    return hour + minute / 60


def _walk_km(poi: Poi) -> float | None:
    return {"low": 0.5, "medium": 1.5, "high": 3.0}.get(poi.walk)


def _change_for(
    *,
    day_index: int,
    day_date: date,
    slot: float,
    poi: Poi,
    trip_total_after: float | None,
    day_walk_after: float | None,
    organizer_id: str,
) -> ProposedChange:
    view = ItemView(
        id=f"draft:{day_index}:{slot}:{poi.title}",
        day_date=day_date,
        start_hour=slot,
        duration_min=poi.duration_min,
        price_per_person=poi.price,
        tags=frozenset(poi.tags),
        dietary_tags=frozenset(poi.diet),
        is_meal=poi.is_meal,
        settledness=Settledness.LOOSE,
    )
    return ProposedChange(
        before=view,
        after=view,
        day_walk_km_after=day_walk_after,
        trip_total_after=trip_total_after,
        requested_by_membership_id=organizer_id,
    )


def _is_complete(
    day_slots: tuple[tuple[int, date], ...], draft: tuple[DraftItem, ...]
) -> bool:
    if not day_slots:
        return False
    counts = {day_index: 0 for day_index, _day_date in day_slots}
    for item in draft:
        if item.day_index not in counts:
            return False
        if not item.poi.is_meal:
            counts[item.day_index] += 1
    return all(MIN_DAY_ACTIVITIES <= count <= MAX_DAY_ACTIVITIES for count in counts.values())


def _day_complete(day: DayDraft) -> bool:
    windows = [planner.time_window(item.start_hour) for item in day.items]
    sightseeing_count = sum(not item.poi.is_meal for item in day.items)
    return (
        MIN_DAY_ACTIVITIES <= sightseeing_count <= MAX_DAY_ACTIVITIES
        and None not in windows
        and len({item.start_hour for item in day.items}) == len(day.items)
    )


def _validate_items(
    draft: tuple[DraftItem, ...],
    constraints: tuple[Constraint, ...],
    organizer_id: str,
    *,
    allow_reuse_across_days: bool = False,
) -> bool:
    total = _draft_total(draft)
    walk_by_date: dict[date, float | None] = {}
    seen_by_day: dict[int, set[float]] = {}
    used_titles_by_day: dict[int, set[str]] = {}
    used_titles: set[str] = set()

    for item in draft:
        day_titles = used_titles_by_day.setdefault(item.day_index, set())
        if item.poi.title in day_titles:
            return False
        day_titles.add(item.poi.title)
        if not allow_reuse_across_days and not _is_flexible_meal_break(item.poi):
            if item.poi.title in used_titles:
                return False
            used_titles.add(item.poi.title)
        slots = seen_by_day.setdefault(item.day_index, set())
        if item.start_hour in slots:
            return False
        slots.add(item.start_hour)
        walk_by_date[item.day_date] = _add_known(
            walk_by_date.get(item.day_date, 0.0), _walk_km(item.poi)
        )

    for item in draft:
        window = planner.time_window(item.start_hour)
        if window is None or not planner.category_allows_window(item.poi.tags, window):
            return False
        if not _candidate_valid_after(
            item,
            constraints=constraints,
            organizer_id=organizer_id,
            trip_total_after=total,
            day_walk_after=walk_by_date[item.day_date],
        ):
            return False
    return True


def _draft_total(draft: tuple[DraftItem, ...]) -> float | None:
    prices = [item.poi.price for item in draft]
    if any(price is None for price in prices):
        return None
    return sum(price for price in prices if price is not None)


def _budget_left(constraints: tuple[Constraint, ...], current_total: float | None) -> float | None:
    if current_total is None:
        return None
    ceilings = [
        constraint.params.get("max_total_per_person")
        for constraint in constraints
        if constraint.kind is ConstraintKind.BUDGET_CEILING
    ]
    numeric = [float(ceiling) for ceiling in ceilings if ceiling is not None]
    if not numeric:
        return math.inf
    return max(0.0, min(numeric) - current_total)


def _aggregate_generated_by(days: tuple[DayDraft, ...]) -> str:
    sources = {day.generated_by for day in days}
    if sources == {"planner"}:
        return "planner"
    if sources == {"rules"}:
        return "rules"
    return "mixed"


def _aggregate_planner_note(
    days: tuple[DayDraft, ...]
) -> tuple[DayPlannerNote, ...] | None:
    if not any(day.generated_by == "planner" for day in days):
        return None
    return tuple(
        DayPlannerNote(
            day_index=day.day_index,
            source=day.generated_by,
            note=day.planner_note or RULES_DAY_NOTE,
            used_ai=day.used_ai,
        )
        for day in days
    )


def _write_items(
    db: Session,
    plan: Plan,
    draft: tuple[DraftItem, ...],
) -> tuple[PlanItem, ...]:
    items: list[PlanItem] = []
    for item in draft:
        poi = item.poi
        row = PlanItem(
            plan_id=plan.id,
            day_index=item.day_index,
            day_date=item.day_date,
            start_hour=item.start_hour,
            duration_min=poi.duration_min,
            title=poi.title,
            local_title=poi.local_title,
            place=poi.place,
            price_per_person=poi.price,
            tags=list(poi.tags),
            dietary_tags=list(poi.diet),
            is_meal=poi.is_meal,
            lat=poi.lat,
            lng=poi.lng,
            photo_url=poi.photo_url,
            source=poi.source,
            settledness="loose",
        )
        db.add(row)
        db.flush()
        origin = "ai_generate" if item.generated_by == "planner" else "rule_generate"
        db.add(
            PlanChange(
                plan_id=plan.id,
                plan_item_id=row.id,
                origin=origin,
                patch=_item_patch(row),
                reason=(
                    "Generated by Planner agent."
                    if item.generated_by == "planner"
                    else "Generated by deterministic rules fallback."
                ),
            )
        )
        items.append(row)
    db.flush()
    return tuple(items)


def _known_item_total(items: tuple[PlanItem, ...]) -> float | None:
    prices = [item.price_per_person for item in items]
    if any(price is None for price in prices):
        return None
    return sum(price for price in prices if price is not None)


def _add_known(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left + right


def _item_patch(item: PlanItem) -> dict:
    return {
        "day_index": item.day_index,
        "day_date": item.day_date.isoformat(),
        "start_hour": item.start_hour,
        "duration_min": item.duration_min,
        "title": item.title,
        "local_title": item.local_title,
        "place": item.place,
        "price_per_person": item.price_per_person,
        "tags": item.tags or [],
        "dietary_tags": item.dietary_tags or [],
        "is_meal": item.is_meal,
        "lat": item.lat,
        "lng": item.lng,
        "photo_url": item.photo_url,
        "source": item.source,
    }


def _days_out(dates: tuple[date, ...], items: Iterable[PlanItem]) -> list[dict]:
    by_day: dict[int, list[dict]] = {}
    for item in items:
        by_day.setdefault(item.day_index, []).append(
            {
                "id": item.id,
                "day_index": item.day_index,
                "day_date": item.day_date.isoformat(),
                "start_hour": item.start_hour,
                "title": item.title,
                "local_title": item.local_title,
                "place": item.place,
                "price_per_person": item.price_per_person,
                "source": item.source,
                "lat": item.lat,
                "lng": item.lng,
                "photo_url": item.photo_url,
                "tags": item.tags or [],
            }
        )

    return [
        {
            "day_index": index,
            "day_date": day.isoformat(),
            "items": sorted(by_day.get(index, []), key=lambda row: row["start_hour"]),
        }
        for index, day in enumerate(dates, start=1)
    ]
