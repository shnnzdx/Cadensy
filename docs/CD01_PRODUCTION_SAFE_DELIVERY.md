# CD-01 — Production-Safe Continuous Delivery

## Decision and scope

CD-01 turns the successful R5 manual image promotion into a repeatable release
path without changing AWS infrastructure, enabling an R5 feature flag, or
making a production deployment automatic. It is intentionally backend-only.

The currently recorded production baseline is:

| Item | Recorded value |
| --- | --- |
| ECS runtime task definition | `tripsync-backend:19` |
| Alembic revision | `c2a4b6d8e0f1` |
| R5 source image tag | `6daf90ed4f1130171ff6e84c11241f374dc5aeeb` |
| R5 runtime digest | `sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34` |
| Development membership-header bypass | `0` |
| R5 protected gates | all disabled |

The detailed manual evidence and the rollback constraint are retained in
[R5 manual deployment record](./R5_MANUAL_AWS_DEPLOYMENT_RECORD.md). CD-01
does not reinterpret that manual release as a deployment workflow run.

## Current workflow audit

| Workflow(s) | Status for CD-01 | Reason |
| --- | --- | --- |
| `build-validation.yml` | Reused as the required upstream gate | Its `CI Regression Gates` run validates Legacy, isolated Pydantic, production image, migration-runner image, and frontend contracts. CD-01 consumes only a successful **push to `main`** run. |
| `r5-manual-ecr-build.yml` | Historical release evidence; patterns reused, workflow left unchanged | It established immutable ECR tags, offline migration-runner validation, and account pinning for the R5 release. It is intentionally fixed to the R5 SHA, so it is not general CD. |
| `main.yml` | Not a release workflow | AWS identity verification only. |
| `database-readiness.yml`, `runtime-secrets-readiness.yml`, `backend-ai-secret-provision.yml`, `backend-ai-runtime-config.yml` | Not used for promotion | Readiness/configuration are not a schema compatibility proof; secret/configuration workflows are not a deployment approval boundary. |
| `phase5-backend-provision.yml`, `phase6-runtime-provision.yml`, `phase7-backend-runtime-config.yml` | Explicitly excluded | Phase 5 can provision/mutate network, IAM, ECR, logging, and ALB resources. Phase 6 contains the prohibited legacy `app.db.init_schema` path. Phase 7 hand-authors a task definition instead of preserving the live one. |
| `phase8-frontend-provision.yml`, `phase9-public-e2e.yml`, `phase10-https-custom-domain.yml`, `frontend-container-readiness.yml`, `frontend-hosting-proof.yml` | Out of backend CD scope | They concern frontend provisioning/hosting/public proof, not the backend release boundary. |
| `cloud-demo-purge.yml`, `cloud-fixed-accounts-upsert.yml` | Explicitly excluded | They can mutate cloud demo/account data and must never be used as deployment smoke tests. |

The reusable AWS properties are the existing ECR repository, ECS service,
roles, SSM secret references, network configuration, CloudWatch logging, ALB
target group, and ECS circuit breaker. CD-01 reads the current service task
definition and preserves those properties; it does not provision, replace, or
read secret values from any of them.

## Target delivery architecture

```text
push/merge to main
        |
CI Regression Gates succeeds on a push to main
        |
CD-01 build/publish (immutable ECR runtime + migration-runner images)
        |  [no ECS/RDS/Alembic write]
        +-- no verified staging environment: stop here
        |
manual dispatch + Main Environment approval
        |
read-only dedicated migration-runner preflight: current == approved head
        |
digest-pinned ECS task-definition revision -> ECS rolling deployment
        |
ECS stable + ALB target health + GET /api/health
```

The implementation is split deliberately:

- [`cd01-build-publish.yml`](../.github/workflows/cd01-build-publish.yml)
  starts only after a successful `CI Regression Gates` **push** run for `main`,
  or from a main-only controlled rebuild. It checks out the exact approved SHA,
  rejects an existing tag, builds two images, validates the migration runner
  offline, pushes immutable tags, resolves immutable digests, and ends. It has
  no ECS, RDS, Alembic mutation, or infrastructure-provisioning command.
- [`cd01-production-release.yml`](../.github/workflows/cd01-production-release.yml)
  is `workflow_dispatch` only and runs inside the existing `Main` GitHub
  Environment. It accepts SHA-256 image digests, confirms their ECR tags prove
  the same main commit provenance, requires the live service to already retain
  its circuit-breaker profile, and produces a new task definition only from the
  current one.
- [`prepare_ecs_task_definition.py`](../scripts/cd01/prepare_ecs_task_definition.py)
  is the offline safety boundary for that clone. It permits only the approved
  ECR repository with an `@sha256:` image, requires Fargate/`awsvpc`, both ECS
  roles, CloudWatch logs, and the existing `DATABASE_URL` SSM secret reference.
  It rejects a plaintext database URL and forces `APP_ENV=production`,
  `DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and every R5 protected
  gate to `0`.

### GitHub Environment requirement

The repository was read-only audited on 2026-10-13: the existing `Main`
Environment exists, but its `protection_rules` list is empty. It is **not** an
approval gate yet. CD-01 therefore has an initial job that calls the GitHub API
and fails before any AWS credential is configured unless `Main` contains a
`required_reviewers` protection rule. The production promotion job then targets
that same Environment, so the reviewer gate is checked both by the workflow
and by GitHub deployment protection.

Before CD-01 is enabled, a repository administrator must add at least one
required reviewer to **Main**, restrict deployment branches to `main` (or an
equivalent protected-branch policy), and ensure its AWS credentials remain
scoped to this repository. Do not move production credentials to an
unprotected repository secret. The existing environment may also make image
publishing wait for approval; that is a safe temporary posture, not a
production deployment.

### Staging decision

No workflow, service, or environment in the repository proves that an isolated
staging deployment exists. CD-01 therefore does not call production “staging”
and does not deploy automatically after an image build.

The minimum future staging proposal is a separately authorized, isolated ECS
service/task family, ALB target group/hostname, non-production database and SSM
parameter, `Staging` GitHub Environment, and non-production AWS IAM role. Only
after its health and smoke contract is recorded should the build workflow add a
staging deployment job. It must still pass the resulting digest—not a mutable
tag—to the manually approved production workflow.

## Schema and migration boundary

The production workflow runs a dedicated, one-off Fargate task using the
immutable migration-runner image before it changes the service. That task runs
only:

```text
alembic current
alembic heads
```

It proceeds only if both values are `c2a4b6d8e0f1`. It never invokes
`app.db.init_schema`, `alembic stamp`, `alembic upgrade`, or `alembic downgrade`.
If a migration is pending, the release fails before application promotion.

This is deliberate: an unknown future migration must not be generalized into
an implicit `upgrade head`. A future migration requires a separate reviewed
release change that provides all of the following before an explicitly approved
migration-runner operation can be added:

1. a fresh, named recovery snapshot in `available` state and an approved
   restore procedure;
2. exact current/head evidence and application/schema compatibility proof;
3. a dedicated immutable migration-runner digest and a command restricted to
   the reviewed migration chain;
4. task ARN, exit code, pre/post revision evidence, and post-migration data
   checks; and
5. forward-schema rollback analysis. Automated downgrade is never permitted.

The deployment workflow itself never creates RDS snapshots, runs database
migrations, or reads SSM/Secrets Manager values.

## Production promotion procedure

1. Confirm the intended commit's `CI Regression Gates` push run succeeded.
2. Let CD-01 build/publish finish and copy its runtime and migration-runner
   digest values. Do not use a tag or `latest`.
3. On `main`, dispatch **CD-01 — Manually Approved Production Backend Release**
   with that source SHA and both digest values.
4. Approve the pending `Main` Environment deployment only after checking the
   target SHA/digests and that no separate database migration is required.
5. The workflow verifies the existing task definition contract, executes the
   read-only migration-head preflight in the current private ECS network,
   registers a clone with only the digest/safe gate posture changed, and updates
   the existing ECS service with `100/200` capacity and circuit-breaker rollback.
6. It waits for service stability, requires every attached ALB target healthy,
   and runs `GET https://app.cadensy.top/api/health`. The resulting GitHub Step
   Summary records commit, both digests, migration status, prior/new task
   definition, ECS result, ALB/API health, gate posture, and rollback reference.

The smoke test is read-only. It intentionally does not create demo data, make
provider calls, enable feature gates, or execute an authenticated write path.

## Rollback and incident procedure

ECS circuit breaker rollback handles an unhealthy rollout before it stabilizes.
After a stable deployment, use the summary's **previous task-definition ARN**
as the application rollback reference only after checking that its image is
compatible with the still-forward database schema. Do not roll back the
database, delete ledger/revision data, restore over the live database, or call
an ECS force deployment as an automatic response. Capture service events, ALB
target health, the new/previous task definitions, and CloudWatch evidence first;
then use the separately authorized incident/recovery process.

## Offline verification

Run the CD-01 contract suite without AWS access:

```powershell
cd C:\Users\zdxzh\Desktop\capstone\New
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_cd01_delivery_safety.py -q
.\backend\.venv\Scripts\python.exe -m py_compile scripts\cd01\prepare_ecs_task_definition.py
git diff --check
```

The static tests assert digest-only promotion, main/CI gating, manual production
dispatch, `Main` environment use, immutable ECR provenance, circuit-breaker
deployment, ALB/API verification, default-off gates, and the absence of legacy
schema initialization or automatic Alembic mutation.
