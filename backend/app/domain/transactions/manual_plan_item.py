"""Feature-gated protected adapter for Cadensy's real manual PlanItem writer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..plans.manual_items import (
    ManualPlanItemCommand,
    ManualPlanMismatch,
    add_manual_plan_item,
    manual_plan_item_out,
)
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


MANUAL_PLAN_ITEM_ADD_OPERATION = "plan.item.manual-add.v1"
_MANUAL_PLAN_ITEM_RESPONSE_FIELDS = frozenset(
    {
        "coords",
        "day_date",
        "day_index",
        "duration_min",
        "id",
        "is_meal",
        "local_title",
        "meal_type",
        "photoUrl",
        "place",
        "price_per_person",
        "settledness",
        "source",
        "start_hour",
        "tags",
        "title",
    }
)


class StaticHmacKeyring:
    """One explicit injected key for a disabled-by-default local integration seam."""

    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedManualPlanItemAdd:
    plan_id: str
    idempotency_key: str
    expected_plan_revision: int
    title: str
    after_item_id: str
    before_item_id: str
    start_hour: float | None


def execute_protected_manual_plan_item_add(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedManualPlanItemAdd,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    """Use the actual adjacent-gap PlanItem mutation under the B.2B protocol."""

    durable = DurableCommandKernel(
        PlanTransactionKernel(session_factory),
        keyring,
        StaticOutcomeContractRegistry(
            (
                OutcomeContract(
                    MANUAL_PLAN_ITEM_ADD_OPERATION,
                    1,
                    _MANUAL_PLAN_ITEM_RESPONSE_FIELDS,
                ),
            )
        ),
    )
    fields = {
        "title": command.title,
        "after_item_id": command.after_item_id,
        "before_item_id": command.before_item_id,
        "start_hour": command.start_hour,
    }
    durable_command = IdempotentCommand(
        operation_code=MANUAL_PLAN_ITEM_ADD_OPERATION,
        resource_kind="plan",
        resource_id=command.plan_id,
        idempotency_key=command.idempotency_key,
        expected_plan_revision=command.expected_plan_revision,
        command_fields=fields,
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedPlanContext, frozen_fields: Mapping[str, object]
    ) -> CommandResponse:
        if context.plan.id != command.plan_id:
            raise ManualPlanMismatch("Plan not found")
        item = add_manual_plan_item(
            session,
            context.plan,
            ManualPlanItemCommand(
                title=_required_str(frozen_fields, "title"),
                after_item_id=_required_str(frozen_fields, "after_item_id"),
                before_item_id=_required_str(frozen_fields, "before_item_id"),
                start_hour=_optional_number(frozen_fields, "start_hour"),
            ),
            lock_children=True,
        )
        return CommandResponse(status=200, payload=manual_plan_item_out(item))

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        mutation=mutation,
    )


def _required_str(fields: Mapping[str, object], name: str) -> str:
    try:
        value = fields[name]
    except KeyError as exc:
        raise ValueError(f"Missing canonical manual-item field {name}") from exc
    if not isinstance(value, str):
        raise ValueError(f"Invalid canonical manual-item field {name}")
    return value


def _optional_number(fields: Mapping[str, object], name: str) -> float | None:
    try:
        value = fields[name]
    except KeyError as exc:
        raise ValueError(f"Missing canonical manual-item field {name}") from exc
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid canonical manual-item field {name}")
    return float(value)
