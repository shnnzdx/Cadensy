"""Public configuration-admission contract for the future B.3 seam."""

from __future__ import annotations

import pytest

from app.domain.transactions.protected_profile import b3_profile_admission


def test_b3_profile_rejects_mixed_legacy_writer_configuration() -> None:
    result = b3_profile_admission(
        environment="test",
        configured_gates={"CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES": True},
    )

    assert result.admitted is False
    assert "CADENSY_B2D_PROTECTED_GENERATOR_WRITES" in result.missing_gates


def test_b3_profile_accepts_only_explicit_complete_nonproduction_configuration() -> None:
    result = b3_profile_admission(
        environment="test",
        configured_gates={name: True for name in result_required_gates()},
    )

    assert result.admitted is True
    assert result.reason == "complete-protected-profile"


def test_b3_profile_rejects_production_even_when_all_gates_are_configured() -> None:
    result = b3_profile_admission(
        environment="production",
        configured_gates={name: True for name in result_required_gates()},
    )

    assert result.admitted is False
    assert result.reason == "production-disabled"


@pytest.mark.parametrize(
    "missing_gate",
    (
        "CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES",
        "CADENSY_B2D_PROTECTED_GENERATOR_WRITES",
        "CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES",
        "CADENSY_B2E_PROTECTED_SCHEDULER_WRITES",
        "CADENSY_B2E_PROTECTED_ORGANIZER_WRITES",
    ),
)
def test_b3_profile_rejects_each_required_writer_family_when_missing(missing_gate: str) -> None:
    gates = {name: True for name in result_required_gates()}
    gates.pop(missing_gate)

    result = b3_profile_admission(environment="test", configured_gates=gates)

    assert result.admitted is False
    assert result.reason == "incomplete-protected-profile"
    assert missing_gate in result.missing_gates


@pytest.mark.parametrize("environment", (None, "", "production", "prod", "unknown"))
def test_b3_profile_is_fail_closed_for_missing_or_unapproved_environment(environment: str | None) -> None:
    result = b3_profile_admission(
        environment=environment,
        configured_gates={name: True for name in result_required_gates()},
    )

    assert result.admitted is False


def test_b3_profile_rejects_false_and_non_boolean_gate_values_without_enabling_them() -> None:
    gates: dict[str, object] = {name: True for name in result_required_gates()}
    gates["CADENSY_B2E_PROTECTED_ORGANIZER_WRITES"] = "1"
    gates["CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES"] = False

    result = b3_profile_admission(environment="test", configured_gates=gates)  # type: ignore[arg-type]

    assert result.admitted is False
    assert {"CADENSY_B2E_PROTECTED_ORGANIZER_WRITES", "CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES"} <= result.missing_gates


def result_required_gates() -> frozenset[str]:
    # The public result exposes the exact inventory rather than tests copying
    # a private implementation list.
    return b3_profile_admission(environment="test", configured_gates={}).required_gates
