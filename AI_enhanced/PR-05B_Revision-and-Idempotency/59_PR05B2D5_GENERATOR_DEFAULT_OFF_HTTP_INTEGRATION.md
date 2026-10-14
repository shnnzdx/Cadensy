# Cadensy PR-05B — B.2D5 Default-Off Generator HTTP Integration

Date: 2026-10-12
Worktree: `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`
Branch: `codex/pr05b1-schema-foundation`

## 1. Executive Summary and Verdict

B.2D5 adds the one authorized public composition for the accepted protected
Generator lifecycle. The existing endpoint remains:

```text
POST /api/trips/{trip_id}/plans/generate
```

It is still Legacy by default. Only an explicit, recognized non-production
`CADENSY_B2D_PROTECTED_GENERATOR_WRITES` value selects the protected lifecycle.
Production, malformed gate values, and ambiguous environments fail closed; none
falls back to Legacy after protected-path selection.

**Verdict: PASS — B.2D5 default-off HTTP adapter only.** This is not production
activation, deployment approval, global writer coverage, or authorization to
begin B.3/B.4.

## 2. Accepted B.2D1–B.2D4 Preconditions

The route composes rather than recreates the accepted implementation:

| Phase | Reused capability | Source evidence |
| --- | --- | --- |
| B.2D1 | Read-only detached `PreparedGeneration` snapshot | `backend/app/domain/plans/generator_lifecycle.py:168` |
| B.2D2 | Detached provider/cache/planner stage | `backend/app/domain/plans/generator_external_work.py:33` |
| B.2D3 | Generator command identity, preflight, replay codec, final ledger protocol | `backend/app/domain/transactions/generator_durable.py:146`, `:319`, `:358`, `:568` |
| B.2D4/A | Real draft persistence through `execute_protected_plan_generation` | `backend/app/domain/plans/generator_commit.py:47` |

No Generator UUID namespace, canonical HMAC input, durable envelope, Planner
algorithm, PlanItem/PlanChange mapping, or schema migration changed in B.2D5.

## 3. Changed File Inventory

| File | Change |
| --- | --- |
| `backend/app/api/main.py` | Default-off B.2D gate, manual Legacy ownership path, primitive-only protected admission, protected coordinator factory, safe error mapping, and actual route selection. |
| `backend/tests/test_generator_protected_http.py` | New real FastAPI/PostgreSQL route integration coverage. |
| `backend/tests/test_plan_generation.py` | Adapts the Legacy route fixture to explicitly inject its test Session factory and avoids reading detached fixture ORM rows after the route closes that owned Session. |
| `backend/tests/test_trip_transaction_kernel.py` | Replaces an obsolete pre-B.2D5 “no route activation at all” assertion with a narrow guard permitting exactly one default-off Generator composition. |
| This report | B.2D5 evidence and remaining activation blockers. |

The worktree already contained uncommitted B.1–B.2D4 work. B.2D5 did not alter
unrelated pre-existing files, frozen PR-04 artifacts, migrations, CI, Docker,
frontend, or deployment configuration.

## 4. Existing HTTP Dependency and Session Graph

Before B.2D5, the route declared FastAPI dependencies:

```text
get_session -> SessionLocal request Session
current_membership(request Session) -> ORM TripMembership
route -> scoped Trip read -> Legacy generate_plan(request Session)
      -> provider/cache/planner/Plan writes -> request Session commit
```

`get_session()` is a `SessionLocal` context manager
(`backend/app/db/session.py:41-43`), and `current_membership()` authenticates on
that same Session (`backend/app/api/main.py:170-203`). Consequently, simply
placing a protected branch inside the former route body would have retained an
open request Session and identity map across external work.

## 5. Protected HTTP Dependency and Session Graph

The current route is dependency-free with respect to database Sessions
(`backend/app/api/main.py:1644-1662`). It parses only raw optional headers, then
branches before opening a Session:

```text
HTTP request
  -> gate state (no DB Session)
  -> protected-only command/header/config validation (no external work)
  -> short preliminary Session: account-first/Guest membership resolution
       -> extract PreliminaryActorScope + hashed CredentialProof -> close
  -> B.2D3 replay preflight: fresh owned Session/transaction -> close
  -> B.2D1 Prepare: fresh repeatable-read, read-only Session -> close
  -> B.2D2 cache sessions / Provider / Planner on detached values
  -> B.2D3 final: fresh owned Trip-root transaction -> commit/rollback -> close
  -> detached validated payload -> HTTP JSON
```

There is no request-held outer transaction to commit after the coordinator's
final atomic transaction. The protected adapter passes neither request ORM
objects, raw bearers, nor a request Session to B.2D4.

## 6. Feature Gate and Environment Policy

The sole gate is declared at `backend/app/api/main.py:120` and classified at
`:316-335`.

| Configuration | Result |
| --- | --- |
| Missing/explicit false (`0`, `false`, `no`, `off`) | Legacy route; protected-only headers ignored. |
| Explicit true in `development`, `dev`, `local`, `test`, `testing`, `ci`, or `staging` | Protected lifecycle only. |
| Explicit true in `production`/`prod` | Controlled 503 before auth, Provider, Planner, or mutation. |
| Unrecognized flag or non-approved environment | Controlled 503, fail closed. |

The flag is not added to defaults, Docker, CI, AWS, or frontend configuration.

## 7. Account and Guest Authentication Boundaries

`_protected_generator_scope()` (`main.py:1499-1530`) reuses the existing
account-first `current_membership()` policy. An Account bearer is attempted as
an Account before a Guest-prefix fallback, so a valid Account token sharing a
Guest prefix is not downgraded. The preliminary Session extracts only:

- route-owned `trip_id`;
- authenticated membership ID;
- verified credential kind;
- Account user ID when applicable; and
- a non-serializable token hash in `CredentialProof`.

It does not accept `X-Membership-Id` as identity. The coordinator independently
locks and revalidates AuthSession/GuestSession, membership lifecycle, trip
binding, and organizer role under the Trip root lock. A matching Ledger row
therefore never bypasses revocation, removal, or role loss.

The protected Generator retains the actual product policy: a valid Guest is not
an organizer merely because its credential resolves to the Trip. The route test
uses a real GuestSession plus a spoofed `X-Membership-Id` and proves both
organizer impersonation and another actor's Ledger replay are denied.

## 8. Coordinator, HMAC, and Outcome Construction

`_generator_protected_coordinator()` (`main.py:1532-1546`) is reachable only
after the protected branch is selected. It uses the established protected HMAC
configuration (`CADENSY_B2C_HMAC_KEY`), `TripTransactionKernel(SessionLocal)`,
the reviewed static keyring, and the trusted
`generator_outcome_contract()`. A missing/blank/invalid protected key returns
503 before preliminary auth or external work.

The durable command is always server owned:

- operation: `plan.generate.v1`;
- fingerprint format: `1`;
- expiry proposal: now plus 24 hours;
- contract: frozen `initial_or_empty_plan.v1`;
- UUID namespaces and canonical payload: unchanged B.2D3 implementation.

The present default-off local composition has one configured active HMAC key.
The underlying coordinator supports retained verification keys and returns a
controlled 409 for an unavailable stored key version. Production key custody,
retention, and rotation configuration remains an activation blocker; no
historical Generator deployment key exists in this default-off phase.

## 9. Idempotency, Revision, Response, and Error Policy

Protected requests require `Idempotency-Key` only. Missing is 428; invalid is
422. The frozen Generator protocol deliberately does **not** accept or emit a
Plan/Trip revision header. Current revisions and full planning facts are
server-observed preconditions, not client command identity.

The protected result is the B.2D3 detached, strict outcome and retains the
Legacy 200 top-level shape exactly:

```text
plan_id, status, days, blocked_reason, blocked_code,
generated_by, used_ai, planner_note
```

Historical replay returns the stored validated outcome. It does not query the
current Plan to rebuild item IDs or notes, re-read preferences, invoke a
Provider/Planner, or write again.

| Condition | HTTP response |
| --- | --- |
| Matching completed command | 200 historical result |
| Missing key | 428 |
| Invalid key/command | 422 |
| Stale draft, populated Plan, duplicate Plans, key reuse/expiry, unavailable replay contract/key/version, revision overflow | 409 |
| Invalid/revoked credential | 401 |
| Removed membership/non-organizer | 403 |
| Scoped Trip missing | existing safe 404 |
| Gate/configuration/production request, lock/deadlock/serialization/integrity/database failure | 503 |

`_protected_generator_error()` at `main.py:1561-1604` maps typed exceptions;
it does not string-match messages or convert unexpected defects into a false
domain result. It returns non-diagnostic messages only.

## 10. Feature-Off Compatibility

`_generate_trip_plan_legacy()` (`main.py:1472-1496`) retains original
authentication, Trip scoping, Generator calls, error mappings, response shape,
and the one Legacy `db.commit()`. It explicitly owns and closes the same kind of
request Session after completion. Feature-off tests prove absent and false gates
ignore malformed `Idempotency-Key` and both future revision headers, do not
construct the protected coordinator or access its HMAC configuration, and write
no Generator Ledger.

Existing Legacy HTTP Generator coverage continues to prove organizer readiness,
non-organizer rejection, populated-Plan rejection, blocked outputs, planner
fallback, response notes, and actual commit behavior.

## 11. PostgreSQL Route-Level Evidence

`backend/tests/test_generator_protected_http.py` exercises the actual FastAPI
endpoint against independent disposable PostgreSQL Sessions:

- feature-off missing/false gate compatibility and malformed protected-header
  tolerance;
- Account organizer success for zero Plan and one empty Plan, including one
  Plan revision increment, unchanged Trip facts revision, PlanItems,
  PlanChanges, completed Plan-scope Ledger, and deterministic first-Plan ID;
- same-key historical replay after both Plan and Trip revisions change;
- missing/malformed key, missing HMAC configuration, production/ambiguous gate
  failure, populated Plan, duplicate Plan, expired Ledger, tampered outcome,
  and unavailable HMAC-key-version failures;
- real Guest credential plus membership-header spoof rejection, actor Ledger
  isolation, Account revocation, and removed-membership replay denial; and
- a route-level Session/transaction test with a blocking fake Provider.

For the blocking Provider test, every tracked request/preflight/Prepare/cache
Session was closed and out of transaction before the Provider began. While the
HTTP worker was blocked, a real accepted Phase 4 `constraint.create.v1` writer
committed on an independent PostgreSQL connection. That advanced Trip facts;
after Provider release, the Generator safely returned 409 stale and created no
Plan or Generator Ledger. This proves both no long request transaction and no
Trip root lock across external work.

## 12. Database Safety and Regression Results

Before database runs, PostgreSQL reported `localhost:5432 - accepting
connections`. The test URL was verified as
`localhost/tripsync_b2d4_test`, separate from the local runtime placeholder
`localhost/tripsync_runtime_codex_b2d4`. Scheduler/auth bypass were disabled;
Provider credentials were blank and all Provider/Planner tests used fakes.
No AWS, RDS, Supabase, real Geoapify, or real model calls occurred.

Executed results:

| Command group | Result |
| --- | --- |
| B.2D HTTP + B.2D1–B.2D4 + Generator HTTP + auth + Phase 4/Trip durable/idempotency regressions | `282 passed in 10.42s` |
| Legacy CI-equivalent pytest command, excluding only the seven isolated Pydantic/eval modules named in `.github/workflows/build-validation.yml` | JUnit: `914 cases, 0 failures, 0 errors, 0 skipped` |
| Isolated Pydantic CI-equivalent nine-module command | `119 passed in 6.84s` |

The Legacy collection is one mutually exclusive suite with the CI's explicit
seven Pydantic/evaluation exclusions; the Pydantic command is the CI's explicit
nine-module isolated suite. Counts are reported separately and not added as a
claim of unique tests.

## 13. Privacy, Non-enumeration, and Known Limitations

The public adapter never serializes HMAC material, credential values,
membership identity, private constraint text, raw planning input, provider
payload, planner prompt, ORM entities, or database diagnostics. Replay
verification uses the trusted historical and current outcome policies already
frozen in B.2D3.

Still out of scope and blocked:

- enabling this gate in production or changing production configuration;
- HMAC custody/retained-key rotation and Ledger retention operations for a
  production deployment;
- full migration of all remaining Legacy writers and global preview/apply
  guarantees;
- Generator scheduler/organizer action migration, Preview/Apply, B.2D6,
  B.3, B.4, PR-06, and PR-08;
- real Provider/model calls, AWS/RDS access, migration, commit, push, merge,
  or deployment.

## 14. Final Readiness

**PASS for controlled review of the default-off Generator HTTP integration.**

The adapter is ready for a later explicit activation review only. It does not
authorize production traffic: production gate-on is deliberately tested as a
503, and the gate remains absent from all default and deployment configuration.

## 15. B.2D5A — Final HTTP Safety Closure

### 15.1 Reviewed findings and narrow corrections

The original B.2D5 implementation classified the protected gate with
`os.getenv("APP_ENV", "development")`.  Thus an explicitly true protected
gate could select the protected path when `APP_ENV` was missing, contrary to
the approved rule that a protected path requires an explicit, recognized
non-production environment.  This did not affect a disabled gate: feature-off
must continue to select Legacy regardless of environment configuration.

`backend/app/api/main.py:317-339` now treats a missing `APP_ENV` as the
Generator gate's `invalid` state only after a true gate value has been
identified.  It intentionally does not alter the application's global or B.2C
environment-default policy.

The review also identified an unhandled infrastructure boundary: the short
preliminary Session in `_protected_generator_scope()` could propagate a raw
SQLAlchemy exception from Account Session lookup, Guest Session lookup, or
membership resolution.  `backend/app/api/main.py:1503-1538` now catches only
`SQLAlchemyError` around acquisition and use of that short owned Session and
returns the stable non-diagnostic 503 detail `Protected Generator
authentication is temporarily unavailable`.  `HTTPException` authentication
and authorization outcomes remain unchanged, and unexpected non-database
programming errors are still not converted to 503.

The `finally` still closes the preliminary Session before the mapping occurs.
This preserves the B.2D1/B.2D2 separation: no request-held Session or
transaction reaches replay preflight, Provider/cache work, Planner work, or
the final owned commit transaction.

### 15.2 Corrected exact Generator gate decision table

| Gate value | `APP_ENV` | Result |
| --- | --- | --- |
| Missing | Missing | Legacy path; no protected configuration required. |
| `0` | Missing | Legacy path; no protected configuration required. |
| Explicit true (`1`, `true`, `yes`, `on`) | Missing | Controlled 503; no Legacy fallback. |
| Explicit true | Empty | Controlled 503; no Legacy fallback. |
| Explicit true | Unknown | Controlled 503; no Legacy fallback. |
| Explicit true | `production` or `prod` | Controlled 503; no Legacy fallback. |
| Explicit true | `development`, `dev`, `local`, `test`, `testing`, `ci`, or `staging` | Protected lifecycle only. |
| Any unrecognized non-false gate value | Any | Controlled 503; no Legacy fallback. |

The disabled route still ignores protected-only headers and preserves Legacy
Generator behavior.  Every controlled fail-closed request is rejected before
preliminary authentication, Provider, Planner, Plan mutation, or Generator
Ledger work.

### 15.3 New route-level PostgreSQL evidence

`backend/tests/test_generator_protected_http.py:471-527` adds the complete
missing/empty/unknown/production/approved environment matrix.  It verifies the
Legacy result for gate-off configurations and, for every protected rejection,
asserts no Legacy call, Provider call, Planner call, Plan row, or Generator
Ledger row.  The approved `test`, `development`, and `staging` values reach
the protected admission boundary and return its required missing-key 428.

`backend/tests/test_generator_protected_http.py:529-581` injects a real
SQLAlchemy `OperationalError` from a tracked preliminary-auth Session.  The
actual FastAPI route returns 503 with the stable non-diagnostic body, closes
that sole preliminary Session, never invokes coordinator execution, Provider,
or Planner, and leaves Plan and Generator Ledger state absent.  Existing full
HTTP coverage continues to prove revoked credentials return 401 and removed or
unauthorized membership returns 403.

### 15.4 Database safety and executed regression results

Before all PostgreSQL suites, `pg_isready -h localhost -p 5432` reported
`localhost:5432 - accepting connections`.  URL inspection confirmed
`TEST_DATABASE_URL=localhost/tripsync_b2d4_test`, distinct from
`DATABASE_URL=localhost/tripsync_runtime_codex_b2d4`.  Scheduler and legacy
membership-header bypass were disabled; Provider credentials were blank and
all Provider/Planner interactions were fakes.  No AWS, RDS, cloud database,
real Provider, or real model call occurred.

| Exact executed command group | Actual result |
| --- | --- |
| `pytest -q tests/test_generator_protected_http.py -k 'gate_requires_explicit or preliminary_authentication_database_failure'` | `12 passed, 14 deselected in 1.08s` |
| `pytest -q tests/test_generator_protected_http.py` | `26 passed in 1.68s` |
| `pytest -q tests/test_generator_commit.py tests/test_generator_durable_coordinator.py tests/test_generator_lifecycle.py tests/test_generator_external_work.py tests/test_plan_generation.py tests/test_auth.py tests/test_pr01a_auth_characterization.py` | `132 passed in 7.34s` |
| `pytest -q tests/test_durable_idempotency_kernel.py tests/test_transaction_kernel.py tests/test_trip_transaction_kernel.py tests/test_protected_manual_plan_item.py tests/test_protected_booking_writer.py tests/test_protected_change_proposal_writer.py tests/test_protected_vote_writer.py tests/test_protected_proposal_decision_writer.py tests/test_protected_notice_objection_writer.py tests/test_protected_round_settlement_writer.py tests/test_trip_preferences_writers.py` | `254 passed in 6.84s` |
| Legacy CI-equivalent `pytest -q` command with the workflow's seven Pydantic/eval exclusions and JUnit output | `922 cases, 0 failures, 0 errors, 0 skipped` |
| Isolated Pydantic CI-equivalent nine-module command | `119 passed in 6.13s` |
| `python -m py_compile backend/app/api/main.py backend/tests/test_generator_protected_http.py backend/tests/test_plan_generation.py backend/tests/test_trip_transaction_kernel.py` | passed |
| `git diff --check` | passed |

The earlier B.2D5 results in section 12 remain historical evidence.  The
Legacy count is now higher because this correction adds route tests; the
separate isolated Pydantic count remains intentionally not additive.

### 15.5 Remaining activation blockers and final B.2D5 verdict

**PASS — B.2D5 including B.2D5A final HTTP safety closure.**  The Generator
route remains default-off and non-production only.  This verdict does not
authorize production activation, configuration, deployment, commit, push,
merge, B.2D6, B.3, or B.4.

Remaining production-activation blockers are unchanged: reviewed production
HMAC custody and retained-key rotation, Ledger retention operations, explicit
production activation approval, full remaining Legacy-writer coverage, and
separate Preview/Apply guarantees.  The route's strict non-production gate and
the protected Session boundaries remain in force until those independent
reviews occur.
