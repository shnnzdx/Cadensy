"""Detached Place cache/provider composition for the future generator lifecycle.

This module is intentionally dormant from HTTP traffic.  It owns only the
external-work middle of ``Prepare -> External Work -> Validate & Commit`` and
therefore creates neither Plans nor durable outcomes.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from ...agents import planner
from ..places import geoapify, service as place_service
from . import generator
from .generator_lifecycle import (
    DraftCalculation,
    PlannerCapability,
    PreparedGeneration,
    calculate_draft,
)


SessionFactory = Callable[[], Session]
ProviderFetch = Callable[[str], object]

# Re-export the narrow, controlled conditions at the composed lifecycle seam.
ProviderResultInvalid = place_service.ProviderResultInvalid
CachePersistenceFailure = place_service.CachePersistenceFailure


def execute_generator_external_work(
    prepared: PreparedGeneration,
    *,
    session_factory: SessionFactory,
    provider_fetch: ProviderFetch = geoapify.fetch_places,
    planner_call: PlannerCapability = planner.plan_day,
    minimum: int = place_service.DEFAULT_MINIMUM_PLACES,
) -> DraftCalculation:
    """Acquire detached candidates, then invoke the accepted pure B.2D1 draft seam.

    Each cache interaction creates and closes its own Session.  The provider sees
    only the normalized destination string; Planner receives only detached
    ``PlannerPlace`` values.  No Session, lock, ORM row, or raw provider payload
    is allowed to survive into either external call.
    """

    snapshot = place_service.read_planner_cache(
        session_factory,
        prepared.trip.destination,
        minimum=minimum,
    )
    candidates = snapshot.candidates
    candidate_failure_code: str | None = None

    if snapshot.needs_refresh:
        try:
            fetched = provider_fetch(snapshot.destination)
            if getattr(fetched, "destination_found", True) is False:
                raise place_service.DestinationNotFound("Destination was not found")
            values = place_service.validate_geoapify_places(fetched)
        except place_service.DestinationNotFound:
            candidate_failure_code = generator.DESTINATION_NOT_FOUND_CODE
            candidates = ()
        except geoapify.GeoapifyDestinationNotFound:
            candidate_failure_code = generator.DESTINATION_NOT_FOUND_CODE
            candidates = ()
        except geoapify.GeoapifyUnavailable:
            # Legacy uses whatever cache it already has when the provider fails.
            candidates = snapshot.candidates
        else:
            if values:
                persisted = place_service.persist_geoapify_cache(
                    session_factory,
                    snapshot.destination,
                    values,
                    minimum=minimum,
                )
                candidates = persisted.candidates
            # An empty successful result has the same Legacy behavior: retain a
            # stale cache when present, or let B.2D1 return NO_PLACE_CANDIDATES.

    return calculate_draft(
        prepared,
        candidates,
        planner_call=planner_call,
        candidate_failure_code=candidate_failure_code,
    )
