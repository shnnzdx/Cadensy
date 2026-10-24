# v0.3.0-03C-FIX — Committed PostgreSQL Fixture Isolation

Date: 2026-10-23

## Scope and outcome

This is a narrow test-fixture lifecycle repair on
`codex/v0303c-pydantic-formal-evaluation`, based on accepted Stage 03B HEAD
`ae030a9b1f0c2e765a5c925b128fcfaafc40f7ae`.  It changes only the two Stage
03 acceptance-test modules and adds Stage 03C evidence.  No production
application code, schema, migration, authorization, Runtime, selector, Legacy
behavior, Preview, Protected Apply, dependency, or CI workflow changed.

The historical reports `25_`, `26_`, and the BLOCKED independent review `27_`
remain unchanged.  They accurately record the original one-process failure.

## Original failure and leak attribution

The original fresh execution was:

```text
python -m pytest -q
exit 1: 1353 passed, 3 failed, 10 warnings
```

| Original node ID | Global-count tables | Failure observed before the fix |
| --- | --- | --- |
| `test_upsert_demo_login_keeps_legacy_account_without_recreating_demo_trip` | `trip`, `trip_membership` | expected no Trip; found a retained Trip |
| `test_upsert_demo_seed_is_idempotent` | `trip`, `plan`, `plan_item`, `trip_membership` | expected one Trip; observed 17 |
| `test_upsert_fixed_accounts_is_idempotent` | `user_account` | expected two Users; observed 34 |

The retained rows were attributed by IDs and names to the Stage 03 committed
fixture graph: 16 `Stage 03 trip` rows and 32 matching
`stage03-owner-*`/`stage03-other-*` users.  Stage 03A invokes its committed
seed graph nine times per full parameter expansion (A1, A2 ×2, A3, A4, A5 ×2,
A6 ×2); Stage 03B invokes the reused graph seven times (B1–B6, with B4 and B5
as separate scenarios).  Each root has one Trip and two Users, explaining the
observed 16/32 retained roots before the three Upsert tests.

The root was `_seed_committed_trip`: it deliberately used
`with factory.begin()` against the session-scoped `test_engine` so real
independent worker Sessions could observe committed state.  This bypassed the
ordinary rollback-only `db` fixture.  That committed visibility is essential
for the acceptance scenarios; the defect was the missing test-owned teardown.

## Test-owned lifecycle correction

`test_pydantic_durable_runtime_acceptance.py` now provides the function-scoped
`committed_stage03_seed_graph` fixture.  Its per-test registry is instance
owned, not module-global.  `seed()` records the exact IDs before its transaction
commits; a setup error before commit rolls back, and a successful commit is
always fixture-teardown-owned.

The teardown uses a fresh Session against the actual test Engine and deletes
only registered graph rows in verified foreign-key order:

```text
ConversationSubmissionRecovery
→ ConversationMessageOption
→ ConversationSubmission
→ ConversationMessage
→ Conversation
→ PlanItem → Plan
→ TripMembership → Trip → User
```

It never truncates, drops, recreates, or scans/deletes unrelated business rows.
All Stage 03 worker tests retain their bounded release/join `finally` blocks;
therefore workers are terminated before fixture teardown.  There is no cleanup
exception suppression: transaction errors and fresh-observer assertions fail
pytest teardown visibly.

The regression guard is the fixture's new independent observer.  After every
test, it asserts that every recorded Trip, Membership, User, Plan, PlanItem,
and Conversation is absent.  Because the child tables have real foreign keys,
any surviving option, submission, recovery, or message would prevent its
parent deletion and cause a visible teardown error.  This directly protects
against recurrence without relying on unrelated global-table counts.

## Preserved acceptance semantics

The tests still seed via independently committed Sessions.  No shared Session,
mocked commit, skipped case, xfail, weaker PID assertion, changed Upsert count,
ordering change, or partition-only substitution was used.  The existing
physical backend PID checks, deterministic barriers, observer reads,
revocation/revision fencing, recovery ownership, stale terminal fencing, and
timeout/late-result lifecycle assertions execute unchanged.

## Validation evidence

All executions used Python `3.13.5`, pytest `9.1.1`, pydantic-ai `2.54.0`,
loopback-only task-owned PostgreSQL 18 on port `55445`, a fresh explicitly
test-named database for each major gate, `APP_ENV=test`, `MOCK_AI=1`, scheduler
disabled, and membership-header override disabled.  Provider and AWS
credentials were removed from the child process; no real provider or AWS call
was made.  Database URLs and credentials are intentionally omitted.

| Validation | Actual result | Exit |
| --- | --- | ---: |
| Stage 03A + 03B + all three formerly failing Upsert modules in one process | 20 passed in 7.56s | 0 |
| Original unfiltered backend command: `python -m pytest -q` | 1356 passed, 10 warnings in 73.88s | 0 |
| Frozen `python -m evals.formal_ab_runner` | Legacy safety 8/8, business 6/8; Pydantic safety 8/8, business 6/8 | 0 |
| Pydantic/Preview/Telemetry focused suite | 115 passed in 4.50s | 0 |

The single-process full run collected and passed all 1356 tests, with zero
failures, skips, xfails, or deselections.  The 10 remaining warnings are the
pre-existing Alembic `path_separator` deprecation warnings from
`test_trip_bound_phase1_migration.py`.

## Frozen evaluation integrity and source identity

The frozen dataset canonical-LF SHA-256 remains
`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`.
The grader, formal runner, and symmetric harness hashes remain the values
recorded in `26_`.  The historical business gaps remain exactly
`ambiguous-time` and `privacy-injection`; matching 6/8 scores are not claimed
as new business parity.

The changed acceptance-test source hashes are intentionally different from
the Stage 03A/03B accepted hashes because they now own their committed graph:

| File | Canonical-LF SHA-256 after this fix |
| --- | --- |
| `backend/tests/test_pydantic_durable_runtime_acceptance.py` | `684d33afa360cf383dc385149bb3cefcaa20022624016a27203362e2d436b304` |
| `backend/tests/test_pydantic_concurrency_fencing_acceptance.py` | `8103db07d2f75449cbdf425bb1e1e85b1dbf570670b8cbd2a973b2ff3aed62a5` |

## Limitations and handoff

- This proves offline deterministic behavior only; real-provider quality,
  latency, and cost remain outside scope.
- Direct Legacy durable execution is still not claimed; Legacy is directly
  compared only in the frozen A/B harness.
- One clean CI-equivalent full run is recorded.  A second clean full run would
  add repeatability confidence but is not required for this narrow fix gate.

The temporary PostgreSQL process is stopped after validation; its task-owned
directory is retained.  No Git commit, push, PR action, merge, or production
activation was performed.

`ORIGINAL FULL SUITE: FAILED — 1353 passed, 3 failed, exit 1`

`POST-FIX FULL SUITE: PASS — 1356 passed, 0 failed, exit 0`
