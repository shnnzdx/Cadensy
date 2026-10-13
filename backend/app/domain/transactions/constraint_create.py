"""Protected adapter for the authoritative Legacy constraint-create service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..preferences import service as pref_service
from .durable import CommandResponse, HmacKeyring, HmacSigningKey, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, PreliminaryActorScope
from .trip_durable import TripDurableCommandKernel, TripDurableCommandResult, TripIdempotentCommand
from .trip_kernel import LockedTripContext, TripTransactionKernel
from .trip_preferences_common import locked_actor_membership, safe_conflicts, safe_constraint_outcome


OPERATION = "constraint.create.v1"
_CONTRACT = OutcomeContract(OPERATION, 1, frozenset({"id", "conflicts"}), safe_constraint_outcome)


@dataclass(frozen=True)
class ProtectedConstraintCreate:
    idempotency_key: str
    expected_trip_facts_revision: int
    kind: str
    params: Mapping[str, object]
    importance: str
    original_text: str
    visibility: str


def execute_protected_constraint_create(*, session_factory: Callable[[], Session], scope: PreliminaryActorScope, credential: CredentialProof, command: ProtectedConstraintCreate, keyring: HmacKeyring, expires_at: datetime | None = None) -> TripDurableCommandResult:
    durable = TripDurableCommandKernel(TripTransactionKernel(session_factory), keyring, StaticOutcomeContractRegistry((_CONTRACT,)))
    durable_command = TripIdempotentCommand(
        operation_code=OPERATION, resource_kind="member_constraint_collection", resource_id=scope.membership_id,
        idempotency_key=command.idempotency_key, expected_trip_facts_revision=command.expected_trip_facts_revision,
        command_fields={"kind": command.kind, "params": dict(command.params), "importance": command.importance, "original_text": command.original_text, "visibility": command.visibility},
        fingerprint_version=1, expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(_session: Session, context: LockedTripContext, fields: Mapping[str, object]) -> CommandResponse:
        membership = locked_actor_membership(_session, context)
        row, conflicts = pref_service.add_constraint(
            _session, membership, kind=str(fields["kind"]), params=dict(fields["params"]),
            importance=str(fields["importance"]), original_text=str(fields["original_text"]), visibility=str(fields["visibility"]),
        )
        # Legacy's route returns FastAPI's default 200; retain that public
        # contract so persisted and observed replay statuses always agree.
        return CommandResponse(200, {"id": row.id, "conflicts": safe_conflicts(conflicts)})

    return durable._execute_with_session_mutation(scope=scope, credential=credential, command=durable_command, mutation=mutation)
