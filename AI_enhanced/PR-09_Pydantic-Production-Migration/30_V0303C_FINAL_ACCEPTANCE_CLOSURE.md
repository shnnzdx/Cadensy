# v0.3.0-03C — Final Acceptance Closure and CI Recovery Verification

Date: 2026-10-24

## Final verdict

**PASS — Stage 03C is accepted for local offline migration evaluation.**

The original single-process failure was real, reproduced, and preserved in
the historical record. The committed-fixture isolation correction removes only
test-owned PostgreSQL graphs and the restored CI-equivalent command passes on a
fresh task-owned database. No production application code has changed. This
approval authorizes exactly one local Stage 03C commit; it does not authorize a
push, PR action, merge, deployment, or Pydantic production activation.

## 1. Reviewed source and worktree identity

| Item | Verified value |
| --- | --- |
| Worktree | `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303c-pydantic-formal-evaluation` |
| Branch | `codex/v0303c-pydantic-formal-evaluation` |
| Pre-commit HEAD / planned parent | `ae030a9b1f0c2e765a5c925b128fcfaafc40f7ae` |
| Stage 03A ancestor | `b1981d849e24e9c88a4ccf997c8831addbd4a48b` |
| Stage 02 ancestor | `8012dce80d5d8ecd30906192311d685ed1d9e13f` |
| Production source/schema/configuration/CI changes | None |

Stage 03A and Stage 03B commits are unchanged. The reviewed pre-commit
manifest contains only three test files and Stage 03C artifacts `25_` through
`30_`; no environment file, credential, database data, cache, generated test
artifact, dependency, production application file, workflow, or migration is
in scope.

## 2. Historical evidence chronology

The record is intentionally chronological, not retrospectively rewritten:

1. `25_`/`26_` record the original formal offline Legacy/Pydantic evaluation.
2. `27_` independently reproduced `1353 passed, 3 failed, exit 1` and correctly
   BLOCKED acceptance.
3. It attributed the failure to 16 committed Stage 03 Trip roots and 32 Users
   retained in the session-scoped test database.
4. `28_`/`29_` document the narrow function-scoped cleanup and the first
   post-fix full-suite pass.
5. This report independently reran the original complete command on fresh
   PostgreSQL against the final candidate and records the result below.

## 3. Committed PostgreSQL fixture cleanup review

The Stage 03A/03B fixture remains committed and independently visible, as
required by real concurrency tests. `committed_stage03_seed_graph` owns each
function's exact root IDs before the setup transaction commits: Trip, two
Memberships, two Users, Plan, and PlanItem. A pre-commit setup failure rolls
back; a successful commit is registered for teardown.

Teardown is function-scoped and deletes only registered identities in valid
foreign-key order:

```text
ConversationSubmissionRecovery
→ ConversationMessageOption
→ ConversationSubmission
→ ConversationMessage
→ Conversation
→ PlanItem → Plan → TripMembership → Trip → User
```

No table-wide deletion, `TRUNCATE`, schema recreation, global autouse cleanup,
shared-Session substitution, mocked commit, skip, xfail, assertion weakening,
or ordering workaround is used. Existing worker `finally` blocks release and
join workers before teardown. Cleanup exceptions are not suppressed. A fresh
SQLAlchemy observer Session queries the exact registered Conversation, PlanItem,
Plan, Membership, Trip, and User IDs for absence; it is not an ORM identity-map
check. Foreign keys ensure a surviving registered child blocks parent deletion
and becomes a visible pytest teardown failure.

The physical PostgreSQL PID checks, committed mutation visibility,
revocation/revision fences, recovery ownership, stale terminal fencing, timeout
handling, and typed telemetry assertions remain intact and ran in the full
suite.

## 4. Original three failures: resolved without changed assertions

| Historical node ID | Leaked rows affecting it | Resolution |
| --- | --- | --- |
| `test_upsert_demo_login_keeps_legacy_account_without_recreating_demo_trip` | `trip`, `trip_membership` | teardown removes only registered Trip/Membership rows |
| `test_upsert_demo_seed_is_idempotent` | `trip`, `plan`, `plan_item`, `trip_membership` | teardown removes the registered plan graph before the global-count test |
| `test_upsert_fixed_accounts_is_idempotent` | `user_account` | teardown removes both fixture users |

Their global-count assertions are unchanged. The Stage 03A + Stage 03B + all
three Upsert modules sequence passed 20/20 in one pytest process after the
correction, and the final unfiltered complete suite passed all 1356 tests.

## 5. Independent complete backend regression

The final independent command used the repository test interpreter:

```text
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-pydantic-poc\Scripts\python.exe
Python 3.13.5; pytest 9.1.1; pydantic-ai 2.54.0
Working directory: backend
Command: python -m pytest -q
Collection: 1356 tests
Result: 1356 passed, 0 failed, 0 skipped, 0 xfailed, 0 deselected
Warnings: 10 pre-existing Alembic path_separator deprecation warnings
Exit: 0
Elapsed: 75.25s
```

It used a new loopback-only task-owned PostgreSQL 18 database on port `55445`,
with a verified test-only name; test mode, mock AI, scheduler-off, and
membership-header override-off were explicit. No DeepSeek, AWS, or production
database operation occurred.

During final independent review, two earlier fresh complete runs exposed a
pre-Stage-02 lifecycle test race. Its 10ms request deadline measured cold
ThreadPool/event-loop startup rather than cancellation semantics. The affected
test and execution boundary predate Stage 02; no Stage 03 fixture graph was
involved. The test-only correction raises that budget to 100ms while retaining
the cancellation and two lifecycle-event assertions. It passed 20/20 fresh
pytest processes, and the final unfiltered suite passed. This is test-stability
work, not a production behavior change or migration-introduced P0/P1 defect.

## 6. Frozen A/B and Stage 03A/03B integrity

The final formal runner executed the frozen eight cases per Runtime. Dataset
canonical-LF SHA-256 remains
`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f` and
grader `chat-ab-grader-v1` is unchanged.

| Runtime | Safety | Business | Historical gaps |
| --- | ---: | ---: | --- |
| Legacy | 8/8 | 6/8 | `ambiguous-time`, `privacy-injection` |
| Pydantic | 8/8 | 6/8 | `ambiguous-time`, `privacy-injection` |

Matching totals are not treated as business-quality improvement.
`ambiguous-time` remains safe degradation rather than the frozen clarification;
`privacy-injection` remains safe clarification rather than `reply_only`.
Direct Legacy durable execution is still not claimed. The complete suite covers
Stage 03A continuity/options/staleness/revocation/cross-trip/revision fencing
and Stage 03B idempotency/concurrency/recovery/observer, stale completion and
failure, timeout, late-result discard, and typed telemetry.

## 7. Source and evidence integrity

All JSON artifacts parse and documented canonical-LF hashes were recomputed.
No raw prompts, private constraints, database passwords, API keys,
authorization tokens, provider payloads, or credential-bearing URLs appear in
the Stage 03C artifacts.

| Final source | Canonical-LF SHA-256 |
| --- | --- |
| `backend/tests/test_pydantic_durable_runtime_acceptance.py` | `684d33afa360cf383dc385149bb3cefcaa20022624016a27203362e2d436b304` |
| `backend/tests/test_pydantic_concurrency_fencing_acceptance.py` | `8103db07d2f75449cbdf425bb1e1e85b1dbf570670b8cbd2a973b2ff3aed62a5` |
| `backend/tests/test_agent_execution_lifecycle.py` | `82640c2e423e77b880c41b297698567dce1360ded57662dcc2e0a6804b5e148e` |
| Frozen dataset | `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f` |
| `backend/evals/graders.py` | `afdfda3266e86f61dd3289c911e82569414384940fd44a874f38f6985cd96be3` |
| `backend/evals/formal_ab_runner.py` | `abb5188aa03249416c8b02455148ab6d1070350efe3357a189cd10365da7afed` |
| `backend/evals/symmetric_harness.py` | `232f35e0f90783b6b5d676b96835ffa769cd1ceb3e7ee15ecaa8c292b85ca737` |

## 8. Residual limitations and integration readiness

- Offline deterministic validation does not evaluate real-provider quality,
  cost, or latency.
- Production Pydantic activation, runtime selection, deployment, PR merge, and
  protected-branch approval remain separate authority boundaries.
- The task-owned PostgreSQL process is stopped after review; its directory is
  retained for traceability.

The local source and evidence are ready for ordinary protected-branch GitHub
review after the gated commit. No GitHub mutation is authorized here.

`STAGE 03C FINAL ACCEPTANCE: PASS`

`LOCAL COMMIT: PENDING GATED COMMIT`
