# PR-06C.1C — Isolated ECS Rollback Experiment Plan

**Status:** **EXPERIMENT DESIGN READY FOR INDEPENDENT APPROVAL — AWS EXECUTION NOT AUTHORIZED**

**Scope:** an execution design and pure offline evidence contract only. No AWS
resource has been read, created, changed, or deleted for this revision. This
document is not an execution authorization and does not alter the current
`PENDING` recovery Manifest or the frozen CD-01 release workflow.

## Objective and narrow safety assertion

The experiment will measure ECS behavior when a sandbox service at
`desiredCount=0` receives one approved `UpdateService` that changes its task
definition and sets desired count to `1`. It must separately induce and prove:

1. **Stage 1**: candidate tasks fail before any candidate reaches `RUNNING`.
2. **Stage 2**: candidates reach `RUNNING`, then fail the unchanged ALB health
   check.

The only safe result is a circuit-breaker rollback with final
`desired/running/pending = 0/0/0`, **and complete time-bounded evidence that
the old digest never ran**. A different result, incomplete evidence, delayed
event delivery, a candidate that reaches `RUNNING` in the proposed Stage 1
case, or a deployment that exceeds its observation deadline is
**INCONCLUSIVE / BLOCKED**. It is never a pass.

AWS describes distinct circuit-breaker stages: failures before a task reaches
`RUNNING` are Stage 1; health-check failures are evaluated after `RUNNING` in
Stage 2. For a service with `desiredCount=1`, the documented default failure
threshold has a minimum of three failures. The plan therefore budgets at least
three failure attempts per case and does not change the production-derived
threshold for convenience. [ECS deployment circuit breaker](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html)

## Isolation, account binding, and resource ceiling

The experiment must be in a dedicated non-production account, not production
account `448678332746`, and only in `us-east-1`. Production databases,
Secrets, user data, VPC connectivity, ECR repositories, and AI-provider
credentials are prohibited.

| Area | Fixed design boundary / maximum |
| --- | --- |
| ECS | One dedicated cluster and one service; `ECS` rolling deployment controller; circuit breaker `{enable: true, rollback: true}`; at most two Fargate tasks concurrently; each Linux/x86 task is 0.25 vCPU / 0.5 GiB with default 20 GiB ephemeral storage. The immutable `:19` digest must be verified compatible with this platform before execution. |
| ALB | One **internal** ALB, one target group, and two private subnets in two AZs; no public listener or listener rule other than the experiment health path. |
| Database | One Single-AZ, non-production PostgreSQL `db.t4g.micro`, 20 GiB gp3 maximum, one database, synthetic fixtures only. It is never a snapshot, replica, restore, peer, or credential target of production. |
| Private access | At most eight interface endpoint ENIs: ECR API, ECR DKR, CloudWatch Logs, and Secrets Manager across two AZs; one S3 gateway endpoint. No NAT gateway, Internet gateway route, VPC peering, Transit Gateway, or provider endpoint. |
| Images / logs / evidence | One sandbox ECR repository, no more than three immutable images (legacy plus two faults), each at most 500 MiB; one log group capped operationally at 100 MiB; one versioned evidence bucket capped operationally at 100 MiB. One EventBridge rule and one audit-collector Lambda are allowed solely to write task/deployment events to that bucket. |
| Identity | Separate provisioner, operator, evidence reader, and cleanup-watchdog roles; SCP/permission boundary denies production account ARNs and untagged creation. ECS execution/task roles have only the named sandbox resources. |

The production-equivalence preflight must capture a read-only, redacted
snapshot of the actual production deployment controller, circuit-breaker,
minimum/maximum healthy percentages, health-check configuration, and runtime
platform. The sandbox configuration must hash-match this approved snapshot.
This is an evidence requirement; it does **not** grant permission to change
production. `iam:PassRole` must name only the approved ECS execution and task
roles and be constrained to ECS.

## Cost contract — authorization cap, not invoice guarantee

`budget_cap_usd` is an **integer authorization ceiling** in the offline
contract: `1 <= budget_cap_usd <= 25`. `max_duration_minutes` is likewise an
integer: `1 <= max_duration_minutes <= 180`; booleans are rejected for either
field or for task counts. The approved request must set the maximum values only
when the cost owner explicitly accepts them.

The following conservative planning calculation is based on a three-hour
maximum and the resource ceilings above. It is an approval input, not a claim
about the eventual invoice. Rates must be re-snapshotted from the linked AWS
sources and attached to the approval before resources are created.

| Meter (us-east-1) | Planning rate / formula | Three-hour planning amount |
| --- | --- | ---: |
| Fargate Linux/x86 | AWS's published CPU $0.000011244/vCPU-second and memory $0.000001235/GB-second; 2 × (0.25 vCPU + 0.5 GiB) × 10,800 seconds | $0.0741 |
| Internal ALB | $0.0225/load-balancer-hour × 3, plus a conservative 1 LCU × $0.008/hour × 3 | $0.0915 |
| Interface endpoints | 8 endpoint ENIs × $0.01/hour × 3, plus no more than 1 GiB endpoint data × $0.01/GB | $0.2500 |
| RDS PostgreSQL | planning input: one `db.t4g.micro` at $0.016/hour × 3 plus 20 GiB gp3 at $0.115/GB-month prorated for 3 hours | $0.0576 |
| Logs, ECR, S3, Lambda/EventBridge, backups and rounding | explicit operational caps above; reserve rather than assume a zero bill | $4.5268 |
| **Planned maximum** | resource subtotal $0.4732 + reserve | **$5.00** |
| **Authorization buffer** | USD 25 authorization cap − USD 5.00 planned maximum | **$20.00** |

Price sources are the [AWS Fargate pricing page](https://aws.amazon.com/fargate/pricing/),
[Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/),
[AWS PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/), and a
saved [AWS Pricing Calculator](https://calculator.aws/#/) estimate for the
exact RDS engine, storage, logs, and regional rates. The Fargate page bills
from image download through termination; ALB and endpoint partial hours, data
transfer, logs, backups, taxes, delayed metering, and pricing changes can make
an invoice differ from this estimate.

An AWS Budget alert is only detection: it is **not** a real-time kill switch
and does not guarantee charges stop at USD 25. The operator must stop on the
first alarm. Any preflight calculation at or above USD 5.00, or any service
size/count outside the table, requires a new cost approval; it may not consume
the buffer silently.

## Cleanup watchdog and stop conditions

`PR06CCleanupWatchdog` is an independent execution identity—not the operator
session—and is scheduled for expiry at T+180 minutes and invoked on any stop
condition. It acts only on resources bearing both
`CadensyExperiment=PR-06C.1C` and the approved run ID. Re-running it is safe:

1. record the tagged inventory and disable further operator updates;
2. set only the tagged sandbox service to zero, wait for no running/pending
   tasks, and collect evidence;
3. delete tagged service, ALB/target group, endpoint ENIs, temporary Secret,
   ECR repository/images, log group, RDS instance, and sandbox network in
   dependency order; retain the evidence bucket only under its approved
   retention policy; and
4. re-list tagged resources and publish a versioned post-cleanup inventory.

Failure, timeout, missing inventory, a leftover tagged runtime resource, or
watchdog invocation under any other identity pages both cost and security
owners, blocks Manifest promotion, and requires manual incident handling.
`automatic_cleanup_completed=true` alone is insufficient: the offline contract
also requires watchdog evidence and a timestamped, versioned clean inventory.

Immediate stop conditions are: any production ARN/account/Region, production
Secret/data access, public exposure, provider call, unapproved resource count
or price, budget alarm, denied isolation control, old-digest start, a manual
second update/retry, or any result outside the matrix.

## Stage-accurate failure matrix and timing

No sandbox field may quietly differ from the approved production snapshot:
deployment controller, circuit breaker, deployment configuration, health-check
path/matcher/interval/threshold/grace settings, runtime platform, and service
network configuration all require the recorded hash comparison. A different
snapshot is a preflight failure, not an experiment variation.

| Case | Fault image behavior | Required proof | Result if proof is absent |
| --- | --- | --- | --- |
| `startup_failure` / Stage 1 | An essential container exits during startup before binding the app port. This is a proposed injection only; exit-on-start is not itself proof of Stage 1. | Every candidate Task State Change record and `DescribeTasks` reconstruction shows `PENDING`/`STOPPED`, no `RUNNING`; stopped reason and deployment failure reason are present; health checks were not evaluated; circuit breaker reaches rollback. | `INCONCLUSIVE`, including any candidate `RUNNING` transition. |
| `alb_health_failure` / Stage 2 | Candidate binds the unchanged target port but returns a failing response on the unchanged sandbox ALB health path. | Candidate Task State Change/`DescribeTasks` shows `RUNNING`; `DescribeTargetHealth` is `unhealthy`; service deployment failure reason and Stage 2 rollback are captured. | `INCONCLUSIVE`. |

The operator issues exactly one approved combined update per case: the bound
candidate task-definition ARN plus `desiredCount=1`. No force deployment,
manual rollback, retry, second update, direct capacity adjustment, or threshold
override is allowed during the observation window.

The 180-minute window is feasible only under the following bounded schedule:
25 minutes preflight and source-revision capture; 40 minutes Stage 1 (three
failures at up to 10 minutes each plus evidence capture); 40 minutes Stage 2
on the same basis; 15 minutes event-delivery/reconciliation; 30 minutes
cleanup; and 30 minutes reserve. If any case has not reached a terminal state
within its 40-minute envelope, the watchdog cleans up and the case is
`INCONCLUSIVE`; the plan must not claim that a default three-failure threshold
was covered.

## Time-bounded old-runtime non-restart proof

Counts at experiment end cannot prove that an old task did not briefly run.
For each case, the audit collector opens a window before the combined update
and closes it only after terminal deployment state, final `0/0/0`, and the
event-delivery reconciliation period. It captures:

- every available ECS Task State Change Event and Service Deployment/Service
  Event, with collection checkpoints and timestamps;
- `ListTasks`/`DescribeTasks` reconciliation before, during, and after the
  deployment, preserving each Task ARN, task-definition ARN, status sequence,
  stopped reason, container digest, and observation time;
- service revision/deployment records, target-health snapshots, and the exact
  source/target task definitions; and
- a versioned evidence index binding every candidate digest, target revision,
  task definition, scenario, account, Region, and experiment run ID.

The two cases must use exactly the same old digest, and it must equal
`old_runtime_forward_schema.legacy_runtime_image_digest`. All ECS service
revision and task-definition ARNs must use the experiment account and
`us-east-1`; a production account ARN such as `448678332746` is rejected. A
Task observation carrying the old digest and `RUNNING`, missing event coverage,
unreconciled delivery, an incomplete time window, or `UNKNOWN` status blocks
the result. The offline validator checks these declarations and cross-links;
independent reviewers must inspect the raw artifacts because a parser cannot
prove that an external AWS event stream is genuine or complete.

## Old-runtime forward-schema compatibility

This is a distinct mandatory gate. Build an empty, isolated PostgreSQL database
at Alembic revision `c2a4b6d8e0f1`; do not copy, restore, peer with, or connect
to production. Run the exact immutable old `tripsync-backend:19` digest using
an experiment-only principal limited to `CONNECT`, schema `USAGE`, and
`SELECT`. It may execute only read-only schema/application checks and a
read-only health probe—never migration, seed, DDL, DML, or provider traffic.

The evidence must bind legacy digest, revision, command hash, task exit result,
redacted grants, and versioned audit artifacts. A denied write, schema mismatch,
startup error, unexplained driver error, missing database-owner approval, or
unreviewed evidence is `INCOMPATIBLE / BLOCKED`.

## Offline evidence gate and Manifest rule

`scripts/pr06c_isolated_rollback_experiment_contract.py` is a pure local
validator; it has no AWS SDK, shell, or network dependency. It enforces the
budget/duration type and range, cleanup evidence, account/Region-bound ECS
ARNs, digest/run/scenario binding, immutable audit-reference shape,
production-snapshot equivalence declaration, Stage 1/Stage 2 facts, strict
integer zero counts, and time-bounded non-restart evidence. Its companion
regression suite is `backend/tests/test_pr06c_isolated_rollback_experiment.py`.

Machine-verified here means only **payload structure and consistency**.
Independent humans must verify AWS identity, raw ECS/ALB/event artifacts,
pricing snapshot, isolation, cleanup inventory, experiment result, and the
three distinct approvals. A versioned S3 URL, an `ACCEPTED`-looking field, an
offline fixture, or an `automatic_cleanup_completed` declaration is not proof
of a real experiment.

The existing Recovery Safety Acceptance Manifest remains **PENDING**. It may
be proposed for `ACCEPTED` only after both cases have independently reviewed
complete evidence, forward-schema compatibility is approved by the database
owner, cost owner and experiment reviewer approve separately, cleanup evidence
is complete, the current immutable release/baseline binding passes PR-06C.1B,
and GitHub Main Environment required reviewers remain enabled. This plan does
not authorize changing that Manifest.

## Exact authorization required before any AWS action

The user must approve, in one bounded change request:

1. the named dedicated account and `us-east-1` only, plus the resource counts,
   task/RDS sizes, image size limits, and `$25` authorization cap above;
2. a current saved AWS Pricing Calculator estimate, the `$5.00` planned ceiling,
   named cost owner, 180-minute expiry, and watchdog escalation contacts;
3. reviewed least-privilege policies, audit collector/watchdog identities,
   sandbox ECR digest-copy source, and no-production SCP/permission boundary;
4. the read-only production-equivalence snapshot procedure and exact unchanged
   deployment/health-check values to reproduce;
5. the two immutable fault image digests and the one-time run window;
6. the exact isolated database build path, read-only legacy compatibility
   command, evidence-bucket/KMS retention, and independent reviewers; and
7. explicit exclusion of production accounts, production data/Secrets, AI
   provider calls, CD-01 dispatches, migrations, and production deployment.

Until that approval exists, no AWS resource may be created or modified and
production recovery remains blocked.
