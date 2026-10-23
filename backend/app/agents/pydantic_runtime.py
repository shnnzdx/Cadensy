"""Isolated Pydantic AI implementation of the PR-04A Runtime contract.

This adapter is deliberately not composed into the Chat route or Chat Service.
It consumes only the framework-neutral ``ReadTripCapability`` / ``RuntimeReadTool``
surface, runs read-only tools under PR-01C's existing deadline boundary, and
returns only common Runtime DTOs.  Provider construction remains outside this
module and is not part of PR-04B.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import date
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters, ModelSettings
from pydantic_ai.models.wrapper import WrapperModel

from .execution import (
    AgentExecutionCapacityExceeded,
    AgentExecutionConfig,
    AgentExecutionDeadlineExceeded,
    AgentExecutionContext,
    AgentProviderDeadlineExceeded,
    AgentRequestDeadlineExceeded,
    AgentToolDeadlineExceeded,
    run_agent_with_deadline,
)
from .runtime_contract import (
    AgentClarification,
    AgentCandidateOption,
    AgentReplyOnly,
    AgentSuggestedChange,
    ChatAgentRuntime,
    ReadTripCapability,
    RuntimeFailure,
    RuntimeFailureKind,
    RuntimeHistoryTurn,
    RuntimeObservation,
    RuntimeReadTool,
    RuntimeRequest,
    RuntimeResult,
    ReplacementCandidateEvidence,
    ToolExecutionEvidence,
)
from . import trace
from ..observability.telemetry import (
    ApprovedToolName,
    FailureCategory,
    InvocationOutcome,
    InvocationSource,
    ModelCallCompleted,
    ModelId,
    ProviderName,
    RuntimeName,
    RuntimeSelected,
    TelemetryContext,
    ToolCallCompleted,
    ToolDispatchStatus,
    UsageStatus,
    emit_event_safely,
)


_PYDANTIC_PATCH_FIELDS = frozenset(
    {"start_hour", "day_date", "duration_min", "title", "place", "lat", "lng", "price_per_person"}
)
_MAX_CONFLICT_ITEM_IDS = 4
_MAX_OPTION_SUGGESTIONS = 4
_MAX_OPTION_TEXT_LENGTH = 4_000
_MAX_CONFLICT_DESCRIPTION_LENGTH = 1_000
_MAX_OPTION_DAY_LENGTH = 64


class _RuntimeReply(BaseModel):
    output_kind: Literal["reply_only"]
    reply: Annotated[str, Field(min_length=1)]


class _RuntimeClarification(BaseModel):
    output_kind: Literal["clarification"]
    reply: Annotated[str, Field(min_length=1)]


class _RuntimeSuggestedChange(BaseModel):
    output_kind: Literal["suggested_change"]
    reply: Annotated[str, Field(min_length=1)]
    item_ref: Annotated[str, Field(min_length=1)]
    safe_patch: dict[str, object]

    @field_validator("safe_patch")
    @classmethod
    def validate_safe_patch(cls, value: dict[str, object]) -> dict[str, object]:
        return _validated_safe_patch(value)


class _RuntimeOptionSuggestion(BaseModel):
    """Bounded model-authored input for the existing options validator."""

    model_config = ConfigDict(extra="forbid")

    item_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    title: Annotated[str, Field(strict=True, min_length=1, max_length=300)]
    body: Annotated[str, Field(strict=True, min_length=1, max_length=_MAX_OPTION_TEXT_LENGTH)]
    tradeoff: Annotated[str, Field(strict=True, min_length=1, max_length=_MAX_OPTION_TEXT_LENGTH)]
    label: Annotated[str, Field(strict=True, min_length=1, max_length=80)] = "Suggested"
    start_hour: Annotated[float, Field(strict=True)] | None = None
    day_date: Annotated[str, Field(strict=True, min_length=1, max_length=10)] | None = None
    duration_min: Annotated[int, Field(strict=True)] | None = None


def _validated_safe_patch(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        raise ValueError("safe_patch must contain at least one allowed field")
    if set(value) - _PYDANTIC_PATCH_FIELDS or any(item in (None, "") for item in value.values()):
        raise ValueError("safe_patch contains unsupported or blank values")
    if any(isinstance(item, (dict, list, tuple, set)) for item in value.values()):
        raise ValueError("safe_patch values must be scalar")
    def number(field: str, low: float, high: float | None = None, exclusive: bool = False) -> bool:
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            return False
        return float(item) >= low and (high is None or (float(item) < high if exclusive else float(item) <= high))
    if "start_hour" in value and not number("start_hour", 0, 24, True):
        raise ValueError("start_hour must be within one calendar day")
    if "duration_min" in value and (isinstance(value["duration_min"], bool) or not isinstance(value["duration_min"], int) or value["duration_min"] <= 0):
        raise ValueError("duration_min must be a positive integer")
    if "price_per_person" in value and not number("price_per_person", 0):
        raise ValueError("price_per_person must be non-negative")
    if "lat" in value and not number("lat", -90, 90):
        raise ValueError("lat must be within geographic bounds")
    if "lng" in value and not number("lng", -180, 180):
        raise ValueError("lng must be within geographic bounds")
    venue = {"title", "place", "lat", "lng"}
    if venue & set(value) and not venue.issubset(value):
        raise ValueError("replacement venue fields must be complete")
    if any(field in value and (type(value[field]) is not str or not value[field].strip()) for field in {"title", "place"}):
        raise ValueError("replacement title and place must be non-empty strings")
    if "day_date" in value:
        if type(value["day_date"]) is not str:
            raise ValueError("day_date must be an ISO date")
        try:
            date.fromisoformat(value["day_date"])
        except ValueError as error:
            raise ValueError("day_date must be an ISO date") from error
    return dict(value)


def _replacement_evidence(output: object) -> tuple[ReplacementCandidateEvidence, ...]:
    if not isinstance(output, Mapping) or not isinstance(output.get("candidates"), list):
        return ()
    candidates: list[ReplacementCandidateEvidence] = []
    for raw in output["candidates"]:
        if not isinstance(raw, Mapping):
            continue
        try:
            candidates.append(ReplacementCandidateEvidence(
                candidate_id=raw["candidate_id"], title=raw["title"], place=raw["place"],
                lat=raw["lat"], lng=raw["lng"], price_per_person=raw.get("price_per_person"),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(candidates)


def _options_from_tool(
    output: object, tool_execution_evidence: list[ToolExecutionEvidence],
) -> tuple[AgentCandidateOption, ...]:
    if not isinstance(output, Mapping) or not isinstance(output.get("options"), list):
        return ()
    options: list[AgentCandidateOption] = []
    for raw in output["options"]:
        if not isinstance(raw, Mapping):
            continue
        patch = raw.get("patch")
        try:
            safe_patch = {} if patch == {} else _validated_safe_patch(patch if isinstance(patch, dict) else {})
        except ValueError:
            continue
        # ``propose_options`` has no replacement authority of its own. Even a
        # malformed or future handler response cannot smuggle venue fields into
        # RuntimeResult without an earlier, same-run approved lookup.
        if not _replacement_patch_is_evidenced(safe_patch, tool_execution_evidence):
            continue
        required = ("id", "label", "title", "body", "tradeoff", "item_id")
        if not all(type(raw.get(key)) is str and raw[key].strip() for key in required):
            continue
        options.append(AgentCandidateOption(
            id=raw["id"], label=raw["label"], title=raw["title"], body=raw["body"],
            tradeoff=raw["tradeoff"], item_ref=raw["item_id"], safe_patch=safe_patch,
        ))
    return tuple(options)


def _replacement_patch_is_evidenced(
    patch: Mapping[str, object], evidence: list[ToolExecutionEvidence]
) -> bool:
    fields = ("title", "place", "lat", "lng", "price_per_person")
    if not any(field in patch for field in fields):
        return True
    if not all(field in patch for field in ("title", "place", "lat", "lng")):
        return False
    for entry in evidence:
        if entry.tool_name != "find_replacement_place" or entry.status not in {"succeeded", "cache_hit"}:
            continue
        for candidate in entry.replacement_candidates:
            if (candidate.title, candidate.place, candidate.lat, candidate.lng) != (
                patch["title"], patch["place"], patch["lat"], patch["lng"],
            ):
                continue
            if "price_per_person" not in patch:
                if candidate.price_per_person is None:
                    return True
                continue
            if candidate.price_per_person == patch["price_per_person"]:
                return True
    return False


@dataclass
class _PydanticRunDeps:
    """Ephemeral run state; it contains no Session or ORM entities."""

    tools_by_name: Mapping[str, RuntimeReadTool]
    deadline: AgentExecutionContext
    read_item_refs: set[str] = field(default_factory=set)
    read_item_refs_by_day_query: dict[str, set[str]] = field(default_factory=dict)
    tool_trajectory: list[str] = field(default_factory=list)
    tool_execution_evidence: list[ToolExecutionEvidence] = field(default_factory=list)
    candidate_options: list[AgentCandidateOption] = field(default_factory=list)
    cache: dict[str, object] = field(default_factory=dict)
    tool_timeout_observed: bool = False


def _tool_cache_key(name: str, arguments: Mapping[str, object]) -> str:
    return name + ":" + json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"), default=str)


def _day_query_key(day: object) -> str:
    return day.strip().casefold() if isinstance(day, str) else ""


def _invoke_scoped_tool(
    deps: _PydanticRunDeps, name: str, arguments: dict[str, object], telemetry_context: TelemetryContext | None,
    *, require_plan: bool = False, require_item: bool = False, required_item_ids: tuple[str, ...] = (),
) -> object:
    """One per-run guarded and deadline-bound read dispatch for Pydantic."""

    def reject_guard(message: str) -> None:
        deps.tool_execution_evidence.append(ToolExecutionEvidence(tool_name=name, status="guard_rejected"))
        _emit_pydantic_tool_call(telemetry_context, name, ToolDispatchStatus.GUARD_REJECTED)
        raise ModelRetry(message)

    tool = deps.tools_by_name.get(name)
    if tool is None:
        deps.tool_execution_evidence.append(ToolExecutionEvidence(tool_name="unsupported", status="unsupported"))
        raise ModelRetry("That scoped tool is unavailable.")
    if require_plan and not deps.read_item_refs:
        reject_guard("Read the Current Plan before using this tool.")
    if require_item and (not isinstance(arguments.get("item_id"), str) or arguments["item_id"] not in deps.read_item_refs):
        reject_guard("Use an item id returned by the Current Plan in this run.")
    if any(item_id not in deps.read_item_refs for item_id in required_item_ids):
        reject_guard("Use only conflict item ids returned by the Current Plan in this run.")
    day_query = _day_query_key(arguments.get("day"))
    if required_item_ids and day_query not in {"", "all", "entire trip", "whole trip"}:
        ids_for_day = deps.read_item_refs_by_day_query.get(day_query, set())
        if any(item_id not in ids_for_day for item_id in required_item_ids):
            reject_guard("Read the requested day and use only conflict item ids returned for that day.")
    key = _tool_cache_key(name, arguments)
    try:
        started = deps.deadline.before_tool()
        if key in deps.cache:
            output = deps.cache[key]
            status = "cache_hit"
            dispatch_status = ToolDispatchStatus.CACHE_HIT
        else:
            output = tool.invoke(**arguments)
            status = "succeeded"
            dispatch_status = ToolDispatchStatus.HANDLER_SUCCEEDED
        deps.deadline.after_tool(started)
    except AgentToolDeadlineExceeded:
        deps.tool_timeout_observed = True
        _emit_pydantic_tool_call(telemetry_context, name, ToolDispatchStatus.TOOL_DEADLINE_EXCEEDED)
        raise
    except Exception:
        _emit_pydantic_tool_call(telemetry_context, name, ToolDispatchStatus.HANDLER_FAILED)
        raise
    if not isinstance(output, dict):
        _emit_pydantic_tool_call(telemetry_context, name, ToolDispatchStatus.HANDLER_FAILED)
        raise ModelRetry("The scoped tool returned an invalid safe projection.")
    if status == "succeeded":
        # Cache only a projection that has passed the adapter's public type
        # boundary. A malformed handler return must never become a reusable
        # same-run value.
        deps.cache[key] = output
    candidates = _replacement_evidence(output) if name == "find_replacement_place" else ()
    deps.tool_execution_evidence.append(ToolExecutionEvidence(
        tool_name=name, status=status, replacement_candidates=candidates,
    ))
    deps.tool_trajectory.append(name)
    _emit_pydantic_tool_call(telemetry_context, name, dispatch_status)
    return output


class _DeadlineBoundModel(WrapperModel):
    """Apply PR-01C remaining provider budget to every Pydantic request."""

    def __init__(
        self, wrapped: Model, deadline: AgentExecutionContext,
        telemetry_context: TelemetryContext | None = None,
    ) -> None:
        super().__init__(wrapped)
        self._deadline = deadline
        self.request_count = 0
        self._telemetry_context = telemetry_context

    async def request(
        self,
        messages: list[object],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.request_count += 1
        invocation_index = self.request_count
        started_at = time.perf_counter()
        provider_timeout = self._deadline.before_provider()
        bounded_settings = dict(model_settings or {})
        bounded_settings["timeout"] = provider_timeout
        try:
            response = await self.wrapped.request(
                cast(list[Any], messages), cast(ModelSettings, bounded_settings), model_request_parameters,
            )
        except Exception as error:
            _emit_pydantic_model_call(
                context=self._telemetry_context, model=self.wrapped,
                invocation_index=invocation_index, outcome=InvocationOutcome.FAILED,
                elapsed_ms=(time.perf_counter() - started_at) * 1000, error=error,
            )
            raise
        _emit_pydantic_model_call(
            context=self._telemetry_context, model=self.wrapped,
            invocation_index=invocation_index, outcome=InvocationOutcome.RETURNED,
            elapsed_ms=(time.perf_counter() - started_at) * 1000, error=None,
        )
        # The wrapper invocation has returned even if the local request budget
        # subsequently rejects consumption of that response.
        self._deadline.after_provider()
        return response


@dataclass(frozen=True)
class PydanticChatAgentRuntime(ChatAgentRuntime):
    """Typed, read-only Pydantic adapter behind the common PR-04A contract."""

    model: Model
    system_prompt: str

    def run(
        self,
        request: RuntimeRequest,
        capability: ReadTripCapability,
        *,
        execution: AgentExecutionConfig,
    ) -> RuntimeResult:
        telemetry_context = execution.telemetry_context
        trace_id = trace.new_trace_id()
        if telemetry_context is not None:
            emit_event_safely(lambda: RuntimeSelected(
                context=replace(telemetry_context, trace_id=trace_id, execution_id=None),
                runtime_name=RuntimeName.PYDANTIC,
            ))
        execution_id = trace.new_trace_id()
        try:
            execution_context = (
                replace(telemetry_context, trace_id=trace_id, execution_id=execution_id)
                if telemetry_context is not None else None
            )
        except Exception:
            execution_context = None
        def worker(deadline: AgentExecutionContext) -> RuntimeResult:
            # Keep PR-04A's pre-capability deadline ordering and let the
            # capability own the independent worker Session for the full run.
            deadline.ensure_request_active()

            def run_with_tools(read_tools: tuple[RuntimeReadTool, ...]) -> RuntimeResult:
                deadline.ensure_request_active()
                return asyncio.run(self._run_async(request, read_tools, deadline, execution_context, trace_id))

            return capability.run_with_read_only_tools(run_with_tools)

        try:
            return run_agent_with_deadline(
                worker=worker, config=execution, execution_id=execution_id,
                telemetry_context=execution_context,
            )
        except Exception as error:
            return _failure_result(error)

    async def _run_async(
        self,
        request: RuntimeRequest,
        read_tools: tuple[RuntimeReadTool, ...],
        deadline: AgentExecutionContext,
        telemetry_context: TelemetryContext | None,
        trace_id: str,
    ) -> RuntimeResult:
        tools_by_name = {tool.name: tool for tool in read_tools}
        if "get_current_plan" not in tools_by_name:
            raise _ToolSurfaceError("get_current_plan is not available in the scoped capability")

        deps = _PydanticRunDeps(tools_by_name=tools_by_name, deadline=deadline)
        deadline_model = _DeadlineBoundModel(self.model, deadline, telemetry_context)
        selected_item_hint = (
            " The client selected item " + json.dumps(request.selected_item_ref)
            + " as context only; it is not authorization and may be stale or invalid."
            if request.selected_item_ref is not None else ""
        )
        agent = Agent(
            deadline_model,
            deps_type=_PydanticRunDeps,
            output_type=[
                ToolOutput(_RuntimeReply, name="runtime_reply"),
                ToolOutput(_RuntimeClarification, name="runtime_clarification"),
                ToolOutput(_RuntimeSuggestedChange, name="runtime_suggested_change"),
            ],
            instructions=(
                f"{self.system_prompt}\n"
                "Use only the scoped read-only tools for safe trip and plan facts. "
                "Read a plan item during this run before suggesting a change. "
                "Before finding a replacement, read the Current Plan and use a returned item id. "
                "Replacement fields must exactly match a replacement tool candidate. "
                "Return a typed reply, clarification, or suggested change. "
                "Never write data or claim a change was applied."
                f"{selected_item_hint}"
            ),
            retries=request.limits.guard_reject_limit,
            # PR-01C owns tool timing. Do not layer Pydantic AI's independent
            # timer over it: this preserves one measured before_tool/invoke/
            # after_tool boundary and avoids double-counting timeout semantics.
            tool_timeout=None,
        )

        @agent.tool(retries=0)
        def get_current_plan(
            ctx: RunContext[_PydanticRunDeps],
            day: Annotated[str, Field(strict=True, min_length=1)],
        ) -> dict[str, object]:
            """Read the Current Plan for a day in the capability-bound trip."""

            try:
                output = _invoke_scoped_tool(ctx.deps, "get_current_plan", {"day": day}, telemetry_context)
            except AgentToolDeadlineExceeded:
                raise
            if not isinstance(output, dict):
                raise ModelRetry("The scoped plan read returned an invalid safe projection.")
            read_item_refs = _record_read_item_refs(output, ctx.deps.read_item_refs)
            ctx.deps.read_item_refs_by_day_query[_day_query_key(day)] = read_item_refs
            return output

        if "get_trip_facts" in tools_by_name:
            @agent.tool(retries=0)
            def get_trip_facts(ctx: RunContext[_PydanticRunDeps]) -> dict[str, object]:
                """Read only safe facts for the capability-bound trip."""
                output = _invoke_scoped_tool(ctx.deps, "get_trip_facts", {}, telemetry_context)
                if not isinstance(output, dict):
                    raise ModelRetry("The scoped trip facts projection was invalid.")
                return output

        if "find_replacement_place" in tools_by_name:
            @agent.tool(retries=0)
            def find_replacement_place(
                ctx: RunContext[_PydanticRunDeps],
                item_id: Annotated[str, Field(strict=True, min_length=1)],
                keywords: list[Annotated[str, Field(strict=True, min_length=1)]] | None = None,
            ) -> dict[str, object]:
                """Find approved replacement candidates for a read Current Plan item."""
                output = _invoke_scoped_tool(
                    ctx.deps, "find_replacement_place", {"item_id": item_id, "keywords": keywords or []},
                    telemetry_context, require_plan=True, require_item=True,
                )
                if not isinstance(output, dict):
                    raise ModelRetry("The scoped replacement lookup was invalid.")
                return output

        if "propose_options" in tools_by_name:
            @agent.tool(retries=0)
            def propose_options(
                ctx: RunContext[_PydanticRunDeps],
                conflict_description: Annotated[
                    str, Field(strict=True, min_length=1, max_length=_MAX_CONFLICT_DESCRIPTION_LENGTH)
                ],
                day: Annotated[str, Field(strict=True, min_length=1, max_length=_MAX_OPTION_DAY_LENGTH)] = "all",
                conflict_item_ids: Annotated[
                    list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]],
                    Field(max_length=_MAX_CONFLICT_ITEM_IDS),
                ] | None = None,
                suggestions: Annotated[
                    list[_RuntimeOptionSuggestion], Field(max_length=_MAX_OPTION_SUGGESTIONS),
                ] | None = None,
            ) -> dict[str, object]:
                """Return bounded advisory options for a scoped planning conflict."""
                grounded_conflict_ids = tuple(conflict_item_ids or ())
                grounded_suggestion_item_ids = tuple(
                    suggestion.item_id for suggestion in (suggestions or [])
                )
                output = _invoke_scoped_tool(
                    ctx.deps,
                    "propose_options",
                    {
                        "conflict_description": conflict_description,
                        "day": day,
                        "conflict_item_ids": list(grounded_conflict_ids),
                        "suggestions": [
                            suggestion.model_dump(exclude_none=True)
                            for suggestion in (suggestions or [])
                        ],
                    },
                    telemetry_context,
                    require_plan=True,
                    # A model-authored suggestion is no less consequential than
                    # a declared conflict id: the downstream handler can turn
                    # either into an advisory option. Both must be facts the
                    # scoped Current Plan returned during this execution.
                    required_item_ids=(
                        *grounded_conflict_ids,
                        *grounded_suggestion_item_ids,
                    ),
                )
                if not isinstance(output, dict):
                    raise ModelRetry("The scoped option result was invalid.")
                ctx.deps.candidate_options.extend(
                    _options_from_tool(output, ctx.deps.tool_execution_evidence)
                )
                return output

        @agent.output_validator
        def validate_output(
            ctx: RunContext[_PydanticRunDeps],
            output: _RuntimeReply | _RuntimeClarification | _RuntimeSuggestedChange,
        ) -> _RuntimeReply | _RuntimeClarification | _RuntimeSuggestedChange:
            if isinstance(output, _RuntimeSuggestedChange):
                if output.item_ref not in ctx.deps.read_item_refs:
                    raise ModelRetry(
                        "Read the proposed item through scoped get_current_plan before suggesting it."
                    )
                if not _replacement_patch_is_evidenced(output.safe_patch, ctx.deps.tool_execution_evidence):
                    raise ModelRetry(
                        "Replacement fields must exactly match a candidate from the scoped replacement lookup."
                    )
            return output

        started_at = time.perf_counter()
        try:
            result = await agent.run(
                request.message,
                deps=deps,
                message_history=_history_messages(request.history),
                usage_limits=_usage_limits(request),
            )
        except Exception as error:
            if deps.tool_timeout_observed:
                raise AgentToolDeadlineExceeded(
                    "Read-only Pydantic tool exceeded its deadline"
                ) from error
            raise
        output = result.output
        observation = RuntimeObservation(
            trace_id=trace_id,
            round_count=deadline_model.request_count,
            total_tokens=_reported_total_tokens(result.usage),
            total_elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            safe_detail=_safe_observation_detail(deps.tool_trajectory),
        )
        if isinstance(output, _RuntimeClarification):
            return RuntimeResult(
                reply=output.reply,
                outcome=AgentClarification(),
                candidate_options=tuple(ctx_option for ctx_option in deps.candidate_options),
                observation=observation,
                tool_execution_evidence=tuple(deps.tool_execution_evidence),
            )
        if isinstance(output, _RuntimeSuggestedChange):
            return RuntimeResult(
                reply=output.reply,
                outcome=AgentSuggestedChange(
                    item_ref=output.item_ref,
                    safe_patch=dict(output.safe_patch),
                ),
                candidate_options=tuple(deps.candidate_options),
                observation=observation,
                tool_execution_evidence=tuple(deps.tool_execution_evidence),
            )
        return RuntimeResult(
            reply=output.reply,
            outcome=AgentReplyOnly(),
            candidate_options=tuple(deps.candidate_options),
            observation=observation,
            tool_execution_evidence=tuple(deps.tool_execution_evidence),
        )


class _ToolSurfaceError(ValueError):
    """The common capability did not provide this adapter's minimum tool."""


def _usage_limits(request: RuntimeRequest) -> UsageLimits:
    """Conservative bounded mapping; see the PR-04B report for parity gaps."""

    return UsageLimits(
        request_limit=request.limits.max_rounds,
        tool_calls_limit=request.limits.max_rounds,
        total_tokens_limit=request.limits.max_total_tokens,
        output_tokens_limit=request.limits.max_tokens,
    )


def _history_messages(history: tuple[RuntimeHistoryTurn, ...]) -> list[ModelRequest | ModelResponse]:
    messages: list[ModelRequest | ModelResponse] = []
    for turn in history:
        if turn.role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(turn.text)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(turn.text)]))
    return messages


def _record_read_item_refs(output: object, destination: set[str]) -> set[str]:
    recorded: set[str] = set()
    if not isinstance(output, dict):
        return recorded
    days = output.get("days")
    if not isinstance(days, list):
        return recorded
    for day in days:
        if not isinstance(day, dict):
            continue
        items = day.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                destination.add(item["id"])
                recorded.add(item["id"])
    return recorded


def _reported_total_tokens(usage: object) -> int | None:
    total_tokens = getattr(usage, "total_tokens", None)
    return total_tokens if isinstance(total_tokens, int) and total_tokens > 0 else None


def _emit_pydantic_model_call(
    *,
    context: TelemetryContext | None,
    model: Model,
    invocation_index: int,
    outcome: InvocationOutcome,
    elapsed_ms: float,
    error: Exception | None,
) -> None:
    """Observe one Pydantic model wrapper call without guessing provider I/O."""
    if context is None:
        return
    try:
        model_name = str(getattr(model, "model_name", ""))
        source = (
            InvocationSource.FAKE
            if model.__class__.__name__ == "FunctionModel"
            else InvocationSource.PROVIDER_SDK
        )
        # Pydantic AI's wrapper response does not establish that a usage value
        # was provider-originated (and default zero values are ambiguous).
        # Preserve the call fact but decline to report usage until that
        # provenance is explicitly available at this seam.
        emit_event_safely(lambda: ModelCallCompleted(
            context=context,
            model_invocation_index=invocation_index,
            invocation_source=source,
            provider=ProviderName.DEEPSEEK if "deepseek" in model_name.lower() else ProviderName.UNAPPROVED_PROVIDER,
            model_id=(ModelId.DEEPSEEK_FLASH if model_name == "deepseek-flash" else ModelId.DEEPSEEK_V4_FLASH if model_name == "deepseek-v4-flash" else ModelId.DEEPSEEK_V4_PRO if model_name == "deepseek-v4-pro" else ModelId.UNAPPROVED_MODEL),
            invocation_outcome=outcome,
            failure_category=None if error is None else _pydantic_failure_category(error),
            elapsed_ms=round(max(elapsed_ms, 0), 2),
            usage_status=UsageStatus.NOT_REPORTED,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        ))
    except Exception:
        return


def _pydantic_failure_category(error: Exception) -> FailureCategory:
    if isinstance(error, TimeoutError) or error.__class__.__name__ in {"APITimeoutError", "ConnectTimeout", "ReadTimeout"}:
        return FailureCategory.PROVIDER_TIMEOUT
    status = getattr(error, "status_code", None)
    if status == 401:
        return FailureCategory.PROVIDER_UNAUTHORIZED
    if status == 429:
        return FailureCategory.PROVIDER_RATE_LIMITED
    if isinstance(status, int) and 500 <= status <= 599:
        return FailureCategory.PROVIDER_SERVER_ERROR
    if isinstance(status, int) and 400 <= status <= 499:
        return FailureCategory.PROVIDER_CLIENT_ERROR
    if error.__class__.__name__ in {"APIConnectionError", "ConnectError", "ConnectionError"}:
        return FailureCategory.PROVIDER_NETWORK_ERROR
    return FailureCategory.UNEXPECTED_RUNTIME_ERROR


def _emit_pydantic_tool_call(
    context: TelemetryContext | None,
    name: str,
    status: ToolDispatchStatus,
) -> None:
    if context is None:
        return
    try:
        emit_event_safely(lambda: ToolCallCompleted(
            context=context,
            tool_name=ApprovedToolName(name),
            tool_dispatch_status=status,
            handler_executed=status in {
                ToolDispatchStatus.HANDLER_SUCCEEDED,
                ToolDispatchStatus.HANDLER_FAILED,
                ToolDispatchStatus.TOOL_DEADLINE_EXCEEDED,
            },
            cache_hit=status is ToolDispatchStatus.CACHE_HIT,
        ))
    except Exception:
        return


def _safe_observation_detail(tool_trajectory: list[str]) -> str:
    return "read_tools=" + ",".join(tool_trajectory) if tool_trajectory else ""


def _failure_result(error: Exception) -> RuntimeResult:
    normalized_kind = _normalized_failure_kind(error)
    evidence_status = "failed"
    if isinstance(error, (AgentProviderDeadlineExceeded, AgentToolDeadlineExceeded, TimeoutError)):
        evidence_status = "timed_out"
    elif isinstance(error, AgentExecutionDeadlineExceeded):
        evidence_status = "cancelled"
    return RuntimeResult(
        reply="",
        outcome=AgentReplyOnly(),
        candidate_options=(),
        observation=RuntimeObservation(
            trace_id=None,
            round_count=None,
            total_tokens=None,
            total_elapsed_ms=None,
            failure=RuntimeFailure(
                normalized_kind=normalized_kind,
                technical_kind=error.__class__.__name__,
            ),
        ),
        tool_execution_evidence=(
            ToolExecutionEvidence(tool_name="agent_execution", status=evidence_status),
        ),
    )


def _normalized_failure_kind(error: Exception) -> RuntimeFailureKind:
    if isinstance(error, AgentProviderDeadlineExceeded):
        return "provider_timeout"
    if isinstance(error, AgentToolDeadlineExceeded):
        return "tool_timeout"
    if isinstance(error, AgentRequestDeadlineExceeded):
        return "request_timeout"
    if isinstance(error, AgentExecutionCapacityExceeded):
        return "runtime_capacity"
    if isinstance(error, AgentExecutionDeadlineExceeded):
        return "request_timeout"
    if isinstance(error, UsageLimitExceeded):
        return "usage_limit"
    if isinstance(error, (ModelRetry, UnexpectedModelBehavior)):
        return "malformed_runtime_output"
    if isinstance(error, TimeoutError):
        return "provider_timeout"
    return "unexpected_exception"
