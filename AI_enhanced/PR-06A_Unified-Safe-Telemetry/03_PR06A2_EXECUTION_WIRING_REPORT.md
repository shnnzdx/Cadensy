# PR-06A.2 — Execution Wiring Report

## Implemented boundary

Telemetry context is explicitly carried on `RuntimeRequest`; no context variable,
global request state, prompt data, tool arguments, model response, or exception text
is used for correlation. A runtime creates one invocation trace and passes one
execution id into `run_agent_with_deadline`; the worker uses that exact id.

- Legacy model events are emitted around `_invoke_agent_provider`, the known SDK
  wrapper crossing. `MOCK_AI` emits `mock`, not `provider_sdk`.
- Pydantic events are emitted around `_DeadlineBoundModel.request`; `FunctionModel`
  is emitted as `fake`. A call is counted before awaiting it, so failed calls are
  observed. Missing usage stays `not_reported`; reported zero stays zero.
- Legacy tool events use the actual Unknown/Guard/Cache/handler branches. Pydantic
  emits only its actual scoped `get_current_plan` handler outcomes.
- Deadline, capacity, queued cancellation, and late completion events are mapped
  from actual `AgentLifecycleEvent` phases. All recorder/emitter construction and
  callback work is no-throw.

`agent.runtime_selected` now requires `trace_id` but not `execution_id`: selection
precedes worker admission, and a capacity-rejected request never owns a worker. Model
and tool events still require both IDs. This is the minimal schema correction needed
to avoid inventing an execution id.

## Verification

`test_telemetry_execution_wiring.py` proves mock model, real handler dispatch,
reported-zero usage, deadline mapping, and marker exclusion. The targeted telemetry
suite completed: **16 passed in 0.07s**.

## Known evidence boundary

These events observe application wrapper crossings, not a claim that an SDK made a
physical HTTP attempt. No real provider request was issued.
