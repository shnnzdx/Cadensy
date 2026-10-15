"""Canonical PR-06B membership-state predicates.

This module deliberately separates access, current-participant, and frozen
historical-electorate semantics.  Stage PR-06B.1A.1 introduces the contract
only; protected writers and read paths adopt it in the later approved stages.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import TripMembership


REMOVED_MEMBERSHIP_STATUS = "removed"


def is_membership_access_authorized(membership: TripMembership) -> bool:
    """Whether a Membership may access this Trip right now.

    This intentionally says nothing about the Account's global AuthSession.
    Quarantine is a Trip-local access revocation and a removed Membership is
    never access-authorized.
    """

    return (
        membership.status != REMOVED_MEMBERSHIP_STATUS
        and membership.quarantined_at is None
    )


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

    Membership current status and quarantine are deliberately irrelevant:
    a later lifecycle mutation cannot rewrite a Round's or Proposal's already
    captured historical electorate.
    """

    return membership_id in frozen_membership_ids


def access_authorized_membership_clause() -> ColumnElement[bool]:
    """SQL predicate for Trip-scoped authorization reads and writers."""

    return and_(
        TripMembership.status != REMOVED_MEMBERSHIP_STATUS,
        TripMembership.quarantined_at.is_(None),
    )


def current_participant_membership_clause() -> ColumnElement[bool]:
    """SQL predicate for rosters and newly captured planning/decision facts."""

    return access_authorized_membership_clause()


def frozen_electorate_membership_clause(
    frozen_membership_ids: Collection[str],
) -> ColumnElement[bool]:
    """SQL predicate for historical snapshots; never filter by current state."""

    return TripMembership.id.in_(frozen_membership_ids)
