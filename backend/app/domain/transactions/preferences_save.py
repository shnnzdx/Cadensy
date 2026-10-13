"""Protected adapter for the authoritative Legacy preference-save service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..preferences import service as pref_service
from .durable import CommandResponse, HmacKeyring, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, PreliminaryActorScope
from .trip_durable import TripDurableCommandKernel, TripDurableCommandResult, TripIdempotentCommand
from .trip_kernel import LockedTripContext, TripTransactionKernel
from .trip_preferences_common import locked_actor_membership, safe_submitted_outcome


OPERATION = "preferences.save.v1"
_CONTRACT = OutcomeContract(OPERATION, 1, frozenset({"submitted"}), safe_submitted_outcome)


@dataclass(frozen=True)
class ProtectedPreferencesSave:
    idempotency_key: str
    expected_trip_facts_revision: int
    fields: Mapping[str, object]


def _preference_data(fields: Mapping[str, object]) -> pref_service.PreferenceData:
    allowed = {
        "preferred_start_date", "preferred_end_date", "available_start_date", "available_end_date",
        "ideal_budget", "maximum_budget", "currency", "budget_visibility", "travel_style", "top_interests",
    }
    if set(fields) != allowed:
        raise ValueError("Invalid preference command fields")
    def as_date(value: object) -> date | None:
        if value is None or isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value)
        raise ValueError("Invalid preference date")
    interests = fields["top_interests"]
    if not isinstance(interests, tuple):
        raise ValueError("Invalid preference interests")
    return pref_service.PreferenceData(
        preferred_start_date=as_date(fields["preferred_start_date"]), preferred_end_date=as_date(fields["preferred_end_date"]),
        available_start_date=as_date(fields["available_start_date"]), available_end_date=as_date(fields["available_end_date"]),
        ideal_budget=fields["ideal_budget"], maximum_budget=fields["maximum_budget"], currency=str(fields["currency"]),
        budget_visibility=str(fields["budget_visibility"]), travel_style=fields["travel_style"], top_interests=tuple(str(item) for item in interests),
    )


def execute_protected_preferences_save(*, session_factory: Callable[[], Session], scope: PreliminaryActorScope, credential: CredentialProof, command: ProtectedPreferencesSave, keyring: HmacKeyring, expires_at: datetime | None = None) -> TripDurableCommandResult:
    durable = TripDurableCommandKernel(TripTransactionKernel(session_factory), keyring, StaticOutcomeContractRegistry((_CONTRACT,)))
    durable_command = TripIdempotentCommand(
        operation_code=OPERATION, resource_kind="member_preference", resource_id=scope.membership_id,
        idempotency_key=command.idempotency_key, expected_trip_facts_revision=command.expected_trip_facts_revision,
        command_fields=dict(command.fields), fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(session: Session, context: LockedTripContext, fields: Mapping[str, object]) -> CommandResponse:
        membership = locked_actor_membership(session, context)
        pref_service.save_mine(session, membership, _preference_data(fields))
        # Every accepted fresh-key save persists submitted_at by Legacy contract.
        return CommandResponse(200, {"submitted": True})

    return durable._execute_with_session_mutation(scope=scope, credential=credential, command=durable_command, mutation=mutation)
