"""Protected adapters for the existing organizer decision actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import ChangeProposal, DecisionRound, PlanItem, ProposalDecision
from ..decisions import organizer as organizer
from .durable import CommandResponse, DurableCommandKernel, DurableCommandResult, HmacKeyring, IdempotentCommand, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, LockedPlanContext, PreliminaryActorScope, PlanTransactionKernel


ROUND_EXTEND_OPERATION = "round.extend.v1"
PROPOSAL_ESCALATE_OPERATION = "proposal.escalate.v1"
PROPOSAL_DEADLOCK_OPERATION = "proposal.deadlock.resolve.v1"


class OrganizerResourceMissing(Exception):
    pass


@dataclass(frozen=True)
class ProtectedOrganizerAction:
    operation_code: str
    resource_id: str
    idempotency_key: str
    expected_plan_revision: int
    action: str | None = None


class _Registry(StaticOutcomeContractRegistry):
    def __init__(self) -> None:
        super().__init__(
            (
                OutcomeContract(ROUND_EXTEND_OPERATION, 1, frozenset({"id", "deadline"})),
                OutcomeContract(PROPOSAL_ESCALATE_OPERATION, 1, frozenset({"id", "status"})),
                OutcomeContract(PROPOSAL_DEADLOCK_OPERATION, 1, frozenset({"item_id", "title", "action"})),
            )
        )


def execute_protected_organizer_action(
    *, session_factory: Callable[[], Session], scope: PreliminaryActorScope,
    credential: CredentialProof, command: ProtectedOrganizerAction, keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    if command.operation_code not in {ROUND_EXTEND_OPERATION, PROPOSAL_ESCALATE_OPERATION, PROPOSAL_DEADLOCK_OPERATION}:
        raise ValueError("Unsupported organizer operation")
    durable = DurableCommandKernel(PlanTransactionKernel(session_factory), keyring, _Registry())
    resource_kind = "decision_round" if command.operation_code == ROUND_EXTEND_OPERATION else "change_proposal"
    fields: dict[str, object] = {} if command.action is None else {"action": command.action}
    durable_command = IdempotentCommand(
        operation_code=command.operation_code, resource_kind=resource_kind, resource_id=command.resource_id,
        idempotency_key=command.idempotency_key, expected_plan_revision=command.expected_plan_revision,
        command_fields=fields, fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(session: Session, context: LockedPlanContext, frozen: Mapping[str, object]) -> CommandResponse:
        if command.operation_code == ROUND_EXTEND_OPERATION:
            round_ = session.scalar(select(DecisionRound).join(PlanItem, DecisionRound.plan_item_id == PlanItem.id).where(DecisionRound.id == command.resource_id, PlanItem.plan_id == context.plan.id).execution_options(populate_existing=True).with_for_update(of=DecisionRound))
            if round_ is None:
                raise OrganizerResourceMissing("Round not found")
            item = session.scalar(select(PlanItem).where(PlanItem.id == round_.plan_item_id, PlanItem.plan_id == context.plan.id).execution_options(populate_existing=True).with_for_update())
            if item is None:
                raise OrganizerResourceMissing("Round not found")
            result = organizer.extend_round(session, _locked_actor_membership(session, context), round_.id)
            return CommandResponse(200, {"id": result.id, "deadline": result.deadline.isoformat()})

        proposal = session.scalar(select(ChangeProposal).join(PlanItem, ChangeProposal.plan_item_id == PlanItem.id).where(ChangeProposal.id == command.resource_id, PlanItem.plan_id == context.plan.id).execution_options(populate_existing=True).with_for_update(of=ChangeProposal))
        if proposal is None:
            raise OrganizerResourceMissing("Proposal not found")
        item = session.scalar(select(PlanItem).where(PlanItem.id == proposal.plan_item_id, PlanItem.plan_id == context.plan.id).execution_options(populate_existing=True).with_for_update())
        if item is None:
            raise OrganizerResourceMissing("Proposal not found")
        session.scalars(select(ProposalDecision).where(ProposalDecision.proposal_id == proposal.id).order_by(ProposalDecision.id).execution_options(populate_existing=True).with_for_update()).all()
        actor = _locked_actor_membership(session, context)
        if command.operation_code == PROPOSAL_ESCALATE_OPERATION:
            # Existing product policy: any authenticated same-Trip member may
            # escalate; no affected-member restriction is introduced here.
            result = organizer.escalate(session, actor, proposal.id)
            return CommandResponse(200, {"id": result.id, "status": result.status})
        action = frozen.get("action")
        if not isinstance(action, str):
            raise ValueError("Invalid deadlock action")
        result = organizer.resolve_deadlock(session, actor, proposal.id, action)
        return CommandResponse(200, {"item_id": result.id, "title": result.title, "action": action})

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        required_role="organizer" if command.operation_code != PROPOSAL_ESCALATE_OPERATION else None,
        lock_all_trip_memberships=True,
        mutation=mutation,
    )


def _locked_actor_membership(session: Session, context: LockedPlanContext):
    from ...db.models import TripMembership
    actor = session.scalar(select(TripMembership).where(TripMembership.id == context.actor.membership_id, TripMembership.trip_id == context.trip.id).execution_options(populate_existing=True).with_for_update())
    if actor is None:
        raise OrganizerResourceMissing("Membership not found")
    return actor
