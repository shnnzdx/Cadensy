# Cadensy PR-05B — Phase 3: Trip Durable Idempotency & Cross-Aggregate Ledger Integration

**Verdict: PASS — infrastructure only.** Phase 3 adds no public route, feature gate, Preferences/Constraints writer, migration, provider integration, commit, or deployment. It is ready for a separately authorized Phase 4 writer-adapter review.

## 1. Executive Summary

The implementation adds a dedicated `TripDurableCommandKernel`, rather than weakening or generalizing the existing Plan collaborator. It composes Phase 2's `TripTransactionKernel` with the reviewed B.2B snapshot/HMAC/keyring/outcome/no-op machinery. A real Trip planning-fact command increments `Trip.planning_facts_revision` once and, where exactly one Plan exists, increments that Plan's revision once in the same owned transaction. No Plan is manufactured for a zero-Plan Trip.

## 2. Frozen Protocol References

This implementation follows reports 43, 46, and 48–51, with report 49 as the frozen authority. In particular: global `(trip_id, actor_membership_id, idempotency_key)` uniqueness; `trip_planning_facts` scope; Trip → credential → membership → Plan → ledger ordering; fresh authorization before replay; zero/one/many Plan semantics; and no raw private command fields in the ledger.

## 3. Actual Phase 1–2 Preconditions

Verified before edits: Phase 1 supplies `Trip.planning_facts_revision`, `aggregate_scope`, mutually exclusive result columns, and their checks. Phase 2 supplies `TripTransactionKernel._run_locked_command()` with a new owned Session, fresh locked authorization, Trip-first root locking, zero-or-one Plan context, and duplicate-Plan failure before the callback. Existing source was inspected in `backend/app/domain/transactions/{kernel,trip_kernel,durable}.py`, `backend/app/domain/idempotency.py`, and `backend/app/db/models.py`.

## 4. Changed File Inventory

- `backend/app/domain/transactions/trip_durable.py` — new Trip command/result/kernel and Trip HMAC/replay/revision protocol.
- `backend/app/domain/transactions/durable.py` — explicit new Plan scope writes, Plan replay foreign-scope rejection, and the shared no-op guard's PostgreSQL transaction-write backstop.
- `backend/app/domain/transactions/__init__.py` — internal type exports.
- `backend/tests/test_trip_durable_command_kernel.py` — disposable PostgreSQL contract/security/concurrency evidence.

No Phase 1 migration, ORM schema, business service, writer, HTTP route, feature gate, or authentication policy changed in this phase.

## 5. Trip Durable Public Contract

`TripIdempotentCommand` contains the server-selected operation/resource identity, idempotency key, expected Trip facts revision, canonical immutable command fields, fingerprint version, and server-selected expiry. Its constructor snapshots `command_fields` with the established canonical snapshot before a callback can receive them. `TripDurableCommandResult` contains only detached status, safe JSON payload, resulting Trip facts revision, and replay flag. The public callback receives `LockedTripContext`, never a Session/commit API; the package-private synchronous writer seam retains the owned Session only for later reviewed adapters.

## 6. Canonical HMAC Envelope

The Trip HMAC uses the existing `canonical_request_hmac()` and the frozen v1 semantic envelope:

```json
{"aggregate_scope":"trip_planning_facts","trip_id":"locked","actor_membership_id":"locked","resource":{"kind":"server-selected","id":"stable"},"expected_trip_facts_revision":0,"command":{}}
```

The helper contributes the versioned operation envelope. It deliberately contains no `plan_id`. `canonical_payload_snapshot()` preserves NFC Unicode, ordered arrays, null-vs-omission, dates, and numeric normalization. The HMAC key version remains distinct from the fingerprint version. Only the digest, versions, and approved result are stored; raw command/private fields are not stored.

## 7. Shared Ledger Integration

New Trip rows explicitly write `aggregate_scope='trip_planning_facts'`, NULL `resulting_plan_revision`, and a non-null resulting Trip facts revision. Existing key lookup dimensions are unchanged. Replay checks a found row's scope before interpreting its HMAC or result: Plan rows are key reuse for Trip and Trip rows are key reuse for Plan. This prevents either aggregate parser from accepting foreign-scope data.

## 8. Minimal Plan Durable Scope Compatibility

New Plan durable rows explicitly write `aggregate_scope='plan'`. On a found record, `DurableCommandKernel._replay()` rejects every non-Plan scope with `IdempotencyKeyReused` before Plan HMAC/result handling. Its Plan fingerprint payload and replay result semantics remain unchanged. Both collision directions are covered by real PostgreSQL tests.

## 9. Atomic Trip / Plan Revision Semantics

For a state-changing zero-Plan command, only Trip facts revision advances. With exactly one locked Plan, both revisions advance exactly once after the callback savepoint succeeds and before the completed ledger row is flushed. Trip ledger rows store only the Trip result revision. A genuine no-op and replay advance neither. Both BIGINT values are checked before either increment; overflow rolls back the callback, both revisions, and the ledger. Duplicate Plans remain Phase 2's pre-ledger failure.

## 10. Safe Outcome and Replay Contracts

The server-owned `OutcomeContractRegistry` validates the first safe result before persistence. Replay validates retained HMAC key/version and fingerprint format, historical outcome contract, and current outcome policy; tightened current policy rejects a formerly allowed field. Result JSON is recursively restricted to JSON-safe primitives/containers with string keys, and rejects ORM/session/context/credential objects. Synthetic safe `{ "ok": true }` operations are used only in infrastructure tests; no Constraint conflict codec was introduced.

## 11. No-op / False-No-op Protection

The shared B.2B nested savepoint remains mandatory. A `state_changed=False` callback must leave no ORM dirty/new/deleted state and no flush/DML evidence. The guard now also reads PostgreSQL `pg_stat_xact_user_tables` before/after the callback in the same transaction. This is a fail-closed backstop for writable CTEs reported as `SELECT`, commented DML, and cursor paths without a usable completion tag. Any detected/ambiguous write raises `UnsafeNoOpMutation`; the savepoint and outer transaction roll back. The guarantee is transactional database persistence only: external/non-transactional side effects are prohibited from these callbacks and cannot be rolled back by this mechanism.

## 12. Credential and Membership Security

Every execute/replay invokes `TripTransactionKernel` first. Thus the Trip root, credential row, actor membership, and optional Plans are freshly locked and revalidated before ledger lookup. The Phase 2 account-first token behavior and active Guest status policy remain unchanged. Phase 3 replay tests prove a revoked Account credential cannot disclose a historical outcome.

## 13. Real PostgreSQL Concurrency Evidence

`test_concurrent_trip_commands_have_real_root_lock_contention_and_one_stale_result` uses independent Session connections and backend PIDs. Command A holds the Trip root; command B reaches `Trip FOR UPDATE`; a third independent PostgreSQL connection observes `pg_blocking_pids(B)` containing A. SQLAlchemy's pre-dispatch hook can occur before a query is server-blocked, so the test uses bounded repeated PostgreSQL observations rather than sleep. Once released, A commits one state change/revision/ledger result and B receives `StaleTripPlanningFactsRevision` with a different key.

## 14. Failure Injection and Rollback Evidence

The focused suite injects a failure after both revision increments and proves the Trip name, Trip revision, Plan revision, and ledger all roll back. It also tests false no-op ORM state, commented UPDATE, and writable CTE UPDATE. Existing B.2B test coverage additionally verifies ORM insert/delete, explicit flush, commit failures, and checkpoint failures under the same shared no-op/transaction implementation.

## 15. HMAC Rotation and Expiry Evidence

An old signed Trip result replays through a new active key when the old verification key is retained. Removing the old verifier raises `UnknownHmacKeyVersion`; no re-execution occurs. Retained expired rows raise `IdempotencyRecordExpired` and do not become reusable. No cleanup/deletion job was added.

## 16. Cross-Lifecycle Replay Evidence

A successful zero-Plan Trip command is recorded, then controlled test setup creates one Plan. Retrying the original key replays its original safe response and Trip revision without invoking the callback or changing either Trip or the newly created Plan revision.

## 17. Exact Regression Commands and Results

All database commands used `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_pr05b2c_final_test`, a distinct local disposable runtime URL, `DISABLE_SCHEDULER=1`, `MOCK_AI=1`, disabled membership-header bypass, and empty provider keys.

- Regression venv: focused Trip Durable suite: **18 passed**.
- Regression venv: Phase 1/B.1, B.2A/B.2B, Trip kernel/Durable, and auth: **137 passed**, **10 Alembic deprecation warnings**.
- Regression venv: accepted protected B.2C writers: **120 passed**.
- Legacy CI-equivalent exact workflow exclusion set: JUnit **771 tests, 0 failures, 0 errors, 0 skipped**. An earlier broad command intentionally documented no result because it mistakenly collected three Pydantic-only modules unavailable in the Legacy venv; the workflow-accurate command was then used.
- Isolated Pydantic environment: **119 passed**.
- Frozen evaluator: expected Legacy **6/8**, **0 safety violations**, at `C:\Users\zdxzh\AppData\Local\Temp\cadensy-phase3-legacy-eval-20261010-final`.
- `python -m compileall -q app`: passed.
- `git diff --check`: passed. (Untracked Phase work is separately compiled and covered by pytest.)

## 18. Remaining Risks

No Trip-bound HTTP adapter or real Preferences/Constraints writer exists. Server-side composition must select operation/resource/outcome contract/24-hour expiry and must not accept those choices from a client. The PostgreSQL transaction counter intentionally relies on the supported PostgreSQL target; no generic cross-database no-op claim is made. Ledger retention/cleanup remains intentionally deferred.

## 19. Phase 3 Acceptance Verdict

**PASS.** The dedicated Trip durable infrastructure, scope collision protection, revision atomicity, safe replay, key rotation/expiry behavior, false-no-op containment, and PostgreSQL root-lock evidence meet the frozen Phase 3 contract.

## 20. Phase 4 Readiness

**Ready for separately authorized Phase 4 only.** The next phase may compose the four frozen Preferences/Constraints writers through this collaborator after a source-backed adapter review. It must not treat this report as authorization for routes, feature activation, generator work, migrations, or production use.
