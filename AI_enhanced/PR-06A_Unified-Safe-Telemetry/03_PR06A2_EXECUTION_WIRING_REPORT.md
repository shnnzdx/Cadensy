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
  observed. A wrapper return is recorded before the local post-return deadline
  check; a later consumption rejection cannot erase that observed return.
- Pydantic wrapper `usage` is deliberately emitted as `not_reported` with null
  counts. This seam cannot prove a value was provider-originated, and it must not
  convert an ambiguous framework-default zero into a reported zero.
- Legacy tool events use the actual Unknown/Guard/Cache/handler branches. An enum
  name is still serialized as null when it was not exposed in that invocation.
  Pydantic records the actual scoped `get_current_plan` handler outcomes; a handler
  result rejected by local validation is `handler_failed`, never success.
- Deadline, capacity, queued cancellation, and late completion events are mapped
  from actual `AgentLifecycleEvent` phases. All recorder/emitter construction and
  callback work is no-throw.

`agent.runtime_selected` now requires `trace_id` but not `execution_id`: selection
precedes worker admission, and a capacity-rejected request never owns a worker. Model
and tool events still require both IDs. This is the minimal schema correction needed
to avoid inventing an execution id.

## Verification

`tests/test_telemetry_contract.py`, `tests/test_telemetry_privacy.py`, and
`tests/test_telemetry_execution_wiring.py` prove closed-schema construction,
factory/mapping/emitter failure containment, real default-sink failure containment,
unknown/guard/cache/handler facts, structured API terminal categories, deadline
mapping, and marker exclusion. The targeted suite completed: **29 passed in 0.58s**.

The Pydantic post-return deadline regression is run as an isolated FunctionModel
test so no network provider is reachable. The hosted Pydantic compatibility job is
the release evidence for the complete adapter suite; see the
[repository CI workflow](https://github.com/shnnzdx/Cadensy/actions/workflows/build-validation.yml).

## Known evidence boundary

These events observe application wrapper crossings, not a claim that an SDK made a
physical HTTP attempt. No real provider request was issued.

`record_agent_lifecycle()` remains a compatibility no-op. PR-06A must not be
independently promoted to production while lifecycle telemetry remains disabled.
