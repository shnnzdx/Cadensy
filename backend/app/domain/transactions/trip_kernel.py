"""Trip-root transaction boundary for future planning-facts commands.

Unlike the existing PlanTransactionKernel, this module accepts zero Plan rows.
It owns one fresh local Session and transaction, serializing only the frozen
root/security sequence: Trip -> credential -> membership(s) -> all Plans.
Ledger, revisions, child rows, and business writers intentionally remain out of
scope until later separately reviewed phases.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..membership_policy import is_membership_access_authorized
from ...db.models import AuthSession, GuestSession, Plan, Trip, TripMembership
from .kernel import (
    ContextExpired,
    CredentialProof,
    CredentialKind,
    DuplicatePlans,
    InsufficientRole,
    InvalidCredential,
    LockedActor,
    MembershipNotAuthorized,
    PreliminaryActorScope,
    TransactionKernelError,
    TripNotFound,
    _is_expired,
    _validate_safe_callback_result,
    classify_database_error,
)


T = TypeVar("T")


@dataclass
class LockedTripContext:
    """Transaction-scoped aggregate references for a future Trip command.

    ORM references are intentionally available only while the kernel owns its
    transaction.  Callers may return detached primitives/DTOs, but cannot keep
    this context or attached rows after the callback returns.
    """

    actor: LockedActor
    _trip: Trip = field(repr=False)
    _plan: Plan | None = field(repr=False)
    session_identity: int
    connection_identity: int
    _active: bool = field(default=True, init=False, repr=False)

    @property
    def trip(self) -> Trip:
        self._require_active()
        return self._trip

    @property
    def plan(self) -> Plan | None:
        self._require_active()
        return self._plan

    @property
    def trip_id(self) -> str:
        self._require_active()
        return self._trip.id

    @property
    def plan_id(self) -> str | None:
        self._require_active()
        return self._plan.id if self._plan is not None else None

    @property
    def planning_facts_revision(self) -> int:
        self._require_active()
        return self._trip.planning_facts_revision

    @property
    def plan_revision(self) -> int | None:
        self._require_active()
        return self._plan.revision if self._plan is not None else None

    def _invalidate(self) -> None:
        self._active = False

    def _require_active(self) -> None:
        if not self._active:
            raise ContextExpired("LockedTripContext cannot outlive its transaction callback")


class TripTransactionKernel(Generic[T]):
    """Run one future Trip-bound command in a fresh owned transaction."""

    def __init__(self, session_factory: Callable[[], Session], *, lock_timeout_ms: int | None = None):
        if lock_timeout_ms is not None and lock_timeout_ms < 1:
            raise ValueError("lock_timeout_ms must be positive")
        self._session_factory = session_factory
        self._lock_timeout_ms = lock_timeout_ms

    def execute(
        self,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        required_role: str | None = None,
        operation: Callable[[LockedTripContext], T],
    ) -> T:
        """Execute a detached-result callback after locked reauthorization.

        The public callback receives neither a raw bearer nor a Session/commit
        API. It may mutate its active context only under a later writer phase;
        any exception rolls back this owned transaction.
        """

        def run(_session: Session, context: LockedTripContext) -> T:
            result = operation(context)
            _validate_safe_callback_result(result)
            return result

        return self._run_locked_command(
            scope=scope,
            credential=credential,
            required_role=required_role,
            operation=run,
        )

    def _run_locked_command(
        self,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        required_role: str | None,
        lock_all_trip_memberships: bool = False,
        operation: Callable[[Session, LockedTripContext], T],
    ) -> T:
        """Package-private owned-Session hook reserved for future collaborators."""
        session = self._session_factory()
        context: LockedTripContext | None = None
        try:
            with session.begin():
                if self._lock_timeout_ms is not None:
                    session.execute(
                        text("SELECT set_config('lock_timeout', :timeout, true)"),
                        {"timeout": f"{self._lock_timeout_ms}ms"},
                    )
                context = self._acquire_locked_trip(
                    session,
                    scope=scope,
                    credential=credential,
                    required_role=required_role,
                    lock_all_trip_memberships=lock_all_trip_memberships,
                )
                return operation(session, context)
        except SQLAlchemyError as exc:
            raise classify_database_error(exc) from exc
        finally:
            if context is not None:
                context._invalidate()
            session.close()

    def _acquire_locked_trip(
        self,
        session: Session,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        required_role: str | None,
        lock_all_trip_memberships: bool,
    ) -> LockedTripContext:
        # This is the first authoritative row lock. No credential, membership,
        # Plan, or child lookup is permitted ahead of the Trip root.
        trip = session.scalar(
            select(Trip)
            .where(Trip.id == scope.trip_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if trip is None:
            raise TripNotFound("Trip does not exist")

        actor = self._reauthorize_actor(
            session,
            scope=scope,
            credential=credential,
            required_role=required_role,
        )
        if lock_all_trip_memberships:
            session.scalars(
                select(TripMembership)
                .where(TripMembership.trip_id == trip.id)
                .order_by(TripMembership.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            ).all()

        plans = session.scalars(
            select(Plan)
            .where(Plan.trip_id == trip.id)
            .order_by(Plan.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        if len(plans) > 1:
            raise DuplicatePlans("Trip has multiple Plans; command is refused")

        connection = session.connection().connection
        return LockedTripContext(
            actor=actor,
            _trip=trip,
            _plan=plans[0] if plans else None,
            session_identity=id(session),
            connection_identity=id(connection),
        )

    def _reauthorize_actor(
        self,
        session: Session,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        required_role: str | None,
    ) -> LockedActor:
        if scope.credential_kind == "account":
            actor = self._locked_account_actor(session, scope, credential)
        else:
            actor = self._locked_guest_actor(session, scope, credential)
        if required_role is not None and actor.role != required_role:
            raise InsufficientRole(f"Role {required_role!r} is required")
        return actor

    def _locked_account_actor(
        self, session: Session, scope: PreliminaryActorScope, credential: CredentialProof
    ) -> LockedActor:
        auth_session = session.scalar(
            select(AuthSession)
            .where(AuthSession.token_hash == credential._token_hash)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or _is_expired(auth_session.expires_at)
            or auth_session.user_id != scope.expected_user_id
        ):
            raise InvalidCredential("Account credential is invalid or expired")
        membership = session.scalar(
            select(TripMembership)
            .where(
                TripMembership.id == scope.membership_id,
                TripMembership.trip_id == scope.trip_id,
                TripMembership.user_id == auth_session.user_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if membership is None or not is_membership_access_authorized(membership):
            raise MembershipNotAuthorized("Account membership is no longer active")
        return LockedActor(
            membership_id=membership.id,
            trip_id=membership.trip_id,
            role=membership.role,
            credential_kind="account",
            user_id=auth_session.user_id,
        )

    def _locked_guest_actor(
        self, session: Session, scope: PreliminaryActorScope, credential: CredentialProof
    ) -> LockedActor:
        guest_session = session.scalar(
            select(GuestSession)
            .where(GuestSession.token_hash == credential._token_hash)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            guest_session is None
            or guest_session.revoked_at is not None
            or _is_expired(guest_session.expires_at)
            or guest_session.trip_id != scope.trip_id
            or guest_session.membership_id != scope.membership_id
        ):
            raise InvalidCredential("Guest credential is invalid or expired")
        membership = session.scalar(
            select(TripMembership)
            .where(
                TripMembership.id == guest_session.membership_id,
                TripMembership.trip_id == scope.trip_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            membership is None
            or membership.user_id is not None
            or not is_membership_access_authorized(membership)
        ):
            raise MembershipNotAuthorized("Guest membership is no longer active")
        return LockedActor(
            membership_id=membership.id,
            trip_id=membership.trip_id,
            role=membership.role,
            credential_kind="guest",
            user_id=None,
        )
