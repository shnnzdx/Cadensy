"""Admission policy for a future, default-off B.3 Preview/Apply surface.

This module deliberately does not select any route or enable a writer.  It
only makes the mixed-profile condition observable so a future B.3 route can
refuse to run unless every relevant writer is already explicitly protected.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping


_NON_PRODUCTION_ENVIRONMENTS = frozenset({"dev", "development", "local", "test", "testing", "ci", "staging"})

# Every listed switch must be explicitly true.  Keeping this inventory here
# avoids a future B.3 request inferring safety from a partial set of routes.
_REQUIRED_GATES = frozenset(
    {
        "CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES",
        "CADENSY_B2C_PROTECTED_BOOKING_WRITES",
        "CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES",
        "CADENSY_B2C_PROTECTED_VOTE_WRITES",
        "CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES",
        "CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES",
        "CADENSY_B2C_PROTECTED_ROUND_SETTLEMENT_WRITES",
        "CADENSY_B2C_PROTECTED_CONSTRAINT_CREATE_WRITES",
        "CADENSY_B2C_PROTECTED_CONSTRAINT_UPDATE_WRITES",
        "CADENSY_B2C_PROTECTED_CONSTRAINT_DELETE_WRITES",
        "CADENSY_B2C_PROTECTED_PREFERENCES_WRITES",
        "CADENSY_B2D_PROTECTED_GENERATOR_WRITES",
        "CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES",
        "CADENSY_B2E_PROTECTED_SCHEDULER_WRITES",
        "CADENSY_B2E_PROTECTED_ORGANIZER_WRITES",
    }
)


@dataclass(frozen=True)
class ProtectedProfileAdmission:
    admitted: bool
    reason: str
    required_gates: frozenset[str]
    missing_gates: frozenset[str]


def b3_profile_admission(
    *, environment: str | None, configured_gates: Mapping[str, bool]
) -> ProtectedProfileAdmission:
    """Return a fail-closed B.3 admission decision without mutating config.

    An environment setting is only a local routing declaration; it is not
    evidence that a deployment fleet is homogeneous.  Production stays denied
    until a separately authorized activation review.
    """

    normalized_environment = (environment or "").strip().lower()
    missing = frozenset(name for name in _REQUIRED_GATES if configured_gates.get(name) is not True)
    if normalized_environment in {"production", "prod"}:
        return ProtectedProfileAdmission(False, "production-disabled", _REQUIRED_GATES, missing)
    if normalized_environment not in _NON_PRODUCTION_ENVIRONMENTS:
        return ProtectedProfileAdmission(False, "environment-not-approved", _REQUIRED_GATES, missing)
    if missing:
        return ProtectedProfileAdmission(False, "incomplete-protected-profile", _REQUIRED_GATES, missing)
    return ProtectedProfileAdmission(True, "complete-protected-profile", _REQUIRED_GATES, frozenset())
