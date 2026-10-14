"""B.3 revision-bracketed Preview application boundary.

Preview is deliberately a read-only, non-authoritative operation.  It captures
one Plan/Trip revision pair, gives a Runtime only narrowly scoped tools backed
by short independent read Sessions, classifies a proposed patch in a new read
Session, then verifies the same pair again.  No request Session or ORM object
crosses the Runtime boundary.

Apply is intentionally *not* another writer in this module.  A confirmed
candidate has exactly the same domain effect as Change Proposal submission and
therefore composes the existing protected ``plan.change.submit.v1`` writer.
That writer owns fresh authentication, Trip-first locks, locked
reclassification, revision advancement, and durable replay.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
import json
import math
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..agents import base as agent_base
from ..agents.execution import AgentExecutionConfig
from ..agents.legacy_runtime import LegacyRuntimeReadTool
from ..agents.runtime_contract import (
    AgentCandidateOption,
    AgentSuggestedChange,
    ChatAgentRuntime,
    RuntimeHistoryTurn,
    RuntimeReadTool,
    RuntimeRequest,
    RuntimeResult,
)
from ..agents.tools import build_read_only_trip_tools
from ..db.models import Plan, PlanItem, Trip, TripMembership
from ..observability.telemetry import TelemetryContext, TerminalCategory
from ..observability.telemetry import (
    ClassificationOutcome,
    DomainClassificationCompleted,
    FailureCategory,
    emit_event_safely,
)
from . import auth as auth_service
from .constraints.types import Classification
from .decisions import orchestrator as orch


class PreviewError(Exception):
    """Base error for the non-authoritative Preview boundary."""


class PreviewTripNotFound(PreviewError):
    pass


class PreviewAccessDenied(PreviewError):
    pass


class PreviewMissingPlan(PreviewError):
    pass


class PreviewDuplicatePlans(PreviewError):
    pass


class PreviewItemNotFound(PreviewError):
    pass


class StalePreview(PreviewError):
    """Facts changed after capture; never return the old candidate/verdict."""


@dataclass(frozen=True)
class PreviewRequest:
    """Primitive-only application input; it deliberately carries no ORM state."""

    trip_id: str
    actor_membership_id: str
    message: str
    selected_item_id: str | None = None
    history: tuple[RuntimeHistoryTurn, ...] = ()
    telemetry_context: TelemetryContext | None = None


@dataclass(frozen=True)
class PreviewCandidate:
    item_id: str
    item_title: str
    patch: dict[str, object]
    classification: Classification


@dataclass(frozen=True)
class PreviewOption:
    id: str
    label: str
    title: str
    body: str
    tradeoff: str
    item_id: str
    patch: dict[str, object]


@dataclass(frozen=True)
class PreviewResult:
    """Detached B.3 result.  ``authoritative`` is always false by design."""

    reply: str
    plan_id: str
    plan_revision: int
    planning_facts_revision: int
    candidate: PreviewCandidate | None
    candidate_options: tuple[PreviewOption, ...]
    authoritative: bool = False
    # Internal request-observability fact.  The HTTP DTO remains unchanged.
    telemetry_terminal: TerminalCategory = TerminalCategory.SUCCEEDED


@dataclass(frozen=True)
class _CapturedRevisions:
    trip_id: str
    actor_membership_id: str
    plan_id: str
    plan_revision: int
    planning_facts_revision: int


@dataclass(frozen=True)
class _ReadToolDescriptor:
    """Session-free metadata retained by one Runtime-facing read tool."""

    name: str
    description: str
    parameters_json: str
    guard: Callable[[agent_base.AgentRunState, dict[str, Any]], str | None] | None
    cache: bool

    @classmethod
    def from_tool(cls, tool: agent_base.AgentTool) -> "_ReadToolDescriptor":
        # Tool schemas are data. A JSON round-trip deliberately leaves the
        # Session-bound source handler behind before Runtime execution begins.
        return cls(
            name=tool.name,
            description=tool.description,
            parameters_json=json.dumps(tool.parameters, sort_keys=True),
            guard=tool.guard,
            cache=tool.cache,
        )

    def parameters(self) -> dict[str, Any]:
        return json.loads(self.parameters_json)


class RevisionBracketedReadTripCapability:
    """ReadTripCapability with one new bounded Session for every tool call.

    It returns the existing Legacy-shaped tool descriptors so the frozen Legacy
    Runtime keeps its tool/guard semantics.  The Pydantic adapter sees the same
    neutral ``RuntimeReadTool`` surface.  A Runtime never receives a Session,
    an ORM entity, or a generic query capability.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        captured: _CapturedRevisions,
    ) -> None:
        self._session_factory = session_factory
        self._captured = captured
        self._stale = False
        self._legacy_tool_results: tuple[dict[str, Any], ...] = ()

    @property
    def stale(self) -> bool:
        return self._stale

    @property
    def _application_legacy_tool_results(self) -> tuple[dict[str, Any], ...]:
        """Private Legacy provenance retained without widening RuntimeResult."""

        return self._legacy_tool_results

    def _record_legacy_tool_results(self, results: tuple[dict[str, Any], ...]) -> None:
        self._legacy_tool_results = results

    def run_with_read_only_tools(
        self, operation: Callable[[tuple[RuntimeReadTool, ...]], Any]
    ) -> Any:
        # Build only immutable descriptions in a short Session.  Every returned
        # handler opens another Session for its own invocation, so the Runtime
        # never keeps this Session across provider/model latency.
        with self._read_session() as db:
            self._assert_current(db)
            templates = build_read_only_trip_tools(
                db,
                trip_id=self._captured.trip_id,
                actor_membership_id=self._captured.actor_membership_id,
            )
        descriptors = tuple(_ReadToolDescriptor.from_tool(tool) for tool in templates)
        tools = tuple(LegacyRuntimeReadTool(self._fresh_handler_tool(descriptor)) for descriptor in descriptors)
        return operation(tools)

    def _fresh_handler_tool(self, descriptor: _ReadToolDescriptor) -> agent_base.AgentTool:
        def invoke(**arguments: object) -> object:
            try:
                with self._read_session() as db:
                    self._assert_current(db)
                    scoped_tools = build_read_only_trip_tools(
                        db,
                        trip_id=self._captured.trip_id,
                        actor_membership_id=self._captured.actor_membership_id,
                    )
                    tool = next(tool for tool in scoped_tools if tool.name == descriptor.name)
                    return tool.handler(**arguments)
            except StalePreview:
                self._stale = True
                raise

        return agent_base.AgentTool(
            name=descriptor.name,
            description=descriptor.description,
            parameters=descriptor.parameters(),
            handler=invoke,
            guard=descriptor.guard,
            cache=descriptor.cache,
        )

    @contextmanager
    def _read_session(self) -> Iterator[Session]:
        db = self._session_factory()
        try:
            with db.begin():
                # PostgreSQL enforces the read-only claim.  Other SQLAlchemy
                # test dialects retain the same narrow tool surface and never
                # receive a write handler.
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(text("SET TRANSACTION READ ONLY"))
                yield db
        finally:
            db.close()

    def _assert_current(self, db: Session) -> None:
        snapshot = self._captured
        trip = db.get(Trip, snapshot.trip_id)
        if trip is None:
            raise StalePreview("Trip no longer exists")
        membership = db.scalar(
            select(TripMembership).where(
                TripMembership.id == snapshot.actor_membership_id,
                TripMembership.trip_id == snapshot.trip_id,
            )
        )
        if not _is_active_preview_membership(membership):
            raise PreviewAccessDenied("Trip membership is no longer active")
        plans = list(
            db.scalars(
                select(Plan)
                .where(Plan.trip_id == snapshot.trip_id)
                .order_by(Plan.id)
            )
        )
        if len(plans) != 1 or plans[0].id != snapshot.plan_id:
            raise StalePreview("Authoritative Plan changed")
        if (
            plans[0].revision != snapshot.plan_revision
            or trip.planning_facts_revision != snapshot.planning_facts_revision
        ):
            raise StalePreview("Planning facts changed")


class RevisionBracketedPreview:
    """Compose an accepted Runtime with deterministic, bracketed classification."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        runtime: ChatAgentRuntime,
        execution: AgentExecutionConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._runtime = runtime
        self._execution = execution or AgentExecutionConfig(
            request_timeout_seconds=30.0,
            provider_timeout_seconds=20.0,
            tool_timeout_seconds=5.0,
        )

    def preview(self, request: PreviewRequest) -> PreviewResult:
        captured = self._capture(request)
        capability = RevisionBracketedReadTripCapability(
            session_factory=self._session_factory,
            captured=captured,
        )
        execution = self._execution
        if request.telemetry_context is not None:
            try:
                execution = replace(execution, telemetry_context=request.telemetry_context)
            except Exception:
                pass
        runtime_result = self._runtime.run(
            RuntimeRequest(
                message=request.message,
                history=request.history,
                selected_item_ref=request.selected_item_id,
                request_id=(request.telemetry_context.request_id if request.telemetry_context is not None else uuid4().hex),
                # Keep the established Runtime limits; B.3 changes Session
                # lifetime, not PR-04's Runtime contract or timeout policy.
                limits=_runtime_limits(),
            ),
            capability,
            execution=execution,
        )
        if capability.stale:
            raise StalePreview("A scoped tool observed newer planning facts")

        candidate = self._classify_candidate(
            captured, runtime_result, capability, request.telemetry_context
        )
        candidate_options = self._safe_options(
            captured,
            runtime_result.candidate_options,
            capability._application_legacy_tool_results,
            request.telemetry_context,
        )
        self._verify_final(captured)
        return PreviewResult(
            reply=_safe_preview_reply(runtime_result),
            plan_id=captured.plan_id,
            plan_revision=captured.plan_revision,
            planning_facts_revision=captured.planning_facts_revision,
            candidate=candidate,
            candidate_options=candidate_options,
            telemetry_terminal=_preview_terminal(runtime_result),
        )

    def _capture(self, request: PreviewRequest) -> _CapturedRevisions:
        with self._bounded_read() as db:
            trip = db.get(Trip, request.trip_id)
            if trip is None:
                raise PreviewTripNotFound("Trip not found")
            membership = db.scalar(
                select(TripMembership).where(
                    TripMembership.id == request.actor_membership_id,
                    TripMembership.trip_id == request.trip_id,
                )
            )
            if not _is_active_preview_membership(membership):
                raise PreviewAccessDenied("Trip membership is not active")
            plans = list(
                db.scalars(
                    select(Plan).where(Plan.trip_id == request.trip_id).order_by(Plan.id)
                )
            )
            if not plans:
                raise PreviewMissingPlan("Trip has no authoritative Plan")
            if len(plans) != 1:
                raise PreviewDuplicatePlans("Trip has multiple Plans")
            return _CapturedRevisions(
                trip_id=trip.id,
                actor_membership_id=membership.id,
                plan_id=plans[0].id,
                plan_revision=plans[0].revision,
                planning_facts_revision=trip.planning_facts_revision,
            )

    def _classify_candidate(
        self,
        captured: _CapturedRevisions,
        runtime_result: RuntimeResult,
        capability: RevisionBracketedReadTripCapability,
        telemetry_context: TelemetryContext | None = None,
    ) -> PreviewCandidate | None:
        if not isinstance(runtime_result.outcome, AgentSuggestedChange):
            return None
        patch = _safe_patch(runtime_result.outcome.safe_patch)
        if not patch:
            return None
        if not _replacement_provenance_allows(
            patch, capability._application_legacy_tool_results
        ):
            # The common Runtime contract deliberately carries no raw Legacy
            # provider/tool provenance.  A replacement without matching
            # trusted tool evidence therefore fails closed instead of becoming
            # an apparently valid Preview.
            return None
        with self._bounded_read() as db:
            _assert_captured_current(db, captured)
            item = db.scalar(
                select(PlanItem).where(
                    PlanItem.id == runtime_result.outcome.item_ref,
                    PlanItem.plan_id == captured.plan_id,
                    PlanItem.settledness != "removed",
                )
            )
            if item is None:
                raise PreviewItemNotFound("Suggested item is not in the authoritative Plan")
            try:
                classification = orch.classify_change(
                    db, item, patch, captured.actor_membership_id,
                )
            except Exception:
                _emit_classification(telemetry_context, succeeded=False)
                raise
            _emit_classification(telemetry_context, succeeded=True)
            return PreviewCandidate(
                item_id=item.id,
                item_title=item.title,
                patch=dict(patch),
                classification=classification,
            )

    def _safe_options(
        self,
        captured: _CapturedRevisions,
        options: tuple[AgentCandidateOption, ...],
        tool_results: tuple[dict[str, Any], ...],
        telemetry_context: TelemetryContext | None = None,
    ) -> tuple[PreviewOption, ...]:
        """Return only options whose target is still public and Plan-scoped.

        PR-04's neutral Runtime result is suggestion syntax, not a Plan-scope
        proof. Each option therefore receives the same fresh, bounded lookup as
        a selected candidate before it can reach the HTTP DTO.
        """

        safe: list[PreviewOption] = []
        with self._bounded_read() as db:
            _assert_captured_current(db, captured)
            current_items = {
                item.id: item
                for item in db.scalars(
                    select(PlanItem).where(
                        PlanItem.plan_id == captured.plan_id,
                        PlanItem.settledness != "removed",
                    )
                )
            }
            for option in options:
                item = current_items.get(option.item_ref)
                patch = _validated_option_patch(option.safe_patch)
                if item is None or patch is None:
                    continue
                if patch and not _replacement_provenance_allows(patch, tool_results):
                    continue
                if patch:
                    try:
                        orch.classify_change(db, item, patch, captured.actor_membership_id)
                    except Exception:
                        _emit_classification(telemetry_context, succeeded=False)
                        # Candidate options are non-authoritative display data.
                        # An unusable option is omitted without exposing domain
                        # details or poisoning the bounded read transaction.
                        continue
                    _emit_classification(telemetry_context, succeeded=True)
                safe.append(
                    PreviewOption(
                        id=option.id,
                        label=option.label,
                        title=option.title,
                        body=option.body,
                        tradeoff=option.tradeoff,
                        item_id=item.id,
                        patch=dict(patch),
                    )
                )
        return tuple(safe)

    def _verify_final(self, captured: _CapturedRevisions) -> None:
        with self._bounded_read() as db:
            _assert_captured_current(db, captured)

    @contextmanager
    def _bounded_read(self) -> Iterator[Session]:
        db = self._session_factory()
        try:
            with db.begin():
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(text("SET TRANSACTION READ ONLY"))
                yield db
        finally:
            db.close()


def _emit_classification(context: TelemetryContext | None, *, succeeded: bool) -> None:
    if context is None:
        return
    emit_event_safely(lambda: DomainClassificationCompleted(
        context=context,
        classification_outcome=(
            ClassificationOutcome.SUCCEEDED
            if succeeded else ClassificationOutcome.FAILED
        ),
        failure_category=None if succeeded else FailureCategory.DOMAIN_ERROR,
    ))


def _assert_captured_current(db: Session, captured: _CapturedRevisions) -> None:
    trip = db.get(Trip, captured.trip_id)
    if trip is None:
        raise StalePreview("Trip no longer exists")
    membership = db.scalar(
        select(TripMembership).where(
            TripMembership.id == captured.actor_membership_id,
            TripMembership.trip_id == captured.trip_id,
        )
    )
    if not _is_active_preview_membership(membership):
        raise PreviewAccessDenied("Trip membership is no longer active")
    plans = list(
        db.scalars(select(Plan).where(Plan.trip_id == captured.trip_id).order_by(Plan.id))
    )
    if len(plans) != 1 or plans[0].id != captured.plan_id:
        raise StalePreview("Authoritative Plan changed")
    if (
        plans[0].revision != captured.plan_revision
        or trip.planning_facts_revision != captured.planning_facts_revision
    ):
        raise StalePreview("Planning facts changed")


def _is_active_preview_membership(membership: TripMembership | None) -> bool:
    if membership is None:
        return False
    if membership.user_id is not None:
        return membership.status != "removed"
    return auth_service.is_active_guest_membership_status(membership.status)


def _safe_patch(raw: Mapping[str, object]) -> dict[str, object]:
    """Keep the existing Chat suggestion field envelope, detached and strict."""

    allowed = {
        "title",
        "place",
        "start_hour",
        "day_date",
        "duration_min",
        "price_per_person",
        "lat",
        "lng",
    }
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if key not in allowed or value in (None, ""):
            continue
        if key == "day_date" and isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                continue
        normalized[key] = value
    return normalized


def _validated_option_patch(raw: Mapping[str, object]) -> dict[str, object] | None:
    """Validate a Runtime option as all-or-nothing public suggestion syntax."""

    allowed = {
        "title", "place", "start_hour", "day_date", "duration_min",
        "price_per_person", "lat", "lng",
    }
    if not set(raw).issubset(allowed):
        return None
    normalized = _safe_patch(raw)
    if len(normalized) != len(raw):
        return None
    for field, value in normalized.items():
        if field in {"title", "place"}:
            if not isinstance(value, str) or not value.strip():
                return None
        elif field == "day_date":
            if not isinstance(value, date):
                return None
        elif field == "duration_min":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return None
        elif field in {"start_hour", "price_per_person", "lat", "lng"}:
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                return None
    return normalized


def _safe_preview_reply(runtime_result: RuntimeResult) -> str:
    reply = runtime_result.reply.strip()
    if reply:
        return reply
    return (
        "I could not prepare a change preview from the current plan. "
        "No change has been submitted or applied."
    )


def _preview_terminal(runtime_result: RuntimeResult) -> TerminalCategory:
    """Classify only established Preview degradation facts, never reply text."""
    if runtime_result.observation.failure is not None or not runtime_result.reply.strip():
        return TerminalCategory.DEGRADED_FALLBACK
    return TerminalCategory.SUCCEEDED


def _replacement_provenance_allows(
    patch: Mapping[str, object], tool_results: tuple[dict[str, Any], ...]
) -> bool:
    """Retain PR-04's Legacy-only replacement provenance restriction."""

    core_fields = ("title", "place", "lat", "lng")
    replacement_fields = (*core_fields, "price_per_person")
    if not any(field in patch for field in replacement_fields):
        return True
    requested_core = tuple(patch.get(field) for field in core_fields)
    if any(value is None for value in requested_core):
        return False
    candidates: list[Mapping[str, object]] = []
    for result in tool_results:
        if result.get("tool") != "find_replacement_place" or result.get("guard_rejected"):
            continue
        output = result.get("output")
        if not isinstance(output, Mapping):
            continue
        raw_candidates = output.get("candidates")
        if not isinstance(raw_candidates, list):
            continue
        candidates.extend(candidate for candidate in raw_candidates if isinstance(candidate, Mapping))
    for candidate in candidates:
        if tuple(candidate.get(field) for field in core_fields) != requested_core:
            continue
        if "price_per_person" in patch:
            if candidate.get("price_per_person") == patch["price_per_person"]:
                return True
        elif candidate.get("price_per_person") is None:
            return True
    return False


def _runtime_limits():
    # Import locally to keep the type-only Runtime boundary clear at module top.
    from ..agents.runtime_contract import RuntimeLimits

    return RuntimeLimits(max_rounds=8, max_total_tokens=120000)
