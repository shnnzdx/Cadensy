# CD-01 — Production-Safe Continuous Delivery

## Decision and scope

CD-01 turns the successful R5 manual image promotion into a repeatable release
path without changing AWS infrastructure, enabling a protected feature flag,
or making a production deployment automatic. It is intentionally backend-only.
The normal path is now an explicitly **code-only Release A**; a separately
approved migration remains a different operation and is never inferred from a
code deployment.

CD-01 originated from the following historical R5 baseline. These values are
retained as provenance, not asserted as current live AWS state:

| Item | Recorded value |
| --- | --- |
| ECS runtime task definition | `tripsync-backend:19` |
| Alembic revision | `c2a4b6d8e0f1` |
| R5 source image tag | `6daf90ed4f1130171ff6e84c11241f374dc5aeeb` |
| R5 runtime digest | `sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34` |
| Development membership-header bypass | `0` |
| R5 protected gates | all disabled |

The current code-only release contract expects the separately completed PR-06B
forward schema at `b7d3a1e9c642` and proves it again with a read-only preflight.

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
select code_only or migration_bound evidence mode
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

The release workflow has two fail-closed evidence modes:

- `code_only` is the default. It requires the exact GitHub Actions run URL for
  the independent historical Gate M execution. The workflow verifies that the
  referenced run and its bounded migration/catalog-verification job succeeded
  on `main`, but does not claim that old Gate M ran for the new source SHA. The
  current source SHA still owns both immutable image digests, and its verifier
  image proves the live database is already at the approved head with the
  required catalog objects before ECS promotion.
- `migration_bound` preserves the original contract for a release whose exact
  source SHA has a successful, verified Gate M run. Its run ID, workflow SHA,
  ECS task, logs, task definition, and durable AWS evidence remain bound to that
  same source SHA.

Neither mode runs a migration inside the production release workflow.

### PR-06B artifact-build boundary

The build workflow validates the checked-out migration runner against the reviewed
PR-06B artifact head `b7d3a1e9c642`. This permits an immutable candidate image to
be built only after the normal `main` CI gate; it does not run Alembic against a
database and does not change ECS or RDS.

The Production Release workflow accepts only the reviewed PR-06B head
`b7d3a1e9c642`. `code_only` requires a live, read-only head and catalog check;
it does not rerun the completed historical migration or rewrite its Source SHA.
`migration_bound` retains the exact Gate M evidence verification for future use
when the release and approved migration are intentionally coupled. Do not
change the approved head merely to make a deployment pass.
- [`prepare_ecs_task_definition.py`](../scripts/cd01/prepare_ecs_task_definition.py)
  is the offline safety boundary for that clone. It permits only the approved
  ECR repository with an `@sha256:` image, requires Fargate/`awsvpc`, both ECS
  roles, CloudWatch logs, and the existing `DATABASE_URL` SSM secret reference.
  It rejects a plaintext database URL and forces `APP_ENV=production`,
  `DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and every R5 protected
  gate to `0`. Release A also forces `MOCK_AI=0`,
  `CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=0`,
  `CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED=0`, and
  `CADENSY_E2E_FAKE_RUNTIME=0`, and removes model-Provider credentials from both
  plaintext environment entries and ECS secret references. Provider credential
  aliases such as `*_TOKEN` are rejected for known model providers, and a name
  cannot be configured simultaneously through `environment` and `secrets`.
- [`validate_release_evidence.py`](../scripts/cd01/validate_release_evidence.py)
  executes the release-mode input contract offline. It requires immutable SHA
  and digest formats, an exact repository Gate M run URL, empty Gate M binding
  fields for `code_only`, and complete matching fields for `migration_bound`.

The same helper validates post-registration and rollback task definitions. A
new Release A definition must explicitly contain both Durable Runtime gates at
`0`. A previously validated rollback definition may omit only the newer
production activation latch, because absence is default-off in the old code;
if the latch exists it must be exactly `0`. The base Runtime gate, Mock/Fake
posture, credential absence, immutable image and independent rollback
cold-start/schema evidence remain mandatory.

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

Before changing the service, the production workflow runs a dedicated,
one-off Fargate task from the immutable verifier image built for the current
Source SHA. It calls only the read-only `verify_pr06b_schema.py` contract. The
verifier requires:

- `alembic_version == b7d3a1e9c642`;
- all five reviewed durable-conversation tables;
- the exact valid/ready/unique active-account index shape.

It never invokes `app.db.init_schema`, `alembic stamp`, `alembic upgrade`, or
`alembic downgrade`. A missing head or catalog object stops the release before
application promotion.

For `code_only`, the successful preflight plus an independently named
historical migration evidence reference proves that the schema already exists;
the reference is deliberately not compared to or described as the new Source
SHA. The release summary records `at-approved-head-no-schema-mutation`.

For `migration_bound`, the workflow additionally checks the exact successful
Gate M run and its durable evidence chain. The separate
`cd01-pr06b-schema-migration.yml` history and approval contract are unchanged.

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
2. Let CD-01 build/publish finish and copy its runtime and schema-verifier
   digest values. Do not use a tag or `latest`.
3. For a code-only change, select `code_only`, supply the exact successful
   historical Gate M GitHub Actions run URL, and leave the Gate M run/SHA/task
   inputs empty.
   Use `migration_bound` only when an exact approved Gate M run belongs to the
   same Source SHA.
4. On `main`, dispatch **CD-01 — Manually Approved Production Backend Release**
   with the reviewed Source SHA, both digests, evidence mode, rollback baseline,
   and rollback-validation evidence.
5. Approve the pending `Main` Environment deployment only after checking the
   target SHA/digests, mode, schema evidence and rollback evidence.
6. The workflow verifies the existing task definition contract, executes the
   read-only migration-head preflight in the current private ECS network,
   registers a clone with only the digest/safe gate posture changed, and updates
   the existing ECS service with `100/200` capacity and circuit-breaker rollback.
7. It waits for service stability, requires every attached ALB target healthy,
   and runs `GET https://app.cadensy.top/api/health`. The resulting GitHub Step
   Summary records commit, mode, both digests, independent schema evidence,
   read-only schema status, prior/new task definition, ECS result, ALB/API
   health, both Runtime gates, Provider-off posture, and rollback reference.

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

An older rollback Task Definition may predate
`CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED`. That single
missing variable is accepted only because the old application defaults it to
off, while the base Runtime gate is explicitly `0`, Provider credentials are
absent, and the baseline has independent cold-start, health, forward-schema and
runtime-off evidence. An explicit latch value other than `0`, a duplicate
entry, or an enabled base/Fake/Mock path rejects the baseline.

## Durable Runtime release boundary

- **Release A — code deployed, Runtime off:** both Durable Runtime gates are
  explicitly `0`; Fake Runtime is `0`; `MOCK_AI=0`; no model Provider
  credential is attached. Durable history remains readable, but production AI
  execution is not activated.
- **Release B — separately authorized activation:** only a later reviewed
  process may attach the DeepSeek secret reference and enable both Runtime
  permissions. The legacy Ollama-era configuration workflows are not the
  activation procedure.

The successful Stage 3B real DeepSeek run was isolated local evidence. It does
not prove AWS configuration, production credentials, ECS readiness or a
production activation.

## Offline verification

Run the CD-01 contract suite without AWS access:

```powershell
cd C:\Users\zdxzh\Desktop\capstone\New
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_cd01_delivery_safety.py -q
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_cd01_fullstack_release_tooling.py -q
.\backend\.venv\Scripts\python.exe -m py_compile scripts\cd01\prepare_ecs_task_definition.py scripts\cd01\validate_release_evidence.py
git diff --check
```

The static tests assert digest-only promotion, main/CI gating, manual production
dispatch, `Main` environment use, immutable ECR provenance, circuit-breaker
deployment, ALB/API verification, default-off gates, and the absence of legacy
schema initialization or automatic Alembic mutation. Negative contracts cover
a missing/enabled/duplicate production latch, Provider credential injection,
legacy rollback latch absence, explicitly enabled rollback latch, code-only
Source SHA/digest provenance, and independent schema evidence.
