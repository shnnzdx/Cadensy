# PR-05B.1 — Dormant Schema Foundation Implementation Record

## Baseline and scope

- Canonical baseline verified before implementation:
  `main == origin/main == 17026adf4b77ced2280629f2b1584fd6892e420e`.
- The canonical worktree contained the user-owned, untracked reports 41, 42, and
  43. To preserve them unchanged, implementation was performed in the clean,
  dedicated worktree `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`
  on `codex/pr05b1-schema-foundation` at that exact baseline.
- Design inputs were reports 41, 42, and 43. The frozen PR-04 artifacts, product
  code, API routes, runtime contracts, frontend, scheduler, and provider paths
  were not changed.
- No migration was applied to an existing runtime database, AWS, RDS, or any
  non-local database. No real AI or provider call was made.

## Exact implementation changes

| File | Change |
| --- | --- |
| `backend/app/db/models.py` | Added dormant `Plan.revision` as `BIGINT NOT NULL DEFAULT 0` with `revision >= 0` check. Added `MutationIdempotency`, including all frozen fields, PostgreSQL JSONB safe-outcome storage, the Trip/actor/key unique constraint, and expiry index. |
| `backend/alembic/versions/6f77e775d201_pr05b1_dormant_schema_foundation.py` | Added a reviewed linear Alembic revision whose sole parent is `54e8dd1ca624`. It adds the revision with a persistent server default and constraint, then creates the additive ledger table and expiry index. Its downgrade removes only objects created by this revision. |
| `backend/app/domain/idempotency.py` | Added pure canonical JSON/HMAC-SHA-256 primitives with explicit fingerprint and HMAC-key versions. It owns no secret configuration, HTTP parsing, database access, replay behavior, or locks. |
| `backend/tests/test_idempotency_schema.py` | Added real-PostgreSQL schema coverage for default/constraint behavior, unique scope, JSONB round-trip, rollback, expiry index, and nullability. |
| `backend/tests/test_idempotency_fingerprint.py` | Added deterministic, changed-payload/operation, version-retention, and fail-closed canonicalization tests using a fixture-only key. |

There is intentionally no `plan.trip_id` unique constraint. Existing Plan rows are
not rewritten by application code: PostgreSQL supplies `0` through the additive
non-null server default during migration.

## Frozen schema contract delivered

```text
plan.revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0)

mutation_idempotency
  id, trip_id, actor_membership_id, idempotency_key,
  operation_code, resource_kind, resource_id,
  fingerprint_version, request_hmac, hmac_key_version,
  outcome_contract_version, response_status, outcome_json JSONB,
  resulting_plan_revision nullable,
  created_at, completed_at, expires_at
  UNIQUE(trip_id, actor_membership_id, idempotency_key)
  INDEX(expires_at)
```

The unique scope intentionally preserves reports 41/43 semantics: a key is
Trip-and-actor scoped, not operation- or resource-scoped. The canonical HMAC
representation binds an operation code to a JSON-native payload, preventing a
future caller from treating the same payload bytes as equivalent across operations.

## Migration design and verification

The Alembic graph is linear:

```text
f9ff23b7b84d -> 54e8dd1ca624 -> 6f77e775d201 (head)
```

The migration was handwritten and reviewed; it was not a destructive repair or a
blind autogenerate result. `alembic check` was then run against the upgraded
disposable database and reported `No new upgrade operations detected.`

For migration verification, a local database named
`cadensy_pr05b1_alembic_test` was recreated on `localhost:5432`. The test first
upgraded only to `54e8dd1ca624`, inserted a pre-B.1 Plan without `revision`, and
then upgraded to head. The upgraded row and a subsequently inserted Plan both read
`revision = 0`. Direct PostgreSQL statements also proved that the migrated check
rejects a negative revision, the migrated three-column unique constraint rejects a
duplicate, and JSONB can be read safely.

## Test and command results

Before any destructive test setup, URL parsing verified all of the following:

```text
TEST_DATABASE_URL host=localhost database=cadensy_pr05b1_pytest_test
DATABASE_URL      host=localhost database=cadensy_pr05b1_alembic_test
distinct=yes; both names are explicitly disposable *_test databases
```

`localhost:5432` was confirmed accepting connections before database work. The
test processes set `DISABLE_SCHEDULER=1`, `MOCK_AI=1`,
`DEV_ALLOW_MEMBERSHIP_HEADER=0`, and empty DeepSeek/Geoapify key variables.

| Check | Result |
| --- | --- |
| `python -m py_compile` for changed Python modules/tests | PASS |
| `pytest tests/test_idempotency_fingerprint.py -q` | PASS — 4 passed |
| Alembic `upgrade 54e8dd1ca624`, pre-B.1 seed, then `upgrade head` | PASS |
| Migration backfill/default/schema inspection | PASS |
| Direct migrated PostgreSQL CHECK, UNIQUE, JSONB verification | PASS |
| `alembic heads` | PASS — only `6f77e775d201 (head)` |
| `alembic check` | PASS — no new upgrade operations |
| `pytest tests/test_idempotency_schema.py tests/test_idempotency_fingerprint.py -q` | PASS — 10 passed |
| CI-equivalent Legacy regression (all CI Pydantic-only files explicitly ignored) | PASS — 531 passed, 0 skipped; JUnit skip contract passed |
| CI-equivalent isolated Pydantic fake-compatibility suite | PASS — 119 passed |
| `git diff --check` | PASS |
| API diff inspection | PASS — `backend/app/api/main.py` has no diff |

One initial broad Legacy command was **not accepted as a test result** because its
handwritten exclusion list omitted three Pydantic-only files. It correctly stopped
at collection with `ModuleNotFoundError: pydantic_ai`, which is expected because the
Legacy environment deliberately does not install that package. The subsequent
CI-equivalent command used every exclusion from `build-validation.yml` and passed
with 531 tests and no skips. No implementation test failed; no required check was
skipped or not executable.

## Security and runtime boundaries

- `outcome_json` is a storage type for an already-safe response DTO. B.1 does not
  define a replay endpoint or permit sensitive provider payloads, authorization
  headers, private preferences, or internal membership data to enter it.
- HMAC keys remain outside the database and source tree. The tests use only a
  deterministic fixture key. `hmac_key_version` and `fingerprint_version` are
  retained for a later approved rotation/verification policy.
- The new model and helpers are dormant: no current request handler creates a
  ledger row, compares HMACs, accepts `Idempotency-Key`, increments revision, or
  changes a response.
- No request-scoped ORM Session is passed to a worker or new write unit of work.
  B.1 does not add a worker, transaction boundary, or lock.

## Explicitly unsupported and reserved for later PRs

This implementation does **not** claim or implement distributed exactly-once,
concurrency safety, public replay, or stale-preview prevention. The following
obligations remain reserved exactly as required:

1. Capture and verify a revision bracket before the earliest authoritative preview
   read/ref selection.
2. Keep Legacy replacement/Geoapify cache work outside PostgreSQL read-only or root
   lock assumptions.
3. Re-authorize a ledger replay actor even when a referenced resource changed or
   was deleted.
4. Achieve complete classified-fact writer coverage before any public revision or
   concurrency enforcement.
5. Refactor the cover fetch path so no network request runs while holding the Trip
   root lock before integrating it with the later lock protocol.

Additional later decisions still include retention/cleanup ownership, active and
verify-only HMAC key rotation, lock-timeout semantics, duplicate-Plan preflight,
fresh authorization linearization, and independent-connection contention tests.

## Independent-review corrections (2026-10-08)

This section records focused corrections requested after the original B.1 review.
The original implementation evidence above is retained; only the facts below were
newly changed and re-verified.

### 1. Versioned fingerprint envelope

`canonical_request_json()` now canonicalizes this explicit envelope before HMAC:

```json
{
  "fingerprint_version": 1,
  "operation_code": "...",
  "command": {"operation-specific canonical fields": "..."}
}
```

`fingerprint_version` is validated as a positive non-boolean integer and is now
inside the authenticated bytes, rather than returned only as `RequestHmac` metadata.
Thus identical command/version inputs yield the same digest, while a version change
or operation-code change yields a different digest. The future command payload must
still contain the remaining report-43 server-resolved scope/resource/revision fields;
B.1 has no caller and does not construct, accept, or persist that future envelope.

### 2. Narrow numeric, Unicode, array, null, and date normalization

- Unicode strings and mapping keys are NFC-normalized.
- Finite integral floats are emitted as integers, so `15` and `15.0` agree; both
  `0.0` and `-0.0` become JSON number `0`.
- Booleans are handled before integers and therefore remain distinct from `0`/`1`.
- NaN and both infinities fail closed. Ordered arrays retain their order, and a
  present `null` mapping member remains different from an omitted member.
- Validated Python `date` values are encoded as ISO-8601 dates, matching the
  accepted date fields in `ChangeRequest` and `ChangeOptionIn`. Datetimes are
  rejected: these current command shapes have date-only fields, and silently
  choosing a timezone would be a semantic change.

This is deliberately not a generic type-collapse policy. It is limited to the
numeric/date forms produced by validated API command fields; arbitrary objects,
non-string keys, timestamps, and non-finite numbers are rejected.

### 3. `request_hmac` schema alignment

Report 43 requires `request_hmac CHAR(64) NOT NULL`. Inspection proved revision
`6f77e775d201` was still untracked, uncommitted, absent from all local/remote
branches, and used only against disposable local databases. It was therefore safe
to correct the original B.1 migration directly rather than invent a forward-only
migration for an unreleased contract.

Both the ORM (`CHAR(64)`) and migration (`sa.CHAR(length=64)`) now agree. A fresh
upgrade from `54e8dd1ca624` confirmed PostgreSQL exposes `CHAR(64)`, alongside the
existing revision default/backfill, CHECK, unique constraint, and JSONB behavior.
No production or shared database was touched.

### 4. Newly executed evidence

Before PostgreSQL work, URL parsing again confirmed `localhost` only, explicit
`*_test` database names, and distinct test/runtime database names. Scheduler and AI
were disabled and provider keys were empty. The following commands were newly run
for these corrections:

| Check | Result |
| --- | --- |
| `pytest tests/test_idempotency_fingerprint.py -q` | PASS — 7 passed |
| Fresh Alembic upgrade `54e8dd1ca624 -> head` plus direct PostgreSQL inspection | PASS — `CHAR(64)`, revision backfill/default, CHECK, UNIQUE, JSONB |
| `pytest tests/test_idempotency_schema.py tests/test_idempotency_fingerprint.py -q` | PASS — 13 passed |
| `alembic heads` and `alembic check` | PASS — one head; no new upgrade operations |
| CI-equivalent Legacy regression with its official Pydantic exclusions | PASS — 534 passed, 0 skipped |
| CI-equivalent isolated Pydantic fake-compatibility suite | PASS — 119 passed |
| Changed-file `py_compile` and whitespace/API diff checks | PASS |

No correction test failed, was skipped, or was not executable. No real provider,
AWS/RDS endpoint, runtime database, API route, frontend, runtime composition, or
PR-04 artifact was accessed or changed.

### 5. Remaining B.2/B.3 obligations

These corrections do not authorize B.2 or B.3. The remaining work includes fresh
authorization and root-lock linearization, durable write/replay semantics, complete
writer coverage with one atomic revision advance, revision-bracketed preview reads,
retention/cleanup and HMAC rotation policy, independent-session contention tests,
and the cover-provider-outside-lock refactor. No B.1 helper currently accepts an
HTTP idempotency key, opens a transaction, or changes a public response.

**Independent-review corrections verdict: PASS for the focused B.1 fixes; full
concurrency/replay activation remains CONDITIONAL PASS pending later phases.**

## Final status

**PASS — the authorized dormant schema foundation is implemented and verified.**

PR-05B.1 implements a dormant schema foundation.
Full concurrency protection has NOT been implemented
or activated.
