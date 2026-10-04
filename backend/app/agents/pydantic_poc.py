"""PR-03-only Pydantic AI compatibility prototype.

This module is deliberately not imported by the HTTP API or the Legacy Custom
Runtime. It validates a typed, read-only capability shape that can later sit
behind the framework-neutral execution boundary without transferring a request
SQLAlchemy Session to Agent work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput, UsageLimits
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.usage import RunUsage
from sqlalchemy.orm import Session

from .execution import (
    AgentExecutionConfig,
    AgentExecutionContext,
    run_agent_with_deadline,
)
from .tools import build_read_only_trip_tools


@dataclass(frozen=True)
class TripReadCapability:
    """Immutable, trip-scoped access capability for a PoC Agent run.

    ``session_factory`` creates an independent short-lived Session for each
    tool invocation. It must never be the request-scoped Session object.
    """

    trip_id: str
    actor_membership_id: str
    session_factory: Callable[[], Session]


class TripFactsOutput(BaseModel):
    """A deliberately small, model-safe typed projection of trip facts."""

    destination: str
    member_count: int


@dataclass(frozen=True)
class ReadOnlyProbeObservation:
    """Normalized, framework-neutral evidence from one Fake Model probe."""

    output_kind: str
    tool_trajectory: tuple[str, ...]
    facts: dict[str, object]
    usage: RunUsage


class PlanItemOutput(BaseModel):
    """Safe, typed fact returned by the Pydantic function tool."""

    id: str
    title: str
    start_hour: float
    settledness: str


class SuggestedAction(BaseModel):
    """Model-proposed action only; it is not a server preview or revision."""

    kind: Literal["move_time"]
    item_id: str
    new_start_hour: float


class ModelChangePreview(BaseModel):
    """One discriminated typed output case from the Model."""

    output_kind: Literal["change_preview"]
    suggested_action: SuggestedAction


class ModelClarification(BaseModel):
    """The alternative typed output case when facts are insufficient."""

    output_kind: Literal["clarification"]
    question: str


ModelOutput: TypeAlias = Annotated[
    ModelChangePreview | ModelClarification,
    Field(discriminator="output_kind"),
]


@dataclass(frozen=True)
class StructuredPreviewObservation:
    """Framework-neutral observation shape for PR-02-compatible evaluation."""

    output_kind: str
    suggested_action: dict[str, object] | None
    tool_trajectory: tuple[str, ...]
    error_classification: str | None
    usage: RunUsage
    safety_metadata: dict[str, object]


@dataclass
class _PocRunState:
    """Ephemeral, trace-safe run state kept outside immutable dependencies."""

    tool_trajectory: list[str] = field(default_factory=list)
    read_item_ids: set[str] = field(default_factory=set)


async def run_fake_read_only_probe(
    capability: TripReadCapability,
    *,
    usage_limits: UsageLimits | None = None,
) -> ReadOnlyProbeObservation:
    """Run one actual read-only Pydantic function tool through ``TestModel``.

    ``TestModel`` is a local Pydantic AI fake; it makes no provider request.
    The tool itself delegates to the existing, trip-scoped Cadensy read-only
    tool builder, so its returned facts come from the database fixture.
    """
    trajectory: list[str] = []
    facts: dict[str, object] = {}
    agent = Agent(
        TestModel(call_tools=["get_trip_facts"]),
        deps_type=TripReadCapability,
        output_type=str,
        instructions="Use the read-only trip facts tool before answering.",
        tool_timeout=1.0,
    )

    @agent.tool
    def get_trip_facts(ctx: RunContext[TripReadCapability]) -> TripFactsOutput:
        """Read safe trip facts for the dependency-bound trip only."""
        with ctx.deps.session_factory() as worker_db:
            tools = build_read_only_trip_tools(
                worker_db,
                trip_id=ctx.deps.trip_id,
                actor_membership_id=ctx.deps.actor_membership_id,
            )
            raw_facts = next(
                tool for tool in tools if tool.name == "get_trip_facts"
            ).handler()
        safe_facts = TripFactsOutput.model_validate(raw_facts)
        facts.update(safe_facts.model_dump())
        trajectory.append("get_trip_facts")
        return safe_facts

    result = await agent.run(
        "Read the trip facts.",
        deps=capability,
        usage_limits=usage_limits or UsageLimits(request_limit=2, tool_calls_limit=1),
    )
    return ReadOnlyProbeObservation(
        output_kind="reply_only",
        tool_trajectory=tuple(trajectory),
        facts=facts,
        usage=result.usage,
    )


async def run_structured_preview_poc(
    capability: TripReadCapability,
    *,
    model: object,
    message: str,
    execution_context: AgentExecutionContext | None = None,
) -> StructuredPreviewObservation:
    """Run a typed structured-output PoC without using a production route.

    The supplied ``model`` is intentionally a test double in PR-03. The output
    validator ensures a model cannot preview an item that this same run has not
    first obtained through the actual trip-scoped read-only tool.
    """
    state = _PocRunState()
    agent = Agent(
        model,  # type: ignore[arg-type]
        deps_type=TripReadCapability,
        output_type=[
            ToolOutput(ModelChangePreview, name="change_preview"),
            ToolOutput(ModelClarification, name="clarification"),
        ],
        instructions=(
            "Use get_plan_item before producing a change preview. "
            "Return only a typed preview or clarification; never mutate data."
        ),
        retries=1,
        tool_timeout=1.0,
    )

    @agent.tool
    def get_plan_item(
        ctx: RunContext[TripReadCapability],
        item_id: Annotated[str, Field(strict=True, min_length=1)],
    ) -> PlanItemOutput:
        """Read one Current Plan item in the dependency-bound trip only."""
        tool_started_at = execution_context.before_tool() if execution_context else None
        try:
            with ctx.deps.session_factory() as worker_db:
                tools = build_read_only_trip_tools(
                    worker_db,
                    trip_id=ctx.deps.trip_id,
                    actor_membership_id=ctx.deps.actor_membership_id,
                )
                plan = next(
                    tool for tool in tools if tool.name == "get_current_plan"
                ).handler(day="all")
        finally:
            if tool_started_at is not None:
                execution_context.after_tool(tool_started_at)
        for day in plan["days"]:
            for item in day["items"]:
                if item["id"] == item_id:
                    state.tool_trajectory.append("get_plan_item")
                    state.read_item_ids.add(item_id)
                    return PlanItemOutput.model_validate(item)
        raise ModelRetry("The requested item is not in the scoped Current Plan.")

    @agent.output_validator
    def validate_output(
        _ctx: RunContext[TripReadCapability], output: ModelOutput
    ) -> ModelOutput:
        if isinstance(output, ModelChangePreview):
            action = output.suggested_action
            if action.item_id not in state.read_item_ids:
                raise ModelRetry("Read the proposed item through get_plan_item first.")
            if not 0.0 <= action.new_start_hour < 24.0:
                raise ModelRetry("new_start_hour must be within one calendar day.")
        return output

    provider_timeout = (
        execution_context.before_provider() if execution_context else None
    )
    result = await agent.run(
        message,
        deps=capability,
        model_settings={"timeout": provider_timeout} if provider_timeout else None,
        usage_limits=UsageLimits(request_limit=6, tool_calls_limit=2),
    )
    if execution_context:
        execution_context.after_provider()
    output = result.output
    suggested_action = (
        output.suggested_action.model_dump()
        if isinstance(output, ModelChangePreview)
        else None
    )
    return StructuredPreviewObservation(
        output_kind=output.output_kind,
        suggested_action=suggested_action,
        tool_trajectory=tuple(state.tool_trajectory),
        error_classification=None,
        usage=result.usage,
        # No request text, raw tool return, database IDs outside the approved
        # evaluation observation, or provider headers are emitted as trace data.
        safety_metadata={
            "read_only_tools_only": True,
            "request_session_shared": False,
            "trace_fields": ["tool_name", "output_kind", "usage"],
        },
    )


def run_structured_preview_with_deadline(
    capability: TripReadCapability,
    *,
    model: object,
    message: str,
    config: AgentExecutionConfig,
) -> StructuredPreviewObservation:
    """Use the existing PR-01C execution boundary around a Pydantic run.

    Cancellation is cooperative for an async model. A blocking synchronous
    model callback can still outlive the request; ``run_agent_with_deadline``
    discards it and records late completion rather than claiming to kill it.
    """
    return run_agent_with_deadline(
        worker=lambda deadline: run_structured_preview_poc(
            capability,
            model=model,
            message=message,
            execution_context=deadline,
        ),
        config=config,
    )


def classify_poc_exception(error: BaseException) -> str:
    """Map framework/provider failures to the PR-02 failure taxonomy."""
    if isinstance(error, UsageLimitExceeded):
        return "usage_limit_exceeded"
    if isinstance(error, ModelHTTPError):
        if error.status_code == 401:
            return "provider_auth_failed"
        if error.status_code == 429:
            return "provider_rate_limited"
        if 500 <= error.status_code <= 599:
            return "provider_unavailable"
        return "provider_http_error"
    if isinstance(error, TimeoutError):
        return "request_timeout"
    return "pydantic_ai_unclassified_failure"


def build_deepseek_chat_model(*, api_key: str) -> OpenAIChatModel:
    """Construct, but never invoke, the documented DeepSeek Chat model path.

    The PoC deliberately takes an explicit value rather than loading an
    environment credential. Tests pass a placeholder and perform no network
    request. The current project model is configured for Chat Completions, and
    thinking is disabled because structured output/tool choice needs it.
    """
    return OpenAIChatModel(
        "deepseek-v4-flash",
        provider=DeepSeekProvider(api_key=api_key),
        settings=OpenAIChatModelSettings(thinking=False),
    )
