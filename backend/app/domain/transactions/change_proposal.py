"""Protected adapter for the existing Change Proposal submission writer.

This module deliberately migrates only the initial ``POST .../changes`` command.
Votes, settlement, proposal decisions, organizer actions, and scheduler expiry keep
their existing entry points.  The durable command reuses the authoritative
``orchestrator.propose_change`` state machine; it does not recreate its NOTICE,
ROUND, REOPEN_ROUND, or CONFIRM rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import (
    ChangeProposal,
    DecisionRound,
    MemberConstraint,
    PlanItem,
    ProposalDecision,
    TripMembership,
    Vote,
)
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


CHANGE_PROPOSAL_OPERATION = "plan.change.submit.v1"
_CHANGE_PROPOSAL_RESPONSE_FIELDS = frozenset(
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
_DAY_START_HOUR = 9.0
_DAY_END_HOUR = 21.0


class ChangeProposalItemMissing(Exception):
    """The requested child does not belong to the freshly locked Plan."""


class ChangeProposalCommandInvalid(ValueError):
    """A frozen command could not be reconstructed as a validated change."""


class StaticHmacKeyring:
    """One explicit injected key for this default-off local adapter."""

    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedChangeProposal:
    item_id: str
    idempotency_key: str
    expected_plan_revision: int
    patch: Mapping[str, object]
    request: str
    reason: str | None
    alternatives: Sequence[Mapping[str, object]]


def execute_protected_change_proposal(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedChangeProposal,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    """Submit one real change under one owned transaction and durable outcome.

    ``command_fields`` intentionally contains the complete validated HTTP
    semantics: the exact patch, request/reopen reason, and every received
    alternative field in order.  The durable kernel snapshots that mapping
    before HMAC calculation and exposes only that frozen snapshot to mutation.
    """

    durable = DurableCommandKernel(
        PlanTransactionKernel(session_factory),
        keyring,
        StaticOutcomeContractRegistry(
            (
                OutcomeContract(
                    CHANGE_PROPOSAL_OPERATION,
                    1,
                    _CHANGE_PROPOSAL_RESPONSE_FIELDS,
                ),
            )
        ),
    )
    durable_command = IdempotentCommand(
        operation_code=CHANGE_PROPOSAL_OPERATION,
        resource_kind="plan_item",
        resource_id=command.item_id,
        idempotency_key=command.idempotency_key,
        expected_plan_revision=command.expected_plan_revision,
        command_fields={
            "patch": dict(command.patch),
            "request": command.request,
            "reason": command.reason,
            "alternatives": [dict(option) for option in command.alternatives],
        },
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedPlanContext, frozen_fields: Mapping[str, object]
    ) -> CommandResponse:
        item = session.scalar(
            select(PlanItem)
            .where(
                PlanItem.id == command.item_id,
                PlanItem.plan_id == context.plan.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if item is None:
            raise ChangeProposalItemMissing("Plan item not found")

        # Every row below is reached only after the durable kernel has locked
        # Trip -> credential -> actor membership -> Plan -> ledger.  Keep the
        # remaining child order stable and Plan-scoped before fresh classification.
        _lock_relevant_change_rows(session, context, item)
        patch = _frozen_patch(frozen_fields)
        request = _frozen_string(frozen_fields, "request")
        reason = _frozen_optional_string(frozen_fields, "reason")
        alternatives = _validated_frozen_alternatives(
            session,
            item,
            context.actor.membership_id,
            _frozen_sequence(frozen_fields, "alternatives"),
        )
        outcome = orch.propose_change(
            session,
            item,
            patch,
            context.actor.membership_id,
            request=request,
            reason=reason,
            alternatives=alternatives,
        )
        return CommandResponse(status=200, payload=change_outcome_out(outcome))

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        lock_all_trip_memberships=True,
        mutation=mutation,
    )


def _lock_relevant_change_rows(
    session: Session, context: LockedPlanContext, item: PlanItem
) -> None:
    """Lock the state-machine facts that the real submission can consume/write.

    The actor and every Trip membership are already locked by
    ``PlanTransactionKernel`` before it locks Plan. The constraints provide a
    fresh, stable classifier input; all Plan children are scoped through
    ``context.plan``.
    Existing legacy lifecycle writers are still outside B.2C coverage, so this
    is a command-local linearization boundary rather than a global claim.
    """

    session.scalars(
        select(MemberConstraint)
        .join(TripMembership, TripMembership.id == MemberConstraint.trip_membership_id)
        .where(TripMembership.trip_id == context.trip.id)
        .order_by(MemberConstraint.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).all()
    session.scalars(
        select(PlanItem)
        .where(PlanItem.plan_id == context.plan.id)
        .order_by(PlanItem.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).all()
    round_ids = session.scalars(
        select(DecisionRound.id)
        .where(DecisionRound.plan_item_id == item.id)
        .order_by(DecisionRound.id)
        .with_for_update()
    ).all()
    proposal_ids = session.scalars(
        select(ChangeProposal.id)
        .where(ChangeProposal.plan_item_id == item.id)
        .order_by(ChangeProposal.id)
        .with_for_update()
    ).all()
    if round_ids:
        session.scalars(
            select(Vote)
            .where(Vote.round_id.in_(round_ids))
            .order_by(Vote.id)
            .with_for_update()
        ).all()
    if proposal_ids:
        session.scalars(
            select(ProposalDecision)
            .where(ProposalDecision.proposal_id.in_(proposal_ids))
            .order_by(ProposalDecision.id)
            .with_for_update()
        ).all()


def _validated_frozen_alternatives(
    session: Session,
    item: PlanItem,
    actor_membership_id: str,
    alternatives: Sequence[object],
) -> list[dict[str, object]]:
    """Re-run the existing API alternative admission policy under root locks."""

    accepted: list[dict[str, object]] = []
    for raw_option in alternatives[:5]:
        if not isinstance(raw_option, Mapping) or raw_option.get("item_id") != item.id:
            continue
        patch: dict[str, object] = {}
        start_hour = raw_option.get("start_hour")
        day_date = raw_option.get("day_date")
        duration_min = raw_option.get("duration_min")
        if start_hour is not None:
            if isinstance(start_hour, bool) or not isinstance(start_hour, (int, float)):
                continue
            if not _DAY_START_HOUR <= float(start_hour) <= _DAY_END_HOUR:
                continue
            patch["start_hour"] = float(start_hour)
        if day_date is not None:
            if not isinstance(day_date, str):
                continue
            try:
                patch["day_date"] = date.fromisoformat(day_date)
            except ValueError:
                continue
        if duration_min is not None:
            if isinstance(duration_min, bool) or not isinstance(duration_min, int):
                continue
            if duration_min <= 0:
                continue
            patch["duration_min"] = duration_min
        if not patch:
            continue

        # ``classify_change`` is read-only. The nested transaction is retained
        # from the legacy route's validation protocol so a future accidental
        # write cannot escape this validation step.
        savepoint = session.begin_nested()
        try:
            orch.classify_change(session, item, patch, actor_membership_id)
        except Exception:
            savepoint.rollback()
            continue
        else:
            savepoint.rollback()

        accepted.append(
            {
                "label": _truncate(_optional_raw_string(raw_option, "label"), 60),
                "title": _truncate(_optional_raw_string(raw_option, "title"), 60)
                or "Alternative",
                "body": _truncate(_optional_raw_string(raw_option, "body"), 200),
                "tradeoff": _truncate(_optional_raw_string(raw_option, "tradeoff"), 200),
                "patch": patch,
            }
        )
    return accepted


def change_outcome_out(outcome: orch.Outcome) -> dict[str, object]:
    """Detached public outcome; never include raw constraints, IDs, or reason."""

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
                # This is fixed engine copy, never private constraint wording.
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


def _frozen_patch(fields: Mapping[str, object]) -> dict[str, object]:
    patch = _frozen_mapping(fields, "patch")
    restored = dict(patch)
    value = restored.get("day_date")
    if isinstance(value, str):
        try:
            restored["day_date"] = date.fromisoformat(value)
        except ValueError as exc:
            raise ChangeProposalCommandInvalid("Invalid canonical day_date") from exc
    return restored


def _frozen_mapping(fields: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = fields.get(name)
    if not isinstance(value, Mapping):
        raise ChangeProposalCommandInvalid(f"Invalid canonical change field {name}")
    return value


def _frozen_sequence(fields: Mapping[str, object], name: str) -> Sequence[object]:
    value = fields.get(name)
    if not isinstance(value, tuple):
        raise ChangeProposalCommandInvalid(f"Invalid canonical change field {name}")
    return value


def _frozen_string(fields: Mapping[str, object], name: str) -> str:
    value = fields.get(name)
    if not isinstance(value, str):
        raise ChangeProposalCommandInvalid(f"Invalid canonical change field {name}")
    return value


def _frozen_optional_string(fields: Mapping[str, object], name: str) -> str | None:
    value = fields.get(name)
    if value is None or isinstance(value, str):
        return value
    raise ChangeProposalCommandInvalid(f"Invalid canonical change field {name}")


def _optional_raw_string(option: Mapping[str, object], name: str) -> str | None:
    value = option.get(name)
    return value if isinstance(value, str) else None


def _truncate(value: str | None, maximum: int) -> str:
    return (value or "")[:maximum]
