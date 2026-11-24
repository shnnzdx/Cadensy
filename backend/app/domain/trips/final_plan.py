"""Final-plan approval and the early-close write.

Readiness is a read of existing rows. It does not settle rounds, resolve
proposals, or change the decision engine.

A PlanItem counts toward readiness when it belongs to the selected plan and
``settledness != 'removed'``. PlanItem has no soft-delete, quarantine, or
non-itinerary flag. An open decision is ``DecisionRound.status == 'open'``.
A pending proposal is ``ChangeProposal.status == 'waiting_affected_members'``.

Close is written only when G-1 holds: an HMAC invite exists, at least two
current participants include a non-organizer, and every current participant
has an approval bound to the locked ``plan.itinerary_revision``. Join does
not call this module.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...db.models import (
    ChangeProposal,
    DecisionRound,
    FinalPlanApproval,
    Plan,
    PlanItem,
    Trip,
    TripMembership,
    new_id,
)
from ..membership_policy import is_current_participant


REASON_TEXT = {
    "plan_not_active": "The plan is not active.",
    "no_plan_items": "The itinerary has no items yet.",
    "open_decision_round": "A decision round is still open.",
    "pending_change_proposal": "A change proposal is still pending.",
}


class FinalPlanNotParticipant(Exception):
    """The caller is not a current participant of this trip."""


class FinalPlanClosed(Exception):
    """final_plan_approved_at is already set. No further approval write."""


class FinalPlanStale(Exception):
    """The client revision is not the locked itinerary_revision."""


class FinalPlanNotReady(Exception):
    def __init__(self, reasons: list[str], message: str) -> None:
        super().__init__(message)
        self.reasons = reasons
        self.message = message


def readiness(db: Session, plan: Plan | None) -> tuple[bool, list[str], str]:
    """Read-only. ``plan`` may be None when the trip has no plan row."""

    reasons: list[str] = []
    if plan is None or plan.status != "active":
        reasons.append("plan_not_active")
    if plan is None or not _has_valid_item(db, plan.id):
        reasons.append("no_plan_items")
    if plan is not None and _has_open_round(db, plan.id):
        reasons.append("open_decision_round")
    if plan is not None and _has_pending_proposal(db, plan.id):
        reasons.append("pending_change_proposal")
    message = " ".join(REASON_TEXT[reason] for reason in reasons)
    return (not reasons, reasons, message)


def read_final_plan(db: Session, trip_id: str, membership: TripMembership) -> dict:
    trip = _require_participant_trip(db, trip_id, membership)
    plan = _selected_plan(db, trip.id, lock=False)
    ready, reasons, message = readiness(db, plan)
    revision = plan.itinerary_revision if plan is not None else 0
    return _status_body(db, trip, plan, membership, revision, ready, reasons, message)


def approve_final_plan(
    db: Session, trip_id: str, membership: TripMembership, itinerary_revision: int
) -> dict:
    trip = _lock_trip(db, trip_id)
    if not is_current_participant(membership) or membership.trip_id != trip.id:
        raise FinalPlanNotParticipant("Only a current participant can approve the final plan")
    _lock_memberships(db, trip.id)
    plan = _selected_plan(db, trip.id, lock=True)
    if trip.final_plan_approved_at is not None:
        raise FinalPlanClosed("invite_closed")
    ready, reasons, message = readiness(db, plan)
    if not ready:
        raise FinalPlanNotReady(reasons, message)
    assert plan is not None
    if plan.itinerary_revision != itinerary_revision:
        raise FinalPlanStale("itinerary_revision_stale")
    now = datetime.now(timezone.utc)
    statement = pg_insert(FinalPlanApproval).values(
        id=new_id(),
        trip_id=trip.id,
        membership_id=membership.id,
        approved_at=now,
        bound_itinerary_revision=plan.itinerary_revision,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_final_plan_approval_trip_membership",
        set_={
            "bound_itinerary_revision": statement.excluded.bound_itinerary_revision,
            "approved_at": statement.excluded.approved_at,
        },
    )
    db.execute(statement)
    db.flush()
    maybe_close_final_plan(db, trip)
    db.refresh(trip)
    return {
        "approved": True,
        "membership_id": membership.id,
        "bound_itinerary_revision": plan.itinerary_revision,
        "itinerary_revision": plan.itinerary_revision,
        "final_plan_approved": trip.final_plan_approved_at is not None,
        "final_plan_approved_at": _iso(trip.final_plan_approved_at),
    }


def maybe_close_final_plan(db: Session, trip: Trip) -> bool:
    """Write early close when G-1 holds. Caller already holds the trip lock."""

    if trip.final_plan_approved_at is not None or trip.first_invite_created_at is None:
        return False
    plan = _selected_plan(db, trip.id, lock=True)
    if plan is None:
        return False
    participants = [
        row for row in _lock_memberships(db, trip.id) if is_current_participant(row)
    ]
    if len(participants) < 2 or not any(row.role != "organizer" for row in participants):
        return False
    approvals = {
        row.membership_id: row
        for row in db.scalars(
            select(FinalPlanApproval).where(FinalPlanApproval.trip_id == trip.id)
        )
    }
    revision = plan.itinerary_revision
    if not all(
        (approval := approvals.get(row.id)) is not None
        and approval.bound_itinerary_revision == revision
        for row in participants
    ):
        return False
    trip.final_plan_approved_at = datetime.now(timezone.utc)
    db.flush()
    return True


def _status_body(
    db: Session,
    trip: Trip,
    plan: Plan | None,
    membership: TripMembership,
    revision: int,
    ready: bool,
    reasons: list[str],
    message: str,
) -> dict:
    participants = [
        row
        for row in db.scalars(
            select(TripMembership)
            .where(TripMembership.trip_id == trip.id)
            .order_by(TripMembership.id)
        )
        if is_current_participant(row)
    ]
    approvals = {
        row.membership_id: row
        for row in db.scalars(
            select(FinalPlanApproval).where(FinalPlanApproval.trip_id == trip.id)
        )
    }
    own = approvals.get(membership.id)
    approved_current = 0
    anonymous = []
    for row in participants:
        approval = approvals.get(row.id)
        if approval is not None and approval.bound_itinerary_revision == revision:
            state = "approved_current"
            approved_current += 1
        elif approval is not None:
            state = "approved_earlier"
        else:
            state = "not_approved"
        anonymous.append({"role": row.role, "approval_state": state})
    return {
        "itinerary_revision": revision,
        "ready": ready,
        "reasons": reasons,
        "message": message,
        "self_approved": own is not None and own.bound_itinerary_revision == revision,
        "self_bound_itinerary_revision": own.bound_itinerary_revision if own else None,
        "approved_current_count": approved_current,
        "current_participant_count": len(participants),
        "participants": anonymous,
        "final_plan_approved": trip.final_plan_approved_at is not None,
        "final_plan_approved_at": _iso(trip.final_plan_approved_at),
    }


def _has_valid_item(db: Session, plan_id: str) -> bool:
    item_id = db.scalar(
        select(PlanItem.id)
        .where(PlanItem.plan_id == plan_id, PlanItem.settledness != "removed")
        .limit(1)
    )
    return item_id is not None


def _has_open_round(db: Session, plan_id: str) -> bool:
    round_id = db.scalar(
        select(DecisionRound.id)
        .join(PlanItem, PlanItem.id == DecisionRound.plan_item_id)
        .where(PlanItem.plan_id == plan_id, DecisionRound.status == "open")
        .limit(1)
    )
    return round_id is not None


def _has_pending_proposal(db: Session, plan_id: str) -> bool:
    proposal_id = db.scalar(
        select(ChangeProposal.id)
        .join(PlanItem, PlanItem.id == ChangeProposal.plan_item_id)
        .where(
            PlanItem.plan_id == plan_id,
            ChangeProposal.status == "waiting_affected_members",
        )
        .limit(1)
    )
    return proposal_id is not None


def _require_participant_trip(db: Session, trip_id: str, membership: TripMembership) -> Trip:
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise FinalPlanNotParticipant("Trip not found")
    if membership.trip_id != trip_id or not is_current_participant(membership):
        raise FinalPlanNotParticipant("Only a current participant can view the final plan")
    return trip


def _lock_trip(db: Session, trip_id: str) -> Trip:
    trip = db.scalar(
        select(Trip)
        .where(Trip.id == trip_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if trip is None:
        raise FinalPlanNotParticipant("Trip not found")
    return trip


def _lock_memberships(db: Session, trip_id: str) -> list[TripMembership]:
    return list(
        db.scalars(
            select(TripMembership)
            .where(TripMembership.trip_id == trip_id)
            .order_by(TripMembership.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
    )


def _selected_plan(db: Session, trip_id: str, *, lock: bool) -> Plan | None:
    statement = select(Plan).where(Plan.trip_id == trip_id).order_by(Plan.id)
    if lock:
        statement = statement.execution_options(populate_existing=True).with_for_update()
    plans = list(db.scalars(statement).all())
    if not plans:
        return None
    return min(plans, key=lambda row: ((row.created_at is None), row.created_at, row.id))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
