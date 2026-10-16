"""HTTP API layer.

This layer is intentionally thin: it accepts parameters, calls domain code, and converts results to JSON.
All rules live in domain/; business decisions should not live here.

Authentication uses verified bearer credentials. ``X-Membership-Id`` is an
object selector only and is never accepted as authentication."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..agents import chat as chat_agent
from ..agents import planner as planner_agent
from ..agents import tools as agent_tools
from ..agents.tools import DAY_END_HOUR, DAY_START_HOUR
from ..db.models import (
    ChangeProposal,
    Conversation,
    ConversationMessage,
    ConversationMessageOption,
    ConversationSubmission,
    DecisionRound,
    InviteLink,
    Plan,
    PlanChange,
    PlanItem,
    PlanItemComment,
    Trip,
    TripMembership,
    UpdateNotice,
    User,
    Vote,
)
from ..db.session import SessionLocal, get_session
from ..domain.access import ForeignTripAccess, ScopedResourceNotFound, for_membership
from ..domain import auth as auth_service
from ..domain.membership_policy import current_participant_membership_clause
from ..domain.chat import service as chat_service
from ..domain.conversations import service as conversation_service
from ..domain.decisions import orchestrator as orch
from ..domain.preview_apply import (
    PreviewAccessDenied,
    PreviewDuplicatePlans,
    PreviewItemNotFound,
    PreviewMissingPlan,
    PreviewTripNotFound,
    StalePreview,
)
from ..domain.decisions import organizer as org_actions
from ..domain.plans import generator as plan_generator
from ..domain.plans import generator_commit as protected_generator_commit
from ..domain.plans import manual_items
from ..domain.places import geoapify
from ..domain.preferences import service as pref_service
from ..domain.trips import service as trip_service
from ..domain.trips import cover_service as trip_cover_service
from ..observability.telemetry import (
    ChatRequestCompleted,
    ChatRequestStarted,
    RouteCategory,
    TelemetryContext,
    TerminalCategory,
    emit_event_safely,
)
from ..domain.transactions import (
    CredentialProof,
    DuplicatePlans,
    FingerprintVersionMismatch,
    GeneratorDurableCoordinator,
    GeneratorIdempotentCommand,
    HmacSigningKey,
    IdempotencyKeyReused,
    IdempotencyRecordExpired,
    InvalidDurableCommand,
    InvalidIdempotencyKey,
    InvalidCredential,
    InsufficientRole,
    LockUnavailable,
    MembershipNotAuthorized,
    MissingPlan,
    PlanRevisionOverflow,
    PreliminaryActorScope,
    RetriableDeadlock,
    RetriableSerializationFailure,
    StalePlanRevision,
    StaleGeneratorPreconditions,
    StaleTripPlanningFactsRevision,
    StaticOutcomeContractRegistry,
    TripNotFound,
    TripPlanningFactsRevisionOverflow,
    TripTransactionKernel,
    TransactionIntegrityViolation,
    TransactionDatabaseFailure,
    UnknownHmacKeyVersion,
    UnsafeDurableOutcome,
    UnsupportedGeneratorOutcome,
    UnsupportedFingerprintVersion,
    UnsupportedOutcomeContract,
    generator_outcome_contract,
)
from ..domain.transactions import manual_plan_item as protected_manual_items
from ..domain.transactions import booking as protected_booking
from ..domain.transactions import change_proposal as protected_changes
from ..domain.transactions import vote as protected_vote
from ..domain.transactions import proposal_decision as protected_proposal_decisions
from ..domain.transactions import notice_objection as protected_notice_objections
from ..domain.transactions import round_settlement as protected_round_settlements
from ..domain.transactions import constraint_create as protected_constraint_create
from ..domain.transactions import constraint_update as protected_constraint_update
from ..domain.transactions import constraint_delete as protected_constraint_delete
from ..domain.transactions import preferences_save as protected_preferences_save
from ..domain.transactions import organizer_actions as protected_organizer_actions
from ..domain.transactions import membership_remove as protected_membership_remove
from ..domain.transactions import membership_join as protected_membership_join
from ..domain.transactions import membership_quarantine as protected_membership_quarantine
from ..domain.transactions.membership_lifecycle import MembershipChangePendingDecision
from ..domain.transactions.protected_profile import b3_profile_admission
from ..domain.transactions.trip_preferences_common import ProtectedConstraintMissing, StaticHmacKeyring as TripStaticHmacKeyring

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
)
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})
_B2C_MANUAL_ITEM_GATE = "CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES"
_B2C_BOOKING_GATE = "CADENSY_B2C_PROTECTED_BOOKING_WRITES"
_B2C_CHANGE_PROPOSAL_GATE = "CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES"
_B2C_VOTE_GATE = "CADENSY_B2C_PROTECTED_VOTE_WRITES"
_B2C_PROPOSAL_DECISION_GATE = "CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES"
_B2C_NOTICE_OBJECTION_GATE = "CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES"
_B2C_ROUND_SETTLEMENT_GATE = "CADENSY_B2C_PROTECTED_ROUND_SETTLEMENT_WRITES"
_B2C_CONSTRAINT_CREATE_GATE = "CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES"
_B2C_CONSTRAINT_UPDATE_GATE = "CADENSY_B2C_PROTECTED_CONSTRAINT_UPDATE_WRITES"
_B2C_CONSTRAINT_DELETE_GATE = "CADENSY_B2C_PROTECTED_CONSTRAINT_DELETE_WRITES"
_B2C_PREFERENCES_GATE = "CADENSY_B2C_PROTECTED_PREFERENCES_WRITES"
_B2C_HMAC_KEY = "CADENSY_B2C_HMAC_KEY"
_B2D_GENERATOR_GATE = "CADENSY_B2D_PROTECTED_GENERATOR_WRITES"
_B2E_ORGANIZER_GATE = "CADENSY_B2E_PROTECTED_ORGANIZER_WRITES"
_B2E_MEMBERSHIP_GATE = "CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES"
_B2E_SCHEDULER_GATE = "CADENSY_B2E_PROTECTED_SCHEDULER_WRITES"
_B3_PREVIEW_APPLY_GATE = "CADENSY_B3_PROTECTED_PREVIEW_APPLY"
_DURABLE_CONVERSATION_RUNTIME_GATE = "CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED"
_B2D_NON_PRODUCTION_ENVIRONMENTS = frozenset(
    {"development", "dev", "local", "test", "testing", "ci", "staging"}
)
_FALSE_GATE_VALUES = frozenset({"", "0", "false", "no", "off"})
_TRUE_GATE_VALUES = frozenset({"1", "true", "yes", "on"})


def parse_cors_origins(raw: str | None = None) -> list[str]:
    value = raw if raw is not None else os.getenv("CORS_ORIGINS", "")
    origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """The settlement scheduler starts with the backend, so no separate process is required."""
    _validate_auth_configuration()
    from ..jobs import scheduler

    task = None
    if os.getenv("DISABLE_SCHEDULER") != "1":
        task = scheduler.start(app)
    yield
    if task:
        task.cancel()


app = FastAPI(title="TripSync API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trip-Facts-Revision", "X-Current-Trip-Facts-Revision"],
)


def _require_scoped_trip(
    db: Session, membership: TripMembership, trip_id: str
) -> Trip:
    try:
        return for_membership(db, membership).require_trip(trip_id)
    except ForeignTripAccess as exc:
        raise HTTPException(403, "This identity belongs to a different trip") from exc
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Trip not found") from exc


def current_membership(
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    x_trip_id: str | None = Header(default=None),
) -> TripMembership:
    """Authenticated trip identity.

    Account bearer credentials select an account, then ``X-Trip-Id`` selects
    that account's membership for the requested trip. Guest bearer support is
    added by PR-01B's bounded credential path. A membership ID is never a
    credential and therefore cannot select an identity on its own.
    """
    token = _bearer_token(authorization)
    if token:
        # Resolve an account session first.  A Guest token has a recognizable
        # prefix for routing/readability, but a randomly generated account
        # token could theoretically share that prefix; a valid account bearer
        # must never be reclassified as Guest solely by its spelling.
        try:
            user = auth_service.user_for_token(db, token)
        except auth_service.AuthRequired as account_error:
            if not auth_service.is_guest_token(token):
                raise HTTPException(401, str(account_error)) from account_error
            try:
                membership = auth_service.membership_for_guest_token(db, token)
            except auth_service.AuthRequired as exc:
                raise HTTPException(401, str(exc)) from exc
            if x_trip_id and x_trip_id != membership.trip_id:
                raise HTTPException(403, "This identity belongs to a different trip")
            return membership

        try:
            return auth_service.membership_for_trip(db, user, x_trip_id)
        except auth_service.TripMembershipRequired as exc:
            raise HTTPException(403, str(exc)) from exc

    raise HTTPException(401, "Login required")


def current_account_user(
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User:
    """Account identity for cross-trip reads and creating the first trip.

    Unlike ``current_membership``, this dependency intentionally works before
    an account belongs to any trip.
    """
    token = _bearer_token(authorization)
    if token:
        try:
            return auth_service.user_for_token(db, token)
        except auth_service.AuthRequired as exc:
            # A Guest bearer is deliberately not an account credential.  Do
            # not inspect browser metadata or header fallbacks after this
            # failure; that would reintroduce the former authorization bypass.
            if auth_service.is_guest_token(token):
                raise HTTPException(401, "Account authentication required") from exc
            raise HTTPException(401, str(exc)) from exc

    raise HTTPException(401, "Login required")


def _validate_auth_configuration() -> None:
    """Fail closed when a production deployment requests retired header auth."""
    environment = os.getenv("APP_ENV", "development").strip().lower()
    legacy_header = os.getenv("DEV_ALLOW_MEMBERSHIP_HEADER", "0").strip().lower()
    if environment in PRODUCTION_ENVIRONMENTS and legacy_header in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "DEV_ALLOW_MEMBERSHIP_HEADER is retired and must not be enabled in production."
        )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _preliminary_actor_scope(membership: TripMembership) -> PreliminaryActorScope:
    """Detach request-auth facts before a protected writer owns its Session."""

    return PreliminaryActorScope(
        trip_id=membership.trip_id,
        membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )


def _b2c_manual_item_gate_enabled() -> bool:
    return _b2c_manual_item_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_manual_item_gate_requested() -> bool:
    return os.getenv(_B2C_MANUAL_ITEM_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_booking_gate_requested() -> bool:
    return os.getenv(_B2C_BOOKING_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_booking_gate_enabled() -> bool:
    return _b2c_booking_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_change_proposal_gate_requested() -> bool:
    return os.getenv(_B2C_CHANGE_PROPOSAL_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_change_proposal_gate_enabled() -> bool:
    return _b2c_change_proposal_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_vote_gate_requested() -> bool:
    return os.getenv(_B2C_VOTE_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_vote_gate_enabled() -> bool:
    return _b2c_vote_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_proposal_decision_gate_requested() -> bool:
    return os.getenv(_B2C_PROPOSAL_DECISION_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_proposal_decision_gate_enabled() -> bool:
    return _b2c_proposal_decision_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_notice_objection_gate_requested() -> bool:
    return os.getenv(_B2C_NOTICE_OBJECTION_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_notice_objection_gate_enabled() -> bool:
    return _b2c_notice_objection_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_round_settlement_gate_requested() -> bool:
    return os.getenv(_B2C_ROUND_SETTLEMENT_GATE, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_round_settlement_gate_enabled() -> bool:
    return _b2c_round_settlement_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2c_trip_writer_gate_requested(gate: str) -> bool:
    return os.getenv(gate, "0").strip().lower() in {"1", "true", "yes", "on"}


def _b2c_trip_writer_gate_enabled(gate: str) -> bool:
    return _b2c_trip_writer_gate_requested(gate) and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS


def _b2d_generator_gate_state() -> str:
    """Classify the sole B.2D Generator gate without opening a DB Session.

    ``off`` deliberately preserves Legacy behavior.  Every non-false malformed
    flag or environment selection fails closed instead of accidentally making a
    protected request eligible.  The route uses this before either auth or a
    request Session can be constructed.
    """

    raw_gate = os.getenv(_B2D_GENERATOR_GATE, "0").strip().lower()
    if raw_gate in _FALSE_GATE_VALUES:
        return "off"
    if raw_gate not in _TRUE_GATE_VALUES:
        return "invalid"
    raw_environment = os.getenv("APP_ENV")
    if raw_environment is None:
        return "invalid"
    environment = raw_environment.strip().lower()
    if environment in PRODUCTION_ENVIRONMENTS:
        return "production"
    if environment not in _B2D_NON_PRODUCTION_ENVIRONMENTS:
        return "invalid"
    return "enabled"


def _b3_preview_apply_gate_state() -> str:
    """Return the effective B.3 admission state without opening a Session.

    The new B.3 switch is intentionally separate from every B.2C/B.2D writer
    flag.  When requested it checks the *effective* protected profile rather
    than treating raw environment strings as evidence that every writer uses
    the same protocol.  Every unexpected value fails closed.
    """

    raw_gate = os.getenv(_B3_PREVIEW_APPLY_GATE, "0").strip().lower()
    if raw_gate in _FALSE_GATE_VALUES:
        return "off"
    if raw_gate not in _TRUE_GATE_VALUES:
        return "invalid"
    environment = os.getenv("APP_ENV")
    if environment is None:
        return "invalid"
    normalized_environment = environment.strip().lower()
    if normalized_environment in PRODUCTION_ENVIRONMENTS:
        return "production"
    configured_gates = {
        _B2C_MANUAL_ITEM_GATE: _b2c_manual_item_gate_enabled(),
        _B2C_BOOKING_GATE: _b2c_booking_gate_enabled(),
        _B2C_CHANGE_PROPOSAL_GATE: _b2c_change_proposal_gate_enabled(),
        _B2C_VOTE_GATE: _b2c_vote_gate_enabled(),
        _B2C_PROPOSAL_DECISION_GATE: _b2c_proposal_decision_gate_enabled(),
        _B2C_NOTICE_OBJECTION_GATE: _b2c_notice_objection_gate_enabled(),
        _B2C_ROUND_SETTLEMENT_GATE: _b2c_round_settlement_gate_enabled(),
        _B2C_CONSTRAINT_CREATE_GATE: _b2c_trip_writer_gate_enabled(_B2C_CONSTRAINT_CREATE_GATE),
        _B2C_CONSTRAINT_UPDATE_GATE: _b2c_trip_writer_gate_enabled(_B2C_CONSTRAINT_UPDATE_GATE),
        _B2C_CONSTRAINT_DELETE_GATE: _b2c_trip_writer_gate_enabled(_B2C_CONSTRAINT_DELETE_GATE),
        _B2C_PREFERENCES_GATE: _b2c_trip_writer_gate_enabled(_B2C_PREFERENCES_GATE),
        _B2D_GENERATOR_GATE: _b2d_generator_gate_state() == "enabled",
        _B2E_MEMBERSHIP_GATE: _b2c_trip_writer_gate_enabled(_B2E_MEMBERSHIP_GATE),
        _B2E_SCHEDULER_GATE: _b2c_trip_writer_gate_enabled(_B2E_SCHEDULER_GATE),
        _B2E_ORGANIZER_GATE: _b2c_trip_writer_gate_enabled(_B2E_ORGANIZER_GATE),
    }
    admission = b3_profile_admission(
        environment=normalized_environment,
        configured_gates=configured_gates,
    )
    return "enabled" if admission.admitted else "incomplete-profile"


# -------------------- Request and response shapes --------------------


class ConversationCreateRequest(BaseModel):
    scope: str = Field(pattern="^(personal|plan_assistant)$")
    context_key: str = Field(min_length=1, max_length=80)


class ConversationSubmitRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12_000)
    selected_plan_item_id: str | None = Field(default=None, max_length=32)


def _conversation_metadata(conversation: Conversation) -> dict:
    return {
        "id": conversation.id,
        "scope": conversation.scope,
        "context_key": conversation.context_key,
        "status": conversation.status,
        "created_at": conversation.created_at,
        "last_qualifying_activity_at": conversation.last_qualifying_activity_at,
        "retention_expires_at": conversation.retention_expires_at,
    }


def _conversation_submission_result(result: conversation_service.SubmissionResult) -> dict:
    return {
        "submission_id": result.submission_id,
        "state": result.state,
        "attempt_number": result.attempt_number,
        "fence": result.fence,
        "lease_expires_at": result.lease_expires_at,
        "assistant_message_id": result.assistant_message_id,
        "replayed": result.replayed,
        "recovery_accepted_attempt_number": result.recovery_accepted_attempt_number,
        "recovery_accepted_fence": result.recovery_accepted_fence,
        "recovery_state": result.recovery_state,
    }


def _durable_conversation_runtime_enabled() -> bool:
    """Allow only an explicit, non-production request-bound Runtime call."""
    requested = os.getenv(_DURABLE_CONVERSATION_RUNTIME_GATE, "0").strip().lower()
    return (
        requested in _TRUE_GATE_VALUES
        and os.getenv("APP_ENV", "development").strip().lower() not in PRODUCTION_ENVIRONMENTS
    )


def _execute_durable_conversation_claim_after_commit(
    result: conversation_service.SubmissionResult,
    *,
    trip_id: str,
    membership_id: str,
    conversation_id: str,
) -> None:
    """Synchronously deliver one committed claim without creating a queue.

    The 202 contract remains an admission response: clients poll the frozen
    submission status resource. Only a newly accepted claim may invoke the
    Runtime, and the coordinator itself writes only fenced durable outcomes.
    """
    if not result.execution_claimed or not _durable_conversation_runtime_enabled():
        return
    try:
        from ..domain.conversations.context import ExecutionIdentity

        chat_service.build_durable_conversation_runtime_coordinator(
            session_factory=SessionLocal
        ).execute(
            identity=ExecutionIdentity(
                trip_id=trip_id,
                owner_membership_id=membership_id,
                conversation_id=conversation_id,
                submission_id=result.submission_id,
                attempt_number=result.attempt_number,
                fence=result.fence,
            ),
            now=conversation_service.utcnow(),
        )
    except Exception:
        # Admission was already committed. Do not turn it into a false runtime
        # success or expose a Provider/composition exception through the API.
        return


def _raise_conversation_error(error: conversation_service.ConversationError) -> None:
    # "not found" is deliberately used for owner, Trip, deletion, expiry and
    # revocation failures so these routes do not disclose private metadata.
    if isinstance(error, conversation_service.ConversationUnavailable):
        raise HTTPException(404, "Conversation not found") from error
    if isinstance(error, conversation_service.IdempotencyConflict):
        raise HTTPException(409, "idempotency_key_conflict") from error
    if isinstance(error, conversation_service.ConversationBusy):
        raise HTTPException(409, "conversation_busy") from error
    if isinstance(error, conversation_service.StaleFence):
        raise HTTPException(409, "stale_submission_fence") from error
    if isinstance(error, conversation_service.ConversationValidationError):
        raise HTTPException(422, "invalid_conversation_request") from error
    raise HTTPException(503, "conversation_state_unavailable") from error


class ChangeOptionIn(BaseModel):
    item_id: str
    label: str | None = None
    title: str
    body: str | None = None
    tradeoff: str | None = None
    start_hour: float | None = None
    day_date: date | None = None
    duration_min: int | None = None


class ChangeRequest(BaseModel):
    title: str | None = None
    local_title: str | None = Field(default=None, max_length=300)
    place: str | None = None
    start_hour: float | None = Field(default=None, ge=0, le=24)
    day_date: date | None = None
    duration_min: int | None = Field(default=None, gt=0)
    price_per_person: float | None = Field(default=None, ge=0)
    remove: bool | None = None
    settledness: str | None = Field(default=None, pattern="^(loose|touched|settled|booked|removed)$")
    # Include new coordinates when replacing a place so the map moves with it.
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    photo_url: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = None
    request: str = ""
    reason: str | None = None
    options: list[ChangeOptionIn] = Field(default_factory=list)

    def patch(self) -> dict:
        nullable_clearable = {"local_title"}
        return {
            k: v
            for k, v in self.model_dump(exclude={"request", "reason", "options"}).items()
            if v is not None or k in nullable_clearable and k in self.model_fields_set
        }


class VoteRequest(BaseModel):
    option_id: str


class BookingRequest(BaseModel):
    booked: bool


class AddPlanItemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    after_item_id: str = Field(min_length=1, max_length=32)
    before_item_id: str = Field(min_length=1, max_length=32)
    start_hour: float | None = Field(default=None, ge=0, le=24)


class DecisionRequest(BaseModel):
    status: str = Field(pattern="^(accepted|declined)$")


class PreferenceRequest(BaseModel):
    preferred_start_date: date | None = None
    preferred_end_date: date | None = None
    available_start_date: date | None = None
    available_end_date: date | None = None
    ideal_budget: float | None = Field(default=None, ge=0)
    maximum_budget: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=1, max_length=8)
    budget_visibility: str = Field(
        default="planning_only", pattern="^(planning_only|organizer|everyone)$"
    )
    travel_style: str | None = None
    top_interests: list[str] = Field(default_factory=list)


class ConstraintRequest(BaseModel):
    # Only six kinds are supported. If it does not fit, say the system cannot protect it.
    kind: str = Field(
        pattern="^(time_window|budget_ceiling|date_range|walk_limit|dietary|avoid_tag)$"
    )
    params: dict = Field(default_factory=dict)
    importance: str = Field(default="required", pattern="^(required|flexible)$")
    original_text: str = ""
    visibility: str = Field(
        default="planning_only", pattern="^(planning_only|organizer|everyone)$"
    )


class ConstraintPatch(BaseModel):
    params: dict | None = None
    importance: str | None = Field(default=None, pattern="^(required|flexible)$")


class TripCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    preferred_start_date: date | None = None
    preferred_end_date: date | None = None
    expected_group_size: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=1, max_length=8)


class ChatTurnCandidateOptionRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=1000)
    tradeoff: str = Field(default="", max_length=1000)
    item_id: str = Field(min_length=1, max_length=120)
    patch: dict = Field(default_factory=dict)


class ChatTurnRequest(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(min_length=1, max_length=1000)
    candidate_options: list[ChatTurnCandidateOptionRequest] = Field(default_factory=list, max_length=10)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    item_id: str | None = None
    history: list[ChatTurnRequest] = Field(default_factory=list, max_length=10)


class _ChatTelemetryResponse(dict):
    """Preserve the public JSON mapping while carrying a typed internal outcome."""

    def __init__(self, payload: dict, terminal: TerminalCategory) -> None:
        super().__init__(payload)
        self.telemetry_terminal = terminal


class ChatFeatureUnavailable(HTTPException):
    """A typed chat feature-gate 503, distinct from runtime execution failure."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=503, detail=detail)


class CommentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class InviteJoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=255)


class AccountInviteJoinRequest(BaseModel):
    """Identity-free: the verified bearer session is the only Account authority."""

    model_config = ConfigDict(extra="forbid")


class MembershipQuarantineRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=40)


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


def _item_out(item: PlanItem) -> dict:
    return {
        "id": item.id,
        "day_index": item.day_index,
        "day_date": item.day_date.isoformat(),
        "start_hour": item.start_hour,
        "duration_min": item.duration_min,
        "title": item.title,
        "local_title": item.local_title,
        "place": item.place,
        "price_per_person": item.price_per_person,
        "source": item.source,
        "settledness": item.settledness,
        "tags": item.tags or [],
        "is_meal": item.is_meal,
        "meal_type": (
            "lunch" if item.is_meal and item.start_hour < 14.0
            else "dinner" if item.is_meal
            else None
        ),
        # The map draws directly from these two numbers; they change when the place changes.
        "coords": [item.lat, item.lng] if item.lat is not None else None,
        # Use null when no image exists; the frontend has a placeholder. Do not invent one here.
        "photoUrl": item.photo_url,
    }


def _planner_note_out(note: plan_generator.DayPlannerNote) -> dict:
    return {
        "day_index": note.day_index,
        "source": note.source,
        "note": note.note,
        "used_ai": note.used_ai,
    }


def _initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (parts[0][:2] if parts else "?").upper()


def _member_name(db: Session, membership: TripMembership) -> str:
    user = db.get(User, membership.user_id) if membership.user_id else None
    return user.name if user else (membership.guest_display_name or "Guest")


def _comment_out(db: Session, comment: PlanItemComment, me: TripMembership) -> dict:
    membership = db.get(TripMembership, comment.trip_membership_id)
    name = _member_name(db, membership) if membership else "Guest"
    return {
        "id": comment.id,
        "plan_item_id": comment.plan_item_id,
        "membership_id": comment.trip_membership_id,
        "name": name,
        "initials": _initials(name),
        "text": comment.body,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "is_me": comment.trip_membership_id == me.id,
    }


def _plan_out(db: Session, trip_id: str) -> dict:
    plan = db.scalar(select(Plan).where(Plan.trip_id == trip_id))
    if plan is None:
        raise HTTPException(404, "No plan yet")
    trip = db.get(Trip, trip_id)
    items = db.scalars(
        select(PlanItem)
        .where(PlanItem.plan_id == plan.id, PlanItem.settledness != "removed")
        .order_by(PlanItem.day_index, PlanItem.start_hour)
    ).all()
    days: dict[int, list] = {}
    for item in items:
        days.setdefault(item.day_index, []).append(_item_out(item))
    return {
        "plan_id": plan.id,
        "status": plan.status,
        "blocked_reason": plan.blocked_reason,
        "needs_refresh": plan.needs_refresh,
        "estimated_total_per_person": plan.estimated_total_per_person,
        "days": [
            {
                "day_index": d,
                "day_date": _canonical_day_date(trip, d, v),
                "items": v,
            }
            for d, v in sorted(days.items())
        ],
    }


def _canonical_day_date(trip: Trip | None, day_index: int, items: list[dict]) -> str | None:
    if trip and trip.preferred_start_date:
        return (trip.preferred_start_date + timedelta(days=day_index - 1)).isoformat()
    return items[0]["day_date"] if items else None


def _outcome_out(outcome: orch.Outcome) -> dict:
    """Convert classification results to JSON.

    findings contain only sanitized wording. The type has no names or raw text, so there is no
    last-minute filtering step to remember."""
    verdict = outcome.classification
    result = _classification_out(verdict)
    result.update(
        {
            "applied": outcome.applied,
            "round_id": outcome.round_id,
            "proposal_id": outcome.proposal_id,
        }
    )
    return result


def _classification_out(verdict) -> dict:
    return {
        "path": verdict.path.value,
        "headline": verdict.headline,
        "detail": verdict.detail,
        "needs_reason": verdict.needs_reason,
        # Return all four checks with hit=true on the matched one so the frontend can explain the path.
        "checks": [
            {
                "id": c.id,
                "label": c.label,
                "hit": c.hit,
                "privateNote": c.private_note,
            }
            for c in verdict.checks
        ],
        "findings": [
            {"code": f.code, "text": f.safe_text, "affected_count": f.affected_count}
            for f in verdict.findings
        ],
    }


def _round_out(db: Session, round_: DecisionRound, me: TripMembership | None = None) -> dict:
    item = db.get(PlanItem, round_.plan_item_id)
    votes = db.scalars(select(Vote).where(Vote.round_id == round_.id)).all()
    total = round_.electorate_count
    tally: dict[str, int] = {}
    for vote in votes:
        tally[vote.option_id] = tally.get(vote.option_id, 0) + 1
    my_vote = None
    if me is not None:
        my_vote = next((vote.option_id for vote in votes if vote.trip_membership_id == me.id), None)
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


def _round_has_all_votes(db: Session, round_: DecisionRound) -> bool:
    item = db.get(PlanItem, round_.plan_item_id)
    trip_id = item.plan.trip_id
    votes = db.scalars(select(Vote).where(Vote.round_id == round_.id)).all()
    total = round_.electorate_count
    if total is None:
        return False
    return total > 0 and len(votes) >= total


def _proposal_out(db: Session, proposal: ChangeProposal, me: TripMembership | None = None) -> dict:
    from ..db.models import ProposalDecision

    decisions = db.scalars(
        select(ProposalDecision)
        .where(ProposalDecision.proposal_id == proposal.id)
        .order_by(ProposalDecision.created_at, ProposalDecision.id)
    ).all()
    my_decision = next(
        (decision for decision in decisions if me is not None and decision.trip_membership_id == me.id),
        None,
    )
    labels = [f"Member {chr(65 + i)}" for i in range(len(decisions))]
    return {
        "id": proposal.id,
        "status": proposal.status,
        "plan_item_id": proposal.plan_item_id,
        "before": proposal.before_json,
        "after": proposal.after_json,
        "my_status": my_decision.status if my_decision is not None else None,
        "can_decide": my_decision is not None and proposal.status == "waiting_affected_members",
        "members": [
            {"label": label, "status": d.status, "is_me": me is not None and d.trip_membership_id == me.id}
            for label, d in zip(labels, decisions)
        ],
        "privacy_note": (
            "Everyone in this conversation is anonymous. "
            "Names and private reasons are never shown."
        ),
    }


# -------------------- Reads --------------------


@app.post("/api/trips/{trip_id}/conversations", status_code=201)
def create_conversation(
    trip_id: str,
    body: ConversationCreateRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        conversation = conversation_service.create_or_discover(
            db, trip_id=trip_id, membership_id=me.id, scope=body.scope, context_key=body.context_key,
            bearer_token=_bearer_token(authorization)
        )
        db.commit()
        return _conversation_metadata(conversation)
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.get("/api/trips/{trip_id}/conversations")
def discover_conversations(
    trip_id: str,
    scope: str | None = None,
    context_key: str | None = None,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        if scope is not None and scope not in {"personal", "plan_assistant"}:
            raise conversation_service.ConversationValidationError()
        rows = conversation_service.list_conversations(
            db, trip_id=trip_id, membership_id=me.id, scope=scope, context_key=context_key,
            bearer_token=_bearer_token(authorization)
        )
        return {"conversations": [_conversation_metadata(row) for row in rows]}
    except conversation_service.ConversationError as error:
        _raise_conversation_error(error)


@app.get("/api/trips/{trip_id}/conversations/{conversation_id}")
def get_conversation(
    trip_id: str,
    conversation_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        conversation = conversation_service.get_conversation(
            db, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id,
            bearer_token=_bearer_token(authorization)
        )
        db.commit()
        return _conversation_metadata(conversation)
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.get("/api/trips/{trip_id}/conversations/{conversation_id}/messages")
def conversation_messages(
    trip_id: str,
    conversation_id: str,
    before: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        rows = conversation_service.history(
            db, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id, before=before, limit=limit,
            bearer_token=_bearer_token(authorization)
        )
        output = []
        for row in rows:
            options = []
            if row.role == "assistant":
                option_rows = db.scalars(
                    select(ConversationMessageOption)
                    .where(ConversationMessageOption.assistant_message_id == row.id)
                    .order_by(ConversationMessageOption.ordinal)
                ).all()
                options = [{"id": option.id, "ordinal": option.ordinal, "title": option.title,
                            "summary": option.summary, "target_plan_item_id": option.target_plan_item_id,
                            "safe_patch": option.safe_patch, "plan_revision": option.plan_revision,
                            "planning_facts_revision": option.planning_facts_revision} for option in option_rows]
            output.append({"id": row.id, "sequence": row.sequence, "role": row.role, "content": row.content,
                           "created_at": row.created_at, "selected_plan_item_id": row.selected_plan_item_id,
                           "options": options})
        db.commit()
        return {"messages": output, "next_before": rows[0].sequence if rows else None}
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.get("/api/trips/{trip_id}/conversations/{conversation_id}/submissions/{submission_id}")
def get_conversation_submission(
    trip_id: str,
    conversation_id: str,
    submission_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        conversation_service.get_conversation(db, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id,
                                              bearer_token=_bearer_token(authorization))
        submission = db.scalar(select(ConversationSubmission).where(
            ConversationSubmission.id == submission_id, ConversationSubmission.conversation_id == conversation_id,
            ConversationSubmission.owner_membership_id == me.id, ConversationSubmission.trip_id == trip_id,
        ))
        if submission is None:
            raise conversation_service.ConversationUnavailable()
        db.commit()
        return _conversation_submission_result(conversation_service.SubmissionResult(
            submission.id, submission.state, submission.attempt_number, submission.fence,
            submission.lease_expires_at, True, submission.assistant_message_id
        ))
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.post("/api/trips/{trip_id}/conversations/{conversation_id}/messages", status_code=202)
def submit_conversation_message(
    trip_id: str,
    conversation_id: str,
    body: ConversationSubmitRequest,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    if idempotency_key is None:
        raise HTTPException(428, "Idempotency-Key is required")
    try:
        result = conversation_service.submit_message(
            db, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id,
            idempotency_key=idempotency_key, text=body.text, selected_plan_item_id=body.selected_plan_item_id,
            bearer_token=_bearer_token(authorization),
        )
        db.commit()
        _execute_durable_conversation_claim_after_commit(
            result, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id
        )
        return _conversation_submission_result(result)
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.post("/api/trips/{trip_id}/conversations/{conversation_id}/submissions/{submission_id}/recover", status_code=202)
def recover_conversation_submission(
    trip_id: str,
    conversation_id: str,
    submission_id: str,
    authorization: str | None = Header(default=None),
    recovery_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    if recovery_key is None:
        raise HTTPException(428, "Idempotency-Key is required")
    try:
        result = conversation_service.recover_submission(
            db, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id,
            submission_id=submission_id, recovery_key=recovery_key, bearer_token=_bearer_token(authorization),
        )
        db.commit()
        _execute_durable_conversation_claim_after_commit(
            result, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id
        )
        return _conversation_submission_result(result)
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.delete("/api/trips/{trip_id}/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    trip_id: str,
    conversation_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> Response:
    _require_scoped_trip(db, me, trip_id)
    try:
        conversation_service.delete_conversation(db, trip_id=trip_id, membership_id=me.id, conversation_id=conversation_id,
                                                bearer_token=_bearer_token(authorization))
        db.commit()
        return Response(status_code=204)
    except conversation_service.ConversationError as error:
        db.rollback()
        _raise_conversation_error(error)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_session)) -> dict:
    try:
        result = auth_service.login(
            db,
            email=body.email,
            password=body.password,
        )
    except auth_service.InvalidCredentials as exc:
        raise HTTPException(401, str(exc)) from exc
    db.commit()
    default_membership = result.memberships[0] if result.memberships else None
    return {
        "token": result.token,
        "user": {
            "id": result.user.id,
            "name": result.user.name,
            "email": result.user.email,
            "initials": _initials(result.user.name),
        },
        "memberships": result.memberships,
        "default_membership": default_membership,
    }


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_session)) -> dict:
    try:
        result = auth_service.register(
            db,
            name=body.name,
            email=body.email,
            password=body.password,
        )
    except auth_service.InvalidRegistration as exc:
        raise HTTPException(422, str(exc)) from exc
    except auth_service.EmailAlreadyRegistered as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {
        "token": result.token,
        "user": {
            "id": result.user.id,
            "name": result.user.name,
            "email": result.user.email,
            "initials": _initials(result.user.name),
        },
        "memberships": result.memberships,
        "default_membership": result.memberships[0] if result.memberships else None,
    }


@app.post("/api/auth/logout")
def logout(
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> dict:
    token = _bearer_token(authorization)
    if token:
        try:
            auth_service.user_for_token(db, token)
        except auth_service.AuthRequired:
            if auth_service.is_guest_token(token):
                auth_service.revoke_guest_token(db, token)
        else:
            auth_service.revoke_token(db, token)
        db.commit()
    return {"ok": True}


@app.get("/api/account")
def get_account(user: User = Depends(current_account_user)) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "initials": _initials(user.name),
    }


@app.get("/api/me")
def get_me(
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    """Who am I. The frontend currentUser comes from here instead of being hard-coded.

    Role belongs to the server-verified membership, not a client supplied
    membership identifier."""
    return trip_service.describe_me(db, me)


@app.get("/api/trips")
def list_trips(
    priority_trip_id: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(current_account_user),
) -> list[dict]:
    try:
        trips = trip_service.list_user_trips(
            db, user, priority_trip_id=priority_trip_id
        )
    except trip_service.GuestTripAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    db.commit()
    return trips


@app.get("/api/trips/{trip_id}")
def get_trip(
    trip_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    trip = _require_scoped_trip(db, me, trip_id)
    result = {
        "id": trip.id,
        "name": trip.name,
        "destination": trip.destination,
        "status": trip.status,
        "preferred_start_date": trip.preferred_start_date.isoformat()
        if trip.preferred_start_date
        else None,
        "preferred_end_date": trip.preferred_end_date.isoformat()
        if trip.preferred_end_date
        else None,
        "member_count": len(
            db.scalars(
                select(TripMembership).where(
                    TripMembership.trip_id == trip_id,
                    current_participant_membership_clause(),
                )
            ).all()
        ),
    }
    result.update(trip_cover_service.trip_cover_out(trip))
    return result


@app.get("/api/trips/{trip_id}/plans/current")
def get_current_plan(
    trip_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    return _plan_out(db, trip_id)


@app.post("/api/plans/{plan_id}/items")
def add_plan_item(
    plan_id: str,
    body: AddPlanItemRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    """Insert a manually named stop between two adjacent current-plan items."""
    if _b2c_manual_item_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected manual-item writes are not available in production")
    if _b2c_manual_item_gate_enabled():
        return _protected_manual_plan_item_add(
            plan_id=plan_id,
            body=body,
            membership=me,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    if not body.title.strip():
        raise HTTPException(422, "A stop name is required")
    try:
        plan = for_membership(db, me).require_plan(plan_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Plan not found") from exc
    try:
        inserted = manual_items.add_manual_plan_item(
            db,
            plan,
            manual_items.ManualPlanItemCommand(
                title=body.title,
                after_item_id=body.after_item_id,
                before_item_id=body.before_item_id,
                start_hour=body.start_hour,
            ),
            lock_children=False,
        )
    except manual_items.ManualPlanItemInvalid as exc:
        raise HTTPException(422, str(exc)) from exc
    except manual_items.ManualPlanItemConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    db.refresh(inserted)
    return _item_out(inserted)


def _protected_manual_plan_item_add(
    *,
    plan_id: str,
    body: AddPlanItemRequest,
    membership: TripMembership,
    authorization: str | None,
    idempotency_key: str | None,
    expected_plan_revision: str | None,
) -> dict:
    """Disabled-by-default B.2C adapter for the real manual-item route.

    This boundary consumes the request Session's membership only to construct
    primitive preliminary scope. The protected command reauthorizes the bearer
    in its own Session before it reads the ledger or mutable business rows.
    """

    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected manual-item writes require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected manual-item writer is not configured")
    scope = PreliminaryActorScope(
        trip_id=membership.trip_id,
        membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )
    try:
        result = protected_manual_items.execute_protected_manual_plan_item_add(
            session_factory=SessionLocal,
            scope=scope,
            credential=CredentialProof.from_bearer_token(token),
            command=protected_manual_items.ProtectedManualPlanItemAdd(
                plan_id=plan_id,
                idempotency_key=idempotency_key,
                expected_plan_revision=revision,
                title=body.title,
                after_item_id=body.after_item_id,
                before_item_id=body.before_item_id,
                start_hour=body.start_hour,
            ),
            keyring=protected_manual_items.StaticHmacKeyring(
                protected_manual_items.HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))
            ),
        )
    except protected_manual_items.ManualPlanMismatch as exc:
        raise HTTPException(404, "Plan not found") from exc
    except manual_items.ManualPlanItemInvalid as exc:
        raise HTTPException(422, str(exc)) from exc
    except manual_items.ManualPlanItemConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion) as exc:
        raise HTTPException(422, str(exc)) from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected manual-item authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected manual-item authorization failed") from exc
    except MissingPlan as exc:
        raise HTTPException(404, "Plan not found") from exc
    except TripNotFound as exc:
        raise HTTPException(404, "Plan not found") from exc
    except DuplicatePlans as exc:
        raise HTTPException(409, "Protected manual-item aggregate is unavailable") from exc
    except (UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected manual-item replay is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected manual-item revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected manual-item command could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected manual-item command could not be completed") from exc
    return result.payload


@app.get("/api/trips/{trip_id}/comments")
def get_trip_comments(
    trip_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> list[dict]:
    """Public group notes for itinerary items in this trip."""
    _require_scoped_trip(db, me, trip_id)
    comments = db.scalars(
        select(PlanItemComment)
        .join(PlanItem, PlanItemComment.plan_item_id == PlanItem.id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(Plan.trip_id == trip_id)
        .order_by(PlanItemComment.created_at, PlanItemComment.id)
    ).all()
    return [_comment_out(db, comment, me) for comment in comments]


@app.get("/api/trips/{trip_id}/updates")
def get_updates(
    trip_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> list[dict]:
    """The trip feed.

    A notice with a recipient is private to that member -- reminders are a
    nudge, not a public callout. Everything else is group-wide and anonymous.
    """
    _require_scoped_trip(db, me, trip_id)
    notices = db.scalars(
        select(UpdateNotice)
        .where(
            UpdateNotice.trip_id == trip_id,
            or_(
                UpdateNotice.recipient_membership_id.is_(None),
                UpdateNotice.recipient_membership_id == me.id,
            ),
        )
        .order_by(UpdateNotice.created_at.desc())
    ).all()
    return [
        {
            "id": n.id,
            "kind": n.kind,
            "title": n.title,
            "body": n.body,
            "can_object": n.can_object,
            "plan_item_id": n.plan_item_id,
        }
        for n in notices
    ]


@app.get("/api/trips/{trip_id}/actions")
def get_trip_actions(
    trip_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    """Open decisions for this trip.

    Polling uses this instead of scraping notices: notices are activity history,
    while rounds/proposals are current actions that may need buttons.
    """
    _require_scoped_trip(db, me, trip_id)
    rounds = db.scalars(
        select(DecisionRound)
        .join(PlanItem, DecisionRound.plan_item_id == PlanItem.id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(Plan.trip_id == trip_id, DecisionRound.status == "open")
        .order_by(DecisionRound.opened_at.desc())
    ).all()
    proposals = db.scalars(
        select(ChangeProposal)
        .join(PlanItem, ChangeProposal.plan_item_id == PlanItem.id)
        .join(Plan, PlanItem.plan_id == Plan.id)
        .where(
            Plan.trip_id == trip_id,
            ChangeProposal.status.in_(("waiting_affected_members", "escalated")),
        )
        .order_by(ChangeProposal.created_at.desc())
    ).all()
    return {
        "rounds": [_round_out(db, round_, me) for round_ in rounds],
        "proposals": [_proposal_out(db, proposal, me) for proposal in proposals],
    }


@app.get("/api/invites/{token}")
def get_invite(token: str, db: Session = Depends(get_session)) -> dict:
    try:
        return trip_service.invite_preview(db, token)
    except trip_service.InviteNotFound as exc:
        raise HTTPException(404, "Invite not found") from exc


def _chat_with_trip_impl(
    trip_id: str,
    body: ChatRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    telemetry_context: TelemetryContext | None = None,
) -> dict:
    """Natural-language trip chat.

    Read-only by design: the agent understands a message, the domain layer runs
    classify(), and the response tells the frontend what it could submit later.
    This endpoint never calls propose_change().
    """
    b3_state = _b3_preview_apply_gate_state()
    if b3_state != "off":
        if b3_state != "enabled":
            raise ChatFeatureUnavailable("Protected Preview is not available")
        # Extract only preliminary primitives while the request Session is
        # live.  Close it before Runtime/provider work so it cannot retain an
        # identity map or transaction through any bounded B.3 read tool.
        if me.trip_id != trip_id:
            raise HTTPException(403, "This membership does not belong to this trip")
        actor_membership_id = me.id
        db.close()
        try:
            preview = chat_service.preview_trip_chat_revision_bracketed(
                session_factory=SessionLocal,
                trip_id=trip_id,
                actor_membership_id=actor_membership_id,
                message=body.message,
                item_id=body.item_id,
                history=tuple(
                    chat_agent.HistoryTurn(
                        role=turn.role,
                        text=turn.text,
                        candidate_options=tuple(
                            chat_agent.HistoryCandidateOption(
                                id=option.id,
                                label=option.label,
                                title=option.title,
                                body=option.body,
                                tradeoff=option.tradeoff,
                                item_id=option.item_id,
                                patch=option.patch,
                            )
                            for option in turn.candidate_options
                        ),
                    )
                    for turn in body.history
                ),
                telemetry_context=telemetry_context,
            )
        except PreviewAccessDenied as exc:
            raise HTTPException(403, "Protected Preview authorization failed") from exc
        except PreviewTripNotFound as exc:
            raise HTTPException(404, "Trip not found") from exc
        except (PreviewItemNotFound, PreviewMissingPlan) as exc:
            raise HTTPException(404, "Preview target is not available") from exc
        except (PreviewDuplicatePlans, StalePreview) as exc:
            raise HTTPException(409, "Preview conflicts with current planning state") from exc

        candidate = None
        if preview.candidate is not None:
            candidate = {
                "item_id": preview.candidate.item_id,
                "item_title": preview.candidate.item_title,
                "patch": preview.candidate.patch,
                "verdict": _classification_out(preview.candidate.classification),
            }
        return _ChatTelemetryResponse({
            "reply": preview.reply,
            "proposed_change": candidate,
            "candidate_options": [
                {
                    "id": option.id,
                    "label": option.label,
                    "title": option.title,
                    "body": option.body,
                    "tradeoff": option.tradeoff,
                    "item_id": option.item_id,
                    "patch": option.patch,
                }
                for option in preview.candidate_options
            ],
            "preview": {
                "plan_id": preview.plan_id,
                "plan_revision": preview.plan_revision,
                "planning_facts_revision": preview.planning_facts_revision,
                "authoritative": False,
            },
        }, preview.telemetry_terminal)

    _require_scoped_trip(db, me, trip_id)
    try:
        result = chat_service.respond_to_trip_chat(
            db,
            trip_id=trip_id,
            membership=me,
            message=body.message,
            item_id=body.item_id,
            history=tuple(
                chat_agent.HistoryTurn(
                    role=turn.role,
                    text=turn.text,
                    candidate_options=tuple(
                        chat_agent.HistoryCandidateOption(
                            id=option.id,
                            label=option.label,
                            title=option.title,
                            body=option.body,
                            tradeoff=option.tradeoff,
                            item_id=option.item_id,
                            patch=option.patch,
                        )
                        for option in turn.candidate_options
                    ),
                )
                for turn in body.history
            ),
            telemetry_context=telemetry_context,
        )
    except chat_service.ChatAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    except (chat_service.ChatTripNotFound, chat_service.ChatItemNotFound) as exc:
        raise HTTPException(404, str(exc)) from exc

    proposed = None
    if result.proposed_change is not None:
        proposed = {
            "item_id": result.proposed_change.item_id,
            "item_title": result.proposed_change.item_title,
            "patch": result.proposed_change.patch,
            "verdict": _classification_out(result.proposed_change.verdict),
        }
    candidate_options = [
        {
            "id": option.id,
            "label": option.label,
            "title": option.title,
            "body": option.body,
            "tradeoff": option.tradeoff,
            "item_id": option.item_id,
            "patch": option.patch,
        }
        for option in result.candidate_options
    ]
    return _ChatTelemetryResponse({
        "reply": result.reply,
        "proposed_change": proposed,
        "candidate_options": candidate_options,
    }, result.telemetry_terminal)


@app.post("/api/trips/{trip_id}/chat")
def chat_with_trip(
    trip_id: str,
    body: ChatRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    """Own exactly one request start and terminal telemetry event."""
    try:
        context = TelemetryContext.new()
    except Exception:
        context = None
    if context is not None:
        emit_event_safely(
            lambda: ChatRequestStarted(context=context, request_path=RouteCategory.TRIP_CHAT)
        )
    terminal = TerminalCategory.UNHANDLED_ERROR
    try:
        response = _chat_with_trip_impl(
            trip_id, body, db=db, me=me, telemetry_context=context,
        )
        terminal = getattr(response, "telemetry_terminal", TerminalCategory.SUCCEEDED)
        return response
    except HTTPException as error:
        terminal = (
            TerminalCategory.FEATURE_UNAVAILABLE
            if isinstance(error, ChatFeatureUnavailable)
            else {
                403: TerminalCategory.ACCESS_DENIED,
                404: TerminalCategory.NOT_FOUND,
                409: TerminalCategory.CONFLICT,
                503: TerminalCategory.RUNTIME_FAILURE,
            }.get(error.status_code, TerminalCategory.UNHANDLED_ERROR)
        )
        raise
    finally:
        if context is not None:
            emit_event_safely(
                lambda: ChatRequestCompleted(context=context, terminal_category=terminal)
            )


def _require_scoped_plan(
    db: Session, membership: TripMembership, plan_id: str
) -> Plan:
    try:
        return for_membership(db, membership).require_plan(plan_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Plan not found") from exc


def _require_scoped_round(
    db: Session, membership: TripMembership, round_id: str
) -> DecisionRound:
    try:
        return for_membership(db, membership).require_round(round_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Round not found") from exc


def _require_scoped_proposal(
    db: Session, membership: TripMembership, proposal_id: str
) -> ChangeProposal:
    try:
        return for_membership(db, membership).require_proposal(proposal_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Proposal not found") from exc


def _require_scoped_plan_item(
    db: Session, membership: TripMembership, item_id: str
) -> PlanItem:
    try:
        return for_membership(db, membership).require_plan_item(item_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Item not found") from exc


def _require_scoped_notice(
    db: Session, membership: TripMembership, notice_id: str
) -> UpdateNotice:
    try:
        return for_membership(db, membership).require_notice(notice_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Nothing to object to") from exc


def _require_scoped_invite(
    db: Session, membership: TripMembership, invite_id: str
) -> InviteLink:
    try:
        return for_membership(db, membership).require_invite(invite_id)
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Invite not found") from exc


def _require_scoped_membership_in_trip(
    db: Session, membership: TripMembership, membership_id: str, trip_id: str
) -> TripMembership:
    try:
        return for_membership(db, membership).require_membership_in_trip(
            membership_id, trip_id
        )
    except ForeignTripAccess as exc:
        raise HTTPException(403, "This identity belongs to a different trip") from exc
    except ScopedResourceNotFound as exc:
        raise HTTPException(404, "Member not found") from exc


def _validated_change_options(
    db: Session,
    current_item: PlanItem,
    actor_membership_id: str,
    options: list[ChangeOptionIn],
) -> list[dict]:
    accepted: list[dict] = []
    trip_id = current_item.plan.trip_id
    for option in options[:5]:
        option_item = db.scalar(
            select(PlanItem)
            .join(Plan, Plan.id == PlanItem.plan_id)
            .where(PlanItem.id == option.item_id, Plan.trip_id == trip_id)
        )
        if option_item is None:
            continue
        # A round settles exactly one item, and settle_round applies the winning
        # patch to that item. An option about a different item would be validated
        # against one item and executed against another, so it cannot be honored.
        if option_item.id != current_item.id:
            continue

        patch = {
            key: value
            for key, value in {
                "start_hour": option.start_hour,
                "day_date": option.day_date,
                "duration_min": option.duration_min,
            }.items()
            if value is not None
        }
        if not patch:
            continue
        if "start_hour" in patch and not (
            DAY_START_HOUR <= patch["start_hour"] <= DAY_END_HOUR
        ):
            continue
        if "duration_min" in patch and patch["duration_min"] <= 0:
            continue

        savepoint = db.begin_nested()
        try:
            orch.classify_change(db, option_item, patch, actor_membership_id)
        except Exception:
            savepoint.rollback()
            continue
        else:
            savepoint.rollback()

        accepted.append(
            {
                "label": _truncate(option.label, 60),
                "title": _truncate(option.title, 60) or "Alternative",
                "body": _truncate(option.body, 200),
                "tradeoff": _truncate(option.tradeoff, 200),
                "patch": patch,
            }
        )
    return accepted


def _truncate(value: str | None, max_length: int) -> str:
    return (value or "")[:max_length]


@app.get("/api/rounds/{round_id}")
def get_round(
    round_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    round_ = _require_scoped_round(db, me, round_id)
    return _round_out(db, round_, me)


@app.get("/api/plans/{plan_id}/changes")
def get_change_log(
    plan_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> list[dict]:
    """Decision log showing how every decision in this trip was made."""
    _require_scoped_plan(db, me, plan_id)
    changes = db.scalars(
        select(PlanChange)
        .where(PlanChange.plan_id == plan_id)
        .order_by(PlanChange.applied_at)
    ).all()
    return [
        {
            "id": c.id,
            "plan_item_id": c.plan_item_id,
            "origin": c.origin,
            "patch": c.patch,
            "reason": c.reason,
            "applied_at": c.applied_at.isoformat(),
        }
        for c in changes
    ]


# -------------------- Writes --------------------


@app.post("/api/trips")
def create_trip(
    body: TripCreateRequest,
    db: Session = Depends(get_session),
    user: User = Depends(current_account_user),
) -> dict:
    try:
        created = trip_service.create_trip(
            db,
            user,
            trip_service.TripCreateData(**body.model_dump()),
        )
    except trip_service.GuestTripAccessDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    db.commit()
    return {
        "id": created.trip.id,
        "name": created.trip.name,
        "destination": created.trip.destination,
        "status": created.trip.status,
        "preferred_start_date": (
            created.trip.preferred_start_date.isoformat()
            if created.trip.preferred_start_date
            else None
        ),
        "preferred_end_date": (
            created.trip.preferred_end_date.isoformat()
            if created.trip.preferred_end_date
            else None
        ),
        "plan_id": created.plan.id,
        # The creator has a different membership in the new trip. Return it so the frontend can switch identity.
        # Otherwise every later call for this trip is rejected because the identity belongs to another trip.
        "membership_id": created.membership.id,
        "member": trip_service.describe_me(db, created.membership),
        **trip_cover_service.trip_cover_out(created.trip),
    }


@app.post("/api/trips/{trip_id}/invite")
def create_invite(
    trip_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        created = trip_service.create_invite(db, trip_id, me)
    except trip_service.OrganizerRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except trip_service.InviteNotFound as exc:
        raise HTTPException(404, "Trip not found") from exc
    db.commit()
    frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return {
        "id": created.invite.id,
        "invite_id": created.invite.id,
        "token": created.token,
        "url": f"{frontend_base}/#/join/{created.token}",
    }


@app.post("/api/invites/{token}/join")
def join_invite(
    token: str,
    body: InviteJoinRequest,
    db: Session = Depends(get_session),
) -> dict:
    if not body.display_name.strip():
        raise HTTPException(422, "Display name is required")
    if body.email is not None:
        raise HTTPException(
            409,
            "account_join_requires_authenticated_session",
        )
    try:
        joined = protected_membership_join.join_guest_invite_under_trip_root(
            session_factory=SessionLocal, token=token, display_name=body.display_name,
        )
    except trip_service.InviteNotFound as exc:
        raise HTTPException(404, "Invite not found") from exc
    except MembershipChangePendingDecision as exc:
        raise HTTPException(409, "membership_change_pending_decision") from exc
    response = {
        "membership_id": joined.membership.id,
        "trip_id": joined.trip_id,
        "role": "participant",
    }
    if joined.guest_session is not None:
        response["guest_token"] = joined.guest_session.token
        response["guest_expires_at"] = joined.guest_session.session.expires_at.isoformat()
    return response


@app.post("/api/invites/{token}/account-join")
def account_join_invite(
    token: str,
    body: AccountInviteJoinRequest,
    user: User = Depends(current_account_user),
    authorization: str | None = Header(default=None),
) -> dict:
    """Join by a verified Account Session; request body supplies no identity."""

    del body
    bearer = _bearer_token(authorization)
    if bearer is None:
        raise HTTPException(401, "Login required")
    try:
        joined = protected_membership_join.join_account_invite_under_trip_root(
            session_factory=SessionLocal,
            token=token,
            account_token=bearer,
            account_user_id=user.id,
        )
    except trip_service.InviteNotFound as exc:
        raise HTTPException(404, "Invite not found") from exc
    except protected_membership_join.LegacyAccountIdentityDenied as exc:
        raise HTTPException(403, "Legacy identity is not an authenticated Account") from exc
    except protected_membership_join.MembershipQuarantined as exc:
        raise HTTPException(409, "Membership is quarantined") from exc
    except protected_membership_join.AccountMembershipJoinDenied as exc:
        raise HTTPException(409, "Account Membership cannot join") from exc
    except MembershipChangePendingDecision as exc:
        raise HTTPException(409, "membership_change_pending_decision") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Account authentication required") from exc
    return {
        "membership_id": joined.membership.id,
        "trip_id": joined.trip_id,
        "role": joined.membership.role,
    }


@app.post("/api/invites/{invite_id}/revoke")
def revoke_invite(
    invite_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    _require_scoped_invite(db, me, invite_id)
    try:
        trip_service.revoke_invite(db, invite_id, me)
    except trip_service.OrganizerRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except trip_service.InviteNotFound as exc:
        raise HTTPException(404, "Invite not found") from exc
    db.commit()
    return {"revoked": True}


@app.delete("/api/trips/{trip_id}/members/{membership_id}")
def remove_membership(
    trip_id: str,
    membership_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_trip_facts_revision: str | None = Header(default=None, alias="X-Expected-Trip-Facts-Revision"),
    response: Response = None,
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        scope, credential, key, revision, keyring = _protected_trip_revision(
            membership=me, authorization=authorization, idempotency_key=idempotency_key,
            expected_trip_facts_revision=expected_trip_facts_revision,
        )
        result = protected_membership_remove.execute_protected_membership_remove(
            session_factory=SessionLocal, scope=scope, credential=credential,
            command=protected_membership_remove.ProtectedMembershipRemove(membership_id, key, revision),
            keyring=keyring,
        )
    except protected_membership_remove.MembershipTargetMissing as exc:
        raise HTTPException(404, "Trip membership not found") from exc
    except MembershipChangePendingDecision as exc:
        raise HTTPException(409, "membership_change_pending_decision") from exc
    except trip_service.MembershipRemovalDenied as exc:
        raise HTTPException(409, "Membership cannot be removed") from exc
    except Exception as exc:
        _protected_trip_error(exc)
    if response is not None:
        response.headers["X-Trip-Facts-Revision"] = str(result.trip_planning_facts_revision)
    return result.payload


@app.post("/api/trips/{trip_id}/members/{membership_id}/quarantine")
def quarantine_membership(
    trip_id: str,
    membership_id: str,
    body: MembershipQuarantineRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_trip_facts_revision: str | None = Header(default=None, alias="X-Expected-Trip-Facts-Revision"),
    response: Response = None,
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    try:
        scope, credential, key, revision, keyring = _protected_trip_revision(
            membership=me,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_trip_facts_revision=expected_trip_facts_revision,
        )
        result = protected_membership_quarantine.execute_protected_membership_quarantine(
            session_factory=SessionLocal,
            scope=scope,
            credential=credential,
            command=protected_membership_quarantine.ProtectedMembershipQuarantine(
                membership_id, body.reason, key, revision
            ),
            keyring=keyring,
        )
    except protected_membership_quarantine.MembershipQuarantineTargetMissing as exc:
        raise HTTPException(404, "Trip membership not found") from exc
    except protected_membership_quarantine.MembershipQuarantineDenied as exc:
        raise HTTPException(409, "Membership cannot be quarantined") from exc
    except Exception as exc:
        _protected_trip_error(exc)
    if response is not None:
        response.headers["X-Trip-Facts-Revision"] = str(result.trip_planning_facts_revision)
    return result.payload


def _generator_result_out(result: plan_generator.GenerationResult) -> dict:
    """Serialize the established Legacy Generator response shape exactly once."""

    return {
        "plan_id": result.plan.id,
        "status": result.status,
        "days": result.days,
        "blocked_reason": result.blocked_reason,
        "blocked_code": result.blocked_code,
        "generated_by": result.generated_by,
        "used_ai": result.used_ai,
        "planner_note": (
            [_planner_note_out(note) for note in result.planner_note]
            if result.planner_note is not None
            else None
        ),
    }


def _generate_trip_plan_legacy(
    *, trip_id: str, authorization: str | None, x_trip_id: str | None
) -> dict:
    """Retain the historical request-Session/commit path while the gate is off."""

    db = SessionLocal()
    try:
        me = current_membership(
            db=db, authorization=authorization, x_trip_id=x_trip_id
        )
        _require_scoped_trip(db, me, trip_id)
        try:
            result = plan_generator.generate_plan(db, trip_id, me)
        except plan_generator.OrganizerRequired as exc:
            raise HTTPException(403, str(exc)) from exc
        except plan_generator.OrganizerPreferencesRequired as exc:
            raise HTTPException(422, str(exc)) from exc
        except plan_generator.PlanAlreadyHasItems as exc:
            raise HTTPException(409, str(exc)) from exc
        except plan_generator.TripNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        db.commit()
        return _generator_result_out(result)
    finally:
        db.close()


def _protected_generator_scope(
    *, trip_id: str, authorization: str | None, x_trip_id: str | None
) -> tuple[PreliminaryActorScope, CredentialProof]:
    """Resolve only primitive, trusted Generator admission facts.

    Account-first bearer resolution remains in ``current_membership``.  The
    short preliminary Session is always closed before replay preflight,
    detached Prepare, and especially provider/planner execution.  The final
    coordinator transaction repeats authoritative credential, membership, and
    organizer authorization under the Trip root lock.
    """

    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    try:
        db = SessionLocal()
        try:
            membership = current_membership(
                db=db, authorization=authorization, x_trip_id=x_trip_id
            )
            if membership.trip_id != trip_id:
                raise HTTPException(403, "This identity belongs to a different trip")
            scope = PreliminaryActorScope(
                trip_id=trip_id,
                membership_id=membership.id,
                credential_kind="account" if membership.user_id is not None else "guest",
                expected_user_id=membership.user_id,
            )
            return scope, CredentialProof.from_bearer_token(token)
        finally:
            db.close()
    except SQLAlchemyError as exc:
        raise HTTPException(
            503, "Protected Generator authentication is temporarily unavailable"
        ) from exc


def _generator_protected_coordinator() -> GeneratorDurableCoordinator:
    """Create the one reviewed B.2D3 coordinator from protected-only config."""

    key_material = os.getenv(_B2C_HMAC_KEY)
    if key_material is None or not key_material.strip():
        raise HTTPException(503, "Protected Generator is not configured")
    try:
        key = HmacSigningKey("b2c-local-v1", key_material.encode("utf-8", "strict"))
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(503, "Protected Generator is not configured") from exc
    return GeneratorDurableCoordinator(
        TripTransactionKernel(SessionLocal),
        TripStaticHmacKeyring(key),
        StaticOutcomeContractRegistry((generator_outcome_contract(),)),
    )


def _generator_http_provider_fetch(destination: str):
    """Narrow test seam; production delegates to the established Geoapify adapter."""

    return geoapify.fetch_places(destination)


def _generator_http_planner_call(payload):
    """Narrow test seam; production delegates to the established planner adapter."""

    return planner_agent.plan_day(payload)


def _protected_generator_error(exc: Exception) -> None:
    """Map only reviewed protected-command failures without exposing internals."""

    if isinstance(exc, (InvalidIdempotencyKey, InvalidDurableCommand)):
        raise HTTPException(422, "Protected Generator command is invalid") from exc
    if isinstance(
        exc,
        (
            StaleGeneratorPreconditions,
            plan_generator.PlanAlreadyHasItems,
            DuplicatePlans,
            IdempotencyKeyReused,
            IdempotencyRecordExpired,
            FingerprintVersionMismatch,
            UnknownHmacKeyVersion,
            UnsupportedFingerprintVersion,
            UnsupportedOutcomeContract,
            UnsupportedGeneratorOutcome,
            UnsafeDurableOutcome,
            PlanRevisionOverflow,
            MissingPlan,
        ),
    ):
        raise HTTPException(409, "Protected Generator command conflicts with current state") from exc
    if isinstance(exc, InvalidCredential):
        raise HTTPException(401, "Protected Generator authorization failed") from exc
    if isinstance(exc, (MembershipNotAuthorized, InsufficientRole, plan_generator.OrganizerRequired)):
        raise HTTPException(403, "Protected Generator authorization failed") from exc
    if isinstance(exc, (TripNotFound, plan_generator.TripNotFound)):
        raise HTTPException(404, "Trip not found") from exc
    if isinstance(exc, plan_generator.OrganizerPreferencesRequired):
        raise HTTPException(422, "The organizer must submit preferences before generating a plan") from exc
    if isinstance(
        exc,
        (
            LockUnavailable,
            RetriableDeadlock,
            RetriableSerializationFailure,
            TransactionIntegrityViolation,
            TransactionDatabaseFailure,
        ),
    ):
        raise HTTPException(503, "Protected Generator could not be completed safely") from exc
    raise exc


def _generate_trip_plan_protected(
    *,
    trip_id: str,
    authorization: str | None,
    x_trip_id: str | None,
    idempotency_key: str | None,
) -> dict:
    if idempotency_key is None:
        raise HTTPException(428, "Protected Generator requires an Idempotency-Key")
    try:
        command = GeneratorIdempotentCommand(
            idempotency_key=idempotency_key,
            fingerprint_version=1,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
    except (InvalidIdempotencyKey, InvalidDurableCommand) as exc:
        raise HTTPException(422, "Protected Generator command is invalid") from exc
    coordinator = _generator_protected_coordinator()
    scope, credential = _protected_generator_scope(
        trip_id=trip_id, authorization=authorization, x_trip_id=x_trip_id
    )
    try:
        result = protected_generator_commit.execute_protected_plan_generation(
            session_factory=SessionLocal,
            coordinator=coordinator,
            scope=scope,
            credential=credential,
            command=command,
            provider_fetch=_generator_http_provider_fetch,
            planner_call=_generator_http_planner_call,
        )
    except Exception as exc:
        _protected_generator_error(exc)
    return result.payload


@app.post("/api/trips/{trip_id}/plans/generate")
def generate_trip_plan(
    trip_id: str,
    authorization: str | None = Header(default=None),
    x_trip_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Run Legacy by default, or the frozen B.2D lifecycle when explicitly gated."""

    gate_state = _b2d_generator_gate_state()
    if gate_state == "off":
        return _generate_trip_plan_legacy(
            trip_id=trip_id, authorization=authorization, x_trip_id=x_trip_id
        )
    if gate_state == "production":
        raise HTTPException(503, "Protected Generator is not available in production")
    if gate_state == "invalid":
        raise HTTPException(503, "Protected Generator configuration is unavailable")
    return _generate_trip_plan_protected(
        trip_id=trip_id,
        authorization=authorization,
        x_trip_id=x_trip_id,
        idempotency_key=idempotency_key,
    )


@app.post("/api/plans/items/{item_id}/classify")
def classify_only(
    item_id: str,
    body: ChangeRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    """Dry-run only, no execution. Private AI chat uses this for impact checks.

    It is cheap and fast because classification does not use AI."""
    item = _require_scoped_plan_item(db, me, item_id)
    savepoint = db.begin_nested()
    try:
        outcome = orch.propose_change(
            db, item, body.patch(), me.id, request=body.request, reason="dry run"
        )
        return _outcome_out(outcome)
    except orch.AlreadyPending as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        savepoint.rollback()


@app.post("/api/plans/items/{item_id}/changes")
def submit_change(
    item_id: str,
    body: ChangeRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    """Submit a real change. Classification and execution happen together."""
    b3_state = _b3_preview_apply_gate_state()
    if b3_state != "off":
        if b3_state != "enabled":
            raise HTTPException(503, "Protected Apply is not available")
        # B.3 Apply intentionally composes the existing protected Change
        # Proposal command.  Extract scope before closing the request Session;
        # the writer then opens and owns its fresh Trip-first transaction.
        scope = _preliminary_actor_scope(me)
        db.close()
        return _protected_submit_change(
            item_id=item_id,
            body=body,
            scope=scope,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    if _b2c_change_proposal_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected change writes are not available in production")
    if _b2c_change_proposal_gate_enabled():
        return _protected_submit_change(
            item_id=item_id,
            body=body,
            scope=_preliminary_actor_scope(me),
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    item = _require_scoped_plan_item(db, me, item_id)
    alternatives = _validated_change_options(db, item, me.id, body.options)
    try:
        outcome = orch.propose_change(
            db,
            item,
            body.patch(),
            me.id,
            request=body.request,
            reason=body.reason,
            alternatives=alternatives,
        )
    except orch.ReasonRequired as exc:
        raise HTTPException(422, str(exc)) from exc
    except orch.AlreadyPending as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _outcome_out(outcome)


def _protected_submit_change(
    *,
    item_id: str,
    body: ChangeRequest,
    scope: PreliminaryActorScope,
    authorization: str | None,
    idempotency_key: str | None,
    expected_plan_revision: str | None,
) -> dict:
    """Default-off adapter for the existing Change Proposal submission route.

    The request Session contributes only primitive preliminary identity facts.
    The protected command opens a fresh Session, then reauthorizes before its
    durable ledger lookup and locked state-machine classification.
    """

    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected change writes require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected change writer is not configured")
    try:
        result = protected_changes.execute_protected_change_proposal(
            session_factory=SessionLocal,
            scope=scope,
            credential=CredentialProof.from_bearer_token(token),
            command=protected_changes.ProtectedChangeProposal(
                item_id=item_id,
                idempotency_key=idempotency_key,
                expected_plan_revision=revision,
                patch=body.patch(),
                request=body.request,
                reason=body.reason,
                alternatives=tuple(option.model_dump() for option in body.options),
            ),
            keyring=protected_changes.StaticHmacKeyring(
                protected_changes.HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))
            ),
        )
    except protected_changes.ChangeProposalItemMissing as exc:
        raise HTTPException(404, "Plan item not found") from exc
    except protected_changes.ChangeProposalCommandInvalid as exc:
        raise HTTPException(422, "Protected change command is invalid") from exc
    except orch.ReasonRequired as exc:
        raise HTTPException(422, str(exc)) from exc
    except orch.AlreadyPending as exc:
        raise HTTPException(409, str(exc)) from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, "Protected change command conflicts with current state") from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion) as exc:
        raise HTTPException(422, "Protected change command is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected change authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected change authorization failed") from exc
    except (MissingPlan, TripNotFound) as exc:
        raise HTTPException(404, "Plan item not found") from exc
    except (DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected change command is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected change revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected change command could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected change command could not be completed") from exc
    return result.payload


@app.get("/api/plans/items/{item_id}/replacement-places")
def search_replacement_places(
    item_id: str,
    q: str = "",
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    item = _require_scoped_plan_item(db, me, item_id)
    keywords = [part for part in re.split(r"[\s,]+", q.strip()) if part][:8]
    result = agent_tools._find_replacement_place(
        db,
        item.plan.trip_id,
        item.id,
        keywords,
    )
    if result.get("error") == "item_not_found":
        raise HTTPException(404, result.get("message") or "Item not found")
    candidates = (result.get("candidates") or [])[:12]
    return {
        "item_id": item.id,
        "query": q,
        "candidates": candidates,
    }


@app.post("/api/plans/items/{item_id}/comments")
def add_item_comment(
    item_id: str,
    body: CommentRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    item = _require_scoped_plan_item(db, me, item_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Comment cannot be empty")
    comment = PlanItemComment(
        plan_item_id=item.id,
        trip_membership_id=me.id,
        body=text,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _comment_out(db, comment, me)


@app.patch("/api/plans/items/{item_id}/booking")
def set_item_booking(
    item_id: str,
    body: BookingRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    if _b2c_booking_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected booking writes are not available in production")
    if _b2c_booking_gate_enabled():
        return _protected_item_booking(
            item_id=item_id,
            body=body,
            membership=me,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    item = _require_scoped_plan_item(db, me, item_id)
    try:
        updated = orch.set_booking_status(db, item, me, body.booked)
    except orch.WrongTrip as exc:
        raise HTTPException(403, str(exc)) from exc
    db.commit()
    db.refresh(updated)
    return _item_out(updated)


def _protected_item_booking(
    *,
    item_id: str,
    body: BookingRequest,
    membership: TripMembership,
    authorization: str | None,
    idempotency_key: str | None,
    expected_plan_revision: str | None,
) -> dict:
    """Default-off adapter for the existing booking writer.

    The dependency performed ordinary Account-first/Guest preliminary
    authentication. Only primitive scope facts cross into the fresh owned
    command Session, which reauthorizes and locks them again.
    """

    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected booking writes require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected booking writer is not configured")
    scope = PreliminaryActorScope(
        trip_id=membership.trip_id,
        membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )
    try:
        result = protected_booking.execute_protected_booking(
            session_factory=SessionLocal,
            scope=scope,
            credential=CredentialProof.from_bearer_token(token),
            command=protected_booking.ProtectedBooking(
                item_id=item_id,
                idempotency_key=idempotency_key,
                expected_plan_revision=revision,
                booked=body.booked,
            ),
            keyring=protected_booking.StaticHmacKeyring(
                protected_booking.HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))
            ),
        )
    except (protected_booking.BookingItemMissing, protected_booking.BookingPlanMismatch) as exc:
        raise HTTPException(404, "Plan item not found") from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, "Protected booking command conflicts with current state") from exc
    except (
        InvalidIdempotencyKey,
        InvalidDurableCommand,
        UnsupportedFingerprintVersion,
    ) as exc:
        raise HTTPException(422, "Protected booking command is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected booking authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected booking authorization failed") from exc
    except (MissingPlan, TripNotFound) as exc:
        raise HTTPException(404, "Plan item not found") from exc
    except (DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected booking command is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected booking revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected booking command could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected booking command could not be completed") from exc
    return result.payload


@app.post("/api/updates/{notice_id}/object")
def object_to_notice(
    notice_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    """Clicking "I have a different idea" on a notice escalates it to a vote."""
    if _b2c_notice_objection_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected notice objections are not available in production")
    if _b2c_notice_objection_gate_enabled():
        return _protected_notice_objection(
            notice_id=notice_id,
            membership=me,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    notice = _require_scoped_notice(db, me, notice_id)
    if not notice.can_object:
        raise HTTPException(404, "Nothing to object to")
    try:
        outcome = orch.object_to_notice(db, notice)
    except orch.AlreadyPending as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _outcome_out(outcome)


def _protected_notice_objection(
    *,
    notice_id: str,
    membership: TripMembership,
    authorization: str | None,
    idempotency_key: str | None,
    expected_plan_revision: str | None,
) -> dict:
    """Default-off adapter for the established Notice objection state machine."""

    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected notice objections require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected notice objection writer is not configured")
    scope = PreliminaryActorScope(
        trip_id=membership.trip_id,
        membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )
    try:
        result = protected_notice_objections.execute_protected_notice_objection(
            session_factory=SessionLocal,
            scope=scope,
            credential=CredentialProof.from_bearer_token(token),
            command=protected_notice_objections.ProtectedNoticeObjection(
                notice_id=notice_id,
                idempotency_key=idempotency_key,
                expected_plan_revision=revision,
            ),
            keyring=protected_notice_objections.StaticHmacKeyring(
                protected_notice_objections.HmacSigningKey(
                    "b2c-local-v1", key_material.encode("utf-8")
                )
            ),
        )
    except protected_notice_objections.NoticeObjectionNoticeMissing as exc:
        raise HTTPException(404, "Notice not found") from exc
    except protected_notice_objections.NoticeObjectionIneligible as exc:
        raise HTTPException(404, "Nothing to object to") from exc
    except orch.AlreadyPending as exc:
        raise HTTPException(409, "Notice objection conflicts with current decision") from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, "Protected notice objection conflicts with current state") from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion) as exc:
        raise HTTPException(422, "Protected notice objection is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected notice objection authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected notice objection authorization failed") from exc
    except (MissingPlan, TripNotFound) as exc:
        raise HTTPException(404, "Notice not found") from exc
    except (DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected notice objection is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected notice objection revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected notice objection could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected notice objection could not be completed") from exc
    return result.payload


@app.post("/api/rounds/{round_id}/votes")
def vote(
    round_id: str,
    body: VoteRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    if _b2c_vote_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected vote writes are not available in production")
    if _b2c_vote_gate_enabled():
        return _protected_round_vote(
            round_id=round_id,
            body=body,
            membership=me,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    round_ = _require_scoped_round(db, me, round_id)
    if round_.status != "open":
        raise HTTPException(404, "Round is not open")
    try:
        orch.cast_vote(db, round_, me.id, body.option_id)
    except orch.InvalidVoteOption as exc:
        raise HTTPException(422, "Vote option is invalid for this round") from exc
    if _round_has_all_votes(db, round_):
        orch.settle_round(db, round_)
    db.commit()
    return _round_out(db, round_, me)


def _protected_round_vote(
    *,
    round_id: str,
    body: VoteRequest,
    membership: TripMembership,
    authorization: str | None,
    idempotency_key: str | None,
    expected_plan_revision: str | None,
) -> dict:
    """Default-off protected adapter for the legacy Round vote route.

    The request Session contributes only primitive preliminary scope facts. A
    fresh owned Session reauthorizes and locks before the durable ledger and
    authoritative vote/settlement state machine are reached.
    """

    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected vote writes require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected vote writer is not configured")
    scope = PreliminaryActorScope(
        trip_id=membership.trip_id,
        membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )
    try:
        result = protected_vote.execute_protected_vote(
            session_factory=SessionLocal,
            scope=scope,
            credential=CredentialProof.from_bearer_token(token),
            command=protected_vote.ProtectedVote(
                round_id=round_id,
                idempotency_key=idempotency_key,
                expected_plan_revision=revision,
                option_id=body.option_id,
            ),
            keyring=protected_vote.StaticHmacKeyring(
                protected_vote.HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))
            ),
        )
    except protected_vote.VoteRoundMissing as exc:
        raise HTTPException(404, "Round not found") from exc
    except protected_vote.VoteRoundNotOpen as exc:
        raise HTTPException(404, "Round is not open") from exc
    except protected_vote.VoteCommandInvalid as exc:
        raise HTTPException(422, "Protected vote command is invalid") from exc
    except orch.InvalidVoteOption as exc:
        raise HTTPException(422, "Vote option is invalid for this round") from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, "Protected vote command conflicts with current state") from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion) as exc:
        raise HTTPException(422, "Protected vote command is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected vote authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected vote authorization failed") from exc
    except (MissingPlan, TripNotFound) as exc:
        raise HTTPException(404, "Round not found") from exc
    except (DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected vote command is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected vote revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected vote command could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected vote command could not be completed") from exc
    return result.payload


@app.post("/api/rounds/{round_id}/settle")
def settle(
    round_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    """Manual settlement. Real usage is driven by the scheduler calling settle_due_rounds; this endpoint is for demos."""
    if _b2c_round_settlement_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected round settlement is not available in production")
    if _b2c_round_settlement_gate_enabled():
        return _protected_round_settlement(round_id=round_id, membership=me, authorization=authorization, idempotency_key=idempotency_key, expected_plan_revision=expected_plan_revision)
    round_ = _require_scoped_round(db, me, round_id)
    try:
        orch.settle_round(db, round_)
    except orch.LegacyElectorateUnavailable as exc:
        raise HTTPException(409, "legacy_round_electorate_unavailable") from exc
    db.commit()
    return _round_out(db, round_, me)


def _protected_round_settlement(*, round_id: str, membership: TripMembership, authorization: str | None, idempotency_key: str | None, expected_plan_revision: str | None) -> dict:
    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected round settlement requires idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0: raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None: raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material: raise HTTPException(503, "Protected round settlement writer is not configured")
    scope = PreliminaryActorScope(trip_id=membership.trip_id, membership_id=membership.id, credential_kind="account" if membership.user_id is not None else "guest", expected_user_id=membership.user_id)
    try:
        result = protected_round_settlements.execute_protected_round_settlement(session_factory=SessionLocal, scope=scope, credential=CredentialProof.from_bearer_token(token), command=protected_round_settlements.ProtectedRoundSettlement(round_id, idempotency_key, revision), keyring=protected_round_settlements.StaticHmacKeyring(protected_round_settlements.HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))))
    except protected_round_settlements.RoundSettlementRoundMissing as exc:
        raise HTTPException(404, "Round not found") from exc
    except orch.LegacyElectorateUnavailable as exc:
        raise HTTPException(409, "legacy_round_electorate_unavailable") from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, "Protected round settlement conflicts with current state") from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion) as exc:
        raise HTTPException(422, "Protected round settlement is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected round settlement authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected round settlement authorization failed") from exc
    except (MissingPlan, TripNotFound) as exc:
        raise HTTPException(404, "Round not found") from exc
    except (DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected round settlement is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected round revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected round settlement could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected round settlement could not be completed") from exc
    return result.payload


@app.post("/api/proposals/{proposal_id}/decisions")
def decide(
    proposal_id: str,
    body: DecisionRequest,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    if _b2c_proposal_decision_gate_requested() and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected proposal decisions are not available in production")
    if _b2c_proposal_decision_gate_enabled():
        return _protected_proposal_decision(
            proposal_id=proposal_id,
            body=body,
            membership=me,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expected_plan_revision=expected_plan_revision,
        )
    proposal = _require_scoped_proposal(db, me, proposal_id)
    applied = orch.decide_proposal(db, proposal, me.id, body.status)
    db.commit()
    result = _proposal_out(db, proposal, me)
    result["applied"] = applied
    return result


def _protected_proposal_decision(
    *,
    proposal_id: str,
    body: DecisionRequest,
    membership: TripMembership,
    authorization: str | None,
    idempotency_key: str | None,
    expected_plan_revision: str | None,
) -> dict:
    """Default-off protected adapter for the legacy Proposal Decision route."""

    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected proposal decisions require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected proposal decision writer is not configured")
    scope = PreliminaryActorScope(
        trip_id=membership.trip_id,
        membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )
    try:
        result = protected_proposal_decisions.execute_protected_proposal_decision(
            session_factory=SessionLocal,
            scope=scope,
            credential=CredentialProof.from_bearer_token(token),
            command=protected_proposal_decisions.ProtectedProposalDecision(
                proposal_id=proposal_id,
                idempotency_key=idempotency_key,
                expected_plan_revision=revision,
                status=body.status,
            ),
            keyring=protected_proposal_decisions.StaticHmacKeyring(
                protected_proposal_decisions.HmacSigningKey(
                    "b2c-local-v1", key_material.encode("utf-8")
                )
            ),
        )
    except protected_proposal_decisions.ProposalDecisionProposalMissing as exc:
        raise HTTPException(404, "Proposal not found") from exc
    except protected_proposal_decisions.ProposalDecisionCommandInvalid as exc:
        raise HTTPException(422, "Protected proposal decision is invalid") from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired) as exc:
        raise HTTPException(409, "Protected proposal decision conflicts with current state") from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion) as exc:
        raise HTTPException(422, "Protected proposal decision is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected proposal decision authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected proposal decision authorization failed") from exc
    except (MissingPlan, TripNotFound) as exc:
        raise HTTPException(404, "Proposal not found") from exc
    except (DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome) as exc:
        raise HTTPException(409, "Protected proposal decision is unavailable") from exc
    except PlanRevisionOverflow as exc:
        raise HTTPException(409, "Protected proposal revision cannot advance") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure) as exc:
        raise HTTPException(503, "Protected proposal decision could not acquire a safe transaction") from exc
    except (TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected proposal decision could not be completed") from exc
    return result.payload


@app.get("/api/proposals/{proposal_id}")
def get_proposal(
    proposal_id: str,
    db: Session = Depends(get_session),
    me: TripMembership = Depends(current_membership),
) -> dict:
    """Proposal detail. **Other members are always anonymous**: current user is You, others are Member A/B/C."""
    proposal = _require_scoped_proposal(db, me, proposal_id)
    return _proposal_out(db, proposal, me)


# -------------------- Preferences and six constraint kinds --------------------
#
# There is no "someone else" slot in the route; reading others would require changing the URL design.
# This is blocked by shape, not just by an authorization check.


def _protected_trip_revision(
    *,
    membership: TripMembership,
    authorization: str | None,
    idempotency_key: str | None,
    expected_trip_facts_revision: str | None,
):
    if not idempotency_key or expected_trip_facts_revision is None:
        raise HTTPException(428, "Protected writes require idempotency and revision headers")
    try:
        revision = int(expected_trip_facts_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Trip facts revision must be an integer") from exc
    if revision < 0 or revision > 9_223_372_036_854_775_807:
        raise HTTPException(422, "Expected Trip facts revision is invalid")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected writer is not configured")
    return (
        PreliminaryActorScope(
            trip_id=membership.trip_id,
            membership_id=membership.id,
            credential_kind="account" if membership.user_id is not None else "guest",
            expected_user_id=membership.user_id,
        ),
        CredentialProof.from_bearer_token(token),
        idempotency_key,
        revision,
        TripStaticHmacKeyring(HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))),
    )


def _protected_trip_error(exc: Exception) -> None:
    if isinstance(exc, pref_service.NotYours | ProtectedConstraintMissing):
        raise HTTPException(404, "Constraint not found") from exc
    if isinstance(exc, (pref_service.UnknownConstraintKind, pref_service.InvalidConstraintParams, pref_service.PreferenceDateOutOfTripRange, InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion, ValueError)):
        raise HTTPException(422, "Protected command is invalid") from exc
    if isinstance(exc, StaleTripPlanningFactsRevision):
        error = HTTPException(409, "Protected command conflicts with current Trip facts")
        error.headers = {"X-Current-Trip-Facts-Revision": str(exc.current)}
        raise error from exc
    if isinstance(exc, (IdempotencyKeyReused, IdempotencyRecordExpired, DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome, TripPlanningFactsRevisionOverflow)):
        raise HTTPException(409, "Protected command is unavailable") from exc
    if isinstance(exc, InvalidCredential):
        raise HTTPException(401, "Protected command authorization failed") from exc
    if isinstance(exc, MembershipNotAuthorized):
        raise HTTPException(403, "Protected command authorization failed") from exc
    if isinstance(exc, TripNotFound):
        raise HTTPException(404, "Trip not found") from exc
    if isinstance(exc, (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure, TransactionIntegrityViolation, TransactionDatabaseFailure)):
        raise HTTPException(503, "Protected command could not be completed safely") from exc
    raise exc


@app.get("/api/trips/{trip_id}/preferences/me")
def get_my_preferences(
    trip_id: str,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    response: Response = None,
) -> dict:
    trip = _require_scoped_trip(db, me, trip_id)
    if response is not None:
        response.headers["X-Trip-Facts-Revision"] = str(trip.planning_facts_revision)
    return pref_service.read_mine(db, me)


@app.put("/api/trips/{trip_id}/preferences/me")
def save_my_preferences(
    trip_id: str,
    body: PreferenceRequest,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_trip_facts_revision: str | None = Header(default=None, alias="X-Expected-Trip-Facts-Revision"),
    response: Response = None,
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    if _b2c_trip_writer_gate_requested(_B2C_PREFERENCES_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected preferences writes are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2C_PREFERENCES_GATE):
        try:
            scope, credential, key, revision, keyring = _protected_trip_revision(
                membership=me, authorization=authorization, idempotency_key=idempotency_key,
                expected_trip_facts_revision=expected_trip_facts_revision,
            )
            result = protected_preferences_save.execute_protected_preferences_save(
                session_factory=SessionLocal, scope=scope, credential=credential,
                command=protected_preferences_save.ProtectedPreferencesSave(key, revision, body.model_dump()), keyring=keyring,
            )
        except Exception as exc:
            _protected_trip_error(exc)
        if response is not None:
            response.headers["X-Trip-Facts-Revision"] = str(result.trip_planning_facts_revision)
        return result.payload
    try:
        result = pref_service.save_mine(
            db, me, pref_service.PreferenceData(**body.model_dump())
        )
    except pref_service.PreferenceDateOutOfTripRange as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return result


@app.post("/api/trips/{trip_id}/constraints")
def add_my_constraint(
    trip_id: str,
    body: ConstraintRequest,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_trip_facts_revision: str | None = Header(default=None, alias="X-Expected-Trip-Facts-Revision"),
    response: Response = None,
) -> dict:
    _require_scoped_trip(db, me, trip_id)
    if _b2c_trip_writer_gate_requested(_B2C_CONSTRAINT_CREATE_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected constraint writes are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2C_CONSTRAINT_CREATE_GATE):
        try:
            scope, credential, key, revision, keyring = _protected_trip_revision(membership=me, authorization=authorization, idempotency_key=idempotency_key, expected_trip_facts_revision=expected_trip_facts_revision)
            result = protected_constraint_create.execute_protected_constraint_create(
                session_factory=SessionLocal, scope=scope, credential=credential,
                command=protected_constraint_create.ProtectedConstraintCreate(key, revision, **body.model_dump()), keyring=keyring,
            )
        except Exception as exc:
            _protected_trip_error(exc)
        if response is not None:
            response.headers["X-Trip-Facts-Revision"] = str(result.trip_planning_facts_revision)
        return result.payload
    try:
        row, conflicts = pref_service.add_constraint(db, me, **body.model_dump())
    except (
        pref_service.UnknownConstraintKind,
        pref_service.InvalidConstraintParams,
    ) as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {"id": row.id, "conflicts": conflicts}


@app.patch("/api/constraints/{constraint_id}")
def patch_my_constraint(
    constraint_id: str,
    body: ConstraintPatch,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_trip_facts_revision: str | None = Header(default=None, alias="X-Expected-Trip-Facts-Revision"),
    response: Response = None,
) -> dict:
    if _b2c_trip_writer_gate_requested(_B2C_CONSTRAINT_UPDATE_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected constraint writes are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2C_CONSTRAINT_UPDATE_GATE):
        try:
            scope, credential, key, revision, keyring = _protected_trip_revision(membership=me, authorization=authorization, idempotency_key=idempotency_key, expected_trip_facts_revision=expected_trip_facts_revision)
            result = protected_constraint_update.execute_protected_constraint_update(
                session_factory=SessionLocal, scope=scope, credential=credential,
                command=protected_constraint_update.ProtectedConstraintUpdate(constraint_id, key, revision, body.model_dump(exclude_unset=True)), keyring=keyring,
            )
        except Exception as exc:
            _protected_trip_error(exc)
        if response is not None:
            response.headers["X-Trip-Facts-Revision"] = str(result.trip_planning_facts_revision)
        return result.payload
    try:
        row, conflicts = pref_service.update_constraint(
            db, me, constraint_id, params=body.params, importance=body.importance
        )
    except pref_service.InvalidConstraintParams as exc:
        raise HTTPException(422, str(exc)) from exc
    except pref_service.NotYours as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return {"id": row.id, "conflicts": conflicts}


@app.delete("/api/constraints/{constraint_id}")
def delete_my_constraint(
    constraint_id: str,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_trip_facts_revision: str | None = Header(default=None, alias="X-Expected-Trip-Facts-Revision"),
    response: Response = None,
) -> dict:
    if _b2c_trip_writer_gate_requested(_B2C_CONSTRAINT_DELETE_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected constraint writes are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2C_CONSTRAINT_DELETE_GATE):
        try:
            scope, credential, key, revision, keyring = _protected_trip_revision(membership=me, authorization=authorization, idempotency_key=idempotency_key, expected_trip_facts_revision=expected_trip_facts_revision)
            result = protected_constraint_delete.execute_protected_constraint_delete(
                session_factory=SessionLocal, scope=scope, credential=credential,
                command=protected_constraint_delete.ProtectedConstraintDelete(constraint_id, key, revision), keyring=keyring,
            )
        except Exception as exc:
            _protected_trip_error(exc)
        if response is not None:
            response.headers["X-Trip-Facts-Revision"] = str(result.trip_planning_facts_revision)
        return result.payload
    try:
        pref_service.delete_constraint(db, me, constraint_id)
    except pref_service.NotYours as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return {"deleted": True}


@app.get("/api/trips/{trip_id}/members")
def list_members(
    trip_id: str,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
) -> dict:
    """Who is in this trip and whether they submitted preferences. **Only submission status, never content.**"""
    _require_scoped_trip(db, me, trip_id)
    return pref_service.list_members(db, me)


# ————————————————————— Organizer actions —————————————————————
#
# All three maintain the shared frame. None of them decides for anyone else.


class DeadlockRequest(BaseModel):
    # Organizer exits never accept the blocked proposal for another traveler.
    # `remove` is retained only as the internal legacy audit origin of `clear`.
    action: str = Field(pattern="^(keep|split|clear)$")


def _organizer_error(exc: Exception) -> HTTPException:
    if isinstance(exc, org_actions.OrganizerOnly):
        return HTTPException(403, str(exc))
    if isinstance(exc, org_actions.TooSoonToRemind):
        return HTTPException(429, str(exc))
    if isinstance(exc, org_actions.AlreadyExtended):
        return HTTPException(409, str(exc))
    return HTTPException(404, str(exc))


@app.post("/api/trips/{trip_id}/members/{membership_id}/remind")
def remind_member(
    trip_id: str,
    membership_id: str,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
) -> dict:
    _require_scoped_membership_in_trip(db, me, membership_id, trip_id)
    try:
        notice = org_actions.remind(db, me, membership_id)
    except Exception as exc:
        raise _organizer_error(exc) from exc
    db.commit()
    return {"sent": True, "notice_id": notice.id}


@app.post("/api/rounds/{round_id}/extend")
def extend_round(
    round_id: str,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    if _b2c_trip_writer_gate_requested(_B2E_ORGANIZER_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected organizer actions are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2E_ORGANIZER_GATE):
        return _protected_organizer_action(
            operation_code=protected_organizer_actions.ROUND_EXTEND_OPERATION,
            resource_id=round_id, action=None, membership=me, authorization=authorization,
            idempotency_key=idempotency_key, expected_plan_revision=expected_plan_revision,
        )
    _require_scoped_round(db, me, round_id)
    try:
        round_ = org_actions.extend_round(db, me, round_id)
    except Exception as exc:
        raise _organizer_error(exc) from exc
    db.commit()
    return {"id": round_.id, "deadline": round_.deadline.isoformat()}


@app.post("/api/proposals/{proposal_id}/escalate")
def escalate_proposal(
    proposal_id: str,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    """Any affected member can escalate, not just the proposer."""
    if _b2c_trip_writer_gate_requested(_B2E_ORGANIZER_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected organizer actions are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2E_ORGANIZER_GATE):
        return _protected_organizer_action(
            operation_code=protected_organizer_actions.PROPOSAL_ESCALATE_OPERATION,
            resource_id=proposal_id, action=None, membership=me, authorization=authorization,
            idempotency_key=idempotency_key, expected_plan_revision=expected_plan_revision,
        )
    _require_scoped_proposal(db, me, proposal_id)
    try:
        proposal = org_actions.escalate(db, me, proposal_id)
    except Exception as exc:
        raise _organizer_error(exc) from exc
    db.commit()
    return {"id": proposal.id, "status": proposal.status}


@app.post("/api/proposals/{proposal_id}/deadlock")
def resolve_deadlock(
    proposal_id: str,
    body: DeadlockRequest,
    me: TripMembership = Depends(current_membership),
    db: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    expected_plan_revision: str | None = Header(default=None, alias="X-Expected-Plan-Revision"),
) -> dict:
    if _b2c_trip_writer_gate_requested(_B2E_ORGANIZER_GATE) and os.getenv("APP_ENV", "development").strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise HTTPException(503, "Protected organizer actions are not available in production")
    if _b2c_trip_writer_gate_enabled(_B2E_ORGANIZER_GATE):
        return _protected_organizer_action(
            operation_code=protected_organizer_actions.PROPOSAL_DEADLOCK_OPERATION,
            resource_id=proposal_id, action=body.action, membership=me, authorization=authorization,
            idempotency_key=idempotency_key, expected_plan_revision=expected_plan_revision,
        )
    _require_scoped_proposal(db, me, proposal_id)
    try:
        item = org_actions.resolve_deadlock(db, me, proposal_id, body.action)
    except Exception as exc:
        raise _organizer_error(exc) from exc
    db.commit()
    return {"item_id": item.id, "title": item.title, "action": body.action}


def _protected_organizer_action(
    *, operation_code: str, resource_id: str, action: str | None,
    membership: TripMembership, authorization: str | None,
    idempotency_key: str | None, expected_plan_revision: str | None,
) -> dict:
    if not idempotency_key or expected_plan_revision is None:
        raise HTTPException(428, "Protected organizer actions require idempotency and revision headers")
    try:
        revision = int(expected_plan_revision)
    except ValueError as exc:
        raise HTTPException(422, "Expected Plan revision must be an integer") from exc
    if revision < 0:
        raise HTTPException(422, "Expected Plan revision must be non-negative")
    token = _bearer_token(authorization)
    if token is None:
        raise HTTPException(401, "Login required")
    key_material = os.getenv(_B2C_HMAC_KEY)
    if not key_material:
        raise HTTPException(503, "Protected organizer writer is not configured")
    scope = PreliminaryActorScope(
        trip_id=membership.trip_id, membership_id=membership.id,
        credential_kind="account" if membership.user_id is not None else "guest",
        expected_user_id=membership.user_id,
    )
    try:
        result = protected_organizer_actions.execute_protected_organizer_action(
            session_factory=SessionLocal, scope=scope, credential=CredentialProof.from_bearer_token(token),
            command=protected_organizer_actions.ProtectedOrganizerAction(
                operation_code, resource_id, idempotency_key, revision, action
            ),
            keyring=TripStaticHmacKeyring(HmacSigningKey("b2c-local-v1", key_material.encode("utf-8"))),
        )
    except protected_organizer_actions.OrganizerResourceMissing as exc:
        raise HTTPException(404, "Organizer action target not found") from exc
    except org_actions.OrganizerOnly as exc:
        raise HTTPException(403, "Organizer authorization required") from exc
    except (org_actions.NothingToDo, org_actions.AlreadyExtended) as exc:
        raise HTTPException(409, "Organizer action conflicts with current state") from exc
    except (StalePlanRevision, IdempotencyKeyReused, IdempotencyRecordExpired, DuplicatePlans, UnsupportedOutcomeContract, UnknownHmacKeyVersion, UnsafeDurableOutcome, PlanRevisionOverflow) as exc:
        raise HTTPException(409, "Protected organizer action is unavailable") from exc
    except (InvalidIdempotencyKey, InvalidDurableCommand, UnsupportedFingerprintVersion, ValueError) as exc:
        raise HTTPException(422, "Protected organizer action is invalid") from exc
    except InvalidCredential as exc:
        raise HTTPException(401, "Protected organizer authorization failed") from exc
    except MembershipNotAuthorized as exc:
        raise HTTPException(403, "Protected organizer authorization failed") from exc
    except (LockUnavailable, RetriableDeadlock, RetriableSerializationFailure, TransactionIntegrityViolation, TransactionDatabaseFailure) as exc:
        raise HTTPException(503, "Protected organizer action could not be completed safely") from exc
    return result.payload
