"""Protected emergency Trip-access quarantine.

Quarantine is intentionally distinct from Membership removal: it preserves the
incarnation and historical attribution while immediately denying new Trip use.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...db.models import GuestSession, InviteLink, TripMembership
from .. import auth as auth_service
from .durable import CommandResponse, HmacKeyring, OutcomeContract, StaticOutcomeContractRegistry
from .kernel import CredentialProof, PreliminaryActorScope
from .membership_lifecycle import lock_nonterminal_decisions
from .trip_durable import TripDurableCommandKernel, TripDurableCommandResult, TripIdempotentCommand
from .trip_kernel import LockedTripContext, TripTransactionKernel


OPERATION = "membership.quarantine.v1"
_CONTRACT = OutcomeContract(OPERATION, 1, frozenset({"quarantined"}))
_REASONS = frozenset(
    {"security_incident", "abuse", "unauthorized_access", "organizer_emergency"}
)


class MembershipQuarantineTargetMissing(Exception):
    pass


class MembershipQuarantineDenied(Exception):
    pass


@dataclass(frozen=True)
class ProtectedMembershipQuarantine:
    membership_id: str
    reason: str
    idempotency_key: str
    expected_trip_facts_revision: int

    def __post_init__(self) -> None:
        if self.reason not in _REASONS:
            raise MembershipQuarantineDenied("Quarantine reason is not allowed")


def execute_protected_membership_quarantine(
    *,
    session_factory: Callable[[], Session],
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: ProtectedMembershipQuarantine,
    keyring: HmacKeyring,
    expires_at: datetime | None = None,
) -> TripDurableCommandResult:
    """Quarantine one participant and revoke its Trip-bound entry paths."""

    durable = TripDurableCommandKernel(
        TripTransactionKernel(session_factory), keyring,
        StaticOutcomeContractRegistry((_CONTRACT,)),
    )
    durable_command = TripIdempotentCommand(
        operation_code=OPERATION,
        resource_kind="trip_membership",
        resource_id=command.membership_id,
        idempotency_key=command.idempotency_key,
        expected_trip_facts_revision=command.expected_trip_facts_revision,
        command_fields={"intent": "quarantine", "reason": command.reason},
        fingerprint_version=1,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=24),
    )

    def mutation(
        session: Session, context: LockedTripContext, _fields: Mapping[str, object]
    ) -> CommandResponse:
        actor = session.scalar(
            select(TripMembership)
            .where(
                TripMembership.id == context.actor.membership_id,
                TripMembership.trip_id == context.trip.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        target = session.scalar(
            select(TripMembership)
            .where(
                TripMembership.id == command.membership_id,
                TripMembership.trip_id == context.trip.id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if actor is None or target is None:
            raise MembershipQuarantineTargetMissing("Trip membership not found")
        if target.id == actor.id or target.role == "organizer":
            raise MembershipQuarantineDenied("This Membership cannot be quarantined here")
        if target.status == "removed":
            raise MembershipQuarantineDenied("Removed Membership cannot be quarantined")
        if target.quarantined_at is not None:
            return CommandResponse(200, {"quarantined": True}, state_changed=False)

        # Quarantine is allowed during the window, but these locks ensure it
        # cannot race a vote/proposal write into an ambiguous history.
        lock_nonterminal_decisions(session, context.trip.id)
        session.scalars(
            select(GuestSession)
            .where(GuestSession.membership_id == target.id)
            .order_by(GuestSession.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        invites = session.scalars(
            select(InviteLink)
            .where(
                InviteLink.trip_id == context.trip.id,
                InviteLink.revoked_at.is_(None),
                or_(InviteLink.expires_at.is_(None), InviteLink.expires_at > datetime.now(timezone.utc)),
            )
            .order_by(InviteLink.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        target.quarantined_at = datetime.now(timezone.utc)
        target.quarantine_reason = command.reason
        target.quarantined_by_membership_id = actor.id
        auth_service.revoke_guest_sessions_for_membership(session, target.id)
        for invite in invites:
            invite.revoked_at = datetime.now(timezone.utc)
            invite.is_primary = False
        session.flush()
        return CommandResponse(200, {"quarantined": True})

    return durable._execute_with_session_mutation(
        scope=scope,
        credential=credential,
        command=durable_command,
        required_role="organizer",
        lock_all_trip_memberships=True,
        mutation=mutation,
    )
