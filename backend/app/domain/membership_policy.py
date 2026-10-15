"""Canonical PR-06B membership-state predicates.

This module deliberately separates access, current-participant, and frozen
historical-electorate semantics.  Stage PR-06B.1A.1 introduces the contract
only; protected writers and read paths adopt it in the later approved stages.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import TripMembership


# Preference submission is an active Account state in the established writer
# contract.  It must not invalidate an Account's own next protected command.
ACCOUNT_ACCESS_STATUSES = ("joined", "preferences_submitted")
# This preserves the existing explicit Guest credential policy in auth.py.
GUEST_ACCESS_STATUSES = ("joined", "preferences_submitted")


def _access_status_clause() -> ColumnElement[bool]:
    """Allow only explicitly recognized active status/identity combinations."""

    return or_(
        and_(
            TripMembership.user_id.is_not(None),
            TripMembership.status.in_(ACCOUNT_ACCESS_STATUSES),
        ),
        and_(
            TripMembership.user_id.is_(None),
            TripMembership.status.in_(GUEST_ACCESS_STATUSES),
        ),
    )


def is_membership_access_authorized(membership: TripMembership) -> bool:
    """Whether a Membership may access this Trip right now.

    This intentionally says nothing about the Account's global AuthSession.
    Quarantine is a Trip-local access revocation and a removed Membership is
    never access-authorized.
    """

    allowed_statuses = (
        ACCOUNT_ACCESS_STATUSES
        if membership.user_id is not None
        else GUEST_ACCESS_STATUSES
    )
    return membership.status in allowed_statuses and membership.quarantined_at is None


def is_current_participant(membership: TripMembership) -> bool:
    """Whether a Membership contributes to newly captured Trip facts.

    At this stage its truth conditions match Trip access.  Keeping this named
    predicate separate prevents later planners from accidentally substituting
    a historical-electorate rule when querying a current roster.
    """

    return is_membership_access_authorized(membership)


def is_frozen_electorate_member(
    membership_id: str, frozen_membership_ids: Collection[str]
) -> bool:
    """Return membership inclusion from a persisted electorate snapshot only.

    Membership current status and quarantine are deliberately irrelevant. This
    predicate is for persisted Membership-ID snapshots (for example,
    ProposalDecision rows), not DecisionRound: a Round stores only its frozen
    electorate *count*, never an ID snapshot.
    """

    return membership_id in frozen_membership_ids


def access_authorized_membership_clause() -> ColumnElement[bool]:
    """SQL predicate for Trip-scoped authorization reads and writers."""

    return and_(_access_status_clause(), TripMembership.quarantined_at.is_(None))


def current_participant_membership_clause() -> ColumnElement[bool]:
    """SQL predicate for rosters and newly captured planning/decision facts."""

    return access_authorized_membership_clause()


def frozen_electorate_membership_clause(
    frozen_membership_ids: Collection[str],
) -> ColumnElement[bool]:
    """SQL predicate for persisted ID snapshots; never filter by current state.

    DecisionRound uses ``electorate_count`` instead; this must not be used to
    imply that a Round stores Membership IDs.
    """

    return TripMembership.id.in_(frozen_membership_ids)
