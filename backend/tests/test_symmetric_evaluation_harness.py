"""PR-04D2 tests for one-case, offline, symmetric Runtime harnesses.

These are intentionally isolated-Pydantic tests.  The Legacy CI partition
excludes this module rather than collecting a dependency skip; the Pydantic CI
partition executes it with the dedicated lockfile.
"""

from __future__ import annotations

import copy
import inspect

import pytest
from sqlalchemy.orm import Session

pytest.importorskip(
    "pydantic_ai",
    reason="PR-04D2 symmetric harness requires the isolated Pydantic lockfile",
)

from pydantic_ai import models

from evals.graders import grade_ab_case, grade_case
from evals.runner import (
    _canonical_dataset_sha256,
    _synthetic_fixture,
    load_chat_change_preview_dataset,
)
from evals.symmetric_harness import (
    compile_neutral_scenario,
    run_single_symmetric_case,
)


def _case(case_id: str) -> dict:
    return next(case for case in load_chat_change_preview_dataset()["cases"] if case["id"] == case_id)


def test_explicit_preview_runs_both_real_orchestrators_and_actual_tools(db: Session):
    """No Runtime method is faked: only each model/provider boundary is scripted."""
    case = _case("explicit-time-notice")
    with _synthetic_fixture(db) as fixture:
        legacy = run_single_symmetric_case(
            db, fixture=fixture, case=case, runtime_name="legacy"
        )
        pydantic = run_single_symmetric_case(
            db, fixture=fixture, case=case, runtime_name="pydantic"
        )

    assert legacy.runtime_executed is True
    assert legacy.provider_events == (
        "legacy_provider_dispatch",
        "legacy_provider_dispatch",
        "legacy_provider_dispatch",
    )
    assert legacy.attempted_tool_calls == legacy.successful_tool_calls
    assert legacy.successful_tool_calls[0].name == "get_current_plan"
    assert legacy.successful_tool_calls[0].arguments == {"day": "all"}
    assert legacy.successful_tool_calls[0].outcome == "success"
    assert legacy.successful_tool_calls[1].name == "classify_change"
    assert legacy.successful_tool_calls[1].arguments == {
        "item_title": "[redacted]",
        "item_id": "art",
        "new_start_hour": 15.5,
    }
    assert legacy.successful_tool_calls[1].outcome == "success"
    assert legacy.output_kind == "change_preview"
    assert legacy.decision_path == "notice"

    assert pydantic.runtime_executed is True
    assert pydantic.provider_events == (
        "pydantic_function_model_dispatch",
        "pydantic_function_model_dispatch",
    )
    assert pydantic.attempted_tool_calls == pydantic.successful_tool_calls
    assert pydantic.successful_tool_calls[0].name == "get_current_plan"
    assert pydantic.successful_tool_calls[0].arguments == {"day": "all"}
    assert pydantic.successful_tool_calls[0].outcome == "success"
    assert pydantic.output_kind == "change_preview"
    assert pydantic.decision_path == "notice"

    # Intentional Runtime tool-surface difference is observable, not hidden.
    assert [event.name for event in legacy.successful_tool_calls] != [
        event.name for event in pydantic.successful_tool_calls
    ]
    assert legacy.worker_session_is_independent is True
    assert pydantic.worker_session_is_independent is True
    assert legacy.network_attempts == pydantic.network_attempts == 0


def test_explicit_previews_bind_complete_execution_evidence_directly_to_ab_grader(db: Session):
    case = _case("explicit-time-notice")
    with _synthetic_fixture(db) as fixture:
        observations = {
            runtime_name: run_single_symmetric_case(
                db, fixture=fixture, case=case, runtime_name=runtime_name
            )
            for runtime_name in ("legacy", "pydantic")
        }

    legacy = observations["legacy"]
    pydantic = observations["pydantic"]
    for observation in observations.values():
        assert observation.output_kind == "change_preview"
        assert observation.proposed_item_key == "art"
        assert observation.decision_path == "notice"
        assert observation.failure_taxonomy is None
        assert observation.scope_enforced is True
        assert observation.grounding.scoped_read_evidence is True
        assert observation.grounding.suggested_item_grounded is True
        assert observation.grounding.application_item_patch_validated is True
        assert observation.grounding.authoritative_domain_classification_executed is True
        assert observation.safety.durable_side_effects is False
        assert observation.safety.private_data_leaked is False
        assert observation.safety.cross_trip_exposed is False
        assert observation.safety.agent_executed is True
        assert observation.safety.unsupported_write_authority is False
        assert observation.safety.false_application_claim is False
        assert observation.safety.business_invariant_violated is False
        assert observation.network_attempts == 0
        assert observation.worker_session_is_independent is True

    legacy_grade = grade_ab_case(case, legacy.as_grade_input())
    pydantic_grade = grade_ab_case(case, pydantic.as_grade_input())
    assert legacy_grade["business_pass"] is legacy_grade["safety_pass"] is True
    assert legacy_grade["tool_contract_status"] == "superset"
    assert pydantic_grade["business_pass"] is pydantic_grade["safety_pass"] is True
    assert pydantic_grade["tool_contract_status"] == "alternate_authoritative"


def test_provider_and_tool_failures_are_actual_events_not_prebuilt_agent_results(db: Session):
    with _synthetic_fixture(db) as fixture:
        for runtime_name in ("legacy", "pydantic"):
            provider = run_single_symmetric_case(
                db,
                fixture=fixture,
                case=_case("provider-failure-fallback"),
                runtime_name=runtime_name,
            )
            tool = run_single_symmetric_case(
                db,
                fixture=fixture,
                case=_case("tool-failure-fallback"),
                runtime_name=runtime_name,
            )

            assert "provider_failure" in provider.provider_events
            assert provider.attempted_tool_calls == ()
            assert provider.successful_tool_calls == ()
            assert provider.runtime_executed is True
            assert provider.safe_degraded is True
            assert provider.network_attempts == 0

            assert tool.runtime_executed is True
            assert len(tool.attempted_tool_calls) == 1
            assert tool.attempted_tool_calls[0].name == "get_current_plan"
            assert tool.attempted_tool_calls[0].arguments == {"day": "all"}
            assert tool.attempted_tool_calls[0].outcome == "failure"
            assert tool.successful_tool_calls == ()
            assert tool.safe_degraded is True
            assert tool.network_attempts == 0
            assert provider.failure_taxonomy == "provider_failure"
            assert tool.failure_taxonomy == "tool_failure"
            assert provider.safety.durable_side_effects is False
            assert tool.safety.durable_side_effects is False
            assert tool.as_grade_input()["attempted_tool_calls"][0]["outcome"] == "failure"


def test_application_only_case_never_enters_either_runtime(db: Session):
    case = _case("ambiguous-item")
    with _synthetic_fixture(db) as fixture:
        legacy = run_single_symmetric_case(
            db, fixture=fixture, case=case, runtime_name="legacy"
        )
        pydantic = run_single_symmetric_case(
            db, fixture=fixture, case=case, runtime_name="pydantic"
        )

    for observation in (legacy, pydantic):
        assert observation.runtime_executed is False
        assert observation.provider_events == ()
        assert observation.attempted_tool_calls == ()
        assert observation.successful_tool_calls == ()
        assert observation.output_kind == "clarification"
        assert observation.clarification_source == "deterministic_ambiguous_item"
        assert observation.network_attempts == 0
        assert observation.scope_enforced is True
        assert observation.safety.agent_executed is False
        assert observation.safety.cross_trip_exposed is False


def test_cross_trip_denial_records_scope_and_safety_evidence_before_runtime_execution(db: Session):
    case = _case("cross-trip-denied")
    with _synthetic_fixture(db) as fixture:
        for runtime_name in ("legacy", "pydantic"):
            observation = run_single_symmetric_case(
                db, fixture=fixture, case=case, runtime_name=runtime_name
            )
            assert observation.runtime_executed is False
            assert observation.output_kind == "access_denied"
            assert observation.failure_taxonomy == "cross_trip_denied"
            assert observation.scope_enforced is True
            assert observation.safety.cross_trip_exposed is False
            assert observation.safety.agent_executed is False
            assert observation.safety.durable_side_effects is False
            grade = grade_ab_case(case, observation.as_grade_input())
            assert grade["business_pass"] is True
            assert grade["safety_pass"] is True
            assert grade["runtime_discriminating"] is False


def test_expected_label_mutation_cannot_change_scenario_or_single_case_observation(db: Session):
    original = _case("explicit-time-notice")
    relabeled = copy.deepcopy(original)
    relabeled["expected_business_outcome"] = "ask_for_missing_time"
    relabeled["expected_failure_taxonomy"] = "provider_failure"
    relabeled["expected_tool_calls"] = []
    relabeled["domain_oracle"]["expected_path"] = "confirm"
    relabeled["allowed_output_kinds"] = ["clarification"]

    assert compile_neutral_scenario(original) == compile_neutral_scenario(relabeled)
    with _synthetic_fixture(db) as fixture:
        for runtime_name in ("legacy", "pydantic"):
            original_observation = run_single_symmetric_case(
                db, fixture=fixture, case=original, runtime_name=runtime_name
            )
            relabeled_observation = run_single_symmetric_case(
                db, fixture=fixture, case=relabeled, runtime_name=runtime_name
            )
            assert original_observation == relabeled_observation
            assert original_observation.as_grade_input() == relabeled_observation.as_grade_input()


def test_other_deterministic_clarifications_have_structured_sources(db: Session):
    missing_item = copy.deepcopy(_case("ambiguous-item"))
    missing_item["input"] = {"message": "Move imaginary venue later", "item_key": None}
    missing_time = copy.deepcopy(_case("explicit-time-notice"))
    missing_time["input"] = {"message": "Change time", "item_key": "art"}

    with _synthetic_fixture(db) as fixture:
        missing = run_single_symmetric_case(
            db, fixture=fixture, case=missing_item, runtime_name="legacy"
        )
        slot = run_single_symmetric_case(
            db, fixture=fixture, case=missing_time, runtime_name="pydantic"
        )

    assert (missing.output_kind, missing.clarification_source) == (
        "clarification",
        "deterministic_missing_item",
    )
    assert (slot.output_kind, slot.clarification_source) == (
        "clarification",
        "deterministic_missing_slot",
    )


def test_ordinary_runtime_reply_stays_reply_only_without_text_heuristics(db: Session):
    ordinary = copy.deepcopy(_case("privacy-injection"))
    ordinary["fixture_preconditions"]["selected_item"] = None
    ordinary["input"] = {"message": "Could you help me?", "item_key": None}

    with _synthetic_fixture(db) as fixture:
        for runtime_name in ("legacy", "pydantic"):
            observation = run_single_symmetric_case(
                db, fixture=fixture, case=ordinary, runtime_name=runtime_name
            )
            assert observation.runtime_executed is True
            assert observation.output_kind == "reply_only"
            assert observation.clarification_source is None


def test_grader_compares_expected_tools_to_successful_calls_but_preserves_failed_attempt():
    case = _case("tool-failure-fallback")
    observed = {
        "output_kind": "safe_degraded",
        "proposed_item_key": None,
        "decision_path": None,
        "attempted_tool_calls": [
            {"name": "get_current_plan", "arguments": {"day": "all"}, "outcome": "failure"}
        ],
        "successful_tool_calls": [],
        "provider_prompts": [],
        "reply": "",
        "failure_taxonomy": "tool_failure",
        "latency_ms": 1.0,
        "token_usage": None,
        "safety": {
            "durable_side_effects": False,
            "private_data_leaked": False,
            "agent_executed": True,
        },
    }

    grade = grade_case(case, observed)

    assert observed["attempted_tool_calls"][0]["outcome"] == "failure"
    assert observed["successful_tool_calls"] == []
    assert case["expected_tool_calls"] == []
    assert grade["checks"]["tool_selection"] is True
    assert grade["checks"]["tool_argument_correctness"] is True
    assert grade["passed"] is True


def test_pydantic_model_request_guard_is_scoped_and_restored(db: Session, monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    with _synthetic_fixture(db) as fixture:
        observation = run_single_symmetric_case(
            db,
            fixture=fixture,
            case=_case("explicit-time-notice"),
            runtime_name="pydantic",
        )

    assert observation.network_attempts == 0
    assert models.ALLOW_MODEL_REQUESTS is True


def test_ab_conversion_and_grader_fail_closed_when_actual_evidence_is_removed(db: Session):
    case = _case("explicit-time-notice")
    with _synthetic_fixture(db) as fixture:
        observation = run_single_symmetric_case(
            db, fixture=fixture, case=case, runtime_name="pydantic"
        )

    missing_grounding = observation.as_grade_input()
    missing_grounding["grounding"]["suggested_item_grounded"] = False
    assert grade_ab_case(case, missing_grounding)["business_pass"] is False

    missing_scope = observation.as_grade_input()
    del missing_scope["scope_enforced"]
    assert grade_ab_case(case, missing_scope)["business_pass"] is False

    missing_privacy = observation.as_grade_input()
    del missing_privacy["safety"]["private_data_leaked"]
    assert grade_ab_case(case, missing_privacy)["safety_pass"] is False


def test_symmetric_harness_preserves_frozen_dataset_hash():
    assert _canonical_dataset_sha256() == (
        "e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f"
    )


def test_harness_never_patches_or_returns_from_base_call_agent():
    import evals.symmetric_harness as harness

    source = inspect.getsource(harness)
    assert 'patch.object(base, "call_agent"' not in source
    assert "AgentRunResult" not in source
    for forbidden in (
        "expected_business_outcome",
        "expected_failure_taxonomy",
        "expected_tool_calls",
        "expected_path",
        "allowed_output_kinds",
    ):
        assert forbidden not in inspect.getsource(compile_neutral_scenario)
