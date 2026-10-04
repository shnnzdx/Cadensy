"""The three things an organizer can do: remind, extend a round, break a deadlock.

The organizer is not a super user. All three maintain the shared frame; none of
them decides anything on another member's behalf:

  - a reminder nudges, it cannot fill the form in
  - an extension buys time, it cannot cast a vote
  - the deadlock exit never lets the organizer pick either side's proposal.
    They can keep the current block, split the block, or clear the disputed
    activity from the shared itinerary. `clear` is recorded under the legacy
    audit origin `deadlock_remove`; callers cannot submit `remove` directly.

That last rule is the whole reason this module exists. Without an exit, a
confirmation that cannot reach agreement is a dead end; but handing the
organizer a tiebreaker would break "the organizer's preferences carry no
extra weight".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import (
    ChangeProposal,
    DecisionRound,
    PlanChange,
    PlanItem,
    Trip,
    TripMembership,
    UpdateNotice,
)

# One reminder per member per day. A nudge, not a nag.
REMIND_COOLDOWN = timedelta(hours=24)
# A round can be extended once. More time is fine; never settling is not.
EXTEND_PLANNING = timedelta(hours=24)
EXTEND_TRAVELING = timedelta(hours=2)


class OrganizerOnly(Exception):
    """Organizer-only action."""


class NothingToDo(Exception):
    """Gone, or no longer in a state that needs handling."""


class AlreadyExtended(Exception):
    """A round can only be extended once, otherwise it never settles."""


class TooSoonToRemind(Exception):
    """Reminded too recently."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_organizer(membership: TripMembership) -> None:
    if membership.role != "organizer":
        raise OrganizerOnly("Only the organizer can do this")


# ————————————————————— Reminders —————————————————————


def remind(db: Session, organizer: TripMembership, target_id: str) -> UpdateNotice:
    """Nudge one member to fill in their preferences.

    Writes a notice addressed to that person only -- never a public callout.
    Naming someone in front of the group turns "has not answered yet" into
    pressure, and this product's whole position is that silence is not leverage.
    """
    _require_organizer(organizer)
    target = db.get(TripMembership, target_id)
    if target is None or target.trip_id != organizer.trip_id:
        raise NothingToDo("Member not found")
    if target.status == "preferences_submitted":
        raise NothingToDo("This member has already submitted")

    recent = db.scalar(
        select(UpdateNotice)
        .where(
            UpdateNotice.trip_id == organizer.trip_id,
            UpdateNotice.recipient_membership_id == target_id,
            UpdateNotice.kind == "reminder",
        )
        .order_by(UpdateNotice.created_at.desc())
    )
    if recent is not None and recent.created_at > _now() - REMIND_COOLDOWN:
        raise TooSoonToRemind("This member was reminded in the last 24 hours")

    notice = UpdateNotice(
        trip_id=organizer.trip_id,
        recipient_membership_id=target_id,
        kind="reminder",
        title="Your preferences are still open",
        body=(
            "The plan is checked against what everyone needs. Yours is not in yet, "
            "so nothing of yours is being protected."
        ),
    )
    db.add(notice)
    db.flush()
    return notice


# ————————————————————— Extending a round —————————————————————


def extend_round(db: Session, organizer: TripMembership, round_id: str) -> DecisionRound:
    """Buy a round more time. The whole group is told -- the rules just changed."""
    _require_organizer(organizer)
    round_ = db.get(DecisionRound, round_id)
    if round_ is None or round_.status != "open":
        raise NothingToDo("No open round here")
    if round_.extended_at is not None:
        raise AlreadyExtended("This round has already been extended once")

    item = db.get(PlanItem, round_.plan_item_id)
    trip = db.get(Trip, item.plan.trip_id)
    window = (
        EXTEND_TRAVELING if trip and trip.status == "traveling" else EXTEND_PLANNING
    )

    round_.deadline = round_.deadline + window
    round_.extended_at = _now()
    db.add(
        UpdateNotice(
            trip_id=item.plan.trip_id,
            plan_item_id=item.id,
            kind="round",
            title=f"More time to weigh in on {item.title}",
            body=(
                "The organizer extended this round. Members who do not respond are "
                "still recorded as no preference, never as agreement."
            ),
        )
    )
    db.flush()
    return round_


# ————————————————————— The deadlock exit —————————————————————


def escalate(db: Session, membership: TripMembership, proposal_id: str) -> ChangeProposal:
    """Hand a stuck confirmation to the organizer.

    Any affected member can escalate, not just the proposer -- the person who
    is stuck is usually the one who disagrees.
    """
    proposal = db.get(ChangeProposal, proposal_id)
    if proposal is None or proposal.status != "waiting_affected_members":
        raise NothingToDo("Nothing to escalate")
    proposal.status = "escalated"
    db.flush()
    return proposal


def resolve_deadlock(
    db: Session, organizer: TripMembership, proposal_id: str, action: str
) -> PlanItem:
    """The organizer closes a deadlock without accepting the blocked proposal.

    `keep` -- reject the pending proposal; the live Current Plan stays as-is
    `split` -- the block splits; each group goes its own way and regroups after
    `clear` -- the disputed activity leaves the shared itinerary

    `deadlock_remove` remains the append-only audit origin for a `clear`
    resolution. It is not an action that callers may submit.
    """
    _require_organizer(organizer)
    if action not in ("keep", "split", "clear"):
        raise NothingToDo("An organizer can only keep, split, or clear the block")
    normalized_action = "remove" if action == "clear" else action

    proposal = db.get(ChangeProposal, proposal_id)
    if proposal is None or proposal.status != "escalated":
        raise NothingToDo("This proposal is not waiting on the organizer")

    item = db.get(PlanItem, proposal.plan_item_id)
    if normalized_action == "split":
        patch = {"title": "Split for this block", "place": "Two groups, regroup after"}
        for field, value in patch.items():
            setattr(item, field, value)
        item.settledness = "settled"
        item.settled_at = _now()
    elif normalized_action == "remove":
        patch = {"remove": True}
        item.settledness = "removed"
        item.settled_at = _now()
    else:
        patch = {}

    proposal.status = "resolved_by_organizer"
    db.add(
        PlanChange(
            plan_id=item.plan_id,
            plan_item_id=item.id,
            origin=f"deadlock_{normalized_action}",
            patch=patch,
            source_proposal_id=proposal.id,
            reason="The affected members could not agree, so the block was not decided either way.",
        )
    )
    titles = {
        "keep": "The current block was kept",
        "split": "This block was split",
        "remove": "This activity was removed",
    }
    bodies = {
        "keep": "The affected members could not agree. The organizer closed the conflict and kept the Current Plan unchanged.",
        "split": "The affected members could not agree. Rather than picking a side, two groups go their own way and regroup after.",
        "remove": "The affected members could not agree. Rather than turning the block into free time, the activity was removed from the itinerary.",
    }
    db.add(
        UpdateNotice(
            trip_id=item.plan.trip_id,
            plan_item_id=item.id,
            kind="proposal",
            title=titles[normalized_action],
            body=bodies[normalized_action],
        )
    )
    db.flush()
    return item
