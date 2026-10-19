# PR-06B Production Migration & CD-01 Compatibility

## Status

This is **Round 1 release engineering**, not production activation. It changes no AWS resource, RDS database, ECS service, Provider configuration, purge schedule, or runtime feature gate. Gate A remains **PASS**; Gate B remains **NOT APPROVED**.

The reviewed baseline was `main` at `75fe2eb109c16d9ccad51349bdbe186786b30e21` (PR #17). Local AWS CLI authentication returned `NoCredentials`; live AWS, ECS, RDS, backup and task-definition facts are therefore **UNVERIFIED**. No secret value was read.

## Confirmed source facts

| Topic | Fact |
| --- | --- |
| Previous release workflow | It accepted only `c2a4b6d8e0f1` and used the migration image only for a read-only schema check. It did not upgrade production. |
| Required lineage | `c2a4b6d8e0f1 -> 9db6f1a1041b -> 7a6c8e1d5f04 -> b7d3a1e9c642`. |
| Transaction/recovery | `9db…` is atomic/additive. `7a6…` uses `autocommit_block()` for concurrent index work and validates its replacement before legacy-index removal. `b7…` adds five Conversation tables transactionally. |
| Startup | `backend/Dockerfile` runs Uvicorn only; the runner default prints readiness only. Neither runs Alembic or `init_schema` implicitly. |
| Runtime | Durable runtime requires an explicit non-production setting; fake runtime rejects production. Round 1 now explicitly emits both settings as `0`. |
| Rollback | Data-bearing PR-06B downgrade guards exist. CD-01 never invokes Alembic downgrade or stamp. |

## Changed source

| File | Purpose |
| --- | --- |
| `.github/workflows/cd01-pr06b-schema-migration.yml` | New manual protected **Gate M** workflow; it cannot promote ECS service tasks. |
| `.github/workflows/cd01-production-release.yml` | **Gate R** requires successful Gate M evidence for the same SHA and a verified PR-06B catalog. |
| `scripts/cd01/prepare_ecs_task_definition.py` | Requires an HMAC secret reference; forces durable/fake runtime off. |
| `backend/Dockerfile.r5-migration-runner` | Packages runner/verifier scripts while retaining the safe default entrypoint. |
| `backend/scripts/cd01/run_pr06b_schema_migration.py` | Exact-start advisory-lock runner for `upgrade b7d3…`; no stamp, downgrade, or retry. |
| `backend/scripts/cd01/verify_pr06b_schema.py` | Read-only revision/table/index verifier. |
| `backend/tests/test_cd01_delivery_safety.py` | Gate M/R, evidence, capacity, HMAC-reference, and runtime-off regressions. |

## Gate M / Gate R design

### Gate M — production schema migration

Gate M is `workflow_dispatch` only, has a separate non-cancelling concurrency group, and requires `Main` Environment approval. It rejects execution unless: (1) caller is on `main`, types `EXECUTE_PR06B_SCHEMA_MIGRATION`, and reviewer protection exists; (2) named owner, backup/restore reference, and maintenance/interruption reference are present; (3) source SHA belongs to `origin/main`, runner digest/tag is immutable, and account/region match; (4) service is active with `desiredCount >= 1` and `runningCount >= 1`; (5) start revision is exactly `c2a4…`, `9db…`, or `7a6…` under the corresponding initial/recovery policy; and (6) the task retains `DATABASE_URL` and `CONVERSATION_SUBMISSION_HMAC_KEY` secret references, production app env, and disabled scheduler.

The runner inherits the existing task definition network placement and database-secret reference. It holds a PostgreSQL advisory lock, has a 900-second internal timeout and 35-minute job timeout, executes only `upgrade b7d3…`, then invokes a read-only catalog verifier. A task error, timeout, unexpected revision, missing table, or malformed index stops the run. It never calls `aws ecs update-service`.

### Gate R — ECS application promotion

Gate R remains manual and gets a distinct `Main` approval event. It requires a successful Gate M run ID; GitHub API evidence must show the same source SHA, `main`, workflow path, completed migration-job name, and job success. It independently checks ECR tags, account/region, nonzero service capacity, exact `b7d3…` catalog, circuit breaker, ALB, and API health. It retains the previous task definition/image reference and never runs a migration. Gate M does not trigger Gate R.

The task-definition helper forces `DEV_ALLOW_MEMBERSHIP_HEADER=0`, `CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=0`, `CADENSY_E2E_FAKE_RUNTIME=0`, disabled scheduler, and protected R5 gates `0`.

## Checkpoint/recovery contract

| Observed revision | Allowed action | Prohibited action |
| --- | --- | --- |
| `c2a4…` | New `initial` Gate M execution. | Promote application first. |
| `9db…` | New explicit `recovery` approval; restart-aware `7a…` then `b7…`. | Blind retry of old task or stamp. |
| `7a6…` | New explicit `recovery` approval; complete only `b7…`. | Downgrade to regain an old label. |
| Anything else | Stop, preserve evidence, investigate under DBA authority. | Promotion, automatic retry, stamp, destructive repair. |

`7a…` can recover an invalid interrupted target only while the valid legacy index remains. It creates/revalidates the exact target before legacy removal. This does not authorize a blind retry: every recovery is a new Gate M approval with its observed checkpoint.

## Compatibility and rollback

| Application | Database | Decision |
| --- | --- | --- |
| Old backend | Old schema | Existing baseline. |
| Old backend | New schema | Local smoke passed: pre-PR-06B source `d584750` created/read User, Trip, and Membership on fresh `b7d3…` schema (`status=invited`). This is ORM-write evidence, not production traffic/load proof. |
| New backend | New schema | Local migration rehearsal and PR-06B regression evidence pass; Gate R also requires live catalog verification. |
| Mixed ECS tasks | New schema | Allowed only after review accepts compatibility for the actual target old image. Otherwise require an independently approved controlled cutover; the circuit breaker is not DB compatibility evidence. |
| Rollback after durable rows | New schema | Application rollback is manual and needs forward-schema review. Never automatic DB downgrade; use containment or roll-forward. |

The schema is mainly additive, but it constrains membership status, changes active-only uniqueness after a validated concurrent-index cutover, and guards historical data against downward migration. ECS rollback is not database rollback.

## Local evidence

All test databases included `test` in their names and were dropped afterwards. Docker is unavailable locally; no local container validation is claimed.

| Validation | Result |
| --- | --- |
| `tests/test_cd01_delivery_safety.py -q` | **11 passed** — Gate M/R, evidence, capacity, HMAC reference, runtime-off contracts. |
| PR-06B PostgreSQL migration suites | **2 passed** — real migration/catalog/constraint checks. |
| `tests/test_runtime_composition.py tests/test_pr06b2_conversation_api.py` | **30 passed** — fake-runtime refusal and durable lifecycle regressions. |
| `cadensy_pr06b_round1_rehearsal_test` | `c2a4… -> b7d3…` through new runner; verifier passed; `ux_trip_membership_active_account:true:true`; DB removed. |
| `cadensy_pr06b_old_new_compat_test` | upgraded to `b7d3…`; old ORM write/read succeeded (`status=invited`); DB and temporary worktree removed. |
| YAML, compilation, `git diff --check` | Passed locally. |

## Runbook

Every mutation below is **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED IN THIS TASK**.

### Phase 1 — preflight

1. Validate AWS identity/account/region and scoped ECS, ECR, CloudWatch, and RDS access.
2. Under DBA authority, inventory target service/task definition and Alembic revision without querying user content.
3. Verify fresh recoverable backup/restore readiness, encrypted backup lifecycle, DB TLS, and database/HMAC secret references without reading values.
4. Record owner, maintenance policy, SHA, digests, and compatibility decision. Stop on mismatch or zero capacity.

### Phase 2 — schema migration / Gate M

1. Dispatch Gate M with observed revision, immutable source/digest, owner/evidence references, confirmation; complete human approval.
2. Observe bounded Fargate task and retain redacted evidence.
3. On success, retain revision, task ARN, digest, and Gate M run ID. On failure, do not retry automatically: preserve evidence, classify state, then request a new approved recovery run.

### Phase 3 — backend promotion / Gate R

1. Dispatch Gate R with SHA, runtime/migration digests, and Gate M run ID; complete a separate approval.
2. Confirm compatibility or controlled-cutover approval. Gate R preserves prior task reference and uses the existing `100/200` circuit-breaker strategy.
3. Verify ALB/API health and runtime-off task environment. Contain failures only after forward-schema review; never auto-downgrade DB.

### Phase 4 — observation and handoff

Record digests, task definitions, gate IDs, and redacted health evidence. Confirm no Provider or purge schedule was enabled. Hand Provider authorization, cost, timeout/recovery, monitoring, retention operations, encryption/TLS, and SLO work to Round 2 / Gate B.

## Remaining blockers and next boundary

Before live activation, an authorized operator must restore AWS access and collect live preflight facts. The production task must have the HMAC secret reference; backup/restore, RDS encryption, backup encryption/lifecycle, TLS, least privilege, live schema/capacity, and actual-image compatibility must be reviewed. Provider behavior/cost, durable runtime, purge ownership, telemetry, alerts, and incident SLOs are **Round 2 / Gate B** scope.

The PR/commit/hosted-CI record is appended after review submission. It must distinguish **Round 1 Release Engineering: COMPLETE** from **Production Migration and ECS Activation: NOT EXECUTED**.
