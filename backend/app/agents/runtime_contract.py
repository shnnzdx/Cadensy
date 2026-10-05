"""Framework-neutral contract for a read-only Chat Agent Runtime.

This module deliberately contains no ORM, HTTP request, provider, or framework
types.  It is an additive seam: the existing Chat Service does not use it in
PR-04A.  Its purpose is to describe the semantic input/output boundary shared
by the Legacy runtime today and a future adapter after separate authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

    def __post_init__(self) -> None:
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
