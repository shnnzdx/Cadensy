"""Isolated Pydantic AI implementation of the PR-04A Runtime contract.

This adapter is deliberately not composed into the Chat route or Chat Service.
It consumes only the framework-neutral ``ReadTripCapability`` / ``RuntimeReadTool``
surface, runs read-only tools under PR-01C's existing deadline boundary, and
returns only common Runtime DTOs.  Provider construction remains outside this
module and is not part of PR-04B.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, Field, field_validator
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
)


# PR-04B has scoped Current Plan reads only. Replacement-sensitive fields need
# separately reviewed candidate provenance and therefore cannot be suggested
# by this minimal adapter.
_PYDANTIC_PR04B_PATCH_FIELDS = frozenset(
    {"start_hour", "day_date", "duration_min"}
)


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
        if not value:
            raise ValueError("safe_patch must contain at least one allowed field")
        unexpected = set(value) - _PYDANTIC_PR04B_PATCH_FIELDS
        if unexpected:
            raise ValueError("safe_patch contains unsupported fields")
        if any(item in (None, "") for item in value.values()):
            raise ValueError("safe_patch values must not be blank")

        if "start_hour" in value:
            start_hour = value["start_hour"]
            if (
                isinstance(start_hour, bool)
                or not isinstance(start_hour, (int, float))
                or not 0 <= float(start_hour) < 24
            ):
                raise ValueError("start_hour must be within one calendar day")
        if "duration_min" in value:
            duration = value["duration_min"]
            if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
                raise ValueError("duration_min must be a positive integer")
        if "price_per_person" in value:
            price = value["price_per_person"]
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                raise ValueError("price_per_person must be non-negative")
        if "lat" in value and not _numeric_in_range(value["lat"], -90, 90):
            raise ValueError("lat must be within geographic bounds")
        if "lng" in value and not _numeric_in_range(value["lng"], -180, 180):
            raise ValueError("lng must be within geographic bounds")
        if "day_date" in value:
            day_date = value["day_date"]
            if not isinstance(day_date, str):
                raise ValueError("day_date must be an ISO date")
            try:
                date.fromisoformat(day_date)
            except ValueError as error:
                raise ValueError("day_date must be an ISO date") from error
        return value


def _numeric_in_range(value: object, lower: float, upper: float) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and lower <= value <= upper


@dataclass
class _PydanticRunDeps:
    """Ephemeral run state; it contains no Session or ORM entities."""

    tools_by_name: Mapping[str, RuntimeReadTool]
    deadline: AgentExecutionContext
    read_item_refs: set[str] = field(default_factory=set)
    tool_trajectory: list[str] = field(default_factory=list)
    tool_timeout_observed: bool = False


class _DeadlineBoundModel(WrapperModel):
    """Apply PR-01C remaining provider budget to every Pydantic request."""

    def __init__(self, wrapped: Model, deadline: AgentExecutionContext) -> None:
        super().__init__(wrapped)
        self._deadline = deadline
        self.request_count = 0

    async def request(
        self,
        messages: list[object],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        provider_timeout = self._deadline.before_provider()
        bounded_settings = dict(model_settings or {})
        bounded_settings["timeout"] = provider_timeout
        response = await self.wrapped.request(
            cast(list[Any], messages),
            cast(ModelSettings, bounded_settings),
            model_request_parameters,
        )
        self._deadline.after_provider()
        self.request_count += 1
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
        def worker(deadline: AgentExecutionContext) -> RuntimeResult:
            # Keep PR-04A's pre-capability deadline ordering and let the
            # capability own the independent worker Session for the full run.
            deadline.ensure_request_active()

            def run_with_tools(read_tools: tuple[RuntimeReadTool, ...]) -> RuntimeResult:
                deadline.ensure_request_active()
                return asyncio.run(self._run_async(request, read_tools, deadline))

            return capability.run_with_read_only_tools(run_with_tools)

        try:
            return run_agent_with_deadline(worker=worker, config=execution)
        except Exception as error:
            return _failure_result(error)

    async def _run_async(
        self,
        request: RuntimeRequest,
        read_tools: tuple[RuntimeReadTool, ...],
        deadline: AgentExecutionContext,
    ) -> RuntimeResult:
        tools_by_name = {tool.name: tool for tool in read_tools}
        if "get_current_plan" not in tools_by_name:
            raise _ToolSurfaceError("get_current_plan is not available in the scoped capability")

        deps = _PydanticRunDeps(tools_by_name=tools_by_name, deadline=deadline)
        deadline_model = _DeadlineBoundModel(self.model, deadline)
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
                "Use only the scoped read-only get_current_plan tool for plan facts. "
                "Read a plan item during this run before suggesting a change. "
                "Return a typed reply, clarification, or suggested change. "
                "Never write data or claim a change was applied."
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

            tool = ctx.deps.tools_by_name["get_current_plan"]
            tool_started_at = ctx.deps.deadline.before_tool()
            try:
                output = tool.invoke(day=day)
                # If a blocking read completed late, this raises before Pydantic
                # can incorporate its result into a subsequent model request.
                ctx.deps.deadline.after_tool(tool_started_at)
            except AgentToolDeadlineExceeded:
                # A PR-01C deadline is terminal for this run. Pydantic's own
                # tool timeout is disabled and this tool has no Pydantic retry
                # budget, so the adapter re-surfaces this as tool_timeout.
                ctx.deps.tool_timeout_observed = True
                raise
            _record_read_item_refs(output, ctx.deps.read_item_refs)
            ctx.deps.tool_trajectory.append("get_current_plan")
            if not isinstance(output, dict):
                raise ModelRetry("The scoped plan read returned an invalid safe projection.")
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
            trace_id=deadline.execution_id,
            round_count=deadline_model.request_count,
            total_tokens=_reported_total_tokens(result.usage),
            total_elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            safe_detail=_safe_observation_detail(deps.tool_trajectory),
        )
        if isinstance(output, _RuntimeClarification):
            return RuntimeResult(
                reply=output.reply,
                outcome=AgentClarification(),
                candidate_options=(),
                observation=observation,
            )
        if isinstance(output, _RuntimeSuggestedChange):
            return RuntimeResult(
                reply=output.reply,
                outcome=AgentSuggestedChange(
                    item_ref=output.item_ref,
                    safe_patch=dict(output.safe_patch),
                ),
                candidate_options=(),
                observation=observation,
            )
        return RuntimeResult(
            reply=output.reply,
            outcome=AgentReplyOnly(),
            candidate_options=(),
            observation=observation,
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


def _record_read_item_refs(output: object, destination: set[str]) -> None:
    if not isinstance(output, dict):
        return
    days = output.get("days")
    if not isinstance(days, list):
        return
    for day in days:
        if not isinstance(day, dict):
            continue
        items = day.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                destination.add(item["id"])


def _reported_total_tokens(usage: object) -> int | None:
    total_tokens = getattr(usage, "total_tokens", None)
    return total_tokens if isinstance(total_tokens, int) and total_tokens > 0 else None


def _safe_observation_detail(tool_trajectory: list[str]) -> str:
    return "read_tools=" + ",".join(tool_trajectory) if tool_trajectory else ""


def _failure_result(error: Exception) -> RuntimeResult:
    normalized_kind = _normalized_failure_kind(error)
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
