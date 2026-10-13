"""Protected adapter for the authoritative Legacy constraint-delete service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..preferences import service as pref_service
from .durable import CommandResponse, HmacKeyring, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, PreliminaryActorScope
from .trip_durable import TripDurableCommandKernel, TripDurableCommandResult, TripIdempotentCommand
from .trip_kernel import LockedTripContext, TripTransactionKernel
from .trip_preferences_common import locked_actor_membership, locked_constraint_private, locked_owned_constraint, safe_deleted_outcome


OPERATION = "constraint.delete.v1"
_CONTRACT = OutcomeContract(OPERATION, 1, frozenset({"deleted"}), safe_deleted_outcome)


@dataclass(frozen=True)
class ProtectedConstraintDelete:
    constraint_id: str
    idempotency_key: str
    expected_trip_facts_revision: int


def execute_protected_constraint_delete(*, session_factory: Callable[[], Session], scope: PreliminaryActorScope, credential: CredentialProof, command: ProtectedConstraintDelete, keyring: HmacKeyring, expires_at: datetime | None = None) -> TripDurableCommandResult:
    durable = TripDurableCommandKernel(TripTransactionKernel(session_factory), keyring, StaticOutcomeContractRegistry((_CONTRACT,)))
    durable_command = TripIdempotentCommand(
        operation_code=OPERATION, resource_kind="member_constraint", resource_id=command.constraint_id,
        idempotency_key=command.idempotency_key, expected_trip_facts_revision=command.expected_trip_facts_revision,
        command_fields={}, fingerprint_version=1, expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(session: Session, context: LockedTripContext, _fields: Mapping[str, object]) -> CommandResponse:
        membership = locked_actor_membership(session, context)
        row = locked_owned_constraint(session, context, command.constraint_id)
        # Locking the private companion is explicit; Legacy retains its safe
        # flush-before-parent-delete ordering and remains authoritative.
        locked_constraint_private(session, row.id)
        pref_service.delete_constraint(session, membership, row.id)
        return CommandResponse(200, {"deleted": True})

    return durable._execute_with_session_mutation(scope=scope, credential=credential, command=durable_command, mutation=mutation)
