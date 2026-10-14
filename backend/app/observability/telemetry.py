"""Typed, privacy-safe telemetry primitives.

This module is deliberately only a contract and a safe emitter.  PR-06A.1
does not wire request, runtime, model, tool, or lifecycle execution paths to
it; later work must propagate ``TelemetryContext`` explicitly.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, ClassVar
from uuid import uuid4


SCHEMA_VERSION = "cadensy.telemetry.v1"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_MAX_ELAPSED_MS = 86_400_000
_MAX_TOKEN_COUNT = 10_000_000


class EventName(str, Enum):
    CHAT_REQUEST_STARTED = "chat.request_started"
    CHAT_REQUEST_COMPLETED = "chat.request_completed"
    AGENT_RUNTIME_SELECTED = "agent.runtime_selected"
    AGENT_MODEL_CALL_COMPLETED = "agent.model_call_completed"
    AGENT_TOOL_CALL_COMPLETED = "agent.tool_call_completed"
    AGENT_DEADLINE_EXCEEDED = "agent.deadline_exceeded"
    AGENT_CAPACITY_REJECTED = "agent.capacity_rejected"
    AGENT_LATE_COMPLETION = "agent.late_completion"
    AGENT_FALLBACK = "agent.fallback"
    DOMAIN_CLASSIFICATION_COMPLETED = "domain.classification_completed"


class RouteCategory(str, Enum):
    TRIP_CHAT = "trip_chat"


class RuntimeName(str, Enum):
    LEGACY = "legacy"
    PYDANTIC = "pydantic"


class InvocationSource(str, Enum):
    PROVIDER_SDK = "provider_sdk"
    MOCK = "mock"
    FAKE = "fake"


class ProviderName(str, Enum):
    DEEPSEEK = "deepseek"
    UNAPPROVED_PROVIDER = "unapproved_provider"


class ModelId(str, Enum):
    DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"
    UNAPPROVED_MODEL = "unapproved_model"


class InvocationOutcome(str, Enum):
    RETURNED = "returned"
    FAILED = "failed"


class UsageStatus(str, Enum):
    REPORTED = "reported"
    NOT_REPORTED = "not_reported"


class ApprovedToolName(str, Enum):
    GET_CURRENT_PLAN = "get_current_plan"
    GET_TRIP_FACTS = "get_trip_facts"
    CLASSIFY_CHANGE = "classify_change"
    FIND_REPLACEMENT_PLACE = "find_replacement_place"
    PROPOSE_OPTIONS = "propose_options"


class ToolDispatchStatus(str, Enum):
    UNKNOWN_TOOL = "unknown_tool"
    GUARD_REJECTED = "guard_rejected"
    DISPATCH_FAILED = "dispatch_failed"
    CACHE_HIT = "cache_hit"
    HANDLER_SUCCEEDED = "handler_succeeded"
    HANDLER_FAILED = "handler_failed"
    TOOL_DEADLINE_EXCEEDED = "tool_deadline_exceeded"


class DeadlineScope(str, Enum):
    REQUEST = "request"
    PROVIDER = "provider"
    TOOL = "tool"


class LateCompletionKind(str, Enum):
    QUEUED_CANCELLED = "queued_cancelled"
    RUNNING_LATE_COMPLETION = "running_late_completion"


class FallbackSurface(str, Enum):
    SERVICE = "service"
    RUNTIME = "runtime"


class FallbackReason(str, Enum):
    RUNTIME_FAILURE = "runtime_failure"
    REQUEST_TIMEOUT = "request_timeout"
    PROVIDER_FAILURE = "provider_failure"
    TOOL_FAILURE = "tool_failure"


class ClassificationOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TerminalCategory(str, Enum):
    SUCCEEDED = "succeeded"
    DETERMINISTIC_CLARIFICATION = "deterministic_clarification"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    DEGRADED_FALLBACK = "degraded_fallback"
    RUNTIME_FAILURE = "runtime_failure"
    UNHANDLED_ERROR = "unhandled_error"


class FailureCategory(str, Enum):
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAUTHORIZED = "provider_unauthorized"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_SERVER_ERROR = "provider_server_error"
    PROVIDER_CLIENT_ERROR = "provider_client_error"
    PROVIDER_NETWORK_ERROR = "provider_network_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TOOL_TIMEOUT = "tool_timeout"
    REQUEST_TIMEOUT = "request_timeout"
    RUNTIME_CAPACITY = "runtime_capacity"
    USAGE_LIMIT = "usage_limit"
    MALFORMED_RUNTIME_OUTPUT = "malformed_runtime_output"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    DOMAIN_ERROR = "domain_error"
    UNEXPECTED_RUNTIME_ERROR = "unexpected_runtime_error"


def _new_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_opaque_id(value: str | None, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or not _OPAQUE_ID.fullmatch(value)):
        raise ValueError(f"{field_name} must be an opaque identifier")


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp_utc must be an RFC3339 UTC timestamp")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp_utc must be an RFC3339 UTC timestamp") from exc


def _validate_non_negative(value: int | float, field_name: str, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or value > maximum
    ):
        raise ValueError(f"{field_name} must be a non-negative bounded number")


def _require_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{field_name} must be a closed {enum_type.__name__} value")


def _require_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")


@dataclass(frozen=True, kw_only=True)
class TelemetryContext:
    """Opaque request correlation only; it intentionally has no domain metadata."""

    request_id: str
    trace_id: str | None = None
    execution_id: str | None = None

    def __post_init__(self) -> None:
        _validate_opaque_id(self.request_id, "request_id")
        _validate_opaque_id(self.trace_id, "trace_id")
        _validate_opaque_id(self.execution_id, "execution_id")

    @classmethod
    def new(cls) -> "TelemetryContext":
        return cls(request_id=uuid4().hex)


@dataclass(frozen=True, kw_only=True)
class TelemetryEvent:
    """Base class that owns the fixed, flattened event envelope."""

    context: TelemetryContext
    timestamp_utc: str = field(default_factory=_new_timestamp)
    event_name: ClassVar[EventName]

    def __post_init__(self) -> None:
        _validate_timestamp(self.timestamp_utc)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "event_name": self.event_name.value,
            "timestamp_utc": self.timestamp_utc,
            "request_id": self.context.request_id,
            "trace_id": self.context.trace_id,
            "execution_id": self.context.execution_id,
            "route_category": RouteCategory.TRIP_CHAT.value,
        }
        for field in fields(self):
            if field.name in {"context", "timestamp_utc"}:
                continue
            value = getattr(self, field.name)
            payload[field.name] = value.value if isinstance(value, Enum) else value
        return payload


@dataclass(frozen=True, kw_only=True)
class ChatRequestStarted(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.CHAT_REQUEST_STARTED
    request_path: RouteCategory = RouteCategory.TRIP_CHAT

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.request_path, RouteCategory, "request_path")


@dataclass(frozen=True, kw_only=True)
class RuntimeSelected(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.AGENT_RUNTIME_SELECTED
    runtime_name: RuntimeName

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.runtime_name, RuntimeName, "runtime_name")


@dataclass(frozen=True, kw_only=True)
class ModelCallCompleted(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.AGENT_MODEL_CALL_COMPLETED
    model_invocation_index: int
    invocation_source: InvocationSource
    provider: ProviderName
    model_id: ModelId
    invocation_outcome: InvocationOutcome
    failure_category: FailureCategory | None
    elapsed_ms: float
    usage_status: UsageStatus
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            isinstance(self.model_invocation_index, bool)
            or not isinstance(self.model_invocation_index, int)
            or self.model_invocation_index <= 0
        ):
            raise ValueError("model_invocation_index must be positive")
        _require_enum(self.invocation_source, InvocationSource, "invocation_source")
        _require_enum(self.provider, ProviderName, "provider")
        _require_enum(self.model_id, ModelId, "model_id")
        _require_enum(self.invocation_outcome, InvocationOutcome, "invocation_outcome")
        _require_enum(self.usage_status, UsageStatus, "usage_status")
        if self.failure_category is not None:
            _require_enum(self.failure_category, FailureCategory, "failure_category")
        _validate_non_negative(self.elapsed_ms, "elapsed_ms", _MAX_ELAPSED_MS)
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None:
                _validate_non_negative(value, name, _MAX_TOKEN_COUNT)
        if self.invocation_outcome is InvocationOutcome.RETURNED and self.failure_category is not None:
            raise ValueError("returned invocation cannot have a failure category")
        if self.invocation_outcome is InvocationOutcome.FAILED and self.failure_category is None:
            raise ValueError("failed invocation requires a failure category")
        if self.usage_status is UsageStatus.NOT_REPORTED and any(
            value is not None
            for value in (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        ):
            raise ValueError("unreported usage must use null token counts")
        if self.usage_status is UsageStatus.REPORTED and all(
            value is None
            for value in (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        ):
            raise ValueError("reported usage requires at least one token count")


@dataclass(frozen=True, kw_only=True)
class ToolCallCompleted(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.AGENT_TOOL_CALL_COMPLETED
    tool_name: ApprovedToolName | None
    tool_dispatch_status: ToolDispatchStatus
    handler_executed: bool
    cache_hit: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.tool_dispatch_status, ToolDispatchStatus, "tool_dispatch_status")
        _require_bool(self.handler_executed, "handler_executed")
        _require_bool(self.cache_hit, "cache_hit")
        if self.tool_name is not None and not isinstance(self.tool_name, ApprovedToolName):
            raise ValueError("tool_name must be an approved tool name or null")
        if self.tool_dispatch_status is ToolDispatchStatus.UNKNOWN_TOOL:
            if self.tool_name is not None or self.handler_executed or self.cache_hit:
                raise ValueError("unknown tools must not serialize a tool name or execution")
        elif self.tool_dispatch_status is ToolDispatchStatus.CACHE_HIT:
            if self.tool_name is None or self.handler_executed or not self.cache_hit:
                raise ValueError("cache hits require a known tool and no handler execution")
        elif self.tool_dispatch_status in {
            ToolDispatchStatus.GUARD_REJECTED,
            ToolDispatchStatus.DISPATCH_FAILED,
        }:
            if self.tool_name is None or self.handler_executed or self.cache_hit:
                raise ValueError("pre-handler dispatch outcomes cannot execute or cache")
        elif self.tool_dispatch_status in {
            ToolDispatchStatus.HANDLER_SUCCEEDED,
            ToolDispatchStatus.HANDLER_FAILED,
        }:
            if self.tool_name is None or not self.handler_executed or self.cache_hit:
                raise ValueError("handler outcomes require an executed known handler")
        elif self.tool_dispatch_status is ToolDispatchStatus.TOOL_DEADLINE_EXCEEDED:
            if self.tool_name is None or self.cache_hit:
                raise ValueError("tool deadline outcome requires a known non-cached tool")


@dataclass(frozen=True, kw_only=True)
class AgentDeadlineExceeded(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.AGENT_DEADLINE_EXCEEDED
    deadline_scope: DeadlineScope
    failure_category: FailureCategory
    elapsed_ms: float
    result_consumed: bool
    cancellation_requested: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.deadline_scope, DeadlineScope, "deadline_scope")
        _require_enum(self.failure_category, FailureCategory, "failure_category")
        _require_bool(self.result_consumed, "result_consumed")
        _require_bool(self.cancellation_requested, "cancellation_requested")
        _validate_non_negative(self.elapsed_ms, "elapsed_ms", _MAX_ELAPSED_MS)
        if self.failure_category is FailureCategory.RUNTIME_CAPACITY:
            raise ValueError("capacity rejection is not a deadline event")


@dataclass(frozen=True, kw_only=True)
class AgentCapacityRejected(TelemetryEvent):
    """Admission rejection is intentionally distinct from a deadline expiry."""

    event_name: ClassVar[EventName] = EventName.AGENT_CAPACITY_REJECTED
    elapsed_ms: float

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_non_negative(self.elapsed_ms, "elapsed_ms", _MAX_ELAPSED_MS)


@dataclass(frozen=True, kw_only=True)
class AgentLateCompletion(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.AGENT_LATE_COMPLETION
    late_completion_kind: LateCompletionKind
    elapsed_ms: float
    result_consumed: bool
    cancellation_requested: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.late_completion_kind, LateCompletionKind, "late_completion_kind")
        _require_bool(self.result_consumed, "result_consumed")
        _require_bool(self.cancellation_requested, "cancellation_requested")
        _validate_non_negative(self.elapsed_ms, "elapsed_ms", _MAX_ELAPSED_MS)
        if self.result_consumed:
            raise ValueError("late completion results cannot be consumed")


@dataclass(frozen=True, kw_only=True)
class AgentFallback(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.AGENT_FALLBACK
    fallback_reason: FallbackReason
    fallback_surface: FallbackSurface

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.fallback_reason, FallbackReason, "fallback_reason")
        _require_enum(self.fallback_surface, FallbackSurface, "fallback_surface")


@dataclass(frozen=True, kw_only=True)
class DomainClassificationCompleted(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.DOMAIN_CLASSIFICATION_COMPLETED
    classification_outcome: ClassificationOutcome
    failure_category: FailureCategory | None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(
            self.classification_outcome,
            ClassificationOutcome,
            "classification_outcome",
        )
        if self.failure_category is not None:
            _require_enum(self.failure_category, FailureCategory, "failure_category")
        if self.classification_outcome is ClassificationOutcome.SUCCEEDED and self.failure_category is not None:
            raise ValueError("successful classification cannot have a failure category")
        if self.classification_outcome is ClassificationOutcome.FAILED and self.failure_category is None:
            raise ValueError("failed classification requires a failure category")


@dataclass(frozen=True, kw_only=True)
class ChatRequestCompleted(TelemetryEvent):
    event_name: ClassVar[EventName] = EventName.CHAT_REQUEST_COMPLETED
    terminal_category: TerminalCategory

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_enum(self.terminal_category, TerminalCategory, "terminal_category")


def _default_sink() -> Callable[[str], None]:
    logger = logging.getLogger("cadensy.safe_telemetry")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger.info


class SafeTelemetryEmitter:
    """One-line JSON emission that is incapable of changing business flow."""

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self._sink = sink or _default_sink()

    def emit(self, event: TelemetryEvent) -> bool:
        if not isinstance(event, TelemetryEvent):
            raise TypeError("SafeTelemetryEmitter accepts only typed TelemetryEvent values")
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
            self._sink(line)
        except Exception:
            return False
        return True
