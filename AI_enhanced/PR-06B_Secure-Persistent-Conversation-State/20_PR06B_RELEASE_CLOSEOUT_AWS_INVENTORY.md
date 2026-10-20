# PR-06B Release Closeout and AWS Resource Inventory

## Scope and authority

- **Inventory time:** 2026-10-20 CDT
- **AWS account / region:** `448678332746` / `us-east-1`
- **Execution identity:** `arn:aws:iam::448678332746:user/github-actions-deploy`

This is a read-only release-closeout record. The operator used only AWS
identity, describe/list, metrics, pricing, and Cost Explorer APIs. No RDS, KMS,
ECS, ECR, EC2 security-group, SSM, log-retention, or production application
resource was created, modified, stopped, disabled, deregistered, or deleted.
No database row, secret value, connection string, token, or application log
payload was retrieved.

The production release evidence remains in
`19_PR06B_PRODUCTION_ACTIVATION_EXECUTION_RECORD.md`.

## Final release position

PR-06B Full-Stack Production Release and Runtime-off Browser Acceptance are
**PASS**. Production runs Backend TD23 and Frontend TD17. The database schema is
at `b7d3a1e9c642`. Real AI Provider execution, Durable Runtime, Fake Runtime,
additional deployment, and infrastructure cleanup remain unapproved.

## RDS inventory and classification

All three instances are PostgreSQL `18.3`, `db.t4g.micro`, Single-AZ, private,
and use 20 GiB gp3 storage in `tripsync-private-db-subnet-group`.

| Resource | Observed state | Classification | Closeout decision |
| --- | --- | --- | --- |
| `tripsync-postgres-pr06b-prod-20261020-01` | `available`; encrypted; CMK `82952d7c-8a33-4d3e-b4cd-00ee6c1ebb71`; 7-day backups; deletion protection enabled; current RDS SG; latest sampled connections `5` | **Current production — must retain** | Do not stop, delete, detach its SG, change its KMS dependency, or reduce recovery settings. |
| `tripsync-postgres` | `available`; unencrypted; 1-day backups; deletion protection disabled; attached to `tripsync-rds-source-retained-sg`, which has no ingress; latest sampled connections `0` | **Temporary retained pre-I2 source** | Retain through the separately confirmed post-cutover period and consistency sign-off. Earliest review is approximately 2026-10-27 CDT; there is no automatic deletion approval. |
| `tripsync-postgres-pr06b-i1-20261019-01` | `available`; encrypted with the production CMK; 7-day backups; deletion protection enabled; latest sampled connections `0` | **I1 restore-rehearsal instance** | Recovery evidence is complete. It is a future cleanup candidate after the evidence PR merges and an encrypted recovery snapshot is explicitly selected for retention. |

The backend ECS service uses `sg-0185eb0590f96c27d`. The current RDS security
group `sg-0bc74bd5a6f5c2728` permits PostgreSQL only from that backend group. The
retained legacy database uses isolated group `sg-0f3bcb394f10b1395`, which had
no ingress at inventory time. Three hours of RDS `DatabaseConnections` metrics
showed maximums of `0`, `0`, and `5` for the legacy, I1, and production
instances respectively. These facts corroborate, without reading a secret,
that the encrypted I2 database is the active production target.

## Snapshot and recovery evidence

Five manual snapshots were `available`:

| Snapshot | Encryption | KMS | Classification |
| --- | --- | --- | --- |
| `tripsync-postgres-i1-source-20261019-01` | No | — | I1 source evidence; future cleanup candidate after retention review. |
| `tripsync-postgres-i1-encrypted-20261019-01` | Yes | PR-06B CMK | **Recommended recovery-evidence retention** for the accepted I1 rehearsal. |
| `tripsync-postgres-i2-final-20261020-01` | No | — | I2 final source evidence; retain with the old source until the rollback-retention decision closes. |
| `tripsync-postgres-i2-final-encrypted-20261020-01` | Yes | PR-06B CMK | **Recommended recovery-evidence retention**; this is the encrypted final cutover snapshot. |
| `tripsync-postgres-r5-20261014-020428` | No | — | Pre-I1 historical migration snapshot; future cleanup candidate after evidence/retention review. |

Automated snapshots also existed for all three database identities. They remain
governed by the instances' 1-day or 7-day backup-retention settings and should
be allowed to expire through the normal RDS lifecycle. No manual attempt to
delete them is recommended in this closeout.

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
| Current dependents | Production encrypted RDS, I1 RDS, encrypted manual snapshots, encrypted automated backups |

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
| Three running RDS instances | approximately `$41.94/month` |
| Extra legacy + I1 RDS instances | approximately `$27.96/month` above the required production instance |
| Customer-managed KMS key | `$1.00/month` plus request charges |
| PostgreSQL snapshot storage over free allocation | `$0.095/GiB-month` |
| Five manual snapshots at a deliberately conservative full logical-size ceiling | up to `$9.50/month`; actual incremental billing may be lower |
| ECR storage | `$0.10/GiB-month`; manifest-size indicator is about 12.301 GiB / `$1.23/month`, while actual September ECR cost was `$0.5729` |
| Active Task Definition registrations | `$0`; only running Fargate tasks incur compute cost |

Using the completed September account cost as the existing one-RDS production
baseline, retaining two extra RDS instances and the new CMK produces a planning
run rate of about `$91.77/month` before snapshot and ECR variation. Adding the
conservative full-size manual-snapshot ceiling and current ECR size delta gives
an intentionally cautious upper planning figure of about `$101.93/month`.
Treat `$91.77–$101.93/month` as a closeout planning band, not an AWS forecast or
invoice. It excludes unexpected traffic, burst CPU credits, data transfer,
future log growth, taxes, and Provider use.

## Minimum-risk future cleanup order

No step below is authorized by this document.

1. **Retain without change:** current encrypted production RDS, its SG and
   backups, the PR-06B CMK, TD23, TD17, their immutable images, and the HMAC and
   database secret references.
2. **Close the rollback retention window:** confirm the I2 cutover date,
   production data consistency, and whether the old unencrypted database is
   still required. Do not retire it before the separately approved retention
   date and final recovery decision.
3. **Retire the I1 live rehearsal database first:** after this evidence PR is
   merged and an encrypted rehearsal snapshot is selected for retention,
   request a separate change to remove deletion protection and delete only
   `tripsync-postgres-pr06b-i1-20261019-01`. This is the lowest-risk material
   monthly saving because it has no connections and is not production.
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
- Schedule a separately approved **AWS Cleanup Change** after the old-source
  retention date, beginning with the unused I1 live RDS instance.
- Schedule a separate **Provider and Durable Runtime readiness gate** only if a
  real-model production experience is desired. It is not part of this closeout.

## Closeout recommendation

**PR-06B RELEASE CLOSEOUT: PASS.** The application release, schema migration,
Backend/Frontend promotion, and Runtime-off browser acceptance are complete and
documented. The AWS inventory is understood. The remaining issues are
non-blocking operational observations and separately authorized cleanup or
future-runtime work.
