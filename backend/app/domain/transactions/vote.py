"""Protected adapter for the existing DecisionRound vote writer.

Only ``POST /api/rounds/{round_id}/votes`` is migrated here.  It intentionally
reuses the established orchestrator vote and settlement state machine, while
putting a final vote, its settlement effects, Plan revision, and durable
response into one owned transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import DecisionRound, PlanItem, TripMembership, Vote
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


VOTE_OPERATION = "round.vote.v1"
_VOTE_RESPONSE_FIELDS = frozenset(
    {
        "id",
        "kind",
        "plan_item_id",
        "item_title",
        "options",
        "reason",
        "deadline",
        "status",
        "winning_option_id",
        "responded",
        "total_members",
        "electorate_count_available",
        "tally",
        "my_vote",
    }
)


class VoteRoundMissing(Exception):
    """The requested round is not a child of the freshly locked Plan."""


class VoteRoundNotOpen(Exception):
    """The existing public route only accepts votes for open rounds."""


class VoteCommandInvalid(ValueError):
    """The canonical command cannot be reconstructed safely."""


class StaticHmacKeyring:
    """One explicit injected key for this disabled-by-default local adapter."""

    def __init__(self, key: HmacSigningKey):
        self._key = key

    def active_key(self) -> HmacSigningKey:
        return self._key

    def key_for_version(self, version: str) -> HmacSigningKey | None:
        return self._key if version == self._key.version else None


@dataclass(frozen=True)
class ProtectedVote:
    round_id: str
    idempotency_key: str
    expected_plan_revision: int
    option_id: str


def execute_protected_vote(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedVote,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> DurableCommandResult:
    """Cast one real vote and atomically settle if it is the final response.

    The transaction kernel has already locked Trip, credential, actor
    membership, all Trip memberships, and the sole Plan before this callback
    takes any child locks.  Every child lookup is Plan-scoped, so a round from
    another Trip is rejected without locking or changing its rows.
    """

    durable = DurableCommandKernel(
        PlanTransactionKernel(session_factory),
        keyring,
        StaticOutcomeContractRegistry(
            (OutcomeContract(VOTE_OPERATION, 1, _VOTE_RESPONSE_FIELDS),)
        ),
    )
    durable_command = IdempotentCommand(
        operation_code=VOTE_OPERATION,
        resource_kind="decision_round",
        resource_id=command.round_id,
        idempotency_key=command.idempotency_key,
        expected_plan_revision=command.expected_plan_revision,
        command_fields={"option_id": command.option_id},
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedPlanContext, frozen_fields: Mapping[str, object]
    ) -> CommandResponse:
        option_id = frozen_fields.get("option_id")
        if not isinstance(option_id, str):
            raise VoteCommandInvalid("Invalid canonical vote option")

        round_ = session.scalar(
            select(DecisionRound)
            .join(PlanItem, DecisionRound.plan_item_id == PlanItem.id)
            .where(
                DecisionRound.id == command.round_id,
                PlanItem.plan_id == context.plan.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if round_ is None:
            raise VoteRoundMissing("Decision round not found")
        if round_.status != "open":
            raise VoteRoundNotOpen("Round is not open")
        # Validate against the freshly locked authoritative Round before any
        # Vote or settlement read/write. ``cast_vote`` repeats this shared
        # invariant for every legacy caller as a defensive boundary.
        orch.validate_vote_option(round_, option_id)

        # Retain the legacy child-lock sequence after the canonical root locks:
        # authoritative Round, its PlanItem, then the Round's existing Votes.
        # The all-membership lock was requested below because electorate size is
        # an authoritative input to both all-voted and reopen-majority logic.
        item = session.scalar(
            select(PlanItem)
            .where(PlanItem.id == round_.plan_item_id, PlanItem.plan_id == context.plan.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if item is None:
            raise VoteRoundMissing("Decision round not found")
        votes = session.scalars(
            select(Vote)
            .where(Vote.round_id == round_.id)
            .order_by(Vote.trip_membership_id, Vote.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        existing = next(
            (vote for vote in votes if vote.trip_membership_id == context.actor.membership_id),
            None,
        )

        # A duplicate value is a genuine no-op unless it encounters a legacy
        # open-round state that already has every vote; the legacy route settles
        # that state on the next vote request, so retain that behavior.
        vote_changed = existing is None or existing.option_id != option_id
        if vote_changed:
            orch.cast_vote(session, round_, context.actor.membership_id, option_id)

        settled = False
        if _round_has_all_votes(session, round_):
            orch.settle_round(session, round_)
            settled = True

        return CommandResponse(
            status=200,
            payload=vote_round_out(session, round_, context.actor.membership_id),
            state_changed=vote_changed or settled,
        )

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        lock_all_trip_memberships=True,
        mutation=mutation,
    )


def _round_has_all_votes(session: Session, round_: DecisionRound) -> bool:
    """Exact legacy all-voted rule, evaluated only under the electorate locks."""

    votes = session.scalars(select(Vote).where(Vote.round_id == round_.id)).all()
    item = session.get(PlanItem, round_.plan_item_id)
    if item is None:
        raise VoteRoundMissing("Decision round has no Plan item")
    total = round_.electorate_count
    if total is None:
        return False
    return total > 0 and len(votes) >= total


def vote_round_out(session: Session, round_: DecisionRound, membership_id: str) -> dict[str, object]:
    """Detached equivalent of the legacy route's round response."""

    item = session.get(PlanItem, round_.plan_item_id)
    if item is None:
        raise VoteRoundMissing("Decision round has no Plan item")
    votes = session.scalars(select(Vote).where(Vote.round_id == round_.id)).all()
    total = round_.electorate_count
    tally: dict[str, int] = {}
    for vote in votes:
        tally[vote.option_id] = tally.get(vote.option_id, 0) + 1
    my_vote = next(
        (vote.option_id for vote in votes if vote.trip_membership_id == membership_id), None
    )
    return {
        "id": round_.id,
        "kind": round_.kind,
        "plan_item_id": round_.plan_item_id,
        "item_title": item.title,
        "options": round_.options,
        "reason": round_.reason,
        "deadline": round_.deadline.isoformat(),
        "status": round_.status,
        "winning_option_id": round_.winning_option_id,
        "responded": len(votes),
        "total_members": total,
        "electorate_count_available": total is not None,
        "tally": tally,
        "my_vote": my_vote,
    }
