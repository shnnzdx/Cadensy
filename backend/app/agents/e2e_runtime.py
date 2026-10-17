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

from .runtime_contract import (
    AgentCandidateOption,
    AgentReplyOnly,
    RuntimeObservation,
    RuntimeRequest,
    RuntimeResult,
)


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
        options: tuple[AgentCandidateOption, ...] = ()
        if "[E2E-OPTION]" in request.message and request.selected_item_ref:
            # This is deliberately suggestion syntax only.  The real
            # RevisionBracketedPreview still validates the item, patch and
            # captured revisions before the coordinator may persist it.
            options = (
                AgentCandidateOption(
                    id="e2e-option-move-1500",
                    label="Move to 3:00 PM",
                    title="Move this activity to 3:00 PM",
                    body="A local acceptance-only advisory option.",
                    tradeoff="The current plan must be checked again before any Apply.",
                    item_ref=request.selected_item_ref,
                    safe_patch={"start_hour": 15.0},
                ),
            )
        return RuntimeResult(
            reply=f"E2E assistant completion: {request.message}",
            outcome=AgentReplyOnly(),
            candidate_options=options,
            observation=RuntimeObservation(
                trace_id="local-e2e-runtime",
                round_count=1,
                total_tokens=0,
                total_elapsed_ms=0,
            ),
        )
