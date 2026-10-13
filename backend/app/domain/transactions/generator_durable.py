"""Narrow durable infrastructure for the future protected Generator command.

This module intentionally contains no HTTP, provider, or real GeneratedDraft
persistence integration.  B.2D3 freezes the command identity and supplies the
transaction/replay coordinator that B.2D4 will later compose with real Generator
business effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hmac
import json
import math
import re
import unicodedata
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.models import MutationIdempotency, Plan, PlanItem
from ..idempotency import (
    CanonicalPayloadError,
    RequestHmac,
    canonical_payload_snapshot,
    canonical_request_hmac,
)
from .durable import (
    CommandResponse,
    DurableCommandError,
    FingerprintVersionMismatch,
    HmacKeyring,
    IdempotencyKeyReused,
    IdempotencyRecordExpired,
    InvalidDurableCommand,
    InvalidIdempotencyKey,
    OutcomeContract,
    OutcomeContractRegistry,
    PlanRevisionOverflow,
    UnknownHmacKeyVersion,
    UnsafeDurableOutcome,
    _IDEMPOTENCY_KEY,
    _MAX_PLAN_REVISION,
    _NoOpMutationGuard,
    _decode_stored_payload,
    _encode_response,
    _is_expired,
    _require_supported_fingerprint_version,
)
from .kernel import CredentialProof, PreliminaryActorScope, SafeCommandResult
from .trip_kernel import LockedTripContext, TripTransactionKernel

if TYPE_CHECKING:
    from ..plans.generator_lifecycle import PreparedGeneration


GENERATOR_OPERATION_CODE = "plan.generate.v1"
GENERATOR_RESOURCE_KIND = "generator_command"
GENERATOR_CONTRACT_V1 = "initial_or_empty_plan.v1"
GENERATOR_COMMAND_NAMESPACE_V1 = uuid.UUID("16bc0061-4ecf-56d5-99a5-f073dc64768b")
GENERATOR_FIRST_PLAN_NAMESPACE_V1 = uuid.UUID("550665b9-3451-5dbf-8f40-466f85e1a214")
_LEDGER_SCOPE = "plan"
_PLAN_ID = re.compile(r"^[0-9a-f]{32}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_BLOCKED_CODES = frozenset(
    {
        "DESTINATION_NOT_FOUND",
        "NO_PLACE_CANDIDATES",
        "CONSTRAINTS_BLOCKED",
        "DATES_INVALID",
        "AVAILABILITY_BLOCKED",
    }
)
_OUTCOME_FIELDS = frozenset(
    {
        "plan_id",
        "status",
        "days",
        "blocked_reason",
        "blocked_code",
        "generated_by",
        "used_ai",
        "planner_note",
    }
)


def generator_identity_name(elements: tuple[str, str, str, str]) -> str:
    """Return the B.2D0B canonical UUID-v5 name string.

    The result is deliberately a bare, compact JSON array rather than a durable
    HMAC envelope.  Existing validation of Trip IDs, membership IDs, and
    idempotency keys remains the caller's responsibility.
    """

    if len(elements) != 4 or any(not isinstance(element, str) for element in elements):
        raise ValueError("Generator identity requires exactly four strings")
    return json.dumps(
        [unicodedata.normalize("NFC", element) for element in elements],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def derive_generator_identity(
    namespace: uuid.UUID,
    elements: tuple[str, str, str, str],
) -> str:
    """Derive a lower-case 32-hex UUID-v5 identity under a frozen namespace."""

    if not isinstance(namespace, uuid.UUID):
        raise TypeError("Generator identity namespace must be a UUID")
    name = generator_identity_name(elements)
    name.encode("utf-8", "strict")
    return uuid.uuid5(namespace, name).hex


def generation_resource_id(
    *, trip_id: str, actor_membership_id: str, idempotency_key: str
) -> str:
    """Return the frozen opaque resource ID bound into the Generator HMAC."""

    return derive_generator_identity(
        GENERATOR_COMMAND_NAMESPACE_V1,
        (GENERATOR_OPERATION_CODE, trip_id, actor_membership_id, idempotency_key),
    )


def deterministic_first_plan_id(
    *, trip_id: str, actor_membership_id: str, idempotency_key: str
) -> str:
    """Return the deterministic candidate Plan ID for a zero-Plan command."""

    return derive_generator_identity(
        GENERATOR_FIRST_PLAN_NAMESPACE_V1,
        (GENERATOR_OPERATION_CODE, trip_id, actor_membership_id, idempotency_key),
    )


@dataclass(frozen=True)
class GeneratorIdempotentCommand:
    """The no-body immutable identity for one ``plan.generate.v1`` command."""

    idempotency_key: str
    fingerprint_version: int
    expires_at: datetime
    generation_contract: str = GENERATOR_CONTRACT_V1

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(
            self.idempotency_key
        ):
            raise InvalidIdempotencyKey("Idempotency key has an invalid representation")
        if isinstance(self.fingerprint_version, bool) or not isinstance(
            self.fingerprint_version, int
        ) or self.fingerprint_version < 1:
            raise InvalidDurableCommand("Fingerprint version must be a positive integer")
        if self.expires_at.tzinfo is None or self.expires_at <= datetime.now(timezone.utc):
            raise InvalidDurableCommand("Ledger expiry must be timezone-aware and in the future")
        if self.generation_contract != GENERATOR_CONTRACT_V1:
            raise InvalidDurableCommand("Generator command contract is not supported")


@dataclass(frozen=True)
class GeneratorDurableCommandResult(SafeCommandResult):
    """Detached, validated result for a first execution or historical replay."""

    status: int
    payload: dict[str, object]
    plan_revision: int
    replayed: bool


@dataclass(frozen=True)
class GeneratorReplayPreflightResult(SafeCommandResult):
    """Either a historical result or an explicit unreserved new-command decision."""

    result: GeneratorDurableCommandResult | None

    @property
    def is_new(self) -> bool:
        return self.result is None


class StaleGeneratorPreconditions(DurableCommandError):
    """Captured Prepare facts no longer describe the locked aggregate."""


@dataclass(frozen=True)
class GeneratorExecutionPreconditions:
    """Detached B.2D1 facts required only for a new final execution.

    The planning-input digest is deliberately not part of the durable request
    HMAC. B.2D4 supplies the domain-specific locked projection checker through
    ``validate_preconditions`` rather than duplicating B.2D1's projection here.
    """

    trip_planning_facts_revision: int
    plan_id: str | None
    plan_revision: int | None
    plan_status: str | None
    plan_currency: str | None
    plan_needs_refresh: bool | None
    plan_item_count: int
    planning_input_fingerprint: str
    plan_blocked_reason: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.trip_planning_facts_revision, bool)
            or not isinstance(self.trip_planning_facts_revision, int)
            or self.trip_planning_facts_revision < 0
        ):
            raise InvalidDurableCommand("Captured Trip facts revision is invalid")
        if (
            isinstance(self.plan_item_count, bool)
            or not isinstance(self.plan_item_count, int)
            or self.plan_item_count < 0
        ):
            raise InvalidDurableCommand("Captured Plan item count is invalid")
        if not isinstance(self.planning_input_fingerprint, str) or not _FINGERPRINT.fullmatch(
            self.planning_input_fingerprint
        ):
            raise InvalidDurableCommand("Captured planning input fingerprint is invalid")
        missing_plan = self.plan_id is None
        if missing_plan:
            if any(
                value is not None
                for value in (
                    self.plan_revision,
                    self.plan_status,
                    self.plan_currency,
                    self.plan_needs_refresh,
                    self.plan_blocked_reason,
                )
            ) or self.plan_item_count != 0:
                raise InvalidDurableCommand("Zero-Plan preconditions must not contain Plan facts")
            return
        if not _PLAN_ID.fullmatch(self.plan_id):
            raise InvalidDurableCommand("Captured Plan identity is invalid")
        if (
            isinstance(self.plan_revision, bool)
            or not isinstance(self.plan_revision, int)
            or self.plan_revision < 0
            or not isinstance(self.plan_status, str)
            or not self.plan_status
            or not isinstance(self.plan_currency, str)
            or not self.plan_currency
            or not isinstance(self.plan_needs_refresh, bool)
            or (self.plan_blocked_reason is not None and not isinstance(self.plan_blocked_reason, str))
        ):
            raise InvalidDurableCommand("Captured Plan facts are invalid")

    @classmethod
    def from_prepared(cls, prepared: "PreparedGeneration") -> "GeneratorExecutionPreconditions":
        """Adapt B.2D1's immutable DTO without copying its planning projection."""

        plan = prepared.plan
        return cls(
            trip_planning_facts_revision=prepared.trip.planning_facts_revision,
            plan_id=None if plan is None else plan.id,
            plan_revision=None if plan is None else plan.revision,
            plan_status=None if plan is None else plan.status,
            plan_currency=None if plan is None else plan.currency,
            plan_needs_refresh=None if plan is None else plan.needs_refresh,
            plan_item_count=0 if plan is None else plan.item_count,
            planning_input_fingerprint=prepared.planning_input_fingerprint,
            plan_blocked_reason=None if plan is None else plan.blocked_reason,
        )


@dataclass(frozen=True)
class GeneratorMutationContext:
    """The active, narrowly scoped Plan mutation target for B.2D3 tests."""

    trip_id: str
    actor_membership_id: str
    plan: Plan
    created_plan: bool


PreconditionValidator = Callable[[Session, LockedTripContext, GeneratorExecutionPreconditions], None]
SyntheticMutation = Callable[[GeneratorMutationContext], CommandResponse]


def generator_outcome_contract() -> OutcomeContract:
    """Return the trusted v1 Generator response representation policy."""

    return OutcomeContract(
        GENERATOR_OPERATION_CODE,
        1,
        _OUTCOME_FIELDS,
        payload_validator=_validate_generator_outcome,
    )


class GeneratorDurableCoordinator:
    """Generator-specific composition of the frozen Trip-root lock protocol.

    B.2D3 intentionally owns only the new operation. It neither changes the
    existing Plan/Trip durable envelopes nor exposes an HTTP or provider seam.
    """

    def __init__(
        self,
        kernel: TripTransactionKernel[object],
        keyring: HmacKeyring,
        outcome_contracts: OutcomeContractRegistry,
    ) -> None:
        self._kernel = kernel
        self._keyring = keyring
        self._outcome_contracts = outcome_contracts

    def preflight(
        self,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        command: GeneratorIdempotentCommand,
    ) -> GeneratorReplayPreflightResult:
        """Return a matching historical result before Prepare or external work.

        A missing Ledger row does not reserve the key. The caller must still use
        ``execute`` for the final locked recheck and atomic completion.
        """

        _require_supported_fingerprint_version(command.fingerprint_version)

        def run(session: Session, context: LockedTripContext) -> GeneratorReplayPreflightResult:
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
            if ledger is None:
                return GeneratorReplayPreflightResult(result=None)
            return GeneratorReplayPreflightResult(
                result=self._replay(ledger=ledger, context=context, command=command)
            )

        return self._kernel._run_locked_command(
            scope=scope,
            credential=credential,
            required_role="organizer",
            operation=run,
        )

    def execute(
        self,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        command: GeneratorIdempotentCommand,
        preconditions: GeneratorExecutionPreconditions,
        validate_preconditions: PreconditionValidator,
        synthetic_mutation: SyntheticMutation,
    ) -> GeneratorDurableCommandResult:
        """Recheck Ledger then atomically apply the reviewed Generator mutation.

        B.2D3 established the narrow callback boundary with synthetic contract
        tests. B.2D4 composes its real detached-draft persistence adapter here;
        neither caller type owns the outer transaction, replay protocol, or
        durable identity.
        """

        if not callable(validate_preconditions) or not callable(synthetic_mutation):
            raise TypeError("Generator coordinator callbacks must be callable")
        _require_supported_fingerprint_version(command.fingerprint_version)

        def run(session: Session, context: LockedTripContext) -> GeneratorDurableCommandResult:
            ledger = self._locked_ledger(session, context, command)
            if ledger is not None:
                return self._replay(ledger=ledger, context=context, command=command)

            outcome_contract = self._outcome_contracts.current_for(GENERATOR_OPERATION_CODE)
            if outcome_contract is None or outcome_contract.operation_code != GENERATOR_OPERATION_CODE:
                raise UnsupportedGeneratorOutcome("Generator has no trusted current outcome contract")

            self._validate_captured_root_state(session, context, preconditions)
            self._run_precondition_validator(
                session, context, preconditions, validate_preconditions
            )
            request_hmac = self._fingerprint(
                command=command,
                context=context,
                key=self._keyring.active_key(),
                fingerprint_version=command.fingerprint_version,
            )

            callback_savepoint = session.begin_nested()
            no_op_guard = _NoOpMutationGuard(session)
            plan = context.plan
            created_plan = plan is None
            try:
                if plan is None:
                    plan = Plan(
                        id=deterministic_first_plan_id(
                            trip_id=context.trip.id,
                            actor_membership_id=context.actor.membership_id,
                            idempotency_key=command.idempotency_key,
                        ),
                        trip_id=context.trip.id,
                        revision=0,
                        currency=context.trip.currency,
                    )
                    session.add(plan)
                    self._checkpoint("after_plan_created")
                response = synthetic_mutation(
                    GeneratorMutationContext(
                        trip_id=context.trip.id,
                        actor_membership_id=context.actor.membership_id,
                        plan=plan,
                        created_plan=created_plan,
                    )
                )
                payload = _encode_response(
                    response,
                    allowed_top_level_fields=outcome_contract.allowed_response_fields,
                    payload_validator=outcome_contract.payload_validator,
                )
                self._checkpoint("after_safe_outcome")
                if payload["plan_id"] != plan.id:
                    raise UnsafeDurableOutcome("Generator outcome Plan identity does not match mutation target")
                if response.state_changed:
                    callback_savepoint.commit()
                else:
                    no_op_guard.require_clean()
                    callback_savepoint.rollback()
            except BaseException:
                if callback_savepoint.is_active:
                    callback_savepoint.rollback()
                raise
            finally:
                no_op_guard.close()

            self._checkpoint("after_domain_effect")
            if response.state_changed:
                if plan.revision >= _MAX_PLAN_REVISION:
                    raise PlanRevisionOverflow("Plan revision cannot be incremented safely")
                plan.revision += 1
            elif created_plan:
                # A no-op cannot retain a Plan-scope outcome for a Plan that was
                # absent before this final transaction. The guard should already
                # reject it; this explicit branch remains fail-closed.
                raise UnsafeDurableOutcome("Zero-Plan command cannot complete as a no-op")
            resulting_revision = plan.revision
            self._checkpoint("after_revision_increment")

            now = datetime.now(timezone.utc)
            session.add(
                MutationIdempotency(
                    trip_id=context.trip.id,
                    actor_membership_id=context.actor.membership_id,
                    idempotency_key=command.idempotency_key,
                    operation_code=GENERATOR_OPERATION_CODE,
                    resource_kind=GENERATOR_RESOURCE_KIND,
                    resource_id=generation_resource_id(
                        trip_id=context.trip.id,
                        actor_membership_id=context.actor.membership_id,
                        idempotency_key=command.idempotency_key,
                    ),
                    fingerprint_version=request_hmac.fingerprint_version,
                    request_hmac=request_hmac.digest,
                    hmac_key_version=request_hmac.hmac_key_version,
                    outcome_contract_version=outcome_contract.version,
                    response_status=response.status,
                    outcome_json=payload,
                    aggregate_scope=_LEDGER_SCOPE,
                    resulting_plan_revision=resulting_revision,
                    resulting_trip_planning_facts_revision=None,
                    completed_at=now,
                    expires_at=command.expires_at,
                )
            )
            self._checkpoint("after_ledger_insert")
            session.flush()
            return GeneratorDurableCommandResult(
                response.status, payload, resulting_revision, False
            )

        return self._kernel._run_locked_command(
            scope=scope,
            credential=credential,
            required_role="organizer",
            lock_all_trip_memberships=True,
            operation=run,
        )

    def _locked_ledger(
        self,
        session: Session,
        context: LockedTripContext,
        command: GeneratorIdempotentCommand,
    ) -> MutationIdempotency | None:
        return session.scalar(
            select(MutationIdempotency)
            .where(
                MutationIdempotency.trip_id == context.trip.id,
                MutationIdempotency.actor_membership_id == context.actor.membership_id,
                MutationIdempotency.idempotency_key == command.idempotency_key,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    def _validate_captured_root_state(
        self,
        session: Session,
        context: LockedTripContext,
        captured: GeneratorExecutionPreconditions,
    ) -> None:
        if context.trip.planning_facts_revision != captured.trip_planning_facts_revision:
            raise StaleGeneratorPreconditions("Trip planning facts changed after Prepare")
        plan = context.plan
        if captured.plan_id is None:
            if plan is not None:
                raise StaleGeneratorPreconditions("A Plan appeared after zero-Plan Prepare")
            return
        if plan is None or plan.id != captured.plan_id:
            raise StaleGeneratorPreconditions("Authoritative Plan changed after Prepare")
        if (
            plan.revision != captured.plan_revision
            or plan.status != captured.plan_status
            or plan.currency != captured.plan_currency
            or plan.needs_refresh != captured.plan_needs_refresh
            or plan.blocked_reason != captured.plan_blocked_reason
        ):
            raise StaleGeneratorPreconditions("Plan lifecycle changed after Prepare")
        item_count = session.scalar(
            select(func.count()).select_from(PlanItem).where(PlanItem.plan_id == plan.id)
        ) or 0
        if item_count != captured.plan_item_count:
            raise StaleGeneratorPreconditions("Plan item lifecycle changed after Prepare")

    def _run_precondition_validator(
        self,
        session: Session,
        context: LockedTripContext,
        captured: GeneratorExecutionPreconditions,
        validator: PreconditionValidator,
    ) -> None:
        """Run B.2D4's locked projection comparison without allowing writes."""

        savepoint = session.begin_nested()
        guard = _NoOpMutationGuard(session)
        try:
            if validator(session, context, captured) is not None:
                raise InvalidDurableCommand("Generator precondition validator must return None")
            guard.require_clean()
            savepoint.rollback()
        except BaseException:
            if savepoint.is_active:
                savepoint.rollback()
            raise
        finally:
            guard.close()

    def _replay(
        self,
        *,
        ledger: MutationIdempotency,
        context: LockedTripContext,
        command: GeneratorIdempotentCommand,
    ) -> GeneratorDurableCommandResult:
        if ledger.aggregate_scope != _LEDGER_SCOPE:
            raise IdempotencyKeyReused("Idempotency key belongs to another aggregate scope")
        expected_resource_id = generation_resource_id(
            trip_id=context.trip.id,
            actor_membership_id=context.actor.membership_id,
            idempotency_key=command.idempotency_key,
        )
        if (
            ledger.operation_code != GENERATOR_OPERATION_CODE
            or ledger.resource_kind != GENERATOR_RESOURCE_KIND
            or ledger.resource_id != expected_resource_id
        ):
            raise IdempotencyKeyReused("Idempotency key belongs to another command")
        _require_supported_fingerprint_version(ledger.fingerprint_version)
        if command.fingerprint_version != ledger.fingerprint_version:
            raise FingerprintVersionMismatch("Incoming and stored fingerprint versions differ")
        verification_key = self._keyring.key_for_version(ledger.hmac_key_version)
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
        if _is_expired(ledger.expires_at):
            raise IdempotencyRecordExpired("Retained idempotency record has expired")
        if (
            ledger.resulting_plan_revision is None
            or ledger.resulting_trip_planning_facts_revision is not None
            or isinstance(ledger.resulting_plan_revision, bool)
            or not isinstance(ledger.resulting_plan_revision, int)
            or ledger.resulting_plan_revision < 0
        ):
            raise UnsupportedGeneratorOutcome("Stored outcome has no valid Plan result revision")
        if (
            isinstance(ledger.response_status, bool)
            or not isinstance(ledger.response_status, int)
            or not 200 <= ledger.response_status < 300
        ):
            raise UnsupportedGeneratorOutcome("Stored outcome status is unsupported")
        historical = self._outcome_contracts.for_version(
            GENERATOR_OPERATION_CODE, ledger.outcome_contract_version
        )
        current = self._outcome_contracts.current_for(GENERATOR_OPERATION_CODE)
        if historical is None or historical.operation_code != GENERATOR_OPERATION_CODE:
            raise UnsupportedGeneratorOutcome("Stored outcome contract is unsupported")
        if current is None or current.operation_code != GENERATOR_OPERATION_CODE:
            raise UnsupportedGeneratorOutcome("Current outcome contract is unsupported")
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
        return GeneratorDurableCommandResult(
            ledger.response_status, payload, ledger.resulting_plan_revision, True
        )

    def _fingerprint(
        self,
        *,
        command: GeneratorIdempotentCommand,
        context: LockedTripContext,
        key,
        fingerprint_version: int,
    ) -> RequestHmac:
        try:
            payload = canonical_payload_snapshot(
                {
                    "aggregate_scope": _LEDGER_SCOPE,
                    "trip_id": context.trip.id,
                    "actor_membership_id": context.actor.membership_id,
                    "resource": {
                        "kind": GENERATOR_RESOURCE_KIND,
                        "id": generation_resource_id(
                            trip_id=context.trip.id,
                            actor_membership_id=context.actor.membership_id,
                            idempotency_key=command.idempotency_key,
                        ),
                    },
                    "command": {"generation_contract": command.generation_contract},
                }
            )
            return canonical_request_hmac(
                GENERATOR_OPERATION_CODE,
                payload,
                key=key.key,
                fingerprint_version=fingerprint_version,
                hmac_key_version=key.version,
            )
        except (CanonicalPayloadError, ValueError) as exc:
            raise InvalidDurableCommand("Generator command cannot be canonically fingerprinted") from exc

    def _checkpoint(self, _stage: str) -> None:
        """Fault-injection seam for B.2D3 atomicity tests."""


class UnsupportedGeneratorOutcome(UnsafeDurableOutcome):
    """A stored or new outcome cannot satisfy the frozen Generator contract."""


def _validate_generator_outcome(payload: Mapping[str, object]) -> Mapping[str, object]:
    if set(payload) != _OUTCOME_FIELDS:
        raise ValueError("Generator outcome must have exactly the approved top-level fields")
    plan_id = _required_string(payload["plan_id"], field="plan_id", maximum=32)
    if not _PLAN_ID.fullmatch(plan_id):
        raise ValueError("Generator outcome Plan identity is invalid")
    status = payload["status"]
    if status not in {"active", "blocked"}:
        raise ValueError("Generator outcome status is invalid")
    generated_by = payload["generated_by"]
    if generated_by not in {"planner", "rules", "mixed"}:
        raise ValueError("Generator outcome provenance is invalid")
    if not isinstance(payload["used_ai"], bool):
        raise ValueError("Generator outcome used_ai must be boolean")
    blocked_reason = _nullable_nfc_string(payload["blocked_reason"], field="blocked_reason", maximum=2048)
    blocked_code = payload["blocked_code"]
    if blocked_code is not None and blocked_code not in _BLOCKED_CODES:
        raise ValueError("Generator outcome blocked_code is invalid")
    if status == "active" and (blocked_reason is not None or blocked_code is not None):
        raise ValueError("Active Generator outcome cannot carry blocked details")
    if status == "blocked" and (blocked_reason is None or blocked_code is None):
        raise ValueError("Blocked Generator outcome requires blocked details")
    days = _validate_days(payload["days"])
    notes = _validate_planner_notes(payload["planner_note"], {day["day_index"] for day in days})
    return {
        "plan_id": plan_id,
        "status": status,
        "days": days,
        "blocked_reason": blocked_reason,
        "blocked_code": blocked_code,
        "generated_by": generated_by,
        "used_ai": payload["used_ai"],
        "planner_note": notes,
    }


def _validate_days(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Generator outcome days must be an array")
    result: list[dict[str, object]] = []
    prior_index, prior_date = 0, date.min
    for raw_day in value:
        if not isinstance(raw_day, Mapping) or set(raw_day) != {"day_index", "day_date", "items"}:
            raise ValueError("Generator outcome day is invalid")
        day_index = raw_day["day_index"]
        if isinstance(day_index, bool) or not isinstance(day_index, int) or day_index <= prior_index:
            raise ValueError("Generator day indexes must be strictly increasing positive integers")
        day_date = _parse_date(raw_day["day_date"], field="day_date")
        if day_date < prior_date:
            raise ValueError("Generator days must be ordered by date")
        items = _validate_day_items(raw_day["items"], day_index, day_date)
        result.append({"day_index": day_index, "day_date": day_date.isoformat(), "items": items})
        prior_index, prior_date = day_index, day_date
    return result


def _validate_day_items(value: object, day_index: int, day_date: date) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Generator day items must be an array")
    result: list[dict[str, object]] = []
    prior_hour = -1.0
    expected = {
        "id", "day_index", "day_date", "start_hour", "title", "local_title", "place",
        "price_per_person", "source", "lat", "lng", "photo_url", "tags",
    }
    for raw_item in value:
        if not isinstance(raw_item, Mapping) or set(raw_item) != expected:
            raise ValueError("Generator outcome item is invalid")
        if raw_item["day_index"] != day_index or _parse_date(raw_item["day_date"], field="item day_date") != day_date:
            raise ValueError("Generator outcome item does not match its day")
        start_hour = _finite_number(raw_item["start_hour"], field="start_hour")
        if not 0 <= start_hour <= 24 or start_hour < prior_hour:
            raise ValueError("Generator items must have ordered valid start hours")
        lat, lng = raw_item["lat"], raw_item["lng"]
        if (lat is None) != (lng is None):
            raise ValueError("Generator coordinates must both be null or present")
        if lat is not None:
            latitude, longitude = _finite_number(lat, field="lat"), _finite_number(lng, field="lng")
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("Generator coordinates are outside geographic bounds")
        else:
            latitude = longitude = None
        price = raw_item["price_per_person"]
        if price is not None:
            price = _finite_number(price, field="price_per_person")
            if price < 0:
                raise ValueError("Generator price cannot be negative")
        tags = raw_item["tags"]
        if not isinstance(tags, list) or len(tags) > 64:
            raise ValueError("Generator tags are invalid")
        normalized_tags = [_required_string(tag, field="tag", maximum=128) for tag in tags]
        result.append(
            {
                "id": _required_string(raw_item["id"], field="item id", maximum=32),
                "day_index": day_index,
                "day_date": day_date.isoformat(),
                "start_hour": start_hour,
                "title": _required_string(raw_item["title"], field="title", maximum=300),
                "local_title": _nullable_nfc_string(raw_item["local_title"], field="local_title", maximum=300),
                "place": _required_string(raw_item["place"], field="place", maximum=500),
                "price_per_person": price,
                "source": _required_string(raw_item["source"], field="source", maximum=20),
                "lat": latitude,
                "lng": longitude,
                "photo_url": _nullable_nfc_string(raw_item["photo_url"], field="photo_url", maximum=1000),
                "tags": normalized_tags,
            }
        )
        prior_hour = start_hour
    return result


def _validate_planner_notes(value: object, day_indexes: set[int]) -> list[dict[str, object]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Generator planner_note must be null or an array")
    result: list[dict[str, object]] = []
    for raw_note in value:
        if not isinstance(raw_note, Mapping) or set(raw_note) != {"day_index", "source", "note", "used_ai"}:
            raise ValueError("Generator planner note is invalid")
        day_index = raw_note["day_index"]
        if isinstance(day_index, bool) or not isinstance(day_index, int) or day_index not in day_indexes:
            raise ValueError("Generator planner note references an invalid day")
        if raw_note["source"] not in {"planner", "rules"} or not isinstance(raw_note["used_ai"], bool):
            raise ValueError("Generator planner note provenance is invalid")
        result.append(
            {
                "day_index": day_index,
                "source": raw_note["source"],
                "note": _required_string(raw_note["note"], field="planner note", maximum=4096),
                "used_ai": raw_note["used_ai"],
            }
        )
    return result


def _required_string(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"Generator outcome {field} is invalid")
    return value


def _nullable_nfc_string(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_string(value, field=field, maximum=maximum)


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Generator outcome {field} must be a finite number")
    return float(value)


def _parse_date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"Generator outcome {field} must be an ISO date")
    return date.fromisoformat(value)
