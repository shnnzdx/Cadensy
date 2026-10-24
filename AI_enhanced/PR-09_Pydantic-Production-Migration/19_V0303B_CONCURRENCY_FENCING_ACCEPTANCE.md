# v0.3.0-03B — Pydantic Concurrency, Idempotency, Lease Recovery & Fencing Acceptance

## Recommendation

**PASS — ready for independent code review.** This stage adds acceptance evidence only. It does not modify production code, database schema, runtime selection, Legacy behavior, configuration, CI, PR #31, or production.

## Baseline and scope

- Stage 03B worktree: `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing`
- Branch: `codex/v0303b-pydantic-concurrency-fencing`
- Exact baseline: `b1981d849e24e9c88a4ccf997c8831addbd4a48b`
- Verified parent: `8012dce80d5d8ecd30906192311d685ed1d9e13f`
- Stage 03A worktree was verified clean before the Stage 03B worktree was created. Its acceptance module and reports were present at the stated baseline.
- PR #31 was inspected independently and left open, clean, and unchanged. No push, PR update, merge, deployment, or commit was performed.

The sole code addition is `backend/tests/test_pydantic_concurrency_fencing_acceptance.py`. It uses a real `PydanticChatAgentRuntime`, local Pydantic `FunctionModel`, `RevisionBracketedPreview`, `ConversationRuntimeCoordinator`, and durable PostgreSQL state. The FunctionModel is a deterministic local provider boundary; no provider network call is made.

## Existing contract audited

`service.submit_message()` owns canonical idempotency and the sole running-submission slot. An active matching idempotency key replays without a new execution claim; a canonical mismatch raises `IdempotencyConflict`; a different key while running raises `ConversationBusy`; an expired same-key submission obtains a new attempt/fence.

`recover_submission()` locks the owner-scoped submission, accepts only an expired running lease, increments attempt and fence, records a recovery row, and replays the same recovery key without granting new authority. `context.build_context()` requires a current, unexpired matching execution identity. `complete_submission()` and `fail_submission()` require running status, live lease, and exactly matching attempt/fence; otherwise they raise `StaleFence`. The coordinator uses short, separate context, Preview, and fenced terminal transactions. Preview remains the independent scoped-read/revision bracket and provides advisory options only.

Prior PostgreSQL service tests covered many synthetic-runtime races. This stage supplies the missing direct Pydantic FunctionModel integration at that same durable authority boundary.

## Scenario matrix

| Scenario | Direct Pydantic invocation | Durable finding |
| --- | --- | --- |
| B1 same-key replay and canonical conflict | One blocked/released FunctionModel invocation | Same submission ID; one user, one assistant, zero options; active and completed replays did not claim a second execution; mismatched canonical body raised `IdempotencyConflict`. |
| B2 different-key concurrent submit | One blocked/released FunctionModel invocation | Competing key raised `ConversationBusy`; no second user message or model execution; first completed once. |
| B3 expiry/replay/recovery | Durable service authority path | Active replay stayed non-claiming; expired same-key replay advanced `(attempt, fence)` from `(1,1)` to `(2,2)`; independent recovery advanced it to `(3,3)`; recovery-key replay stayed non-claiming. |
| B3 concurrent recovery claims | Durable service authority path | Two physical PostgreSQL claimants raced at one expired boundary; exactly one obtained `(2,2)` and execution authority; the other received `ConversationBusy`; no duplicate user/assistant/options. |
| B4 stale success | Real blocked FunctionModel, Preview, coordinator | Recovery committed `(2,2)` while old `(1,1)` execution was blocked. Old success was `discarded`; it persisted no assistant/options. Current attempt completed exactly once. |
| B5 stale failure | Real blocked FunctionModel, Preview, coordinator | After recovery `(2,2)`, the old model raised a controlled exception. The old terminal failure was `discarded`; it did not fail or overwrite the current attempt. Current attempt completed exactly once. |
| B6 deadline and late return | Real Pydantic deadline framework and blocked FunctionModel | Request deadline was exceeded with `result_consumed=false`; after release, late model return was observed and had no durable assistant/options or plan mutation. |

Every test uses bounded synchronization and `finally` release/join cleanup. Background exceptions are captured and asserted. No long sleep or fabricated execution identity is used.

## Physical PostgreSQL evidence

The disposable task-owned PostgreSQL instance was loopback-only and the test-only `NullPool` factory from Stage 03A recorded the actual server backend PID at each transaction start. Distinct `Session` identity was not treated as evidence.

Final focused evidence run recorded these sanitized PID groups:

- B2: worker `{7756, 8660, 19656, 31412, 49772}`, competing submit `{50524}`, observer `{13136, 49832}`. Worker and competing submit were disjoint; competing submit and observer were disjoint.
- Concurrent B3 recovery: claimants `{6768, 50100}`; exactly two distinct physical connections participated.
- B4 success: pre-recovery worker `{52568, 54104}`, committed recovery `{14068}`, post-recovery worker `{23676, 28572, 38276, 48016, 48260, 50520, 50832, 53796}`, observer `{13204, 23184}`. Required disjointness was asserted.
- B5 failure: pre-recovery worker `{43088, 47672}`, committed recovery `{41440}`, post-recovery worker `{5148, 15300, 22688, 23128, 28860, 43172, 51688, 52428}`, observer `{32636, 41052}`. Required disjointness was asserted.

Committed setup was created before concurrent actors. Each recovery mutation was committed from the independent mutator transaction; later independent observer reads verified the resulting attempt/fence, terminal state, row counts, and unchanged plan revision.

## Test results

All commands used `APP_ENV=test`, `MOCK_AI=1`, `DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, a fresh task-owned loopback PostgreSQL `DATABASE_URL`/`TEST_DATABASE_URL`, and no DeepSeek/AWS credentials. The connection URL is intentionally omitted.

| Command (from `backend/`) | Collected | Passed | Failed | Skipped | Xfailed | Exit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `python -m pytest tests/test_pydantic_concurrency_fencing_acceptance.py -q -s` | 7 | 7 | 0 | 0 | 0 | 0 |
| `python -m pytest tests/test_pydantic_durable_runtime_acceptance.py -q` | 8 | 8 | 0 | 0 | 0 | 0 |
| `python -m pytest tests/test_pr06b2_conversation_concurrency_postgres.py tests/test_pr06b3_runtime.py -q` | 15 | 15 | 0 | 0 | 0 | 0 |
| `python -m pytest tests/test_pydantic_runtime.py tests/test_pydantic_composition.py tests/test_pydantic_ai_poc.py tests/test_b3_revision_bracketed_preview.py tests/test_telemetry_contract.py tests/test_telemetry_execution_wiring.py tests/test_telemetry_privacy.py -q` | 115 | 115 | 0 | 0 | 0 | 0 |
| `python -m pytest tests/test_legacy_schema_compat.py tests/test_runtime_contract.py tests/test_runtime_composition.py -q` | 41 | 41 | 0 | 0 | 0 | 0 |

A preliminary command referenced a nonexistent `tests/test_legacy_runtime.py`; pytest collected zero tests and returned an invocation error. It did not run or invalidate any test result, and was immediately replaced by the actual Legacy coverage command above.

## Limitations and evidence boundary

- B1 exercises service admission plus direct coordinator dispatch, not a full HTTP request orchestration path. It does not claim browser or HTTP end-to-end coverage.
- FunctionModel is intentionally local and deterministic. This is direct Pydantic integration, not provider acceptance and not a real DeepSeek call.
- Deadline behavior proves safe non-consumption and no late persistence. It does not claim physical termination of a synchronous worker thread; the FunctionModel is explicitly released and observed to return.
- Full backend regression is deferred to Stage 03C because production code is unchanged and all required focused suites passed.

## Source reconciliation

Exact baseline and source SHA-256 values, scenario dispositions, sanitized backend PID evidence, and commands are in `20_V0303B_CONCURRENCY_FENCING_EVIDENCE.json`. JSON contains no credentials, raw user messages, provider payloads, or database connection URL.

## Final Stage 03B recommendation

The central invariant is demonstrated for the covered flows: **only the current, authorized, unexpired submission attempt may persist an assistant result.** Replay, concurrent submission, expired-lease takeover, recovery contention, stale successful completion, stale failure, and a late timeout result did not bypass the application-owned durable fence. Recommend independent review; do not start Stage 03C automatically.

## 03B-FIX correction — P1 evidence closure

This report remains the historical Stage 03B acceptance record. The subsequent
independent review in `21_V0303B_FINAL_INDEPENDENT_REVIEW.md` correctly found
that its B3 concurrent-recovery wording did not prove committed recovery state
through a fresh observer transaction, and that its B6 wording exceeded the
test's typed deadline/late-completion and worker-cleanup assertions.

Those findings are not erased or reclassified retroactively. They are closed
only by the separately executed, source-hash-bound assertions and evidence in
`22_V0303B_P1_EVIDENCE_CLOSURE.md` and
`23_V0303B_P1_EVIDENCE_CLOSURE.json`. The closure adds no production behavior,
schema, configuration, or runtime-selection change.
