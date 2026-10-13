"""Narrow shared helpers for frozen Trip Preferences/Constraints writers."""

from __future__ import annotations

from datetime import date, datetime
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import MemberConstraint, MemberConstraintPrivate, PlanItem, TripMembership
from .durable import HmacSigningKey
from .trip_kernel import LockedTripContext


class StaticHmacKeyring:
    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


class ProtectedConstraintMissing(Exception):
    """Non-enumerating protected equivalent of the Legacy NotYours result."""


def locked_actor_membership(session: Session, context: LockedTripContext) -> TripMembership:
    membership = session.scalar(
        select(TripMembership)
        .where(TripMembership.id == context.actor.membership_id, TripMembership.trip_id == context.trip.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if membership is None:
        # Defensive: TripTransactionKernel has already freshly authorized it.
        raise ProtectedConstraintMissing("Constraint not found")
    return membership


def locked_owned_constraint(
    session: Session, context: LockedTripContext, constraint_id: str
) -> MemberConstraint:
    row = session.scalar(
        select(MemberConstraint)
        .join(TripMembership, TripMembership.id == MemberConstraint.trip_membership_id)
        .where(
            MemberConstraint.id == constraint_id,
            MemberConstraint.trip_membership_id == context.actor.membership_id,
            TripMembership.trip_id == context.trip.id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if row is None:
        raise ProtectedConstraintMissing("Constraint not found")
    return row


def locked_constraint_private(session: Session, constraint_id: str) -> MemberConstraintPrivate | None:
    return session.scalar(
        select(MemberConstraintPrivate)
        .where(MemberConstraintPrivate.constraint_id == constraint_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )


def plan_has_items(session: Session, context: LockedTripContext) -> bool:
    return context.plan is not None and session.scalar(
        select(PlanItem.id).where(PlanItem.plan_id == context.plan.id).limit(1)
    ) is not None


_CONFLICT_KEYS = frozenset(
    {"constraint_id", "code", "text", "scope", "item_id", "item_title", "day_date", "settledness"}
)
_CONFLICT_CODES = frozenset(
    {
        "TIME_WINDOW",
        "BUDGET_CEILING",
        "DATE_RANGE",
        "WALK_LIMIT",
        "DIETARY",
        "AVOID_TAG",
    }
)


def safe_conflicts(value: object) -> list[dict[str, object]]:
    """Encode only the reviewed public conflict projection for ledger replay."""
    if not isinstance(value, list):
        raise ValueError("Conflict result must be a list")
    result: list[dict[str, object]] = []
    for conflict in value:
        if not isinstance(conflict, dict) or set(conflict) != _CONFLICT_KEYS:
            raise ValueError("Conflict result has an unsupported shape")
        for field in ("constraint_id", "code", "text", "scope"):
            if not isinstance(conflict[field], str):
                raise ValueError("Conflict result has an unsupported field type")
        if not conflict["constraint_id"] or conflict["code"] not in _CONFLICT_CODES:
            raise ValueError("Conflict result has an unsupported identifier")
        if conflict["scope"] not in {"trip", "item"}:
            raise ValueError("Conflict result has an unsupported scope")
        for field in ("item_id", "item_title", "settledness"):
            if conflict[field] is not None and not isinstance(conflict[field], str):
                raise ValueError("Conflict result has an unsupported nullable field")
        day_date = conflict["day_date"]
        if isinstance(day_date, date) and not isinstance(day_date, datetime):
            normalized_day_date: str | None = day_date.isoformat()
        elif day_date is None:
            normalized_day_date = None
        elif isinstance(day_date, str):
            # Stored replay JSON must retain an ISO date, not an arbitrary
            # string that could have been injected into a ledger row.
            normalized_day_date = date.fromisoformat(day_date).isoformat()
        else:
            raise ValueError("Conflict result has an unsupported date")
        result.append(
            {
                "constraint_id": conflict["constraint_id"], "code": conflict["code"],
                "text": conflict["text"], "scope": conflict["scope"],
                "item_id": conflict["item_id"], "item_title": conflict["item_title"],
                "day_date": normalized_day_date, "settledness": conflict["settledness"],
            }
        )
    return result


def safe_constraint_outcome(value: Mapping[str, object]) -> dict[str, object]:
    """Exact safe wire/replay representation for Create and Update."""
    if set(value) != {"id", "conflicts"}:
        raise ValueError("Constraint outcome has an unsupported shape")
    constraint_id = value["id"]
    if not isinstance(constraint_id, str) or not constraint_id or len(constraint_id) > 64:
        raise ValueError("Constraint outcome has an unsupported id")
    return {"id": constraint_id, "conflicts": safe_conflicts(value["conflicts"])}


def safe_submitted_outcome(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != {"submitted"} or value["submitted"] is not True:
        raise ValueError("Preferences outcome has an unsupported shape")
    return {"submitted": True}


def safe_deleted_outcome(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != {"deleted"} or value["deleted"] is not True:
        raise ValueError("Constraint deletion outcome has an unsupported shape")
    return {"deleted": True}
