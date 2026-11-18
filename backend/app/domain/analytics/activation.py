"""Privacy-safe activation events.

Events are inserted with ``ON CONFLICT (dedup_key) DO NOTHING`` in the caller's
transaction. A rollback removes the event with the business write, and a retry
cannot insert a second row.

``invite_sent`` is reserved for a future email provider and has no writer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...db.models import ActivationEvent, InviteLink, Plan, Trip, TripMembership, new_id

_ID = re.compile(r"^[0-9a-f]{32}$")
_DEDUP = re.compile(r"^[a-z0-9_:-]{1,200}$")

RESERVED_EVENT_NAMES = frozenset({"invite_sent"})

EVENT_NAMES = frozenset(
    {
        "trip_created",
        "invite_created",
        "invite_link_copied",
        "invite_shared_wechat_template",
        "invite_opened",
        "member_joined",
        "preferences_submitted",
        "preferences_updated",
        "first_plan_created",
        "first_decision_completed",
        "notice_auto_applied",
    }
)

ACTOR_KINDS = frozenset({"account", "guest", "system"})

_PLAN_ORIGINS = frozenset(
    {
        "notice",
        "round",
        "reopen_round",
        "confirm",
        "ai_generate",
        "rule_generate",
        "booking",
        "deadlock_keep",
        "deadlock_split",
        "deadlock_remove",
        "preference_update",
    }
)

# Keys are the only metadata fields a named event may carry. Values are enums.
_METADATA: dict[str, dict[str, frozenset[str]]] = {
    "trip_created": {},
    "invite_created": {},
    "invite_link_copied": {"channel": frozenset({"clipboard"})},
    "invite_shared_wechat_template": {"channel": frozenset({"wechat_template"})},
    "invite_opened": {"auth_state": frozenset({"anonymous", "account", "guest"})},
    "member_joined": {
        "join_method": frozenset({"creator", "invite_guest", "invite_account"})
    },
    "preferences_submitted": {},
    "preferences_updated": {},
    "first_plan_created": {"origin": _PLAN_ORIGINS},
    "first_decision_completed": {
        "outcome": frozenset(
            {
                "round_applied",
                "round_rejected",
                "deadlock_resolved",
                "confirm_applied",
                "confirm_declined",
            }
        )
    },
    "notice_auto_applied": {"source": frozenset({"notice"})},
}

_REQUIRED_METADATA = {
    "invite_link_copied": frozenset({"channel"}),
    "invite_shared_wechat_template": frozenset({"channel"}),
    "invite_opened": frozenset({"auth_state"}),
    "member_joined": frozenset({"join_method"}),
    "first_decision_completed": frozenset({"outcome"}),
    "notice_auto_applied": frozenset({"source"}),
}

SAMPLE_THRESHOLD = 20


class ActivationEventRejected(Exception):
    """The event would break isolation, privacy, or the reserved-name rule."""


@dataclass(frozen=True)
class InviteOpenContext:
    actor_kind: str = "system"
    auth_state: str = "anonymous"
    account_user_id: str | None = None
    membership_id: str | None = None
    membership_trip_id: str | None = None


def record_activation_event(
    db: Session,
    *,
    event_name: str,
    trip_id: str,
    dedup_key: str,
    actor_kind: str,
    invite_id: str | None = None,
    membership_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert one allowlisted event. Conflicts on ``dedup_key`` are ignored."""

    if event_name in RESERVED_EVENT_NAMES or event_name not in EVENT_NAMES:
        raise ActivationEventRejected("Unsupported activation event")
    if actor_kind not in ACTOR_KINDS:
        raise ActivationEventRejected("Unsupported actor kind")
    if (
        not isinstance(dedup_key, str)
        or not _DEDUP.fullmatch(dedup_key)
        or not dedup_key.startswith(f"{event_name}:")
    ):
        raise ActivationEventRejected("Invalid dedup key")
    if not isinstance(trip_id, str) or not _ID.fullmatch(trip_id):
        raise ActivationEventRejected("Invalid trip")
    if db.get(Trip, trip_id) is None:
        raise ActivationEventRejected("Invalid trip")
    if invite_id is not None:
        if not _ID.fullmatch(invite_id):
            raise ActivationEventRejected("Invalid invite")
        invite = db.get(InviteLink, invite_id)
        if invite is None or invite.trip_id != trip_id:
            raise ActivationEventRejected("Invalid invite")
    if membership_id is not None:
        if not _ID.fullmatch(membership_id):
            raise ActivationEventRejected("Invalid membership")
        membership = db.get(TripMembership, membership_id)
        if membership is None or membership.trip_id != trip_id:
            raise ActivationEventRejected("Invalid membership")

    payload = _metadata_for(event_name, metadata)
    db.execute(
        pg_insert(ActivationEvent)
        .values(
            id=new_id(),
            event_name=event_name,
            trip_id=trip_id,
            invite_id=invite_id,
            membership_id=membership_id,
            actor_kind=actor_kind,
            occurred_at=datetime.now(timezone.utc),
            dedup_key=dedup_key,
            event_metadata=payload,
        )
        .on_conflict_do_nothing(constraint="uq_activation_event_dedup_key")
    )


def record_trip_created(db: Session, *, trip_id: str, membership_id: str) -> None:
    record_activation_event(
        db,
        event_name="trip_created",
        trip_id=trip_id,
        membership_id=membership_id,
        actor_kind="account",
        dedup_key=f"trip_created:{trip_id}",
    )


def record_invite_created(
    db: Session, *, trip_id: str, invite_id: str, membership_id: str
) -> None:
    record_activation_event(
        db,
        event_name="invite_created",
        trip_id=trip_id,
        invite_id=invite_id,
        membership_id=membership_id,
        actor_kind="account",
        dedup_key=f"invite_created:{invite_id}",
    )


def record_invite_opened(
    db: Session, *, trip_id: str, invite_id: str, opener: InviteOpenContext | None
) -> None:
    """First successful open of an invite, plus one row per authenticated account.

    Anonymous repeats do not add rows. There is no browser identifier.
    """

    context = opener or InviteOpenContext()
    actor_kind = "system"
    auth_state = "anonymous"
    membership_id = None
    if (
        context.actor_kind == "account"
        and context.auth_state == "account"
        and context.account_user_id
        and _ID.fullmatch(context.account_user_id)
    ):
        actor_kind = "account"
        auth_state = "account"
    elif (
        context.actor_kind == "guest"
        and context.auth_state == "guest"
        and context.membership_id
        and context.membership_trip_id == trip_id
        and _ID.fullmatch(context.membership_id)
    ):
        actor_kind = "guest"
        auth_state = "guest"
        membership_id = context.membership_id

    record_activation_event(
        db,
        event_name="invite_opened",
        trip_id=trip_id,
        invite_id=invite_id,
        membership_id=membership_id,
        actor_kind=actor_kind,
        dedup_key=f"invite_opened:{invite_id}",
        metadata={"auth_state": auth_state},
    )
    if actor_kind == "account" and context.account_user_id:
        record_activation_event(
            db,
            event_name="invite_opened",
            trip_id=trip_id,
            invite_id=invite_id,
            actor_kind="account",
            dedup_key=f"invite_opened:{invite_id}:account:{context.account_user_id}",
            metadata={"auth_state": "account"},
        )


def record_member_joined(
    db: Session,
    *,
    trip_id: str,
    membership_id: str,
    invite_id: str,
    actor_kind: str,
    join_method: str,
) -> None:
    record_activation_event(
        db,
        event_name="member_joined",
        trip_id=trip_id,
        invite_id=invite_id,
        membership_id=membership_id,
        actor_kind=actor_kind,
        dedup_key=f"member_joined:{membership_id}",
        metadata={"join_method": join_method},
    )


def record_preferences_submitted(db: Session, membership: TripMembership) -> None:
    record_activation_event(
        db,
        event_name="preferences_submitted",
        trip_id=membership.trip_id,
        membership_id=membership.id,
        actor_kind=_actor_for_membership(membership),
        dedup_key=f"preferences_submitted:{membership.id}",
    )


def record_preferences_updated(db: Session, membership: TripMembership) -> None:
    """One later-edit signal per membership. It does not recount the first save."""

    record_activation_event(
        db,
        event_name="preferences_updated",
        trip_id=membership.trip_id,
        membership_id=membership.id,
        actor_kind=_actor_for_membership(membership),
        dedup_key=f"preferences_updated:{membership.id}",
    )


def record_first_plan_change(db: Session, *, plan_id: str, origin: str) -> None:
    trip_id = db.scalar(select(Plan.trip_id).where(Plan.id == plan_id))
    if not isinstance(trip_id, str):
        return
    metadata = {"origin": origin} if origin in _PLAN_ORIGINS else None
    record_activation_event(
        db,
        event_name="first_plan_created",
        trip_id=trip_id,
        actor_kind="system",
        dedup_key=f"first_plan_created:{trip_id}",
        metadata=metadata,
    )


def record_first_decision_completed(db: Session, *, trip_id: str, outcome: str) -> None:
    record_activation_event(
        db,
        event_name="first_decision_completed",
        trip_id=trip_id,
        actor_kind="system",
        dedup_key=f"first_decision_completed:{trip_id}",
        metadata={"outcome": outcome},
    )


def record_notice_auto_applied(db: Session, *, trip_id: str) -> None:
    record_activation_event(
        db,
        event_name="notice_auto_applied",
        trip_id=trip_id,
        actor_kind="system",
        dedup_key=f"notice_auto_applied:{trip_id}",
        metadata={"source": "notice"},
    )


def record_client_share(
    db: Session,
    *,
    trip_id: str,
    membership_id: str,
    invite_id: str,
    event_name: str,
) -> None:
    channel = {
        "invite_link_copied": "clipboard",
        "invite_shared_wechat_template": "wechat_template",
    }.get(event_name)
    if channel is None:
        raise ActivationEventRejected("Unsupported activation event")
    record_activation_event(
        db,
        event_name=event_name,
        trip_id=trip_id,
        invite_id=invite_id,
        membership_id=membership_id,
        actor_kind="account",
        dedup_key=f"{event_name}:{invite_id}:{membership_id}",
        metadata={"channel": channel},
    )


def _actor_for_membership(membership: TripMembership) -> str:
    return "guest" if membership.user_id is None else "account"


def _metadata_for(event_name: str, metadata: dict | None) -> dict:
    allowed = _METADATA[event_name]
    required = _REQUIRED_METADATA.get(event_name, frozenset())
    raw = {} if metadata is None else metadata
    if not isinstance(raw, dict):
        raise ActivationEventRejected("Invalid metadata")
    if set(raw) - set(allowed):
        raise ActivationEventRejected("Invalid metadata")
    if not required.issubset(raw):
        raise ActivationEventRejected("Invalid metadata")
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        permitted = allowed[key]
        if not isinstance(value, str) or value not in permitted:
            raise ActivationEventRejected("Invalid metadata")
        cleaned[key] = value
    return cleaned
