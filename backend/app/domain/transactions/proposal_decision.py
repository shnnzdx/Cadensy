"""Protected adapter for the existing ChangeProposal decision writer.

Only ``POST /api/proposals/{proposal_id}/decisions`` is migrated here.  The
existing confirmation state machine remains the sole authority for eligibility,
decline, unanimous acceptance, conflict escalation, and application effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import ChangeProposal, PlanItem, ProposalDecision
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


PROPOSAL_DECISION_OPERATION = "plan.proposal.decision.v1"
_PROPOSAL_DECISION_RESPONSE_FIELDS = frozenset(
    {
        "id",
        "status",
        "plan_item_id",
        "before",
        "after",
        "my_status",
        "can_decide",
        "members",
        "privacy_note",
        "applied",
    }
)
_VALID_DECISION_STATUSES = frozenset({"accepted", "declined"})


class ProposalDecisionProposalMissing(Exception):
    """The requested Proposal is not a child of the freshly locked Plan."""


class ProposalDecisionCommandInvalid(ValueError):
    """The frozen command cannot be reconstructed as a valid decision."""


class StaticHmacKeyring:
    """One explicit injected key for this default-off local adapter."""

    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedProposalDecision:
    proposal_id: str
    idempotency_key: str
    expected_plan_revision: int
    status: str


def execute_protected_proposal_decision(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedProposalDecision,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    """Record one real proposal decision in the owned durable transaction."""

    durable = DurableCommandKernel(
        PlanTransactionKernel(session_factory),
        keyring,
        StaticOutcomeContractRegistry(
            (
                OutcomeContract(
                    PROPOSAL_DECISION_OPERATION,
                    1,
                    _PROPOSAL_DECISION_RESPONSE_FIELDS,
                ),
            )
        ),
    )
    durable_command = IdempotentCommand(
        operation_code=PROPOSAL_DECISION_OPERATION,
        resource_kind="change_proposal",
        resource_id=command.proposal_id,
        idempotency_key=command.idempotency_key,
        expected_plan_revision=command.expected_plan_revision,
        command_fields={"status": command.status},
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedPlanContext, frozen_fields: Mapping[str, object]
    ) -> CommandResponse:
        status = frozen_fields.get("status")
        if not isinstance(status, str) or status not in _VALID_DECISION_STATUSES:
            raise ProposalDecisionCommandInvalid("Invalid canonical proposal decision")

        proposal = session.scalar(
            select(ChangeProposal)
            .join(PlanItem, ChangeProposal.plan_item_id == PlanItem.id)
            .where(
                ChangeProposal.id == command.proposal_id,
                PlanItem.plan_id == context.plan.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update(of=ChangeProposal)
        )
        if proposal is None:
            raise ProposalDecisionProposalMissing("Proposal not found")

        # Keep child ordering stable after the canonical root locks.  Lock all
        # active Plan facts that conflict classification may inspect before
        # ProposalDecision rows and before calling the unchanged state machine.
        plan_items = session.scalars(
            select(PlanItem)
            .where(PlanItem.plan_id == context.plan.id)
            .order_by(PlanItem.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        if not any(item.id == proposal.plan_item_id for item in plan_items):
            raise ProposalDecisionProposalMissing("Proposal not found")
        decisions = session.scalars(
            select(ProposalDecision)
            .where(ProposalDecision.proposal_id == proposal.id)
            .order_by(ProposalDecision.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        before = (proposal.status, tuple((decision.id, decision.status) for decision in decisions))

        actor_decision = next(
            (decision for decision in decisions if decision.trip_membership_id == context.actor.membership_id),
            None,
        )
        # ``decide_proposal`` flushes even for a repeated identical decision.
        # A regular repeat is therefore recognized before entering the durable
        # callback mutation boundary. Retain the legacy recovery behavior for
        # inconsistent old waiting rows that already contain a decline or full
        # unanimous acceptance: those still need the authoritative state
        # machine to reach their terminal status.
        recover_waiting_state = (
            proposal.status == "waiting_affected_members"
            and (
                any(decision.status == "declined" for decision in decisions)
                or (bool(decisions) and all(decision.status == "accepted" for decision in decisions))
            )
        )
        if (
            proposal.status != "waiting_affected_members"
            or actor_decision is None
            or (actor_decision.status == status and not recover_waiting_state)
        ):
            return CommandResponse(
                status=200,
                payload=proposal_out(
                    session, proposal, context.actor.membership_id, applied=False
                ),
                state_changed=False,
            )

        # This call remains the one authoritative confirmation state machine.
        # It rechecks affected-member eligibility, terminal status, unanimous
        # approval, and schedule conflicts against the locks above.
        applied = orch.decide_proposal(session, proposal, context.actor.membership_id, status)
        after = (proposal.status, tuple((decision.id, decision.status) for decision in decisions))
        state_changed = before != after
        return CommandResponse(
            status=200,
            payload=proposal_out(session, proposal, context.actor.membership_id, applied=applied),
            state_changed=state_changed,
        )

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        lock_all_trip_memberships=True,
        mutation=mutation,
    )


def proposal_out(
    session: Session,
    proposal: ChangeProposal,
    membership_id: str,
    *,
    applied: bool,
) -> dict[str, object]:
    """Detached, anonymous equivalent of the existing ``_proposal_out`` DTO."""

    decisions = session.scalars(
        select(ProposalDecision)
        .where(ProposalDecision.proposal_id == proposal.id)
        .order_by(ProposalDecision.created_at, ProposalDecision.id)
    ).all()
    my_decision = next(
        (decision for decision in decisions if decision.trip_membership_id == membership_id), None
    )
    labels = [f"Member {chr(65 + index)}" for index in range(len(decisions))]
    return {
        "id": proposal.id,
        "status": proposal.status,
        "plan_item_id": proposal.plan_item_id,
        "before": proposal.before_json,
        "after": proposal.after_json,
        "my_status": my_decision.status if my_decision is not None else None,
        "can_decide": my_decision is not None and proposal.status == "waiting_affected_members",
        "members": [
            {
                "label": label,
                "status": decision.status,
                "is_me": decision.trip_membership_id == membership_id,
            }
            for label, decision in zip(labels, decisions)
        ],
        "privacy_note": "Everyone in this conversation is anonymous. Names and private reasons are never shown.",
        "applied": applied,
    }
