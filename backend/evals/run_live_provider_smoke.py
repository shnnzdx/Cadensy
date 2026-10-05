"""Test-only, bounded PR-03R.2 DeepSeek smoke runner.

This module is not imported by a Cadensy route. It has no fallback credential
lookup and requires a process-scoped ``DEEPSEEK_API_KEY`` plus an explicit,
local disposable ``TEST_DATABASE_URL``. Its JSON result deliberately contains
only redacted protocol facts, counters, and symbolic test observations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.provider_smoke_harness import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_ID,
    MAX_PROVIDER_REQUESTS,
    MAX_REPORTED_TOTAL_TOKENS,
    PER_REQUEST_OUTPUT_CAP,
    PROVIDER_TIMEOUT_SECONDS,
    GlobalUsageLedger,
    LiveDeepSeekSmokeAdapter,
    SmokeObservation,
    classify_smoke_exception,
    normalize_smoke_failure,
)
from app.agents.pydantic_poc import TripReadCapability
from app.db.models import Base
from evals.runner import (
    PRIVATE_FIXTURE_PHRASE,
    _durable_counts,
    _independent_session_factory,
    _require_local_disposable_database,
    _synthetic_fixture,
)


# Official DeepSeek Flash per-million-token rates verified for this bounded
# run. Cache-hit pricing is excluded because provider usage may not identify
# cache-hit counters; this is a transparent cache-miss upper estimate.
FLASH_OFF_PEAK_INPUT_PER_MILLION_USD = 0.15
FLASH_OFF_PEAK_OUTPUT_PER_MILLION_USD = 0.60
FLASH_PEAK_INPUT_PER_MILLION_USD = 0.30
FLASH_PEAK_OUTPUT_PER_MILLION_USD = 1.20


def _observation_payload(observation: SmokeObservation) -> dict[str, object]:
    return {
        "output_kind": observation.output_kind,
        "suggested_action": observation.suggested_action,
        "failure_classification": observation.failure_classification,
    }


def _cost_estimates(usage: tuple[dict[str, int | None], ...]) -> dict[str, float] | None:
    if any(
        item["prompt_tokens"] is None or item["completion_tokens"] is None
        for item in usage
    ):
        return None
    prompt_tokens = sum(item["prompt_tokens"] or 0 for item in usage)
    completion_tokens = sum(item["completion_tokens"] or 0 for item in usage)
    return {
        "off_peak_usd": round(
            (
                prompt_tokens * FLASH_OFF_PEAK_INPUT_PER_MILLION_USD
                + completion_tokens * FLASH_OFF_PEAK_OUTPUT_PER_MILLION_USD
            )
            / 1_000_000,
            8,
        ),
        "peak_usd": round(
            (
                prompt_tokens * FLASH_PEAK_INPUT_PER_MILLION_USD
                + completion_tokens * FLASH_PEAK_OUTPUT_PER_MILLION_USD
            )
            / 1_000_000,
            8,
        ),
    }


async def run_bounded_live_smoke(
    db: Session,
    *,
    worker_session_factory: sessionmaker[Session],
    api_key: str,
) -> dict[str, Any]:
    """Execute at most R1=1, R2=2, R3=1 Provider requests, without retry."""
    durable_before = _durable_counts(db)
    ledger = GlobalUsageLedger(
        max_requests=MAX_PROVIDER_REQUESTS,
        max_total_tokens=MAX_REPORTED_TOTAL_TOKENS,
    )
    result: dict[str, Any] = {
        "provider_configuration": {
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL_ID,
            "max_requests": MAX_PROVIDER_REQUESTS,
            "max_reported_total_tokens": MAX_REPORTED_TOTAL_TOKENS,
            "max_tokens_per_request": PER_REQUEST_OUTPUT_CAP,
            "provider_timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
            "retries": 0,
            "streaming": False,
        },
        "cases": {},
        "failure": None,
    }
    with _synthetic_fixture(db) as fixture:
        item_id = fixture["items"]["art"].id
        membership_id = fixture["memberships"]["organizer"].id
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=membership_id,
            session_factory=worker_session_factory,
        )
        adapter = LiveDeepSeekSmokeAdapter(
            api_key=api_key,
            ledger=ledger,
            # These values exist only while this process runs. The transport
            # rejects them before network dispatch and never persists them.
            forbidden_payload_values=(
                PRIVATE_FIXTURE_PHRASE,
                membership_id,
                item_id,
            ),
        )
        harness = None
        try:
            async with adapter.create_harness() as harness:
                result["cases"]["r1"] = _observation_payload(
                    await harness.run_r1_typed_clarification()
                )
                result["cases"]["r2"] = _observation_payload(
                    await harness.run_r2_scoped_change_preview(
                        capability,
                        item_id=item_id,
                    )
                )
                result["cases"]["r3"] = _observation_payload(
                    await harness.run_r3_required_tool_choice()
                )
                result["r3_tool_invocations"] = list(harness.scenario_tool_invocations)
                result["redacted_wire_contracts"] = list(harness.captured_requests)
                result["unfinished_late_tasks_at_close"] = (
                    harness.unfinished_late_tasks_at_close
                )
        except BaseException as exc:
            result["failure"] = {
                "technical": classify_smoke_exception(exc),
                "normalized": normalize_smoke_failure(exc).normalized_evaluation_failure,
            }
        finally:
            # A usage/HTTP terminal response can be rejected before Agent
            # output handling. Preserve already-captured *redacted* protocol
            # facts for the report without retaining raw payloads or headers.
            if harness is not None:
                result["r3_tool_invocations"] = list(
                    harness.scenario_tool_invocations
                )
                result["redacted_wire_contracts"] = list(
                    harness.captured_requests
                )
                result["unfinished_late_tasks_at_close"] = (
                    harness.unfinished_late_tasks_at_close
                )

    result["provider_reported_usage"] = list(ledger.reported_usage)
    result["reported_total_tokens"] = ledger.reported_total_tokens
    result["actual_http_requests"] = ledger.requests_used
    result["ledger_stop_reason"] = ledger.stop_reason
    result["cost_estimates_usd"] = _cost_estimates(ledger.reported_usage)
    result["fixture_cleanup_preserved_durable_counts"] = _durable_counts(db) == durable_before
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bounded PR-03R.2 smoke")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Explicit path for the redacted JSON execution result",
    )
    args = parser.parse_args()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY must be present in this process environment")
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit("TEST_DATABASE_URL is required")
    _require_local_disposable_database(database_url)

    engine = create_engine(database_url, future=True, connect_args={"client_encoding": "utf8"})
    try:
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, future=True)
        with Session(engine, future=True) as db:
            result = asyncio.run(
                run_bounded_live_smoke(
                    db,
                    worker_session_factory=session_factory,
                    api_key=api_key,
                )
            )
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "actual_http_requests": result["actual_http_requests"],
                "failure": result["failure"],
                "ledger_stop_reason": result["ledger_stop_reason"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
