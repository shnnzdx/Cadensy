# PR-06B Release Closeout and AWS Resource Inventory

## Scope and authority

- **Inventory time:** 2026-10-20 CDT
- **AWS account / region:** `448678332746` / `us-east-1`
- **Inventory identity:** `arn:aws:iam::448678332746:user/github-actions-deploy`
- **Approved I1 cleanup identity:**
  `arn:aws:iam::448678332746:user/ProjectOwnerAdmin`

The initial release-closeout inventory used only AWS identity, describe/list,
metrics, pricing, and Cost Explorer APIs. A later, separately approved cleanup
changed only the I1 RDS deletion-protection setting and deleted that instance.
No KMS, ECS, ECR, EC2 security-group, SSM, log-retention, schema, or production
application resource was changed. No database row, secret value, connection
string, token, or application log payload is recorded in this document.

The production release evidence remains in
`19_PR06B_PRODUCTION_ACTIVATION_EXECUTION_RECORD.md`.

## Final release position

PR-06B Full-Stack Production Release and Runtime-off Browser Acceptance are
**PASS**. Production runs Backend TD23 and Frontend TD17. The database schema is
at `b7d3a1e9c642`. Real AI Provider execution, Durable Runtime, Fake Runtime,
additional deployment, and any further infrastructure cleanup remain unapproved.

## Approved post-closeout cleanup — I1 rehearsal RDS

On 2026-10-20 CDT, a separately approved cost-cleanup change removed only
`tripsync-postgres-pr06b-i1-20261019-01` in account `448678332746`, region
`us-east-1`. The execution identity was `ProjectOwnerAdmin`.

Deletion preflight confirmed all of the following:

- PR #23 was merged at main commit
  `b084d431092fc4f34c1eb77654b4861257ea3214`;
- the target was the I1 PostgreSQL 18.3 restore-rehearsal instance restored from
  `tripsync-postgres-i1-encrypted-20261019-01`, not the production instance;
- the production `DATABASE_URL` endpoint matched
  `tripsync-postgres-pr06b-prod-20261020-01` and did not match the I1 endpoint;
- an independent CloudWatch query returned 51 five-minute samples with an I1
  maximum of `0` connections, while the production database maximum was `5`;
- no RDS Proxy or read-replica dependency referenced the I1 instance;
- the retained I1 snapshot was `available`, 100% complete, encrypted with the
  production CMK, and matched the restored engine and storage contract; and
- Backend TD23 and Frontend TD17 were each `1/1/0`, both ALB targets were
  `healthy`, and `/`, `/login`, and `/api/health` returned HTTP 200.

The I1 rehearsal had been accepted as read-only compatibility validation and
had no production endpoint assignment or snapshot-external data requiring
retention. The operator then performed exactly these two state changes:

| CloudTrail event | Time (CDT) | Operation | Result |
| --- | --- | --- | --- |
| `a651f028-953d-4b20-b097-e49a02ef03a1` | `2026-10-20 19:15:46` | `ModifyDBInstance` with deletion protection disabled and immediate application | Target returned `available` with deletion protection `false`. |
| `2b63e7f2-22b6-45aa-9d6c-e90e5e1800eb` | `2026-10-20 19:15:51` | `DeleteDBInstance` with `skipFinalSnapshot=true` and `deleteAutomatedBackups=false` | Target entered `deleting` and subsequently returned `DBInstanceNotFound`. |

No final snapshot was created. The existing automated backup record was left in
`retained` state to expire through the RDS lifecycle. The five manual snapshots
were unchanged, including the required encrypted I1 recovery snapshot. No other
RDS instance, ECS Service, Task Definition, ECR image, security group, SSM
parameter, schema, CMK, or runtime configuration was changed.

## RDS inventory and classification

The initial inventory contained three PostgreSQL `18.3`, `db.t4g.micro`,
Single-AZ, private instances with 20 GiB gp3 storage. After the approved I1
cleanup, the production and isolated legacy instances remain.

| Resource | Observed state | Classification | Closeout decision |
| --- | --- | --- | --- |
| `tripsync-postgres-pr06b-prod-20261020-01` | `available`; encrypted; CMK `82952d7c-8a33-4d3e-b4cd-00ee6c1ebb71`; 7-day backups; deletion protection enabled; current RDS SG; latest sampled connections `5` | **Current production — must retain** | Do not stop, delete, detach its SG, change its KMS dependency, or reduce recovery settings. |
| `tripsync-postgres` | `available`; unencrypted; 1-day backups; deletion protection disabled; attached to `tripsync-rds-source-retained-sg`, which has no ingress; latest sampled connections `0` | **Temporary retained pre-I2 source** | Retain through the separately confirmed post-cutover period and consistency sign-off. Earliest review is approximately 2026-10-27 CDT; there is no automatic deletion approval. |
| `tripsync-postgres-pr06b-i1-20261019-01` | **Deleted 2026-10-20 CDT**; final `describe-db-instances` returned `DBInstanceNotFound` | **Completed I1 cleanup** | The encrypted rehearsal snapshot remains available. No further action is authorized for its snapshots or automated backups. |

The backend ECS service uses `sg-0185eb0590f96c27d`. The current RDS security
group `sg-0bc74bd5a6f5c2728` permits PostgreSQL only from that backend group. The
retained legacy database uses isolated group `sg-0f3bcb394f10b1395`, which had
no ingress at inventory time. The deletion preflight connection query showed an
I1 maximum of `0` and a production maximum of `5`. A protected endpoint-only
comparison also confirmed that the production database parameter referenced
the encrypted I2 database, not the I1 instance. The I1 instance no longer
exists.

## Snapshot and recovery evidence

Five manual snapshots were `available`:

| Snapshot | Encryption | KMS | Classification |
| --- | --- | --- | --- |
| `tripsync-postgres-i1-source-20261019-01` | No | — | I1 source evidence; future cleanup candidate after retention review. |
| `tripsync-postgres-i1-encrypted-20261019-01` | Yes | PR-06B CMK | **Recommended recovery-evidence retention** for the accepted I1 rehearsal. |
| `tripsync-postgres-i2-final-20261020-01` | No | — | I2 final source evidence; retain with the old source until the rollback-retention decision closes. |
| `tripsync-postgres-i2-final-encrypted-20261020-01` | Yes | PR-06B CMK | **Recommended recovery-evidence retention**; this is the encrypted final cutover snapshot. |
| `tripsync-postgres-r5-20261014-020428` | No | — | Pre-I1 historical migration snapshot; future cleanup candidate after evidence/retention review. |

Automated backups existed for all three database identities at inventory time.
The approved I1 deletion used `deleteAutomatedBackups=false`; its backup record
was observed as `retained` with 20 GiB logical allocation after the instance was
gone. It should expire through the RDS lifecycle. The legacy and production
backups remain governed by their 1-day and 7-day instance retention settings.
No manual backup deletion is authorized by this record.

Snapshot `AllocatedStorage` is the logical source size, not the billable
incremental snapshot size. The cost section therefore presents a conservative
logical ceiling and does not claim that five 20 GiB snapshots are all billed as
five independent full copies.

## KMS inventory and mandatory retention

| Field | Observed value |
| --- | --- |
| Alias | `alias/tripsync/pr06b-rds-recovery` |
| Key ARN | `arn:aws:kms:us-east-1:448678332746:key/82952d7c-8a33-4d3e-b4cd-00ee6c1ebb71` |
| Manager / origin | Customer-managed / AWS KMS |
| State | `Enabled` |
| Rotation | Enabled; 365-day period; next rotation recorded for 2027-10-19 |
| Current dependents | Production encrypted RDS, encrypted manual snapshots, production automated backups, and the retained I1 automated backup |

**This key is a current production dependency and must be retained enabled.**
Do not disable it, schedule deletion, replace its policy, or remove RDS access
as part of cleanup. Snapshot retention decisions do not remove the production
database's dependency on the key.

## ECS Task Definition and one-off resource inventory

Only two ECS tasks were running at inventory time:

- `tripsync-backend:23` — production Backend, approved runtime digest
  `sha256:ba76821c460de90e2dbc3491fb8d3c7b23e3184db2a34e131186084f01f8b602`;
- `tripsync-frontend:17` — production Frontend, approved digest
  `sha256:f78297e5bbabd14e469da9e022558603e078389d11ba0b7ebfba6c06bad171ec`.

Relevant retained definitions:

| Definitions | Purpose | Cost / recommendation |
| --- | --- | --- |
| `tripsync-backend:23`, `tripsync-frontend:17` | Current production | Must retain. |
| `tripsync-backend:20`, `tripsync-frontend:16` | Cold-start-verified Backend rollback and prior Frontend rollback | Retain through the defined rollback/evidence period together with their immutable ECR digests. |
| `tripsync-backend:21` | Gate M migration runner | No running task and no Task Definition charge; retain while Gate M evidence is active, then deregister only under separate housekeeping approval. |
| `tripsync-backend:22` | Gate R read-only schema preflight | Same evidence-retention treatment as TD21. |
| `tripsync-backend-db-admin:1`–`:8` | Historical demo-login, demo-seed, fixed-account, and demo-purge one-off tasks | No running task and no Task Definition charge; future hygiene candidates after confirming no runbook still names them. |
| `tripsync-backend-r5-migration:1` | Historical R5 migration task | No running task and no Task Definition charge; future hygiene candidate after evidence retention. |
| Older active Backend `:1`–`:19` and Frontend `:1`–`:15` revisions | Historical service definitions | No direct charge. Deregistration reduces clutter, not the AWS bill; preserve any revision still named by rollback/audit records. |

Task Definition deregistration and stopped-task cleanup provide no material cost
savings. They should follow, not precede, rollback and audit-retention closure.

## ECR and CloudWatch inventory

| Resource | Observed state | Closeout position |
| --- | --- | --- |
| `tripsync-backend` ECR | 48 image details; 12 untagged; summed manifest-reported size about 3.295 GiB | Existing policy expires untagged images after 7 days. Keep the production, rollback, and executed migration digests. Review old tagged demo/migration images later. |
| `tripsync-frontend` ECR | 25 image details; 6 untagged; summed manifest-reported size about 9.006 GiB | Same 7-day untagged policy. Keep TD17 and TD16 digests. Review older tagged images later. |
| `/ecs/tripsync-backend` | About 1.55 MB stored; 7-day retention | Retain normal expiry; the log group supports current production and release evidence. |
| `/ecs/tripsync-frontend` | About 0.05 MB stored; 7-day retention | Retain normal expiry. |

The ECR size sum can overstate billable storage because image details can share
layers. Cost Explorer is the billing source of truth. Tagged historical images
are not affected by the present untagged-only lifecycle policies.

## Cost evidence and deterministic estimate

Cost Explorer used `UnblendedCost` and excluded Credits and Refunds. All totals
and projections below were calculated by Python using the returned API values
and AWS Price List rates; none were added manually.

### Actual account cost

| Period | Actual / estimated charge |
| --- | ---: |
| 2026-09-01 through 2026-09-30 | `$62.8087` |
| 2026-10-01 through 2026-10-20, incomplete MTD | `$33.6896` |
| October MTD RDS portion | `$6.9846` (`$5.5699` instance, `$1.4147` gp3) |

The completed September account cost was led by ECS `$17.7742`, ELB
`$16.2318`, VPC `$14.4098`, and RDS `$13.8200`. It is a useful pre-I1/I2
baseline, not a guarantee of a future invoice.

### Current AWS public on-demand rates and monthly planning values

| Component | Rate / calculated monthly value |
| --- | ---: |
| RDS `db.t4g.micro` Single-AZ PostgreSQL | `$0.016/hour`; `$11.68/month` at 730 hours |
| RDS gp3 | `$0.115/GiB-month`; `$2.30/month` for 20 GiB |
| One current-size RDS total | approximately `$13.98/month` |
| Three running RDS instances before I1 cleanup | approximately `$41.94/month` |
| Two running RDS instances after I1 cleanup | approximately `$27.96/month` |
| Remaining extra legacy RDS instance | approximately `$13.98/month` above the required production instance |
| Customer-managed KMS key | `$1.00/month` plus request charges |
| PostgreSQL snapshot storage over free allocation | `$0.095/GiB-month` |
| Five manual snapshots at a deliberately conservative full logical-size ceiling | up to `$9.50/month`; actual incremental billing may be lower |
| ECR storage | `$0.10/GiB-month`; manifest-size indicator is about 12.301 GiB / `$1.23/month`, while actual September ECR cost was `$0.5729` |
| Active Task Definition registrations | `$0`; only running Fargate tasks incur compute cost |

Before cleanup, using the completed September account cost as the existing
one-RDS production baseline, the two extra RDS instances and CMK produced a
planning band of `$91.77–$101.93/month`. Removing the I1 instance eliminates
approximately `$11.68/month` of instance time plus `$2.30/month` of gp3 storage,
for an estimated ongoing saving of **`$13.98/month`**. The updated planning band
is therefore **`$77.79–$87.95/month`** after the retained automated backup
expires. These figures were recalculated deterministically from the documented
public rates.

No final snapshot was added. The retained automated backup has 20 GiB logical
allocated storage and may create a temporary incremental backup charge until
expiration. At the public `$0.095/GiB-month` rate, a deliberately conservative
full-logical-size ceiling is `$1.90` for a full month or about `$0.44` prorated
for seven days; actual incremental billing may be lower. The updated planning
band remains guidance rather than an AWS forecast or invoice and excludes
unexpected traffic, burst CPU credits, data transfer, future log growth, taxes,
and Provider use.

## Minimum-risk future cleanup order

No step below is authorized by this document.

1. **Retain without change:** current encrypted production RDS, its SG and
   backups, the PR-06B CMK, TD23, TD17, their immutable images, and the HMAC and
   database secret references.
2. **Close the rollback retention window:** confirm the I2 cutover date,
   production data consistency, and whether the old unencrypted database is
   still required. Do not retire it before the separately approved retention
   date and final recovery decision.
3. **Completed — I1 live rehearsal database:** the separately approved change
   deleted only `tripsync-postgres-pr06b-i1-20261019-01`; the encrypted rehearsal
   snapshot remains available and the automated backup is retained for normal
   lifecycle expiry.
4. **Retire the isolated legacy database later:** after the old-source
   retention window and explicit data-owner approval, preserve the required
   final snapshot evidence and separately authorize deletion of only
   `tripsync-postgres`.
5. **Prune redundant snapshots:** retain at minimum the chosen I1 encrypted
   rehearsal evidence and I2 encrypted final snapshot. Review unencrypted
   source copies and the R5 snapshot only after database-retirement and audit
   requirements close.
6. **Housekeeping after rollback closure:** deregister obsolete Task Definition
   revisions and remove old tagged ECR images only after mapping every retained
   rollback/audit digest. This is mainly hygiene; ECR savings are small and Task
   Definition deregistration saves no money.
7. **Never include the active CMK in cleanup:** it remains required by the
   production database and encrypted recovery points.

## Remaining non-blocking items and suggested next tasks

- Track the rapid Plan-to-Chat Leaflet teardown observation; it did not
  reproduce under normal render waits and does not block this release.
- Track the three non-fatal vinext static-file `Premature close` lines observed
  during synthetic navigation.
- Retain the historical Gate M `state=skipped` approval exception and
  maintenance-rule timing gap as process-improvement items; do not rerun the
  successful migration solely for audit cosmetics.
- If desired, schedule a separate **AWS Cleanup Change** after the old-source
  retention date for the isolated legacy RDS. This completed change grants no
  authority to delete it, any snapshot, ECR image, or Task Definition.
- Schedule a separate **Provider and Durable Runtime readiness gate** only if a
  real-model production experience is desired. It is not part of this closeout.

## Closeout recommendation

**PR-06B RELEASE CLOSEOUT: PASS.** The application release, schema migration,
Backend/Frontend promotion, and Runtime-off browser acceptance are complete and
documented. The first separately authorized cleanup removed the unused I1 live
RDS while preserving the encrypted recovery evidence and production health.
The remaining issues are non-blocking operational observations and separately
authorized cleanup or future-runtime work.
