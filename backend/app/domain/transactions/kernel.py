"""PostgreSQL root-lock transaction kernel for future Cadensy mutations.

This module deliberately has no FastAPI dependency and does not activate any
existing route. It owns one fresh Session and one local transaction per command:

    Trip -> credential session -> membership -> Plan

Later phases add ledger and child-row behavior after this root protocol is used by
the relevant writers. No model/provider/network work belongs in this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .. import auth as auth_service
from ...db.models import AuthSession, Base, GuestSession, Plan, Trip, TripMembership


CredentialKind = Literal["account", "guest"]
T = TypeVar("T")


class TransactionKernelError(Exception):
    """Base class for domain-neutral protected-command failures."""


class TripNotFound(TransactionKernelError):
    pass


class MissingPlan(TransactionKernelError):
    pass


class DuplicatePlans(TransactionKernelError):
    pass


class InvalidCredential(TransactionKernelError):
    pass


class MembershipNotAuthorized(TransactionKernelError):
    pass


class InsufficientRole(TransactionKernelError):
    pass


class LockUnavailable(TransactionKernelError):
    pass


class RetriableDeadlock(TransactionKernelError):
    pass


class RetriableSerializationFailure(TransactionKernelError):
    pass


class TransactionIntegrityViolation(TransactionKernelError):
    pass


class TransactionDatabaseFailure(TransactionKernelError):
    pass


class ContextExpired(TransactionKernelError):
    pass


class UnsafeCallbackResult(TransactionKernelError):
    """A command callback attempted to leak transaction-scoped state."""


class SafeCommandResult:
    """Marker base for explicit, detached command-result DTOs.

    Subclasses must be dataclasses. Their fields are checked recursively before
    the transaction can commit, so the marker cannot wrap an ORM object or a
    ``LockedPlanContext``.
    """


@dataclass(frozen=True)
class PreliminaryActorScope:
    """Primitive-only admission facts; never a request-Session ORM object."""

    trip_id: str
    membership_id: str
    credential_kind: CredentialKind
    expected_user_id: str | None = None

    def __post_init__(self) -> None:
        if not self.trip_id or not self.membership_id:
            raise ValueError("trip_id and membership_id are required")
        if self.credential_kind == "account" and not self.expected_user_id:
            raise ValueError("account scope requires expected_user_id")
        if self.credential_kind == "guest" and self.expected_user_id is not None:
            raise ValueError("guest scope must not carry an account user id")


@dataclass(frozen=True)
class CredentialProof:
    """Private, non-serializable token proof that never retains the raw bearer."""

    _token_hash: str = field(repr=False)

    @classmethod
    def from_bearer_token(cls, token: str) -> "CredentialProof":
        if not token:
            raise ValueError("bearer token is required")
        # Kind is established by the API's account-first preliminary
        # authentication, not by bearer spelling. In particular, a valid
        # Account credential is allowed to share the Guest token prefix.
        return cls(_token_hash=auth_service.token_hash(token))


@dataclass(frozen=True)
class LockedActor:
    membership_id: str
    trip_id: str
    role: str
    credential_kind: CredentialKind
    user_id: str | None


@dataclass
class LockedPlanContext:
    """Callback-scoped locked aggregate references.

    The kernel invalidates this object before returning its Session to the pool.
    It is intentionally not a response DTO and has no public Session/commit API.
    """

    actor: LockedActor
    _trip: Trip = field(repr=False)
    _plan: Plan = field(repr=False)
    session_identity: int
    connection_identity: int
    _active: bool = field(default=True, init=False, repr=False)

    @property
    def trip(self) -> Trip:
        self._require_active()
        return self._trip

    @property
    def plan(self) -> Plan:
        self._require_active()
        return self._plan

    def _invalidate(self) -> None:
        self._active = False

    def _require_active(self) -> None:
        if not self._active:
            raise ContextExpired("LockedPlanContext cannot outlive its transaction callback")


class PlanTransactionKernel(Generic[T]):
    """Execute a local command after fresh authorization and canonical root locks."""

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
        operation: Callable[[LockedPlanContext], T],
    ) -> T:
        """Run ``operation`` once in a fresh owned transaction.

        ``operation`` receives no raw bearer, request Session, or commit method.
        Its attached Trip/Plan ORM references are valid only during the callback.
        A normal return commits once; every exception rolls back and closes the
        Session. B.2A classifies retryable database failures but does not retry.
        """

        def run(_session: Session, context: LockedPlanContext) -> T:
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
        operation: Callable[[Session, LockedPlanContext], T],
    ) -> T:
        """Run package-private work in the one owned transaction.

        The public callback API above intentionally never receives ``Session``.
        This hook exists only for tightly reviewed transaction-package
        collaborators such as the durable ledger protocol.
        """

        session = self._session_factory()
        context: LockedPlanContext | None = None
        try:
            with session.begin():
                if self._lock_timeout_ms is not None:
                    session.execute(
                        text("SELECT set_config('lock_timeout', :timeout, true)"),
                        {"timeout": f"{self._lock_timeout_ms}ms"},
                    )
                context = self._acquire_locked_plan(
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

    def _acquire_locked_plan(
        self,
        session: Session,
        *,
        scope: PreliminaryActorScope,
        credential: CredentialProof,
        required_role: str | None,
        lock_all_trip_memberships: bool,
    ) -> LockedPlanContext:
        # Root comes first. Never move a credential, membership, Plan, or child
        # lock ahead of this statement without a separately reviewed protocol.
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

        # The Change Proposal submission path creates an affected-member
        # electorate.  Its private adapter opts into this lock before Plan so
        # that Trip -> credential -> membership -> Plan remains canonical.
        # Existing narrower writers retain their established actor-only scope.
        if lock_all_trip_memberships:
            session.scalars(
                select(TripMembership)
                .where(TripMembership.trip_id == trip.id)
                .order_by(TripMembership.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            ).all()

        # Lock every candidate Plan so duplicates are a controlled failure, not
        # an arbitrary `.first()` choice. B.2A never creates a missing Plan.
        plans = session.scalars(
            select(Plan)
            .where(Plan.trip_id == trip.id)
            .order_by(Plan.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).all()
        if not plans:
            raise MissingPlan("Trip has no authoritative Plan")
        if len(plans) != 1:
            raise DuplicatePlans("Trip has multiple Plans; command is refused")

        connection = session.connection().connection
        return LockedPlanContext(
            actor=actor,
            _trip=trip,
            _plan=plans[0],
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
        # ``scope`` is created from the API's already-validated account-first
        # authentication path. Never reclassify a credential from its prefix.
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
        if membership is None or membership.status == "removed":
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
            or not auth_service.is_active_guest_membership_status(membership.status)
        ):
            raise MembershipNotAuthorized("Guest membership is no longer active")
        return LockedActor(
            membership_id=membership.id,
            trip_id=membership.trip_id,
            role=membership.role,
            credential_kind="guest",
            user_id=None,
        )


def classify_database_error(exc: SQLAlchemyError) -> TransactionKernelError:
    """Classify a failed local PostgreSQL transaction without retrying it."""

    sqlstate = _sqlstate_for(exc)
    if sqlstate == "40P01":
        return RetriableDeadlock("PostgreSQL deadlock detected; retry only a future local command")
    if sqlstate == "40001":
        return RetriableSerializationFailure(
            "PostgreSQL serialization failure; retry only a future local command"
        )
    if sqlstate == "55P03":
        return LockUnavailable("PostgreSQL lock was unavailable before the local timeout")
    if isinstance(exc, IntegrityError):
        return TransactionIntegrityViolation("Local transaction failed an integrity constraint")
    return TransactionDatabaseFailure("Local transaction failed")


def _sqlstate_for(exc: SQLAlchemyError) -> str | None:
    if not isinstance(exc, DBAPIError):
        return None
    original = exc.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def _validate_safe_callback_result(value: object) -> None:
    """Reject Session-owned state before the surrounding transaction commits.

    This intentionally supports only detached scalar values, ordinary response
    containers, and explicit ``SafeCommandResult`` dataclasses. It is a narrow
    ownership boundary, not a general serialization layer.
    """

    _validate_safe_value(value, active_container_ids=set())


def _validate_safe_value(value: object, *, active_container_ids: set[int]) -> None:
    if isinstance(value, (Base, LockedPlanContext)):
        raise UnsafeCallbackResult("Callback result must not expose ORM or locked context state")
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return

    value_id = id(value)
    if value_id in active_container_ids:
        raise UnsafeCallbackResult("Callback result must not contain cyclic containers")

    if isinstance(value, SafeCommandResult):
        if not is_dataclass(value):
            raise UnsafeCallbackResult("SafeCommandResult subclasses must be dataclasses")
        active_container_ids.add(value_id)
        try:
            for descriptor in fields(value):
                _validate_safe_value(
                    getattr(value, descriptor.name), active_container_ids=active_container_ids
                )
        finally:
            active_container_ids.remove(value_id)
        return

    if isinstance(value, (list, tuple)):
        active_container_ids.add(value_id)
        try:
            for item in value:
                _validate_safe_value(item, active_container_ids=active_container_ids)
        finally:
            active_container_ids.remove(value_id)
        return

    if isinstance(value, dict):
        active_container_ids.add(value_id)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise UnsafeCallbackResult("Callback result dictionaries require string keys")
                _validate_safe_value(item, active_container_ids=active_container_ids)
        finally:
            active_container_ids.remove(value_id)
        return

    raise UnsafeCallbackResult("Callback result must be a detached primitive or explicit DTO")
