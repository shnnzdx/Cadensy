# PR-06C.1D — Isolated Experiment Execution Approval Package

**Status:** **EXPERIMENT APPROVAL PACKAGE READY — AWS EXECUTION NOT AUTHORIZED**

**Repository / branch:** `shnnzdx/Cadensy` /
`codex/pr06c-production-telemetry-validation`  
**Reviewed code baseline:** `c5fd744333db2791cd94d43f6681234488f836f0`

This is approval material and an offline validation update only. It does not
create, inspect, modify, or delete an AWS resource; dispatch CD-01; change a
desired count; run a migration; invoke a provider; or modify the PENDING
Recovery Safety Acceptance Manifest. It implements the approved PR-06C.1C
design and does not redesign recovery, CD-01, or telemetry delivery.

## 1. Approval decision and unresolved values

The following values are intentionally unresolved. They are not defaults and
must be supplied, independently reviewed, and frozen in the approved change
request before any AWS action.

| Required value | Current value | Approval rule |
| --- | --- | --- |
| Isolated AWS account ID | `<UNKNOWN>` | Must be a 12-digit account distinct from production `448678332746`; every experiment ECS ARN must use it. |
| Organization/SCP boundary | `<UNKNOWN>` | Must deny production-account ARNs and untagged creation. |
| Region | `us-east-1` | Fixed; no second Region is permitted. |
| Change ID, run ID, expiry UTC | `<UNKNOWN>` | Unique immutable run ID; expiry ≤180 minutes after first creation. |
| Experiment/database/cost/security owners and paging route | `<UNKNOWN>` | Named people or rotations, recorded before execution. |
| Legacy, Stage 1, Stage 2 image digests | `<UNKNOWN>` | Three immutable `sha256:` digests and ECR scan records. |
| Production-equivalence snapshot | `<UNKNOWN>` | Redacted read-only snapshot hash covering controller, circuit breaker, deployment/network shape, health check, runtime platform. |
| Official price snapshot | `<UNKNOWN>` | Dated us-east-1 sources and a versioned Calculator export. |
| Execution time window and audit retention | `<UNKNOWN>` | Named on-call coverage and approved retention expiry. |
| Evidence bucket/KMS key and exact isolated DB engine | `<UNKNOWN>` | Versioning, encryption, policy, engine/version, and reader scope approved. |
| Legacy read-only command | `<UNKNOWN>` | Runtime and database owners approve the command hash and no-write proof. |

Any unresolved entry is a blocker. A completed approval records actual values
in a versioned evidence object; it must not substitute an example, inferred
production value, or mutable tag.

## 2. Two-stage approval and go / no-go checklist

Provisioning Approval and Experiment Execution Approval are separate signed
decisions. **Provisioning Approval authorizes only the bounded resource
creation in §3 and collection of pre-fault evidence. It never authorizes an
ECS `UpdateService`, a desired-count increase, a fault-image deployment, or
either failure scenario.** A new, explicit Experiment Execution Approval is
required after provisioning and before any fault injection.

### 2.1 Provisioning Approval — before the first resource is created

All answers must be **YES**, with a versioned approval/evidence reference,
before resource creation. The approval names the isolated account, run and
change IDs, expiry, owners, USD 25 authorization cap, and the only permitted
resource manifest.

| Gate | Required evidence | No-go condition |
| --- | --- | --- |
| Isolation | Account, `us-east-1`, SCP/boundary, no production routes/peering/Secrets | Any production account, VPC, database, Secret, user data, or provider path. |
| Equivalence plan | Redacted production snapshot hash and independent comparison plan | Controller not `ECS`, circuit breaker change, or changed deployment/ALB health setting. |
| Resources | Filled manifest in §3 | Resource outside manifest, public ALB/database, NAT/Internet path. |
| Cost | §5 official snapshot and deterministic calculation | Planned amount >USD 5.00, cap >USD 25, missing continuing meter. |
| IAM | §4 placeholders instantiated and validated by Access Analyzer/Policy Simulator | Wildcard `iam:PassRole`, `Action:*`, FullAccess, non-isolated ARN. |
| Fault-image plan | §7 build specifications and no-provider design review | Provider credential, tag-only deployment plan, or unapproved build input. |
| Database plan | §8 isolated initialization and read-only command review | Production source, schema write by legacy task, or unknown command hash. |
| Watchdog | §6 independent invocation identity, finalizer identity, and paging route | Cleanup depends on operator session or no escalation target. |
| Evidence | Versioned bucket/prefix, retention, redaction, and reader design | Missing task/deployment event coverage or no redaction plan. |
| People/time | One proposed 180-minute interval and owner coverage | Automatic retry, second update, or absent owner coverage. |

### 2.2 Experiment Execution Approval — after provisioning, before fault injection

This fresh approval binds the exact, provisioned resources to the approved run.
It is not implied by the earlier signature and expires at the approved run end.
No fault image may be selected and no ECS service may be raised from zero until
every row below is **YES** with a versioned, post-provisioning evidence
reference.

| Gate | Required post-provisioning evidence | No-go condition |
| --- | --- | --- |
| Resource identity | Tagged inventory with actual account/Region/ARNs; isolated VPC, private ALB and DB, service/task-definition baseline | Any unapproved resource, ARN/account/Region mismatch, public path, or baseline drift. |
| Release equivalence | Frozen controller, circuit-breaker, deployment/network and ALB-health snapshot compared to the approved snapshot | Any material drift or a snapshot that cannot be independently reviewed. |
| Exact images | ECR immutable legacy/Stage 1/Stage 2 digests, basic scans, build-input hashes, and task-definition digest bindings | Tag-only URI, digest mismatch, overlapping legacy/candidate digest, or provider material. |
| Database gate | Isolated schema revision, restricted legacy principal, read-only transaction proof, approved command hash and one-off result | Any write, unknown command, schema mismatch, or missing database-owner evidence. |
| Evidence / cleanup readiness | Collector coverage, EventBridge schedule, watchdog/finalizer role tests, versioned-bucket write/read proof, escalation recipient | Missing event coverage, unverified audit write, or cleanup identity unavailable. |
| Cost / people / window | Dated price artifact, current tagged inventory, named on-call owners, exact start/stop/expiry UTC | Forecast exceeds the signed bound, a continuing meter is unpriced, or coverage is absent. |

The experiment reviewer, database owner, cost owner, and security owner must
sign both stages. The GitHub Main Environment reviewer remains mandatory for a
future production workflow only; neither approval authorizes production.

## 3. Resource creation manifest

All resources use `us-east-1` and the approved isolated account, with four
tags: `CadensyExperiment=PR-06C.1C`, `ExperimentRunId=<RUN_ID>`,
`ChangeId=<CHANGE_ID>`, and `ExpiresAtUTC=<UTC>`. Actual ARNs/IDs are written
to a versioned manifest before dependent creation.

| Service | Maximum resource | Name/lifecycle | Continuing charge / safety boundary |
| --- | --- | --- | --- |
| VPC / EC2 | 1 VPC, 2 private subnets in different AZs, 2 route tables, ALB/task/endpoint security groups | `pr06c1c-<RUN_ID>-*`; delete last | No IGW, NAT, peering, Transit Gateway, or public listener. Dependencies block VPC deletion until removed. |
| VPC endpoints | 8 interface ENIs: ECR API/DKR, Logs, Secrets Manager across two AZs; 1 S3 gateway endpoint | Tag all; delete before network | Endpoint-hours/data continue until deletion; endpoint SG allows only task-SG TCP/443. |
| ECS / Fargate | 1 cluster, 1 service, 3 task-definition revisions maximum; at most 2 concurrent Linux/x86 0.25-vCPU/512-MiB tasks | `pr06c1c-<RUN_ID>-*`; Fargate `awsvpc`, `assignPublicIp=DISABLED`, `LATEST` | Valid Fargate pairing. Target-group type is `ip`. |
| ALB | 1 internal ALB, target group, private listener | Delete after tasks/service reach zero | ALB and LCU metering continues until deletion. Snapshot controls health path/settings. |
| RDS PostgreSQL | 1 Single-AZ `db.t4g.micro`, 20 GiB gp3 max, 1 DB subnet group | Dedicated synthetic-only DB; delete without final snapshot unless separately approved | Instance, storage, backup/snapshot and transfer can persist. No replica, Multi-AZ, or production snapshot. |
| ECR | 1 immutable repository, 3 images maximum, each ≤500 MiB | Delete images/repository during cleanup | Basic scan only; enhanced scanning needs separate cost approval. |
| Logs | 1 log group, approved retention, 100 MiB operational cap | Export/redact evidence before delete | Ingestion/storage/query costs; prohibit Secret, token, user, provider data. |
| EventBridge / Lambda | 1 rule and 1 audit-collector Lambda, plus a non-compute control-plane finalizer role | Collector assumes a distinct watchdog role; finalizer closes its control plane after audit | Event/Lambda charges apply; cleanup does not use human session credentials. IAM roles do not add a continuing compute meter. |
| S3 / KMS | 1 versioned evidence bucket/prefix and 1 approved key | Evidence retained only for approved period | Public access block required. KMS key/API and S3 version charges are priced. |
| IAM / Budget / notification | Roles in §4, 1 USD budget, 1 approved alert route | Path `/pr06c1c/<RUN_ID>/` | Budget detects spend, not a kill switch. Alert recipient remains `<UNKNOWN>` until approval. |

No production resource may be reused. This is a bounded creation allow-list,
not permission to create the listed resources now.

## 4. Least-privilege IAM draft

Because account ID, names, bucket, key and role ARNs are unresolved, this is a
policy statement matrix—not attachable JSON. Before attachment, replace every
placeholder, validate it with IAM Access Analyzer/Policy Simulator, and apply a
permission boundary that denies non-`us-east-1`, production-account, and
untagged resource use. Creation requires all four request tags; deletion
requires the same existing resource tags wherever AWS supports that condition.

| Principal | Minimal action groups | Exact resource / condition boundary |
| --- | --- | --- |
| `PR06CExperimentProvisioner` | EC2 VPC/subnet/route/SG/endpoint create; ECS cluster/task-definition/service create; ELBv2 ALB/target/listener create; RDS DB/subnet-group create; ECR repo create; Logs group/retention; EventBridge rule/target; Lambda function/permission; S3 bucket/versioning/encryption/public-block/policy; IAM role/policy creation | Only `arn:aws:*:us-east-1:<EXPERIMENT_ACCOUNT_ID>:*` and global IAM roles under `/pr06c1c/<RUN_ID>/`; approved name prefix and request tags required. Read-only Describe/Get remains same account/Region. |
| `PR06CExperimentOperator` | ECS `UpdateService/RunTask/StopTask/DescribeServices/ListTasks/DescribeTasks/ListServiceDeployments/DescribeServiceDeployments`; ELBv2 `DescribeTargetHealth`; RDS `DescribeDBInstances`; ECR `DescribeImages/BatchGetImage`; Logs `FilterLogEvents`; S3 evidence-prefix read/write | No infrastructure create/delete. Exactly one combined task-definition + desired-count update per case is evidenced from CloudTrail/service records. |
| `PR06CAuditCollectorRuntime` | ECS/ELBv2 read-only collection; logs stream/write; S3 `PutObject/AbortMultipartUpload` only under `evidence/<RUN_ID>/`; `sts:AssumeRole` only to watchdog | Lambda function is the only trust principal. Standard collector execution has no delete action. |
| `PR06CCleanupWatchdog` | Tagged inventory and only delete/zero actions for runtime manifest resources: ECS service/task/TD, ELBv2 listener/ALB/TG, RDS DB/subnet group, EC2 endpoints/SG/subnet/route/VPC, ECR repo, log group, temporary Secret, S3 objects/versions under run prefix | Trusted only by collector runtime role; account/Region/name/run-tag match required. Cannot delete retained evidence bucket/key or its own Lambda/EventBridge control plane. |
| `PR06CControlPlaneFinalizer` | Read tagged inventory; S3 versioned audit write; EventBridge target/rule delete; Lambda permission/function delete | Separate pre-provisioned identity, not the operator or Lambda runtime. Account/Region/name/run-tag match required; it retains evidence bucket/key and records its own result after Lambda deletion. |
| `PR06CEcsExecution` | `ecr:GetAuthorizationToken` (`Resource:"*"` required registry action); ECR image pull only sandbox repository; Logs stream/write only run group; isolated Secret read and KMS decrypt only one DB secret/key | `ecs-tasks.amazonaws.com` trust; never production ECR, Secret, or KMS. |
| `PR06CEcsTask` | No AWS actions | Empty application role; no provider credentials, Secret read, IAM, ECS, or DB administration. |
| `PR06CEvidenceReader` | Evidence-bucket list and object/version read; approved key decrypt only | `evidence/<RUN_ID>/` only; no overwrite/delete, unmask, or Secret access. |

`iam:PassRole` is its own statement and can reference only:

```text
arn:aws:iam::<EXPERIMENT_ACCOUNT_ID>:role/pr06c1c/<RUN_ID>/PR06CEcsExecution
arn:aws:iam::<EXPERIMENT_ACCOUNT_ID>:role/pr06c1c/<RUN_ID>/PR06CEcsTask
arn:aws:iam::<EXPERIMENT_ACCOUNT_ID>:role/pr06c1c/<RUN_ID>/PR06CAuditCollectorRuntime
arn:aws:iam::<EXPERIMENT_ACCOUNT_ID>:role/pr06c1c/<RUN_ID>/PR06CCleanupWatchdog
```

It uses `iam:PassedToService` only for the applicable
`ecs-tasks.amazonaws.com`, `lambda.amazonaws.com`, or
`events.amazonaws.com` principal. `iam:PassRole` with `Resource:"*"` is
prohibited. Trust policies use exact service principals and, where the service
supports them, the exact `aws:SourceAccount` plus `aws:SourceArn`. The IAM
owner verifies condition-key support at attachment time rather than inventing
one. RDS/Lambda option-specific prerequisites are rechecked against AWS Service
Authorization Reference at final policy review; no `*FullAccess` policy is
allowed.

## 5. Current us-east-1 price verification checklist

USD 5.00 is the approved **planned cost ceiling**, not a billing promise. USD
25 is the maximum **authorization cap**, not a real-time stop mechanism. The
cost owner must calculate the snapshot with a script/spreadsheet and attach
versioned output; no total is accepted from mental arithmetic.

| Meter | Official source / required snapshot field | Bound |
| --- | --- | --- |
| Fargate Linux/x86 | [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/): vCPU-second, GB-second, image-pull-to-termination billing; URL/effective time/rates recorded | 2 × 0.25 vCPU / 0.5 GiB tasks × 180 minutes max. |
| ALB | [Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/): ALB-hour + consumed LCU-hour | 1 internal ALB × 3 hours, including health-check traffic. |
| Interface endpoints | [AWS PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/): endpoint ENI-hour + processed GB | 8 ENIs × 3 hours plus pessimistic endpoint data. |
| RDS | [RDS pricing](https://aws.amazon.com/rds/pricing/) and a saved [AWS Pricing Calculator](https://calculator.aws/#/) export: engine, `db.t4g.micro`, Single-AZ, gp3, backup/snapshot, transfer | 1 instance × 3 hours, 20 GiB gp3, no replica/Multi-AZ. Current exact rate is `<UNKNOWN>` until export review. |
| Logs / ECR / S3 | Calculator line items for log ingestion/storage/query, image storage, object/version storage/requests | 100 MiB logs, 3 × 500 MiB images, 100 MiB evidence. Retention effect included. |
| EventBridge / Lambda / KMS / alerting | Calculator line items for events/schedules, Lambda request/duration, KMS key/API, budget notification | One rule, one Lambda, one key, one alert route. |
| Continuing resources | ALB/LCU, RDS backups/snapshots/storage, endpoints, retained logs/objects, inter-AZ/data transfer, taxes | Every non-zero post-expiry meter must be priced and paired with cleanup. |

The price artifact records retrieval UTC, service/usage type, Region, rate/unit,
Calculator export hash, deterministic total, comparison with USD 5.00, USD
20.00 authorization reserve, and the explicit statement that Budget alerts can
lag. An estimate above USD 5.00, missing meter, or unpriced continuing resource
is NO-GO until new cost approval.

## 6. Cleanup Watchdog execution design

The approved 1C ceiling remains one audit-collector Lambda. EventBridge invokes
that function with `mode=cleanup`; the function assumes
`PR06CCleanupWatchdog` before any cleanup action. This uses a distinct cleanup
role and does not depend on the operator's session.

**Triggers:** immutable T+180 EventBridge schedule; any stop condition; and a
manual emergency invocation by the named security owner. The operator cannot
cancel the schedule. Payload contains only run ID, change ID and cleanup mode—no
Secret or credential.

**Idempotent sequence:**

1. write a versioned `cleanup-started` record and inventory resources bearing
   all four tags;
2. prevent further experiment updates; set only the tagged ECS service to zero;
   wait for no running/pending tasks and collect evidence;
3. export/redact evidence, then delete service, listener/ALB/TG, endpoints,
   temporary Secret, RDS DB/subnet group, ECR repository, log group, security
   groups, subnets, routes, and VPC in dependency order;
4. re-list the runtime tags and write/verify a versioned
   `cleanup-runtime-complete` inventory proving no tagged **runtime** resource
   remains; and
5. retain the evidence bucket/key and the Lambda/EventBridge control plane for
   the separate finalization step below.

The watchdog must not delete the Lambda, its invoke permission, or its
EventBridge target/rule while it is the executing audit writer. That would make
the final cleanup record dependent on a resource it has already removed.
Instead, after step 4 a separately provisioned `PR06CControlPlaneFinalizer`
performs this ordered closeout:

1. read the runtime-complete inventory and write/verify a versioned
   `control-plane-finalization-started` record;
2. remove the EventBridge target and rule, then the Lambda invoke permission,
   so no new watchdog invocation can begin;
3. delete the audit-collector Lambda last; and
4. as the independent finalizer remains active, write/verify a versioned
   `control-plane-finalization-completed` (or `CLEANUP_FAILED`) record with the
   post-delete tagged inventory and every deletion result.

The finalizer role and retained evidence bucket/key are not deleted by this
flow; they are closed only after approved evidence-retention expiry. Thus no
billable runtime remains before the audit writer is removed, while the final
audit is still written by an identity that survives the Lambda deletion.

`NotFound` is an idempotent delete result but is recorded. Access denial,
timeout, leftover tag, missing audit write, or schedule failure pages the named
cost and security owners, writes `CLEANUP_FAILED`, and blocks Manifest
promotion. `automatic_cleanup_completed=true` cannot replace execution evidence
plus a timestamped post-cleanup inventory.

## 7. Fault-image and digest approval specification

Images are sandbox-only derivatives of the approved immutable base. ECR tags
are immutable, but task definitions use
`<repository>@sha256:<digest>`, never a tag. Evidence records ECR image digest,
manifest media type, basic scan result, build-input commit, Dockerfile hash and
task-definition image URI.

| Image | Build specification | Required observed evidence | Approval condition |
| --- | --- | --- | --- |
| Legacy | Exact sandbox copy of `tripsync-backend:19` by immutable digest; no production Secret/provider config | Source revision and forward-schema one-off only; never `RUNNING` during rollback windows | Digest equals `old_runtime_forward_schema.legacy_runtime_image_digest`. |
| Stage 1 | Same platform/base; essential container exits during startup before port bind; `MOCK_AI=1`; no provider key | Every actual failed task has unique ARN, no `RUNNING`, and a status sequence whose final state is `STOPPED`, with stopped/failure reason and versioned attempt artifact | Exit-on-start is an injection design, not proof. Any `RUNNING` or non-`STOPPED` terminal state makes result `INCONCLUSIVE`. |
| Stage 2 | Same platform/base; binds exact snapshot port but fails unchanged snapshot ALB health path; `MOCK_AI=1`; no provider key | Every actual failed task includes `RUNNING` and a status sequence whose final state is `STOPPED`, target health `unhealthy`, reason, and versioned attempt artifact | No altered health threshold, grace period, port, controller or circuit breaker. A non-`STOPPED` terminal state is `INCONCLUSIVE`. |

The approved snapshot supplies the expected failure threshold. The offline
contract now rejects a case unless `actual_failed_task_count` is an integer
exactly equal to `expected_failure_count`, and `failed_task_attempts` has that
same length. Each attempt must contain sequential attempt number, unique ECS
Task ARN, task-definition ARN, candidate image digest, status transition,
failure reason, timestamp and versioned audit reference. Every attempt must
finally reach `STOPPED`; Stage 1 must never include `RUNNING`, while Stage 2
must include `RUNNING` before `STOPPED`. Expected count alone, missing attempts,
duplicate task ARNs, unrelated task identity, or an unfinished task cannot pass.

## 8. Isolated PostgreSQL initialization and forward-schema gate

The following are **future approved-environment templates**, not commands to
execute now. DSNs, Secret ARNs, engine/version and legacy command remain
`<UNKNOWN>`; passwords must never appear in shell history, CI logs or evidence.

1. Database owner applies the reviewed isolated migration and records command
   hash:

   ```text
   cd backend
   <APPROVED_PYTHON> -m alembic -c alembic.ini upgrade c2a4b6d8e0f1
   psql "$ISOLATED_ADMIN_DSN" -X -v ON_ERROR_STOP=1 \
     -c "SELECT version_num FROM alembic_version;"
   ```

   Result must be exactly `c2a4b6d8e0f1`. No production restore, snapshot,
   replica, credential or network link is permitted.

2. Database owner creates an experiment-only legacy principal restricted to
   `CONNECT`, schema `USAGE`, and `SELECT` only. The exact grant SQL, role,
   object list and redacted grants output are approval artifacts; no DML, DDL,
   migration or seed privilege is allowed.

3. Preflight under that principal proves the connection is read-only:

   ```sql
   BEGIN TRANSACTION READ ONLY;
   SELECT current_user;
   SELECT version_num FROM alembic_version;
   ROLLBACK;
   ```

4. The legacy container command is `<UNKNOWN>` until runtime/database owners
   inspect its immutable image config and approve a command hash. It must use
   the read-only DSN, `MOCK_AI=1`, no provider credential, no Alembic/seed
   command and no application write path. It runs as an isolated one-off ECS
   task, never a production service update. Any write, denied privilege, startup
   error or unknown command is `INCOMPATIBLE / BLOCKED`.

Evidence binds legacy digest, engine/version, revision, command hash, task ARN
and exit result, grants output, read-only proof and database-owner approval. A
health response alone is insufficient.

## 9. Final approval record and stop conditions

Each approval record names the phase, exact run/change ID, approvers, evidence
version IDs and expiry. The Provisioning Approval authorizes exactly one §3
manifest creation. The later Experiment Execution Approval authorizes at most
the two specified fault scenarios in that same manifest run. A Provisioning
Approval, a completed provisioning inventory, or a cleanup approval is never a
substitute for Experiment Execution Approval. This experiment does not
authorize a production release.

Stop and invoke cleanup for any production ARN/account/Region, public exposure,
provider invocation, missing audit event, stale equivalence snapshot, price
alert/overrun, old-digest start, second update/retry, scenario mismatch or
terminal timeout. Never promote the PENDING Manifest from a fixture,
URL-shaped reference or incomplete evidence.

After cleanup, reviewers inspect raw ECS Task State Change Events,
`DescribeTasks`, service/deployment records, task definitions/digests, ALB
target health, CloudTrail, collector coverage, forward-schema, cost/cleanup
evidence and approvals. Then run the offline contract test. A parser pass
proves payload consistency only; it does not prove that AWS evidence is genuine.
