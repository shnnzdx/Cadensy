"""Internal B.2D4 composition for protected Generator validation and commit.

This module intentionally has no route, feature-gate, provider implementation,
or request-Session dependency.  It composes the accepted B.2D1 detached
snapshot, B.2D2 external work, and B.2D3 durable final transaction exactly once.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session, object_session

from ...agents import planner
from ...db.models import Plan, PlanItem
from ..places import geoapify, service as place_service
from ..transactions import CommandResponse, CredentialProof, PreliminaryActorScope
from ..transactions.generator_durable import (
    GeneratorDurableCommandResult,
    GeneratorDurableCoordinator,
    GeneratorExecutionPreconditions,
    GeneratorIdempotentCommand,
    GeneratorMutationContext,
    StaleGeneratorPreconditions,
)
from ..transactions.trip_kernel import LockedTripContext
from . import generator
from .generator_external_work import ProviderFetch, execute_generator_external_work
from .generator_lifecycle import (
    BlockedDraft,
    DraftCalculation,
    GeneratedDraft,
    PlannerCapability,
    PreparedGeneration,
    capture_generation_snapshot,
    prepare_generation,
)


SessionFactory = Callable[[], Session]


class GeneratorDraftInvariantError(RuntimeError):
    """A detached draft cannot be safely represented as a protected Plan write."""


def execute_protected_plan_generation(
    *,
    session_factory: SessionFactory,
    coordinator: GeneratorDurableCoordinator,
    scope: PreliminaryActorScope,
    credential: CredentialProof,
    command: GeneratorIdempotentCommand,
    provider_fetch: ProviderFetch = geoapify.fetch_places,
    planner_call: PlannerCapability = planner.plan_day,
    minimum: int = place_service.DEFAULT_MINIMUM_PLACES,
) -> GeneratorDurableCommandResult:
    """Run ``preflight -> prepare -> external work -> final commit``.

    The replay preflight and final coordinator each own their own short-lived
    Session.  The detached prepare and B.2D2 external-work boundaries never
    receive the final transaction Session, and the final callback never calls a
    provider, planner, commit, or rollback method.
    """

    preflight = coordinator.preflight(scope=scope, credential=credential, command=command)
    if not preflight.is_new:
        assert preflight.result is not None
        return preflight.result

    prepared = prepare_generation(session_factory, scope.trip_id, scope.membership_id)
    if not prepared.generation_eligible:
        # B.2D1 captured an existing authoritative item. Preserve Legacy's
        # no-overwrite rule and avoid expensive B.2D2 work that cannot commit.
        raise generator.PlanAlreadyHasItems("This trip already has plan items")
    draft = execute_generator_external_work(
        prepared,
        session_factory=session_factory,
        provider_fetch=provider_fetch,
        planner_call=planner_call,
        minimum=minimum,
    )
    preconditions = GeneratorExecutionPreconditions.from_prepared(prepared)

    return coordinator.execute(
        scope=scope,
        credential=credential,
        command=command,
        preconditions=preconditions,
        validate_preconditions=lambda session, context, captured: _validate_current_generation(
            session, context, captured, prepared
        ),
        synthetic_mutation=lambda context: _persist_draft(context, prepared, draft),
    )


def _validate_current_generation(
    session: Session,
    context: LockedTripContext,
    captured: GeneratorExecutionPreconditions,
    prepared: PreparedGeneration,
) -> None:
    """Compare the complete B.2D1 projection in the final locked Session."""

    if context.actor.membership_id != prepared.organizer.membership_id:
        raise StaleGeneratorPreconditions("Organizer changed after Generator Prepare")
    current = capture_generation_snapshot(
        session, context.trip.id, context.actor.membership_id
    )
    if current != prepared:
        raise StaleGeneratorPreconditions("Generator planning inputs changed after Prepare")
    if current.planning_input_fingerprint != captured.planning_input_fingerprint:
        raise StaleGeneratorPreconditions("Generator planning fingerprint changed after Prepare")
    if not current.generation_eligible:
        raise StaleGeneratorPreconditions("Plan is no longer eligible for generation")


def _persist_draft(
    context: GeneratorMutationContext,
    prepared: PreparedGeneration,
    draft: DraftCalculation,
) -> CommandResponse:
    """Apply a detached B.2D1 draft through the Coordinator-owned Session only."""

    session = object_session(context.plan)
    if session is None:
        raise GeneratorDraftInvariantError("Generator Plan is detached from its owned Session")
    if isinstance(draft, GeneratedDraft):
        return _persist_generated_draft(session, context.plan, prepared, draft)
    if isinstance(draft, BlockedDraft):
        return _persist_blocked_draft(context.plan, prepared, draft)
    raise GeneratorDraftInvariantError("Generator external work returned an unsupported draft")


def _persist_generated_draft(
    session: Session,
    plan: Plan,
    prepared: PreparedGeneration,
    draft: GeneratedDraft,
) -> CommandResponse:
    if not draft.items:
        raise GeneratorDraftInvariantError("An active Generator draft must contain PlanItems")

    # Reuse the accepted Legacy row/PlanChange mapper; this is deliberately not
    # a second PlanItem-writing algorithm.
    legacy_items = tuple(
        generator.DraftItem(
            day_index=item.day_index,
            day_date=item.day_date,
            start_hour=item.start_hour,
            poi=item.poi,
            generated_by=item.generated_by,
        )
        for item in draft.items
    )
    persisted_items = generator._write_items(session, plan, legacy_items)
    plan.status = "active"
    plan.blocked_reason = None
    plan.currency = prepared.trip.currency
    plan.needs_refresh = False
    plan.estimated_total_per_person = generator._known_item_total(persisted_items)
    session.flush()
    return CommandResponse(
        status=200,
        payload=_safe_outcome(plan, prepared, persisted_items, draft),
        state_changed=True,
    )


def _persist_blocked_draft(
    plan: Plan,
    prepared: PreparedGeneration,
    draft: BlockedDraft,
) -> CommandResponse:
    changed = any(
        (
            plan.status != "blocked",
            plan.blocked_reason != draft.blocked_reason,
            plan.estimated_total_per_person is not None,
            plan.currency != prepared.trip.currency,
        )
    )
    if changed:
        plan.status = "blocked"
        plan.blocked_reason = draft.blocked_reason
        plan.estimated_total_per_person = None
        plan.currency = prepared.trip.currency
    return CommandResponse(
        status=200,
        payload=_safe_outcome(plan, prepared, (), draft),
        state_changed=changed,
    )


def _safe_outcome(
    plan: Plan,
    prepared: PreparedGeneration,
    items: tuple[PlanItem, ...],
    draft: DraftCalculation,
) -> dict[str, object]:
    """Build the B.2D3 codec input only from persisted, safe fields."""

    notes = (
        None
        if draft.planner_note is None
        else [
            {
                "day_index": note.day_index,
                "source": note.source,
                "note": note.note,
                "used_ai": note.used_ai,
            }
            for note in draft.planner_note
        ]
    )
    return {
        "plan_id": plan.id,
        "status": draft.status,
        "days": generator._days_out(prepared.trip.dates, items),
        "blocked_reason": draft.blocked_reason,
        "blocked_code": draft.blocked_code,
        "generated_by": draft.generated_by,
        "used_ai": draft.used_ai,
        "planner_note": notes,
    }
