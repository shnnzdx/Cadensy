# PR-04D2 — Shared Symmetric Evaluation Harness Implementation

## Verdict

**CONDITIONAL — HARNESS GAP**

PR-04D2 implements and verifies an offline, one-case-at-a-time symmetric
harness.  Both Runtime paths retain their real orchestration, receive fakes
only below that orchestration, share the same synthetic facts and capability
construction, and record actual read-tool invocations.

It intentionally does **not** run or score a formal eight-case A/B comparison.
The remaining gate is explicit: the frozen Golden grader treats
`expected_tool_calls` as a successful trajectory, while an actual failed tool
attempt is now deliberately visible.  That policy must be separately decided
before PR-04D3 can claim a complete aggregate score.

## 1. Verified base

| Item | Value |
| --- | --- |
| `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| PR-04D-Prep checkpoint | `eb913e3a1b7a9bbf79998990705de65ac87ae1bc` |
| PR-04D1 checkpoint | `1b403ee81e40a388f8ecddcd4149edee4b5c3579` |
| Branch | `codex/pr04d2-symmetric-harness` |
| Worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04d2-symmetric-harness` |
| Dataset version | `chat-change-preview-v1` |
| Canonical LF SHA-256 | `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f` |

## 2. Changed files

- `backend/app/agents/legacy_runtime.py`
- `backend/evals/symmetric_harness.py`
- `backend/tests/test_symmetric_evaluation_harness.py`
- `.github/workflows/build-validation.yml`
- `backend/tests/test_ci_workflow_contract.py`
- this report

No Chat Service, Domain rule, Runtime selector/default, frozen Dataset, grader
criterion, Pydantic feature surface, production Provider composition, or
product behavior changed.

## 3. Final harness architecture

```text
single frozen case (execution fields only)
      |
      v
neutral scenario compiler
  - case id / fake_provider / input / fixture preconditions only
  - no expected_* / oracle / allowed-output fields
      |
      v
same committed synthetic trip + same authorized actor
      |
      v
same Chat Service deterministic preflight
      |
      +-- application-only: returns without a Runtime
      |
      +-- Runtime-reached:
            `LegacyReadTripCapability` + default-off recorder
            -> distinct short-lived worker Session per execution
            -> Legacy: real `base.call_agent` -> fake `_invoke_agent_provider`
            -> Pydantic: real `Agent.run` -> fake `FunctionModel`
            -> common sanitized, ungraded observation
```

`run_single_symmetric_case(...)` intentionally accepts only one selected case
and returns ungraded `HarnessObservation`.  It has no iteration over Dataset
cases, no grader call, no score, and emits no A/B report.  Formal comparison is
therefore still reserved for PR-04D3.

## 4. Legacy fake seam

The harness patches only:

```text
app.agents.base._invoke_agent_provider
```

with a deterministic callable returning `AgentProviderReply`.  It does not
patch or return from `base.call_agent`.

For a suggested change, the Legacy fake produces three real provider rounds:

```text
get_current_plan tool call
-> classify_change tool call
-> user-visible final reply
```

The unmodified Legacy loop performs tool lookup, prerequisite guard, cache,
handler invocation, tool-result accumulation, deadline boundaries, round
tracking, and result mapping.  The focused test proves two actual handler
events and three lower-boundary dispatches.  A structural regression test also
rejects both `patch.object(base, "call_agent", ...)` and any `AgentRunResult`
construction in the harness module.

The harness scopes `is_mocked()` to false only inside the Legacy evaluation
context, supplies an in-memory fake provider configuration, and patches the
lower call before any OpenAI client creation.  This does not alter default CI
or production environment behavior.

## 5. Pydantic fake seam

The test-only Pydantic composition creates:

```text
PydanticChatAgentRuntime(FunctionModel(script))
```

and injects it only through the test/evaluation composition seam.  It does not
fake `PydanticChatAgentRuntime.run`, alter `runtime_factory.py`, or construct a
production provider.

The real Pydantic path remains:

```text
run_agent_with_deadline
-> capability-owned worker Session
-> `_DeadlineBoundModel`
-> Pydantic `Agent.run`
-> FunctionModel
-> registered `get_current_plan`
-> typed output validation and RuntimeResult mapping
```

The normal suggested-change script must read `get_current_plan` before returning
`runtime_suggested_change`.  The focused test observes two FunctionModel
dispatches, one actual scoped tool invocation, and a change preview.  The
isolated Pydantic suite also reruns the existing validator contract that rejects
an unread suggested item.

## 6. Neutral scenario model

`NeutralScenario` contains only:

```text
case_id, kind, public item key, safe patch, user-visible reply
```

Supported kinds are:

```text
suggested_change
provider_failure
tool_failure
reply_only
must_not_run
```

`compile_neutral_scenario` reads only `id`, `fake_provider`, `input`, and
fixture-time facts supplied to its lowerers.  It never reads
`expected_business_outcome`, `expected_failure_taxonomy`,
`expected_tool_calls`, `domain_oracle.expected_path`, or
`allowed_output_kinds`.

The neutral model is not a Legacy `AgentRunResult`.  The Legacy lowerer emits
`AgentProviderReply` / `AgentToolCall`; the Pydantic lowerer emits
`FunctionModel` `ModelResponse` / `ToolCallPart`.  That preserves each real
framework's protocol while using the same scenario meaning.

## 7. Actual tool recorder

`LegacyReadTripCapability` now accepts an optional,
default-`None` `tool_invocation_recorder` hook.  With no hook (the production
path), it does not wrap or replace a handler.

With the evaluation hook, each freshly built `AgentTool.handler` is wrapped
before `LegacyRuntimeReadTool` is created.  The wrapper records only:

```text
tool name
sanitized argument projection
success | failure
```

It never stores tool output, exceptions, Session objects, membership ids,
private constraint wording, credentials, or authorization material.  Internal
plan-item ids become the fixture's public symbolic key; unsafe strings are
redacted.

This placement matters: the Legacy loop receives the normal underlying
`AgentTool` (including guards/cache) while the Pydantic adapter receives the
same `LegacyRuntimeReadTool` and invokes the same wrapped handler.  A generic
outer `RuntimeReadTool` decorator would have failed the Legacy type boundary or
been bypassed by `_legacy_tools`.

## 8. Provider and tool failure mapping

| Scenario | Actual event | Harness evidence | No Golden-derived behavior |
| --- | --- | --- | --- |
| provider failure | lower fake raises `SyntheticProviderFailure` | `provider_events` contains `provider_failure` before the Runtime-safe degradation path | yes |
| tool failure | Runtime requests real `get_current_plan`; test-only tool-factory wrapper raises `SyntheticToolFailure` | recorder retains `{get_current_plan, {day: all}, failure}` and re-raises | yes |

The former prebuilt `AgentRunResult(stopped_reason="synthetic_tool_failure")`
path is not used by the symmetric harness.  Both lowerers now ask for the
common read tool; the handler genuinely fails and the recorder sees the
attempted failure.

## 9. Same capability and Session proof

For both Runtime choices, the harness creates the same
`LegacyReadTripCapability` shape from the same committed disposable fixture:

- identical trip id and authorized membership id;
- identical synthetic plan facts;
- the same read-only tool builder and optional recorder hook;
- a separately created, short-lived SQLAlchemy Session per Runtime execution.

The harness tracks every worker Session and asserts it is not the caller's
request Session.  This preserves PR-01C ownership: fairness is the same scoped
facts, never sharing a Session object across runtimes.

Intentional trajectory difference remains visible:

```text
Legacy:   get_current_plan -> classify_change -> final reply
Pydantic: get_current_plan -> typed suggested change
```

The Application Service still obtains the authoritative deterministic preview;
neither Runtime gets Domain write authority.

## 10. Network-deny proof

Every harness execution patches synchronous and asynchronous `httpx` request
methods with an assertion guard and records `network_attempts`.  Focused tests
assert zero for Legacy, Pydantic, application-only, provider-failure, and
tool-failure paths.  Pydantic AI additionally keeps
`models.ALLOW_MODEL_REQUESTS = False` and receives `FunctionModel` only.

The lower Legacy function is patched before the code that imports/constructs an
OpenAI client, so a provider request is neither possible nor needed.

## 11. Oracle-independence proof

The focused tests deep-copy an explicit-time case, mutate every prohibited
Golden/oracle field, then compare:

- compiled neutral scenario;
- Legacy single-case observation; and
- Pydantic single-case observation.

All are identical.  A source-level test rejects forbidden expected/oracle names
inside the scenario compiler.  No evaluator observation in this harness is
created from expected labels.

## 12. Application-only and Runtime-discriminating handling

The harness records `runtime_executed` from actual lower-boundary dispatch.

| Group | Frozen cases | Harness handling |
| --- | --- | --- |
| Runtime-discriminating | `explicit-time-notice`, `ambiguous-time`, `booked-item-confirm`, `provider-failure-fallback`, `tool-failure-fallback` | available for a future Runtime comparison after the remaining grader decision |
| Application-only | `ambiguous-item`, `privacy-injection`, `cross-trip-denied` | preserved as end-to-end product coverage; never used to rank a Runtime |

Focused execution proves `ambiguous-item` returns before either Runtime has a
provider dispatch or tool invocation.

## 13. Verification

All database commands used the explicitly named disposable local PostgreSQL
database `cadensy_pr04d2_test`; `DATABASE_URL` was a distinct local runtime
placeholder.  No AWS/RDS or production database was used.

| Command scope | Result |
| --- | --- |
| Focused PR-04D2 harness tests | **5 passed** |
| Isolated Pydantic compatibility / smoke / composition / evaluation / CI-contract range | **107 passed** |
| Approved Legacy full partition with all Pydantic-only modules explicitly ignored | **510 passed**, JUnit **0 skips** |
| `pip check` — Pydantic isolated environment | no broken requirements |
| `pip check` — Legacy regression environment | no broken requirements |
| Dataset canonical SHA verification | matches approved V1 hash |

The CI workflow now excludes `tests/test_symmetric_evaluation_harness.py` from
the Legacy job and explicitly executes it in the isolated Pydantic job.  The
updated CI contract test verifies both requirements, avoiding an unapproved
Legacy dependency skip.

## 14. Remaining gap

The frozen `tool-failure-fallback` Golden case declares
`expected_tool_calls: []`.  The new real trajectory correctly emits an
attempted failed `get_current_plan` call.  Feeding that event unchanged to the
current grader would make the tool trajectory comparison fail.

This is intentional evidence, not a reason to hide the invocation or relax a
test.  Before PR-04D3, a separately approved policy must specify whether the
Golden expected-tool list represents only successful calls, and if so how the
grader stores/compares failed attempts without modifying Dataset V1.  Provider
failure has an equivalent separate actual-event channel; it does not rely on a
Golden label.

## 15. Recommendation

**CONDITIONAL — HARNESS GAP**

The symmetric execution boundary, shared scenario compiler, capability/session
parity, recorder, network guard, and oracle-independence controls are ready.
Do not run a formal eight-case score until the failed-tool observation policy
is independently authorized.  No formal A/B execution occurred in PR-04D2.
