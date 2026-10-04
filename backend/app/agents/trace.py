"""Structured tracing for real AI provider calls.

This module deliberately records only routing and runtime metadata. Prompts,
model responses, member identifiers, and raw preference wording do not belong
in trace logs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = BACKEND_ROOT / "logs"


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
    """Record one model-call outcome.

    `provider_elapsed_ms` is the time spent in the provider that succeeded.
    It is null on failed events because no provider produced an answer.
    `total_elapsed_ms` is the full call_model wall time, including any earlier
    failed providers in the fallback chain.
    """
    timestamp = datetime.now(timezone.utc)
    event = {
        "timestamp": timestamp.isoformat(),
        "trace_id": trace_id,
        "route": route,
        "provider": provider,
        "model": model,
        "provider_elapsed_ms": (
            round(provider_elapsed_ms, 2) if provider_elapsed_ms is not None else None
        ),
        "total_elapsed_ms": round(total_elapsed_ms, 2),
        "tokens": (tokens or TokenUsage()).as_dict(),
        "ok": ok,
        "fallback_to": fallback_to,
        "failures": failures or [],
    }

    status = "ok" if ok else "failed"
    token_count = event["tokens"]["total_tokens"]
    token_text = "tokens=unknown" if token_count is None else f"tokens={token_count}"
    fallback_text = f" fallback_to={fallback_to}" if fallback_to else ""
    print(
        "[ai-trace] "
        f"{status} trace_id={trace_id} route={route} provider={provider} "
        f"model={model or 'unset'} provider_elapsed_ms={event['provider_elapsed_ms']} "
        f"total_elapsed_ms={event['total_elapsed_ms']} "
        f"{token_text}{fallback_text}"
    )
    if not ok:
        for failure in event["failures"]:
            print(
                "[ai-trace] "
                f"failure trace_id={trace_id} provider={failure['provider']} "
                f"reason={failure['reason']}"
            )

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"trace-{timestamp:%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:
        print(f"[ai-trace] log_write_failed trace_id={trace_id} reason={exc!r}")


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
) -> dict[str, Any]:
    """Record one experimental tool-agent round.

    Tool arguments and results are allowed here only for the agent-server demo:
    they must stay limited to itinerary titles, times, and conflict summaries,
    never member identities, membership ids, or raw private preference wording.
    """
    timestamp = datetime.now(timezone.utc)
    guard_info = _agent_round_guard_info(tool_result)
    event = {
        "timestamp": timestamp.isoformat(),
        "trace_id": trace_id,
        "route": route,
        "provider": provider,
        "model": model,
        "round_index": round_index,
        "requested_tool": requested_tool,
        "tool_name": tool_name,
        "tool_arguments": tool_arguments or {},
        "tool_result": tool_result,
        "elapsed_ms": round(elapsed_ms, 2),
        "tokens": (tokens or TokenUsage()).as_dict(),
        "guard_rejected": guard_info["guard_rejected"],
        "guard_reasons": guard_info["guard_reasons"],
        "guard_limit_exceeded": guard_info["guard_limit_exceeded"],
    }

    tool_text = tool_name or "none"
    token_count = event["tokens"]["total_tokens"]
    token_text = "tokens=unknown" if token_count is None else f"tokens={token_count}"
    print(
        "[agent-trace] "
        f"trace_id={trace_id} round={round_index} route={route} "
        f"provider={provider} model={model or 'unset'} requested_tool={requested_tool} "
        f"tool={tool_text} elapsed_ms={event['elapsed_ms']} {token_text} "
        f"guard_rejected={event['guard_rejected']}"
    )

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"agent-trace-{timestamp:%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:
        print(f"[agent-trace] log_write_failed trace_id={trace_id} reason={exc!r}")

    return event


def record_agent_lifecycle(
    *,
    phase: str,
    execution_id: str,
    elapsed_ms: float,
    result_consumed: bool,
    worker_cancel_requested: bool,
) -> None:
    """Record deadline lifecycle metadata without prompts, tool data, or output.

    A late completion proves only that a local worker reached a terminal state
    after the HTTP/request response had already been abandoned. The result is
    intentionally not serialized or inspected here.
    """
    timestamp = datetime.now(timezone.utc)
    event = {
        "timestamp": timestamp.isoformat(),
        "phase": phase,
        "execution_id": execution_id,
        "elapsed_ms": round(elapsed_ms, 2),
        "result_consumed": result_consumed,
        "worker_cancel_requested": worker_cancel_requested,
    }
    print(
        "[agent-lifecycle] "
        f"phase={phase} execution_id={execution_id} elapsed_ms={event['elapsed_ms']} "
        f"result_consumed={result_consumed} worker_cancel_requested={worker_cancel_requested}"
    )
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"agent-lifecycle-{timestamp:%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:
        print(f"[agent-lifecycle] log_write_failed execution_id={execution_id} reason={exc!r}")


def _agent_round_guard_info(tool_result: Any) -> dict[str, Any]:
    calls = tool_result.get("calls", []) if isinstance(tool_result, dict) else []
    reasons = []
    limit_exceeded = False
    for call in calls:
        if not isinstance(call, dict):
            continue
        if call.get("guard_rejected"):
            reason = call.get("guard_reason")
            if reason:
                reasons.append(reason)
        limit_exceeded = limit_exceeded or bool(call.get("guard_limit_exceeded"))
    return {
        "guard_rejected": bool(reasons),
        "guard_reasons": reasons,
        "guard_limit_exceeded": limit_exceeded,
    }
