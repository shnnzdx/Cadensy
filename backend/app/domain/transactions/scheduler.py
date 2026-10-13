"""Server-owned, Trip-root serialized Scheduler writers.

The scheduler has no human credential and therefore intentionally does not use
the interactive durable ledger.  Its stable work identity is the authoritative
child state: an open due round can settle once; a waiting due proposal can
expire once.  After the terminal state commits, retry observes a no-op.
"""

from __future__ import annotations

from datetime import datetime
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import ChangeProposal, DecisionRound, Plan, PlanItem, Trip, TripMembership, Vote
from ..decisions import orchestrator as orch
from .durable import _MAX_PLAN_REVISION, PlanRevisionOverflow
from .kernel import DuplicatePlans, TripNotFound


def _locked_plan(session: Session, trip_id: str) -> Plan:
    trip = session.scalar(select(Trip).where(Trip.id == trip_id).execution_options(populate_existing=True).with_for_update())
    if trip is None:
        raise TripNotFound("Trip does not exist")
    plans = session.scalars(select(Plan).where(Plan.trip_id == trip.id).order_by(Plan.id).execution_options(populate_existing=True).with_for_update()).all()
    if len(plans) > 1:
        raise DuplicatePlans("Trip has multiple Plans; Scheduler command is refused")
    if not plans:
        raise TripNotFound("Due decision has no authoritative Plan")
    return plans[0]


def settle_due_round(*, session_factory: Callable[[], Session], trip_id: str, round_id: str, now: datetime) -> bool:
    """Settle one still-due Round, advancing Plan revision only on settlement."""
    with session_factory() as session:
        with session.begin():
            plan = _locked_plan(session, trip_id)
            session.scalars(select(TripMembership).where(TripMembership.trip_id == trip_id).order_by(TripMembership.id).execution_options(populate_existing=True).with_for_update()).all()
            round_ = session.scalar(select(DecisionRound).join(PlanItem, DecisionRound.plan_item_id == PlanItem.id).where(DecisionRound.id == round_id, PlanItem.plan_id == plan.id).execution_options(populate_existing=True).with_for_update(of=DecisionRound))
            if round_ is None or round_.status != "open" or round_.deadline > now:
                return False
            item = session.scalar(select(PlanItem).where(PlanItem.id == round_.plan_item_id, PlanItem.plan_id == plan.id).execution_options(populate_existing=True).with_for_update())
            if item is None:
                return False
            session.scalars(select(Vote).where(Vote.round_id == round_.id).order_by(Vote.trip_membership_id, Vote.id).execution_options(populate_existing=True).with_for_update()).all()
            orch.settle_round(session, round_)
            if plan.revision >= _MAX_PLAN_REVISION:
                raise PlanRevisionOverflow("Plan revision cannot advance safely")
            plan.revision += 1
            session.flush()
            return True


def expire_due_proposal(*, session_factory: Callable[[], Session], trip_id: str, proposal_id: str, now: datetime) -> bool:
    """Expire one still-pending Proposal under the canonical Trip root lock."""
    with session_factory() as session:
        with session.begin():
            plan = _locked_plan(session, trip_id)
            session.scalars(select(TripMembership).where(TripMembership.trip_id == trip_id).order_by(TripMembership.id).execution_options(populate_existing=True).with_for_update()).all()
            proposal = session.scalar(select(ChangeProposal).join(PlanItem, ChangeProposal.plan_item_id == PlanItem.id).where(ChangeProposal.id == proposal_id, PlanItem.plan_id == plan.id).execution_options(populate_existing=True).with_for_update(of=ChangeProposal))
            if proposal is None or proposal.status not in {"waiting_affected_members", "escalated"} or proposal.deadline is None or proposal.deadline > now:
                return False
            item = session.scalar(select(PlanItem).where(PlanItem.id == proposal.plan_item_id, PlanItem.plan_id == plan.id).execution_options(populate_existing=True).with_for_update())
            if item is None:
                return False
            # Reuse the established domain state transition, constrained to this
            # locked proposal rather than scanning unrelated rows.
            proposal.status = "expired"
            proposal.expired_at = now
            from ...db.models import UpdateNotice
            session.add(UpdateNotice(trip_id=trip_id, plan_item_id=item.id, kind="proposal", title=f"A proposed change to {item.title} expired", body="Not everyone affected confirmed in time, so the Current Plan is unchanged. Anyone can propose it again."))
            if plan.revision >= _MAX_PLAN_REVISION:
                raise PlanRevisionOverflow("Plan revision cannot advance safely")
            plan.revision += 1
            session.flush()
            return True
