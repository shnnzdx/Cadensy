"""PR-04B isolated Pydantic Runtime contract tests.

These tests exercise the public ``ChatAgentRuntime.run`` seam with local
Pydantic AI FunctionModels only.  They neither route through HTTP nor permit a
network request.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from evals.runner import _independent_session_factory, _synthetic_fixture

pytest.importorskip(
    "pydantic_ai",
    reason="PR-04B adapter tests require requirements-pydantic-ai-poc.lock.txt",
)

from pydantic_ai import ModelResponse, ToolCallPart, models
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models import ModelRequestParameters

from app.agents import execution
from app.agents import pydantic_runtime
from app.agents.execution import AgentExecutionConfig
from app.agents.legacy_runtime import LegacyReadTripCapability
from app.agents.pydantic_runtime import PydanticChatAgentRuntime
from app.agents.runtime_factory import build_chat_runtime
from app.agents.runtime_contract import (
    AgentClarification,
    AgentReplyOnly,
    AgentSuggestedChange,
    RuntimeHistoryTurn,
    RuntimeLimits,
    RuntimeResult,
    RuntimeRequest,
    ReplacementCandidateEvidence,
    ToolExecutionEvidence,
)
from app.db.models import ChangeProposal, PlanItem, TripMembership, Vote
from app.domain.places import service as place_service
from app.domain.places.service import PlannerPlace
from app.domain.preview_apply import PreviewRequest, RevisionBracketedPreview
from app.domain.conversations import context as conversation_context
from app.domain.conversations import service as conversation_service
from app.domain.conversations.runtime import ConversationRuntimeCoordinator
from app.observability.telemetry import ModelCallCompleted, TelemetryContext, ToolCallCompleted


# Prevent an accidental replacement of a fake model with a live model.
models.ALLOW_MODEL_REQUESTS = False


@dataclass
class _SyntheticReadTool:
    name: str = "get_current_plan"
    description: str = "Read the scoped Current Plan."
    parameters: dict[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "additionalProperties": False,
            "properties": {"day": {"type": "string"}},
            "required": ["day"],
        }
    )
    plan: dict[str, object] = field(
        default_factory=lambda: {
            "days": [
                {
                    "items": [
                        {
                            "id": "scoped-item",
                            "title": "Scoped museum visit",
                            "start_hour": 14.0,
                        }
                    ]
                }
            ]
        }
    )
    calls: list[dict[str, object]] = field(default_factory=list)
    block_for_seconds: float = 0.0

    def invoke(self, **arguments: object) -> object:
        self.calls.append(arguments)
        if self.block_for_seconds:
            time.sleep(self.block_for_seconds)
        if set(arguments) != {"day"} or not isinstance(arguments["day"], str):
            raise AssertionError("tool arguments were not validated")
        return self.plan


@dataclass
class _SyntheticCapability:
    tools: tuple[_SyntheticReadTool, ...] = field(default_factory=lambda: (_SyntheticReadTool(),))
    entered: threading.Event = field(default_factory=threading.Event)

    def run_with_read_only_tools(self, operation):  # type: ignore[no-untyped-def]
        self.entered.set()
        return operation(self.tools)


@dataclass
class _NamedReadTool:
    name: str
    handler: Any
    description: str = "Scoped synthetic tool."
    parameters: dict[str, object] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)

    def invoke(self, **arguments: object) -> object:
        self.calls.append(arguments)
        return self.handler(**arguments)


def _request(
    *, max_rounds: int = 3, max_total_tokens: int | None = 1_000,
    max_tokens: int | None = 128, selected_item_ref: str | None = "scoped-item",
) -> RuntimeRequest:
    return RuntimeRequest(
        message="Please help with the Current Plan.",
        history=(RuntimeHistoryTurn(role="user", text="Earlier question"),),
        selected_item_ref=selected_item_ref,
        request_id="pydantic-runtime-test",
        limits=RuntimeLimits(
            max_rounds=max_rounds,
            max_total_tokens=max_total_tokens,
            max_tokens=max_tokens,
            guard_reject_limit=1,
        ),
    )


def _execution(*, request: float = 0.5, tool: float = 0.1) -> AgentExecutionConfig:
    return AgentExecutionConfig(
        request_timeout_seconds=request,
        provider_timeout_seconds=0.25,
        tool_timeout_seconds=tool,
    )


def _runtime(model: object) -> PydanticChatAgentRuntime:
    return PydanticChatAgentRuntime(model=model, system_prompt="Synthetic system prompt.")


def test_explicit_pydantic_selector_composes_only_an_injected_pydantic_runtime(monkeypatch):
    monkeypatch.setenv("CHAT_AGENT_RUNTIME", "pydantic")
    expected = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Synthetic reply."},
                    )
                ]
            )
        )
    )

    runtime = build_chat_runtime(
        system_prompt="Synthetic system prompt.",
        pydantic_runtime_factory=lambda: expected,
    )

    assert runtime is expected


@pytest.mark.parametrize(
    ("tool_name", "payload", "outcome_type"),
    [
        (
            "runtime_reply",
            {"output_kind": "reply_only", "reply": "Here is the plan summary."},
            AgentReplyOnly,
        ),
        (
            "runtime_clarification",
            {
                "output_kind": "clarification",
                "reply": "Which time should I use for the museum?",
            },
            AgentClarification,
        ),
    ],
)
def test_typed_reply_and_clarification_map_to_common_runtime_result(
    tool_name: str, payload: dict[str, object], outcome_type: type[object]
):
    result = _runtime(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[ToolCallPart(tool_name, payload)]))
    ).run(_request(), _SyntheticCapability(), execution=_execution())

    assert isinstance(result.outcome, outcome_type)
    assert result.reply == payload["reply"]
    assert result.candidate_options == ()
    assert result.tool_execution_evidence == ()
    assert result.observation.failure is None


def test_suggested_change_requires_same_run_scoped_read_and_has_no_write_authority():
    calls = 0
    capability = _SyntheticCapability()

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_suggested_change",
                    {
                        "output_kind": "suggested_change",
                        "reply": "I can prepare this proposed time change.",
                        "item_ref": "scoped-item",
                        "safe_patch": {"start_hour": 15.5},
                    },
                )
            ]
        )

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 2
    assert capability.tools[0].calls == [{"day": "all"}]
    assert isinstance(result.outcome, AgentSuggestedChange)
    assert result.outcome.item_ref == "scoped-item"
    assert result.outcome.safe_patch == {"start_hour": 15.5}
    assert result.reply
    assert result.observation.failure is None
    assert [(entry.tool_name, entry.status) for entry in result.tool_execution_evidence] == [
        ("get_current_plan", "succeeded"),
    ]
    assert isinstance(result.tool_execution_evidence, tuple)


@pytest.mark.parametrize("selected_item_ref", ["scoped-item", "forged-client-item"])
def test_selected_item_is_a_non_authoritative_hint(selected_item_ref: str):
    capability = _SyntheticCapability()
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_suggested_change",
            {
                "output_kind": "suggested_change", "reply": "Use the plan-backed item.",
                "item_ref": "scoped-item", "safe_patch": {"start_hour": 16.0},
            },
        )])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(selected_item_ref=selected_item_ref), capability, execution=_execution()
    )

    assert isinstance(result.outcome, AgentSuggestedChange)
    assert result.outcome.item_ref == "scoped-item"
    assert capability.tools[0].calls == [{"day": "all"}]


def test_pydantic_replacement_uses_scoped_handler_and_returns_trusted_evidence():
    plan = _SyntheticReadTool()
    replacement = _NamedReadTool(
        name="find_replacement_place",
        handler=lambda item_id, keywords: {
            "candidates": [{
                "candidate_id": "trusted-cafe", "title": "Trusted Cafe", "place": "River North",
                "lat": 41.9, "lng": -87.6, "price_per_person": None,
            }]
        },
    )
    capability = _SyntheticCapability(tools=(plan, replacement))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart(
                "find_replacement_place", {"item_id": "scoped-item", "keywords": ["cafe"]},
            )])
        return ModelResponse(parts=[ToolCallPart("runtime_suggested_change", {
            "output_kind": "suggested_change", "reply": "Try Trusted Cafe.", "item_ref": "scoped-item",
            "safe_patch": {"title": "Trusted Cafe", "place": "River North", "lat": 41.9, "lng": -87.6},
        })])

    result = _runtime(FunctionModel(scripted_model)).run(_request(), capability, execution=_execution())

    assert replacement.calls == [{"item_id": "scoped-item", "keywords": ["cafe"]}]
    assert isinstance(result.outcome, AgentSuggestedChange)
    assert result.outcome.safe_patch["title"] == "Trusted Cafe"
    evidence = next(entry for entry in result.tool_execution_evidence if entry.tool_name == "find_replacement_place")
    assert evidence.replacement_candidates[0].candidate_id == "trusted-cafe"
    assert isinstance(evidence.replacement_candidates, tuple)


def test_replacement_evidence_price_matching_is_order_independent():
    priced = ReplacementCandidateEvidence(
        candidate_id="priced", title="Trusted Cafe", place="River North",
        lat=41.9, lng=-87.6, price_per_person=25.0,
    )
    unpriced = ReplacementCandidateEvidence(
        candidate_id="unpriced", title="Trusted Cafe", place="River North",
        lat=41.9, lng=-87.6, price_per_person=None,
    )
    patch_without_price = {
        "title": "Trusted Cafe", "place": "River North", "lat": 41.9, "lng": -87.6,
    }
    patch_with_price = {**patch_without_price, "price_per_person": 25.0}
    patch_with_wrong_price = {**patch_without_price, "price_per_person": 30.0}

    for candidates in ((priced, unpriced), (unpriced, priced)):
        evidence = [ToolExecutionEvidence(
            tool_name="find_replacement_place", status="succeeded", replacement_candidates=candidates,
        )]
        assert pydantic_runtime._replacement_patch_is_evidenced(patch_without_price, evidence)
        assert pydantic_runtime._replacement_patch_is_evidenced(patch_with_price, evidence)
        assert not pydantic_runtime._replacement_patch_is_evidenced(patch_with_wrong_price, evidence)

    split_evidence = [
        ToolExecutionEvidence(
            tool_name="find_replacement_place", status="succeeded", replacement_candidates=(priced,),
        ),
        ToolExecutionEvidence(
            tool_name="find_replacement_place", status="cache_hit", replacement_candidates=(unpriced,),
        ),
        ToolExecutionEvidence(tool_name="find_replacement_place", status="guard_rejected"),
    ]
    assert pydantic_runtime._replacement_patch_is_evidenced(patch_without_price, split_evidence)
    assert not pydantic_runtime._replacement_patch_is_evidenced(
        {**patch_without_price, "place": "Unknown"}, split_evidence,
    )


def test_pydantic_trip_facts_and_options_use_scoped_handlers_only():
    plan = _SyntheticReadTool()
    facts = _NamedReadTool(
        name="get_trip_facts",
        handler=lambda: {"destination": "Synthetic City", "member_count": 2},
    )
    options = _NamedReadTool(
        name="propose_options",
        handler=lambda conflict_description, day, conflict_item_ids, suggestions: {
            "options": [
                {
                    "id": "move-later", "label": "Move later", "title": "Move museum later",
                    "body": "Move the scoped stop later.", "tradeoff": "Dinner gets closer.",
                    "item_id": "scoped-item", "patch": {"start_hour": 16.0},
                },
                {
                    "id": "bad", "label": "Bad", "title": "Bad option", "body": "Bad.",
                    "tradeoff": "Bad.", "item_id": "scoped-item", "patch": {"lat": 999},
                },
                {
                    "id": "forged-venue", "label": "Forged", "title": "Forged venue",
                    "body": "This must not become an option.", "tradeoff": "Unsafe provenance.",
                    "item_id": "scoped-item",
                    "patch": {"title": "Forged", "place": "Elsewhere", "lat": 41.0, "lng": -87.0},
                },
            ]
        },
    )
    capability = _SyntheticCapability(tools=(plan, facts, options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_trip_facts", {})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        if calls == 3:
            return ModelResponse(parts=[ToolCallPart(
                "propose_options", {"conflict_description": "The day is crowded.", "day": "all"},
            )])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_reply", {"output_kind": "reply_only", "reply": "Here are bounded options."},
        )])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(max_rounds=4), capability, execution=_execution()
    )

    assert facts.calls == [{}]
    assert options.calls == [{
        "conflict_description": "The day is crowded.", "day": "all",
        "conflict_item_ids": [], "suggestions": [],
    }]
    assert [(option.id, option.item_ref, option.safe_patch) for option in result.candidate_options] == [
        ("move-later", "scoped-item", {"start_hour": 16.0}),
    ]
    assert [(entry.tool_name, entry.status) for entry in result.tool_execution_evidence] == [
        ("get_trip_facts", "succeeded"),
        ("get_current_plan", "succeeded"),
        ("propose_options", "succeeded"),
    ]


def test_pydantic_options_passes_grounded_conflict_ids_and_typed_suggestions():
    plan = _SyntheticReadTool(plan={
        "days": [{"items": [
            {"id": "item-a", "title": "Museum", "start_hour": 14.0},
            {"id": "item-b", "title": "Dinner", "start_hour": 19.0},
            {"id": "unrelated-long", "title": "Unrelated", "start_hour": 9.0},
        ]}],
    })
    options = _NamedReadTool(
        name="propose_options",
        handler=lambda conflict_description, day, conflict_item_ids, suggestions: {
            "options": [
                {
                    "id": "keep", "label": "Keep current", "title": "Keep conflict items",
                    "body": "Leave the named conflict unchanged.", "tradeoff": "The conflict remains.",
                    "item_id": "item-a", "patch": {},
                },
                {
                    "id": "suggested-1", "label": "Move museum", "title": "Move museum earlier",
                    "body": "Move only the museum.", "tradeoff": "Less afternoon time.",
                    "item_id": "item-a", "patch": {"start_hour": 12.0},
                },
                {
                    "id": "suggested-2", "label": "Shorten dinner", "title": "Shorten dinner",
                    "body": "Shorten only dinner.", "tradeoff": "Less dinner time.",
                    "item_id": "item-b", "patch": {"duration_min": 60},
                },
            ]
        },
    )
    capability = _SyntheticCapability(tools=(plan, options))
    calls = 0
    suggestions = [
        {
            "item_id": "item-a", "label": "Move museum", "title": "Move museum earlier",
            "body": "Move only the museum.", "tradeoff": "Less afternoon time.", "start_hour": 12.0,
        },
        {
            "item_id": "item-b", "label": "Shorten dinner", "title": "Shorten dinner",
            "body": "Shorten only dinner.", "tradeoff": "Less dinner time.", "duration_min": 60,
        },
    ]

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart("propose_options", {
                "conflict_description": "Museum and dinner are too close.", "day": "all",
                "conflict_item_ids": ["item-a", "item-b"], "suggestions": suggestions,
            })])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_reply", {"output_kind": "reply_only", "reply": "Here are focused options."},
        )])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(max_rounds=4, max_tokens=512), capability, execution=_execution()
    )

    assert options.calls == [{
        "conflict_description": "Museum and dinner are too close.", "day": "all",
        "conflict_item_ids": ["item-a", "item-b"], "suggestions": suggestions,
    }]
    assert calls == 3
    assert result.observation.failure is None
    assert [(option.id, option.item_ref, option.safe_patch) for option in result.candidate_options] == [
        ("keep", "item-a", {}),
        ("suggested-1", "item-a", {"start_hour": 12.0}),
        ("suggested-2", "item-b", {"duration_min": 60}),
    ]


def test_pydantic_options_rejects_foreign_conflict_ids_before_handler_call():
    options = _NamedReadTool(name="propose_options", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart("propose_options", {
            "conflict_description": "Foreign conflict.", "conflict_item_ids": ["foreign-item"],
        })])

    result = _runtime(FunctionModel(scripted_model)).run(_request(), capability, execution=_execution())

    assert options.calls == []
    assert result.observation.failure is not None
    assert not any(entry.tool_name == "propose_options" and entry.status in {"succeeded", "cache_hit"} for entry in result.tool_execution_evidence)


def test_pydantic_options_allows_one_grounded_correction_after_guard_rejection():
    """A rejected proposal can be corrected once without relaxing item grounding."""
    options = _NamedReadTool(
        name="propose_options",
        handler=lambda **_kwargs: {
            "options": [{
                "id": "move-later", "label": "Move later", "title": "Move museum later",
                "body": "Move only the grounded museum visit.", "tradeoff": "Less afternoon time.",
                "item_id": "scoped-item", "patch": {"start_hour": 16.0},
            }],
        },
    )
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart("propose_options", {
                "conflict_description": "Foreign conflict.", "conflict_item_ids": ["foreign-item"],
            })])
        if calls == 3:
            return ModelResponse(parts=[ToolCallPart("propose_options", {
                "conflict_description": "Grounded conflict.", "conflict_item_ids": ["scoped-item"],
            })])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_reply", {"output_kind": "reply_only", "reply": "Here is the grounded option."},
        )])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(max_rounds=4), capability, execution=_execution(),
    )

    assert calls == 4
    assert options.calls == [{
        "conflict_description": "Grounded conflict.", "day": "all",
        "conflict_item_ids": ["scoped-item"], "suggestions": [],
    }]
    assert result.observation.failure is None
    assert [(entry.tool_name, entry.status) for entry in result.tool_execution_evidence] == [
        ("get_current_plan", "succeeded"),
        ("propose_options", "guard_rejected"),
        ("propose_options", "succeeded"),
    ]


def test_pydantic_options_stops_after_one_rejected_correction_attempt():
    """Repeated foreign IDs never reach the scoped handler or an unbounded retry loop."""
    options = _NamedReadTool(name="propose_options", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart("propose_options", {
            "conflict_description": "Foreign conflict.", "conflict_item_ids": ["foreign-item"],
        })])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(max_rounds=4), capability, execution=_execution(),
    )

    assert calls == 3
    assert options.calls == []
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"
    assert [(entry.tool_name, entry.status) for entry in result.tool_execution_evidence] == [
        ("agent_execution", "failed"),
    ]


def test_pydantic_options_can_return_a_typed_clarification_after_insufficient_evidence():
    """The corrective path may clarify, but cannot invent planning facts or options."""
    options = _NamedReadTool(name="propose_options", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("propose_options", {
                "conflict_description": "No Current Plan has been read yet.",
            })])
        return ModelResponse(parts=[ToolCallPart("runtime_clarification", {
            "output_kind": "clarification",
            "reply": "Which day should I read before suggesting options?",
        })])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(max_rounds=2), capability, execution=_execution(),
    )

    assert calls == 2
    assert options.calls == []
    assert isinstance(result.outcome, AgentClarification)
    assert result.candidate_options == ()
    assert result.observation.failure is None
    assert [(entry.tool_name, entry.status) for entry in result.tool_execution_evidence] == [
        ("propose_options", "guard_rejected"),
    ]


def test_pydantic_options_rejects_specific_conflict_outside_its_read_day():
    options = _NamedReadTool(name="propose_options", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart("propose_options", {
            "conflict_description": "Specific conflict on another day.",
            "day": "day 2",
            "conflict_item_ids": ["scoped-item"],
        })])

    result = _runtime(FunctionModel(scripted_model)).run(_request(), capability, execution=_execution())

    assert options.calls == []
    assert result.observation.failure is not None
    assert not any(entry.tool_name == "propose_options" and entry.status in {"succeeded", "cache_hit"} for entry in result.tool_execution_evidence)


@pytest.mark.parametrize(
    ("case", "day", "suggestions"),
    [
        (
            "same_trip_unread",
            "all",
            [{
                "item_id": "same-trip-but-unread", "label": "Move it", "title": "Move it",
                "body": "This item was not returned by Current Plan.", "tradeoff": "None.",
                "start_hour": 12.0,
            }],
        ),
        (
            "foreign",
            "all",
            [{
                "item_id": "foreign-trip-item", "label": "Move it", "title": "Move it",
                "body": "This id is not grounded.", "tradeoff": "None.", "start_hour": 12.0,
            }],
        ),
        (
            "cross_day",
            "day 2",
            [{
                "item_id": "scoped-item", "label": "Move it", "title": "Move it",
                "body": "A whole-plan read cannot ground a named day.", "tradeoff": "None.",
                "start_hour": 12.0,
            }],
        ),
        (
            "one_of_many_ungrounded",
            "all",
            [
                {
                    "item_id": "scoped-item", "label": "Grounded", "title": "Grounded",
                    "body": "This id is safe.", "tradeoff": "None.", "start_hour": 12.0,
                },
                {
                    "item_id": "unread-item", "label": "Ungrounded", "title": "Ungrounded",
                    "body": "One unsafe id rejects the complete call.", "tradeoff": "None.",
                    "start_hour": 13.0,
                },
            ],
        ),
    ],
)
def test_pydantic_options_rejects_ungrounded_suggestion_items_before_handler_call(
    case: str, day: str, suggestions: list[dict[str, object]],
):
    options = _NamedReadTool(name="propose_options", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart("propose_options", {
            "conflict_description": f"Ungrounded suggestion: {case}.",
            "day": day,
            "suggestions": suggestions,
        })])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(), capability, execution=_execution(),
    )

    assert options.calls == []
    assert result.observation.failure is not None
    assert not any(
        entry.tool_name == "propose_options" and entry.status in {"succeeded", "cache_hit"}
        for entry in result.tool_execution_evidence
    )


def test_pydantic_options_rejects_oversized_payload_before_handler_call():
    options = _NamedReadTool(name="propose_options", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), options))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart("propose_options", {
            "conflict_description": "x" * 1_001,
            "conflict_item_ids": ["scoped-item"] * 5,
        })])

    result = _runtime(FunctionModel(scripted_model)).run(_request(), capability, execution=_execution())

    assert options.calls == []
    assert result.observation.failure is not None
    assert not any(entry.tool_name == "propose_options" and entry.status == "succeeded" for entry in result.tool_execution_evidence)


def test_pydantic_real_options_handler_keeps_a_named_conflict_off_unrelated_longest_item(
    db: Session,
):
    """Pydantic forwards grounded ids; existing handler retains targeting/domain validation."""
    with _synthetic_fixture(db) as fixture:
        session_factory = _independent_session_factory(db)
        with session_factory.begin() as setup_db:
            unrelated = PlanItem(
                plan_id=fixture["items"]["art"].plan_id,
                day_index=1,
                day_date=fixture["items"]["art"].day_date,
                start_hour=8.0,
                duration_min=600,
                title="Unrelated longest item",
                place="Elsewhere",
                settledness="loose",
            )
            setup_db.add(unrelated)
            setup_db.flush()
            unrelated_id = unrelated.id
        capability = LegacyReadTripCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=session_factory,
        )
        art_id = fixture["items"]["art"].id
        dinner_id = fixture["items"]["dinner"].id
        calls = 0

        def scripted_model(_messages, _info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
            if calls == 2:
                return ModelResponse(parts=[ToolCallPart("propose_options", {
                    "conflict_description": "The museum and dinner are too close.",
                    "day": "all",
                    "conflict_item_ids": [art_id, dinner_id],
                    "suggestions": [{
                        "item_id": art_id,
                        "label": "Move museum earlier",
                        "title": "Move the museum earlier",
                        "body": "Move only the museum to 9 AM.",
                        "tradeoff": "The group starts its museum visit earlier.",
                        "start_hour": 9.0,
                    }, {
                        "item_id": art_id,
                        "label": "Shorten museum visit",
                        "title": "Shorten the museum visit",
                        "body": "Keep the museum but reduce its duration to 90 minutes.",
                        "tradeoff": "The group has less time inside the museum.",
                        "duration_min": 90,
                    }],
                })])
            return ModelResponse(parts=[ToolCallPart(
                "runtime_reply", {"output_kind": "reply_only", "reply": "Here are reviewed options."},
            )])

        try:
            result = _runtime(FunctionModel(scripted_model)).run(
                _request(max_rounds=4, max_total_tokens=12_000, max_tokens=4_096),
                capability,
                execution=_execution(),
            )
        finally:
            with session_factory.begin() as cleanup_db:
                extra = cleanup_db.get(PlanItem, unrelated_id)
                if extra is not None:
                    cleanup_db.delete(extra)

    assert result.observation.failure is None
    assert calls == 3
    assert result.candidate_options
    assert {option.item_ref for option in result.candidate_options} == {art_id}
    assert all(option.item_ref != unrelated_id for option in result.candidate_options)
    assert any(option.safe_patch == {"start_hour": 9.0} for option in result.candidate_options)
    assert any(option.safe_patch == {"duration_min": 90} for option in result.candidate_options)


def test_pydantic_tool_cache_is_limited_to_valid_same_run_projections(monkeypatch):
    plan = _SyntheticReadTool()
    capability = _SyntheticCapability(tools=(plan,))
    emitted: list[object] = []
    monkeypatch.setattr(
        pydantic_runtime,
        "emit_event_safely",
        lambda factory: emitted.append(factory()) or True,
    )
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls < 3:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_suggested_change",
            {
                "output_kind": "suggested_change", "reply": "Move it later.",
                "item_ref": "scoped-item", "safe_patch": {"start_hour": 16.0},
            },
        )])

    result = _runtime(FunctionModel(scripted_model)).run(
        _request(),
        capability,
        execution=AgentExecutionConfig(
            request_timeout_seconds=0.5,
            provider_timeout_seconds=0.25,
            tool_timeout_seconds=0.1,
            telemetry_context=TelemetryContext(
                request_id="request_12345678",
                trace_id="trace_12345678",
                execution_id="execution_12345678",
            ),
        ),
    )

    assert plan.calls == [{"day": "all"}]
    assert [(entry.tool_name, entry.status) for entry in result.tool_execution_evidence] == [
        ("get_current_plan", "succeeded"),
        ("get_current_plan", "cache_hit"),
    ]
    tool_events = [event for event in emitted if isinstance(event, ToolCallCompleted)]
    cache_event = next(
        event for event in tool_events
        if event.tool_dispatch_status.value == "cache_hit"
    )
    assert cache_event.cache_hit is True
    assert cache_event.handler_executed is False


def test_pydantic_replacement_before_plan_is_guard_rejected_without_handler_call():
    replacement = _NamedReadTool(name="find_replacement_place", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(_SyntheticReadTool(), replacement))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("find_replacement_place", {"item_id": "scoped-item", "keywords": []})])
        return ModelResponse(parts=[ToolCallPart("runtime_reply", {"output_kind": "reply_only", "reply": "Please read the plan first."})])

    result = _runtime(FunctionModel(scripted_model)).run(_request(), capability, execution=_execution())

    assert replacement.calls == []
    assert result.observation.failure is not None
    assert not any(entry.tool_name == "find_replacement_place" and entry.status in {"succeeded", "cache_hit"} for entry in result.tool_execution_evidence)


def test_pydantic_replacement_unknown_item_is_guard_rejected_without_handler_call():
    plan = _SyntheticReadTool()
    replacement = _NamedReadTool(name="find_replacement_place", handler=lambda **_kwargs: {})
    capability = _SyntheticCapability(tools=(plan, replacement))
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(parts=[ToolCallPart(
            "find_replacement_place", {"item_id": "foreign-item", "keywords": []},
        )])

    result = _runtime(FunctionModel(scripted_model)).run(_request(), capability, execution=_execution())

    assert replacement.calls == []
    assert result.observation.failure is not None
    assert not any(entry.tool_name == "find_replacement_place" and entry.status in {"succeeded", "cache_hit"} for entry in result.tool_execution_evidence)


@pytest.mark.parametrize(
    "unsafe_patch",
    [
        {"unexpected": "value"},
        {"start_hour": True},
        {"start_hour": float("nan")},
        {"price_per_person": float("inf")},
        {"lat": 91},
        {"day_date": "not-a-date"},
        {"title": "Only title"},
        {"start_hour": {"nested": 1}},
    ],
)
def test_pydantic_patch_validation_rejects_unsafe_values(unsafe_patch: dict[str, object]):
    with pytest.raises(ValueError):
        pydantic_runtime._validated_safe_patch(unsafe_patch)


@pytest.mark.parametrize(
    "unsupported_patch",
    [
        {
            "title": "Invented Cafe",
            "place": "Invented Address",
            "lat": 41.0,
            "lng": -87.0,
        },
        {"price_per_person": 20},
    ],
)
def test_replacement_shaped_suggestion_without_provenance_fails_closed(
    unsupported_patch: dict[str, object],
):
    calls = 0
    capability = _SyntheticCapability()

    def read_then_replacement_attempt(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_suggested_change",
                    {
                        "output_kind": "suggested_change",
                        "reply": "I can replace this venue.",
                        "item_ref": "scoped-item",
                        "safe_patch": unsupported_patch,
                    },
                )
            ]
        )

    result = _runtime(FunctionModel(read_then_replacement_attempt)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 3  # plan read plus one bounded invalid-output retry
    assert capability.tools[0].calls == [{"day": "all"}]
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"
    assert not isinstance(result.outcome, AgentSuggestedChange)


@pytest.mark.parametrize("item_ref", ["scoped-item", "forged-other-trip-item"])
def test_suggested_change_without_matching_same_run_read_fails_closed(item_ref: str):
    calls = 0
    capability = _SyntheticCapability()

    def invented_suggestion(_messages, _info):
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_suggested_change",
                    {
                        "output_kind": "suggested_change",
                        "reply": "I will change it.",
                        "item_ref": item_ref,
                        "safe_patch": {"start_hour": 15.0},
                    },
                )
            ]
        )

    result = _runtime(FunctionModel(invented_suggestion)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 2  # exactly one bounded output-validation retry
    assert capability.tools[0].calls == []
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"
    assert not isinstance(result.outcome, AgentSuggestedChange)


def test_tool_schema_has_no_model_selectable_scope_or_storage_parameters():
    captured_schema: dict[str, object] = {}

    def capture_schema(_messages, info):
        tool = next(tool for tool in info.function_tools if tool.name == "get_current_plan")
        captured_schema.update(tool.parameters_json_schema)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_reply",
                    {"output_kind": "reply_only", "reply": "I need no scoped read."},
                )
            ]
        )

    result = _runtime(FunctionModel(capture_schema)).run(
        _request(), _SyntheticCapability(), execution=_execution()
    )

    assert result.observation.failure is None
    serialized = json.dumps(captured_schema, sort_keys=True).lower()
    for forbidden in (
        "trip_id",
        "actor_membership_id",
        "membership_id",
        "session",
        "sql",
        "query",
        "commit",
    ):
        assert forbidden not in serialized
    assert "day" in serialized


def test_invalid_tool_arguments_are_rejected_before_the_scoped_tool_runs():
    calls = 0
    capability = _SyntheticCapability()

    def invalid_then_reply(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": 7})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_reply",
                    {"output_kind": "reply_only", "reply": "Please provide a day."},
                )
            ]
        )

    result = _runtime(FunctionModel(invalid_then_reply)).run(
        _request(), capability, execution=_execution()
    )

    assert calls == 1  # the minimum read tool has zero framework retries
    assert capability.tools[0].calls == []
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "malformed_runtime_output"


def test_adapter_uses_common_capability_with_independent_session_and_no_durable_write(
    db: Session,
):
    with _synthetic_fixture(db) as fixture:
        item = fixture["items"]["art"]
        item_id = item.id
        original_start_hour = item.start_hour
        base_factory = _independent_session_factory(db)
        worker_sessions: list[Session] = []
        worker_connection_is_independent: list[bool] = []
        closed = threading.Event()

        def tracked_session_factory() -> Session:
            worker_session = base_factory()
            original_close = worker_session.close

            def close() -> None:
                closed.set()
                original_close()

            worker_session.close = close  # type: ignore[method-assign]
            worker_connection_is_independent.append(
                worker_session.connection() is not db.connection()
            )
            worker_sessions.append(worker_session)
            return worker_session

        capability = LegacyReadTripCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=tracked_session_factory,
        )
        with base_factory() as verification_db:
            proposal_count_before = len(
                verification_db.scalars(
                    select(ChangeProposal).where(ChangeProposal.plan_item_id == item_id)
                ).all()
            )
            vote_count_before = len(verification_db.scalars(select(Vote)).all())
        calls = 0

        def scripted_model(_messages, _info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_suggested_change",
                        {
                            "output_kind": "suggested_change",
                            "reply": "Here is a non-authoritative suggestion.",
                            "item_ref": item.id,
                            "safe_patch": {"start_hour": 16.0},
                        },
                    )
                ]
            )

        result = _runtime(FunctionModel(scripted_model)).run(
            _request(), capability, execution=_execution()
        )

        with base_factory() as verification_db:
            persisted_item = verification_db.get(PlanItem, item_id)
            proposal_count_after = len(
                verification_db.scalars(
                    select(ChangeProposal).where(ChangeProposal.plan_item_id == item_id)
                ).all()
            )
            vote_count_after = len(verification_db.scalars(select(Vote)).all())

        assert isinstance(result.outcome, AgentSuggestedChange)
        assert persisted_item is not None
        assert persisted_item.start_hour == original_start_hour
        assert proposal_count_after == proposal_count_before
        assert vote_count_after == vote_count_before
        assert len(worker_sessions) == 1
        assert worker_sessions[0] is not db
        assert worker_connection_is_independent == [True]
        assert closed.is_set()


def test_pydantic_real_scoped_replacement_evidence_reaches_revision_bracketed_preview(
    db: Session, monkeypatch: pytest.MonkeyPatch,
):
    """FunctionModel -> real scoped handler -> detached evidence -> real Preview."""
    with _synthetic_fixture(db) as fixture:
        session_factory = _independent_session_factory(db)
        with session_factory.begin() as ready_db:
            membership = ready_db.get(TripMembership, fixture["memberships"]["organizer"].id)
            assert membership is not None
            membership.status = "joined"
        candidate = PlannerPlace(
            candidate_id="test:trusted-cafe", name="Trusted Cafe", location="River North",
            latitude=41.9, longitude=-87.6, category="catering.cafe", address=None,
            image_url=None, opening_hours="24/7",
        )
        monkeypatch.setattr(
            place_service, "places_for_replacement", lambda _db, _destination: (candidate,),
        )
        calls = 0

        def scripted_model(_messages, _info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
            if calls == 2:
                return ModelResponse(parts=[ToolCallPart(
                    "find_replacement_place", {"item_id": fixture["items"]["art"].id, "keywords": ["cafe"]},
                )])
            return ModelResponse(parts=[ToolCallPart(
                "runtime_suggested_change",
                {
                    "output_kind": "suggested_change", "reply": "Try Trusted Cafe.",
                    "item_ref": fixture["items"]["art"].id,
                    "safe_patch": {
                        "title": "Trusted Cafe", "place": "River North", "lat": 41.9, "lng": -87.6,
                    },
                },
            )])

        class RecordingRuntime:
            def __init__(self) -> None:
                self.delegate = _runtime(FunctionModel(scripted_model))
                self.result: RuntimeResult | None = None

            def run(self, request, capability, *, execution):  # type: ignore[no-untyped-def]
                self.result = self.delegate.run(request, capability, execution=execution)
                return self.result

        runtime = RecordingRuntime()
        preview = RevisionBracketedPreview(
            session_factory=session_factory, runtime=runtime,
            execution=_execution(), runtime_limits=RuntimeLimits(max_rounds=4, max_total_tokens=1_000),
        ).preview(PreviewRequest(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            message="Replace the museum with a cafe.",
            selected_item_id=fixture["items"]["art"].id,
        ))

        assert runtime.result is not None
        evidence = next(
            entry for entry in runtime.result.tool_execution_evidence
            if entry.tool_name == "find_replacement_place"
        )
        assert evidence.status == "succeeded"
        assert evidence.replacement_candidates[0].candidate_id == "test:trusted-cafe"
        assert preview.candidate is not None
        assert preview.candidate.patch == {
            "title": "Trusted Cafe", "place": "River North", "lat": 41.9, "lng": -87.6,
        }


def test_pydantic_runtime_persists_preview_options_through_durable_coordinator(
    db: Session, trip_setup: dict,
):
    """Exercise durable Context -> Pydantic -> Preview -> fenced option persistence."""
    owner = trip_setup["organizer"]
    owner.status = "joined"
    now = conversation_service.utcnow()
    conversation = conversation_service.create_or_discover(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        scope="plan_assistant", context_key=f'item:{trip_setup["item"].id}', now=now,
    )
    submission = conversation_service.submit_message(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, idempotency_key="pydantic-options",
        text="The day is crowded; show alternatives.",
        selected_plan_item_id=trip_setup["item"].id, now=now,
    )
    calls = 0

    def scripted_model(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})])
        if calls == 2:
            return ModelResponse(parts=[ToolCallPart(
                "propose_options", {"conflict_description": "The day is crowded.", "day": "all"},
            )])
        return ModelResponse(parts=[ToolCallPart(
            "runtime_reply", {"output_kind": "reply_only", "reply": "Here are safe alternatives."},
        )])

    factory = sessionmaker(
        bind=db.connection(), future=True, join_transaction_mode="create_savepoint",
    )
    coordinator = ConversationRuntimeCoordinator(
        session_factory=factory,
        runtime_factory=lambda: _runtime(FunctionModel(scripted_model)),
        execution=_execution(),
        budget=conversation_context.ContextBudget(10_000, 100, 100),
        runtime_limits=RuntimeLimits(max_rounds=4, max_total_tokens=1_000),
    )
    result = coordinator.execute(
        identity=conversation_context.ExecutionIdentity(
            trip_setup["trip"].id, owner.id, conversation.id, submission.submission_id,
            submission.attempt_number, submission.fence,
        ),
        now=now,
    )
    history = conversation_service.history_projection(
        db, trip_id=trip_setup["trip"].id, membership_id=owner.id,
        conversation_id=conversation.id, before=None, limit=20, now=now,
    )

    assert result.state == "completed"
    assistant = next(message for message in history if message.role == "assistant")
    assert assistant.content == "Here are safe alternatives."
    assert assistant.options
    assert all(option.target_plan_item_id == trip_setup["item"].id for option in assistant.options)
    assert all(option.plan_revision == trip_setup["plan"].revision for option in assistant.options)


def test_blocking_scoped_tool_maps_to_tool_timeout_without_consuming_late_result():
    tool = _SyntheticReadTool(block_for_seconds=0.03)
    capability = _SyntheticCapability(tools=(tool,))

    result = _runtime(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})]))
    ).run(_request(), capability, execution=_execution(tool=0.001))

    assert tool.calls == [{"day": "all"}]
    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "tool_timeout"
    assert [(entry.tool_name, entry.status, entry.replacement_candidates) for entry in result.tool_execution_evidence] == [
        ("agent_execution", "timed_out", ()),
    ]


def test_post_return_deadline_keeps_pydantic_wrapper_return_telemetry(monkeypatch):
    """A local consumption rejection cannot erase the provider wrapper fact."""

    emitted: list[object] = []
    monkeypatch.setattr(
        pydantic_runtime,
        "emit_event_safely",
        lambda factory: emitted.append(factory()) or True,
    )
    monkeypatch.setattr(
        execution.AgentExecutionContext,
        "after_provider",
        lambda _self: (_ for _ in ()).throw(execution.AgentRequestDeadlineExceeded("late")),
    )
    runtime = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[ToolCallPart("runtime_reply", {"output_kind": "reply_only", "reply": "late"})]
            )
        )
    )
    telemetry_context = TelemetryContext(
        request_id="request_12345678",
        trace_id="trace_12345678",
        execution_id="execution_12345678",
    )

    result = runtime.run(
        _request(),
        _SyntheticCapability(),
        execution=AgentExecutionConfig(
            request_timeout_seconds=0.5,
            provider_timeout_seconds=0.25,
            tool_timeout_seconds=0.1,
            telemetry_context=telemetry_context,
        ),
    )

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "request_timeout"
    model_events = [event for event in emitted if isinstance(event, ModelCallCompleted)]
    assert len(model_events) == 1
    assert model_events[0].invocation_outcome.value == "returned"
    assert model_events[0].usage_status.value == "not_reported"


def test_wrapper_return_does_not_access_response_usage_for_telemetry(monkeypatch):
    """Telemetry must not touch an otherwise successful provider response."""

    marker = "response usage must stay untouched"
    emitted: list[object] = []

    class Deadline:
        after_provider_calls = 0

        def before_provider(self) -> float:
            return 0.25

        def after_provider(self) -> None:
            self.after_provider_calls += 1

    response = ModelResponse(parts=[])
    monkeypatch.setattr(
        ModelResponse,
        "usage",
        property(lambda _self: (_ for _ in ()).throw(RuntimeError(marker))),
        raising=False,
    )
    monkeypatch.setattr(
        pydantic_runtime,
        "emit_event_safely",
        lambda factory: emitted.append(factory()) or True,
    )
    deadline = Deadline()
    wrapper = pydantic_runtime._DeadlineBoundModel(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[])),
        deadline,  # type: ignore[arg-type]
        TelemetryContext(
            request_id="request_12345678",
            trace_id="trace_12345678",
            execution_id="execution_12345678",
        ),
    )
    async def successful_provider_request(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return response

    # Isolate the provider seam. FunctionModel itself validates usage, whereas
    # this regression is specifically about the wrapper's telemetry behavior
    # after a provider has successfully returned a response.
    monkeypatch.setattr(wrapper.wrapped, "request", successful_provider_request)

    returned = asyncio.run(
        wrapper.request([], None, ModelRequestParameters())
    )

    assert returned is response
    assert deadline.after_provider_calls == 1
    model_events = [event for event in emitted if isinstance(event, ModelCallCompleted)]
    assert len(model_events) == 1
    assert model_events[0].invocation_outcome.value == "returned"
    assert model_events[0].usage_status.value == "not_reported"


def test_rejected_pydantic_tool_result_never_claims_handler_success(monkeypatch):
    emitted: list[object] = []
    monkeypatch.setattr(
        pydantic_runtime,
        "emit_event_safely",
        lambda factory: emitted.append(factory()) or True,
    )
    telemetry_context = TelemetryContext(
        request_id="request_12345678",
        trace_id="trace_12345678",
        execution_id="execution_12345678",
    )
    runtime = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[ToolCallPart("get_current_plan", {"day": "all"})]
            )
        )
    )

    result = runtime.run(
        _request(max_rounds=2),
        _SyntheticCapability(tools=(_SyntheticReadTool(plan="not a safe projection"),)),
        execution=AgentExecutionConfig(
            request_timeout_seconds=0.5,
            provider_timeout_seconds=0.25,
            tool_timeout_seconds=0.1,
            telemetry_context=telemetry_context,
        ),
    )

    assert result.observation.failure is not None
    tool_events = [event for event in emitted if isinstance(event, ToolCallCompleted)]
    assert tool_events
    assert {event.tool_dispatch_status.value for event in tool_events} == {"handler_failed"}


def test_request_timeout_discards_late_pydantic_result(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_model(_messages, _info):
        started.set()
        release.wait(timeout=5.0)
        finished.set()
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "runtime_reply",
                    {"output_kind": "reply_only", "reply": "This reply arrived too late."},
                )
            ]
        )

    lifecycle_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        execution.trace,
        "record_agent_lifecycle",
        lambda **event: lifecycle_events.append(event),
    )
    result_holder: dict[str, RuntimeResult] = {}
    caller_errors: list[BaseException] = []

    def call_runtime() -> None:
        try:
            result_holder["result"] = _runtime(FunctionModel(blocking_model)).run(
                _request(),
                _SyntheticCapability(),
                execution=_execution(request=0.5),
            )
        except BaseException as error:
            caller_errors.append(error)

    caller = threading.Thread(target=call_runtime)
    caller.start()
    try:
        assert started.wait(timeout=2.0)
        caller.join(timeout=2.0)
        assert not caller.is_alive()
        assert not caller_errors
        result = result_holder["result"]
        assert result.observation.failure is not None
        assert result.observation.failure.normalized_kind == "request_timeout"
        assert [(entry.tool_name, entry.status, entry.replacement_candidates) for entry in result.tool_execution_evidence] == [
            ("agent_execution", "cancelled", ()),
        ]
    finally:
        release.set()

    caller.join(timeout=2.0)
    assert not caller.is_alive()
    assert finished.wait(timeout=2.0)
    assert _wait_until(lambda: any(event["phase"] == "late_completion" for event in lifecycle_events))
    late_completion = next(
        event for event in lifecycle_events if event["phase"] == "late_completion"
    )
    assert late_completion["result_consumed"] is False
    assert late_completion["worker_cancel_requested"] is False


def test_usage_limit_and_malformed_output_are_normalized_and_bounded():
    usage_result = _runtime(
        FunctionModel(lambda _messages, _info: ModelResponse(parts=[ToolCallPart("get_current_plan", {"day": "all"})]))
    ).run(_request(max_rounds=1), _SyntheticCapability(), execution=_execution())

    assert usage_result.observation.failure is not None
    assert usage_result.observation.failure.normalized_kind == "usage_limit"

    invalid_calls = 0

    def malformed_output(_messages, _info):
        nonlocal invalid_calls
        invalid_calls += 1
        return ModelResponse(
            parts=[ToolCallPart("runtime_suggested_change", {"output_kind": "suggested_change"})]
        )

    malformed_result = _runtime(FunctionModel(malformed_output)).run(
        _request(), _SyntheticCapability(), execution=_execution()
    )

    assert invalid_calls == 2
    assert malformed_result.observation.failure is not None
    assert malformed_result.observation.failure.normalized_kind == "malformed_runtime_output"


def test_missing_minimum_capability_is_an_adapter_failure_not_malformed_output():
    result = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Not reached."},
                    )
                ]
            )
        )
    ).run(_request(), _SyntheticCapability(tools=()), execution=_execution())

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "unexpected_exception"
    assert result.observation.failure.technical_kind == "_ToolSurfaceError"


def test_adapter_side_type_error_is_not_blamed_on_model_output(monkeypatch):
    async def broken_adapter(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise TypeError("synthetic adapter implementation error")

    monkeypatch.setattr(PydanticChatAgentRuntime, "_run_async", broken_adapter)

    result = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Not reached."},
                    )
                ]
            )
        )
    ).run(_request(), _SyntheticCapability(), execution=_execution())

    assert result.observation.failure is not None
    assert result.observation.failure.normalized_kind == "unexpected_exception"
    assert result.observation.failure.technical_kind == "TypeError"


def test_pydantic_adapter_isolated_from_legacy_tool_internals_and_current_chat_service():
    adapter_source = inspect.getsource(__import__("app.agents.pydantic_runtime", fromlist=["*"]))
    common_contract_source = inspect.getsource(__import__("app.agents.runtime_contract", fromlist=["*"]))
    legacy_adapter_source = inspect.getsource(__import__("app.agents.legacy_runtime", fromlist=["*"]))
    chat_source = inspect.getsource(__import__("app.domain.chat.service", fromlist=["*"]))

    assert "app.agents.base" not in adapter_source
    assert "from . import base" not in adapter_source
    assert "base.AgentTool" not in adapter_source
    assert "_for_legacy_orchestration" not in adapter_source
    assert "_legacy_tool" not in adapter_source
    assert "pydantic_ai" not in common_contract_source
    assert "pydantic_ai" not in legacy_adapter_source
    assert "PydanticChatAgentRuntime" not in chat_source

    result = _runtime(
        FunctionModel(
            lambda _messages, _info: ModelResponse(
                parts=[
                    ToolCallPart(
                        "runtime_reply",
                        {"output_kind": "reply_only", "reply": "Common DTO only."},
                    )
                ]
            )
        )
    ).run(_request(), _SyntheticCapability(), execution=_execution())
    assert type(result) is RuntimeResult
    assert type(result.outcome) is AgentReplyOnly


def _wait_until(predicate, *, timeout: float = 0.5) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
