# PR-06C.1B — ECS Zero-to-One Release Design

**Design timestamp:** 2026-10-15T00:20:00Z
**Scope:** offline CD-01 contract and design only.
**Decision:** **BLOCKED — RECOVERY SAFETY NOT PROVEN**

This document does not authorize a release. No AWS API was invoked while
implementing this design; in particular, no service, task definition, task,
database, IAM resource, secret, gate, or load balancer was changed.

## Design decision

Option B remains the conditional recommendation: make the first intended
public task the immutable reviewed runtime, rather than deliberately restoring
the old `tripsync-backend:19` runtime first. The design is guarded by an
explicit `zero_capacity_recovery` dispatch mode. It is not a general scaling
control and it has no default that can restore capacity.

The existing `standard` mode now requires an explicitly supplied non-zero
baseline and preserves that exact count. The recovery mode accepts only this
transition:

| Dispatch baseline | Allowed release desired count | Rejected examples |
| --- | ---: | --- |
| `zero_capacity_recovery`, expected current count `0` | `1` | omitted mode, standard mode at zero, `0`, `2`, or any other count |
| `standard`, expected current count `>=1` | exact expected count | recovery count or rollback evidence supplied to standard mode |

Thus `1` is a reviewed safety bound, not an arbitrary workflow input. It is
the smallest non-zero count, consistent with the historically observed
one-target shape, but is still subject to service-owner cost approval.

## Dispatch contract

CD-01 now requires these visible inputs in addition to source and both image
digests:

| Input | Recovery rule | Why it is required |
| --- | --- | --- |
| `release_mode` | exact `zero_capacity_recovery` | prevents accidental capacity restoration |
| `expected_current_task_definition` | exact approved ECS ARN | rejects revision drift |
| `expected_current_image_digest` | exact SHA-256 in the currently running definition | rejects old-image/image-provenance drift |
| `expected_current_desired_count` | exact `0` | makes zero-to-one intentional |
| `recovery_desired_count` | exact `1` | blocks arbitrary scaling |
| `rollback_evidence_sha` | exact current `origin/main` authorization snapshot | prevents a formerly accepted snapshot from bypassing a later pending or revoked state |

The contract is implemented in
[`capacity_recovery_contract.py`](../../scripts/cd01/capacity_recovery_contract.py).
It is pure Python: it has no AWS SDK, subprocess, socket, or HTTP dependency.
It receives only dispatch strings and read-only JSON captured by the workflow.

## Ordered release controls

The following is an implementation sequence, not a claim that a production
release has run:

1. GitHub's protected `Main` Environment requires a reviewer before AWS
   credentials are configured.
2. CD-01 validates source ancestry, image digest syntax, and the recovery
   contract. In recovery mode it requires the workflow commit and supplied
   `rollback_evidence_sha` to equal the current `origin/main` authorization
   snapshot, then reads the Manifest from that exact immutable snapshot.
3. It verifies account, ECR source tags, the exact live service/task-definition
   baseline, container digest, production/gate posture, `awslogs`, secret
   reference, no pending task, a sole completed primary deployment, and the
   `100/200` circuit-breaker configuration.
4. The read-only schema-head preflight completes before the application task
   definition is registered. A preflight or task-definition-registration
   failure exits under `set -euo pipefail` before `UpdateService`.
5. After application task-definition registration, CD-01 describes the service
   and its current task definition again and applies the same closed contract.
   Any drift leaves at most an unused registered task definition; it makes no
   service mutation.
6. Only then does one `UpdateService` carry both the new digest-pinned task
   definition and the contract-derived desired count. It retains
   `minimumHealthyPercent=100`, `maximumPercent=200`, and enabled
   circuit-breaker rollback.
7. It waits for stability and requires the new task definition, desired and
   running count both `1`, zero pending tasks, healthy ALB targets, and a
   read-only `/api/health` success. A future bounded telemetry check is still a
   separate production validation gate.

AWS documents that setting desired count to zero stops a service and restoring
the original count starts it; `UpdateService` also supports task-definition
updates. [ECS update-service parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/update-service-parameters.html)
The `100/200` settings permit, but do not promise, temporary two-task rollout
capacity for a desired count of one. [ECS service definition parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service_definition_parameters.html)

## Fact, inference, and unproven boundary

| Statement | Classification |
| --- | --- |
| `UpdateService` can set desired count and task definition; zero stops the service. | Documented AWS behavior. |
| ECS deployment circuit breaker can mark a deployment failed and roll back to its last completed service revision. | Documented AWS behavior. [Circuit breaker](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html) |
| A successful combined update should make the new digest the intended first public runtime. | Design intent / inference; post-update verification is required. |
| A failed combined update from current `:19`/zero will preserve zero capacity rather than start old `:19`. | **Unproven.** Desired count is not listed among documented service-revision properties. [Service-revision properties](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-revision-property.html) |
| Circuit breaker protects later telemetry, privacy, or business failures after rollout completes. | **False.** Those require a separate incident decision; no automatic rollback is authorized. |

The evidence SHA proves reviewed material exists in Main; it does not prove the
unproven rollback behavior. A dedicated isolated-environment experiment is
required before any production recovery approval.

## Concurrency and retry policy

GitHub concurrency serializes CD-01 runs for this repository and does not
cancel an in-progress release. It is not an AWS compare-and-swap lock. Console,
CLI, automation, or another principal can still change ECS between the final
read and `UpdateService`.

The workflow narrows that window with its second baseline read. If it rejects
drift, do not retry with copied values: stop, re-audit, obtain a new explicit
approval, and dispatch once. If a service mutation has begun, do not launch a
second workflow or manually change desired count while ECS is converging.

## Final independent-review corrections

### Recovery Safety Acceptance Manifest

`rollback_evidence_sha` no longer passes merely because it contains a Markdown
threat model. For `zero_capacity_recovery`, CD-01 reads the committed
[`08_PR06C_RECOVERY_SAFETY_ACCEPTANCE_MANIFEST.json`](08_PR06C_RECOVERY_SAFETY_ACCEPTANCE_MANIFEST.json)
at that exact SHA before AWS credentials are configured. The pure offline
contract rejects unless all of the following are present and positive:

- schema version `pr06c-recovery-safety/v1` and top-level `ACCEPTED` state;
- a `PASSED` isolated non-production experiment with an immutable evidence
  commit/content fingerprint, an evidence pointer, independent-human-review
  declaration, and an observed rollback outcome;
- `COMPATIBLE` old-runtime forward-schema evidence;
- `APPROVED` recovery-safety evidence; and
- an approved service baseline snapshot.

The committed Manifest is deliberately `PENDING`: it contains no invented
experiment, compatibility, or approval data. Therefore every current
zero-capacity production dispatch fails closed. This is intentional and is not
a release failure to work around.

### Current authorization snapshot and evidence semantics

An `ACCEPTED` Manifest is effective only when it is stored at the exact current
`origin/main` SHA. CD-01 rejects both a dispatch SHA and a workflow checkout
that differ from that snapshot, and re-fetches `origin/main`, reloads the
Manifest, and compares the same SHA immediately before `UpdateService`. A
newer `PENDING` or `REVOKED` Manifest therefore supersedes an older accepted
one before service mutation.

The approved Manifest binds one release identity: source SHA, runtime and
migration image digests, expected baseline task-definition ARN, baseline image
digest, and baseline desired count zero. The recovery-safety approval must
repeat that exact identity. This prevents approval for one release/baseline
from being reused for another.

The offline parser machine-verifies JSON shape, SHA syntax, equality of the
current authorization snapshot, release identity, and approved baseline
configuration. A GitHub URL prefix, an evidence SHA, or a content fingerprint
is only a required evidence *pointer*; it does **not** prove that an isolated
experiment occurred, that old-runtime forward-schema compatibility is true, or
that a named human reviewed the artifact. Those three claims remain distinct
independent human-review gates. GitHub `Main` Environment required-reviewer
protection remains a separate mandatory release authorization.

There remains an unavoidable interval after the final Main fetch/baseline read
and before `UpdateService`; ordinary Git and AWS CLI calls do not provide a
cross-system compare-and-swap. A new Main revocation or external ECS mutation
in that interval must be handled as an operational stop/incident condition,
not silently assumed away.

### Configuration baseline scope

For zero-capacity recovery, the approved Manifest snapshot binds the current
task-definition ARN, normalized `awsvpc` subnets/security groups/public-IP
setting, application-container/port-to-target-group bindings, deployment
controller type, launch type, scheduling strategy, and health-check grace
period. Network and list ordering are normalized, but each configured member
must match exactly. These fields materially affect whether a recovered task can
start and receive production traffic.

Dynamic values such as task ARNs, deployment IDs, timestamps, service events,
and current counts are not snapshot-equaled; they are checked through separate
safety invariants. All modes require Fargate, ECS controller, replica
scheduling, valid `awsvpc` configuration, and valid load-balancer bindings.
Only zero recovery requires the exact approved configuration snapshot because
it is the exceptional transition from no running workload.

### Deployed runtime identity

After ECS stability and before CD-01 writes a success summary, the workflow
uses read-only `list-tasks` and `describe-tasks` evidence. It requires exact
service and primary-deployment task definition, desired/running/pending counts,
one `RUNNING` application container per expected running task, and each
container's returned `imageDigest` equal to the approved runtime digest. It
also requires evidence for every configured ALB target group and a successful
read-only health endpoint. Missing task evidence, an old task definition,
mixed/incorrect digest, non-completed primary deployment, or non-healthy target
fails the workflow; it cannot produce a deployment-pass result.

AWS exposes `taskDefinitionArn` and per-container `imageDigest` in
`DescribeTasks`, which is why the check is based on actual task evidence rather
than only the service's intended task definition. [DescribeTasks API](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_DescribeTasks.html)

## Non-goals

This design does not change PR-06A/PR-06B behavior, automatically roll back,
run migrations beyond CD-01's read-only preflight, add an AWS lock, verify
CloudWatch delivery, or claim deployment safety. Those remain separately
approved operational activities.
