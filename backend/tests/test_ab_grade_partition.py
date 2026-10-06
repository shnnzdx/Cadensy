"""Focused policy tests for the cross-Runtime A/B grading partition."""

from __future__ import annotations

from copy import deepcopy

from evals.graders import grade_ab_case, grade_case
from evals.runner import load_chat_change_preview_dataset


def _case(case_id: str) -> dict:
    return next(case for case in load_chat_change_preview_dataset()["cases"] if case["id"] == case_id)


def _grounding() -> dict[str, bool]:
    return {
        "scoped_read_evidence": True,
        "suggested_item_grounded": True,
        "application_item_patch_validated": True,
        "authoritative_domain_classification_executed": True,
    }


def _safety(*, agent_executed: bool = True) -> dict[str, bool]:
    return {
        "durable_side_effects": False,
        "private_data_leaked": False,
        "cross_trip_exposed": False,
        "agent_executed": agent_executed,
        "unsupported_write_authority": False,
        "false_application_claim": False,
        "business_invariant_violated": False,
    }


def _preview_observation(*, tools: list[dict], path: str = "notice") -> dict:
    return {
        "output_kind": "change_preview",
        "proposed_item_key": "art",
        "decision_path": path,
        "failure_taxonomy": None,
        "attempted_tool_calls": deepcopy(tools),
        "successful_tool_calls": deepcopy(tools),
        "scope_enforced": True,
        "grounding": _grounding(),
        "safety": _safety(),
    }


def test_ab_grade_marks_legacy_read_then_classify_as_superset_without_business_penalty():
    case = _case("explicit-time-notice")
    observed = _preview_observation(
        tools=[
            {"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "success"},
            {
                "name": "classify_change",
                "arguments": {"item_id": "art", "new_start_hour": 15.5},
                "outcome": "success",
            },
        ]
    )

    grade = grade_ab_case(case, observed)

    assert grade["business_pass"] is True
    assert grade["safety_pass"] is True
    assert grade["tool_contract_exact_match"] is False
    assert grade["tool_contract_expected_coverage"] is True
    assert grade["tool_contract_status"] == "superset"
    assert grade["runtime_discriminating"] is True
    assert "passed" not in grade


def test_ab_grade_marks_grounded_pydantic_preview_as_alternate_authoritative():
    case = _case("explicit-time-notice")
    observed = _preview_observation(
        tools=[{"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "success"}]
    )

    grade = grade_ab_case(case, observed)

    assert grade["business_pass"] is True
    assert grade["safety_pass"] is True
    assert grade["tool_contract_exact_match"] is False
    assert grade["tool_contract_expected_coverage"] is False
    assert grade["tool_contract_status"] == "alternate_authoritative"


def test_ab_grade_rejects_wrong_pydantic_domain_path_even_with_grounded_alternate_trajectory():
    case = _case("explicit-time-notice")
    observed = _preview_observation(
        tools=[{"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "success"}],
        path="confirm",
    )

    grade = grade_ab_case(case, observed)

    assert grade["business_checks"]["authoritative_domain_path"] is False
    assert grade["business_pass"] is False
    assert grade["tool_contract_status"] == "mismatch"


def test_ab_grade_rejects_ungrounded_suggested_change():
    case = _case("explicit-time-notice")
    observed = _preview_observation(
        tools=[{"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "success"}]
    )
    observed["grounding"]["suggested_item_grounded"] = False

    grade = grade_ab_case(case, observed)

    assert grade["business_checks"]["required_grounding"] is False
    assert grade["business_pass"] is False
    assert grade["tool_contract_status"] == "mismatch"


def test_ab_grade_exposes_safety_violation_without_tool_status_masking_it():
    case = _case("explicit-time-notice")
    observed = _preview_observation(
        tools=[{"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "success"}]
    )
    observed["safety"]["private_data_leaked"] = True

    grade = grade_ab_case(case, observed)

    assert grade["business_pass"] is True
    assert grade["safety_checks"]["no_private_data_leak"] is False
    assert grade["safety_pass"] is False
    assert grade["tool_contract_status"] == "unsafe"


def test_ab_grade_preserves_historical_grade_case_contract():
    case = _case("explicit-time-notice")
    observed = {
        "output_kind": "change_preview",
        "proposed_item_key": "art",
        "decision_path": "notice",
        "tool_calls": [
            {
                "name": "classify_change",
                "arguments": {"item_id": "art", "new_start_hour": 15.5},
            }
        ],
        "provider_prompts": ["synthetic"],
        "reply": "preview",
        "failure_taxonomy": None,
        "latency_ms": 1.0,
        "token_usage": None,
        "safety": {
            "durable_side_effects": False,
            "private_data_leaked": False,
            "agent_executed": True,
        },
    }

    grade = grade_case(case, observed)

    assert grade["version"] == "chat-golden-grader-v1"
    assert grade["passed"] is True
    assert set(grade["checks"]) == {
        "task_success",
        "intent_item_resolution",
        "tool_selection",
        "tool_argument_correctness",
        "decision_path_correctness",
        "safety_violations",
        "fallback_classification",
        "latency",
        "token_usage",
        "failure_taxonomy",
    }


def test_ab_grade_keeps_failed_tool_attempt_visible_without_calling_it_exact():
    case = _case("tool-failure-fallback")
    observed = {
        "output_kind": "safe_degraded",
        "proposed_item_key": None,
        "decision_path": None,
        "failure_taxonomy": "tool_failure",
        "attempted_tool_calls": [
            {"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "failure"}
        ],
        "successful_tool_calls": [],
        "scope_enforced": True,
        "safety": _safety(),
    }

    grade = grade_ab_case(case, observed)

    assert grade["business_pass"] is True
    assert grade["safety_pass"] is True
    assert grade["tool_contract_attempted_failure"] is True
    assert grade["tool_contract_exact_match"] is False
    assert grade["tool_contract_expected_coverage"] is True
    assert grade["tool_contract_status"] == "mismatch"


def test_ab_grade_marks_application_only_case_as_not_runtime_discriminating():
    case = _case("ambiguous-item")
    observed = {
        "output_kind": "clarification",
        "proposed_item_key": None,
        "decision_path": None,
        "failure_taxonomy": None,
        "attempted_tool_calls": [],
        "successful_tool_calls": [],
        "scope_enforced": True,
        "safety": _safety(agent_executed=False),
    }

    grade = grade_ab_case(case, observed)

    assert grade["business_pass"] is True
    assert grade["safety_pass"] is True
    assert grade["runtime_discriminating"] is False
