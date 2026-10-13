"""Protected Trip-facts adapter for the existing organizer member removal."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import GuestSession, TripMembership
from ..trips import service as trips
from .durable import CommandResponse, HmacKeyring, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, PreliminaryActorScope
from .trip_durable import TripDurableCommandKernel, TripDurableCommandResult, TripIdempotentCommand
from .trip_kernel import LockedTripContext, TripTransactionKernel


OPERATION = "membership.remove.v1"
_CONTRACT = OutcomeContract(OPERATION, 1, frozenset({"removed"}))


class MembershipTargetMissing(Exception):
    pass


@dataclass(frozen=True)
class ProtectedMembershipRemove:
    membership_id: str
    idempotency_key: str
    expected_trip_facts_revision: int


def execute_protected_membership_remove(
    *, session_factory: Callable[[], Session], scope: PreliminaryActorScope,
    credential: CredentialProof, command: ProtectedMembershipRemove, keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> TripDurableCommandResult:
    durable = TripDurableCommandKernel(
        TripTransactionKernel(session_factory), keyring,
        StaticOutcomeContractRegistry((_CONTRACT,)),
    )
    durable_command = TripIdempotentCommand(
        operation_code=OPERATION, resource_kind="trip_membership", resource_id=command.membership_id,
        idempotency_key=command.idempotency_key,
        expected_trip_facts_revision=command.expected_trip_facts_revision,
        command_fields={"intent": "remove"}, fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(session: Session, context: LockedTripContext, _fields: Mapping[str, object]) -> CommandResponse:
        actor = session.scalar(select(TripMembership).where(TripMembership.id == context.actor.membership_id, TripMembership.trip_id == context.trip.id).execution_options(populate_existing=True).with_for_update())
        target = session.scalar(select(TripMembership).where(TripMembership.id == command.membership_id, TripMembership.trip_id == context.trip.id).execution_options(populate_existing=True).with_for_update())
        if actor is None or target is None:
            raise MembershipTargetMissing("Trip membership not found")
        session.scalars(select(GuestSession).where(GuestSession.membership_id == target.id).order_by(GuestSession.id).execution_options(populate_existing=True).with_for_update()).all()
        if target.status == "removed":
            return CommandResponse(200, {"removed": True}, state_changed=False)
        trips.remove_membership(session, trip_id=context.trip.id, organizer=actor, membership_id=target.id)
        return CommandResponse(200, {"removed": True})

    return durable._execute_with_session_mutation(
        scope=scope, credential=credential, command=durable_command,
        required_role="organizer", lock_all_trip_memberships=True, mutation=mutation,
    )
