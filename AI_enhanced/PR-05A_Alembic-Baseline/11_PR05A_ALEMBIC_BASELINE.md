# Cadensy PR-05A — Alembic Baseline

Status: complete for local disposable-database validation. This PR establishes
the migration foundation only; it does not authorize a production migration,
add `GuestSession`, or begin PR-01B.

## Scope and safety boundary

The facts used to form this baseline were `backend/app/db/models.py` and
`backend/app/db/init_schema.py`. Validation used four newly created local
PostgreSQL databases with names ending in `_test`; no AWS host, project
runtime database, deployed environment, or real data was contacted.

| Database purpose | Disposable database used | Permitted operation |
|---|---|---|
| Autogenerate isolation | `cadensy_alembic_autogen_20261003_test` | Generate and inspect the candidate revision. |
| Fresh migration and rollback | `cadensy_alembic_fresh_20261003_test` | `upgrade`, disposable-only `downgrade base`, then re-upgrade. |
| Existing-schema compatibility | `cadensy_alembic_existing_20261003_test` | Legacy initialization, zero-drift comparison, `stamp head`, no schema rewrite. |
| Legacy-helper drift observation | `cadensy_alembic_helper_drift_20261003_test` | Isolated observation only; it was not stamped or repaired. |

Each create operation checked that the database name matched
`^cadensy_[a-z0-9_]+_test$` and refused an existing name. The disposable-only
rollback was guarded by the same rule. The local password was passed only to
the local PostgreSQL client process and is not recorded here.

## Baseline design

* **Revision:** `f9ff23b7b84d` — `baseline existing schema`
* **Parent:** none; this is the immutable cutover/root revision.
* **Configuration:** `backend/alembic.ini`, `backend/alembic/env.py`, and the
  conventional `backend/alembic/versions/` tree.
* **Metadata authority:** `app.db.models.Base.metadata`, imported by Alembic.
  Runtime `DATABASE_URL` is authoritative and must be set explicitly before
  running a migration command.
* **Comparison policy:** PostgreSQL type and server-default comparison are
  enabled. A revision must be reviewed; `--autogenerate` is a proposal, not an
  approval to execute DDL.

The revision creates these 18 model-owned tables: `user_account`,
`auth_session`, `trip`, `trip_membership`, `invite_link`, `preference`,
`member_constraint`, `member_constraint_private`, `plan`, `plan_item`,
`place`, `plan_item_comment`, `plan_change`, `decision_round`, `vote`,
`change_proposal`, `proposal_decision`, and `update_notice`.

It includes the model-owned foreign keys, unique constraints, normal place
index, and PostgreSQL partial unique indexes:

| Object | Baseline coverage |
|---|---|
| `uq_place_provider_id` | Provider/place identity remains unique. |
| Account, session, invite, preference, vote and proposal-decision uniqueness | Model-declared unique constraints are created. |
| `one_membership_per_user_per_trip` | Partial unique index where `user_id IS NOT NULL`. |
| `one_open_round_per_item` | Partial unique index where `status = 'open'`. |
| `one_pending_proposal_per_item` | Partial unique index where `status = 'waiting_affected_members'`. |
| `ix_place_city_country` | Non-unique place lookup index. |

`init_schema.py`'s current additive compatibility statements are represented
by the resulting model schema (nullable cost/duration fields, widened strings,
localized labels, refresh flag, and trip-cover fields). New empty databases
must now use Alembic. `init_schema.py` remains only an explicit compatibility
verification aid for pre-Alembic installations.

## Acceptance evidence

| Acceptance check | Result | Evidence |
|---|---|---|
| Static root/revision contract | Pass | `tests/test_alembic_baseline_contract.py` fixes the root revision, all 18 metadata tables, and all three partial index names without opening a database. |
| Fresh database `upgrade head` | Pass | `f9ff23b7b84d` applied to a newly created disposable PostgreSQL database. |
| Fresh database drift check | Pass | `alembic check` reported `No new upgrade operations detected.` after upgrade. |
| Fresh database rollback/reapply | Pass | On the same fresh disposable DB only, `downgrade base` completed, then `upgrade head` and `alembic check` completed. |
| Existing `init_schema` comparison | Pass | Direct Alembic metadata comparison reported `metadata_diff_count=0`, `metadata_table_count=18`, `actual_table_count=18`. |
| Existing-schema stamping | Pass | `stamp head` recorded `f9ff23b7b84d`; a subsequent `upgrade head` had no operations and `alembic check` was clean. |
| Existing-data preservation during stamp | Pass | A synthetic `user_account` sentinel was inserted before stamp and remained exactly once afterward. |
| Legacy helper drift detection | Pass as a finding | `ensure_cloud_schema()` adds `change_proposal.extended_at`; comparator reported precisely one unmodeled `remove_column` difference. No delete, migration, or stamp was performed for that database. |

The pre-stamp `alembic check` result on the existing-schema DB was deliberately
not treated as a drift result: Alembic correctly rejected it as untracked.
The direct comparator is the pre-stamp structural proof; `alembic check`
becomes meaningful after the explicit stamp.

## Existing-schema strategy

`stamp head` is a bookkeeping transition, not a schema migration. It is
allowed only when all of the following are true:

1. The exact target database has been identified, backed up under an approved
   operational process, and is within an authorized environment.
2. A direct metadata comparison is zero-drift, with any intentional exception
   separately approved and encoded in model/migration policy.
3. The database has not previously been stamped to an incompatible revision.
4. An operator has reviewed that `stamp` writes only `alembic_version`; it
   does not make old schema differences safe.

This PR proves that strategy for the schema currently produced by
`init_schema.py`, using an independently initialized disposable copy. It does
**not** authorize stamping a real project database from this record.

## Schema drift report

| Source | Finding | Impact | Required disposition |
|---|---|---|---|
| `app.db.models.Base.metadata` versus `init_schema.py` result | No difference across 18 model tables. | Baseline is compatible with the documented legacy initializer result. | Eligible for reviewed stamp after normal backup/preflight. |
| `app.db.upsert_demo_login.ensure_cloud_schema()` | Adds `change_proposal.extended_at TIMESTAMPTZ`, but current `ChangeProposal` model has no such column. | Autogenerate would propose dropping a potentially meaningful column. | Do not stamp as zero-drift and do not drop it. A future approved reconciliation must either model the field or preserve/remove it through a data-reviewed migration. |

This finding is deliberately a blocker for applying the simple stamp procedure
to databases touched by that helper until the field's ownership and data
meaning are resolved.

## Migration and rollback runbook

### New empty local database

```powershell
cd backend
$env:DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/cadensy_new_test'
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
.\.venv\Scripts\python.exe -m alembic -c alembic.ini current
.\.venv\Scripts\python.exe -m alembic -c alembic.ini check
```

Use a verified local disposable database for test commands. For a normal new
developer database, use a developer-controlled database, not an RDS endpoint.
Never run `app.db.init_schema` to establish a new Alembic-managed database.

### Pre-Alembic existing schema

1. Stop if the target is production, AWS RDS, unknown, or lacks a separately
   approved backup/restore plan. This PR grants no authority for that work.
2. Compare the actual schema with `Base.metadata` without writing DDL. Stop on
   any difference, including `change_proposal.extended_at`.
3. After a zero-drift result and approval, run only:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c alembic.ini stamp head
   .\.venv\Scripts\python.exe -m alembic -c alembic.ini current
   .\.venv\Scripts\python.exe -m alembic -c alembic.ini check
   ```

4. Confirm application smoke tests and preserve the backup. Do not use
   `upgrade head` to make a non-zero-drift database fit the baseline.

### Rollback policy

The root baseline's `downgrade base` drops every baseline table. It was tested
only on a fresh, empty disposable database and is **never** an operational
rollback for an existing or data-bearing database. For a stamped legacy
database, do not run baseline downgrade; restore an approved backup if the
stamp itself must be reversed, or use `stamp` only under the same reviewed
bookkeeping procedure. Each future additive revision must supply and test its
own downgrade path or explicitly require backup/forward-fix recovery.

## Known risks and PR-01B readiness

* The legacy helper drift above must be resolved before any database it touched
  is stamped.
* `DATABASE_URL` is intentionally powerful; this PR adds no environment-name
  allowlist in Alembic. The runbook's target verification remains mandatory,
  and no production command was run.
* The baseline captures the current schema, not a guarantee that all historic
  installations match it. Each existing target needs its own preflight.
* Alembic baseline **foundation: ready** for PR-01B. PR-01B remains separately
  blocked on the ADR decisions for Guest credential generation, TTL/hash/
  rotation, authentication precedence, logout revocation, browser storage
  threat model, membership-removal invalidation, and an approved additive
  `GuestSession` migration. No GuestSession table or PR-01B behavior was added
  here.

## Changed files in PR-05A

* `backend/requirements.txt`
* `backend/alembic.ini`
* `backend/alembic/env.py`
* `backend/alembic/script.py.mako`
* `backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py`
* `backend/alembic/versions/.gitkeep`
* `backend/tests/test_alembic_baseline_contract.py`
* `backend/app/db/init_schema.py`
* `backend/README.md`
* `backend/LOCAL_DEV.md`
* this runbook

The PR-01A closure edits are separately recorded in
`ADR-001_GUEST_CREDENTIAL_AND_SESSION.md`,
`10_PR01A_AUTH_GUEST_BASELINE.md`, and its characterization tests.
