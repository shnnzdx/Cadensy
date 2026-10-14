# Cadensy PR-01C — Agent Deadline, Cancellation & SQLAlchemy Session Isolation

Status: implemented and verified locally with Fake Providers, synthetic
identities, and a named disposable PostgreSQL target only. This is not a
Pydantic AI migration, a real DeepSeek invocation, an AWS/RDS operation, a
deployment, or a production verification.

## Architecture decision

### Original finding

The Chat Agent branch received a request-scoped SQLAlchemy `Session` and
executed it through a background worker. The HTTP response could time out
while synchronous provider or tool work retained that request-owned session.
That made session ownership unclear and left no reliable rule for discarding a
late result.

### Decision

Create a framework-neutral execution boundary in
`app.agents.execution`. The HTTP/chat service performs deterministic
authorization and plan preflight in its request session, then passes only
immutable capabilities (`trip_id`, actor membership id, request text, and
history) to an Agent worker. The worker creates and owns a new `SessionLocal`
instance, builds trip-scoped read-only tools inside that session, and closes it
when the worker actually exits.

The boundary is intentionally usable by the existing Custom Runtime and a
future Pydantic AI adapter. It does not install, import, or migrate to
Pydantic AI.

### Ownership and authority

| Layer | Owns | Explicitly does not own |
|---|---|---|
| HTTP/request service | Request session, account/Guest authorization, deterministic preflight, final safe response. | Agent worker session or durable mutation authority. |
| Agent execution boundary | Per-request deadline, local cancellation signal, bounded worker admission, late-completion trace. | Force-killing Python threads or remote provider work. |
| Agent worker | One short-lived `SessionLocal`, then trip-scoped read-only tool closures. | Request session, `TripMembership` ORM instance, proposal/vote/PlanItem write capability. |
| Tool/domain layer | Read-only plan facts and deterministic classification. | Model-selected writes or applying a change. |
| Product apply flow | Transactional proposal/vote/plan mutations, outside this worker. | Accepting a late Agent result. |

`build_read_only_trip_tools()` remains the capability boundary: it checks the
stored membership/trip relation in the worker's own session and offers no
database-writing tool. Deterministic classification remains in the existing
domain orchestrator; the model can suggest or ask, never apply.

## Deadline and cancellation contract

| Concept | PR-01C behavior | What it does **not** mean |
|---|---|---|
| Request deadline | Outer Chat Agent deadline: 30 s. When it elapses, the request emits a safe degraded reply and locally signals cancellation. | The underlying synchronous thread has stopped. |
| Provider deadline | Each provider invocation receives `min(20 s, request time remaining)` as its client timeout. A recognized provider timeout has its own failure category. | A provider that already accepted a request certainly did no further remote work. |
| Tool deadline | Read-only tool budget: 5 s. The runtime checks before and after each tool; an over-budget result is not cached, added to messages, or used for another model round. | An arbitrary synchronous Python function has been forcibly preempted at five seconds. |
| Local cancellation | The execution context exposes a cancellation signal. Awaitable worker work is cooperatively cancelled; queued futures may be cancelled. | `Future.cancel()` kills an already-running synchronous function. |
| Actual worker completion | The worker releases its own session and worker slot only when it really returns/unwinds. Late terminal state is traceable. | Its result will be read, returned, or committed. |

The process-local Agent executor has four slots. A timed-out,
non-cooperative worker keeps its slot until actual completion. When all slots
are held, new Agent work raises `agent_capacity_exhausted`, which the Chat
branch safely degrades. This bounds stranded local Agent work and avoids a
per-request executor creating unbounded late threads. It is admission control,
not a claim of thread termination.

```text
HTTP request (request Session)
  | authorize membership + deterministic plan preflight
  | snapshot immutable trip/actor/message/history data
  v
bounded Agent worker slot
  | open independent SessionLocal
  | build read-only, trip-scoped tools
  | call provider with provider budget
  | execute tool only while request budget remains
  |
  +-- completes before request deadline --> validate result --> HTTP response
  |
  +-- request deadline expires -------> signal local cancellation
       |                                  return deterministic safe degradation
       |                                  do not consume provider/tool result
       |
       +-- cooperative coroutine ----> receives cancellation, unwinds session
       +-- sync provider/tool --------> may continue until its own call returns
                                           then closes session/releases slot
                                           emits late_completion(result_consumed=false)
```

Lifecycle traces use `request_timeout`, `request_deadline_exceeded`,
`provider_deadline_exceeded`, `tool_deadline_exceeded`,
`agent_capacity_exhausted`, `completed`, and `late_completion`. Their
serialized fields are only phase, opaque execution id, elapsed milliseconds,
whether a result was consumed, and whether cancellation of queued work was
requested. They intentionally exclude prompts, provider output, tool
arguments/results, membership identifiers, and private preferences.

## Implementation record

* `app.agents.execution` supplies deadline contexts, failure taxonomy,
  cooperative async cancellation, late-completion handling, and a four-slot
  process-local worker executor.
* `app.domain.chat.service` no longer captures the request `Session` or a
  request-owned `TripMembership` in the worker closure. It opens `SessionLocal`
  only in that closure and builds tools there.
* `app.agents.base` accepts a deferred tool factory, applies the remaining
  provider budget to the provider client, and rechecks the request deadline
  after provider/tool return before a result can advance the Agent loop.
* `app.agents.trace` records a trace-safe lifecycle event separately from the
  existing Agent-round trace.
* No Agent tool was given write access, and no plan/proposal/vote write path
  was changed.

## Failure-injection and regression evidence

All commands below used:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr01c_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
```

The test harness accepts only a local database name matching its disposable
test-database policy, recreates it before the suite, and uses synthetic test
data. `GEOAPIFY_API_KEY` was blanked for this run so tests could not call the
external place provider. Provider scenarios use fakes; no real DeepSeek call
occurred.

| Evidence | Result | What it proves |
|---|---:|---|
| `tests/test_agents_call_agent.py tests/test_agent_execution_lifecycle.py tests/test_pr01c_agent_lifecycle.py` | Pass: 22 | Provider budget propagation and taxonomy; cooperative cancellation; non-cooperative late result discard; worker-slot capacity; independent worker-session ownership/close (including a real disposable-database factory); over-deadline tool discard; cross-trip rejection; no durable PlanItem/proposal/vote/round side effects; trace privacy shape. |
| `tests/test_agents_tools.py` | Pass: 31 | Existing read-only tool semantics and trip scope still hold. |
| `tests/test_chat_agent_branch.py tests/test_agents_base.py tests/test_agents_call_agent.py tests/test_agents_tools.py tests/test_pr01c_agent_lifecycle.py tests/test_agent_execution_lifecycle.py` | Pass: 112 | Authorized Agent/Chat regression range, including Chat response contract, domain classification, privacy handling, safe fallback, and lifecycle contracts. |
| `tests/test_chat.py tests/test_chat_safety_and_time.py` | Historical fixture result: 13 failed, 13 passed | The failures are pre-existing raw `X-Membership-Id` API fixtures now rejected with `401` by PR-01B. They are not repaired by this PR and no header fallback was restored. |

The worker-session tests hold the HTTP request session separately, assert the
tool factory receives a distinct worker session, then inject a slow provider
and a slow read-only tool. In both cases the HTTP branch has already returned
safe degradation, durable table counts are unchanged, the worker session is
closed only after actual late completion, and the terminal trace says
`result_consumed=false`.

## Resource cleanup and safe degradation

Resource ownership is deliberately tied to the actual worker lifetime:

1. Worker admission obtains one bounded slot.
2. The worker opens one `SessionLocal` in a context manager.
3. A normal return, provider/tool deadline, exception, or cooperative cancel
   unwinds the context manager and releases the slot.
4. A request timeout does not wait for non-cooperative work. It returns the
   existing deterministic, read-only fallback; the eventual callback releases
   the slot and emits a trace-only late completion.

Rollback is code-only: remove the PR-01C execution boundary and restore the
previous synchronous request path only under separate change approval. That
would reintroduce the identified session-lifetime hazard, so the preferred
operational response to a provider issue is the implemented safe degradation
or capacity backpressure, not a longer timeout and not a security rollback.

## Known limitations and remaining risks

* Python cannot safely force-stop arbitrary running synchronous functions.
  A remote provider might complete work after local timeout, and a sync tool
  might outlive its 5 s policy budget. PR-01C proves isolation, discard, trace,
  and bounded admission; it does not claim remote cancellation or hard thread
  termination.
* Provider client timeout is an actual local socket/client limit; tool deadline
  is a post-return safety gate for existing synchronous tools. Any new external
  tool must define its own I/O timeout within the remaining tool/request
  budget before it is admitted.
* Four slots bound a single process only. Multi-process deployments need
  process-level capacity configuration/metrics and operational saturation
  alerts before production rollout.
* Existing broad Chat HTTP fixtures still require migration from the retired
  Membership Header model. They remain explicitly separate from PR-01C.
* This PR does not resolve browser bearer XSS/shared-device exposure, Guest
  logout network failure, Guest Session binding-corruption verification, or
  the `change_proposal.extended_at` schema drift. See
  `13_PR01B_VERIFICATION_FOLLOW_UP.md` for the PR-01B-specific tracked work.

## Changed files

| Area | Files |
|---|---|
| Execution boundary | `backend/app/agents/execution.py`, `backend/app/agents/base.py`, `backend/app/agents/trace.py` |
| Chat session ownership | `backend/app/domain/chat/service.py` |
| Failure injection | `backend/tests/test_agent_execution_lifecycle.py`, `backend/tests/test_pr01c_agent_lifecycle.py`, `backend/tests/test_agents_call_agent.py` |
| Decisions and tracking | This document; `13_PR01B_VERIFICATION_FOLLOW_UP.md` |

## PR-02 Evaluation Foundation readiness verdict

**Conditionally ready at the runtime boundary.** PR-01C supplies a
framework-independent worker/session/deadline contract that a future Pydantic
AI compatibility PoC can use without receiving request-scoped ORM state or
write capabilities. PR-02 must still be separately authorized and must first
validate the pinned Pydantic AI version, Fake Model behavior, DeepSeek tool
calling/structured output/thinking compatibility, error mapping, and the same
deadline/lifecycle contracts. No framework migration starts as part of this
verdict.
