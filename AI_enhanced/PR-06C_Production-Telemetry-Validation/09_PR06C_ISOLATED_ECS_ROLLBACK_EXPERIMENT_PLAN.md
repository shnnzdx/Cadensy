# PR-06C.1C — Isolated ECS Rollback Experiment Plan

**Status:** **DESIGN ACCEPTED — PRODUCTION RECOVERY BLOCKED**
**Scope:** execution plan and pure offline evidence contract only.
**Authorization:** no AWS resource has been created, inspected, changed, or
deleted by this work. This document is not an execution authorization.

## Objective and safety assertion

The experiment will determine the actual ECS behavior when a service at
`desiredCount=0` receives one combined update that changes its task definition
and sets desired count to `1`. It will exercise two deterministic candidate
failures:

1. the candidate never reaches `RUNNING`; and
2. the candidate reaches `RUNNING`, but its ALB health check is unhealthy.

For each case, the evidence record must preserve the source and target service
revision, both task-definition ARNs, deployment/service events, task state and
actual container image digest, desired/running/pending counts, ALB target
health, and an explicit finding on whether the old image ever became RUNNING.

The pre-approved safe result is narrow: after each failure and rollback,
desired/running/pending must all be zero and the old image must not restart.
Any other result is a valid experimental observation but a **negative recovery
safety result**. It stops the experiment and cannot support an `ACCEPTED`
production Manifest without a new threat-model and approval decision.

ECS documents that the circuit breaker can roll back a failed rolling deployment
to the last completed service revision, and that service deployments/revisions
are observable. It does not make this isolated experiment redundant: the exact
combined zero-to-one outcome is the fact being measured. [ECS circuit-breaker
failure detection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html)
and [ECS service deployment history](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-deployment.html)
describe the control-plane evidence to capture.

## Required isolated boundary

The experiment must use a **dedicated non-production AWS account**, distinct
from production account `448678332746`, in `us-east-1`. A separate account is
mandatory: a different cluster, namespace, tag, or security group in the
production account is insufficient isolation.

| Layer | Required boundary |
| --- | --- |
| ECS / ECR | Dedicated cluster, service, task-definition family, internal ALB/target group, log group, and sandbox ECR repository. The legacy `:19` image may only be copied by immutable digest into that repository; the experiment task definition must never reference a production secret. |
| Database | Dedicated non-production PostgreSQL instance/database at Alembic `c2a4b6d8e0f1`, with empty synthetic fixtures only. It must not be a production snapshot, replica, restore, network peer, or shared credentials target. |
| Network | Dedicated VPC/private subnets. Fargate uses `awsvpc`; the ALB is internal; the RDS security group permits port 5432 only from the experiment task security group. No public listener, public database endpoint, VPC peering, Transit Gateway attachment, or production route is permitted. |
| Egress | No NAT/Internet egress from task subnets. Create only the required VPC endpoints (ECR API/DKR, S3 gateway, CloudWatch Logs, and Secrets Manager if used) after approval. This both prevents paid-provider calls and restricts task traffic to required AWS services. |
| Data / AI | `MOCK_AI=1`; no provider API key, no production secret, no user data, and no production telemetry marker. The task role has no provider or data-plane permissions. |
| Audit | Dedicated versioned, SSE-KMS-encrypted evidence bucket. Evidence-reader access is read-only; writers can append but cannot overwrite or delete captured objects. |

The VPC/subnet/load-balancer detail is an implementation prerequisite, not a
claim that this routing exists. The repository's available networking skill
does not implement VPC/ALB foundations; that infrastructure must receive its
own reviewed change plan before execution.

## Resources and least-privilege roles

All resources must carry `CadensyExperiment=PR-06C.1C`, an expiry timestamp,
and the approved change ID. A permission boundary and SCP/account policy must
deny production account ARNs and deny untagged resource creation.

| Principal | Permitted purpose | Explicit exclusions |
| --- | --- | --- |
| `PR06CExperimentProvisioner` | Create/delete only tagged sandbox VPC/ECS/ALB/RDS/ECR/log/evidence resources; create the approved budget/cleanup controls. Short-lived MFA-backed role only. | No production account, IAM administration, secret reads, production ECR write, or broad `iam:PassRole`. |
| `PR06CExperimentOperator` | Read sandbox resources; register approved sandbox task definitions; perform the two approved combined service updates; collect evidence; invoke the approved cleanup workflow. | No production ARN, no arbitrary scaling, no database migration, no manual rollback/retry, no IAM/secret mutation. |
| ECS execution role | Pull only named sandbox ECR digests, write only named log group, read only the named sandbox database secret at task launch. | No production ECR/Secrets, no wildcard data services. |
| ECS task role | Empty unless a bounded runtime permission is demonstrated necessary. | No AWS administrative actions, no AI provider credentials, no database-secret read. |
| `PR06CEvidenceReader` | Read versioned evidence objects and CloudWatch log exports for independent review. | No write/delete, no production logs/secrets. |

`iam:PassRole` must name only the two approved ECS roles and use an ECS service
condition. IAM review must verify the final policies against the actual planned
actions; no `FullAccess`, `Action: *`, or resource `*` policy may be substituted.

## Deterministic experiment matrix

The source service starts with the sandbox copy of the old `tripsync-backend:19`
image at desired/running/pending `0/0/0` and a completed source revision. It
uses a dedicated, disposable database and disabled provider configuration. The
candidate images are sandbox-only fault images with immutable digests; they do
not make a paid model request.

| Case | Candidate fault | Required observation | Pass result |
| --- | --- | --- | --- |
| `startup_failure` | Entrypoint exits before binding the application port. | Candidate stopped-task reason; no candidate `RUNNING` task; empty ALB target-health response is recorded; service deployment/revision state and counts are captured. | Circuit breaker rollback reaches its terminal state; final `0/0/0`; old digest never runs. |
| `alb_health_failure` | Candidate binds the configured port but returns an unhealthy response on the sandbox ALB health path. | Candidate reaches `RUNNING`; `DescribeTargetHealth` records `unhealthy`; deployment/revision state, task digest, and counts are captured. | Circuit breaker rollback reaches its terminal state; final `0/0/0`; old digest never runs. |

For each case, the operator issues exactly one approved `UpdateService` request
containing both the candidate task-definition ARN and desired count `1`. The
service must use the ECS rolling deployment controller and circuit-breaker
rollback. No manual rollback, retry, second update, force deployment, or direct
capacity correction is permitted while evidence is being collected.

The deployment circuit breaker has distinct startup and health-check detection
stages; ALB health is one of the evaluated health sources once a task reaches
`RUNNING`. [AWS's circuit-breaker behavior](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html)
supports this two-case design.

## Old-runtime forward-schema compatibility plan

This is a separate mandatory gate, not an inference from rollback behavior.

1. Build a dedicated empty PostgreSQL database at revision `c2a4b6d8e0f1` using
   reviewed migration tooling in the isolated account. Do not clone, restore,
   replicate, or connect to production data.
2. Create an experiment-only database principal limited to `CONNECT`, schema
   `USAGE`, and `SELECT`; deny schema DDL and DML. Capture the resulting grants
   without recording credentials.
3. Run the exact immutable legacy `tripsync-backend:19` image digest in a
   one-off isolated verification task against that database. The task may run
   read-only schema/application compatibility checks and a read-only health
   probe; it must not run Alembic upgrade/downgrade, seed, or application writes.
4. Record legacy image digest, target revision, command hash, task exit status,
   read-only database principal, and redacted query/driver evidence. Any denied
   write attempt, schema mismatch, startup incompatibility, or unexplained
   database error is `INCOMPATIBLE` and stops the program.
5. The database owner independently reviews the evidence. A parser pass or a
   health response alone is never a forward-schema compatibility conclusion.

## Evidence package and offline gate

`scripts/pr06c_isolated_rollback_experiment_contract.py` accepts only a
completed evidence package that proves the designed envelope: dedicated account,
synthetic data, disabled paid provider, armed budget, completed cleanup,
read-only schema check, both failure cases, zero final counts, no old-image
restart, and three human approvals. Its companion test is
`backend/tests/test_pr06c_isolated_rollback_experiment.py`.

For each actual case, capture these immutable, redacted artifacts into the
versioned evidence bucket:

- `DescribeServices`, `ListServiceDeployments` / `DescribeServiceDeployments`,
  service revision references, task-definition JSON, and service events;
- `ListTasks` / `DescribeTasks`, including task-definition ARN, task status,
  stopped reason, and per-container image digest;
- `DescribeTargetHealth` output, including the empty result for startup failure;
- relevant CloudWatch log excerpts with no secrets, raw request data, tokens,
  or user data; and
- the database compatibility command, exit code, read-only grants, cleanup
  result, and approval references.

The validator only verifies schema, value shape, and declared safety results.
It cannot prove an AWS experiment occurred or that an S3 object is truthful.
Independent reviewers must inspect the referenced artifacts, account identity,
task image digest, and timestamps before any Manifest change is considered.

## Cost, duration, cleanup, and stop conditions

This is a hard authorization boundary, not a price estimate:

- maximum experiment duration: **180 minutes** from first resource creation;
- maximum total experiment spend: **USD 25**, subject to cost-owner approval;
- an AWS Budget alarm is required, but is not an immediate kill switch;
- a separately scheduled cleanup watchdog must stop the service and delete only
  resources bearing the exact experiment tag at the expiry time; and
- an on-call experiment owner must stop the run immediately on a budget alarm,
  unexpected public exposure, denied isolation control, unexpected old-image
  start, any production ARN/secret/data access, or any task/provider behavior
  outside the matrix.

Before authorization, the cost owner must obtain current regional pricing and
confirm that the approved resource sizes, endpoint choice, storage, logs, and
the time cap fit below USD 25. No estimate in this plan replaces that check.
After cleanup, the operator captures tagged-resource inventory proving no ECS
service/tasks, ALB/target group, RDS instance, ECR image, log group, endpoint,
or temporary secret remains. The evidence bucket may remain only under the
approved retention policy.

## Manifest promotion criteria

The current `08_PR06C_RECOVERY_SAFETY_ACCEPTANCE_MANIFEST.json` remains
`PENDING`. This plan neither edits it nor authorizes an edit.

It may be proposed for `ACCEPTED` only after all of the following are true:

1. both experiment cases meet the narrow safe result and have independently
   reviewed, versioned evidence;
2. the old-runtime forward-schema check is `COMPATIBLE`, reviewed by the
   database owner, and tied to the exact old image digest and revision;
3. experiment reviewer, database owner, and cost owner approvals are recorded
   separately and are current;
4. cleanup evidence is complete and no isolation violation occurred;
5. an independent reviewer accepts the evidence package and the current Main
   authorization snapshot binds it to the intended release/baseline; and
6. GitHub `Main` Environment required-reviewer protection remains enabled for
   any later production recovery dispatch.

A future acceptance commit must include real, immutable evidence references and
the exact release identity required by PR-06C.1B. It must not copy this plan's
synthetic test fixture or replace human review with a URL-shaped field.

## Execution authorization required

Before any AWS action, the user must explicitly approve all of the following:

1. creating the dedicated non-production account/environment and only the
   tagged resources listed above;
2. the USD 25 cap, 180-minute expiry, cleanup watchdog, and named stop owner;
3. the reviewed least-privilege IAM policies and sandbox ECR image-copy source;
4. the exact database engine/version, isolated database build path, and
   read-only legacy compatibility command;
5. the two immutable fault-image digests and their deterministic fault behavior;
6. the audit bucket/KMS retention and independent evidence reviewers; and
7. a one-time execution window. This authorization must explicitly exclude all
   production accounts, databases, secrets, user data, provider calls, and
   production CD-01 dispatches.
