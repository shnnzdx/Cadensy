# PR-06C.1A — ECS Zero-Capacity Recovery Readiness

**Evidence timestamp:** 2026-10-14T23:47:39Z
**Scope:** read-only AWS investigation and offline recovery analysis only.
**Decision:** **CAPACITY RECOVERY BLOCKED**
**PR-06C status remains:** **BLOCKED — PRODUCTION GATE NOT SATISFIED**

No ECS service update, task-definition registration, one-off task, migration,
CD-01 dispatch, database operation, IAM/secret/feature-gate change, or paid
model request was made.

## Live resource evidence

| Check | Status | Evidence |
| --- | --- | --- |
| AWS target identity | PASS | STS returned account 448678332746 for explicit region us-east-1. Credentials from user-designated backend/.env were loaded only into the audit subprocess; no value was printed or persisted. |
| ECS service state | PASS | tripsync-backend-service is ACTIVE, but has desiredCount=0, runningCount=0, and pendingCount=0. |
| Current deployment | PASS | Sole primary deployment is tripsync-backend:19, COMPLETED, with all deployment counts at zero. |
| Current runtime image | PASS | tripsync-backend:19 points to old digest sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34, not PR-06A digest sha256:d2b0c580845c2b1afb738260c788f1cadb01416ba1018493fe0e9f2d133dd83c. |
| Service rollback configuration | PASS | Circuit breaker has rollback enabled; minimum healthy percent is 100 and maximum percent is 200. This alone does not prove what capacity/task definition a rollback will run after a combined capacity-and-task-definition update. |
| ALB target health | FAIL | The service target group has zero registered targets, hence zero healthy targets. |
| Public API read-only health check | FAIL | GET https://app.cadensy.top/api/health returned HTTP 503 on 2026-10-14T23:46:41Z. No body beyond the generic 503 page was retained. |
| CloudWatch delivery readiness | PASS | Current task definition retains awslogs to /ecs/tripsync-backend; the log group exists with seven-day retention. This does not establish PR-06A event delivery while no task is running. |
| Current safety posture | PASS | Current task definition is production mode, has scheduler disabled, DEV_ALLOW_MEMBERSHIP_HEADER=0, and all 17 configured protected CADENSY_B* gates at 0. DATABASE_URL was verified only as a secret reference. |

## Zero-capacity root cause

**Root cause: UNKNOWN.** Available read-only evidence establishes the state
transition but does not establish its initiator or intent.

| Investigation | Status | Evidence |
| --- | --- | --- |
| Recent service history | PASS | One task started and registered in the target group on 2026-10-13 21:16 (UTC−05), followed by steady state. On 2026-10-14 10:10 (UTC−05), one task was stopped and one target deregistered; the service then reported steady state at zero capacity. |
| CloudTrail UpdateService evidence | UNKNOWN | Latest matching UpdateService record (2026-10-13 21:15 UTC−05) set task definition tripsync-backend:19 but did not include desiredCount. It was an IAM-user AWS CLI invocation; identity detail is intentionally not reproduced. No event explicitly setting this service to zero was found in the available 90-day regional Event History response. |
| Resource-indexed CloudTrail history | UNKNOWN | Exact resource-name lookup returned no ECS service event. Event History indexing and retention do not prove absence of an operation. |
| Application Auto Scaling | PASS | No scalable target, scaling policy, or Application Auto Scaling scheduled action exists for service/tripsync-cluster/tripsync-backend-service. |
| EventBridge / Scheduler automation | PASS | No EventBridge rule directly targets the ECS service ARN; no ECS-targeting EventBridge Scheduler schedule was found. |
| Service tags / explicit cost-control marker | UNKNOWN | The service has no tags. No automatic cost-control mechanism can be inferred from absence of tags. |

Do not characterize this as a manual pause, budget action, scheduled stop, or
auto-scaling event without additional audit evidence.

## Last known running state

The last observed ECS-stable runtime was tripsync-backend:19 using old image
sha256:c51782…. Service history shows one task and one registered target before
the later stop. The latest direct CloudTrail record with an explicit non-zero
desiredCount is older: desiredCount=1 for backend revision :18 on
2026-08-24. The exact desired count of the :19 running period is **UNKNOWN**;
it must not be inferred only from the registered-target count.

## Option assessment

| Dimension | Option A — restore existing runtime first | Option B — controlled capacity restoration with PR-06A release |
| --- | --- | --- |
| Action shape | An approved capacity update starts old :19, then CD-01 runs later. | A separately reviewed CD-01 change controls immutable PR-06A promotion and approved capacity restoration together. No workflow change is made in this phase. |
| Old-runtime exposure | **High.** Old image becomes externally reachable before PR-06A checks and telemetry validation. | **Lower on success.** New digest is intended as first runtime. Current rollback behavior after a combined update is unproven and could reintroduce old :19; this cannot be claimed impossible. |
| Database compatibility | **UNKNOWN and required before action.** | New image requires CD-01 schema-head preflight; old-image rollback compatibility must also be established or the rollback design changed. |
| Health / rollback | Existing circuit breaker exists, but no live target/API health currently exists. Starts old code first. | Retains digest, Main approval, schema preflight, circuit breaker, ALB health, and API smoke gates; needs a reviewed answer for rollback-to-old behavior. |
| Cost | Starts Fargate compute before release; ALB, logging, and provider traffic may resume on old code. | Starts compute only in the authorized release path; same steady-state compute at equal capacity, without a successful-path old-runtime interval. |
| Complexity | Lower, but separates capacity from artifact verification. | Higher: existing CD-01 never changes desiredCount and must be independently reviewed. |
| Delivery compatibility | Weak: first public runtime is outside CD-01. | Stronger after review: retains Main approval, ECR validation, live-task cloning, safety gates, schema preflight, circuit breaker, ALB and API gates. |

## Recommended recovery strategy

**Recommend Option B conditionally; do not execute it yet.** It avoids
intentional public re-exposure of stale code on the successful path and keeps
PR-06A controls together. It is not runnable with current CD-01 because the
workflow preserves zero desired capacity.

A separate reviewed design must:

1. retain Main Environment protection, source ancestry and immutable ECR checks;
2. retain the read-only migration-head preflight before service mutation;
3. permit only an approval-visible recovery desired count, not arbitrary scaling;
4. preserve 100/200 deployment settings and circuit-breaker rollback;
5. prove from ECS documentation or an isolated equivalent environment what a
   failed combined deployment rolls back to when the prior deployment has zero
   capacity. If old :19 could start, establish its forward-schema compatibility
   or redesign rollback before production approval;
6. require desired/running counts, new image digest, healthy ALB target,
   GET /api/health, and bounded privacy-safe CloudWatch validation after change.

## Proposed desired count and cost

**Proposed desired count: 1, pending explicit service-owner approval.** It is
the smallest non-zero target, matches the observed single-task/single-target
last-running shape, and is compatible with 100/200 deployment configuration.
It is not proof of business capacity and not authorization to update service.

Read-only AWS Price List evidence for the live 0.25 vCPU / 0.5 GiB task in US
East (N. Virginia) was $0.04048 per vCPU-hour and $0.004445 per GiB-hour.
Deterministic calculation gives this Fargate compute-only estimate:

| Capacity | Hourly USD | Daily USD | 730-hour month USD |
| --- | ---: | ---: | ---: |
| One 0.25 vCPU / 0.5 GiB task | 0.0123425 | 0.2962200 | 9.0100250 |

ALB recovery-delta cost is **UNKNOWN** because LCU/data processing depends on
traffic. CloudWatch incremental cost is **UNKNOWN** until telemetry volume is
measured. Provider cost is **UNKNOWN** because invocation volume, model mix,
and token usage are outside this phase. The estimate excludes data transfer,
NAT/VPC endpoints, storage, discounts/free-tier effects, and temporary
two-task rollout capacity.

## Prerequisites, rollback, and stop conditions

Before any release action:

- CD-01 read-only schema preflight must prove current and head are Alembic
  revision c2a4b6d8e0f1;
- old :19 forward-schema compatibility is required if rollback can start it;
- generated task definition must retain roles, secret references, awslogs,
  production mode, and gate-off posture;
- service owner must accept desiredCount=1 and temporary two-task capacity.

Stop before mutation if ECR provenance changes, schema preflight or task
contract fails, reviewer protection is missing, rollback semantics remain
unproven, or database compatibility is absent. After an approved mutation,
invoke the separately authorized incident process if ECS cannot run the new
definition, ALB lacks a healthy target, health endpoint fails, or privacy-safe
telemetry validation fails. Do not automatically scale, retry old code,
downgrade the database, or bypass CD-01.

## Exact approvals required

1. Approve a separately reviewed minimal CD-01 workflow design for controlled
   non-zero capacity and rollback semantics; this does not execute it.
2. Approve desiredCount=1 as recovery target and accept its cost/risk envelope.
3. After the design and database prerequisites pass, provide explicit
   production-release authorization through the protected Main environment.
4. Authorize any synthetic telemetry separately; passive bounded CloudWatch
   validation remains a later release gate.

## Remaining blockers

- Mechanism/actor that set zero capacity is UNKNOWN.
- No task is running; ALB has no target and public health is HTTP 503.
- Existing CD-01 preserves zero desired capacity.
- Old-runtime forward-schema compatibility and combined-update rollback behavior
  are unverified.
- No reviewed workflow design or production release authorization exists.
