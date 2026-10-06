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

from evals.runner import _synthetic_fixture, load_chat_change_preview_dataset
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
    assert legacy.tool_calls[0].name == "get_current_plan"
    assert legacy.tool_calls[0].arguments == {"day": "all"}
    assert legacy.tool_calls[0].outcome == "success"
    assert legacy.tool_calls[1].name == "classify_change"
    assert legacy.tool_calls[1].arguments == {
        "item_title": "[redacted]",
        "item_id": "art",
        "new_start_hour": 15.5,
    }
    assert legacy.tool_calls[1].outcome == "success"
    assert legacy.output_kind == "change_preview"
    assert legacy.decision_path == "notice"

    assert pydantic.runtime_executed is True
    assert pydantic.provider_events == (
        "pydantic_function_model_dispatch",
        "pydantic_function_model_dispatch",
    )
    assert pydantic.tool_calls[0].name == "get_current_plan"
    assert pydantic.tool_calls[0].arguments == {"day": "all"}
    assert pydantic.tool_calls[0].outcome == "success"
    assert pydantic.output_kind == "change_preview"
    assert pydantic.decision_path == "notice"

    # Intentional Runtime tool-surface difference is observable, not hidden.
    assert [event.name for event in legacy.tool_calls] != [
        event.name for event in pydantic.tool_calls
    ]
    assert legacy.worker_session_is_independent is True
    assert pydantic.worker_session_is_independent is True
    assert legacy.network_attempts == pydantic.network_attempts == 0


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
            assert provider.tool_calls == ()
            assert provider.runtime_executed is True
            assert provider.network_attempts == 0

            assert tool.runtime_executed is True
            assert len(tool.tool_calls) == 1
            assert tool.tool_calls[0].name == "get_current_plan"
            assert tool.tool_calls[0].arguments == {"day": "all"}
            assert tool.tool_calls[0].outcome == "failure"
            assert tool.network_attempts == 0


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
        assert observation.tool_calls == ()
        assert observation.output_kind == "reply_only"
        assert observation.network_attempts == 0


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
