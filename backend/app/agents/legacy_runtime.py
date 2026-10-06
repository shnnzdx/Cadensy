"""PR-04A characterization adapter for the existing Custom Runtime.

It intentionally reuses ``base.call_agent`` and PR-01C's execution boundary.
It is not imported by the Chat route or Chat Service in this PR.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, Literal

from sqlalchemy.orm import Session

from . import base
from .execution import (
    AgentExecutionCapacityExceeded,
    AgentExecutionConfig,
    AgentExecutionDeadlineExceeded,
    AgentProviderDeadlineExceeded,
    AgentRequestDeadlineExceeded,
    AgentToolDeadlineExceeded,
    run_agent_with_deadline,
)
from .runtime_contract import (
    AgentCandidateOption,
    AgentClarification,
    AgentReplyOnly,
    AgentSuggestedChange,
    ChatAgentRuntime,
    ReadTripCapability,
    RuntimeFailure,
    RuntimeFailureKind,
    RuntimeObservation,
    RuntimeReadTool,
    RuntimeRequest,
    RuntimeResult,
)
from .tools import build_read_only_trip_tools
from ..db.session import SessionLocal


_SAFE_PATCH_FIELDS = frozenset(
    {
        "title",
        "place",
        "start_hour",
        "day_date",
        "duration_min",
        "price_per_person",
        "lat",
        "lng",
    }
)


ToolInvocationRecorder = Callable[
    [str, Mapping[str, object], Literal["success", "failure"]], None
]


@dataclass(frozen=True, init=False)
class LegacyReadTripCapability:
    """Worker-owned Session and immutable scope for current Legacy tools."""

    _trip_id: str
    _actor_membership_id: str
    _session_factory: Callable[[], Session]
    _tool_invocation_recorder: ToolInvocationRecorder | None
    _legacy_tool_results: tuple[dict[str, Any], ...]

    def __init__(
        self,
        *,
        trip_id: str,
        actor_membership_id: str,
        session_factory: Callable[[], Session] = SessionLocal,
        tool_invocation_recorder: ToolInvocationRecorder | None = None,
    ) -> None:
        object.__setattr__(self, "_trip_id", trip_id)
        object.__setattr__(self, "_actor_membership_id", actor_membership_id)
        object.__setattr__(self, "_session_factory", session_factory)
        object.__setattr__(self, "_tool_invocation_recorder", tool_invocation_recorder)
        object.__setattr__(self, "_legacy_tool_results", ())

    @property
    def _application_legacy_tool_results(self) -> tuple[dict[str, Any], ...]:
        """Private evidence for legacy-only application revalidation.

        Replacement previews require the raw, guard-filtered
        ``find_replacement_place`` provenance.  That evidence is intentionally
        not part of the framework-neutral Runtime contract because the Pydantic
        adapter does not support replacement fields in PR-04B.
        """

        return self._legacy_tool_results

    def _record_legacy_tool_results(
        self, tool_results: tuple[dict[str, Any], ...]
    ) -> None:
        object.__setattr__(self, "_legacy_tool_results", tool_results)

    def run_with_read_only_tools(
        self,
        operation: Callable[[tuple[RuntimeReadTool, ...]], Any],
    ) -> Any:
        with self._session_factory() as worker_db:
            tools = build_read_only_trip_tools(
                worker_db,
                trip_id=self._trip_id,
                actor_membership_id=self._actor_membership_id,
            )
            if self._tool_invocation_recorder is not None:
                tools = tuple(
                    _record_tool_invocation(tool, self._tool_invocation_recorder)
                    for tool in tools
                )
            return operation(tuple(LegacyRuntimeReadTool(tool) for tool in tools))


def _record_tool_invocation(
    tool: base.AgentTool, recorder: ToolInvocationRecorder
) -> base.AgentTool:
    """Wrap one existing handler only when an eval/test recorder was supplied.

    The closure deliberately retains neither output nor exception details.  The
    recorder owns any sanitization before it persists an observation.  The
    default capability path does not call this helper, preserving production
    handler identity and behavior.
    """

    original_handler = tool.handler

    @wraps(original_handler)
    def observed_handler(**arguments: object) -> object:
        try:
            output = original_handler(**arguments)
        except Exception:
            recorder(tool.name, dict(arguments), "failure")
            raise
        recorder(tool.name, dict(arguments), "success")
        return output

    return replace(tool, handler=observed_handler)


@dataclass(frozen=True)
class LegacyRuntimeReadTool:
    """Neutral view of one Legacy tool; Legacy policy remains encapsulated."""

    _legacy_tool: base.AgentTool

    @property
    def name(self) -> str:
        return self._legacy_tool.name

    @property
    def description(self) -> str:
        return self._legacy_tool.description

    @property
    def parameters(self) -> dict[str, object]:
        return self._legacy_tool.parameters

    def invoke(self, **arguments: object) -> object:
        return self._legacy_tool.handler(**arguments)

    def _for_legacy_orchestration(self) -> base.AgentTool:
        """Keep guard/cache/handler policy private to this Legacy adapter."""

        return self._legacy_tool


@dataclass(frozen=True)
class LegacyChatAgentRuntime(ChatAgentRuntime):
    """Thin, independently testable mapping around the existing Agent loop."""

    system_prompt: str
    provider: str | None = base.AGENT_ROUTE

    def run(
        self,
        request: RuntimeRequest,
        capability: ReadTripCapability,
        *,
        execution: AgentExecutionConfig,
    ) -> RuntimeResult:
        def worker(deadline):
            def call_with_tools(read_tools: tuple[RuntimeReadTool, ...]) -> base.AgentRunResult:
                # The capability keeps the worker Session open around the
                # full loop, so conversion belongs here rather than in a
                # second tool-factory/session lifetime.
                deadline.ensure_request_active()
                legacy_tools = _legacy_tools(read_tools)
                deadline.ensure_request_active()
                return base.call_agent(
                    system=self.system_prompt,
                    user=request.message,
                    tools=legacy_tools,
                    history=tuple(
                        {"role": turn.role, "content": turn.text}
                        for turn in request.history
                    ),
                    max_rounds=request.limits.max_rounds,
                    max_total_tokens=request.limits.max_total_tokens,
                    max_tokens=request.limits.max_tokens,
                    provider=self.provider,
                    guard_reject_limit=request.limits.guard_reject_limit,
                    deadline=deadline,
                )

            # Preserve the Legacy deferred-tool ordering: reject an inactive
            # request before the capability opens its independent Session and
            # constructs the scoped read-only tools.
            deadline.ensure_request_active()
            return capability.run_with_read_only_tools(call_with_tools)

        try:
            legacy_result = run_agent_with_deadline(worker=worker, config=execution)
        except Exception as error:
            return _failure_result(error)
        if isinstance(capability, LegacyReadTripCapability):
            capability._record_legacy_tool_results(legacy_result.tool_results)
        return _map_legacy_result(legacy_result)


def _legacy_tools(read_tools: tuple[RuntimeReadTool, ...]) -> tuple[base.AgentTool, ...]:
    """Translate only this adapter's neutral descriptors back to Legacy policy."""

    legacy_tools: list[base.AgentTool] = []
    for tool in read_tools:
        if not isinstance(tool, LegacyRuntimeReadTool):
            raise TypeError("Legacy runtime requires LegacyReadTripCapability tools")
        legacy_tools.append(tool._for_legacy_orchestration())
    return tuple(legacy_tools)


def _map_legacy_result(result: base.AgentRunResult) -> RuntimeResult:
    if result.stopped_reason:
        return RuntimeResult(
            reply="",
            outcome=AgentReplyOnly(),
            candidate_options=(),
            observation=_observation(
                result,
                failure=RuntimeFailure(
                    normalized_kind=_normalized_stopped_reason(result.stopped_reason),
                    technical_kind=result.stopped_reason,
                ),
            ),
        )

    reply = result.content.strip()
    if not reply:
        return RuntimeResult(
            reply="",
            outcome=AgentReplyOnly(),
            candidate_options=(),
            observation=_observation(
                result,
                failure=RuntimeFailure(
                    normalized_kind="malformed_runtime_output",
                    technical_kind="empty_reply",
                ),
            ),
        )

    suggested = _suggested_change(result.tool_results)
    return RuntimeResult(
        reply=reply,
        outcome=suggested or AgentReplyOnly(),
        candidate_options=_candidate_options(result.tool_results),
        observation=_observation(result),
    )


def _observation(
    result: base.AgentRunResult,
    *,
    failure: RuntimeFailure | None = None,
) -> RuntimeObservation:
    return RuntimeObservation(
        trace_id=result.trace_id,
        round_count=len(result.rounds),
        total_tokens=result.total_tokens,
        total_elapsed_ms=result.total_elapsed_ms,
        failure=failure,
    )


def _suggested_change(
    tool_results: tuple[dict[str, Any], ...]
) -> AgentSuggestedChange | None:
    for result in reversed(tool_results):
        if result.get("tool") != "classify_change" or result.get("guard_rejected"):
            continue
        output = result.get("output")
        if not isinstance(output, dict):
            continue
        item = output.get("item")
        patch = output.get("proposed_patch")
        if not isinstance(item, dict) or not isinstance(patch, dict):
            continue
        item_ref = str(item.get("id") or "").strip()
        safe_patch = _safe_patch(patch)
        if item_ref and safe_patch:
            return AgentSuggestedChange(item_ref=item_ref, safe_patch=safe_patch)
    return None


def _candidate_options(
    tool_results: tuple[dict[str, Any], ...]
) -> tuple[AgentCandidateOption, ...]:
    options: list[AgentCandidateOption] = []
    for result in tool_results:
        if result.get("tool") != "propose_options" or result.get("guard_rejected"):
            continue
        output = result.get("output")
        if not isinstance(output, dict):
            continue
        for option in output.get("options") or ():
            if not isinstance(option, dict):
                continue
            raw_patch = option.get("patch")
            options.append(
                AgentCandidateOption(
                    id=str(option.get("id") or ""),
                    label=str(option.get("label") or ""),
                    title=str(option.get("title") or ""),
                    body=str(option.get("body") or ""),
                    tradeoff=str(option.get("tradeoff") or ""),
                    item_ref=str(option.get("item_id") or ""),
                    safe_patch=_safe_patch(raw_patch if isinstance(raw_patch, dict) else {}),
                )
            )
    return tuple(options)


def _safe_patch(raw_patch: dict[str, Any]) -> dict[str, object]:
    return {
        key: value
        for key, value in raw_patch.items()
        if key in _SAFE_PATCH_FIELDS and value not in (None, "")
    }


def _normalized_stopped_reason(stopped_reason: str) -> RuntimeFailureKind:
    if stopped_reason in {"token_limit_exceeded", "round_limit_exceeded"}:
        return "usage_limit"
    if stopped_reason == "guard_rejection_limit_exceeded":
        return "malformed_runtime_output"
    return "unexpected_exception"


def _failure_result(error: Exception) -> RuntimeResult:
    normalized_kind: RuntimeFailureKind = "unexpected_exception"
    technical_kind = error.__class__.__name__
    if isinstance(error, AgentProviderDeadlineExceeded):
        normalized_kind = "provider_timeout"
    elif isinstance(error, AgentToolDeadlineExceeded):
        normalized_kind = "tool_timeout"
    elif isinstance(error, AgentRequestDeadlineExceeded):
        normalized_kind = "request_timeout"
    elif isinstance(error, AgentExecutionCapacityExceeded):
        normalized_kind = "runtime_capacity"
    elif isinstance(error, AgentExecutionDeadlineExceeded):
        normalized_kind = "request_timeout"
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
                technical_kind=technical_kind,
            ),
        ),
    )
