# Cadensy — PR-06A.1 Implementation Report

**Status:** Ready for independent review
**Branch:** `codex/pr06a1-safe-telemetry-contract`
**Base:** `aa13eafd61cc0578824ddd5166f3974eff498d7f` (`origin/main` matched during preflight)

## Scope delivered

PR-06A.1 implements only the safe telemetry contract and legacy logging privacy remediation.

- Added dependency-free typed context, closed v1 event schemas/enums, and a no-throw compact JSON emitter.
- Replaced legacy stdout/local-JSONL trace persistence with a safe in-memory `AgentRunResult.rounds` compatibility summary.
- Removed raw `repr(exc)` stdout output from `base.call_model`.
- Added deterministic contract and privacy-negative tests.

No request context is created in FastAPI and no route/runtime/model/tool/lifecycle event is wired into live execution. That work is intentionally deferred to PR-06A.2 and later.

## Changed files

| File | Reason |
| --- | --- |
| `backend/app/observability/__init__.py` | Narrow public telemetry primitive surface. |
| `backend/app/observability/telemetry.py` | Frozen `TelemetryContext`, closed event types/enums, validation, and injected-sink `SafeTelemetryEmitter`. |
| `backend/app/agents/trace.py` | Safe summary/no-op legacy-output compatibility layer. |
| `backend/app/agents/base.py` | Removed direct raw provider exception stdout printing. |
| `backend/tests/test_telemetry_contract.py` | Schema, enum, timing/token, capacity, dispatch/handler, and emitter tests. |
| `backend/tests/test_telemetry_privacy.py` | Synthetic-marker privacy tests. |
| `backend/tests/test_agent_execution_lifecycle.py` | Replaced deprecated JSONL assertion with no-file/no-stdout contract. |
| `AI_enhanced/PR-06A_Unified-Safe-Telemetry/01_PR06A_PHASE_B_TECHNICAL_DESIGN.md` | Recorded C1–C3 only. |
| `AI_enhanced/PR-06A_Unified-Safe-Telemetry/02_PR06A1_IMPLEMENTATION_REPORT.md` | This implementation evidence. |

No other production source path changed: API, Chat service, preview flow, Pydantic runtime, execution boundary, domain decisions, database schema, CI, and AWS configuration are untouched.

## Contract decisions

`TelemetryContext` has only opaque `request_id`, optional `trace_id`, and optional `execution_id`; it cannot carry domain IDs, message/history/prompt, credentials, arbitrary metadata, or mutable state. Each event uses `cadensy.telemetry.v1`, a fixed flattened envelope, closed event name, and typed event-specific fields. A dict or unexpected constructor field is rejected. The emitter accepts only a `TelemetryEvent`, produces one compact JSON line through an injected sink or dedicated non-propagating stdout logger, catches sink/serialization failures, and never creates a file or external export.

**C1 — capacity is not a deadline.** `AgentExecutionCapacityExceeded` maps to the separate schema event `agent.capacity_rejected`; `AgentDeadlineExceeded` rejects `runtime_capacity`. No live runtime event is emitted in this slice.

**C2 — unknown tool names.** `ToolCallCompleted` accepts an `ApprovedToolName` enum or `null`. `unknown_tool` requires `tool_name=null`, `handler_executed=false`, and `cache_hit=false`. The legacy summary allow-lists established trip tool names, so model-supplied unknown or combined names become `null`.

**C3 — dispatch vs handler failure.** `dispatch_failed` requires `handler_executed=false`; `handler_failed` requires `handler_executed=true`. Typed construction rejects impossible combinations. Existing exception propagation and retry behaviour remain unchanged.

## Legacy compatibility and privacy remediation

`record_agent_round` retains `len(result.rounds)` and consumed flags but now exposes only `round_index`, `requested_tool`, an approved `tool_name` or null, `guard_rejected`, `guard_limit_exceeded`, and `tool_dispatch_status`. It persists no arguments, results, guard wording, model content, prompt/history, or exceptions. Existing internal `tool_results` provenance remains in the established execution mechanism and is not emitted.

`record_model_call` and `record_agent_lifecycle` retain their signatures but are output no-ops. `LOG_DIR` remains only for third-party/test monkeypatch compatibility and is not read or written. `RuntimeObservation.safe_detail` is unchanged and is not an approved telemetry field.

The former `call_model` exception banner/`repr(exc)` output is removed. Its existing `AgentUnavailable` aggregate is retained to preserve control flow and user-facing handling; this PR does not send it to a logging sink.

## Test evidence

Completed from `backend` with `DISABLE_SCHEDULER=1` and `MOCK_AI=1`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telemetry_contract.py tests/test_telemetry_privacy.py tests/test_agents_call_agent.py tests/test_agents_base.py tests/test_agent_execution_lifecycle.py -q
```

Result: **41 passed in 0.25s**.

Also completed `python -m compileall -q app/observability app/agents/trace.py app/agents/base.py` and `git diff --check`: both succeeded.

### Baseline and broader-suite limitation

Before edits, a combined legacy/lifecycle command emitted 19 passing dots but no pytest terminal summary within 30 seconds; confirmed pytest processes were stopped. A later broader runtime command emitted 11 passing dots and similarly failed to reach a terminal summary within 30 seconds; those confirmed processes were stopped. Neither produced a failure traceback before termination. These are **not counted as passing results**. They include database-dependent paths and need a healthy disposable `TEST_DATABASE_URL` investigation before the backend CI-equivalent suite.

The completed 41-test suite covers every changed module and direct legacy/lifecycle contract without a real provider call. Privacy tests inject markers into nested tool arguments/results, guard reasons, provider failure details, provider exception text, and unknown tool names; stdout/stderr and a temporary legacy log directory prove the markers are absent, no legacy JSONL is created, and `call_model` prints no exception content. They also prove internal provenance remains available without observable output and sink failures are swallowed without recursive diagnostics.

## Regression, limitations, and rollback

Existing agent/base/lifecycle tests in the passing suite retain round count/guard flags, tool cache/guard behaviour, mock flow, deadline distinctions, bounded late completion, queued cancellation distinction, and no late-result consumption. The intentional persisted-behaviour change is removal of unsafe legacy trace output. Provider routing, runtime selection, tool invocation/provenance, guards, caching, retries, deadline handling, user responses, classification, revisions, idempotency, and database writes were not modified.

PR-06A.2 (explicit request/trace/execution propagation and live model/tool/lifecycle emission), PR-06A.3 (route terminal/fallback/classification correlation), and PR-06A.4 (authorized AWS delivery/retention evidence and operations) remain unfinished. Pydantic token provenance, SDK retry visibility, CloudWatch delivery, retention, task definition, and production logging remain **Needs Verification**.

Rollback is a single-commit revert after merge (or restore these files from its parent). No migration, database write, feature-gate change, AWS change, external service, or persisted telemetry state needs reversal.

## Mutation evidence

Only local repository files on this feature branch were changed. No AWS/API/deployment/CI configuration command, production database command, Alembic migration, protected-gate enablement, real provider call, or production data mutation was performed.

## Independent Review Findings Resolution

This section records the follow-up to the independent review of
`d5343b1060f4f794155fba2478fb4630d02176b7`.

### P0 — closed event registry

Before the correction, `TelemetryEvent.to_dict()` reflected all dataclass fields and
`SafeTelemetryEmitter.emit()` accepted any `isinstance(TelemetryEvent)`. An untrusted
dataclass subclass could therefore attach a `raw_user_message` field.

The corrected implementation has an exact concrete-type registry and a separate,
fixed payload-field tuple for every approved event class. `to_dict()` rejects an
unregistered concrete type; `emit()` returns `False` without calling a sink for any
unregistered type. An adversarial `LeakyEvent(ChatRequestStarted)` with
`SYNTHETIC_PRIVATE_MARKER_12345` proves the marker reaches neither JSON, stdout, nor
stderr.

### P0 — controlled default stdout sink

The default sink no longer uses `logging.StreamHandler`. A direct controlled writer
calls `sys.stdout.write` once for the complete JSON line and then flushes. This was
selected because a standard logging handler can invoke `handleError()` on write
failure, emit an exception/traceback to stderr, and leave `logger.info()` appearing
successful. The controlled writer lets `SafeTelemetryEmitter.emit()` catch the error,
return `False`, and emit no recursive diagnostic.

The adversarial default-sink test replaces `stdout.write` with a writer that raises an
exception containing `SYNTHETIC_PRIVATE_MARKER_12345`. It verifies `False`, no marker,
no `Logging error`, and no traceback on stdout/stderr.

### P1 — true legacy dispatch facts

`base.call_agent` now derives a closed safe status in its actual Unknown Tool, Guard,
Cache Hit, and handler-return branches, and passes the status tuple to
`record_agent_round`. The trace compatibility layer no longer infers
`handler_succeeded` from the absence of guard/cache flags. When no supplied status can
prove success, its safe summary is `dispatch_unavailable`.

The regression test uses mock replies to prove these outcomes: Unknown Tool →
`unknown_tool` with a null safe tool name; Guard → `guard_rejected`; first handler
return → `handler_succeeded`; cached second call → `cache_hit`. Internal
`tool_results` provenance remains unchanged.

### P2 — schema hardening

Token counts now require non-negative integers: `1.5`, `True`, negatives, and values
above the configured maximum are rejected. Deadline category pairs are closed:
request/request-timeout, provider/provider-timeout, and tool/tool-timeout; capacity
remains a separate event. Runtime, model, and tool events require non-null opaque
trace and execution IDs. Null usage remains distinct from a genuinely reported zero.

Typed construction remains the contract-validation boundary; the emitter itself is
no-throw for unregistered values, serialization faults, and sink faults, returning
`False` rather than affecting a future runtime caller.

### Verification after review fixes

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_telemetry_contract.py tests/test_telemetry_privacy.py tests/test_agents_call_agent.py tests/test_agents_base.py tests/test_agent_execution_lifecycle.py -q
```

Result: **45 passed in 0.34s**. `compileall` and `git diff --check` also succeeded.

### Operational release gate

`record_agent_lifecycle()` remains intentionally neutralized pending PR-06A.2 safe
lifecycle-event wiring. **PR-06A.1 must not be independently promoted to production
while lifecycle telemetry remains disabled.** The Draft PR is a contract/privacy
remediation review artifact, not a production observability release.

### Superseded follow-up

PR-06A.2 subsequently restores lifecycle observation through the typed, no-throw
emitter; see `03_PR06A2_EXECUTION_WIRING_REPORT.md`. The PR-06A.1 release gate above
remains historically accurate for the standalone PR-06A.1 state and must not be used
to claim production readiness for the combined PR without the final review evidence.
