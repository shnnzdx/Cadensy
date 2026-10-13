# Cadensy PR-05B — B.2D4 Generator Validate & Commit Integration

Date: 2026-10-12
Worktree: `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`
Branch: `codex/pr05b1-schema-foundation`
Scope: internal Generator composition only; no HTTP activation, migration, provider call, commit, push, or deployment.

## 1. Executive Summary

This phase adds the narrow internal entry point
`execute_protected_plan_generation(...)`. It composes the accepted B.2D1
detached snapshot, B.2D2 detached external work, and B.2D3 Generator durable
coordinator without changing the public legacy generator route.

The implemented execution graph is:

```text
Coordinator preflight
  -> matching authorized ledger replay: return historical outcome
  -> new command: B.2D1 read-only Prepare
       -> B.2D2 cache/provider/planner work on detached values
       -> B.2D3 final owned transaction
            Trip lock -> credential/membership/organizer recheck
            -> Plan cardinality -> Ledger recheck
            -> captured-root + full projection validation
            -> real GeneratedDraft or BlockedDraft persistence
            -> strict safe-outcome validation -> Plan revision -> Ledger -> commit
```

The public `POST /api/trips/{trip_id}/plans/generate` route still invokes
`plan_generator.generate_plan(...)` at `backend/app/api/main.py:1421`. This
phase has no route, feature flag, or production activation path.

**Verdict: PASS for the internal B.2D4 vertical slice.** B.2D4A corrected the
captured Plan-currency defect and closed the specific acceptance gaps identified
in the original report: existing-Plan rollback restoration, real PlanItem and
PlanChange flush failures, final-flush failure, Plan replacement/deletion/
duplicate lifecycle changes, organizer changes, and direct Legacy parity.
Focused PostgreSQL, Legacy CI-equivalent, and isolated-Pydantic regressions all
pass. This is not an HTTP activation or global-writer-coverage verdict.

## 2. B.2D1–B.2D3 Preconditions

The composition uses the accepted seams, rather than recreating them:

| Accepted phase | Used B.2D4 capability | Source evidence |
| --- | --- | --- |
| B.2D1 | Read-only detached `PreparedGeneration`, immutable draft values, semantic input projection | `generator_lifecycle.py:167-189`, `:194-337` |
| B.2D2 | Detached cache/provider/planner lifecycle returning only a draft | `generator_external_work.py:33-91`; Report 56 |
| B.2D3 | Preflight, final Ledger recheck, locked authorization, deterministic first Plan, outcome codec, Plan-only revision | `generator_durable.py:319-497`, `:499-655`; Report 57 |

No Generator UUID namespace, canonical HMAC input, aggregate scope, outcome
contract, or Plan/Trip generic durable kernel was changed. B.2D4A adds the
actual `Plan.currency` to B.2D1's immutable `PreparedPlan`; B.2D3 now copies
that captured Plan value rather than substituting the Trip currency. The only
coordinator extension remains two private fault-injection checkpoints after
first-Plan staging and safe-outcome construction (`generator_durable.py:417,431`).

## 3. Frozen Report 54 Protocol References

The implementation follows Report 54's frozen decisions:

- Replay comes before Prepare and stale validation; the final transaction also
  rechecks the Ledger before stale validation (Report 54 section 11.4).
- Generator is Plan-scope durable state: only `Plan.revision` changes for a
  state-changing result; `Trip.planning_facts_revision` does not
  (Report 54 section 11.6).
- Zero Plan creates the B.2D0B deterministic first Plan only in the final
  transaction; more than one Plan fails closed; a populated Plan is not
  overwritten (Report 54 sections 11.3–11.4).
- The immutable full projection, not merely its digest, is rechecked under the
  final root lock (Report 54 section 11.4).
- The durable outcome uses the frozen trusted v1 Generator contract only
  (Report 54 section 11.7).

## 4. Changed File Inventory

| File | B.2D4 change |
| --- | --- |
| `backend/app/domain/plans/generator_commit.py` | New internal lifecycle composition and real GeneratedDraft/BlockedDraft persistence adapter. |
| `backend/app/domain/plans/generator_lifecycle.py` | Extracted `capture_generation_snapshot()` from the accepted B.2D1 reader so Final Validate can build the same projection inside its already-owned transaction. `prepare_generation()` remains the owner of the read-only snapshot Session. |
| `backend/app/domain/transactions/generator_durable.py` | Narrow private checkpoint additions and documentation for B.2D4's real persistence callback; no envelope, identity, replay, or kernel change. |
| `backend/tests/test_generator_commit.py` | New real PostgreSQL B.2D4 integration suite, including generated/blocked writes, replays, stale validation, rollback, and root-lock evidence. |
| This report | Implementation and verification evidence. |

`generator.py` is reused as the Legacy source of truth for PlanItem/PlanChange
mapping. Its public generator entry point was not changed for B.2D4.

## 5. Complete Internal Generator Lifecycle

`execute_protected_plan_generation()` at
`backend/app/domain/plans/generator_commit.py:47-94` accepts a primitive
`PreliminaryActorScope`, a credential proof, a B.2D3 command, and a Session
factory. It never accepts a request Session or a request-scoped ORM membership.

1. `coordinator.preflight(...)` runs first.
2. A historical match returns immediately.
3. A new command calls B.2D1 `prepare_generation(...)`.
4. A pre-existing PlanItem fails Legacy's no-overwrite rule before external
   work.
5. B.2D2 `execute_generator_external_work(...)` produces a detached
   `GeneratedDraft` or `BlockedDraft`.
6. B.2D3 `execute(...)` obtains the final owned transaction and applies
   B.2D4's validation and persistence callbacks.

The outer integration has no `commit()`, `rollback()`, network call, or
provider/model implementation of its own.

## 6. Replay Preflight and External Work Ordering

`GeneratorDurableCoordinator.preflight()` uses the locked Trip transaction and
checks the shared `(trip, actor, idempotency key)` ledger row at
`generator_durable.py:319-356`. A valid matching row is decoded through the
historical and current trusted outcome policies before a `GeneratorDurableCommandResult`
is returned.

The B.2D4 entry point returns that result at `generator_commit.py:66-69`, before
it could create a snapshot Session, read cache, call the provider, call the
planner, construct a draft, or inspect current PlanItems. The real replay test
uses a Session factory/provider/planner that each fail if called and proves the
stored body and original item IDs are returned unchanged. A second test changes
the live Plan revision and Trip planning-facts revision, then proves the same
historical body and original resulting revision replay without Prepare.

## 7. Detached Snapshot Integration

`prepare_generation()` still owns a short PostgreSQL repeatable-read,
read-only transaction and closes it on all paths (`generator_lifecycle.py:167-192`).
The new `capture_generation_snapshot()` (`:194-337`) is the exact extracted
projection builder. It neither begins nor closes a transaction; B.2D4 calls it
only inside the B.2D3-owned final transaction to compare current facts with the
captured immutable value graph.

This reuse prevents a second, drifting definition of destination, dates,
currency, organizer readiness, Plan lifecycle, required constraints,
preferences, membership count, and planning-input fingerprint.

B.2D4A corrected a concrete gap in this promise: `PreparedPlan` now contains
the current `Plan.currency` (`generator_lifecycle.py:47-54,327-335`), and
`GeneratorExecutionPreconditions.from_prepared()` uses that field
(`generator_durable.py:260-274`). A regression starts with an empty Plan in
`EUR` while the Trip is `USD`: Final Validate accepts the unchanged captured
Plan, then the real Legacy-compatible mutation synchronizes it to `USD`. Before
this correction, the final checker compared `EUR` against an incorrectly
captured `USD` and falsely rejected a valid command as stale.

## 8. Provider/Cache Isolation Integration

B.2D4 passes only `PreparedGeneration` to B.2D2 at
`generator_commit.py:76-82`. `execute_generator_external_work()` owns its
short cache-reader/cache-writer Sessions and exposes detached planner values;
the provider callback receives a destination string, not an ORM Session
(`generator_external_work.py:33-91`). The final B.2D3 transaction begins only
after the external function returns.

Focused tests use fake Geoapify values and fake planner functions. No real
Geoapify or model call occurred. A cache side effect may survive a stale
Generator draft by design; it is a non-authoritative cache row, not a Plan or
Ledger effect.

## 9. Final Fresh Authorization and Validation

The final coordinator call uses `TripTransactionKernel._run_locked_command()`
with organizer role and all Trip memberships locked
(`generator_durable.py:489-497`). It freshly validates the supplied credential,
Trip membership, and organizer role before B.2D4 can read or mutate Plan state.

The B.2D4 validator at `generator_commit.py:97-116` additionally verifies that
the locked actor is the prepared organizer and that the organizer remains ready.
The credential-revocation integration test revokes the actual `AuthSession`
during external work; Final Commit fails with `InvalidCredential` and leaves no
Generator Plan or Ledger row.

## 10. Full Planning Input Projection Recheck

B.2D3 first checks captured Trip facts revision, Plan identity/revision/lifecycle,
and item count (`generator_durable.py:516-543`). It then runs the B.2D4 full
projection comparator inside a write-guarded rollback-only savepoint
(`:545-566`). B.2D4 compares the complete `PreparedGeneration` value, then its
fingerprint, and rejects ineligible current state.

The focused PostgreSQL suite proves all of the following without accepting a
stale Generator write:

- a legacy-bypass Preference fact changes without a revision;
- a real Phase 4 `constraint.create.v1` changes Trip facts during external work;
- a real Phase 4 `preferences.save.v1` changes Trip facts during external work;
- a credential is revoked during external work;
- another Generator command commits first under the same Trip root lock.

In each rejection, no generated Plan/PlanItem/PlanChange/revision/Generator
Ledger completion survives. The independent cache effect in the legacy-bypass
case remains permissible.

## 11. Real GeneratedDraft Persistence

`_persist_generated_draft()` at `generator_commit.py:135-168` converts only the
accepted detached draft items to Legacy `DraftItem` values and calls the exact
Legacy `_write_items()` mapper (`generator.py:1718-1763`). B.2D4 therefore
retains existing PlanItem fields, actual Place association, date/index/start
time placement, tags, source, coordinates, price semantics, and append-only
PlanChange construction. It does not implement another Plan writer.

After those rows flush, B.2D4 sets the Legacy result state: `active`, clear
blocked reason, Trip currency, `needs_refresh=False`, and the existing
known-total calculation. It constructs the response from these flushed rows,
so every returned item ID is a real persisted PlanItem ID.

The real zero-Plan test proves deterministic first-Plan identity, real items,
their persisted IDs, ordered day output, matching PlanChanges, `ai_generate`
and `rule_generate` item origins, aggregate `planner` provenance, status,
currency, unknown-total semantics, and a single completed plan-scope ledger.
The planner-outage test proves the existing rules fallback and `rule_generate`
origin are retained.

## 12. Real BlockedDraft Persistence

`_persist_blocked_draft()` at `generator_commit.py:170-193` makes only Legacy
blocked-state mutations: status, reason, total, and the `_get_or_create_plan()`
compatible currency assignment. It creates no PlanItem or PlanChange.

For a new Plan, the coordinator creates the deterministic first Plan then the
blocked state changes it; its revision becomes one and the completed outcome is
stored atomically. For an existing Plan already in the identical blocked state,
the callback reports `state_changed=False`. B.2D3's nested no-op guard rolls
back its savepoint and permits only the safe, replayable ledger outcome at the
unchanged Plan revision. `needs_refresh` is intentionally preserved for blocked
results because the Legacy blocked path does not reset it; B.2D4 does not claim
an unperformed refresh-state change.

## 13. Zero / One / Duplicate Plan Semantics

| State at Prepare/final validate | B.2D4 behavior |
| --- | --- |
| Zero Plan | Final transaction creates exactly the frozen deterministic first Plan; generated or blocked effects and Plan-scope Ledger commit together. |
| One empty Plan | The locked Plan ID is retained and populated/blocked in place. |
| One Plan with items | New command fails the Legacy no-overwrite rule before external work; no Generator Ledger row is written. A historical matching replay remains possible because it is decided before Prepare. |
| Different/replaced Plan, changed lifecycle, item count changed | B.2D3 root validation fails stale before mutation. |
| Duplicate Plans | B.2D1 Prepare and final kernel policy fail closed; no arbitrary Plan is selected. |

The one-empty-Plan integration test confirms identity preservation. The
pre-existing-item test proves provider/planner are not called and no Generator
Ledger completion is created.

## 14. PlanItem / PlanChange Mapping

The only row-writing algorithm is Legacy `_write_items()`:

- it writes one `PlanItem` for each detached accepted item;
- it flushes to obtain an actual ID before creating its `PlanChange`;
- it chooses only existing `ai_generate` or `rule_generate` origins;
- it uses existing safe change patch formatting and existing reasons.

The B.2D4 test reads database rows, rather than trusting the DTO only, and
asserts a one-to-one PlanItem/PlanChange relationship and correct provenance.
No new PlanChange event category, raw planner output, raw constraint text, or
provider JSON is introduced.

## 15. Plan Status / Total / Currency Semantics

Generated drafts set `active`, clear the blocked reason, preserve the Legacy
unknown-price-as-`None` total rule, use the current Trip currency, and reset
`needs_refresh` as Legacy successful generation does. Blocked drafts set
`blocked`, preserve the approved reason/code in the safe response, set total to
`None`, and do not fabricate child rows. Existing Plan currency is synchronized
to Trip currency just as Legacy `_get_or_create_plan()` does
(`generator.py:318-323`).

## 16. Plan-only Revision Ownership

For each state-changing generated or blocked command, B.2D3 increments the
locked Plan exactly once after the callback savepoint succeeds and before Ledger
staging (`generator_durable.py:440-465`). No Generator path increments
`Trip.planning_facts_revision`. A genuine blocked no-op holds the revision and
stores a completed plan-scope outcome whose resulting revision equals the
unchanged Plan revision.

The focused tests prove zero-Plan generation/blocked creation reaches Plan
revision 1, one-empty-Plan generation reaches revision 1 without replacement,
and identical blocked state stays at revision 0.

## 17. Safe Outcome Construction and Replay

`_safe_outcome()` at `generator_commit.py:195-224` includes only the frozen
Generator response fields: plan ID, public status/days, blocked fields,
provenance, `used_ai`, and sanitized structured planner notes. It takes
persisted rows for `days`, never draft placeholders or arbitrary ORM objects.

The coordinator validates that body against the trusted current outcome contract
before Ledger persistence, and validates historical plus current policies on
replay (`generator_durable.py:568-655`). The success test asserts the exact
top-level field set and verifies that a bearer token, `maximum_budget`, and
private constraint field names are absent from the serialized outcome. B.2D3
codec tests additionally cover malformed/unsupported nested fields and tampered
stored outcomes fail closed.

## 18. Cross-kernel Concurrency Evidence

Two real PostgreSQL tests use two independent worker connections, deterministic
Events, SQL instrumentation, and a third observer connection. They capture each
backend PID and query `pg_blocking_pids(waiter_pid)` while the first final
transaction holds its Trip root lock. No arbitrary sleep is used.

| Scenario | Observed result |
| --- | --- |
| Same idempotency key | Both calls reach detached external work. One final transaction creates the Plan and Ledger; the waiter blocks at `Trip FOR UPDATE`, then its final Ledger recheck returns the exact historical replay. One Plan revision and one Ledger row survive. |
| Different idempotency keys | Both calls reach detached external work. One final transaction commits; the waiter is proven blocked at `Trip FOR UPDATE`, then fails `StaleGeneratorPreconditions` after release. Exactly one Plan generation and one Ledger row survive. |

These tests prove root-lock serialization at the actual B.2D4 composition seam,
not merely at the synthetic B.2D3 callback seam.

## 19. Failure Injection and Rollback Evidence

The real persistence suite now injects deterministic failures at all B.2D4A
required boundaries:

1. after deterministic first-Plan staging;
2. after safe outcome construction, after real Plan/child mutation has flushed;
3. after callback/savepoint completion;
4. after Plan revision increment;
5. after Ledger staging;
6. on the second real `PlanItem` insert/flush, after an earlier item flush;
7. on the first real `PlanChange` insert/flush, after item persistence began;
8. during the final outer flush when the Ledger insert is issued.

The first six use the narrow B.2D3 checkpoint seam. The last three use a
temporary test-only SQLAlchemy `before_cursor_execute` listener armed only
inside Final Commit; it raises against the actual PostgreSQL statements and is
removed in `finally`. PostgreSQL verifies no generated Plan, PlanItem,
PlanChange, Generator Ledger, or revision survives any of these failures.

A separate existing-Plan test starts from a blocked, `EUR`, refresh-required,
revision-7 aggregate and injects after the real generated draft has flushed.
It proves the original status, blocked reason, total, currency, refresh flag,
revision, and zero-child state are all restored exactly.

## 20. Transaction Lifetime Evidence

- B.2D3 preflight returns before `prepare_generation()` is called.
- B.2D1 owns and closes its read-only snapshot Session before B.2D2 is called.
- B.2D2 has independently tested cache-reader/provider/cache-writer Session
  boundaries and exposes detached values only.
- B.2D4 starts B.2D3 Final Commit only after B.2D2 returns.
- The persistence callbacks receive a locked context, use the Plan's owned
  Session, and never call `commit()` or `rollback()`.
- Final credential authorization, root lock, Plan cardinality, Ledger recheck,
  stale validation, domain effects, Plan revision, and Ledger completion occur
  inside one B.2D3-owned transaction.

This is supported by B.2D2 lifecycle tests, the B.2D4 replay no-Prepare test,
and the actual final-root-lock tests. A single end-to-end event trace spanning
all four stages is not added; no production debug instrumentation was created.

## 21. Legacy Behavioral Parity

The public Legacy route and `generator.generate_plan()` remain unchanged. B.2D4
uses Legacy helpers for constraints, candidate handling, planner validation,
rules fallback, blocked classification, meal anchors, item mapping, totals,
day ordering, and output day construction. The focused B.2D1/B.2D2/B.2D4 tests
cover normal planner output, planner-unavailable rules fallback, no candidates,
one existing empty Plan, existing-item refusal, blocked output, and real day
ordering/provenance.

The intentionally different protected behaviors are only the frozen ones:
durable replay, deterministic zero-Plan identity, strict duplicate-Plan
rejection, fresh locked revalidation, and Plan-scope Ledger ownership.

B.2D4A directly compares Legacy and protected public semantics with the same
deterministic candidates and planner input for: normal planner output, invalid
planner output falling back to rules, and no-candidate blocked output. The
comparison excludes only persisted Plan IDs, then verifies status, blocked
fields, aggregate provenance, AI flag, ordered day/item public fields, and the
PlanChange-origin multiset. All three scenarios pass. Existing Generator,
Planner, and Place regressions retain budget/constraint/destination coverage.

## 22. Full Regression Test Results

All database commands first verified the following boundary:

```text
TEST_DATABASE_URL = postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_b2d4_test
DATABASE_URL      = postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_runtime_codex_b2d4
```

The test host was `localhost`, the test database name ended in `_test`, and the
runtime and test database names differed. PostgreSQL reported accepting
connections. The scheduler was disabled; `MOCK_AI=1`, membership-header bypass
was disabled, and both provider API keys were empty.

| Command / selection | Result |
| --- | --- |
| B.2D1–B.2D4, Generator, Place, and Planner focused selection | **147 passed** in 6.22s |
| Legacy CI-equivalent selection, partition A | **312 passed** in 10.05s |
| Legacy CI-equivalent selection, partition B | **280 passed** in 11.93s |
| Legacy CI-equivalent selection, partition C | **304 passed**, 10 Alembic config deprecation warnings, in 23.37s |
| Isolated Pydantic compatibility selection (its nine CI files) | **119 passed** in 6.89s |
| `python -m compileall -q app tests` | PASS |
| `git diff --check` plus B.2D4 trailing-whitespace scan | PASS |

The initial one-command Legacy selector reached 89% before the execution
window ended without a pytest summary, so it is deliberately not counted. The
three mutually exclusive file partitions above provide the completed current
CI-equivalent evidence: **312 + 280 + 304 = 896 passed**.

The complete reproducible pytest selectors are:

```powershell
# Legacy environment and focused B.2D4 acceptance
& 'C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe' -m pytest -q `
  tests/test_generator_commit.py tests/test_generator_durable_coordinator.py `
  tests/test_generator_external_work.py tests/test_generator_lifecycle.py `
  tests/test_plan_generation.py tests/test_places.py tests/test_planner.py

# Exact Legacy CI selector; the recorded 896 result was completed as the three
# mutually exclusive file partitions above to stay inside the desktop runner window.
& 'C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe' -m pytest -q `
  --ignore=tests/test_pydantic_ai_poc.py --ignore=tests/test_pydantic_runtime.py `
  --ignore=tests/test_pydantic_composition.py --ignore=tests/test_symmetric_evaluation_harness.py `
  --ignore=tests/test_formal_ab_runner.py --ignore=tests/test_provider_smoke_harness.py `
  --ignore=tests/test_live_provider_smoke_adapter.py

# Isolated Pydantic CI selector
& 'C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-pydantic-poc\Scripts\python.exe' -m pytest -q `
  tests/test_pydantic_ai_poc.py tests/test_pydantic_composition.py `
  tests/test_pydantic_runtime.py tests/test_symmetric_evaluation_harness.py `
  tests/test_formal_ab_runner.py tests/test_runtime_composition.py `
  tests/test_provider_smoke_harness.py tests/test_live_provider_smoke_adapter.py `
  tests/test_evaluation_foundation.py
```

## 23. Remaining Risks and Unmigrated Writer Coverage

- B.2D4 is internal-only. No public HTTP route, feature gate, deployment, or
  production activation exists.
- This work does not establish global protection against every unmigrated Legacy
  writer. The full projection comparator reduces stale draft risk but does not
  substitute for complete writer coverage.
- Real provider/model calls are deliberately untested and unauthorized; all
  external work evidence uses deterministic fakes.
- The existing B.2D3 outcome codec covers malformed/tampered outcomes, but the
  B.2D4 persistence adapter intentionally delegates that validation to the
  trusted codec rather than duplicating a second response validator.

## 24. B.2D4 Acceptance Verdict

**PASS — B.2D4 internal Generator Validate & Commit integration.**

Implemented and evidenced:

- the frozen replay → prepare → external → final recheck → validate → atomic
  commit lifecycle;
- real GeneratedDraft and BlockedDraft persistence through Legacy's mapper;
- deterministic zero-Plan identity and existing-empty-Plan preservation;
- exact Plan-only revision and Plan-scope Ledger behavior;
- historical replay without external work, including later aggregate changes;
- fresh authorization and full immutable-projection rejection;
- real PostgreSQL same-key and different-key root-lock serialization;
- original-Plan restoration and real child/final-flush rollback; and
- replacement, deletion, duplicate-Plan, and organizer-lifecycle rejection; and
- direct Legacy parity for normal, rules-fallback, and blocked outcomes; and
- Legacy and isolated Pydantic regression compatibility.

The report does not claim the unimplemented public HTTP adapter, production
activation, or global writer coverage are complete.

## 25. B.2D5 Readiness

**B.2D5 is not authorized or started.** B.2D4's internal acceptance prerequisite
is now satisfied, so B.2D5 may be considered only after separate explicit
authorization and review. Any future default-off HTTP adapter must preserve the
current feature-off route behavior and must not activate protected generator
traffic in production.
