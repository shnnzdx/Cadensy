"""Protected adapter for the existing local booking writer.

No provider, request Session, or HTTP behavior belongs here.  The command is
limited to a single existing PlanItem in the already locked authoritative Plan.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import PlanItem
from ..decisions import orchestrator as orch
from .durable import (
    CommandResponse,
    DurableCommandKernel,
    DurableCommandResult,
    HmacKeyring,
    HmacSigningKey,
    IdempotentCommand,
    OutcomeContract,
    StaticOutcomeContractRegistry,
)
from .kernel import CredentialProof, LockedPlanContext, PlanTransactionKernel, PreliminaryActorScope


BOOKING_OPERATION = "plan.item.booking.v1"
_BOOKING_RESPONSE_FIELDS = frozenset(
    {
        "id", "day_index", "day_date", "start_hour", "duration_min", "title",
        "local_title", "place", "price_per_person", "source", "settledness",
        "tags", "is_meal", "meal_type", "coords", "photoUrl",
    }
)


class BookingItemMissing(Exception):
    pass


class BookingPlanMismatch(Exception):
    pass


class StaticHmacKeyring:
    """One explicit injected key for this disabled-by-default local adapter."""

    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedBooking:
    item_id: str
    idempotency_key: str
    expected_plan_revision: int
    booked: bool


def execute_protected_booking(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedBooking,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    """Run booking/unbooking and its existing audit/notice effects atomically."""

    durable = DurableCommandKernel(
        PlanTransactionKernel(session_factory),
        keyring,
        StaticOutcomeContractRegistry((OutcomeContract(BOOKING_OPERATION, 1, _BOOKING_RESPONSE_FIELDS),)),
    )
    durable_command = IdempotentCommand(
        operation_code=BOOKING_OPERATION,
        resource_kind="plan_item",
        resource_id=command.item_id,
        idempotency_key=command.idempotency_key,
        expected_plan_revision=command.expected_plan_revision,
        command_fields={"booked": command.booked},
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedPlanContext, frozen_fields: Mapping[str, object]
    ) -> CommandResponse:
        booked = frozen_fields.get("booked")
        if not isinstance(booked, bool):
            raise ValueError("Invalid canonical booking state")
        item = session.scalar(
            select(PlanItem)
            .where(
                PlanItem.id == command.item_id,
                PlanItem.plan_id == context.plan.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if item is None:
            raise BookingItemMissing("Plan item not found")
        if item.plan_id != context.plan.id:
            raise BookingPlanMismatch("Plan item does not belong to the authoritative Plan")
        before = item.settledness
        target = "booked" if booked else "settled"
        updated = orch.set_booking_status_for_actor(
            session,
            item,
            trip_id=context.trip.id,
            actor_membership_id=context.actor.membership_id,
            booked=booked,
        )
        return CommandResponse(
            status=200,
            payload=booking_item_out(updated),
            state_changed=before != target,
        )

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        mutation=mutation,
    )


def booking_item_out(item: PlanItem) -> dict[str, object]:
    """The existing route's detached item representation, safe for the ledger."""

    return {
        "id": item.id,
        "day_index": item.day_index,
        "day_date": item.day_date.isoformat(),
        "start_hour": item.start_hour,
        "duration_min": item.duration_min,
        "title": item.title,
        "local_title": item.local_title,
        "place": item.place,
        "price_per_person": item.price_per_person,
        "source": item.source,
        "settledness": item.settledness,
        "tags": item.tags or [],
        "is_meal": item.is_meal,
        "meal_type": (
            "lunch" if item.is_meal and item.start_hour < 14.0
            else "dinner" if item.is_meal
            else None
        ),
        "coords": [item.lat, item.lng] if item.lat is not None else None,
        "photoUrl": item.photo_url,
    }
