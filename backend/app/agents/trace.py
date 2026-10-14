"""Legacy trace compatibility helpers with no persistent trace output.

The old implementation wrote agent rounds, tool arguments/results, and failure
details to stdout and local JSONL files. PR-06A.1 retains only the small
in-memory round summary consumed by the legacy agent result. Typed telemetry
is defined separately in :mod:`app.observability.telemetry` and is not wired
to execution in this slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


# Kept temporarily for test/third-party monkeypatch compatibility. No trace
# function in this module reads or writes this directory anymore.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = BACKEND_ROOT / "logs"

_APPROVED_TOOL_NAMES = frozenset(
    {
        "get_current_plan",
        "get_trip_facts",
        "classify_change",
        "find_replacement_place",
        "propose_options",
    }
)
_APPROVED_DISPATCH_STATUSES = frozenset(
    {
        "unknown_tool",
        "guard_rejected",
        "cache_hit",
        "handler_succeeded",
        "handler_failed",
        "dispatch_failed",
        "tool_deadline_exceeded",
    }
)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


def new_trace_id() -> str:
    return uuid4().hex


def extract_token_usage(completion: Any) -> TokenUsage:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return TokenUsage()
    if isinstance(usage, dict):
        return TokenUsage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def record_model_call(
    *,
    trace_id: str,
    route: str,
    provider: str,
    model: str | None,
    provider_elapsed_ms: float | None,
    total_elapsed_ms: float,
    tokens: TokenUsage | None,
    ok: bool,
    fallback_to: str | None = None,
    failures: list[dict[str, str]] | None = None,
) -> None:
    """Compatibility no-op for an unused legacy output path.

    Keep the callable signature while intentionally refusing to serialize
    provider failures or arbitrary data supplied to it.
    """
    del (
        trace_id,
        route,
        provider,
        model,
        provider_elapsed_ms,
        total_elapsed_ms,
        tokens,
        ok,
        fallback_to,
        failures,
    )


def record_agent_round(
    *,
    trace_id: str,
    route: str,
    provider: str,
    model: str | None,
    round_index: int,
    requested_tool: bool,
    tool_name: str | None,
    tool_arguments: dict[str, Any] | None,
    tool_result: Any,
    elapsed_ms: float,
    tokens: TokenUsage | None,
    dispatch_statuses: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build the safe in-memory legacy round summary without I/O.

    The broad parameters are retained so existing callers need no behavioral
    migration. Only the fixed fields below can reach ``AgentRunResult.rounds``.
    """
    del trace_id, route, provider, model, tool_arguments, elapsed_ms, tokens
    guard_rejected, guard_limit_exceeded = _agent_round_guard_flags(tool_result)
    safe_name = _safe_tool_name(tool_name)
    return {
        "round_index": round_index,
        "requested_tool": requested_tool,
        "tool_name": safe_name,
        "guard_rejected": guard_rejected,
        "guard_limit_exceeded": guard_limit_exceeded,
        "tool_dispatch_status": _summary_dispatch_status(
            requested_tool=requested_tool,
            statuses=dispatch_statuses or (),
        ),
    }


def record_agent_lifecycle(
    *,
    phase: str,
    execution_id: str,
    elapsed_ms: float,
    result_consumed: bool,
    worker_cancel_requested: bool,
) -> None:
    """Compatibility no-op preserving lifecycle ordering and worker behavior."""
    del phase, execution_id, elapsed_ms, result_consumed, worker_cancel_requested


def _safe_tool_name(value: str | None) -> str | None:
    """Never copy model-supplied/combined tool labels into a safe summary."""
    return value if value in _APPROVED_TOOL_NAMES else None


def _agent_round_guard_flags(tool_result: Any) -> tuple[bool, bool]:
    calls = tool_result.get("calls", []) if isinstance(tool_result, dict) else []
    guard_rejected = False
    guard_limit_exceeded = False
    for call in calls:
        if not isinstance(call, dict):
            continue
        if bool(call.get("guard_rejected")):
            guard_rejected = True
        guard_limit_exceeded = guard_limit_exceeded or bool(
            call.get("guard_limit_exceeded")
        )
    return guard_rejected, guard_limit_exceeded


def _summary_dispatch_status(
    *, requested_tool: bool, statuses: tuple[str, ...]
) -> str | None:
    if not requested_tool:
        return None
    safe_statuses = {status for status in statuses if status in _APPROVED_DISPATCH_STATUSES}
    if len(safe_statuses) == 1:
        return next(iter(safe_statuses))
    if len(safe_statuses) > 1:
        return "multiple_dispatches"
    return "dispatch_unavailable"
