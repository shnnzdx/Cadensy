# v0.3.0-03A Independent Acceptance Code Review

Date: 2026-10-23
Review mode: read-only; no production, runtime, schema, configuration, or test-source changes were made by this review.

## Review identity and scope

| Item | Observed value |
| --- | --- |
| Worktree branch | `codex/v0303a-pydantic-durable-acceptance` |
| Reviewed HEAD / candidate baseline | `8012dce80d5d8ecd30906192311d685ed1d9e13f` |
| Parent | `ce74137` |
| `origin/main` merge-base | `5ce3782df8222cc87fd468bf1cf649a6d4aefe69` |
| Candidate relationship | PR #31 candidate remains unmerged; Stage 03A adds untracked acceptance artifacts on top of its reviewed candidate commit. |
| Tracked diff from candidate HEAD | Empty; the Stage 03A scope is three untracked files. |

Reviewed untracked files:

- `backend/tests/test_pydantic_durable_runtime_acceptance.py`
- `AI_enhanced/PR-09_Pydantic-Production-Migration/13_V0303A_DURABLE_LIFECYCLE_ACCEPTANCE.md`
- `AI_enhanced/PR-09_Pydantic-Production-Migration/14_V0303A_DURABLE_LIFECYCLE_EVIDENCE.json`

The evidence file's recorded SHA-256 values match the current test and acceptance-document bytes:

- test: `e4950714f0916e14ab39573a811b6e847139d1e4a439f40e7b2e5d10d0ef7580`
- acceptance document: `b150c41c9edc455755d29c90ca659a92baa7f638f12a4613ed38833a74579eb6`

`git diff --check` and each untracked-file no-index whitespace check were clean. No credentials, `.env` files, database data, generated logs, or production configuration are in the reviewed change scope.

## Architecture and scenario assessment

The acceptance module invokes the public `ConversationRuntimeCoordinator.execute` seam with the real `PydanticChatAgentRuntime`, Pydantic AI `FunctionModel`, revision-bracketed Preview, scoped tools, Conversation service, and PostgreSQL persistence. Provider requests are disabled at `backend/tests/test_pydantic_durable_runtime_acceptance.py:33-35`.

- **A1 two turns — PASS.** The test asserts the persisted User/Assistant/User/Assistant sequence and confirms the second model run contains exactly one copy of the first stored user and assistant messages (`:140-200`). This establishes server-owned durable reconstruction at this seam.
- **A2 durable options — BLOCKED as an acceptance claim.** The test verifies persisted, projected, currently resolvable advisory options (`:256-286`), but calls `resolve_historical_option` before changing any captured revision. It does not prove stale or invalid persisted options are rejected. The evidence assertion `historical_option_resolution_rechecks_state: true` overstates this direct test. The existing production guard is not a substitute for the required direct acceptance case.
- **A3 pre-runtime revocation — PASS.** The current admission path rejects before a FunctionModel invocation and persists no unauthorized output.
- **A4 mid-runtime revocation — PARTIAL.** The Event barrier, committed membership change, final rejection, and no durable Assistant/Option output are substantively exercised (`:353-401`). See the independent-connection finding below.
- **A5 cross-trip isolation — PASS.** A foreign trip/membership identity is rejected before the model can receive Conversation history.
- **A6 Plan/facts revision fencing — PARTIAL.** Both revision changes are committed after the read/tool phase and before model release; the resulting `stale_context` and absence of output are asserted (`:480-535`). See the independent-connection finding below.

## PostgreSQL transaction and concurrency audit

The module correctly avoids the rollback-only `db.connection()`/savepoint fixture pattern. It creates engine-bound session objects through `sessionmaker(bind=test_engine, ...)` (`:38-39`), commits fixture/submission/writer work in `factory.begin()` contexts, and gives the coordinator the same sessionmaker (`:93-104`). The A4/A6 synchronization is deterministic: it uses bounded `threading.Event` waits, releases in `finally`, joins with bounds, and surfaces worker exceptions (`:353-388`, `:480-528`). No sleep-based race was found.

However, the test does **not** demonstrate that the concurrently relevant operations use distinct physical PostgreSQL connections. At the FunctionModel barrier, the Preview/context transactions may already have returned their connections to the pool. The writer can therefore reuse the same backend connection, and the test records neither checked-out connection identity nor `pg_backend_pid()`. Independent SQLAlchemy `Session` objects and committed transactions are demonstrated; independent physical PostgreSQL connection visibility is not.

This is an acceptance-evidence deficiency, not a confirmed Pydantic/Preview/Durable product defect. A follow-up correction must retain two leased connections across the barrier and assert distinct PostgreSQL backend PIDs (or equivalent physical connection identities) before committing the revocation/revision mutation.

## Findings

### P1 — Required independent physical PostgreSQL connection proof is absent

**Locations:** `backend/tests/test_pydantic_durable_runtime_acceptance.py:38-39`, `:353-401`, `:480-535`; claims in `13_V0303A_DURABLE_LIFECYCLE_ACCEPTANCE.md:37-40` and `14_V0303A_DURABLE_LIFECYCLE_EVIDENCE.json` A4/A6 attribution.

The tests prove an engine-bound, separately committed Session transaction, but not distinct backend connections during the race. Consequently, the required independent-connection acceptance property is **NOT VERIFIED**. Do not treat the documented “second session” language as proof of physical connection independence.

### P1 — A2 evidence claims a stale-state check that the direct acceptance test never exercises

**Locations:** `backend/tests/test_pydantic_durable_runtime_acceptance.py:256-286`; `14_V0303A_DURABLE_LIFECYCLE_EVIDENCE.json` A2 `historical_option_resolution_rechecks_state`.

The test asserts successful current-state resolution at `:283-284`; it neither changes Plan/facts revisions nor manufactures an invalid historical option before resolution. Its appropriate claim is “current-state historical resolution succeeds.” A stale/invalid rejection test is required before calling the larger acceptance claim verified.

## Test and evidence review

The recorded Stage 03A evidence is internally consistent with the hashed source:

- direct acceptance module: `7 passed in 1.71s`, exit `0` (recorded);
- focused Pydantic/Preview/Durable/conversation/telemetry set: `192 passed in 7.35s`, exit `0` (recorded);
- independent review execution of the direct acceptance module: `7 passed in 2.12s`, exit `0`.

The direct module uses deterministic `FunctionModel` responses and disables model network access. It has no real provider or AWS access. The focused command could not be rerun in the final review shell because its available Python interpreter lacked `pytest`; that non-execution is not represented as a passing rerun. The recorded evidence remains hash-matched, but cannot close either P1 evidence gap above.

The review-specific disposable PostgreSQL instance at port `55439` was stopped successfully and the port was confirmed inactive. Its task-owned temporary data directory is retained, rather than deleted without explicit authorization.

## Compatibility and change-boundary review

No production source file is changed. Therefore this slice introduces no direct change to Legacy Runtime behavior, Domain authorization, Preview, Durable services, schemas, APIs, provider selection, protected Apply, AWS, or production configuration. The FunctionModel tests exercise the existing Pydantic integration without real DeepSeek calls.

## Verdict

No concrete production implementation vulnerability was found in the reviewed Stage 03A scope. Nevertheless, the acceptance package makes two claims its tests do not demonstrate. The Stage 03A review cannot be approved until those test/evidence corrections are made and independently rerun.

`INDEPENDENT POSTGRESQL TRANSACTIONS: NOT VERIFIED`
`DIRECT PYDANTIC INTEGRATION: PARTIAL`
`TWO-TURN DURABLE CONVERSATION: PASS`
`DURABLE OPTIONS PERSISTENCE: PASS`
`MID-RUNTIME MEMBERSHIP REVOCATION: BLOCKED`
`CROSS-TRIP ISOLATION: PASS`
`PLAN / FACTS REVISION FENCING: BLOCKED`
`TEST DETERMINISM: PASS`
`EVIDENCE INTEGRITY: BLOCKED`
`STAGE 03A REVIEW: BLOCKED`
`READY FOR LOCAL COMMIT: NO`
`READY FOR STAGE 03B: NO`
`PR #31 MERGED: NO`
`PRODUCTION CHANGE AUTHORIZATION: NOT GRANTED`
