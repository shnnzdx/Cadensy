"""Protected adapter for the authoritative Legacy constraint-update service."""

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
from .trip_preferences_common import locked_actor_membership, locked_owned_constraint, plan_has_items, safe_conflicts, safe_constraint_outcome


OPERATION = "constraint.update.v1"
_CONTRACT = OutcomeContract(OPERATION, 1, frozenset({"id", "conflicts"}), safe_constraint_outcome)


@dataclass(frozen=True)
class ProtectedConstraintUpdate:
    constraint_id: str
    idempotency_key: str
    expected_trip_facts_revision: int
    # Presence is represented by the keys in this mapping; explicit null is retained.
    patch: Mapping[str, object]


def execute_protected_constraint_update(*, session_factory: Callable[[], Session], scope: PreliminaryActorScope, credential: CredentialProof, command: ProtectedConstraintUpdate, keyring: HmacKeyring, expires_at: datetime | None = None) -> TripDurableCommandResult:
    durable = TripDurableCommandKernel(TripTransactionKernel(session_factory), keyring, StaticOutcomeContractRegistry((_CONTRACT,)))
    durable_command = TripIdempotentCommand(
        operation_code=OPERATION, resource_kind="member_constraint", resource_id=command.constraint_id,
        idempotency_key=command.idempotency_key, expected_trip_facts_revision=command.expected_trip_facts_revision,
        command_fields={"patch": dict(command.patch)}, fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(session: Session, context: LockedTripContext, fields: Mapping[str, object]) -> CommandResponse:
        membership = locked_actor_membership(session, context)
        row = locked_owned_constraint(session, context, command.constraint_id)
        patch = fields.get("patch")
        if not isinstance(patch, Mapping) or not set(patch) <= {"params", "importance"}:
            raise ValueError("Invalid constraint patch")
        params_present, importance_present = "params" in patch, "importance" in patch
        params = patch.get("params")
        importance = patch.get("importance")
        if params_present and params is not None and not isinstance(params, Mapping):
            raise pref_service.InvalidConstraintParams("params must be an object")
        if importance_present and importance is not None and not isinstance(importance, str):
            raise ValueError("Invalid constraint importance")
        normalized_params = (
            pref_service._validated_constraint_params(pref_service.ConstraintKind(row.kind), dict(params))
            if params_present and params is not None else row.params or {}
        )
        target_importance = str(importance) if importance_present and importance is not None else row.importance
        direct_change = normalized_params != (row.params or {}) or target_importance != row.importance
        derived_change = bool(context.plan is not None and plan_has_items(session, context) and not context.plan.needs_refresh)
        if not direct_change and not derived_change:
            return CommandResponse(200, {"id": row.id, "conflicts": safe_conflicts(pref_service.scan_conflicts(session, membership, only=row))}, state_changed=False)
        updated, conflicts = pref_service.update_constraint(
            session, membership, row.id,
            params=dict(params) if params_present and params is not None else None,
            importance=target_importance if importance_present else None,
        )
        return CommandResponse(200, {"id": updated.id, "conflicts": safe_conflicts(conflicts)})

    return durable._execute_with_session_mutation(scope=scope, credential=credential, command=durable_command, mutation=mutation)
