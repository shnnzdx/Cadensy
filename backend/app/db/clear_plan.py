"""Empty the demo trip's itinerary so `Generate` can be demonstrated.

Keeps the 6 members and their hard limits. Only the 9 plan items and the
decisions attached to them go away.

Why this exists: the seeded trip ships with an itinerary, so
POST /plans/generate correctly refuses with 409. A freshly created trip has
no members and no constraints, so generating there shows nothing interesting.
This gives you the one case worth demonstrating -- six people's hard limits
already in place, and the generator has to find a plan that clears all of them.

    .venv/bin/python -m app.db.clear_plan
"""

from __future__ import annotations

from sqlalchemy import delete, select

from .models import (
    ChangeProposal,
    DecisionRound,
    FinalPlanApproval,
    Plan,
    PlanChange,
    PlanItem,
    ProposalDecision,
    Trip,
    UpdateNotice,
    Vote,
)
from .session import SessionLocal


def clear() -> dict:
    with SessionLocal() as db:
        cleared = {}
        for model in (PlanChange, UpdateNotice, Vote, ProposalDecision,
                      DecisionRound, ChangeProposal):
            cleared[model.__tablename__] = db.execute(delete(model)).rowcount
            db.flush()

        cleared["plan_item"] = db.execute(delete(PlanItem)).rowcount
        cleared["final_plan_approval"] = db.execute(delete(FinalPlanApproval)).rowcount
        for plan in db.scalars(select(Plan)):
            plan.status = "active"
            plan.estimated_total_per_person = 0
        # Clear the terminal approval flag only. Invite anchors stay.
        for trip in db.scalars(select(Trip)):
            trip.final_plan_approved_at = None
        db.commit()
        return cleared


if __name__ == "__main__":
    result = clear()
    print("行程清空了，成员和硬底线都还在：")
    for table, count in result.items():
        if count:
            print(f"  清掉 {table}: {count} 条")
