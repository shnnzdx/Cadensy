"""Framework-neutral contract for a read-only Chat Agent Runtime.

This module deliberately contains no ORM, HTTP request, provider, or framework
types.  It is an additive seam: the existing Chat Service does not use it in
PR-04A.  Its purpose is to describe the semantic input/output boundary shared
by the Legacy runtime today and a future adapter after separate authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from typing import Literal, Protocol, TypeVar

from .execution import AgentExecutionConfig


@dataclass(frozen=True)
class RuntimeHistoryTurn:
    """Bounded user-visible history supplied by the Application Service."""

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class RuntimeLimits:
    """Runtime-loop limits, distinct from the shared request deadline config."""

    max_rounds: int
    max_total_tokens: int | None = None
    max_tokens: int | None = None
    guard_reject_limit: int = 2

    def __post_init__(self) -> None:
        if self.max_rounds <= 0:
            raise ValueError("max_rounds must be greater than zero")
        if self.max_total_tokens is not None and self.max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be greater than zero")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")
        if self.guard_reject_limit < 0:
            raise ValueError("guard_reject_limit must not be negative")


@dataclass(frozen=True)
class RuntimeRequest:
    """Semantic request only; authorization belongs exclusively to capability."""

    message: str
    history: tuple[RuntimeHistoryTurn, ...]
    selected_item_ref: str | None
    request_id: str
    limits: RuntimeLimits

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("message must not be empty")
        if not self.request_id.strip():
            raise ValueError("request_id must not be empty")


class RuntimeReadTool(Protocol):
    """Framework-neutral descriptor for one capability-authorized read tool.

    Guard/cache/orchestration policy remains adapter-specific.  A Runtime may
    inspect the public descriptor and invoke only this scoped read operation;
    it never receives a Session or a generic query/write API.
    """

    name: str
    description: str
    parameters: Mapping[str, object]

    def invoke(self, **arguments: object) -> object: ...


T = TypeVar("T")


class ReadTripCapability(Protocol):
    """The sole runtime-side authority for scoped read/classify access.

    Implementations hold trip/actor scope and open their own short-lived
    Session.  They expose neither an ORM Session nor a query/write API.
    """

    def run_with_read_only_tools(
        self, operation: Callable[[tuple[RuntimeReadTool, ...]], T]
    ) -> T: ...


@dataclass(frozen=True)
class AgentClarification:
    """The explicit reply asks for the missing information needed to continue."""


@dataclass(frozen=True)
class AgentSuggestedChange:
    """Allowed-field suggestion syntax only, never Domain-authorized patch data.

    ``safe_patch`` filters known field names.  It is not fully value-validated,
    fresh-state/revision validated, replacement-authorized, transactional, or a
    Domain verdict/apply authority.
    """

    item_ref: str
    safe_patch: dict[str, object]


@dataclass(frozen=True)
class AgentReplyOnly:
    """The explicit reply is informative and contains no suggested patch."""


RuntimeOutcome = AgentClarification | AgentSuggestedChange | AgentReplyOnly


@dataclass(frozen=True)
class AgentCandidateOption:
    """A filtered, non-authoritative candidate option returned with an outcome."""

    id: str
    label: str
    title: str
    body: str
    tradeoff: str
    item_ref: str
    safe_patch: dict[str, object]


ToolExecutionStatus = Literal[
    "succeeded",
    "guard_rejected",
    "failed",
    "timed_out",
    "cancelled",
    "cache_hit",
    "unsupported",
]


@dataclass(frozen=True)
class ReplacementCandidateEvidence:
    """Detached, validated facts from one scoped replacement-search result."""

    candidate_id: str
    title: str
    place: str
    lat: float
    lng: float
    price_per_person: float | None

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value.strip()
            for value in (self.candidate_id, self.title, self.place)
        ):
            raise ValueError("replacement evidence requires non-empty identity and location text")
        for value, lower, upper, field in (
            (self.lat, -90.0, 90.0, "lat"),
            (self.lng, -180.0, 180.0, "lng"),
        ):
            if (
                isinstance(value, bool)
                or type(value) not in {int, float}
                or not math.isfinite(float(value))
                or not lower <= float(value) <= upper
            ):
                raise ValueError(f"replacement evidence {field} is invalid")
        if self.price_per_person is not None and (
            isinstance(self.price_per_person, bool)
            or type(self.price_per_person) not in {int, float}
            or not math.isfinite(float(self.price_per_person))
            or float(self.price_per_person) < 0
        ):
            raise ValueError("replacement evidence price_per_person is invalid")


@dataclass(frozen=True)
class ToolExecutionEvidence:
    """Per-run evidence of a completed dispatch or a fixed execution marker.

    Runtime adapters expose only validated, detached facts. ``agent_execution``
    is the only non-tool name and records a safe failed/timed-out/cancelled
    execution when no completed tool result may be consumed.
    """

    tool_name: str
    status: ToolExecutionStatus
    replacement_candidates: tuple[ReplacementCandidateEvidence, ...] = ()

    def __post_init__(self) -> None:
        if type(self.tool_name) is not str or not self.tool_name.strip():
            raise ValueError("tool evidence requires a tool name")
        if type(self.status) is not str or self.status not in {
            "succeeded", "guard_rejected", "failed", "timed_out",
            "cancelled", "cache_hit", "unsupported",
        }:
            raise ValueError("tool evidence has an unsupported status")
        if not isinstance(self.replacement_candidates, (tuple, list)):
            raise ValueError("replacement evidence candidates must be a tuple or list")
        candidates = tuple(self.replacement_candidates)
        if not all(type(candidate) is ReplacementCandidateEvidence for candidate in candidates):
            raise ValueError("replacement evidence candidates must be validated DTOs")
        object.__setattr__(self, "replacement_candidates", candidates)
        if candidates:
            if self.tool_name != "find_replacement_place":
                raise ValueError("replacement candidates require find_replacement_place evidence")
            if self.status not in {"succeeded", "cache_hit"}:
                raise ValueError("replacement candidates require a successful execution")


RuntimeFailureKind = Literal[
    "provider_timeout",
    "tool_timeout",
    "request_timeout",
    "usage_limit",
    "runtime_capacity",
    "malformed_runtime_output",
    "unexpected_exception",
]


@dataclass(frozen=True)
class RuntimeFailure:
    """Redacted failure classification; provider bodies and raw errors stay out."""

    normalized_kind: RuntimeFailureKind
    technical_kind: str


@dataclass(frozen=True)
class RuntimeObservation:
    """Safe telemetry separate from reply, suggestion, and candidate semantics."""

    trace_id: str | None
    round_count: int | None
    total_tokens: int | None
    total_elapsed_ms: float | None
    failure: RuntimeFailure | None = None
    safe_detail: str = ""


@dataclass(frozen=True)
class RuntimeResult:
    """Common semantic result.

    A normal successful result must expose a user-visible reply.  Failure
    observations may use an empty reply so the Application Service can retain
    its existing deterministic safe-degradation behavior.
    """

    reply: str
    outcome: RuntimeOutcome
    candidate_options: tuple[AgentCandidateOption, ...]
    observation: RuntimeObservation
    tool_execution_evidence: tuple[ToolExecutionEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tool_execution_evidence, (tuple, list)):
            raise ValueError("tool execution evidence must be a tuple or list")
        evidence = tuple(self.tool_execution_evidence)
        if not all(type(entry) is ToolExecutionEvidence for entry in evidence):
            raise ValueError("tool execution evidence must contain validated DTOs")
        object.__setattr__(self, "tool_execution_evidence", evidence)
        if self.observation.failure is None and not self.reply.strip():
            raise ValueError("successful RuntimeResult must have a user-visible reply")


class ChatAgentRuntime(Protocol):
    """Framework-neutral Runtime seam; composition/route wiring is out of scope."""

    def run(
        self,
        request: RuntimeRequest,
        capability: ReadTripCapability,
        *,
        execution: AgentExecutionConfig,
    ) -> RuntimeResult: ...
