"""Organizer invite retrieve, ensure, and explicit replace.

One unrevoked invite per trip. The 180-day window is set once, on the first
successful HMAC create, and replace never extends it. Page load retrieves;
it does not mint.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...db.models import InviteLink, Trip, TripMembership
from ..analytics.activation import record_invite_created
from ..membership_policy import is_membership_access_authorized
from .invite_hmac import (
    InviteHmacUnavailable,
    derive_invite_token,
    get_invite_keyring,
    mint_invite_token,
    require_invite_keyring,
    token_hash,
    token_matches_hash,
)

INVITE_WINDOW = timedelta(days=180)
TOKEN_SCHEME_HMAC = "hmac_v1"


class OrganizerRequired(Exception):
    """Only the trip organizer can manage invite links."""


class InviteNotFound(Exception):
    """Public invite lookup failed. ``code`` is safe to return to the client."""

    code = "invite_invalid"


class InviteRevoked(InviteNotFound):
    code = "invite_revoked"


class InviteExpired(InviteNotFound):
    code = "invite_expired"


class InviteJoinClosed(InviteNotFound):
    """A terminal early close. Join recognizes it and does not write it."""

    code = "invite_closed"


class InviteClosed(Exception):
    """Replace or a later approve is refused because early close is terminal."""


class InviteWindowClosed(Exception):
    """The 180-day window has elapsed. Ensure and replace do not mint."""


class InviteNotReplaceable(Exception):
    """Replace is not allowed for this status. Ensure may still create."""


class InviteConflict(Exception):
    """The unique active-invite index rejected a second unrevoked row."""


class InviteIdempotencyInvalid(Exception):
    """Idempotency-Key is present but not a usable token."""


@dataclass(frozen=True)
class InviteSnapshot:
    status: str
    can_replace: bool
    can_create: bool
    invite_id: str | None
    token: str | None
    expires_at: datetime | None
    created_at: datetime | None
    first_invite_created_at: datetime | None
    invite_window_ends_at: datetime | None
    final_plan_approved_at: datetime | None
    idempotent_replay: bool = False

    def as_dict(self) -> dict:
        body = {
            "status": self.status,
            "can_replace": self.can_replace,
            "can_create": self.can_create,
            "invite_id": self.invite_id,
            "expires_at": _iso(self.expires_at),
            "created_at": _iso(self.created_at),
            "first_invite_created_at": _iso(self.first_invite_created_at),
            "invite_window_ends_at": _iso(self.invite_window_ends_at),
            "final_plan_approved_at": _iso(self.final_plan_approved_at),
            "idempotent_replay": self.idempotent_replay,
        }
        if self.status == "active_hmac" and self.token:
            body["id"] = self.invite_id
            body["token"] = self.token
        return body


def is_hmac_era(invite: InviteLink) -> bool:
    return invite.token_scheme == TOKEN_SCHEME_HMAC and invite.key_id is not None


def assert_joinable(invite: InviteLink | None, trip: Trip | None) -> InviteLink:
    """Public join and preview. Non-HMAC rows are never joinable."""

    if invite is None:
        raise InviteNotFound("invite_invalid")
    if not is_hmac_era(invite):
        raise InviteRevoked("invite_revoked")
    if invite.revoked_at is not None:
        raise InviteRevoked("invite_revoked")
    if trip is not None and trip.final_plan_approved_at is not None:
        raise InviteJoinClosed("invite_closed")
    if _expired(invite) or _window_elapsed(trip):
        raise InviteExpired("invite_expired")
    return invite


def ensure_invite(db: Session, trip_id: str, organizer: TripMembership) -> InviteSnapshot:
    """Mint only when this trip has never created an HMAC invite."""

    trip, unrevoked = _locked_trip_and_unrevoked(db, trip_id, organizer)
    now = _now()
    if trip.first_invite_created_at is None:
        require_invite_keyring()
        _revoke_rows(unrevoked, now)
        trip.first_invite_created_at = now
        trip.invite_window_ends_at = now + INVITE_WINDOW
        invite, token = _insert_hmac(db, trip, organizer, idempotency_key=None)
        return _snapshot(trip, invite, token, idempotent_replay=False)
    status, can_replace, can_create = _classify(trip, unrevoked[0] if unrevoked else None, now)
    token = _token_for_active(status, unrevoked[0] if unrevoked else None)
    return _snapshot(
        trip,
        unrevoked[0] if unrevoked else None,
        token,
        status=status,
        can_replace=can_replace,
        can_create=can_create,
    )


def replace_invite(
    db: Session,
    trip_id: str,
    organizer: TripMembership,
    idempotency_key: str | None,
) -> InviteSnapshot:
    """Explicit replace: new invite id and token, same expiry."""

    key = _clean_idempotency_key(idempotency_key)
    trip = _lock_trip(db, trip_id, organizer)
    if key is not None:
        prior = db.scalar(
            select(InviteLink)
            .where(
                InviteLink.trip_id == trip.id,
                InviteLink.creation_idempotency_key == key,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if prior is not None:
            return _replay(db, trip, prior)
    unrevoked = _lock_unrevoked(db, trip.id)
    now = _now()
    status, can_replace, _can_create = _classify(trip, unrevoked[0] if unrevoked else None, now)
    if not can_replace:
        if trip.final_plan_approved_at is not None or status == "early_expired":
            raise InviteClosed("invite_closed")
        if status == "window_expired":
            raise InviteWindowClosed("invite_window_expired")
        raise InviteNotReplaceable("invite_not_replaceable")
    require_invite_keyring()
    _revoke_rows(unrevoked, now)
    invite, token = _insert_hmac(db, trip, organizer, idempotency_key=key)
    return _snapshot(trip, invite, token, idempotent_replay=False)


def retrieve_invite(db: Session, trip_id: str, organizer: TripMembership) -> InviteSnapshot:
    """Read-only. Never mints and does not take FOR UPDATE."""

    _require_organizer(organizer, trip_id)
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise InviteNotFound("Trip not found")
    unrevoked = db.scalar(
        select(InviteLink)
        .where(InviteLink.trip_id == trip_id, InviteLink.revoked_at.is_(None))
        .order_by(InviteLink.is_primary.desc(), InviteLink.created_at.desc())
        .limit(1)
    )
    now = _now()
    status, can_replace, can_create = _classify(trip, unrevoked, now)
    token = _token_for_active(status, unrevoked)
    return _snapshot(
        trip,
        unrevoked,
        token,
        status=status,
        can_replace=can_replace,
        can_create=can_create,
    )


def _replay(db: Session, trip: Trip, prior: InviteLink) -> InviteSnapshot:
    """Same Idempotency-Key returns the original invite and does not mint."""

    unrevoked = _lock_unrevoked(db, trip.id)
    live = unrevoked[0] if unrevoked else None
    if live is not None and live.id == prior.id:
        token = _token_for_active("active_hmac", prior)
        return _snapshot(trip, prior, token, idempotent_replay=True)
    status, can_replace, can_create = _classify(trip, live, _now())
    return _snapshot(
        trip,
        live,
        _token_for_active(status, live),
        status=status,
        can_replace=can_replace,
        can_create=can_create,
        idempotent_replay=True,
    )


def _token_for_active(status: str, invite: InviteLink | None) -> str | None:
    if status != "active_hmac" or invite is None or not is_hmac_era(invite):
        return None
    return _rederive(invite)


def _rederive(invite: InviteLink) -> str:
    keyring = get_invite_keyring()
    if keyring is None or invite.key_id is None or invite.key_id not in keyring.keys:
        raise InviteHmacUnavailable("invite_hmac_unavailable")
    token = derive_invite_token(keyring, invite.key_id, invite.id, invite.trip_id)
    if not token_matches_hash(token, invite.token_hash):
        raise InviteHmacUnavailable("invite_hmac_unavailable")
    return token


def _insert_hmac(
    db: Session,
    trip: Trip,
    organizer: TripMembership,
    idempotency_key: str | None,
) -> tuple[InviteLink, str]:
    keyring = require_invite_keyring()
    invite = InviteLink(
        trip_id=trip.id,
        token_hash=token_hash(secrets.token_urlsafe(32)),
        is_primary=True,
        expires_at=trip.invite_window_ends_at,
        token_scheme=TOKEN_SCHEME_HMAC,
        key_id=keyring.active_key_id,
        creation_idempotency_key=idempotency_key,
    )
    db.add(invite)
    try:
        db.flush()
        token = mint_invite_token(keyring, invite.id, trip.id)
        invite.token_hash = token_hash(token)
        db.flush()
    except IntegrityError as exc:
        raise InviteConflict("invite_conflict") from exc
    db.refresh(invite)
    record_invite_created(
        db, trip_id=trip.id, invite_id=invite.id, membership_id=organizer.id
    )
    return invite, token


def _snapshot(
    trip: Trip,
    invite: InviteLink | None,
    token: str | None,
    *,
    status: str | None = None,
    can_replace: bool | None = None,
    can_create: bool | None = None,
    idempotent_replay: bool = False,
) -> InviteSnapshot:
    now = _now()
    if status is None or can_replace is None or can_create is None:
        status, can_replace, can_create = _classify(trip, invite, now)
    if status != "active_hmac":
        token = None
    show_row = invite is not None and status in {"active_hmac", "non_hmac"}
    return InviteSnapshot(
        status=status,
        can_replace=can_replace,
        can_create=can_create,
        invite_id=invite.id if show_row else None,
        token=token,
        expires_at=invite.expires_at if show_row else (
            trip.invite_window_ends_at if status != "none" else None
        ),
        created_at=invite.created_at if show_row else None,
        first_invite_created_at=trip.first_invite_created_at,
        invite_window_ends_at=trip.invite_window_ends_at,
        final_plan_approved_at=trip.final_plan_approved_at,
        idempotent_replay=idempotent_replay,
    )


def _classify(
    trip: Trip, unrevoked: InviteLink | None, now: datetime
) -> tuple[str, bool, bool]:
    if trip.final_plan_approved_at is not None:
        status = "early_expired"
    elif (
        trip.first_invite_created_at is not None
        and _aware(trip.invite_window_ends_at) is not None
        and now >= _aware(trip.invite_window_ends_at)
    ):
        status = "window_expired"
    elif unrevoked is not None and is_hmac_era(unrevoked) and not _expired(unrevoked):
        status = "active_hmac"
    elif unrevoked is not None and not is_hmac_era(unrevoked):
        status = "non_hmac"
    elif trip.first_invite_created_at is not None:
        status = "revoked_replaceable"
    else:
        status = "none"
    can_replace, can_create = _flags(status, trip, now)
    return status, can_replace, can_create


def _flags(status: str, trip: Trip, now: datetime) -> tuple[bool, bool]:
    anchor = trip.first_invite_created_at is not None
    window_open = (
        anchor
        and _aware(trip.invite_window_ends_at) is not None
        and now < _aware(trip.invite_window_ends_at)
        and trip.final_plan_approved_at is None
    )
    if status == "none":
        return False, True
    if status == "active_hmac":
        return True, False
    if status == "non_hmac":
        return window_open, not anchor
    if status == "revoked_replaceable":
        return True, False
    return False, False


def _locked_trip_and_unrevoked(
    db: Session, trip_id: str, organizer: TripMembership
) -> tuple[Trip, list[InviteLink]]:
    trip = _lock_trip(db, trip_id, organizer)
    return trip, _lock_unrevoked(db, trip.id)


def _lock_trip(db: Session, trip_id: str, organizer: TripMembership) -> Trip:
    _require_organizer(organizer, trip_id)
    trip = db.scalar(
        select(Trip)
        .where(Trip.id == trip_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if trip is None:
        raise InviteNotFound("Trip not found")
    return trip


def _lock_unrevoked(db: Session, trip_id: str) -> list[InviteLink]:
    return list(
        db.scalars(
            select(InviteLink)
            .where(InviteLink.trip_id == trip_id, InviteLink.revoked_at.is_(None))
            .order_by(InviteLink.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
    )


def _revoke_rows(rows: list[InviteLink], now: datetime) -> None:
    for row in rows:
        row.revoked_at = now
        row.is_primary = False


def revoke_invite_row(db: Session, invite_id: str, organizer: TripMembership) -> None:
    invite = db.get(InviteLink, invite_id)
    if invite is None:
        raise InviteNotFound("Invite not found")
    trip = _lock_trip(db, invite.trip_id, organizer)
    locked = db.scalar(
        select(InviteLink)
        .where(InviteLink.id == invite.id, InviteLink.trip_id == trip.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if locked is None:
        raise InviteNotFound("Invite not found")
    locked.revoked_at = _now()
    locked.is_primary = False
    db.flush()


def share_is_refused(invite: InviteLink, trip: Trip | None) -> bool:
    if invite.revoked_at is not None or not is_hmac_era(invite):
        return True
    if trip is not None and trip.final_plan_approved_at is not None:
        return True
    if _expired(invite) or _window_elapsed(trip):
        return True
    return False


def _require_organizer(membership: TripMembership, trip_id: str) -> None:
    if (
        membership.trip_id != trip_id
        or membership.role != "organizer"
        or not is_membership_access_authorized(membership)
    ):
        raise OrganizerRequired("Only the organizer can manage this trip invite")


def _clean_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 128:
        raise InviteIdempotencyInvalid("idempotency_key_invalid")
    return cleaned


def _expired(invite: InviteLink) -> bool:
    expires_at = _aware(invite.expires_at)
    return expires_at is not None and expires_at <= _now()


def _window_elapsed(trip: Trip | None) -> bool:
    if trip is None or trip.first_invite_created_at is None:
        return False
    ends = _aware(trip.invite_window_ends_at)
    return ends is not None and ends <= _now()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


def _now() -> datetime:
    return datetime.now(timezone.utc)
