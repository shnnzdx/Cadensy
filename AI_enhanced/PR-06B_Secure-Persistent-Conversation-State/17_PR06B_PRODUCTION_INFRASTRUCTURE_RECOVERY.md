# PR-06B — Production Infrastructure Recovery & Encryption Readiness

Status: **ROUND 1.5 ENGINEERING — IMPLEMENTATION-READY; LIVE REMEDIATION NOT EXECUTED.**

This document is a recovery design and operator runbook, not evidence of a production recovery. Every future live operation below is **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED IN THIS TASK.** No RDS, ECS, secret, database, Provider, or deployment mutation occurred in Round 1.5.

## 1. Executive summary

PR-06B code, migration/release contracts, and Gate A are complete. Production cannot yet use Gate M or Gate R safely because the current production database is unencrypted and stopped, its visible backups are unencrypted and stale, the backend service is intentionally at zero capacity, and task definition revision 19 has no `CONVERSATION_SUBMISSION_HMAC_KEY` reference.

The supported remediation is a controlled maintenance cutover, not an in-place encryption edit and not an automatic deployment:

1. Under Gate I1, create a fresh source snapshot only after an approved write freeze, copy it with an enabled customer-managed KMS key, and restore a distinct private encrypted RDS PostgreSQL instance.
2. Prove restore/data/schema/application compatibility against that replacement and retain a restore-rehearsal record before endpoint cutover.
3. Under Gate I2, switch only the controlled `DATABASE_URL` reference to the replacement after a new-write decision point and preserve the source for the approved retention period.
4. Under Gate I3, create a safe replacement task definition with a dedicated HMAC reference, every PR-06B runtime/fake-runtime gate set to `0`, scheduler disabled, and public traffic blocked by an approved maintenance control before restoring ECS capacity.
5. Only then use the already-approved, independent Gate M and Gate R workflows. They remain unchanged and cannot be implied by I1/I2/I3.

The approach is based on the AWS rule that RDS storage encryption cannot be enabled in place: an encrypted copy of an unencrypted DB snapshot must be restored as a new DB instance. AWS also states that a snapshot is not a restore rehearsal and a restored instance continues background lazy loading. [AWS RDS encryption](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html), [AWS RDS snapshot restore](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html).

## 2. Evidence classification and source state

| Classification | Meaning in this report |
| --- | --- |
| **SOURCE VERIFIED** | Reviewed repository source at `origin/main` merge `68d2511261573284e4d163228f6932a38ff853bb`. |
| **CURRENT AWS VERIFIED** | Read-only AWS CLI metadata collected on 2026-10-19 in account `448678332746`, region `us-east-1`. |
| **HISTORICAL ONLY** | Prior Round 1 observations; never used as a current assertion. |
| **LOCALLY TESTED** | Offline validator/collector tests; not cloud restore, network, or database evidence. |
| **PROPOSED** | A future authorized infrastructure change. |
| **UNVERIFIED** | A required fact not established by this task. |
| **BLOCKED** | A condition that prevents the next live gate. |

The current main commit is `68d2511261573284e4d163228f6932a38ff853bb` (PR #18 merge). Round 1 Gate M and Gate R retain immutable-image, source-SHA, exact-Alembic-head, approval, runtime-off, and no-automatic-downgrade contracts.

## 3. Current AWS read-only inventory

The new collector ran `aws --version` (CLI v2) and `sts get-caller-identity`, then only its static `describe`, `list`, or identity-operation allow-list. It did not retrieve parameter/secret values, connect to PostgreSQL, or issue a mutation.

| Area | Current AWS verified observation | Consequence |
| --- | --- | --- |
| Identity | Account `448678332746`, authenticated IAM user `github-actions-deploy`; `us-east-1`. | Correct account/region only; not an I1/I2/I3 approval. |
| ECS backend | `tripsync-backend-service` is `ACTIVE`, Fargate, but `desiredCount=0`, `runningCount=0`, `pendingCount=0`; circuit breaker is enabled with rollback and `100/200` bounds. | **BLOCKED:** Gate M/R capacity preconditions intentionally fail. Do not remove them. |
| Network/ALB | Backend service uses two ECS subnets, backend SG, and an HTTP `/api/health` target group. Target health is empty. | No live health or public-traffic safety proof exists. |
| Task definition | Revision 19, `awsvpc`, Fargate, scheduler disabled, existing `DATABASE_URL` SSM reference; no `CONVERSATION_SUBMISSION_HMAC_KEY` secret name. | **BLOCKED:** CD-01 helper must reject it. A future I3 revision must add the dedicated reference. |
| Runtime posture | Current revision contains real-provider configuration and has no explicit PR-06B durable/fake runtime entries. | I3 must use the CD-01 helper against a reviewed image and explicitly pin protected/durable/fake gates to `0`; health alone is insufficient. |
| RDS | `tripsync-postgres` is PostgreSQL `18.3`, private, `stopped`, single-AZ, `StorageEncrypted=false`, backup retention `1`, default `postgres18` parameter group, private two-subnet group. | **BLOCKED:** violates storage/encrypted-backup contract; current retention has no approved RPO. |
| RDS snapshots | Visible automated/manual source snapshots are available but `Encrypted=false`; newest visible source snapshot is from 2026-10-14. | **BLOCKED:** neither freshness nor encrypted recovery nor restore success is demonstrated. Snapshot existence is not recovery evidence. |
| KMS | Only service aliases were observed; no selected/enabled customer-managed replacement key was supplied. | **BLOCKED:** Gate I1 needs a specific KMS key, key policy and grant review. |
| SSM metadata | Existing production entries are `SecureString`, including database URL; no HMAC parameter reference was present in the observed path. Values were not read. | I3 needs a dedicated HMAC reference and execution-role permission review without reading it. |
| IAM metadata | Task role has no visible attached/inline policies. Execution role has the standard ECS execution policy plus an inline runtime-parameter policy. | **UNVERIFIED:** least-privilege and access to the new HMAC reference require policy-resource review by the authorized owner. |
| Observability | `/ecs/tripsync-backend` exists with 7-day log retention; no CloudWatch alarms or relevant EventBridge rules were returned by the collector. | **BLOCKED/UNVERIFIED:** define recovery monitoring, alerting, and purge/startup scheduling ownership before accepting production traffic. |
| ECR | Digest-pinned runtime and migration images for Round 1 merge are present. | Artifact availability is not authority to migrate or deploy. |

The current RDS automatic-restart timestamp and prior service events are metadata, not authorization to let either resource start. No interpretation of the stopped instance as an approved recovery was made.

## 4. Recovery architecture and acceptance rules

### 4.1 RDS encryption replacement

RDS encryption is create-time only. The future I1 operator must not attempt an in-place encryption update. The approved candidate is snapshot-copy-and-restore because present evidence is a stopped source and there is no evidence of active writes. If the I1 inventory discovers writes, a required RPO/RTO that cannot tolerate the outage, or an unacceptable source snapshot age, stop and return for architecture approval; do not introduce DMS/logical replication by default.

Proposed DB checkpoints:

| Checkpoint | Required evidence | State in this task |
| --- | --- | --- |
| DB-0 | Source identity, endpoint/configuration metadata, write-freeze policy, RPO/RTO owner. | **CURRENT AWS VERIFIED / UNVERIFIED** — identity/config is known; policy/RPO/RTO absent. |
| DB-1 | Fresh source snapshot, encrypted copy using approved KMS key, completed restore rehearsal matching declared RPO/RTO. | **BLOCKED**. |
| DB-2 | Separate private encrypted PostgreSQL 18.3 replacement with approved subnet/SG/parameter configuration, retention and KMS evidence. | **PROPOSED**. |
| DB-3 | Restore data integrity, extension/role/sequence/index/constraint and application compatibility evidence. | **UNVERIFIED**. |
| DB-4 | Gate I2 approval binds exact source/replacement IDs, write freeze, secret-reference change, and rollback decision. | **PROPOSED**. |
| DB-5 | Approved application connectivity, schema and operational verification after cutover. | **UNVERIFIED**. |
| DB-6 | Source retained and access-protected until an explicit retention/decommission decision. | **PROPOSED**. |

The replacement must match source engine/version (`postgres`/`18.3`) initially. Any engine upgrade, class/storage/Multi-AZ change, custom parameter group change, or extension change is a separately reviewed compatibility decision. Restore does not prove application roles, extension behavior, sequence state, data correctness, TLS behavior, or user permissions; DB-3 must prove those on an approved restored environment.

### 4.2 Backup, restore, KMS and TLS rules

- A source snapshot is only a candidate input. An **encrypted copied snapshot** naming the approved KMS key is required before the encrypted-backup gate can pass.
- A snapshot reference alone never satisfies DB-1. A timestamped restore rehearsal must record source/copy identifiers, target identifier, KMS key, elapsed restore and verification duration, checks run, RPO/RTO result, and an approver reference.
- A customer-managed KMS key must be `Enabled`, have a named key administrator and recovery owner, rotation/recovery policy, and a key policy/grant path that permits RDS snapshot copy/restore without a blanket principal. Do not disable the key: RDS availability depends on it.
- Preserve private subnet group/SG topology unless a reviewed network change says otherwise. Verify replacement DB ingress only from the intended backend identity and document why broad egress is acceptable or remediate it separately.
- TLS must be verified by an actual approved connection/rehearsal using the current RDS CA. Metadata showing `rds-ca-rsa2048-g1` is not proof that application connections enforce certificate verification.
- Choose and approve a numeric backup retention minimum based on declared RPO; the current `1` day is an observation, not an approved target. The validator rejects a replacement below the explicitly supplied minimum.

## 5. Data consistency and rollback boundaries

The I2 change has a hard new-write boundary:

1. Before replacement receives writes, an approved endpoint rollback to the original source can be considered only after confirming the new endpoint never accepted writes.
2. After replacement receives writes, switching `DATABASE_URL` back to source can lose those writes. It is **not** a safe rollback. Stop, preserve both databases, choose reconciliation/roll-forward under DBA authority, and do not promise zero loss.
3. Database restore, application rollback, and Alembic recovery are separate decisions. ECS circuit breaker protects deployment state, not database consistency or secret changes.
4. No automatic deletion, database downgrade, snapshot deletion, source retirement, or secret rotation is permitted.

## 6. HMAC secret provisioning design

The future I3 owner must provision one dedicated cryptographically strong HMAC secret for `CONVERSATION_SUBMISSION_HMAC_KEY`. It must not reuse `DATABASE_URL`, a Provider key, or a current conversation key. The value must never appear in Git, task-definition JSON, CI logs, this report, or agent context.

Required future procedure:

1. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Secret owner creates an approved `SecureString`/Secrets Manager secret under the production architecture, encrypted by an approved KMS key and with documented rotation/recovery owner.
2. Owner grants only the ECS **execution role** the exact SSM/Secrets Manager read and KMS decrypt permissions required for that reference. The task role does not receive it unless application code genuinely needs AWS API access.
3. Operator records reference ARN/name and policy-review evidence without retrieving its value.
4. I3 creates a new task-definition revision that adds only the reference; CD-01 helper rejects plaintext injection or a missing HMAC reference.
5. Launch-time validation is a controlled task-start/health observation, not an attempt to print the value. A reference existing in metadata does not prove it can be resolved by ECS.

## 7. ECS zero-capacity bootstrap decision

| Option | Assessment |
| --- | --- |
| A — Recover existing legacy service directly | Rejected. Revision 19 lacks HMAC reference and has non-explicit PR-06B runtime posture; its public ALB attachment makes unreviewed recovery unsafe. |
| B — New isolated migration mechanism | Not selected for Round 1.5. It would require a new production execution path and an independent approval/attestation design. Do not weaken Gate M's nonzero-service precondition to simulate this. |
| C — Planned maintenance cutover | **Recommended.** Gate I3 first establishes a reviewed, safe task definition and maintenance traffic control, then restores only the minimum service capacity required for Gate M/R capacity and health checks. It retains the existing circuit breaker and does not make Gate M/R implicit. |

The maintenance control must block ordinary public traffic before capacity is restored, while preserving a tightly scoped authorized health-verification route if required. Its exact ALB/DNS/WAF implementation is intentionally **UNVERIFIED** and requires I3 review; no public application traffic may be treated as acceptable merely because an ECS task is healthy.

## 8. Dependency graph and execution order

```text
I1: fresh source snapshot + KMS-encrypted copy + restore rehearsal
  -> DB-2 encrypted private replacement
  -> DB-3 integrity / TLS / compatibility evidence
  -> I2: write freeze + DATABASE_URL cutover approval
  -> I3: HMAC reference + role permission + maintenance traffic block
  -> safe task-definition revision; runtime/fake runtime/scheduler off
  -> desired=1, running=1, ALB health (maintenance-only)
  -> Gate M: independently approved PR-06B schema migration
  -> Gate R: independently approved digest-pinned application promotion
  -> runtime-off functional verification -> Round 2 / Gate B review
```

| Transition | Prerequisite / actor | Future operation and evidence | Abort/recovery / traffic / cost |
| --- | --- | --- | --- |
| I1 / DB-1 | DBA + security + cost owner; DB-0, RPO/RTO, write-freeze approved | Create fresh snapshot, encrypted copy, restore a separate DB; capture KMS/restore logs and rehearsal checks. | Abort if snapshot age, KMS, restore, TLS, integrity or RPO/RTO fails. No public traffic. Moderate temporary RDS/snapshot/KMS cost. |
| I2 / DB-4 | DBA accepts DB-3; exact IDs and new-write boundary documented | Change only approved `DATABASE_URL` reference under write freeze. | Before writes, endpoint rollback only after proof. After writes, preserve both, reconcile/roll forward. No general traffic. Low configuration cost. |
| I3 | ECS/security owner accepts I2 and role/reference review | Add HMAC reference in a new safe task revision; install maintenance traffic control; restore min capacity and check health. | Abort if secret launch resolution/network/health fails; scale back only under approval. Maintenance-only traffic. Fargate/ALB cost begins. |
| Gate M | Existing protected approver; source SHA/image/start revision/capacity preflight | Run existing migration runner exactly once. | Existing checkpoint/recovery policy; no retry/stamp/downgrade. Maintenance-only traffic. |
| Gate R | Separate protected approver; successful Gate M + catalog/API prerequisites | Promote immutable runtime digest with circuit breaker. | Circuit breaker only handles ECS deployment; DB/secret rollback follows I2 rules. Traffic admission requires explicit go/no-go. |

Timeouts must be approved per operation: use AWS service waiters plus a change-window deadline, rather than assuming fixed snapshot/restore time. The Gate M 900-second runner/35-minute workflow limit applies only after I3; it is not an I1/I2 timeout.

## 9. New local safety tooling

| File | Purpose |
| --- | --- |
| `scripts/cd01/collect_pr06b_production_inventory.py` | Redacted read-only AWS CLI collector. Its static allow-list contains only identity/describe/list operations; it records authorization errors instead of inventing PASS and omits environment values and `valueFrom` secret references. |
| `scripts/cd01/validate_pr06b_infrastructure_recovery.py` | Offline fail-closed manifest validator. It rejects wrong account/region, ambiguous source/replacement identity, unencrypted target/evidence, stale/no restore rehearsal, missing KMS, HMAC/DB secret issues, runtime-on posture, automatic cutover and unapproved/public bootstrap. |
| `scripts/cd01/fixtures/pr06b-infrastructure-recovery.example.json` | Fictional, non-production local rehearsal manifest; not an authorization artifact. |
| `backend/tests/test_pr06b_infrastructure_recovery.py` | 22 offline regressions for the above contracts. |

Example local-only rehearsal:

```powershell
backend\.venv\Scripts\python.exe scripts\cd01\validate_pr06b_infrastructure_recovery.py `
  --manifest scripts\cd01\fixtures\pr06b-infrastructure-recovery.example.json `
  --now 2026-10-19T12:00:00Z
```

The inventory collector may be run only by an authorized operator with AWS CLI credentials. It has no write-capable code path. Its resulting file should be stored outside Git unless independently reviewed for sensitive metadata.

## 10. Future production operator runbook

### Gate I1 — Infrastructure Recovery Authorization

Approve cost, RPO/RTO, source ID, write-freeze ownership, encrypted-target naming, customer-managed KMS key/policy, subnet/SG/parameter settings, backup retention, and source retention. Then, and only then:

1. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Re-run read-only inventory; fail if account/region/source differs.
2. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Create a fresh source snapshot after write freeze; copy it with the approved KMS key; restore a distinct private replacement.
3. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Verify `StorageEncrypted`, KMS key, backup retention, engine/version, parameter/subnet/SG, and no public exposure.
4. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Perform documented restore rehearsal and DB-3 integrity/TLS/compatibility checks. Stop on any failed check.

### Gate I2 — Database Cutover Authorization

Approve exact source/replacement IDs, DB-3 evidence, maintenance period, write-freeze, `DATABASE_URL` reference change, post-write rollback decision, and DB-6 retention. Then:

1. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Apply the secret-reference endpoint cutover without exposing values.
2. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Validate authorized application connectivity and record whether any replacement writes occurred.
3. Preserve source; do not delete it or claim endpoint rollback remains safe after writes.

### Gate I3 — ECS Bootstrap Authorization

Approve maintenance traffic block, new HMAC reference, execution-role resource permissions, old-image compatibility decision, target capacity, health timeout, and public-traffic go/no-go. Then:

1. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Register a new task definition from the reviewed helper with `DATABASE_URL` and HMAC references, scheduler disabled, all protected gates `0`, durable runtime `0`, and fake runtime `0`.
2. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Restore minimum ECS capacity only while the approved maintenance control prevents normal traffic.
3. **REQUIRES SEPARATE EXPLICIT AUTHORIZATION — NOT EXECUTED:** Verify task startup, health, log/error signals, ALB target registration, secret-resolution behavior, and no Provider invocation. Abort on any error; circuit breaker does not repair database/secret mistakes.

### Gate M and Gate R

Gate M and Gate R are unchanged separate approvals. Supply only images built for the exact approved source SHA. Gate M requires the exact preflighted Alembic start revision and then verifies `b7d3a1e9c642`; Gate R requires the successful same-SHA Gate M run and catalog proof. Neither gate creates an RDS replacement, changes a secret, or makes the runtime active.

## 11. Remaining blockers and handoff

1. Gate I1: no approved RPO/RTO/retention target, customer-managed KMS key, fresh encrypted snapshot, or restore rehearsal.
2. Gate I2: no DB-3 integrity/TLS/application compatibility evidence and no approved new-write/rollback decision.
3. Gate I3: no HMAC secret reference, no reviewed execution-role resource permission proof, no maintenance traffic-control design, no approved safe bootstrap task revision.
4. Gate M/R: zero ECS capacity and stopped/unverified replacement database block their existing preconditions.
5. Gate B: Provider authorization/cost, active runtime/recovery behavior, telemetry and alerting, purge scheduling, SLO/incident ownership, least-privilege completion, encrypted-backup lifecycle and live TLS proof remain open.

## 12. Validation and final status

| Check | Result |
| --- | --- |
| Current read-only AWS inventory | **CURRENT AWS VERIFIED** — no mutation or secret-value retrieval. |
| `backend/tests/test_pr06b_infrastructure_recovery.py -q` | **LOCALLY TESTED** — 22 passed. |
| Static source inspection | **SOURCE VERIFIED** — collector has a read-only allow-list; existing Gate M/R protections unchanged. |
| Real RDS encrypted copy/restore, ECS bootstrap, Gate M/R dispatch, Provider invocation | **NOT EXECUTED / UNVERIFIED**. |

Gate status:

- Gate A: **PASS — CLOSED**.
- Round 1 Release Engineering: **COMPLETE**.
- Round 1.5 Infrastructure Engineering: **PASS — engineering-ready only**.
- Production Infrastructure Remediation: **NOT EXECUTED**.
- Gate B: **NOT APPROVED**.

Round 2 must not start from an assumed active Runtime. It begins only after separately authorized I1/I2/I3/M/R evidence and a new Gate B review.
