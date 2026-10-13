"""Deterministic manual PlanItem mutation shared by legacy and protected paths."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Plan, PlanItem


class ManualPlanItemError(Exception):
    """Base error for the existing manual-item business rules."""


class ManualPlanItemInvalid(ManualPlanItemError):
    pass


class ManualPlanItemConflict(ManualPlanItemError):
    pass


class ManualPlanMismatch(ManualPlanItemError):
    pass


@dataclass(frozen=True)
class ManualPlanItemCommand:
    title: str
    after_item_id: str
    before_item_id: str
    start_hour: float | None


def add_manual_plan_item(
    db: Session,
    plan: Plan,
    command: ManualPlanItemCommand,
    *,
    lock_children: bool,
) -> PlanItem:
    """Apply Cadensy's existing adjacent-gap insertion rules in ``db``.

    The protected path passes ``lock_children=True`` after acquiring its Plan root
    lock.  The legacy path retains its current request-owned transaction behavior.
    Neither path commits here.
    """

    title = command.title.strip()
    if not title:
        raise ManualPlanItemInvalid("A stop name is required")

    statement = (
        select(PlanItem)
        .where(PlanItem.plan_id == plan.id, PlanItem.settledness != "removed")
        .order_by(PlanItem.day_index, PlanItem.start_hour, PlanItem.id)
    )
    if lock_children:
        statement = statement.execution_options(populate_existing=True).with_for_update()
    items = db.scalars(statement).all()
    after_index = next(
        (index for index, item in enumerate(items) if item.id == command.after_item_id), None
    )
    before_index = next(
        (index for index, item in enumerate(items) if item.id == command.before_item_id), None
    )
    if after_index is None or before_index is None or before_index != after_index + 1:
        raise ManualPlanItemConflict("These stops are no longer adjacent")

    after = items[after_index]
    before = items[before_index]
    if after.day_index != before.day_index or before.start_hour <= after.start_hour:
        raise ManualPlanItemConflict("There is no open time between these stops")

    manual_duration_min = 30
    default_block_minutes = 90
    after_end_hour = after.start_hour + (after.duration_min or default_block_minutes) / 60
    latest_start_hour = before.start_hour - manual_duration_min / 60
    if latest_start_hour < after_end_hour:
        raise ManualPlanItemConflict("This stop may affect the next activity's time.")
    start_hour = (
        command.start_hour
        if command.start_hour is not None
        else (after_end_hour + latest_start_hour) / 2
    )
    if start_hour < after_end_hour or start_hour > latest_start_hour:
        raise ManualPlanItemInvalid("Choose a time that fits between these stops.")

    inserted = PlanItem(
        plan_id=plan.id,
        day_index=after.day_index,
        day_date=after.day_date,
        start_hour=start_hour,
        duration_min=manual_duration_min,
        title=title,
        local_title=None,
        place=title,
        price_per_person=None,
        tags=[],
        dietary_tags=[],
        is_meal=False,
        lat=None,
        lng=None,
        photo_url=None,
        source="manual",
        settledness="loose",
    )
    db.add(inserted)
    db.flush()
    return inserted


def manual_plan_item_out(item: PlanItem) -> dict[str, object]:
    """Detached existing-route representation allowed in the durable ledger."""

    return {
        "id": item.id,
        "day_index": item.day_index,
        "day_date": item.day_date.isoformat(),
        "start_hour": item.start_hour,
        "duration_min": item.duration_min,
        "title": item.title,
        "local_title": item.local_title,
        "place": item.place,
        "price_per_person": item.price_per_person,
        "source": item.source,
        "settledness": item.settledness,
        "tags": item.tags or [],
        "is_meal": item.is_meal,
        "meal_type": None,
        "coords": None,
        "photoUrl": item.photo_url,
    }
