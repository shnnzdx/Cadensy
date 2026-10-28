# R5 Manual AWS Deployment Record

**Record date:** 2026-10-13<br>
**Source commit:** `692b29a201e066d714dd70f5e9a59061d74b21ea` (merged PR #8)<br>
**Target:** AWS production (`us-east-1`) — controlled release stopped in preflight<br>
**Verdict:** **MANUAL R5 AWS BACKEND RELEASE — BLOCKED**

## Scope and non-actions

This is a pre-deployment audit only. No workflow was dispatched; no image, task
definition, ECS service, ECR repository, RDS instance, SSM parameter, secret,
gate, schema, or user data was changed. One short-lived ECS task executed only
`SELECT`/`information_schema` queries and exited `0`. No secret value was read.

The release requirement is that protected gates remain off, including
`CADENSY_B3_PROTECTED_PREVIEW_APPLY=0`, and that
`DEV_ALLOW_MEMBERSHIP_HEADER=0`; no protected writer gate may be truthy.
`APP_ENV=production` remains the intended runtime environment.

## Repository-configured release path

These are repository configuration values, not verified live-resource facts:

| Concern | Configured value / source |
| --- | --- |
| Region | GitHub Actions variable `AWS_REGION`; Phase 6 only provides a `us-east-1` fallback (`phase6-runtime-provision.yml:10,41,56`). |
| ECS | Cluster `tripsync-cluster`, service `tripsync-backend-service`, task family/container `tripsync-backend` (`phase5-backend-provision.yml:24-26`). |
| Image | ECR repository `tripsync-backend` (`phase5-backend-provision.yml:23`). |
| Health | ALB `tripsync-backend-alb`, target group `tripsync-backend-tg`, health path `/api/health` (`phase5-backend-provision.yml:27-28,236-253`). |
| Database | RDS PostgreSQL identifier `tripsync-postgres`, database `tripsync`, SecureString parameter `/tripsync/backend/prod/database-url` (`phase6-runtime-provision.yml:11-18`). |
| DB injection | ECS receives `DATABASE_URL` by SSM parameter ARN (`phase6-runtime-provision.yml:148-212`; `phase7-backend-runtime-config.yml:54-94`). |
| Rollback setting | Workflows request `100/200` healthy percentages and ECS circuit-breaker rollback; live settings are unverified. |

Historical Phase 5–7 GitHub Actions runs succeeded in August 2026. That does not
prove current account, region, task definition, image digest, database schema,
backup, health, or feature-gate state.

## Live read-only inventory

The AWS identity was verified in `us-east-1` using a process-local load of the
existing `backend/.env` AWS variables; no credential was printed or persisted.

| Concern | Observed live result |
| --- | --- |
| ECS service | `ACTIVE`, task definition revision `tripsync-backend:18`, desired/running/pending `1/1/0`, primary rollout `COMPLETED`. |
| Deployment safety | Rolling strategy, 100/200 healthy percentages, circuit breaker enabled with rollback, 60-second health grace period. |
| Runtime image | `tripsync-backend:33f8699e08f4c90ecc7baef4eb701acbb0cb8705`, pushed 2026-08-24. The R5 merge SHA has no ECR image tag. |
| Task runtime | `awsvpc`, 256 CPU, 512 MiB; `APP_ENV=production`, `DISABLE_SCHEDULER=1`, `MOCK_AI=0`. |
| Required gate posture | **Violation:** active task definition sets `DEV_ALLOW_MEMBERSHIP_HEADER=1`. No `CADENSY_B2C_*`, `CADENSY_B2D_*`, `CADENSY_B2E_*`, or B.3 environment entries were present; absent R5 flags default off in code. |
| DB injection | Task references secret names only, including `DATABASE_URL`; no value or reference value was read. SSM metadata confirms the configured parameter is a `SecureString`, version 1. |
| ALB target | One backend target reported `healthy` on port 8000. No stopped task was returned for the service. |
| RDS | `tripsync-postgres` is available, PostgreSQL 18.3, private, single-AZ, one-day retention, and unencrypted storage. Current automated snapshots are available, but restoration has not been tested. |

## AWS-access result

The first shell had no AWS variables, but the variables in `backend/.env` are
usable when deliberately loaded into a process-local shell. `sts` subsequently
succeeded. All AWS calls in this record were read-only except the short-lived,
read-only schema-probe task; neither Secrets Manager nor SSM parameter values
were retrieved.

## Migration preflight

Required chain:

```text
f9ff23b7b84d -> 54e8dd1ca624 -> 6f77e775d201 -> c2a4b6d8e0f1
```

The two R5 revisions are additive but are not safe to apply to an unknown
schema. `c2a4b6d8e0f1` refuses historical ledger rows whose
`resulting_plan_revision` is null or negative.

### Blocking findings

1. **Alembic history is absent.** The read-only ECS probe exited successfully
   and reported `alembic_table=false`, no revisions, and none of the R5 columns
   (`plan.revision`, `trip.planning_facts_revision`, or ledger scope/result
   columns). The database must undergo zero-drift baseline reconciliation before
   any explicit baseline stamp; a blind stamp is prohibited.
2. **Backup/restore remains unproven.** Automated snapshots exist and the
   instance has one-day retention, but there is no approved fresh pre-migration
   snapshot identifier or independently tested restore procedure.
3. **Production image lacks Alembic artifacts.** `backend/Dockerfile` copies
   only `requirements.txt`, `app/`, and `data/` (lines 11-15), not
   `alembic.ini` or `alembic/`.
4. **Phase 6 is prohibited as a migration method.** It runs
   `python -m app.db.init_schema` (`phase6-runtime-provision.yml:218-247`),
   while `init_schema.py` is explicitly a legacy `create_all()` compatibility
   helper, not Alembic.
5. **Phase 5 is not a narrow release workflow.** It can provision or mutate
   network, IAM, ECR lifecycle policy, logging, ALB and ECS resources before
   deployment (`phase5-backend-provision.yml:65-234`). Its impact on current
   resources cannot be assessed without live read access.
6. **Active runtime configuration violates the release gate.**
   `DEV_ALLOW_MEMBERSHIP_HEADER=1` is active, while this release requires `0`.
   The deployment must not proceed until a reviewed task-definition revision
   changes it to `0` and preserves the existing secret, role, network and log
   configuration. The protected R5 flags are absent, which is default-off, but
   this does not cure the invalid development-header setting.

## Required safe next action

The release operator already has a controlled read identity. Before deployment,
prepare and approve a narrow migration/release procedure that captures:

1. identity/region; ECS service and task definition; running/stopped tasks;
   target health; previous ECR digest; and recent CloudWatch error evidence;
2. RDS status/engine/network/backup evidence plus an approved fresh snapshot
   identifier and restoration procedure;
3. actual `alembic_version` and a non-destructive drift comparison. If Alembic
   history is absent, complete the reviewed zero-drift baseline reconciliation
   before any explicit stamp—never stamp an unknown schema;
4. an approved immutable migration-runner artifact containing `alembic.ini`,
   the full `alembic/` tree and dependencies. It must use private database
   connectivity and an SSM reference, never `init_schema` or plaintext DB URL;
5. recorded `alembic current` before/after `alembic upgrade head`, migration
   task ARN/exit status, and post-migration compatibility queries; and
6. a task-definition diff preserving roles, SSM reference, CPU/memory,
   `awsvpc`, logging, and gate-off values, plus the prior task-definition ARN
   and image digest for application rollback.

Database rollback must not delete Ledger or revision history. Do not
automatically downgrade schema; first prove old-application compatibility with
the forward schema.

## Deployment evidence

| Required record | Result |
| --- | --- |
| Previous image and task definition | Verified: August image SHA `33f869…8705`, task definition revision 18. |
| New image and task definition | Not created; R5 SHA is absent from ECR. |
| Schema revision before/after | Verified before: no Alembic history and no R5 columns / unchanged. |
| Backup and restoration evidence | Automated snapshots observed; restore proof absent. |
| Migration task or workflow run | Read-only probe exit 0; no migration or workflow run. |
| ECS and ALB | Service stable and ALB target healthy; production smoke tests not run. |
| Feature-gate status | R5 flags absent/default-off, but `DEV_ALLOW_MEMBERSHIP_HEADER=1` is a release blocker. |

## Final verdict

**MANUAL R5 AWS BACKEND RELEASE — BLOCKED**

The live schema has no Alembic history or R5 columns, the current image cannot
run Alembic, the existing Phase 6 workflow uses the prohibited legacy
initializer, the R5 image does not exist in ECR, and the active task definition
has `DEV_ALLOW_MEMBERSHIP_HEADER=1`. Stop before any image deployment, ECS
update, or schema change.

## Controlled release execution — 2026-10-13

The subsequently authorized bounded release was started and stopped during
**Step 1, before any production write**. No RDS snapshot, Alembic stamp or
upgrade, ECR push, task-definition registration, ECS service update, or
production feature-gate change occurred.

### Reconfirmed live state

| Check | Result |
| --- | --- |
| Release source | Local `main` is exactly `692b29a201e066d714dd70f5e9a59061d74b21ea`. |
| AWS identity / region | Authenticated release identity in `us-east-1`; credentials were loaded only into the invoking process and never recorded. |
| ECS | `tripsync-backend-service` remained `ACTIVE`, `1/1/0`, on `tripsync-backend:18`; its primary deployment was `COMPLETED`. |
| RDS | `tripsync-postgres` remained `available`, PostgreSQL `18.3`, private, with one-day retention. |
| Existing application posture | The task definition still has the old August image and `DEV_ALLOW_MEMBERSHIP_HEADER=1`; no protected R5 gate was enabled. |

### Baseline reconciliation evidence

Three fresh, explicitly named local PostgreSQL 18 isolation databases were
created solely for this preflight. The exact repository chain successfully ran
on them as `f9ff23b7b84d`, `54e8dd1ca624`, and `c2a4b6d8e0f1` (head). This
proves that the checked-out release's migration chain is internally executable;
it does **not** authorize applying it to production.

Three short-lived ECS tasks used the existing service task definition and its
existing SSM `DATABASE_URL` reference. They executed only read-only
`information_schema`/catalog queries with transaction-read-only, statement, and
lock timeouts. Each exited `0`. No database URL, secret value, or business row
was emitted.

The production schema has the 18 pre-guest-session base tables, no
`alembic_version`, no `guest_session`, no `mutation_idempotency`, and none of
the R5 revision columns. The table/column/type/nullability/constraint/FK/index
fingerprint matches the f9 candidate except for the following drift:

| Production difference | f9/54 repository baseline expectation | Why it blocks stamping |
| --- | --- | --- |
| `change_proposal.extended_at` exists as nullable `timestamptz`. | No Alembic revision in this chain creates it. | Unrepresented production schema change. |
| `trip.archived_at` exists as nullable `timestamptz`. | No Alembic revision in this chain creates it. | Unrepresented production schema change. |
| `plan.needs_refresh` has server default `false` and follows `created_at` physically. | No server default; it precedes `created_at` in the baseline. | Default semantics differ from the historical revision. |
| `plan_item.local_title` is physically appended after `created_at`. | It occurs after `title` in the baseline. | A non-semantic ordinal difference, but it confirms the schema was not produced exactly by the recorded revision. |

All other compared base-table columns, types, nullability, constraints,
foreign keys, and indexes matched the f9 candidate. `54e8dd1ca624` is ruled
out independently because production does not contain `guest_session` or its
indexes. Production has 37 Trips and 37 Plans, with **zero** Trips holding
duplicate Plans. There is no historical durable ledger table, so the Phase-1
ledger-row preflight has no rows to remediate.

### Release decision and shortest safe recovery

The repository does not contain an Alembic revision that explains the two extra
production columns or the `needs_refresh` default. Therefore production is
**neither** the exact `f9ff23b7b84d` baseline nor `54e8dd1ca624`; stamping
either revision would be a blind historical assertion and is prohibited.

The shortest safe recovery is a separately reviewed, additive Alembic
reconciliation plan that explicitly models or intentionally preserves those
legacy differences, followed by a fresh zero-drift comparison against that
approved history. Only then may this bounded release resume at the RDS snapshot
gate. The existing old application must remain deployed until that decision is
made. No destructive data repair, schema downgrade, or legacy initializer is
an acceptable substitute.

### Cleanup and non-actions

The three disposable local verification databases were removed after the
recorded preflight; they never contained production data. Production snapshots, the
production database, ECS service/task definitions, ECR images, SSM parameters,
and AWS networking/IAM resources were left unchanged.

## Legacy schema reconciliation — 2026-10-13

### Decision: Option A — reviewed legacy baseline compatibility

The four observed differences are semantically compatible with the f9 baseline
when, and only when, they are treated as the following explicit historical
allowlist:

| Difference | Historical source / compatibility conclusion |
| --- | --- |
| `trip.archived_at` nullable `timestamptz` | A retired cloud-demo helper added and used it for the removed archive feature. Current source has no reference to it. Production has zero non-null values, so preserving it is data-safe and does not revive an old behavior. |
| `change_proposal.extended_at` nullable `timestamptz` | The same retired helper added it. Current source has no runtime reference; the original Alembic baseline report already identified it as legacy drift. Production has zero non-null values. |
| `plan.needs_refresh` server default `false` | The legacy initializer installed this default. It matches the current ORM's Python default and preserves the documented false initial state. It remains in place; no cosmetic `ALTER` is allowed. |
| Physical ordinal of `plan_item.local_title` | No application SQL uses positional row access; ORM and raw SQL use named fields. Its type, nullability, data and constraints match f9. Column ordinal is therefore deliberately not compared. |

No other difference is accepted. The validator rejects an extra/missing table or
column, type/nullability/default mismatch, primary key, foreign key, unique or
check-constraint mismatch, or index mismatch. It also rejects a pre-existing
`alembic_version`, GuestSession, Ledger, or R5 revision object before the
first stamp.

### Implementation and isolated PostgreSQL 18 proof

The release source now includes `app.db.legacy_schema_compat`, a read-only
validator for the f9 semantic contract, and a separate
`Dockerfile.migration-runner` containing `alembic.ini`, the complete Alembic
tree, app modules and pinned PostgreSQL dependencies. The normal HTTP image is
unchanged. CI includes an offline build proof for this one-off runner; it does
not deploy anything.

On isolated local PostgreSQL 18 database
`cadensy_r5_legacy_compat_20261013`, the exact production-like legacy shape
was recreated with both nullable extra columns, `needs_refresh DEFAULT false`,
no Alembic history, no GuestSession, no Ledger and representative non-sensitive
rows. The validator passed. The reviewed sequence then passed:

```text
validate legacy compatibility
alembic stamp f9ff23b7b84d
alembic upgrade head
alembic current -> c2a4b6d8e0f1 (head)
```

The sentinel account/trip/plan rows remained present; the archived timestamp,
both legacy columns and `needs_refresh DEFAULT false` remained intact; the
Plan/Trip R5 revision columns and `mutation_idempotency` table were added.
Current ORM reads of the upgraded sentinel data passed. Focused static tests
passed (`15 passed`), and representative Legacy auth/trip tests passed (`62
passed`). The local isolation database is retained only until this reconciliation
source has completed its separate Hosted-CI gate, then will be removed.

### Fresh production read-only confirmation

A subsequent short-lived read-only ECS probe exited `0` and reconfirmed the
18 base tables, zero duplicate Plan Trips, no Alembic/GuestSession/Ledger/R5
objects, `needs_refresh` default `false`, and `local_title` ordinal 21. Both
legacy extra columns currently have zero non-null rows. No production data or
schema was changed by this confirmation.

### Source-integrity gate before release resumption

Because the strict validator and migration-runner image are new source, this
is not eligible to ship from the old R5 SHA. A separate PR with Hosted CI must
merge first; its resulting `main` SHA becomes the only candidate release
source. Until that gate passes, **no RDS snapshot, stamp, migration, ECR push,
or ECS update may occur**.

## Final controlled R5 release execution — 2026-10-13

This section records the bounded production execution authorized after the
source-integrity and immutable-image gates passed. It preserves the earlier
blocked audit history above; it does not reinterpret any previous preflight as
a completed release.

### Immutable inputs and fresh recovery point

| Item | Verified value |
| --- | --- |
| AWS account / region | `448678332746` / `us-east-1` |
| Application source | `6daf90ed4f1130171ff6e84c11241f374dc5aeeb` |
| Build tooling source | `fab78418f30dd20e55d4bab38c67eeddc1e4f026` |
| Runtime image | `tripsync-backend@sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34` |
| Migration runner | `tripsync-backend@sha256:5117981f94c71893578ece57489914989058bc3572c6827a924e4060510973b0` |
| Fresh manual RDS snapshot | `tripsync-postgres-r5-20261014-020428` — `available` |

The snapshot is a manual PostgreSQL snapshot of `tripsync-postgres` in the
existing VPC. IAM policy simulation confirmed that the release identity is
allowed to restore it to a separate instance, and the existing private DB
subnet group has active subnets in `us-east-1a` and `us-east-1b`. The ECS
service subnets are in the same VPC. No restore drill was performed and no
restore-over-live action is authorized by this record.

### Alembic migration evidence

A temporary, non-service task definition
`tripsync-backend-r5-migration:1` ran the immutable migration-runner image in
the existing ECS-to-private-RDS database network. It reused the existing task and
execution roles, runtime secret reference, networking, and CloudWatch log
configuration; its command was explicit rather than the image's safe default.

| Item | Result |
| --- | --- |
| ECS task | `arn:aws:ecs:us-east-1:448678332746:task/tripsync-cluster/ed73d6ef9bd7497ebe454a1280dc2e80` |
| Exit code | `0` |
| Pre-stamp validator | Passed the strict Option A profile only: `trip.archived_at`, `change_proposal.extended_at`, and `plan.needs_refresh DEFAULT false` |
| Stamp | `f9ff23b7b84d` |
| Upgrade chain | `f9ff23b7b84d -> 54e8dd1ca624 -> 6f77e775d201 -> c2a4b6d8e0f1` |
| Final Alembic head | `c2a4b6d8e0f1` |
| Post-migration data check | 37 Trips, 37 Plans, zero duplicate Plan Trips, both approved legacy columns preserved |
| R5 structure check | `guest_session`, `mutation_idempotency`, Plan revision, Trip planning-facts revision, and required check constraints verified |

The task never ran `app.db.init_schema`, did not perform a schema downgrade,
and did not expose a database URL or any secret value in release evidence.

### ECS promotion and smoke evidence

The previous live revision was `tripsync-backend:18`, using image digest
`sha256:9ad324866bc7906c54a5e00478a0164591ad8aaff46c3fb648a4262490588057`.
The existing service alone was updated to `tripsync-backend:19`; its task,
execution roles, Fargate sizing, `awsvpc` network configuration, SSM secret
references, container port, and CloudWatch configuration were preserved.

Only the approved runtime image and safety posture changed:

- Runtime image is the immutable R5 digest listed above.
- `APP_ENV=production` remains set.
- `DEV_ALLOW_MEMBERSHIP_HEADER=0`.
- All 15 protected B.2C/B.2D/B.2E writer gates plus
  `CADENSY_B3_PROTECTED_PREVIEW_APPLY` are explicitly `0`.

The completed service reports desired/running/pending `1/1/0`; its sole
running task uses revision `:19` and the requested runtime digest. The existing
deployment circuit breaker and rollback setting were retained. The sole ALB
target is `healthy`.

Production smoke evidence, with no real Trip mutation:

| Check | Result |
| --- | --- |
| `GET /api/health` | `200` with `ok: true` |
| Read-only nonexistent-invite probe | `404`, proving runtime database access without creating data |
| Request with forged membership/trip headers and no bearer credential | `401`, confirming the retired header bypass is unavailable |
| Fixed organizer account login, authenticated account read, and immediate logout | `200` / `200` / `200`; the ephemeral token was not persisted or recorded |
| CloudWatch scan after promotion | zero `ERROR`, `Traceback`, and `CRITICAL` events in `/ecs/tripsync-backend` |

### Rollback and remaining infrastructure risks

The pre-release manual snapshot is available and a separate-instance restore is
technically permitted. The previous ECS task definition `:18` remains
registered, but the database is now forward-migrated: do not revert the
application task definition unless the older image's compatibility with the
forward schema has first been assessed. No automated schema downgrade or data
deletion is an approved rollback action.

Known infrastructure risks remain unchanged: the production RDS instance is
single-AZ, unencrypted, and has one-day automated-retention; a full restore
drill has not been performed. Protected R5 behavior remains disabled in
production pending separate authorization.

## Final verdict

**MANUAL R5 AWS BACKEND RELEASE — PASS**

The bounded RDS snapshot, allowlisted Alembic baseline/upgrade, immutable ECS
runtime promotion, health checks, authentication smoke, and default-off feature
gate verification all have live AWS evidence above.
