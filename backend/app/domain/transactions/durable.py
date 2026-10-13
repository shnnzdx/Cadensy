"""Internal durable idempotency protocol for existing-Plan mutations only.

This module intentionally has no HTTP, provider, or route integration.  It extends
the B.2A kernel's one owned Session/transaction: Trip -> credential -> membership
-> Plan -> ledger -> local mutation -> revision -> completed outcome.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hmac
import math
import re
from types import MappingProxyType
from typing import Protocol

from sqlalchemy import event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ...db.models import Base, MutationIdempotency
from ..idempotency import (
    CanonicalPayloadError,
    RequestHmac,
    canonical_payload_snapshot,
    canonical_request_hmac,
)
from .kernel import (
    CredentialProof,
    LockedPlanContext,
    PlanTransactionKernel,
    PreliminaryActorScope,
    SafeCommandResult,
    TransactionKernelError,
)
from .trip_kernel import LockedTripContext


_MAX_PLAN_REVISION = 9_223_372_036_854_775_807
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SUPPORTED_FINGERPRINT_VERSIONS = frozenset({1})


class DurableCommandError(TransactionKernelError):
    """Base class for B.2B's internal command/replay outcomes."""


class InvalidIdempotencyKey(DurableCommandError):
    pass


class InvalidDurableCommand(DurableCommandError):
    pass


class IdempotencyKeyReused(DurableCommandError):
    """A retained key belongs to a different semantic command."""


class StalePlanRevision(DurableCommandError):
    def __init__(self, *, expected: int, current: int):
        self.expected = expected
        self.current = current
        super().__init__("Expected Plan revision is stale")


class PlanRevisionOverflow(DurableCommandError):
    pass


class IdempotencyRecordExpired(DurableCommandError):
    pass


class UnknownHmacKeyVersion(DurableCommandError):
    pass


class UnsupportedOutcomeContract(DurableCommandError):
    pass


class UnsupportedFingerprintVersion(DurableCommandError):
    pass


class FingerprintVersionMismatch(DurableCommandError):
    pass


class UnsafeDurableOutcome(DurableCommandError):
    pass


class UnsafeNoOpMutation(UnsafeDurableOutcome):
    """A callback changed persistence state while claiming a no-op."""

    pass


class UnsupportedNoOp(DurableCommandError):
    pass


@dataclass(frozen=True)
class HmacSigningKey:
    """An injected in-memory signing/verification key; never persisted."""

    version: str
    key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version or len(self.version) > 32:
            raise ValueError("HMAC key version must be a non-empty string of at most 32 characters")
        if not isinstance(self.key, bytes) or not self.key:
            raise ValueError("HMAC key material must be non-empty bytes")


class HmacKeyring(Protocol):
    """Narrow injection point for active signing and retained replay verification."""

    def active_key(self) -> HmacSigningKey: ...

    def key_for_version(self, version: str) -> HmacSigningKey | None: ...


@dataclass(frozen=True)
class OutcomeContract:
    """Trusted server-owned representation policy for one operation version."""

    operation_code: str
    version: int
    allowed_response_fields: frozenset[str]
    # An optional, server-owned operation codec.  The generic envelope checks
    # JSON safety and top-level policy; writers that expose nested structures
    # supply this stricter representation policy for both first writes and
    # replay.  Existing Plan contracts deliberately retain their current shape.
    payload_validator: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None

    def __post_init__(self) -> None:
        _bounded_identifier(self.operation_code, field="operation_code", maximum=80)
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Outcome contract version must be a positive integer")
        if not isinstance(self.allowed_response_fields, frozenset) or any(
            not isinstance(field, str) or not field or len(field) > 128
            for field in self.allowed_response_fields
        ):
            raise ValueError("Outcome contract fields must be a frozen set of bounded strings")
        if self.payload_validator is not None and not callable(self.payload_validator):
            raise ValueError("Outcome contract validator must be callable when supplied")


class OutcomeContractRegistry(Protocol):
    """Composition-root policy source; command input cannot choose its contract."""

    def current_for(self, operation_code: str) -> OutcomeContract | None: ...

    def for_version(self, operation_code: str, version: int) -> OutcomeContract | None: ...


class StaticOutcomeContractRegistry:
    """Immutable in-process outcome policy registry for the dormant B.2B kernel."""

    def __init__(self, contracts: Iterable[OutcomeContract]):
        indexed: dict[tuple[str, int], OutcomeContract] = {}
        current: dict[str, OutcomeContract] = {}
        for contract in contracts:
            if not isinstance(contract, OutcomeContract):
                raise TypeError("Outcome registries accept only OutcomeContract entries")
            key = (contract.operation_code, contract.version)
            if key in indexed:
                raise ValueError("Outcome contracts must not duplicate an operation/version")
            indexed[key] = contract
            existing = current.get(contract.operation_code)
            if existing is None or contract.version > existing.version:
                current[contract.operation_code] = contract
        self._indexed = MappingProxyType(indexed)
        self._current = MappingProxyType(current)

    def current_for(self, operation_code: str) -> OutcomeContract | None:
        return self._current.get(operation_code)

    def for_version(self, operation_code: str, version: int) -> OutcomeContract | None:
        return self._indexed.get((operation_code, version))


@dataclass(frozen=True)
class IdempotentCommand:
    """Server-resolved, validated input for one existing-Plan command."""

    operation_code: str
    resource_kind: str
    resource_id: str
    idempotency_key: str
    expected_plan_revision: int
    command_fields: Mapping[str, object]
    fingerprint_version: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _bounded_identifier(self.operation_code, field="operation_code", maximum=80)
        _bounded_identifier(self.resource_kind, field="resource_kind", maximum=40)
        _bounded_identifier(self.resource_id, field="resource_id", maximum=64)
        if not isinstance(self.idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(
            self.idempotency_key
        ):
            raise InvalidIdempotencyKey("Idempotency key has an invalid representation")
        if isinstance(self.expected_plan_revision, bool) or not isinstance(
            self.expected_plan_revision, int
        ) or self.expected_plan_revision < 0:
            raise InvalidDurableCommand("Expected Plan revision must be a non-negative integer")
        if isinstance(self.fingerprint_version, bool) or not isinstance(
            self.fingerprint_version, int
        ) or self.fingerprint_version < 1:
            raise InvalidDurableCommand("Fingerprint version must be a positive integer")
        if not isinstance(self.command_fields, Mapping):
            raise InvalidDurableCommand("Command fields must be a mapping")
        if self.expires_at.tzinfo is None:
            raise InvalidDurableCommand("Ledger expiry must be timezone-aware")
        if self.expires_at <= datetime.now(timezone.utc):
            raise InvalidDurableCommand("Ledger expiry must be in the future")
        try:
            object.__setattr__(self, "command_fields", canonical_payload_snapshot(self.command_fields))
        except CanonicalPayloadError as exc:
            raise InvalidDurableCommand("Command fields cannot be canonically snapshotted") from exc


@dataclass(frozen=True)
class CommandResponse(SafeCommandResult):
    """The sole callback response shape eligible for completed-ledger storage."""

    status: int
    payload: Mapping[str, object]
    state_changed: bool = True


@dataclass(frozen=True)
class DurableCommandResult(SafeCommandResult):
    """Detached response returned for both first execution and durable replay."""

    status: int
    payload: dict[str, object]
    plan_revision: int
    replayed: bool


class DurableCommandKernel:
    """A same-Session ledger/revision collaborator for ``PlanTransactionKernel``.

    B.2B supports only a single, already-existing authoritative Plan and explicit
    state-changing callbacks. Plan creation, no-op outcomes, and public route
    migration remain deliberately outside this contract.
    """

    def __init__(
        self,
        kernel: PlanTransactionKernel[object],
        keyring: HmacKeyring,
        outcome_contracts: OutcomeContractRegistry,
    ):
        self._kernel = kernel
        self._keyring = keyring
        self._outcome_contracts = outcome_contracts

    def execute(
        self,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        command: IdempotentCommand,
        required_role: str | None = None,
        mutation: Callable[[LockedPlanContext, Mapping[str, object]], CommandResponse],
    ) -> DurableCommandResult:
        """Execute once or return the previously committed safe outcome.

        Current authorization/root locks are acquired by B.2A before any ledger
        lookup. The ledger comparison deliberately occurs before stale-revision
        rejection so an identical retry of a successful command can replay.
        """

        return self._execute_with_session_mutation(
            scope=scope,
            credential=credential,
            command=command,
            required_role=required_role,
            mutation=lambda _session, context, fields: mutation(context, fields),
        )

    def _execute_with_session_mutation(
        self,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        command: IdempotentCommand,
        required_role: str | None = None,
        lock_all_trip_memberships: bool = False,
        mutation: Callable[[Session, LockedPlanContext, Mapping[str, object]], CommandResponse],
    ) -> DurableCommandResult:
        """Run a reviewed in-package domain writer in the owned transaction.

        This is deliberately private: public callers retain ``execute()`` and
        cannot receive a Session. A B.2C collaborator may use the Session only
        synchronously to apply its narrow business effect; it has no commit,
        rollback, or lifecycle authority.
        """

        _require_supported_fingerprint_version(command.fingerprint_version)

        def run(session: Session, context: LockedPlanContext) -> DurableCommandResult:
            ledger = session.scalar(
                select(MutationIdempotency)
                .where(
                    MutationIdempotency.trip_id == context.trip.id,
                    MutationIdempotency.actor_membership_id == context.actor.membership_id,
                    MutationIdempotency.idempotency_key == command.idempotency_key,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if ledger is not None:
                return self._replay(ledger=ledger, context=context, command=command)

            outcome_contract = self._outcome_contracts.current_for(command.operation_code)
            if outcome_contract is None or outcome_contract.operation_code != command.operation_code:
                raise UnsupportedOutcomeContract("Operation has no trusted current outcome contract")

            if context.plan.revision != command.expected_plan_revision:
                raise StalePlanRevision(
                    expected=command.expected_plan_revision, current=context.plan.revision
                )

            signing_key = self._keyring.active_key()
            request_hmac = self._fingerprint(
                command=command,
                context=context,
                key=signing_key,
                fingerprint_version=command.fingerprint_version,
            )
            # Every reviewed domain callback runs inside a savepoint.  A callback
            # that declares no state change is never allowed to merge its work
            # into the outer durable transaction, even if it issued SQL we do
            # not recognize locally.
            callback_savepoint = session.begin_nested()
            no_op_guard = _NoOpMutationGuard(session)
            try:
                response = mutation(session, context, command.command_fields)
                payload = _encode_response(
                    response,
                    allowed_top_level_fields=outcome_contract.allowed_response_fields,
                    payload_validator=outcome_contract.payload_validator,
                )
                if not response.state_changed:
                    no_op_guard.require_clean()
                    callback_savepoint.rollback()
                else:
                    callback_savepoint.commit()
            except BaseException:
                if callback_savepoint.is_active:
                    callback_savepoint.rollback()
                raise
            finally:
                no_op_guard.close()
            self._checkpoint("after_domain_effect")

            # A successful idempotent command may prove that the requested state
            # already exists.  Retain that safe response for retry, but do not
            # fabricate a Plan change (or consume the final BIGINT value).
            if response.state_changed:
                if context.plan.revision >= _MAX_PLAN_REVISION:
                    raise PlanRevisionOverflow("Plan revision cannot be incremented safely")
                context.plan.revision += 1
            resulting_revision = context.plan.revision
            self._checkpoint("after_revision_increment")

            now = datetime.now(timezone.utc)
            session.add(
                MutationIdempotency(
                    trip_id=context.trip.id,
                    actor_membership_id=context.actor.membership_id,
                    idempotency_key=command.idempotency_key,
                    operation_code=command.operation_code,
                    resource_kind=command.resource_kind,
                    resource_id=command.resource_id,
                    fingerprint_version=request_hmac.fingerprint_version,
                    request_hmac=request_hmac.digest,
                    hmac_key_version=request_hmac.hmac_key_version,
                    outcome_contract_version=outcome_contract.version,
                    response_status=response.status,
                    outcome_json=payload,
                    aggregate_scope="plan",
                    resulting_plan_revision=resulting_revision,
                    completed_at=now,
                    expires_at=command.expires_at,
                )
            )
            self._checkpoint("after_ledger_insert")
            session.flush()
            return DurableCommandResult(
                status=response.status,
                payload=payload,
                plan_revision=resulting_revision,
                replayed=False,
            )

        return self._kernel._run_locked_command(
            scope=scope,
            credential=credential,
            required_role=required_role,
            lock_all_trip_memberships=lock_all_trip_memberships,
            operation=run,
        )

    def _replay(
        self,
        *,
        ledger: MutationIdempotency,
        context: LockedPlanContext,
        command: IdempotentCommand,
    ) -> DurableCommandResult:
        # The key namespace is global across scopes.  Do not attempt to parse a
        # Trip-scoped record with the frozen Plan HMAC or Plan-result contract.
        if ledger.aggregate_scope != "plan":
            raise IdempotencyKeyReused("Idempotency key belongs to another aggregate scope")
        verification_key = self._keyring.key_for_version(ledger.hmac_key_version)
        _require_supported_fingerprint_version(ledger.fingerprint_version)
        if command.fingerprint_version != ledger.fingerprint_version:
            raise FingerprintVersionMismatch("Incoming and stored fingerprint versions differ")
        if verification_key is None:
            raise UnknownHmacKeyVersion("Stored idempotency record cannot be verified")
        expected = self._fingerprint(
            command=command,
            context=context,
            key=verification_key,
            fingerprint_version=ledger.fingerprint_version,
        )
        if not hmac.compare_digest(ledger.request_hmac, expected.digest):
            raise IdempotencyKeyReused("Idempotency key belongs to another command")
        if ledger.operation_code != command.operation_code:
            raise IdempotencyKeyReused("Idempotency key belongs to another command")
        if _is_expired(ledger.expires_at):
            raise IdempotencyRecordExpired("Retained idempotency record has expired")
        if ledger.resulting_plan_revision is None:
            raise UnsupportedOutcomeContract("Stored outcome has no resulting Plan revision")
        if (
            isinstance(ledger.response_status, bool)
            or not isinstance(ledger.response_status, int)
            or not 200 <= ledger.response_status < 300
        ):
            raise UnsupportedOutcomeContract("Stored outcome status is unsupported")
        outcome_contract = self._outcome_contracts.for_version(
            command.operation_code, ledger.outcome_contract_version
        )
        if outcome_contract is None or outcome_contract.operation_code != command.operation_code:
            raise UnsupportedOutcomeContract("Stored outcome contract is unsupported")
        current_outcome_contract = self._outcome_contracts.current_for(command.operation_code)
        if (
            current_outcome_contract is None
            or current_outcome_contract.operation_code != command.operation_code
        ):
            raise UnsupportedOutcomeContract("Current outcome contract is unsupported")
        # Historical validation proves that the record is intelligible; current
        # validation prevents a previously allowed field from escaping after the
        # service policy has been tightened.
        payload = _decode_stored_payload(
            ledger.outcome_json,
            allowed_top_level_fields=outcome_contract.allowed_response_fields,
            payload_validator=outcome_contract.payload_validator,
        )
        payload = _decode_stored_payload(
            payload,
            allowed_top_level_fields=current_outcome_contract.allowed_response_fields,
            payload_validator=current_outcome_contract.payload_validator,
        )
        return DurableCommandResult(
            status=ledger.response_status,
            payload=payload,
            plan_revision=ledger.resulting_plan_revision,
            replayed=True,
        )

    def _fingerprint(
        self,
        *,
        command: IdempotentCommand,
        context: LockedPlanContext,
        key: HmacSigningKey,
        fingerprint_version: int,
    ) -> RequestHmac:
        try:
            return canonical_request_hmac(
                command.operation_code,
                {
                    "trip_id": context.trip.id,
                    "plan_id": context.plan.id,
                    "actor_membership_id": context.actor.membership_id,
                    "resource": {"kind": command.resource_kind, "id": command.resource_id},
                    "expected_plan_revision": command.expected_plan_revision,
                    "command": command.command_fields,
                },
                key=key.key,
                fingerprint_version=fingerprint_version,
                hmac_key_version=key.version,
            )
        except (CanonicalPayloadError, ValueError) as exc:
            raise InvalidDurableCommand("Command cannot be canonically fingerprinted") from exc

    def _checkpoint(self, _stage: str) -> None:
        """Narrow protected fault-injection seam for transaction-atomicity tests."""


def _encode_response(
    response: CommandResponse,
    *,
    allowed_top_level_fields: frozenset[str] | None,
    payload_validator: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    if not isinstance(response, CommandResponse):
        raise UnsafeDurableOutcome("Mutation callback must return CommandResponse")
    if isinstance(response.status, bool) or not isinstance(response.status, int) or not 200 <= response.status < 300:
        raise UnsafeDurableOutcome("Completed command status must be a 2xx integer")
    _require_no_dynamic_dataclass_attributes(response)
    payload = _safe_json_mapping(response.payload)
    if allowed_top_level_fields is not None and not set(payload) <= allowed_top_level_fields:
        raise UnsafeDurableOutcome("Outcome has fields outside its approved response contract")
    return _validate_outcome_payload(payload, payload_validator)


def _decode_stored_payload(
    value: object,
    *,
    allowed_top_level_fields: frozenset[str] | None,
    payload_validator: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    payload = _safe_json_mapping(value)
    if allowed_top_level_fields is not None and not set(payload) <= allowed_top_level_fields:
        raise UnsafeDurableOutcome("Stored outcome has fields outside its approved response contract")
    return _validate_outcome_payload(payload, payload_validator)


def _validate_outcome_payload(
    payload: dict[str, object],
    payload_validator: Callable[[Mapping[str, object]], Mapping[str, object]] | None,
) -> dict[str, object]:
    if payload_validator is None:
        return payload
    try:
        validated = payload_validator(payload)
    except UnsafeDurableOutcome:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsafeDurableOutcome("Outcome does not satisfy its trusted representation contract") from exc
    # A codec cannot smuggle an ORM instance or another non-JSON object through
    # merely by returning it.  Reapply the generic safety barrier afterwards.
    return _safe_json_mapping(validated)


def _safe_json_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise UnsafeDurableOutcome("Safe outcome payload must be a mapping")
    encoded = _safe_json_value(value, active_container_ids=set())
    assert isinstance(encoded, dict)
    return encoded


def _safe_json_value(value: object, *, active_container_ids: set[int]) -> object:
    if isinstance(value, (Base, LockedPlanContext, LockedTripContext, CredentialProof, Session, Connection)):
        raise UnsafeDurableOutcome("Outcome contains transaction or credential state")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsafeDurableOutcome("Outcome floats must be finite")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise UnsafeDurableOutcome("Outcome must not contain binary data")

    value_id = id(value)
    if value_id in active_container_ids:
        raise UnsafeDurableOutcome("Outcome must not contain cyclic containers")
    if isinstance(value, Mapping):
        active_container_ids.add(value_id)
        try:
            encoded: dict[str, object] = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise UnsafeDurableOutcome("Outcome mapping keys must be strings")
                encoded[key] = _safe_json_value(child, active_container_ids=active_container_ids)
            return encoded
        finally:
            active_container_ids.remove(value_id)
    if isinstance(value, (list, tuple)):
        active_container_ids.add(value_id)
        try:
            return [_safe_json_value(child, active_container_ids=active_container_ids) for child in value]
        finally:
            active_container_ids.remove(value_id)
    raise UnsafeDurableOutcome("Outcome contains an unsupported object")


def _require_no_dynamic_dataclass_attributes(value: object) -> None:
    declared = {descriptor.name for descriptor in fields(value)}
    dynamic = set(vars(value)) - declared
    if dynamic:
        raise UnsafeDurableOutcome("Outcome DTO has undeclared dynamic attributes")


def _bounded_identifier(value: str, *, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise InvalidDurableCommand(f"{field} must be a non-empty string of at most {maximum} characters")


def _require_supported_fingerprint_version(version: int) -> None:
    if version not in _SUPPORTED_FINGERPRINT_VERSIONS:
        raise UnsupportedFingerprintVersion("Fingerprint format is not implemented")


def _is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


class _NoOpMutationGuard:
    """Detect callback writes before accepting a ``state_changed=False`` result.

    ``Session.new``/``dirty``/``deleted`` catches unflushed ORM work only.  The
    callback is also allowed to call ``flush()``, which clears those collections,
    so this narrow per-callback guard records ORM flushes and PostgreSQL's actual
    server command tags on this transaction's connection.  Command tags cover
    commented and CTE-form DML without guessing from SQL text.  It deliberately
    stops before the durable kernel writes its own ledger row.
    """

    def __init__(self, session: Session):
        self._session = session
        self._connection = session.connection()
        self._engine = self._connection.engine
        self._driver_connection = self._connection.connection.driver_connection
        self._saw_flush = False
        self._saw_dml = False
        self._saw_ambiguous_sql = False
        self._measuring_xact_writes = False
        # Completion tags are fast evidence; this transaction-local PostgreSQL
        # counter is the fail-closed backstop for writable CTEs reported as
        # SELECT and for driver paths that do not expose a completion tag.
        self._xact_writes_before = self._xact_write_count()
        event.listen(session, "before_flush", self._before_flush)
        # Listen at the Engine dispatch point: an ORM instance can obtain a
        # connection facade different from the one used by a raw Session SQL
        # call, while both share this physical transaction connection.
        event.listen(self._engine, "after_cursor_execute", self._after_cursor_execute)

    def _before_flush(self, _session: Session, _flush_context: object, _instances: object) -> None:
        self._saw_flush = True

    def _after_cursor_execute(
        self,
        connection: Connection,
        cursor: object,
        _statement: str,
        _parameters: object,
        execution_context: object,
        _executemany: object,
    ) -> None:
        if self._measuring_xact_writes:
            return
        if connection.connection.driver_connection is not self._driver_connection:
            return
        # psycopg exposes PostgreSQL's protocol-level completion tag (for
        # example ``UPDATE 1``), independent of comments or a WITH clause in
        # the submitted SQL.  The enclosing savepoint remains the final
        # fail-closed backstop for a write-capable statement with a non-DML tag.
        # psycopg 3 exposes the protocol completion tag through ``pgresult``;
        # ``statusmessage`` is a compatibility fallback and is empty for some
        # cursor/result combinations.  Neither branch inspects SQL text.
        pgresult = getattr(cursor, "pgresult", None)
        raw_tag = getattr(pgresult, "command_status", b"")
        if isinstance(raw_tag, bytes):
            raw_tag = raw_tag.decode("ascii", errors="replace")
        command_tag_parts = str(raw_tag or getattr(cursor, "statusmessage", "")).split(None, 1)
        command_tag = command_tag_parts[0].upper() if command_tag_parts else ""
        if command_tag in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
            self._saw_dml = True
            return
        # A raw TextClause does not expose reliable SQLAlchemy DML metadata;
        # PostgreSQL can report a writable CTE as ``SELECT``.  A false no-op
        # callback therefore fails closed for textual SQL rather than trusting
        # a lexical prefix or allowing an ambiguous write through the guard.
        compiled = getattr(execution_context, "compiled", None)
        statement_object = getattr(compiled, "statement", None)
        if statement_object is None or bool(getattr(statement_object, "is_text", False)):
            self._saw_ambiguous_sql = True

    def require_clean(self) -> None:
        if (
            self._saw_flush
            or self._saw_dml
            or self._saw_ambiguous_sql
            or self._xact_write_count() != self._xact_writes_before
            or self._session.new
            or self._session.dirty
            or self._session.deleted
        ):
            raise UnsafeNoOpMutation(
                "A state_changed=False callback must not stage or flush domain mutations"
            )

    def _xact_write_count(self) -> int:
        self._measuring_xact_writes = True
        try:
            value = self._connection.execute(
                text(
                    "SELECT COALESCE(SUM(n_tup_ins + n_tup_upd + n_tup_del), 0) "
                    "FROM pg_stat_xact_user_tables"
                )
            ).scalar_one()
            return int(value)
        finally:
            self._measuring_xact_writes = False

    def close(self) -> None:
        event.remove(self._session, "before_flush", self._before_flush)
        event.remove(self._engine, "after_cursor_execute", self._after_cursor_execute)
