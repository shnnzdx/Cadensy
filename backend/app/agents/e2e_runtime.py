"""Deterministic Runtime adapter for the explicitly local PR-06B.4 browser harness.

It implements the same narrow ``ChatAgentRuntime`` contract as production
adapters.  It is never selected by default and the factory refuses it outside
local/test environments.  The optional file barrier lets a browser test prove
that a revoked Membership fences a late completion without adding an HTTP test
control route.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .runtime_contract import AgentReplyOnly, RuntimeObservation, RuntimeRequest, RuntimeResult


class LocalE2ERuntime:
    """A content-free, deterministic completion that still crosses the Runtime seam."""

    def run(self, request: RuntimeRequest, capability, *, execution) -> RuntimeResult:
        # Enter the existing capability boundary: this proves the adapter is
        # passed only scoped, read-only authority, not an ORM Session.
        capability.run_with_read_only_tools(lambda _tools: None)
        if "[E2E-BLOCK]" in request.message:
            control = Path(os.environ["CADENSY_E2E_RUNTIME_CONTROL_DIR"])
            control.mkdir(parents=True, exist_ok=True)
            (control / "started").write_text("1", encoding="ascii")
            deadline = time.monotonic() + 20
            while not (control / "release").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("local E2E runtime barrier was not released")
                time.sleep(0.02)
        return RuntimeResult(
            reply=f"E2E assistant completion: {request.message}",
            outcome=AgentReplyOnly(),
            candidate_options=(),
            observation=RuntimeObservation(
                trace_id="local-e2e-runtime",
                round_count=1,
                total_tokens=0,
                total_elapsed_ms=0,
            ),
        )
