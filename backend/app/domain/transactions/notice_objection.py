"""Protected adapter for the existing UpdateNotice objection writer.

Only ``POST /api/updates/{notice_id}/object`` is migrated here.  The adapter
locks a plan-scoped Notice and the decision facts it consumes, then delegates
the state machine itself to :func:`orchestrator.object_to_notice`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import ChangeProposal, DecisionRound, PlanItem, UpdateNotice
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


NOTICE_OBJECTION_OPERATION = "notice.object.v1"
_NOTICE_OBJECTION_RESPONSE_FIELDS = frozenset(
    {
        "applied",
        "checks",
        "detail",
        "findings",
        "headline",
        "needs_reason",
        "path",
        "proposal_id",
        "round_id",
    }
)


class NoticeObjectionNoticeMissing(Exception):
    """The Notice is not a child of the freshly locked Trip and Plan."""


class NoticeObjectionIneligible(Exception):
    """The Notice has already been consumed or was never objection-eligible."""


class StaticHmacKeyring:
    """One explicit injected key for this default-off local adapter."""

    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedNoticeObjection:
    notice_id: str
    idempotency_key: str
    expected_plan_revision: int


def execute_protected_notice_objection(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedNoticeObjection,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    """Escalate one eligible Notice using the owned durable transaction.

    This endpoint deliberately has no business request body.  Its complete
    semantic command is therefore the operation/resource/actor root supplied
    to the durable kernel plus the expected Plan revision.
    """

    durable = DurableCommandKernel(
        PlanTransactionKernel(session_factory),
        keyring,
        StaticOutcomeContractRegistry(
            (OutcomeContract(NOTICE_OBJECTION_OPERATION, 1, _NOTICE_OBJECTION_RESPONSE_FIELDS),)
        ),
    )
    durable_command = IdempotentCommand(
        operation_code=NOTICE_OBJECTION_OPERATION,
        resource_kind="update_notice",
        resource_id=command.notice_id,
        idempotency_key=command.idempotency_key,
        expected_plan_revision=command.expected_plan_revision,
        command_fields={},
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedPlanContext, _frozen_fields: Mapping[str, object]
    ) -> CommandResponse:
        # The scoped predicate is deliberately part of the locking statement:
        # an ID from another Trip/Plan matches no row and is never locked.
        notice = session.scalar(
            select(UpdateNotice)
            .join(PlanItem, UpdateNotice.plan_item_id == PlanItem.id)
            .where(
                UpdateNotice.id == command.notice_id,
                UpdateNotice.trip_id == context.trip.id,
                PlanItem.plan_id == context.plan.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update(of=UpdateNotice)
        )
        if notice is None:
            raise NoticeObjectionNoticeMissing("Notice not found")

        item = session.scalar(
            select(PlanItem)
            .where(PlanItem.id == notice.plan_item_id, PlanItem.plan_id == context.plan.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if item is None or notice.trip_id != context.trip.id:
            raise NoticeObjectionNoticeMissing("Notice not found")

        # Keep child order stable after Trip -> credential -> memberships ->
        # Plan -> ledger.  These are the precise pending facts consulted by
        # the authoritative guard below.
        session.scalars(
            select(DecisionRound)
            .where(DecisionRound.plan_item_id == item.id)
            .order_by(DecisionRound.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        session.scalars(
            select(ChangeProposal)
            .where(ChangeProposal.plan_item_id == item.id)
            .order_by(ChangeProposal.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()

        if not notice.can_object:
            raise NoticeObjectionIneligible("Nothing to object to")
        # The authoritative implementation repeats the same guard while all
        # relevant decision facts remain locked, before it changes the Notice.
        outcome = orch.object_to_notice(session, notice)
        return CommandResponse(status=200, payload=notice_objection_out(outcome), state_changed=True)

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        lock_all_trip_memberships=True,
        mutation=mutation,
    )


def notice_objection_out(outcome: orch.Outcome) -> dict[str, object]:
    """Detached equivalent of the legacy sanitized ``_outcome_out`` DTO."""

    verdict = outcome.classification
    return {
        "path": verdict.path.value,
        "headline": verdict.headline,
        "detail": verdict.detail,
        "needs_reason": verdict.needs_reason,
        "checks": [
            {
                "id": check.id,
                "label": check.label,
                "hit": check.hit,
                "privateNote": check.private_note,
            }
            for check in verdict.checks
        ],
        "findings": [
            {
                "code": finding.code,
                "text": finding.safe_text,
                "affected_count": finding.affected_count,
            }
            for finding in verdict.findings
        ],
        "applied": outcome.applied,
        "round_id": outcome.round_id,
        "proposal_id": outcome.proposal_id,
    }
