# v0.3.0-03B-FIX — P1 Evidence Closure

## Scope

This closure addresses only the two P1 evidence findings recorded in `21_V0303B_FINAL_INDEPENDENT_REVIEW.md`:

1. committed PostgreSQL observation for the concurrent B3 recovery race; and
2. direct B6 deadline, late-completion, and worker-cleanup evidence for one real Pydantic Coordinator execution.

It preserves the historical BLOCKED review and the historical Stage 03B report. No production business code, schema, domain authorization, Durable fencing, Agent Execution contract, runtime selector, Legacy runtime, CI/CD, provider setting, or production configuration changed.

## Reviewed source identity

- Worktree: `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing`
- Branch: `codex/v0303b-pydantic-concurrency-fencing`
- HEAD: `b1981d849e24e9c88a4ccf997c8831addbd4a48b`
- Accepted Stage 03A parent: `8012dce80d5d8ecd30906192311d685ed1d9e13f`
- Updated acceptance source SHA-256: `7dbf3dabcba5c2f9460eec3728345e8925b7fbe55d5e30fc972c49906db231a4`

The test uses only existing public application seams: `recover_submission()` plus a fresh SQLAlchemy observer Session for B3, and `ConversationRuntimeCoordinator -> RevisionBracketedPreview -> PydanticChatAgentRuntime -> FunctionModel` for B6. The B6 lifecycle recorder intercepts the existing `execution.emit_event_safely` event factory, passes its actual typed event through `SafeTelemetryEmitter`, and never fabricates an event.

## P1-01 — B3 committed recovery persistence

Node:

`tests/test_pydantic_concurrency_fencing_acceptance.py::test_concurrent_recovery_claims_grant_only_one_current_attempt`

Two bounded, simultaneous recovery claimants use separate `NullPool` physical PostgreSQL connections at the same deterministic expired-lease boundary. Exactly one claim receives execution authority; the losing claim receives `ConversationBusy`.

A new independent `observer` phase opens fresh connections after both claimant threads join and verifies persisted state directly rather than trusting a returned Python object:

- original Submission ID is unchanged;
- Submission is `(attempt_number=2, fence=2, state="running")` and has no Assistant Message ID;
- exactly one Recovery exists for the Submission;
- that Recovery is `(accepted_attempt_number=2, accepted_fence=2, state="running")`;
- its key is one of the two claimant keys;
- one User, zero Assistant, and zero Option rows exist;
- the Plan revision is unchanged.

Focused execution PID attribution was: winning claimant `{44304}`, losing claimant `{49564}`, observer `{11744, 17232, 44940}`. The test asserts each claimant has one physical backend, winner/loser are disjoint, and observer PIDs are disjoint from both.

The existing serial B3 case additionally asserts a duplicate recovery-key replay is replayed and `execution_claimed == false`; it does not create a new recovery authority.

## P1-02 — B6 request timeout and late completion

Node:

`tests/test_pydantic_concurrency_fencing_acceptance.py::test_pydantic_request_timeout_discards_late_function_model_result`

One real Pydantic FunctionModel is blocked by a test-owned Event while the Coordinator's request deadline is 50 ms. The event is released in `finally`. The test proves:

- Coordinator result is exactly `state="failed"`, `failure_category="request_timeout"`.
- An actual typed `AgentDeadlineExceeded` is emitted with `DeadlineScope.REQUEST`, `FailureCategory.REQUEST_TIMEOUT`, and `result_consumed=false`.
- After release, the original running worker reaches the actual done callback; an actual typed `AgentLateCompletion` has `LateCompletionKind.RUNNING_LATE_COMPLETION` and `result_consumed=false`.
- The deadline and late-completion events have the same execution identity.
- The FunctionModel returns, the late-completion callback finishes within a bounded wait, and no pending captured callback remains before the observer read.
- A fresh durable observer finds the Submission in `failed` state with `failure_category="request_timeout"` and no Assistant Message ID; row counts are one User, zero Assistant, zero Option; Plan revision is unchanged.

This does not claim physical cancellation of a running synchronous Python worker. It proves the supported safety contract: the late result is not consumed, cannot reach durable completion, and the observed worker/callback finishes before test teardown.

## Executed validation

All runs used a new task-owned PostgreSQL 18 cluster on loopback only, explicit disposable runtime/test database identities, `APP_ENV=test`, `MOCK_AI=1`, `DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and no DeepSeek/AWS credentials. Pytest's existing test harness deliberately redirects imported application runtime to the validated disposable test database.

| Command | Collected | Passed | Failed | Skipped | Xfailed | Exit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B3/B6 focused nodes | 2 | 2 | 0 | 0 | 0 | 0 |
| Full Stage 03B acceptance module | 7 | 7 | 0 | 0 | 0 | 0 |
| Full Stage 03A acceptance module | 8 | 8 | 0 | 0 | 0 | 0 |
| Durable/PostgreSQL concurrency regression | 15 | 15 | 0 | 0 | 0 | 0 |
| Pydantic/Preview/Telemetry regression | 115 | 115 | 0 | 0 | 0 | 0 |
| Legacy regression | 41 | 41 | 0 | 0 | 0 | 0 |

Exact command strings and source hashes are recorded in `23_V0303B_P1_EVIDENCE_CLOSURE.json`. JSON validation, Python compilation, hash reconciliation, whitespace/diff checks, and secret scanning are recorded there after the final test source is validated.

## Remaining limitations

- B1 remains service admission plus direct Coordinator dispatch, not HTTP/browser acceptance.
- FunctionModel is a local deterministic Pydantic boundary; this is not a real-provider or production test.
- The global bounded Agent worker executor is intentionally not shut down by the test. The new late-completion callback assertion is the supported proof that this test's worker has finished and released its execution lifecycle.
- Full backend regression remains outside the Stage 03B-FIX scope because no production code changed.

## Closure result

Both P1 findings are closed by executed assertions and sanitized evidence. This is a self-consistency closure, not an automatic independent final acceptance or authorization to commit, push, merge, deploy, or start Stage 03C.
