"""Canonical invite joins for Guest and authenticated Account identities."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import AuthSession, InviteLink, Plan, Trip, TripMembership, User
from .. import auth as auth_service
from ..membership_policy import is_membership_access_authorized
from ..trips import service as trips
from .durable import _MAX_PLAN_REVISION
from .kernel import DuplicatePlans, InvalidCredential, TripNotFound
from .membership_lifecycle import require_no_decision_window
from .trip_durable import _MAX_TRIP_FACTS_REVISION, TripPlanningFactsRevisionOverflow


class LegacyAccountIdentityDenied(Exception):
    """A password-less legacy row is not evidence of Account ownership."""


class MembershipQuarantined(Exception):
    pass


class AccountMembershipJoinDenied(Exception):
    pass


class GuestJoinAccountIdentityRejected(Exception):
    pass


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _discover_trip_id(session_factory: Callable[[], Session], token: str) -> str:
    """Do an untrusted lookup; the invite is locked and checked later."""

    with session_factory() as discovery:
        candidate = discovery.scalar(
            select(InviteLink).where(InviteLink.token_hash == _token_hash(token))
        )
        if candidate is None:
            raise trips.InviteNotFound("Invite not found")
        return candidate.trip_id


def _locked_trip(session: Session, trip_id: str) -> Trip:
    trip = session.scalar(
        select(Trip)
        .where(Trip.id == trip_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if trip is None:
        raise TripNotFound("Trip does not exist")
    return trip


def _locked_invite(session: Session, trip: Trip, token: str) -> InviteLink:
    invite = session.scalar(
        select(InviteLink)
        .where(InviteLink.trip_id == trip.id, InviteLink.token_hash == _token_hash(token))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        invite is None
        or invite.revoked_at is not None
        or (invite.expires_at is not None and invite.expires_at <= _now())
    ):
        raise trips.InviteNotFound("Invite not found")
    return invite


def _locked_memberships(session: Session, trip_id: str) -> list[TripMembership]:
    return session.scalars(
        select(TripMembership)
        .where(TripMembership.trip_id == trip_id)
        .order_by(TripMembership.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).all()


def _locked_plan(session: Session, trip_id: str) -> Plan | None:
    plans = session.scalars(
        select(Plan)
        .where(Plan.trip_id == trip_id)
        .order_by(Plan.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).all()
    if len(plans) > 1:
        raise DuplicatePlans("Trip has multiple Plans; membership join is refused")
    return plans[0] if plans else None


def _advance_membership_revisions(trip: Trip, plan: Plan | None) -> None:
    if trip.planning_facts_revision >= _MAX_TRIP_FACTS_REVISION:
        raise TripPlanningFactsRevisionOverflow("Planning revision cannot advance safely")
    if plan is not None and plan.revision >= _MAX_PLAN_REVISION:
        raise TripPlanningFactsRevisionOverflow("Planning revision cannot advance safely")
    trip.planning_facts_revision += 1
    if plan is not None:
        plan.revision += 1


def join_guest_invite_under_trip_root(
    *, session_factory: Callable[[], Session], token: str, display_name: str
) -> trips.JoinedInvite:
    """Create an isolated Guest Membership and one bounded Guest credential."""

    trip_id = _discover_trip_id(session_factory, token)
    with session_factory() as session:
        with session.begin():
            trip = _locked_trip(session, trip_id)
            _locked_invite(session, trip, token)
            _locked_memberships(session, trip.id)
            plan = _locked_plan(session, trip.id)
            require_no_decision_window(session, trip.id)
            membership = TripMembership(
                trip_id=trip.id,
                user_id=None,
                guest_display_name=display_name.strip(),
                role="participant",
                join_method="invite_guest",
                status="joined",
            )
            session.add(membership)
            session.flush()
            guest_session = auth_service.start_guest_session(session, membership)
            _advance_membership_revisions(trip, plan)
            session.flush()
            return trips.JoinedInvite(
                membership=membership, trip_id=trip.id, guest_session=guest_session
            )


def join_invite_under_trip_root(
    *, session_factory: Callable[[], Session], token: str, display_name: str, email: str | None
) -> trips.JoinedInvite:
    """Compatibility entry point that routes only the former Guest shape.

    Keeping the symbol avoids an accidental direct-writer import becoming an
    Account identity bypass. Email-bearing callers must move to the verified
    Account-session writer.
    """

    if email is not None:
        raise GuestJoinAccountIdentityRejected(
            "Guest Join cannot establish an Account identity from email"
        )
    return join_guest_invite_under_trip_root(
        session_factory=session_factory, token=token, display_name=display_name
    )


def join_account_invite_under_trip_root(
    *, session_factory: Callable[[], Session], token: str, account_token: str, account_user_id: str
) -> trips.JoinedInvite:
    """Join only as the User proven by a live Account bearer.

    A removed incarnation remains immutable and a later permitted join makes a
    new row. A quarantined incarnation is intentionally not replaceable.
    """

    trip_id = _discover_trip_id(session_factory, token)
    with session_factory() as session:
        with session.begin():
            trip = _locked_trip(session, trip_id)
            auth_session = session.scalar(
                select(AuthSession)
                .where(AuthSession.token_hash == _token_hash(account_token))
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if (
                auth_session is None
                or auth_session.revoked_at is not None
                or auth_session.expires_at <= _now()
                or auth_session.user_id != account_user_id
            ):
                raise InvalidCredential("Account credential is invalid or expired")
            account = session.get(User, auth_session.user_id)
            if account is None:
                raise InvalidCredential("Account credential is invalid or expired")
            if account.password_hash is None:
                raise LegacyAccountIdentityDenied(
                    "Legacy password-less identity cannot join as an Account"
                )
            _locked_invite(session, trip, token)
            memberships = _locked_memberships(session, trip.id)
            current = next(
                (
                    membership
                    for membership in memberships
                    if membership.user_id == account.id and membership.status != "removed"
                ),
                None,
            )
            if current is not None:
                if current.quarantined_at is not None:
                    raise MembershipQuarantined("Quarantined Membership cannot rejoin")
                if not is_membership_access_authorized(current):
                    raise AccountMembershipJoinDenied(
                        "Existing Account Membership is not joinable"
                    )
                # This is not a Membership-set change, even during a Decision Window.
                return trips.JoinedInvite(membership=current, trip_id=trip.id)
            plan = _locked_plan(session, trip.id)
            require_no_decision_window(session, trip.id)
            membership = TripMembership(
                trip_id=trip.id,
                user_id=account.id,
                role="participant",
                join_method="invite_account",
                status="joined",
            )
            session.add(membership)
            session.flush()
            _advance_membership_revisions(trip, plan)
            session.flush()
            return trips.JoinedInvite(membership=membership, trip_id=trip.id)
