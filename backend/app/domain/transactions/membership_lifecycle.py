"""Shared locked decision-window checks for Membership-set mutations.

These helpers deliberately lock the current decision rows after the Trip root,
credential/invite, Membership, and Plan locks have been acquired by their
caller. They do not settle or otherwise alter a historical decision.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import ChangeProposal, DecisionRound, Plan, PlanItem


class MembershipChangePendingDecision(Exception):
    """A normal Membership-set mutation overlaps an unresolved decision."""


def lock_nonterminal_decisions(session: Session, trip_id: str) -> bool:
    """Lock and report the authoritative Decision Window for one Trip."""

    rounds = session.scalars(
        select(DecisionRound)
        .join(PlanItem, DecisionRound.plan_item_id == PlanItem.id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(Plan.trip_id == trip_id, DecisionRound.status == "open")
        .order_by(DecisionRound.id)
        .execution_options(populate_existing=True)
        .with_for_update(of=DecisionRound)
    ).all()
    proposals = session.scalars(
        select(ChangeProposal)
        .join(PlanItem, ChangeProposal.plan_item_id == PlanItem.id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(
            Plan.trip_id == trip_id,
            ChangeProposal.status.in_(("waiting_affected_members", "escalated")),
        )
        .order_by(ChangeProposal.id)
        .execution_options(populate_existing=True)
        .with_for_update(of=ChangeProposal)
    ).all()
    return bool(rounds or proposals)


def require_no_decision_window(session: Session, trip_id: str) -> None:
    if lock_nonterminal_decisions(session, trip_id):
        raise MembershipChangePendingDecision(
            "Membership changes are blocked while a decision is unresolved"
        )
