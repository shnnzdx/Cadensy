"""One-request, R3-only PR-03R.3 DeepSeek compatibility runner.

This test-only entry point does not import any HTTP route, does not run R1 or
R2, and has no credential fallback.  Its fresh ledger is deliberately a new,
strict incremental authorization: one outbound request and at most 500
provider-reported total tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.provider_smoke_harness import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_ID,
    PER_REQUEST_OUTPUT_CAP,
    PROVIDER_TIMEOUT_SECONDS,
    GlobalUsageLedger,
    LiveDeepSeekSmokeAdapter,
    classify_smoke_exception,
    normalize_smoke_failure,
)
from app.db.models import Base
from evals.run_live_provider_smoke import _cost_estimates, _observation_payload
from evals.runner import _durable_counts, _require_local_disposable_database


R3_INCREMENTAL_MAX_REQUESTS = 1
R3_INCREMENTAL_MAX_REPORTED_TOTAL_TOKENS = 500


async def run_r3_only_live_smoke(db: Session, *, api_key: str) -> dict[str, Any]:
    """Verify only R3's first required-tool turn through the live boundary."""
    durable_before = _durable_counts(db)
    ledger = GlobalUsageLedger(
        max_requests=R3_INCREMENTAL_MAX_REQUESTS,
        max_total_tokens=R3_INCREMENTAL_MAX_REPORTED_TOTAL_TOKENS,
    )
    result: dict[str, Any] = {
        "provider_configuration": {
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL_ID,
            "max_requests": R3_INCREMENTAL_MAX_REQUESTS,
            "max_reported_total_tokens": R3_INCREMENTAL_MAX_REPORTED_TOTAL_TOKENS,
            "max_tokens_per_request": PER_REQUEST_OUTPUT_CAP,
            "provider_timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
            "retries": 0,
            "streaming": False,
            "scenario": "r3_only_incremental",
        },
        "cases": {},
        "failure": None,
    }
    adapter = LiveDeepSeekSmokeAdapter(api_key=api_key, ledger=ledger)
    harness = None
    try:
        async with adapter.create_harness() as harness:
            result["cases"]["r3"] = _observation_payload(
                await harness.run_r3_required_tool_choice(
                    total_tokens_limit=R3_INCREMENTAL_MAX_REPORTED_TOTAL_TOKENS
                )
            )
            result["r3_tool_invocations"] = list(harness.scenario_tool_invocations)
            result["redacted_wire_contracts"] = list(harness.captured_requests)
            result["unfinished_late_tasks_at_close"] = harness.unfinished_late_tasks_at_close
    except BaseException as exc:
        result["failure"] = {
            "technical": classify_smoke_exception(exc),
            "normalized": normalize_smoke_failure(exc).normalized_evaluation_failure,
        }
    finally:
        if harness is not None:
            result["r3_tool_invocations"] = list(harness.scenario_tool_invocations)
            result["redacted_wire_contracts"] = list(harness.captured_requests)
            result["unfinished_late_tasks_at_close"] = harness.unfinished_late_tasks_at_close

    result["provider_reported_usage"] = list(ledger.reported_usage)
    result["reported_total_tokens"] = ledger.reported_total_tokens
    result["actual_http_requests"] = ledger.requests_used
    result["ledger_stop_reason"] = ledger.stop_reason
    result["cost_estimates_usd"] = _cost_estimates(ledger.reported_usage)
    result["durable_counts_unchanged"] = _durable_counts(db) == durable_before
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the one-request PR-03R.3 R3 smoke")
    parser.add_argument("--output", type=Path, required=True)
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
        with Session(engine, future=True) as db:
            result = asyncio.run(run_r3_only_live_smoke(db, api_key=api_key))
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
