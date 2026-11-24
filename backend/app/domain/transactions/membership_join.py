"""Canonical invite joins for Guest and authenticated Account identities."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import (
    AuthSession,
    GuestSession,
    InviteLink,
    Plan,
    Trip,
    TripMembership,
    User,
)
from .. import auth as auth_service
from ..analytics.activation import record_member_joined
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


class GuestRejoinDenied(Exception):
    """A presented Guest credential belongs to this trip but cannot rejoin."""


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
    return trips.assert_joinable(invite, trip)


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


def _existing_guest_membership(
    session: Session, trip: Trip, guest_token: str | None
) -> TripMembership | None:
    """Return this trip's Guest membership for a live credential, or None.

    A credential for another trip is ignored. No new tracking identifier is
    created. An inactive membership for this trip is refused instead of
    duplicated.
    """

    if not guest_token or not auth_service.is_guest_token(guest_token):
        return None
    guest_session = session.scalar(
        select(GuestSession)
        .where(GuestSession.token_hash == auth_service.token_hash(guest_token))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if guest_session is None or guest_session.revoked_at is not None:
        return None
    expires_at = guest_session.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is None or expires_at <= _now() or guest_session.trip_id != trip.id:
        return None
    membership = session.get(TripMembership, guest_session.membership_id)
    if (
        membership is None
        or membership.trip_id != trip.id
        or membership.user_id is not None
    ):
        return None
    if not is_membership_access_authorized(membership):
        raise GuestRejoinDenied("Guest membership cannot rejoin")
    return membership


def join_guest_invite_under_trip_root(
    *,
    session_factory: Callable[[], Session],
    token: str,
    display_name: str,
    guest_token: str | None = None,
) -> trips.JoinedInvite:
    """Create an isolated Guest Membership and one bounded Guest credential."""

    trip_id = _discover_trip_id(session_factory, token)
    with session_factory() as session:
        with session.begin():
            trip = _locked_trip(session, trip_id)
            invite = _locked_invite(session, trip, token)
            _locked_memberships(session, trip.id)
            existing = _existing_guest_membership(session, trip, guest_token)
            if existing is not None:
                return trips.JoinedInvite(
                    membership=existing,
                    trip_id=trip.id,
                    guest_session=None,
                    already_member=True,
                )
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
            record_member_joined(
                session,
                trip_id=trip.id,
                membership_id=membership.id,
                invite_id=invite.id,
                actor_kind="guest",
                join_method="invite_guest",
            )
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
            invite = _locked_invite(session, trip, token)
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
                return trips.JoinedInvite(
                    membership=current, trip_id=trip.id, already_member=True
                )
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
            record_member_joined(
                session,
                trip_id=trip.id,
                membership_id=membership.id,
                invite_id=invite.id,
                actor_kind="account",
                join_method="invite_account",
            )
            _advance_membership_revisions(trip, plan)
            session.flush()
            return trips.JoinedInvite(membership=membership, trip_id=trip.id)
