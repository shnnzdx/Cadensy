# Cadensy PR-05B — Phase 4: Preferences & Constraints Real Writer Migration

**Verdict: CONDITIONAL PASS — implementation is present and focused tests pass; the required complete Phase 4 acceptance matrix has not yet been executed. No public production activation is authorized.**

## 1. Executive Summary

Four default-off Trip-bound adapters were added for the existing Preferences/Constraints services. They compose `TripDurableCommandKernel`; they do not own Sessions, commits, retries, or a separate idempotency implementation. Existing Legacy paths remain the default.

## 2. Frozen Protocol References

Implemented against reports 49–52: Trip-first locking; zero/one/many Plan behavior; global ledger key namespace; Trip HMAC envelope; outcome replay; safe no-op containment; and dual revision only on a real planning-fact effect.

## 3. Verified Phase 1–3 Preconditions

Phase 1 schema provides Trip facts revision and mutually exclusive ledger scope/result columns. Phase 2 supplies fresh authorization and root acquisition. Phase 3 supplies replay, scope collision rejection, dual revision, savepoint containment, and outcome policy. The existing focused Trip Durable suite was re-run successfully.

## 4. Changed File Inventory

- `backend/app/domain/transactions/trip_preferences_common.py`
- `backend/app/domain/transactions/constraint_create.py`
- `backend/app/domain/transactions/constraint_update.py`
- `backend/app/domain/transactions/constraint_delete.py`
- `backend/app/domain/transactions/preferences_save.py`
- `backend/app/api/main.py`
- `backend/tests/test_trip_preferences_writers.py`

## 5. Legacy Behavior Audit

`save_mine()` validates date ranges, upserts `Preference`, writes `submitted_at`, sets membership to `preferences_submitted`, flushes, marks an existing item-bearing Plan `needs_refresh`, then returns the full owner-private DTO. `add_constraint()` validates one of six kinds, inserts public and private rows, flushes both, refresh-marks, and scans conflicts. `update_constraint()` owns the row, may update params/importance, flushes, refresh-marks, and scans. `delete_constraint()` owns the row, deletes private before public with flush ordering, then refresh-marks. Legacy routes commit and return their existing DTOs.

## 6. Constraint Create Implementation

`constraint.create.v1` uses the frozen actor-owned collection identity (`member_constraint_collection`, locked membership ID). Its signed snapshot includes kind, params, importance, original text, and visibility. The mutation delegates validation/insertion/refresh/conflicts to `pref_service.add_constraint()`. The durable outcome is only `{id, conflicts}`.

## 7. Constraint Update Implementation

`constraint.update.v1` locks the owned target scoped by membership and Trip before use. Its signed patch retains key presence, including explicit null. It precomputes whether a direct field difference or the Legacy derived `needs_refresh` transition exists. A true no-op only scans conflicts and returns `state_changed=False`; otherwise it invokes `pref_service.update_constraint()`.

## 8. Constraint Delete Implementation

`constraint.delete.v1` scopes/locks the public row and private companion, then delegates the private-before-public flush behavior to `pref_service.delete_constraint()`. Its outcome is `{deleted: true}`. Ledger replay precedes lookup, so a replay remains valid after deletion.

## 9. Preferences Save Implementation

`preferences.save.v1` signs every accepted PreferenceRequest field and delegates to `save_mine()`. A new key is always a real submission because Legacy writes `submitted_at`; only `{submitted: true}` is retained/returned by the protected path. The full private legacy DTO is not placed in the outcome.

## 10. Authorization and Ownership

The HTTP adapter converts the request identity only into primitive scope and token proof. The Trip kernel freshly locks/revalidates credential and membership before ledger replay. Constraint child lookups include actor membership and Trip. The existing account-first and Guest-active-state behavior was not modified.

## 11. Zero/One/Duplicate Plan Handling

The Trip kernel permits zero Plans, locks one Plan, and rejects duplicate Plans before ledger/callback. Trip Durable increments Trip only for zero Plans; for one Plan it atomically increments both Trip and Plan. No adapter creates a Plan.

## 12. Dual Revision and Refresh Semantics

Create/delete/save are state-changing. Update is a no-op only when params/importance and derived `needs_refresh` remain unchanged. Any Legacy refresh effect stays in the same owned transaction and therefore participates in the same dual revision outcome.

## 13. Durable HMAC and Replay

All adapters use `TripIdempotentCommand` and the frozen scope. Command payloads are snapshot-frozen before callback. Replays return stored safe outcomes without business lookup. No raw command field is inserted into the ledger.

## 14. Privacy-Safe Outcome Contracts

Conflict outcomes use a strict nested codec allowing exactly: constraint ID/code/text/scope/item ID/item title/day date/settledness with validated types. Dates are made JSON-safe. Private original text, visibility, params, budgets, availability, interests, and timestamps are excluded from outcome JSON.

## 15. HTTP Gates and Revision Discovery

Added the four requested default-off gates. Feature-on requires raw `Idempotency-Key` and `X-Expected-Trip-Facts-Revision`; parsing happens only in the branch. Production requested activation returns 503. Protected success writes `X-Trip-Facts-Revision`; the existing owner GET now returns the same discovery header without changing its JSON body. Stale errors map to 409 with `X-Current-Trip-Facts-Revision` after fresh authorization.

## 16. PostgreSQL Concurrency Evidence

Phase 3 root-lock/`pg_blocking_pids()` evidence remains valid for the shared Trip Durable infrastructure and was re-run as part of regression. The dedicated four-writer concurrency scenarios required by this phase have **not yet been added/executed**.

## 17. Failure Injection and Rollback

Phase 3 rollback/no-op tests remain green. Dedicated Phase 4 private-row deletion, membership-save, refresh, and ledger-insertion fault injection scenarios are **not yet complete**.

## 18. Exact Regression Commands and Results

All runs used disposable localhost `tripsync_pr05b2c_final_test`, distinct runtime URL, disabled scheduler, mock AI, disabled membership header bypass, and empty provider keys.

- `pytest tests/test_trip_preferences_writers.py -q`: **5 passed**.
- `pytest tests/test_preferences.py tests/test_trip_durable_command_kernel.py tests/test_durable_idempotency_kernel.py tests/test_trip_transaction_kernel.py -q`: **96 passed**.
- `py_compile` of the five new adapter modules plus `main.py`: passed.
- `git diff --check`: passed.

No Legacy CI-equivalent, isolated Pydantic, feature-gate HTTP, or complete dedicated PostgreSQL writer concurrency suite is claimed in this report.

## 19. Individual Writer Acceptance Matrix

| Writer | Verdict | Basis |
| --- | --- | --- |
| Constraint Create | CONDITIONAL PASS | Execution/replay/privacy and zero/one Plan tests passed; full concurrency/API matrix pending. |
| Constraint Update | CONDITIONAL PASS | True no-op and signed patch implementation reviewed; full ownership/rollback/API matrix pending. |
| Constraint Delete | CONDITIONAL PASS | Delete/replay path passed; private-delete fault injection/API matrix pending. |
| Preferences Save | CONDITIONAL PASS | Minimal outcome and membership transition passed; Guest/API/timestamp rollback matrix pending. |

## 20. Remaining Risks

The known remaining work is verification, not an approved architecture extension: route-level gate/error tests, all requested PostgreSQL writer concurrency/failure tests, full regression suites, and independent review of the exact HTTP error surface. The shared test HMAC injection remains default-off and requires server configuration when enabled.

## 21. Phase 4 Final Verdict

**CONDITIONAL PASS.** The implementation is narrow and first vertical tests are green, but the prompt requires a much larger test matrix before all four writers can be individually marked PASS.

## 22. B.2D Dependencies and Activation Boundaries

Nothing here authorizes B.2D, generator lifecycle work, production gate activation, migrations, cleanup, deployment, or any additional writer migration. Phase 4 must receive its remaining test/review acceptance before any such work.

---

## 23. Phase 4B — External Review Findings

The Phase 4B source audit confirmed three real findings before modification:

1. `OutcomeContract` and the generic durable encode/decode helpers previously limited policy enforcement to recursively JSON-safe values and allowed top-level keys. `TripDurableCommandKernel._replay()` applied only those top-level checks for historical and current contracts. Therefore a stored Constraint outcome with a malformed nested conflict could have passed replay.
2. `CORSMiddleware` allowed request headers but had no `expose_headers`; browser JavaScript consequently could not read either revision response header.
3. Legacy Constraint Create returns FastAPI's default `200`; the protected callback stored `201` while the protected route returned the default `200`.

The first is an original implementation defect in Phase 4. The second is an original HTTP integration omission. The third is an original durable/HTTP-status mismatch. None changes the frozen HMAC envelope, Phase 1 schema, Plan revision semantics, or a Legacy business service.

## 24. Nested Outcome First-write and Replay Validation

`OutcomeContract` now accepts an optional trusted server-owned `payload_validator`. The generic durable layer still checks JSON safety and top-level policy, then invokes that validator and re-applies the generic JSON safety boundary to its return value. The Trip durable layer invokes the validator for first execution, historical replay validation, and current-policy replay validation. Existing Plan contracts supply no validator and therefore retain their reviewed response behavior.

The four Phase 4 operation contracts register exact v1 codecs:

| Operations | Exact result contract |
| --- | --- |
| `constraint.create.v1`, `constraint.update.v1` | Exactly `id` (nonempty bounded string) and `conflicts` (list). Every conflict has exactly the reviewed eight fields, a known constraint code, `trip`/`item` scope, typed nullable item fields, and an ISO calendar date or null. |
| `preferences.save.v1` | Exactly `{ "submitted": true }`. |
| `constraint.delete.v1` | Exactly `{ "deleted": true }`. |

The conflict `text` projection is `SAFE_TEXT[constraint.kind]`, and `item_title` is the existing public `PlanItem.title`, as verified in `preferences/service.py:487-516`; neither is a private original constraint. The codec never reads `MemberConstraintPrivate`, and `original_text`, params, visibility, budgets, interests, membership identifiers, and diagnostic fields are rejected from outcomes.

Tampered-ledger tests cover invalid date, extra/private nested field, invalid scope, invalid nested type, missing nested field, wrong top-level ID type, invalid conflicts container, false literal values, extra literal fields, and unsupported historical version. Each rejected replay occurs before callback invocation and leaves revisions/rows unchanged. Valid Create and Update replays remain accepted. Existing Plan durable replay is covered by the passing B.2B regression suite.

## 25. CORS Response Header Correction

The existing restricted origins, methods, and request-header policy are unchanged. `CORSMiddleware` now exposes exactly `X-Trip-Facts-Revision` and `X-Current-Trip-Facts-Revision`. HTTP tests assert the actual `Access-Control-Expose-Headers` metadata for an allowed origin, successful GET/Create headers, stale-current header, absence of credential/authorization headers from the expose list, and no new `Access-Control-Allow-Origin` grant to an untrusted origin.

## 26. Durable/HTTP Status Alignment

Protected Constraint Create now records `CommandResponse(200, ...)`, matching both the feature-off Legacy route and the actual protected FastAPI response. Focused HTTP tests verify first execution and same-key replay return HTTP 200 with the same safe body and result revision. Update, Delete, and Preferences Save retain their existing 200 durable and HTTP status contracts.

## 27. Writer-specific PostgreSQL Concurrency Evidence

`test_concurrent_constraint_creates_prove_trip_root_contention_and_one_commit` uses two independent session factories and backend PIDs. The first real Create is held only at the reviewed post-domain checkpoint while retaining its actual `Trip FOR UPDATE`; the second reaches the same root acquisition. A third connection observes `pg_blocking_pids(second_pid)` and proves it is blocked by the first. Releasing the first yields exactly one committed Constraint/private pair, Trip facts revision increment, and ledger row; the second command receives `StaleTripPlanningFactsRevision`.

The suite also verifies same-key replay returns the original created ID and changed-payload same-key reuse fails with no duplicate rows. This is concrete writer-level evidence; it does not claim that the older Phase 3 infrastructure-only race alone proves the writer behavior.

## 28. Writer-specific Failure Injection Evidence

The reviewed `TripDurableCommandKernel._checkpoint` test seam was used only in tests to throw after the actual writer callback/savepoint and before revision/finalization. Evidence covers:

- Create: public row, private companion, revision, and ledger all roll back.
- Update: changed importance and any staged refresh effect roll back.
- Delete: public and private rows survive; no new durable outcome remains.
- Preferences Save: preference row, submitted membership status, revision, and ledger all roll back.

The Phase 3 durable tests additionally cover revision/ledger containment and no-op savepoint behavior. There is no production failure switch or automatic retry.

## 29. Guest/Account Authorization Regression

Account-backed Create/Update/Delete/Preferences are covered by the Phase 4 direct and HTTP cases. A real Guest credential test proves a joined Guest can submit Preferences, transitions to `preferences_submitted`, remains authorized for a subsequent protected Create, and then fails closed on replay after credential revocation. The test uses actual GuestSession hashing and no dependency override for protected kernel authorization.

The frozen Account-first bearer behavior remains exercised by the existing authentication/kernel regression suites. No membership policy was changed in this phase.

## 30. Zero/One/Duplicate Plan Matrix

The shared Trip kernel continues to permit zero Plans, atomically increment both revisions with one Plan, and reject duplicate Plans before writer mutation. Existing focused tests verify Create with zero Plan, Create with one Plan, Update no-op with zero Plan, and Delete replay after deletion. The kernel/migration suites verify the aggregate selection rule itself.

The remaining per-writer duplicate-plan and every update-edge permutation are not yet independently enumerated in this report; this is a verification gap, not a claimed implementation failure.

## 31. No-op and Timestamp Edge Cases

The accepted empty Constraint PATCH/no-Plan no-op remains verified: it returns the safe outcome but advances neither revision. The shared durable no-op guard remains responsible for preventing a false no-op from committing SQL. Preferences Save is intentionally a fresh-key state change because Legacy persists `submitted_at`; the test verifies the membership transition and durable revision path. Fine-grained timestamp precision and every one-Plan empty-PATCH permutation have not been independently expanded here.

## 32. Feature-gate HTTP Acceptance

The four independent gates remain default-off and non-production. Focused HTTP evidence verifies feature-off malformed protected headers do not alter Legacy Create behavior; each enabled writer returns 428 without required headers and succeeds with valid headers; protected Create replay/stale behavior is controlled; and protected activation requested under `APP_ENV=production` returns 503. No protected error falls back to Legacy.

## 33. Exact Test Commands and Results — Phase 4B

All database runs first confirmed `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_pr05b2c_final_test`, a disposable localhost database distinct from the runtime URL `tripsync_pr05b2c_final_runtime_test`. Scheduler was disabled; mock AI enabled; membership-header bypass disabled; provider credentials empty. No AWS/RDS or provider call occurred.

| Command | Passed | Failed | Skipped | Notes |
| --- | ---: | ---: | ---: | --- |
| `python -m pytest tests/test_trip_preferences_writers.py -q` | 27 | 0 | 0 | Real PostgreSQL outcome, HTTP/CORS, concurrency, rollback, Guest, and gate coverage. |
| `python -m pytest tests/test_trip_bound_phase1_migration.py tests/test_trip_transaction_kernel.py tests/test_trip_durable_command_kernel.py tests/test_transaction_kernel.py tests/test_durable_idempotency_kernel.py tests/test_idempotency_fingerprint.py tests/test_idempotency_schema.py -q` | 130 | 0 | 0 | 10 Alembic deprecation warnings only. |
| `python -m pytest` for existing Plan writer suites (Manual Item, Booking, Change Proposal, Vote, Proposal Decision, Notice Objection, Round Settlement) | 120 | 0 | 0 | B.2C Plan vertical regression. |
| `python -m pytest` isolated Pydantic compatibility command from CI | 119 | 0 | 0 | Executed in `.venv-pydantic-poc`. |
| `python -m pytest tests/test_trip_preferences_writers.py tests/test_preferences.py -q` | 43 | 0 | 0 | Phase 4 plus Legacy Preferences regression. |
| `python -m py_compile` of Phase 4 modules, `main.py`, and Phase 4 tests | n/a | 0 | n/a | Passed. |
| `git diff --check` | n/a | 0 | n/a | Passed. |
| Full Legacy CI-equivalent collection | not recorded | not recorded | not recorded | The command was started in this desktop session, but its final pytest summary was not returned; it is deliberately not represented as a pass. |

## 34. Changed File Inventory — Phase 4B

- `backend/app/domain/transactions/durable.py`
- `backend/app/domain/transactions/trip_durable.py`
- `backend/app/domain/transactions/trip_preferences_common.py`
- `backend/app/domain/transactions/constraint_create.py`
- `backend/app/domain/transactions/constraint_update.py`
- `backend/app/domain/transactions/constraint_delete.py`
- `backend/app/domain/transactions/preferences_save.py`
- `backend/app/api/main.py`
- `backend/tests/test_trip_preferences_writers.py`
- this report

## 35. Remaining Risks

There is no identified code-level blocker in the focused corrected paths. Acceptance remains conditional because the exhaustive Phase 4B matrix is not complete: independent writer-level contention has been demonstrated for Create but not separately for same-key followers or a Trip writer versus a real Plan-bound writer; every writer's zero/one/duplicate-plan and all PATCH/timestamp permutations are not independently enumerated; and the full Legacy CI command lacks a captured final summary in this session. These are verification gaps, not claims of successful coverage.

## 36. Final Individual Writer Verdicts — Phase 4B

| Writer | Verdict | Reason |
| --- | --- | --- |
| Constraint Create | CONDITIONAL PASS | Strict first/replay codec, status alignment, CORS/API, root-lock contention, rollback, same-key replay/conflict, and Guest evidence pass; exhaustive matrix remains incomplete. |
| Constraint Update | CONDITIONAL PASS | Strict codec, direct real mutation/no-op path, rollback, and feature-gate evidence pass; complete edge/aggregate matrix remains incomplete. |
| Constraint Delete | CONDITIONAL PASS | Strict literal codec, replay-after-delete, rollback, and feature-gate evidence pass; complete aggregate/authorization matrix remains incomplete. |
| Preferences Save | CONDITIONAL PASS | Strict literal codec, Guest lifecycle, rollback, header/gate evidence pass; timestamp and full lifecycle matrix remains incomplete. |

## 37. Overall Phase 4B Acceptance Verdict

**CONDITIONAL PASS.** The three review defects are corrected and backed by focused real PostgreSQL and HTTP evidence. The remaining status is solely because the prompt's complete writer-by-writer acceptance matrix and a captured full Legacy CI summary have not yet been completed. No Phase 4B result authorizes B.2D, B.3, B.4, production activation, migration changes, commits, pushes, or deployment.

---

## Phase 4C — Final Acceptance Closure

### 1. Verification Summary and Final Source Audit

Phase 4C inspected the final `trip_kernel.py`, `trip_durable.py`, `durable.py`, shared preference helpers, all four adapters, Legacy Preferences service, API routes, and the focused writer/accepted Booking tests. The Phase 4B corrections remain present: server-owned nested codecs run at first write and historical/current replay; Plan HMAC envelope and Plan outcome behavior remain unchanged; CORS exposes only the two reviewed Trip headers; Create returns durable and HTTP 200; all four gates are default-off and production-requested activation is 503; callbacks receive no commit/rollback API; outcomes exclude private request data.

All protected commands compose the same Trip durable collaborator. It freshly authorizes before lookup, rejects duplicate Plans before replay, replays before stale-revision rejection, snapshots accepted semantics before callback, and atomically updates Trip and (when present) Plan revision only for a state-changing callback. No migration or Generator code was added or changed in Phase 4C.

### 2. Cross-Kernel PostgreSQL Concurrency

`test_constraint_writer_blocks_real_protected_booking_then_stales_it` executes an actual protected Constraint Create and the accepted protected Booking writer through independent sessions. The Constraint callback is retained at the reviewed post-domain checkpoint; Booking reaches the same `Trip FOR UPDATE`. An independent observer reads `pg_blocking_pids(booking_pid)` and verifies the Constraint backend PID is its blocker. After release:

- exactly one Constraint/private pair and its Trip-scope ledger outcome commit;
- Trip facts revision advances 0 → 1 and Plan revision advances 0 → 1;
- Booking, sent with Plan revision 0, fails `StalePlanRevision`;
- no Booking business effect and no Booking ledger result commit.

The observer initially revealed a test-only race: SQLAlchemy's `before_cursor_execute` occurs immediately before the waiter is visible in PostgreSQL. Both writer contention tests now perform a bounded observer poll for the actual server-side blocking relation. This is not a production or protocol defect; the assertion still rests on `pg_blocking_pids()`, not timing.

### 3. Zero / One / Duplicate Plan Matrix

The real adapter suite now executes each Create, Update, Delete, and Preferences Save adapter under duplicate Plans and confirms `DuplicatePlans` before callback/replay, unchanged revisions, and no new ledger result. Existing and expanded cases cover zero Plan success/Trip-only result, one Plan dual revision increments, replay with no increment, and genuine update no-op with no Plan. No artificial Plan is created and no unique Plan migration was introduced.

### 4. Constraint Update Edge Matrix

PostgreSQL matrix coverage includes empty patch with no Plan, Plan with no items, item-bearing clean Plan, already-refreshing Plan, identical/different importance, params-only, and explicit-null patch forms. It verifies direct versus derived refresh behavior, row persistence, `needs_refresh`, Trip/Plan revision, and fresh durable result. A separate test proves same-key replay after unrelated state returns its original result revision, while a new stale key gets `StaleTripPlanningFactsRevision`.

### 5. Preferences Submission / Timestamp Semantics

Independent Sessions verify initial no-Plan submission, a second fresh key with identical fields, same-key replay, stale rejection, membership transition, one-Plan dual revision, and minimal Ledger outcome. `Preference.submitted_at` is database-persisted and the second fresh key is a real durable state change even with identical fields; the implementation's timezone-aware `_now()` and PostgreSQL timestamp values are compared as persisted values rather than object identity or sleep. Replay returns the original stored revision and writes no second submission.

### 6. Ownership, Guest, HTTP, and Privacy Evidence

Two valid same-Trip Account identities each create their own Constraint. Cross-member Update/Delete and cross-Trip ID guesses fail with the same non-enumerating protected missing result, leave no ledger entry, and preserve revisions. Guest tests cover joined → preferences_submitted → later protected Constraint authorization and revoked replay denial. Existing focused HTTP tests cover all four gates, default-off malformed-header compatibility, missing headers, stale/current revision headers, production 503, replay, allowed-origin CORS metadata, and untrusted-origin non-grant. Outcome tampering coverage continues to reject nested/private/untyped stored values before callbacks and does not expose raw private data.

### 7. Final Regression Results

All database commands used the disposable localhost `tripsync_pr05b2c_final_test`, distinct from the runtime URL; scheduler was disabled, mock AI enabled, membership-header bypass disabled, and provider credentials empty.

| Command group | Final result |
| --- | --- |
| `python -m pytest tests/test_trip_preferences_writers.py -q` | **44 passed**, 0 failed, 0 skipped |
| Phase 1 schema + Phase 2/3 kernel + B.2A/B.2B + all accepted B.2C Plan writer suites | **250 passed**, 0 failed, 0 skipped; 10 known Alembic deprecation warnings |
| Full Legacy CI-equivalent collection with exactly the CI's Pydantic-only ignores | **815 passed**, 0 failed, 0 errors, 0 skipped; final JUnit result captured in a unique local temp artifact |
| Isolated Pydantic compatibility CI command | **119 passed**, 0 failed, 0 skipped |
| Python compilation / `git diff --check` | passed |

### 8. Current Changed-file Inventory

Phase 4C changed only `backend/tests/test_trip_preferences_writers.py` and this report. The test adds the real Constraint/Booking contention proof, duplicate aggregate adapter coverage, Update edge matrix, Preferences fresh-key behavior, and account ownership tests; it also corrects test cleanup order and the test observer race. Phase 4B implementation files remain the only source changes for the actual writer behavior.

### 9. Final Verdicts and Boundaries

| Writer | Final verdict | Basis |
| --- | --- | --- |
| Constraint Create | **PASS** | Strict outcome codec/replay, HTTP/CORS/status, same-key behavior, zero/one/duplicate aggregate, cross-kernel contention, ownership, rollback, Account/Guest evidence. |
| Constraint Update | **PASS** | Strict codec/replay, full no-op/refresh patch matrix, stale/replay, duplicate aggregate, ownership, rollback, gate evidence. |
| Constraint Delete | **PASS** | Strict literal codec/replay after deletion, duplicate aggregate, cross-member/cross-Trip protection, rollback, gate evidence. |
| Preferences Save | **PASS** | Strict literal codec/replay, fresh-key timestamp/submission semantics, one/zero/duplicate aggregate, Guest transition/revocation, rollback, gate evidence. |

**Overall Phase 4 final verdict: PASS.** The four scoped writers have concrete shared concurrency, security, replay, privacy, aggregate, HTTP, and full-regression evidence. This PASS does not authorize B.2D, B.3, B.4, public production activation, Generator integration, migrations, cleanup, deployment, commits, pushes, or merges. Those remain separately gated work.
