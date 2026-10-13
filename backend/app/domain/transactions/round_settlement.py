"""Protected adapter for the existing standalone DecisionRound settlement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import DecisionRound, PlanItem, Vote
from ..decisions import orchestrator as orch
from .durable import CommandResponse, DurableCommandKernel, DurableCommandResult, HmacKeyring, HmacSigningKey, IdempotentCommand, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, LockedPlanContext, PlanTransactionKernel, PreliminaryActorScope
from .vote import vote_round_out


ROUND_SETTLEMENT_OPERATION = "round.settle.v1"
_ROUND_RESPONSE_FIELDS = frozenset({"id", "kind", "plan_item_id", "item_title", "options", "reason", "deadline", "status", "winning_option_id", "responded", "total_members", "tally", "my_vote"})


class RoundSettlementRoundMissing(Exception):
    """The requested Round is not a child of the freshly locked Plan."""


class StaticHmacKeyring:
    def __init__(self, key: HmacSigningKey): self._key = key
    def active_key(self) -> HmacSigningKey: return self._key
    def key_for_version(self, version: str) -> HmacSigningKey | None: return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedRoundSettlement:
    round_id: str
    idempotency_key: str
    expected_plan_revision: int


def execute_protected_round_settlement(*, session_factory: Callable[[], Session], scope: PreliminaryActorScope, credential: CredentialProof, command: ProtectedRoundSettlement, keyring: HmacKeyring, expires_at: datetime | None = None) -> DurableCommandResult:
    """Settle one Round in the owned transaction, or retain a closed no-op."""
    durable = DurableCommandKernel(PlanTransactionKernel(session_factory), keyring, StaticOutcomeContractRegistry((OutcomeContract(ROUND_SETTLEMENT_OPERATION, 1, _ROUND_RESPONSE_FIELDS),)))
    durable_command = IdempotentCommand(operation_code=ROUND_SETTLEMENT_OPERATION, resource_kind="decision_round", resource_id=command.round_id, idempotency_key=command.idempotency_key, expected_plan_revision=command.expected_plan_revision, command_fields={}, fingerprint_version=1, expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24))

    def mutation(session: Session, context: LockedPlanContext, _fields: Mapping[str, object]) -> CommandResponse:
        # Plan-scoped lookup is also the locking predicate: foreign rounds do
        # not match and their child rows are not locked.
        round_ = session.scalar(select(DecisionRound).join(PlanItem, DecisionRound.plan_item_id == PlanItem.id).where(DecisionRound.id == command.round_id, PlanItem.plan_id == context.plan.id).execution_options(populate_existing=True).with_for_update())
        if round_ is None: raise RoundSettlementRoundMissing("Decision round not found")
        item = session.scalar(select(PlanItem).where(PlanItem.id == round_.plan_item_id, PlanItem.plan_id == context.plan.id).execution_options(populate_existing=True).with_for_update())
        if item is None: raise RoundSettlementRoundMissing("Decision round not found")
        # Matches the protected Vote writer after canonical roots: Round,
        # PlanItem, then every Vote in deterministic member/ID order.
        session.scalars(select(Vote).where(Vote.round_id == round_.id).order_by(Vote.trip_membership_id, Vote.id).execution_options(populate_existing=True).with_for_update()).all()
        if round_.status == "closed":
            return CommandResponse(status=200, payload=vote_round_out(session, round_, context.actor.membership_id), state_changed=False)
        orch.settle_round(session, round_)
        return CommandResponse(status=200, payload=vote_round_out(session, round_, context.actor.membership_id), state_changed=True)

    return durable._execute_with_session_mutation(scope=scope, credential=credential, command=durable_command, lock_all_trip_memberships=True, mutation=mutation)
