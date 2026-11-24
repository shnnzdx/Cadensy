"""Remove known demo trips and demo-only users from a real database safely.

The fixed organizer/participant accounts stay in place. Only the known demo
trip shapes and users that exist purely for those demo trips are removed.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select

from ..domain.auth import normalize_email
from .models import (
    AuthSession,
    ChangeProposal,
    DecisionRound,
    FinalPlanApproval,
    InviteLink,
    MemberConstraint,
    MemberConstraintPrivate,
    Plan,
    PlanChange,
    PlanItem,
    PlanItemComment,
    Preference,
    ProposalDecision,
    Trip,
    TripMembership,
    UpdateNotice,
    User,
    Vote,
)
from .seed import MEMBERS
from .session import SessionLocal
from .upsert_demo_login import DEMO_TRIP_NAME as CLOUD_DEMO_TRIP_NAME
from .upsert_demo_seed import DEMO_TRIP_NAME as SEEDED_DEMO_TRIP_NAME

FIXED_ACCOUNT_EMAILS = {
    normalize_email("organizer@cadensy.local"),
    normalize_email("participant@cadensy.local"),
}
SEEDED_DEMO_USERS = {
    normalize_email(email): name
    for name, email, _role in MEMBERS
}
DEMO_ONLY_EMAILS = {
    email
    for email in SEEDED_DEMO_USERS
    if email not in FIXED_ACCOUNT_EMAILS
}
KNOWN_DEMO_TRIP_NAMES = {
    CLOUD_DEMO_TRIP_NAME,
    SEEDED_DEMO_TRIP_NAME,
}


def _scalars(db, statement) -> list[str]:
    return list(db.scalars(statement).all())


def _delete_by_ids(db, model, column, ids: Iterable[str]) -> int:
    values = list(dict.fromkeys(ids))
    if not values:
        return 0
    return db.execute(delete(model).where(column.in_(values))).rowcount or 0


def _is_demo_only_user(user: User) -> bool:
    email = normalize_email(user.email)
    if email not in DEMO_ONLY_EMAILS:
        return False
    expected_name = SEEDED_DEMO_USERS.get(email)
    return expected_name is not None and user.name == expected_name


def purge_demo_data() -> dict:
    with SessionLocal() as db:
        trip_ids = _scalars(
            db,
            select(Trip.id).where(Trip.name.in_(KNOWN_DEMO_TRIP_NAMES)),
        )
        membership_ids = _scalars(
            db,
            select(TripMembership.id).where(TripMembership.trip_id.in_(trip_ids or [""])),
        )
        plan_ids = _scalars(
            db,
            select(Plan.id).where(Plan.trip_id.in_(trip_ids or [""])),
        )
        item_ids = _scalars(
            db,
            select(PlanItem.id).where(PlanItem.plan_id.in_(plan_ids or [""])),
        )
        constraint_ids = _scalars(
            db,
            select(MemberConstraint.id).where(
                MemberConstraint.trip_membership_id.in_(membership_ids or [""])
            ),
        )
        round_ids = _scalars(
            db,
            select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(item_ids or [""])),
        )
        proposal_ids = _scalars(
            db,
            select(ChangeProposal.id).where(
                ChangeProposal.plan_item_id.in_(item_ids or [""])
            ),
        )

        deleted = {
            "vote": _delete_by_ids(db, Vote, Vote.round_id, round_ids)
            + _delete_by_ids(db, Vote, Vote.trip_membership_id, membership_ids),
            "proposal_decision": _delete_by_ids(
                db, ProposalDecision, ProposalDecision.proposal_id, proposal_ids
            )
            + _delete_by_ids(
                db, ProposalDecision, ProposalDecision.trip_membership_id, membership_ids
            ),
            "plan_change": _delete_by_ids(db, PlanChange, PlanChange.plan_id, plan_ids)
            + _delete_by_ids(db, PlanChange, PlanChange.plan_item_id, item_ids),
            "update_notice": _delete_by_ids(db, UpdateNotice, UpdateNotice.trip_id, trip_ids),
            "decision_round": _delete_by_ids(
                db, DecisionRound, DecisionRound.plan_item_id, item_ids
            ),
            "change_proposal": _delete_by_ids(
                db, ChangeProposal, ChangeProposal.plan_item_id, item_ids
            ),
            "invite_link": _delete_by_ids(db, InviteLink, InviteLink.trip_id, trip_ids),
            "plan_item_comment": _delete_by_ids(
                db, PlanItemComment, PlanItemComment.plan_item_id, item_ids
            )
            + _delete_by_ids(
                db, PlanItemComment, PlanItemComment.trip_membership_id, membership_ids
            ),
            "member_constraint_private": _delete_by_ids(
                db,
                MemberConstraintPrivate,
                MemberConstraintPrivate.constraint_id,
                constraint_ids,
            ),
            "member_constraint": _delete_by_ids(
                db, MemberConstraint, MemberConstraint.id, constraint_ids
            ),
            "preference": _delete_by_ids(
                db, Preference, Preference.trip_membership_id, membership_ids
            ),
            "final_plan_approval": _delete_by_ids(
                db, FinalPlanApproval, FinalPlanApproval.trip_id, trip_ids
            ),
            "plan_item": _delete_by_ids(db, PlanItem, PlanItem.id, item_ids),
            "plan": _delete_by_ids(db, Plan, Plan.id, plan_ids),
            "trip_membership": _delete_by_ids(
                db, TripMembership, TripMembership.id, membership_ids
            ),
            "trip": _delete_by_ids(db, Trip, Trip.id, trip_ids),
            "auth_session": 0,
        }

        deleted_users = 0
        for user in db.scalars(
            select(User).where(func.lower(User.email).in_(DEMO_ONLY_EMAILS))
        ).all():
            if not _is_demo_only_user(user):
                continue
            if db.scalar(
                select(TripMembership.id).where(TripMembership.user_id == user.id)
            ):
                continue
            deleted["auth_session"] += _delete_by_ids(
                db, AuthSession, AuthSession.user_id, [user.id]
            )
            db.delete(user)
            deleted_users += 1

        db.commit()
        deleted["user_account"] = deleted_users
        return {
            "updated": True,
            "deleted": deleted,
            "known_demo_trip_names": sorted(KNOWN_DEMO_TRIP_NAMES),
            "fixed_account_emails": sorted(FIXED_ACCOUNT_EMAILS),
            "demo_only_emails": sorted(DEMO_ONLY_EMAILS),
        }


if __name__ == "__main__":
    result = purge_demo_data()
    print("demo data purged:", result)
