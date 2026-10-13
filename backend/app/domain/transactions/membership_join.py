"""Trip-root serialized invite joining without a browser durable ledger.

An unauthenticated Guest cannot safely replay a lost bearer-token response: no
prior actor exists for the Trip/actor/key ledger and retaining the plaintext
token would violate the credential boundary.  This adapter therefore preserves
the Legacy join identity contract while making its authoritative membership and
revision writes one short Trip-root transaction.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.models import InviteLink, Plan, Trip, TripMembership, User
from ..trips import service as trips
from .durable import _MAX_PLAN_REVISION
from .kernel import DuplicatePlans, TripNotFound
from .trip_durable import _MAX_TRIP_FACTS_REVISION, TripPlanningFactsRevisionOverflow


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def join_invite_under_trip_root(
    *, session_factory: Callable[[], Session], token: str, display_name: str, email: str | None
) -> trips.JoinedInvite:
    """Preserve Legacy account/guest results while atomically invalidating facts."""
    # Discovery is non-authoritative. The token is re-read and locked after the
    # Trip root so revocation cannot race into the mutation.
    with session_factory() as discovery:
        candidate = discovery.scalar(select(InviteLink).where(InviteLink.token_hash == _token_hash(token)))
        if candidate is None:
            raise trips.InviteNotFound("Invite not found")
        trip_id = candidate.trip_id
    with session_factory() as session:
        with session.begin():
            trip = session.scalar(select(Trip).where(Trip.id == trip_id).execution_options(populate_existing=True).with_for_update())
            if trip is None:
                raise TripNotFound("Trip does not exist")
            invite = session.scalar(select(InviteLink).where(InviteLink.trip_id == trip.id, InviteLink.token_hash == _token_hash(token)).execution_options(populate_existing=True).with_for_update())
            if invite is None or invite.revoked_at is not None or (invite.expires_at is not None and invite.expires_at <= trips._now()):
                raise trips.InviteNotFound("Invite not found")
            normalized_email = (email or "").strip().lower() or None
            existing = None
            if normalized_email is not None:
                user = session.scalar(select(User).where(func.lower(User.email) == normalized_email))
                if user is not None:
                    existing = session.scalar(select(TripMembership).where(TripMembership.trip_id == trip.id, TripMembership.user_id == user.id).execution_options(populate_existing=True).with_for_update())
            joined = trips.join_invite(session, token, display_name=display_name, email=email)
            changed = joined.guest_session is not None or existing is None and normalized_email is not None
            plans = session.scalars(select(Plan).where(Plan.trip_id == trip.id).order_by(Plan.id).execution_options(populate_existing=True).with_for_update()).all()
            if len(plans) > 1:
                raise DuplicatePlans("Trip has multiple Plans; membership join is refused")
            if changed:
                if trip.planning_facts_revision >= _MAX_TRIP_FACTS_REVISION or (plans and plans[0].revision >= _MAX_PLAN_REVISION):
                    raise TripPlanningFactsRevisionOverflow("Planning revision cannot advance safely")
                trip.planning_facts_revision += 1
                if plans:
                    plans[0].revision += 1
            session.flush()
            return joined
