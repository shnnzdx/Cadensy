"""Durable idempotency infrastructure for Trip planning-facts commands only."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import MutationIdempotency
from ..idempotency import CanonicalPayloadError, RequestHmac, canonical_payload_snapshot, canonical_request_hmac
from .durable import (
    CommandResponse, DurableCommandError, FingerprintVersionMismatch, HmacKeyring,
    HmacSigningKey, IdempotencyKeyReused, IdempotencyRecordExpired, InvalidDurableCommand,
    InvalidIdempotencyKey, OutcomeContractRegistry, UnknownHmacKeyVersion,
    UnsupportedOutcomeContract, _IDEMPOTENCY_KEY, _MAX_PLAN_REVISION, _NoOpMutationGuard,
    _bounded_identifier, _decode_stored_payload, _encode_response, _is_expired,
    _require_supported_fingerprint_version,
)
from .kernel import CredentialProof, PreliminaryActorScope, SafeCommandResult
from .trip_kernel import LockedTripContext, TripTransactionKernel

_MAX_TRIP_FACTS_REVISION = 9_223_372_036_854_775_807
_SCOPE = "trip_planning_facts"


class StaleTripPlanningFactsRevision(DurableCommandError):
    def __init__(self, *, expected: int, current: int):
        self.expected, self.current = expected, current
        super().__init__("Expected Trip planning-facts revision is stale")


class TripPlanningFactsRevisionOverflow(DurableCommandError):
    pass


@dataclass(frozen=True)
class TripIdempotentCommand:
    operation_code: str
    resource_kind: str
    resource_id: str
    idempotency_key: str
    expected_trip_facts_revision: int
    command_fields: Mapping[str, object]
    fingerprint_version: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _bounded_identifier(self.operation_code, field="operation_code", maximum=80)
        _bounded_identifier(self.resource_kind, field="resource_kind", maximum=40)
        _bounded_identifier(self.resource_id, field="resource_id", maximum=64)
        if not isinstance(self.idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise InvalidIdempotencyKey("Idempotency key has an invalid representation")
        if isinstance(self.expected_trip_facts_revision, bool) or not isinstance(self.expected_trip_facts_revision, int) or self.expected_trip_facts_revision < 0:
            raise InvalidDurableCommand("Expected Trip planning-facts revision must be a non-negative integer")
        if isinstance(self.fingerprint_version, bool) or not isinstance(self.fingerprint_version, int) or self.fingerprint_version < 1:
            raise InvalidDurableCommand("Fingerprint version must be a positive integer")
        if not isinstance(self.command_fields, Mapping):
            raise InvalidDurableCommand("Command fields must be a mapping")
        if self.expires_at.tzinfo is None or self.expires_at <= datetime.now(timezone.utc):
            raise InvalidDurableCommand("Ledger expiry must be timezone-aware and in the future")
        try:
            object.__setattr__(self, "command_fields", canonical_payload_snapshot(self.command_fields))
        except CanonicalPayloadError as exc:
            raise InvalidDurableCommand("Command fields cannot be canonically snapshotted") from exc


@dataclass(frozen=True)
class TripDurableCommandResult(SafeCommandResult):
    status: int
    payload: dict[str, object]
    trip_planning_facts_revision: int
    replayed: bool


class TripDurableCommandKernel:
    """Trip-root owned-transaction durable protocol; no HTTP or business rules."""

    def __init__(self, kernel: TripTransactionKernel[object], keyring: HmacKeyring, outcome_contracts: OutcomeContractRegistry):
        self._kernel, self._keyring, self._outcome_contracts = kernel, keyring, outcome_contracts

    def execute(self, *, scope: PreliminaryActorScope, credential: CredentialProof, command: TripIdempotentCommand, required_role: str | None = None, mutation: Callable[[LockedTripContext, Mapping[str, object]], CommandResponse]) -> TripDurableCommandResult:
        return self._execute_with_session_mutation(
            scope=scope, credential=credential, command=command, required_role=required_role,
            mutation=lambda _session, context, fields: mutation(context, fields),
        )

    def _execute_with_session_mutation(self, *, scope: PreliminaryActorScope, credential: CredentialProof, command: TripIdempotentCommand, required_role: str | None = None, lock_all_trip_memberships: bool = False, mutation: Callable[[Session, LockedTripContext, Mapping[str, object]], CommandResponse]) -> TripDurableCommandResult:
        _require_supported_fingerprint_version(command.fingerprint_version)

        def run(session: Session, context: LockedTripContext) -> TripDurableCommandResult:
            ledger = session.scalar(select(MutationIdempotency).where(
                MutationIdempotency.trip_id == context.trip.id,
                MutationIdempotency.actor_membership_id == context.actor.membership_id,
                MutationIdempotency.idempotency_key == command.idempotency_key,
            ).execution_options(populate_existing=True).with_for_update())
            if ledger is not None:
                return self._replay(ledger=ledger, context=context, command=command)
            outcome = self._outcome_contracts.current_for(command.operation_code)
            if outcome is None or outcome.operation_code != command.operation_code:
                raise UnsupportedOutcomeContract("Operation has no trusted current outcome contract")
            if context.trip.planning_facts_revision != command.expected_trip_facts_revision:
                raise StaleTripPlanningFactsRevision(expected=command.expected_trip_facts_revision, current=context.trip.planning_facts_revision)
            request_hmac = self._fingerprint(command=command, context=context, key=self._keyring.active_key(), fingerprint_version=command.fingerprint_version)
            savepoint, guard = session.begin_nested(), _NoOpMutationGuard(session)
            try:
                response = mutation(session, context, command.command_fields)
                payload = _encode_response(
                    response,
                    allowed_top_level_fields=outcome.allowed_response_fields,
                    payload_validator=outcome.payload_validator,
                )
                if response.state_changed:
                    savepoint.commit()
                else:
                    guard.require_clean()
                    savepoint.rollback()
            except BaseException:
                if savepoint.is_active:
                    savepoint.rollback()
                raise
            finally:
                guard.close()
            self._checkpoint("after_domain_effect")
            if response.state_changed:
                if context.trip.planning_facts_revision >= _MAX_TRIP_FACTS_REVISION:
                    raise TripPlanningFactsRevisionOverflow("Trip planning-facts revision cannot be incremented safely")
                if context.plan is not None and context.plan.revision >= _MAX_PLAN_REVISION:
                    raise TripPlanningFactsRevisionOverflow("Plan revision cannot be incremented safely")
                context.trip.planning_facts_revision += 1
                if context.plan is not None:
                    context.plan.revision += 1
            resulting_revision = context.trip.planning_facts_revision
            self._checkpoint("after_revision_increment")
            now = datetime.now(timezone.utc)
            session.add(MutationIdempotency(
                trip_id=context.trip.id, actor_membership_id=context.actor.membership_id,
                idempotency_key=command.idempotency_key, operation_code=command.operation_code,
                resource_kind=command.resource_kind, resource_id=command.resource_id,
                fingerprint_version=request_hmac.fingerprint_version, request_hmac=request_hmac.digest,
                hmac_key_version=request_hmac.hmac_key_version, outcome_contract_version=outcome.version,
                response_status=response.status, outcome_json=payload, aggregate_scope=_SCOPE,
                resulting_plan_revision=None, resulting_trip_planning_facts_revision=resulting_revision,
                completed_at=now, expires_at=command.expires_at,
            ))
            self._checkpoint("after_ledger_insert")
            session.flush()
            return TripDurableCommandResult(response.status, payload, resulting_revision, False)

        return self._kernel._run_locked_command(
            scope=scope, credential=credential, required_role=required_role,
            lock_all_trip_memberships=lock_all_trip_memberships, operation=run,
        )

    def _replay(self, *, ledger: MutationIdempotency, context: LockedTripContext, command: TripIdempotentCommand) -> TripDurableCommandResult:
        # Reject foreign scope before any foreign HMAC or result interpretation.
        if ledger.aggregate_scope != _SCOPE:
            raise IdempotencyKeyReused("Idempotency key belongs to another aggregate scope")
        key = self._keyring.key_for_version(ledger.hmac_key_version)
        _require_supported_fingerprint_version(ledger.fingerprint_version)
        if command.fingerprint_version != ledger.fingerprint_version:
            raise FingerprintVersionMismatch("Incoming and stored fingerprint versions differ")
        if key is None:
            raise UnknownHmacKeyVersion("Stored idempotency record cannot be verified")
        expected = self._fingerprint(command=command, context=context, key=key, fingerprint_version=ledger.fingerprint_version)
        if not hmac.compare_digest(ledger.request_hmac, expected.digest) or ledger.operation_code != command.operation_code:
            raise IdempotencyKeyReused("Idempotency key belongs to another command")
        if _is_expired(ledger.expires_at):
            raise IdempotencyRecordExpired("Retained idempotency record has expired")
        if ledger.resulting_plan_revision is not None or ledger.resulting_trip_planning_facts_revision is None:
            raise UnsupportedOutcomeContract("Stored outcome has no valid Trip result revision")
        if isinstance(ledger.response_status, bool) or not isinstance(ledger.response_status, int) or not 200 <= ledger.response_status < 300:
            raise UnsupportedOutcomeContract("Stored outcome status is unsupported")
        historical = self._outcome_contracts.for_version(command.operation_code, ledger.outcome_contract_version)
        current = self._outcome_contracts.current_for(command.operation_code)
        if historical is None or historical.operation_code != command.operation_code:
            raise UnsupportedOutcomeContract("Stored outcome contract is unsupported")
        if current is None or current.operation_code != command.operation_code:
            raise UnsupportedOutcomeContract("Current outcome contract is unsupported")
        payload = _decode_stored_payload(
            ledger.outcome_json,
            allowed_top_level_fields=historical.allowed_response_fields,
            payload_validator=historical.payload_validator,
        )
        payload = _decode_stored_payload(
            payload,
            allowed_top_level_fields=current.allowed_response_fields,
            payload_validator=current.payload_validator,
        )
        return TripDurableCommandResult(ledger.response_status, payload, ledger.resulting_trip_planning_facts_revision, True)

    def _fingerprint(self, *, command: TripIdempotentCommand, context: LockedTripContext, key: HmacSigningKey, fingerprint_version: int) -> RequestHmac:
        try:
            return canonical_request_hmac(command.operation_code, {
                "aggregate_scope": _SCOPE, "trip_id": context.trip.id,
                "actor_membership_id": context.actor.membership_id,
                "resource": {"kind": command.resource_kind, "id": command.resource_id},
                "expected_trip_facts_revision": command.expected_trip_facts_revision,
                "command": command.command_fields,
            }, key=key.key, fingerprint_version=fingerprint_version, hmac_key_version=key.version)
        except (CanonicalPayloadError, ValueError) as exc:
            raise InvalidDurableCommand("Command cannot be canonically fingerprinted") from exc

    def _checkpoint(self, _stage: str) -> None:
        """Fault-injection seam used only by transaction-atomicity tests."""
