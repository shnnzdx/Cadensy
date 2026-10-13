# Cadensy PR-05B — Phase 2: TripTransactionKernel

**Status:** completed transaction-kernel foundation and local PostgreSQL validation, 2026-10-10. Phase 2 is not a durable-command or writer-activation phase.

## 1. Executive Summary

Phase 2 adds a dedicated Trip-root transaction boundary and the frozen Guest active-status compatibility change. It uses an owned short-lived SQLAlchemy Session, takes `Trip FOR UPDATE` before every other authoritative row lock, freshly reauthorizes the actor, locks zero/one/all candidate Plans deterministically, and invalidates its context before closing the Session.

The implementation deliberately does **not** add Trip durable commands, ledger access, HMACs, replay, OutcomeContracts, revision increments, child locks, business-writer migration, route activation, or provider work.

## 2. Phase 0 / Phase 1 References

The governing protocol is `49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md`, with Phase 1 schema evidence in `50_PRE_PLAN_TRIP_BOUND_PHASE1_SCHEMA_FOUNDATION.md`. Reports 43, 45, and 46 provide the existing Plan-bound contract and its reusable safety primitives.

## 3. Pre-implementation Audit and Intended File Changes

Verified reusable Plan-kernel primitives:

- primitive-only `PreliminaryActorScope`, non-serializable hashed `CredentialProof`, and `LockedActor`;
- owned `Session` lifecycle, local timeout configuration, rollback/close, context invalidation, detached-result validation, and PostgreSQL error classification;
- Account-first scope binding and locked `AuthSession`/`GuestSession` validation.

The existing exact-one-Plan acquisition and `LockedPlanContext` are deliberately Plan-bound and cannot be reused for a valid zero-Plan Trip command.

Planned changes before implementation:

| File | Intended change |
| --- | --- |
| `backend/app/domain/auth.py` | One narrow shared Guest active-status predicate used by Legacy authentication. |
| `backend/app/domain/transactions/kernel.py` | Replace the joined-only Plan-kernel Guest check with that shared predicate; preserve every other account/guest check. |
| `backend/app/domain/transactions/trip_kernel.py` | New dedicated TripTransactionKernel and LockedTripContext, Trip-first lock order, optional locked Plan, no ledger/revision behavior. |
| `backend/app/domain/transactions/__init__.py` | Narrow exports for the new public internal contract. |
| `backend/tests/test_trip_transaction_kernel.py` | Public-kernel, lifetime, authorization, rollback, and PostgreSQL concurrency evidence. |
| this report | Final Phase 2 evidence only. |

All listed changes were implemented exactly as planned. No Phase 1 migration/schema file, durable kernel, business writer, HTTP route, feature gate, scheduler, generator, or frozen PR-04 artifact was changed.

## 4. TripTransactionKernel Public Contract

`backend/app/domain/transactions/trip_kernel.py` provides `TripTransactionKernel` and `LockedTripContext`. The public call is:

```python
TripTransactionKernel(session_factory).execute(
    scope=PreliminaryActorScope(...),
    credential=CredentialProof.from_bearer_token(...),
    required_role=None,
    operation=lambda context: detached_result,
)
```

The public callback receives no request Session, raw bearer, commit API, or HMAC material. It can read the active context and return only the existing detached primitive/container/explicit-DTO result shapes. The package-private `_run_locked_command()` follows the established Plan-kernel collaborator pattern for a later reviewed Trip durable collaborator; it is not exported as a public application service.

## 5. LockedTripContext Lifetime

`LockedTripContext` exposes the freshly authorized `LockedActor`, active locked `trip`, optional active locked `plan`, identity properties, `planning_facts_revision`, and optional `plan_revision`. It exposes no raw credential, Session, commit, or rollback operation.

The context is invalidated in `finally` before the owned Session closes on success, authorization failure, duplicate aggregate, callback exception, and database error. Access after callback raises the existing `ContextExpired`. The shared detached-result validator rejects the context as an unrecognized transaction-owned object, including nested containers.

## 6. Zero / One / Duplicate Plan Handling

After Trip → credential → membership locking, candidate Plans are selected only by the locked Trip ID, ordered by `Plan.id`, and locked with `FOR UPDATE`.

| Candidate Plans | Result |
| --- | --- |
| 0 | Valid Trip command; context has `plan is None`, `plan_id is None`, `plan_revision is None`; no Plan is created. |
| 1 | The one authoritative Plan is locked and exposed only for callback lifetime; neither revision changes merely by entering the kernel. |
| >1 | `DuplicatePlans`; callback is never entered and no revision/business state is modified. |

No `UNIQUE(plan.trip_id)` migration or automatic Plan creation was added.

## 7. Canonical Lock Ordering

The implemented sequence is exactly the Phase 2 portion of the frozen protocol:

```text
owned Session + BEGIN
  -> Trip FOR UPDATE
  -> AuthSession or GuestSession FOR UPDATE
  -> actor TripMembership FOR UPDATE
  -> optional all TripMembership rows ordered by id
  -> all Plans for the locked Trip ordered by id FOR UPDATE
  -> callback
  -> COMMIT or ROLLBACK; invalidate context; close Session
```

Ledger and owner-scoped child locks are intentionally absent. Source evidence is `trip_kernel.py` root acquisition and reauthorization paths. The public mismatched-Guest test proves the kernel rejects at credential validation before any Plan lock is attempted for the foreign Trip.

## 8. Session Ownership and Rollback

Every `execute()` creates one Session through its supplied factory and one outer `session.begin()` transaction. The request-owned Session never crosses this boundary; tests compare a request connection identity with the kernel context identity.

Callback exceptions propagate and roll back staged writes. A focused test changes `Trip.name` inside the callback, raises a sentinel exception, then proves the persisted value is unchanged, the owned tracking Session closed, and the context expired. Lock acquisition and SQLAlchemy failures use the existing typed database classifier without automatic retry.

## 9. Guest Authorization Compatibility

`auth.py` now defines `ACTIVE_GUEST_MEMBERSHIP_STATUSES = {"joined", "preferences_submitted"}` and `is_active_guest_membership_status()`. The same narrow helper is used by:

1. Legacy `membership_for_guest_token()`;
2. `PlanTransactionKernel._locked_guest_actor()`; and
3. `TripTransactionKernel._locked_guest_actor()`.

All other Guest checks remain fail-closed: token hash, credential kind, expiration, revocation, Trip binding, membership binding, anonymous (`user_id is None`) invariant, and scope binding. Account policy remains its existing `status != "removed"` contract. API `current_membership()` remains account-first; no token-prefix classification was added to `CredentialProof` or the Trip kernel.

## 10. Account/Guest Security Regression Results

Focused PostgreSQL tests cover:

- Account and Guest success, including an Account bearer beginning `gst_` remaining an Account;
- Guest `joined` and `preferences_submitted` success through Legacy helper, Plan kernel, and Trip kernel;
- removed, invited, declined, revoked, expired, foreign/mismatched, and Account-linked Guest membership denial;
- Account/Guest credential-kind mismatch denial;
- missing Trip and role denial; and
- no callback after authorization failure.

The relevant focused test run passed; no public authentication endpoint or membership-lifecycle rule was changed.

## 11. Real PostgreSQL Lock Evidence

`test_trip_root_lock_blocks_second_trip_kernel_with_postgresql_evidence` uses two independent worker Sessions on the same Trip. The first callback holds the acquired root lock; the second reaches its `Trip FOR UPDATE` statement. A third observer reads PostgreSQL `pg_blocking_pids(second_backend_pid)` in a bounded, sleep-free SQL loop and proves the first backend PID blocks the second. After the coordinator releases the first callback, both workers finish and the second callback executes.

This is PostgreSQL server evidence, not a mock or SQLite substitute.

## 12. Cross-Kernel Concurrency Results

`test_plan_and_trip_kernels_serialize_on_the_same_trip_root` holds a Trip kernel transaction and starts an existing `PlanTransactionKernel` command for the same Trip on an independent Session. PostgreSQL reports blocking for the Plan command's root backend PID; it progresses only after release. This demonstrates compatible Trip-first root ordering and does not change Plan revision semantics.

It does not claim that unmigrated Legacy writers serialize through this kernel.

## 13. Revocation / Removal Race Results

The Guest credential-only logout compatibility race holds `GuestSession FOR UPDATE` in one connection without a Trip lock. The Trip kernel acquires Trip, reaches its locked Guest credential query, waits, then observes the committed `revoked_at`, raises `InvalidCredential`, and never calls the callback.

The suite also simulates a completed preliminary membership read followed by a separate committed membership removal; locked reauthorization rejects it before callback entry. These results preserve the Phase 0 rule that preliminary authentication is admission only and fresh in-transaction validation is the authorization point.

## 14. Database Failure Classification

The new kernel uses the existing Plan-kernel `classify_database_error()` unchanged:

| Condition | Typed result |
| --- | --- |
| Missing root Trip | `TripNotFound` |
| Multiple Plans | `DuplicatePlans` |
| Invalid/revoked/expired credential | `InvalidCredential` |
| Removed/invalid membership | `MembershipNotAuthorized` |
| Role mismatch | `InsufficientRole` |
| PostgreSQL `55P03` timeout | `LockUnavailable` |
| PostgreSQL `40P01` / `40001` | existing `RetriableDeadlock` / `RetriableSerializationFailure` classifier categories |
| Other DB/integrity error | existing controlled database/integrity categories |

The timeout path is exercised with a real held Trip root lock. Existing Plan-kernel classifier regression covers the deadlock/serialization SQLSTATE mapping reused here. No raw SQL, credential, or diagnostic is exposed through an HTTP endpoint because Phase 2 activates none.

## 15. Exact Test Commands and Results

All database commands used `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_pr05b2c_final_test`, a distinct loopback runtime URL ending `tripsync_pr05b2c_final_runtime_test`, scheduler disabled, mock AI enabled, membership-header compatibility disabled, and empty provider keys. PostgreSQL readiness was confirmed with `pg_isready -h localhost -p 5432`. No AWS/RDS/provider endpoint was accessed.

| Command | Result |
| --- | --- |
| `pytest tests/test_trip_transaction_kernel.py -q` | **22 passed** in 1.74s |
| `pytest tests/test_trip_transaction_kernel.py tests/test_transaction_kernel.py tests/test_auth.py tests/test_idempotency_fingerprint.py tests/test_idempotency_schema.py tests/test_trip_bound_phase1_migration.py tests/test_durable_idempotency_kernel.py -q` | **118 passed** in 11.12s; 10 Alembic config deprecation warnings |
| Seven accepted B.2C protected writer suites | **120 passed** in 7.18s |
| Legacy CI-equivalent pytest with official Pydantic exclusions and JUnit no-skip check | **753 collected, 0 failures, 0 errors, 0 skips** |
| Frozen Legacy evaluation runner | **PASS** — 8 total, 6 approved baseline passes, 2 approved Legacy mismatches, 0 safety violations |
| Isolated Pydantic CI-equivalent suite | **119 passed** in 11.23s |

The first Legacy run caught an observer-test scheduling race (the SQLAlchemy pre-dispatch event can precede the database's blocking state); it was corrected with bounded server-side lock observation and the complete Legacy suite was rerun successfully. This is test-evidence hardening, not a production behavior change.

Executed from `backend` after setting the stated local safety environment:

```powershell
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest tests\test_trip_transaction_kernel.py -q
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest tests\test_trip_transaction_kernel.py tests\test_transaction_kernel.py tests\test_auth.py tests\test_idempotency_fingerprint.py tests\test_idempotency_schema.py tests\test_trip_bound_phase1_migration.py tests\test_durable_idempotency_kernel.py -q
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest tests\test_protected_manual_plan_item.py tests\test_protected_booking_writer.py tests\test_protected_change_proposal_writer.py tests\test_protected_vote_writer.py tests\test_protected_proposal_decision_writer.py tests\test_protected_notice_objection_writer.py tests\test_protected_round_settlement_writer.py -q
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --ignore=tests/test_pydantic_runtime.py --ignore=tests/test_pydantic_composition.py --ignore=tests/test_symmetric_evaluation_harness.py --ignore=tests/test_formal_ab_runner.py --ignore=tests/test_provider_smoke_harness.py --ignore=tests/test_live_provider_smoke_adapter.py --junitxml=$env:TEMP\cadensy-phase2-legacy-junit-rerun.xml
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m evals.runner --output $env:TEMP\cadensy-phase2-legacy-eval-20261010
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-pydantic-poc\Scripts\python.exe -m pytest -q tests\test_pydantic_ai_poc.py tests\test_pydantic_composition.py tests\test_pydantic_runtime.py tests\test_symmetric_evaluation_harness.py tests\test_formal_ab_runner.py tests\test_runtime_composition.py tests\test_provider_smoke_harness.py tests\test_live_provider_smoke_adapter.py tests\test_evaluation_foundation.py
```

Final checks after the last source change:

```powershell
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m py_compile app\domain\auth.py app\domain\transactions\kernel.py app\domain\transactions\trip_kernel.py app\domain\transactions\__init__.py tests\test_trip_transaction_kernel.py
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m alembic -c alembic.ini heads
git diff --check
```

All three passed; Alembic reports only `c2a4b6d8e0f1 (head)`.

## 16. Remaining Risks

1. No Trip durable collaborator, ledger read/write, replay, or response contract exists yet.
2. No Trip or Plan revision increment/check occurs here; the Phase 1 fields remain dormant.
3. No Preferences/Constraints writer uses this boundary and no public feature gate/route is enabled.
4. Only root/security/Plan aggregate locks are implemented; child locking and business-state revalidation belong to a later writer/durable phase.
5. Existing credential-only logout still takes only a credential lock; the kernel safely waits/revalidates, but this is not a wholesale membership-lifecycle redesign.

## 17. Phase 2 Acceptance Verdict

**PASS.** The dedicated TripTransactionKernel satisfies the frozen Phase 0/1 foundation: owned transaction/session lifecycle, Trip-first locking, fresh authorization, deterministic zero/one/duplicate Plan treatment, context expiry, rollback, Guest-status compatibility, and real PostgreSQL root/cross-kernel/revocation evidence.

This PASS applies only to the dormant internal transaction-kernel foundation. It does not claim durable idempotency, revision protection, writer migration, or application-wide concurrency coverage.

## 18. Phase 3 Readiness

Phase 3 may begin only after independent approval. Required starting constraints are:

1. Preserve this dedicated Trip kernel; do not generalize the Plan kernel or activate routes.
2. Add a separately reviewed Trip durable collaborator using the frozen Trip HMAC envelope and Phase 1 `aggregate_scope='trip_planning_facts'` representation.
3. Implement the required Plan replay scope-mismatch rejection before any Trip-scope ledger outcome becomes active.
4. Keep revision checks/increments, business effect, and completed ledger outcome atomic in one owned transaction.
5. Preserve the local PostgreSQL independent-session authorization/lock evidence standard and expand it per writer.

No Phase 3/4, Preferences/Constraints writer, B.2D, B.3, B.4, production activation, commit, push, or merge started in this phase.
