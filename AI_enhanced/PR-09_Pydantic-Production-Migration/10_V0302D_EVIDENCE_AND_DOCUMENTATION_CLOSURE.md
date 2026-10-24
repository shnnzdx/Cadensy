# v0.3.0-02D Evidence and Documentation Closure

Date: 2026-10-23
Scope: local evidence, validation, and documentation closure only. No production
runtime, infrastructure, schema, Protected Apply, provider, or selector change was made.

## A. Original P1 blockers

02C identified two release blockers: the 11-scenario file was a test-name manifest
without independently reproducible execution evidence, and the three documentation
files required by `docs/AGENTS.md` had not been synchronized. Both are addressed
here. The original 02C report remains intact with a dated addendum; it is not
rewritten to hide the former blocked state.

## B. PostgreSQL stall diagnosis

The interrupted 02C command passed its first two non-database nodes and stalled
when it reached the first `db`-fixture node,
`test_pydantic_real_options_handler_keeps_a_named_conflict_off_unrelated_longest_item`.
The target worktree has neither root nor `backend/.env`. Test `conftest.py` therefore
fell back to `localhost:5432/tripsync_test`, while the temporary server used a
different port. No explicit `TEST_DATABASE_URL` was supplied and connection setup
had no matching short client timeout.

A narrow single-node rerun on an explicit new test URL passed in 1.82 seconds.
Later, the real lock-contention regression exposed a separate environment detail:
the fresh server initially listened only on IPv4 `127.0.0.1`, whereas the test URL
uses `localhost` and Windows tried IPv6 `::1` first. A bounded faulthandler trace
showed `psycopg.connection.connect` during fixture setup, not a Pydantic worker,
executor, deadline worker, transaction, or PostgreSQL lock wait. Restarting the same
task-owned server explicitly with `-h localhost -p 55436` bound both loopback
families. The eight root-lock cases then passed in 1.40 seconds.

No source defect, timeout increase, test weakening, or unrelated-process termination
was required. The prior test process was not waiting on a child process; the evidence
supports configuration/connection-family mismatch, not an implementation deadlock.

## C. Reproducible local test environment

- PostgreSQL 18 was initialized in a new task-owned disposable local Temp directory;
  the directory is not part of the repository.
- It listened only on loopback `localhost`, explicit port `55436`, verified by a
  server query, with disposable database `cadensy_v0302d_test`.
- `TEST_DATABASE_URL` and `DATABASE_URL` were explicitly set to that disposable
  database for each pytest process. `conftest.py` additionally forces test binding.
- `APP_ENV=test`, `MOCK_AI=1`, `DISABLE_SCHEDULER=1`, and
  `DEV_ALLOW_MEMBERSHIP_HEADER=0` were set. AWS and provider credential variables
  were removed from each child test process. FunctionModel/injected handlers were
  used; no real provider request occurred.

The exact sanitized environment, changed-source digests, commands, node mappings,
and exit codes are in
`09_V0302D_FUNCTIONAL_EVALUATION_EVIDENCE.json`.

## D. Eleven-scenario evidence

The dedicated scenario node set completed **14 passed in 2.03s, exit 0**. The extra
three cases come from parameterized fabricated-venue and clarification assertions;
the evaluation remains eleven logical scenarios. The new JSON artifact separates
test-source assertions from observed execution and records actual FunctionModel,
real-handler, Preview, Durable, or runtime-neutral attribution for every scenario.

Important limitation retained honestly: revoked-membership/stale-revision is now
execution-backed downstream Coordinator/Preview coverage, but is **not** represented
as Pydantic FunctionModel execution. It proves the runtime-neutral persistence and
revision boundary; no direct Pydantic attribution is claimed.

## E. Preview and Durable results

- Real scoped replacement -> immutable trusted evidence ->
  `RevisionBracketedPreview` passed in the scenario suite.
- Pydantic `FunctionModel` -> scoped `propose_options` -> Preview -> Durable
  Coordinator persistence passed in the scenario suite.
- The focused Pydantic/Preview/Durable/telemetry set passed **207** tests in
  **20.80s**, exit `0`.

Trusted Evidence, Preview's independent matching/classification, scoped grounding,
per-run guard/cache behavior, deadlines, and Legacy-default selection were not
weakened or changed during this closure.

## F. Full regression evidence

`pytest --collect-only -q backend/tests` collected **1337** cases, exit `0`.
The executable full regression used five deterministic alphabetically sorted,
non-overlapping 18-file partitions:

| Partition | Range | Result |
| --- | --- | --- |
| P0 | `test_ab_grade_partition.py` .. `test_ci_workflow_contract.py` | 328 passed, exit 0 |
| P1 | `test_db_safety.py` .. `test_live_provider_smoke_adapter.py` | 248 passed, exit 0 |
| P2 | `test_organizer.py` .. `test_pr06b2_conversation_api.py` | 206 passed, exit 0 |
| P3 | `test_pr06b2_conversation_concurrency_postgres.py` .. `test_r5_manual_ecr_build_workflow.py` | 260 passed, exit 0 |
| P4 | `test_runtime_composition.py` .. `test_upsert_fixed_accounts.py` | 295 passed, 10 existing Alembic warnings, exit 0 |

The current regression total is **1337 passed, 10 warnings**, as an explicitly
non-overlapping partition sum. It is not claimed to be one single pytest process.
The frozen evaluation files also completed as bounded groups: **35 passed**, exit `0`.
No frozen PR-04D3 input, grader, historical result, or baseline-gap finding changed.

## G. Frozen A/B baseline

`ambiguous-time` and `privacy-injection` remain the documented historical
Application/Product business baseline gaps. Their frozen safety outcomes remain
passing, and the current Pydantic implementation introduces no new frozen-runtime
regression. The formal/symmetric evaluation test group was executed without modifying
the frozen artifact or grader.

## H. Documentation synchronization

The repository-required documents are now synchronized and intentionally concise:

- `docs/PRODUCT.md` explains advisory trip facts, replacement suggestions and targeted
  compromises; no suggestion changes the Current Plan automatically, and local
  Pydantic behavior is not production-activated.
- `trip/BACKEND.md` records the runtime-neutral scoped-tool, Trusted Evidence,
  Preview/Domain, Durable option, guard/cache/deadline, Legacy-default, and unchanged
  Protected Apply boundaries.
- `HANDOFF.md` retains the verified Release B production record and adds the local
  v0.3.0-01/v0.3.0-02 migration state, PR-09 records, current acceptance boundary,
  and explicit prohibition on production activation or a next migration stage.

## I. Source and test corrections

No production or implementation source correction was needed. The only operational
correction was explicit test environment configuration: provide the disposable test
URL and bind the task-owned PostgreSQL process to both loopback families. No test
timeout was increased and no application behavior was changed.

## J. Remaining risks and PR readiness

The local test environment requires explicit `TEST_DATABASE_URL` when a worktree has
no `backend/.env`; future release commands should keep that requirement rather than
relying on the 5432 fallback. The recorded 1337 result is partitioned by design and
should remain described that way in review/PR materials.

The v0.3.0-02 implementation now has reproducible evidence, synchronized
documentation, passing focused/Preview/Durable/frozen checks, and a passing full
backend partitioned regression. It is ready for a new final acceptance review, not
for automatic production activation, push, merge, or commit before that review.

`v0.3.0-02D EVIDENCE CLOSURE: PASS`

`11-SCENARIO EXECUTION EVIDENCE: PASS`

`PYDANTIC PREVIEW INTEGRATION: PASS`

`DURABLE CONVERSATION INTEGRATION: PASS`

`TEST STALL ROOT CAUSE: CONFIRMED`

`DOCUMENTATION SYNC: PASS`

`SECURITY REVIEW: PASS`

`LEGACY REGRESSION: PASS`

`FULL BACKEND REGRESSION: PASS`

`READY FOR LOCAL COMMIT: YES`

`READY FOR PR REVIEW: YES`

`PRODUCTION CHANGE AUTHORIZATION: NOT GRANTED`
