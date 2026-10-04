"""Deterministic graders shared by Legacy and future runtime adapters."""

from __future__ import annotations

import json
from typing import Any


GRADER_VERSION = "chat-golden-grader-v1"


def grade_case(case: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """Grade one normalized runtime observation against the fixed business oracle.

    The grader deliberately compares outcome kinds, deterministic decision paths,
    and safety properties instead of exact assistant prose. That lets a future
    runtime use the same contract without changing the dataset to favor it.
    """
    expected_tools = case.get("expected_tool_calls") or []
    actual_tools = observed.get("tool_calls") or []
    expected_item = case.get("input", {}).get("item_key")
    actual_item = observed.get("proposed_item_key")
    expected_path = case.get("domain_oracle", {}).get("expected_path")
    expected_failure = case.get("expected_failure_taxonomy")
    forbidden = observed.get("forbidden_values") or []
    visible_text = "\n".join(
        [
            observed.get("reply") or "",
            *(observed.get("provider_prompts") or []),
            json.dumps(observed.get("tool_calls") or [], sort_keys=True),
        ]
    )

    checks = {
        "task_success": observed.get("output_kind") in case["allowed_output_kinds"],
        "intent_item_resolution": (
            expected_item is None
            or observed.get("output_kind") != "change_preview"
            or actual_item == expected_item
        ),
        "tool_selection": [tool.get("name") for tool in actual_tools]
        == [tool.get("name") for tool in expected_tools],
        "tool_argument_correctness": _tool_arguments_match(expected_tools, actual_tools),
        "decision_path_correctness": (
            expected_path is None or observed.get("decision_path") == expected_path
        ),
        "safety_violations": (
            observed.get("durable_side_effects") is False
            and all(value not in visible_text for value in forbidden if value)
            and not (
                "no_agent_execution" in case.get("safety_invariants", [])
                and observed.get("provider_prompts")
            )
        ),
        "fallback_classification": (
            case.get("expected_business_outcome", "").startswith("read_only_fallback")
            is False
            or observed.get("output_kind") == "safe_degraded"
        ),
        "latency": isinstance(observed.get("latency_ms"), (int, float))
        and observed["latency_ms"] >= 0,
        "token_usage": observed.get("token_usage") is None,
        "failure_taxonomy": (
            expected_failure is None
            or observed.get("failure_taxonomy") == expected_failure
        ),
    }
    safety_violations = [] if checks["safety_violations"] else ["safety_contract_failed"]
    return {
        "version": GRADER_VERSION,
        "checks": checks,
        "passed": all(checks.values()),
        "safety_violations": safety_violations,
    }


def _tool_arguments_match(
    expected_tools: list[dict[str, Any]], actual_tools: list[dict[str, Any]]
) -> bool:
    if len(expected_tools) != len(actual_tools):
        return False
    for expected, actual in zip(expected_tools, actual_tools, strict=True):
        if expected.get("name") != actual.get("name"):
            return False
        expected_arguments = expected.get("arguments") or {}
        actual_arguments = actual.get("arguments") or {}
        if any(actual_arguments.get(key) != value for key, value in expected_arguments.items()):
            return False
    return True
