"""Trip dashboard and creation rules.

API handlers stay thin; this module owns the cross-trip membership lookups and
the side effects required when a user creates a new trip.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ...db.models import InviteLink, Plan, PlanItem, Trip, TripMembership, User
from .. import auth as auth_service
from ..analytics.activation import (
    InviteOpenContext,
    record_client_share,
    record_invite_created,
    record_invite_opened,
    record_trip_created,
)
from ..membership_policy import (
    access_authorized_membership_clause,
    is_membership_access_authorized,
)
from ..places.service import normalize_destination
from . import cover_service


class GuestTripAccessDenied(Exception):
    """Guests do not have an account-level dashboard or creator identity."""


class OrganizerRequired(Exception):
    """Only the trip organizer can manage invite links."""


class MembershipRemovalDenied(Exception):
    """Membership removal is invalid for this actor or target."""


class InviteNotFound(Exception):
    """Public invite lookup failed. ``code`` is safe to return to the client."""

    code = "invite_invalid"


class InviteRevoked(InviteNotFound):
    """The token existed and was revoked. Do not include trip details."""

    code = "invite_revoked"


class InviteExpired(InviteNotFound):
    """The token existed and is past expires_at. Do not include trip details."""

    code = "invite_expired"


INVITE_TTL = timedelta(days=30)


MAX_COVER_FETCHES_PER_DASHBOARD_REQUEST = 4


def _initials(name: str) -> str:
    """Return initials for a name; for Chinese names, use the first two characters."""
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (parts[0][:2] if parts else "?").upper()


def describe_me(db: Session, membership: TripMembership) -> dict:
    """Identify the current viewer **within this trip**.

    Role belongs to membership, not account. The same person can organize trip A and participate in
    trip B, so the answer depends on which trip is being queried.

    Guests have no account; their name comes from the join nickname and they have no email."""
    user = db.get(User, membership.user_id) if membership.user_id else None
    is_guest = user is None
    name = user.name if user else (membership.guest_display_name or "Guest")

    return {
        "membership_id": membership.id,
        # Guests have no account, so use membership id when the frontend needs a stable id.
        "id": user.id if user else membership.id,
        "name": name,
        "initials": _initials(name),
        "email": user.email if user else None,
        # organizer | participant | guest; guest role ignores membership.role.
        "role": "guest" if is_guest else membership.role,
        "trip_id": membership.trip_id,
        "is_guest": is_guest,
    }


@dataclass(frozen=True)
class TripCreateData:
    name: str
    destination: str
    preferred_start_date: date | None
    preferred_end_date: date | None
    expected_group_size: int
    currency: str


@dataclass(frozen=True)
class CreatedTrip:
    trip: Trip
    plan: Plan
    membership: TripMembership


def _account_user(db: Session, creator: User | TripMembership) -> User:
    if isinstance(creator, User):
        return creator
    if creator.user_id is None:
        raise GuestTripAccessDenied("Guests cannot create trips")
    user = db.get(User, creator.user_id)
    if user is None:
        raise GuestTripAccessDenied("An account is required")
    return user


def create_trip(
    db: Session, creator: User | TripMembership, data: TripCreateData
) -> CreatedTrip:
    user = _account_user(db, creator)

    trip = Trip(
        name=data.name,
        destination=normalize_destination(data.destination),
        preferred_start_date=data.preferred_start_date,
        preferred_end_date=data.preferred_end_date,
        expected_group_size=data.expected_group_size,
        currency=data.currency,
        status="planning",
        created_by_user_id=user.id,
    )
    db.add(trip)
    db.flush()

    membership = TripMembership(
        trip_id=trip.id,
        user_id=user.id,
        role="organizer",
        join_method="creator",
        status="joined",
    )
    plan = Plan(
        trip_id=trip.id,
        estimated_total_per_person=0,
        currency=data.currency,
    )
    db.add_all([membership, plan])
    db.flush()
    record_trip_created(db, trip_id=trip.id, membership_id=membership.id)

    return CreatedTrip(trip=trip, plan=plan, membership=membership)


def list_user_trips(
    db: Session,
    account: User | TripMembership,
    *,
    priority_trip_id: str | None = None,
) -> list[dict]:
    user = _account_user(db, account)

    memberships = db.scalars(
        select(TripMembership)
        .where(
            TripMembership.user_id == user.id,
            access_authorized_membership_clause(),
        )
        .order_by(TripMembership.created_at)
    ).all()
    display_memberships = list(memberships)
    if priority_trip_id:
        display_memberships.sort(key=lambda row: row.trip_id != priority_trip_id)

    membership_trip_rows: list[tuple[TripMembership, Trip]] = []
    unique_trips: dict[str, Trip] = {}
    for membership in display_memberships:
        trip = db.get(Trip, membership.trip_id)
        if trip is None:
            continue
        membership_trip_rows.append((membership, trip))
        unique_trips[trip.id] = trip

    cover_fetches = 0
    # Lock trips in a stable order so concurrent dashboard requests cannot deadlock
    # when they prioritize different trips at the UI layer.
    for trip in sorted(unique_trips.values(), key=lambda row: row.id):
        if cover_fetches < MAX_COVER_FETCHES_PER_DASHBOARD_REQUEST:
            previous_fetch = trip.cover_image_fetched_at
            previous_url = trip.cover_image_url
            cover_service.ensure_trip_cover(db, trip)
            if (
                trip.cover_image_fetched_at != previous_fetch
                or trip.cover_image_url != previous_url
            ):
                cover_fetches += 1

    trips: list[dict] = []
    for my_membership, trip in membership_trip_rows:
        member_count = db.scalar(
            select(func.count())
            .select_from(TripMembership)
            .where(
                TripMembership.trip_id == trip.id,
                access_authorized_membership_clause(),
            )
        )
        plan = db.scalar(select(Plan).where(Plan.trip_id == trip.id))
        next_item = None
        if plan is not None:
            # The card says "next", not "first"; after the trip starts,
            # without this date condition it would keep showing the first day, which is already past.
            next_item = db.scalar(
                select(PlanItem)
                .where(PlanItem.plan_id == plan.id, PlanItem.day_date >= date.today())
                .order_by(PlanItem.day_date, PlanItem.start_hour)
                .limit(1)
            )

        trips.append(
            {
                "id": trip.id,
                "name": trip.name,
                "destination": trip.destination,
                "status": trip.status,
                "preferred_start_date": trip.preferred_start_date.isoformat()
                if trip.preferred_start_date
                else None,
                "preferred_end_date": trip.preferred_end_date.isoformat()
                if trip.preferred_end_date
                else None,
                "member_count": member_count or 0,
                "next_item_title": next_item.title if next_item else None,
                "membership_id": my_membership.id,
                "my_role": my_membership.role,
                **cover_service.trip_cover_out(trip),
            }
        )
    return trips


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_organizer(db: Session, trip_id: str, membership: TripMembership) -> None:
    if (
        membership.trip_id != trip_id
        or membership.role != "organizer"
        or not is_membership_access_authorized(membership)
    ):
        raise OrganizerRequired("Only the organizer can manage this trip invite")


@dataclass(frozen=True)
class CreatedInvite:
    invite: InviteLink
    token: str


def create_invite(db: Session, trip_id: str, organizer: TripMembership) -> CreatedInvite:
    _require_organizer(db, trip_id, organizer)
    if db.get(Trip, trip_id) is None:
        raise InviteNotFound("Trip not found")

    issued_at = _now()
    db.execute(
        update(InviteLink)
        .where(InviteLink.trip_id == trip_id, InviteLink.revoked_at.is_(None))
        .values(revoked_at=issued_at, is_primary=False)
    )
    token = secrets.token_urlsafe(32)
    invite = InviteLink(
        trip_id=trip_id,
        token_hash=_token_hash(token),
        is_primary=True,
        expires_at=issued_at + INVITE_TTL,
    )
    db.add(invite)
    db.flush()
    record_invite_created(
        db, trip_id=trip_id, invite_id=invite.id, membership_id=organizer.id
    )
    return CreatedInvite(invite=invite, token=token)


def _classify_invite(invite: InviteLink | None) -> InviteLink:
    if invite is None:
        raise InviteNotFound("invite_invalid")
    if invite.revoked_at is not None:
        raise InviteRevoked("invite_revoked")
    expires_at = invite.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= _now():
        raise InviteExpired("invite_expired")
    return invite


def _valid_invite(db: Session, token: str) -> InviteLink:
    invite = db.scalar(select(InviteLink).where(InviteLink.token_hash == _token_hash(token)))
    return _classify_invite(invite)


def active_invite_metadata(db: Session, trip_id: str, organizer: TripMembership) -> dict:
    """Return the reusable active invite without the raw token.

    Pre-existing unrevoked links stay valid until the organizer generates a
    new one. When more than one is still active, the primary or newest is
    the one the page reuses.
    """

    _require_organizer(db, trip_id, organizer)
    if db.get(Trip, trip_id) is None:
        raise InviteNotFound("Trip not found")
    now = _now()
    invite = db.scalar(
        select(InviteLink)
        .where(
            InviteLink.trip_id == trip_id,
            InviteLink.revoked_at.is_(None),
            or_(InviteLink.expires_at.is_(None), InviteLink.expires_at > now),
        )
        .order_by(InviteLink.is_primary.desc(), InviteLink.created_at.desc())
        .limit(1)
    )
    if invite is None:
        raise InviteNotFound("Invite not found")
    return {
        "invite_id": invite.id,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "created_at": invite.created_at.isoformat() if invite.created_at else None,
        "status": "active",
    }


def invite_preview(
    db: Session, token: str, *, opener: InviteOpenContext | None = None
) -> dict:
    invite = _valid_invite(db, token)
    record_invite_opened(
        db, trip_id=invite.trip_id, invite_id=invite.id, opener=opener
    )
    trip = db.get(Trip, invite.trip_id)
    if trip is None:
        raise InviteNotFound("Invite not found")

    organizer = db.scalar(
        select(TripMembership).where(
            TripMembership.trip_id == trip.id,
            TripMembership.role == "organizer",
        )
    )
    organizer_user = db.get(User, organizer.user_id) if organizer and organizer.user_id else None
    member_count = db.scalar(
        select(func.count())
        .select_from(TripMembership)
        .where(
            TripMembership.trip_id == trip.id,
            access_authorized_membership_clause(),
        )
    )
    return {
        "name": trip.name,
        "destination": trip.destination,
        "preferred_start_date": trip.preferred_start_date.isoformat() if trip.preferred_start_date else None,
        "preferred_end_date": trip.preferred_end_date.isoformat() if trip.preferred_end_date else None,
        "member_count": member_count or 0,
        "organizer_name": organizer_user.name if organizer_user else "Organizer",
        **cover_service.trip_cover_out(trip),
    }


@dataclass(frozen=True)
class JoinedInvite:
    membership: TripMembership
    trip_id: str
    guest_session: auth_service.GuestSessionResult | None = None
    already_member: bool = False


def record_organizer_share_event(
    db: Session,
    *,
    trip_id: str,
    organizer: TripMembership,
    event_name: str,
    invite_id: str,
) -> None:
    """Record one organizer clipboard or WeChat-template copy for this trip."""

    _require_organizer(db, trip_id, organizer)
    invite = db.get(InviteLink, invite_id)
    if invite is None or invite.trip_id != trip_id or invite.revoked_at is not None:
        raise InviteNotFound("Invite not found")
    record_client_share(
        db,
        trip_id=trip_id,
        membership_id=organizer.id,
        invite_id=invite.id,
        event_name=event_name,
    )


def revoke_invite(db: Session, invite_id: str, organizer: TripMembership) -> None:
    invite = db.get(InviteLink, invite_id)
    if invite is None:
        raise InviteNotFound("Invite not found")
    _require_organizer(db, invite.trip_id, organizer)
    invite.revoked_at = _now()
    invite.is_primary = False
    db.flush()


def remove_membership(
    db: Session,
    *,
    trip_id: str,
    organizer: TripMembership,
    membership_id: str,
) -> None:
    """Remove authorization without deleting membership history.

    Invite revocation stays separate: it prevents future joins only. Membership
    removal changes this membership's authorization state and revokes every
    live Guest credential bound to it in the same transaction.
    """
    _require_organizer(db, trip_id, organizer)
    membership = db.get(TripMembership, membership_id)
    if membership is None or membership.trip_id != trip_id:
        raise MembershipRemovalDenied("Trip membership not found")
    if membership.id == organizer.id or membership.role == "organizer":
        raise MembershipRemovalDenied("An organizer membership cannot be removed here")

    membership.status = "removed"
    auth_service.revoke_guest_sessions_for_membership(db, membership.id)
    db.flush()
