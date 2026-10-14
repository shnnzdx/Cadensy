# Cadensy PR-05B — B.2D1 Detached Snapshot & Pure Draft Seam

**Verdict: PASS — B.2D1 only.** This is a detached Prepare/draft foundation. It is not a Generator durable command, a provider/cache lifecycle, a Plan creation or persistence path, a revision writer, a Ledger/replay path, or a public API activation.

## 1. Executive Summary

Two internal seams now exist:

1. `prepare_generation(session_factory, trip_id, organizer_membership_id)` owns a fresh, short PostgreSQL `REPEATABLE READ, READ ONLY` Session and returns a fully detached immutable `PreparedGeneration`.
2. `calculate_draft(prepared, candidates, *, planner_call)` accepts only that snapshot, detached candidate DTOs, and an explicitly supplied planner capability. It returns immutable `GeneratedDraft` or `BlockedDraft` values and has no Session, provider/cache, Domain-write, revision, or Ledger capability.

The legacy `generator.generate_plan()` and its route are not replaced. The only reuse-point adjustment is an optional private planner callable passed through `_build_day()` / `_planner_day()`; Legacy still resolves `planner.plan_day` dynamically when no callable is supplied.

## 2. Authoritative Report 54 References

Report 54 now incorporates B.2D0A and marks that protocol **DESIGN FROZEN — PASS** (`54_PR05B2D_GENERATOR_LIFECYCLE_AUDIT.md:7`). Its frozen lifecycle is Replay Preflight → Prepare → External Work → Validate & Commit (`:22-25`). It explicitly authorizes B.2D1 independently, with no coordinator, cache ownership, schema change, or route activation (`:836-839,852-855`).

This implementation deliberately does not encode B.2D0A's durable protocol. In particular, no stable Generator resource ID, deterministic first-Plan ID, HMAC envelope, Ledger preflight, expiry/replay behavior, final stale check, or Commit exists in this slice.

## 3. Confirmed Existing Generator Call Graph

| Existing concern | Source evidence | Classification / B.2D1 handling |
| --- | --- | --- |
| Admission and existing-item guard | `generator.py:160-181`, `296-313` | Mixed read/write legacy entry; Prepare reproduces only its authoritative reads/organizer readiness and captures item count without throwing away the snapshot. Draft retains no-overwrite by raising `PlanAlreadyHasItems` before candidate calculation. |
| Legacy destination normalization and Plan selection | `generator.py:184-188`, `316-324` | Legacy writes destination and may create/update a Plan; forbidden in B.2D1. Prepare normalizes into a DTO only. |
| Constraints, interests, budgets, availability | `generator.py:358-485` | Database reads projected to immutable facts in `generator_lifecycle.py:229-319,432-491`. |
| Candidate acquisition | `generator.py:190-201`; `places/service.py:72-125` | Mixed cache/provider operation; not invoked by either B.2D1 seam. Candidate DTOs are caller-supplied. |
| Candidate ranking, planner validation, deterministic fallback and meals | `generator.py:497-1715` | Existing pure calculations are reused, not copied. B.2D1 injects the model callable only at `_planner_day`. |
| PlanItem/PlanChange creation, status/totals and API-output construction | `generator.py:241-292,1718-1827` | Legacy writes/output remain unmodified. B.2D1 creates detached equivalent draft metadata only. |

The smallest safe extraction point was therefore the planner callable at `generator.py:497-543,614-707`. It has a `None` default that resolves `planner.plan_day` at runtime (`:662`), preserving existing Legacy monkeypatch and model behavior.

## 4. Changed File Inventory

| File | Change |
| --- | --- |
| `backend/app/domain/plans/generator_lifecycle.py` | New detached snapshot/value contracts, read-only Prepare, semantic input digest, and pure draft calculation. |
| `backend/app/domain/plans/generator.py` | Minimal optional `planner_call` plumbing for private pure-day reuse; public `generate_plan()` is unchanged. |
| `backend/tests/test_generator_lifecycle.py` | New focused PostgreSQL snapshot/concurrency and pure-draft parity tests. |
| This report | Implementation evidence only. |

No migration, ORM model, API route, feature gate, durable kernel, cache/provider implementation, frozen PR-04 artifact, frontend, or production configuration was changed.

## 5. PreparedGeneration Data Contract

`PreparedGeneration` (`generator_lifecycle.py:35-116`) is a frozen, non-verbose value graph:

- `PreparedTrip`: IDs, persisted and normalized destination, date bounds/materialized date tuple, currency, and `planning_facts_revision`.
- `OrganizerReadiness`: only organizer membership ID, role/status, and submitted-preference readiness result.
- `PreparedPlan | None`: ID, revision, lifecycle fields, and item count. Zero Plan is `None`.
- `PreparedPlanningInputs`: current membership count; required structured constraints; only preference fields consumed by Generator; derived interests, tightest submitted budget, availability facts, day slots, and preferred activity counts.
- `generation_eligible`: false when the one captured Plan already has items; no regeneration behavior is introduced.
- `planning_input_fingerprint`: SHA-256 over canonical JSON of the calculation-relevant projection.

`PreparedConstraint.params` is recursively frozen with mapping proxies and tuples (`:615-621`). Private companion rows are never queried. `PreparedPreference` intentionally contains only fields read by current Generator semantics; no email, bearer token, raw private wording, or ORM relationship crosses the seam.

## 6. PostgreSQL Isolation and Session Ownership

Prepare requires a caller-supplied fresh Session factory rather than a request Session. It rejects non-PostgreSQL binds, starts one local transaction, and makes its first SQL statement:

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY
```

(`generator_lifecycle.py:167-187`). It then materializes scalar values and closes the Session on every return/error path (`:188-338`). This neither changes the Engine's connection-global isolation nor permits write statements in the transaction.

The concurrency test uses independent PostgreSQL connections and a deterministic `after_cursor_execute` Event barrier (`test_generator_lifecycle.py:397-446`): the snapshot reads Trip first, a second Session commits a currency/budget update, the original snapshot sees the coherent old pair, and a new snapshot sees the coherent new pair.

## 7. Zero / One / Duplicate Plan Behavior

| Captured cardinality | B.2D1 behavior |
| --- | --- |
| Zero | Valid `PreparedGeneration.plan is None`; no Plan is inserted. |
| One | Capture ID, revision, status, blocked reason, refresh flag, and `PlanItem` count without mutation. |
| More than one | Raise approved `DuplicatePlans` before draft work; never select an arbitrary first row. |
| One with items | Return the lifecycle observation with `generation_eligible=False`; `calculate_draft()` preserves Legacy no-overwrite by raising `PlanAlreadyHasItems`, with no side effect. |

The source implementation is at `generator_lifecycle.py:216-235,297-331`; focused coverage is `test_generator_lifecycle.py:335-358`.

## 8. Planning Input Projection and Fingerprint

The projection mirrors the real Generator read set:

- normalized destination, dates, day slots, currency, Trip planning-facts revision;
- organizer role/status/submitted preference readiness;
- all Membership rows only as an unfiltered count, matching `generator._trip_membership_count()`;
- required machine constraints in current `created_at` order, with current single-member budget-ceiling exclusion;
- current Generator interest ordering/deduplication, submitted budget cap, and submitted availability rules;
- zero/one Plan lifecycle and item observation.

The digest is `sha256(canonical_request_json("generator.planning-input.v1", ...))` (`generator_lifecycle.py:541-613`), reusing the existing deterministic Unicode/date/numeric/order canonicalizer (`idempotency.py:35-66,118-159`). It is internal validation metadata, not an HMAC, durable command fingerprint, authorization credential, Ledger value, or serializability claim.

`persisted_destination` is captured for later Legacy compatibility validation but intentionally excluded from the semantic digest: current calculation consumes its normalized form, so equivalent normalized destinations do not acquire artificial digest differences. Fingerprint stability/change coverage is at `test_generator_lifecycle.py:377-395`.

## 9. Privacy Boundaries

- `MemberConstraintPrivate` and `original_text` are not selected.
- Structured constraints retain opaque membership IDs only because the existing Constraint engine needs them internally; snapshots/drafts do not expose them through diagnostics or result DTOs.
- Snapshot dataclasses suppress verbose field representation where they contain sensitive planning inputs.
- No raw bearer, account email, guest token, HMAC signing material, Ledger payload, or provider raw response is accepted or emitted.
- Planner notes are retained only through existing safe `DayPlannerNote` aggregation, and no generated item has a persistent PlanItem ID.

## 10. Detached GeneratedDraft / BlockedDraft Contract

`calculate_draft()` (`generator_lifecycle.py:340-429`) returns:

- `GeneratedDraft`: detached days/items, immutable `Poi`, per-item current `ai_generate`/`rule_generate` origin and matching reason, aggregate provenance, `used_ai`, safe notes, and existing total semantics (`None` remains unknown, never free).
- `BlockedDraft`: current blocked reason/code, empty detached items, and safe empty day structure. It has no Plan/PlanItem reference and makes no persistence decision.

Candidate values are copied into fresh `Poi` values with tuples for every nested candidate sequence (`:623-647`), so a caller's mutable list/tag container cannot alter a returned draft.

## 11. Existing Generator Logic Reuse

The new seam reuses the original helpers for date/availability slots, blocked classification, candidate conversion/ranking, planner output validation, deterministic fallback, required-constraint evaluation, meal anchoring, completeness, totals, and aggregate provenance. It does not duplicate a second rule engine.

Explicit planner injection is required for the new seam. Thus it cannot accidentally construct a model client or call the global model/provider path. Legacy remains unchanged because its private helper receives `None` and performs the exact prior dynamic `planner.plan_day` lookup.

## 12. Parity Test Evidence

`test_generator_lifecycle.py:448-629` proves representative real business semantics:

- injected valid planner result produces active planner provenance and the same day/item placement, total result, and `PlanChange` origin set as Legacy generation;
- `AgentUnavailable`, unknown candidate IDs, and unusable planner times go through existing planner validation then deterministic rules fallback;
- no candidate input yields current `NO_PLACE_CANDIDATES`; explicit upstream destination failure yields `DESTINATION_NOT_FOUND`; no shared availability yields `AVAILABILITY_BLOCKED`;
- current no-overwrite remains an explicit `PlanAlreadyHasItems` failure;
- returned values stay independent when caller candidates or nested tags are mutated.

The existing Generator/Places/Planner suite additionally passed unchanged (66 tests), supporting that Legacy behavior was not replaced.

## 13. PostgreSQL Snapshot / Concurrency Evidence

Focused PostgreSQL tests (12 passed) cover:

- valid organizer; missing Trip; non-organizer; missing submitted organizer Preference;
- zero, one empty, one populated, and duplicate Plan rules;
- structured constraints, membership count, budget, availability, date/day-slot, interests, destination normalization, private-text boundary, fingerprint stability/change;
- no Plan/PlanItem insertion, no revision change, no destination persistence, and SQL statement observation without DML;
- no ORM object/lazy relationship escapes by construction: results consist only of frozen dataclasses, scalars, tuples, and mapping proxies after the owned Session closes;
- genuine independent-connection, PostgreSQL repeatable-read coherence during concurrent commit.

Database safety was independently checked before executing tests: `TEST_DATABASE_URL` resolved `localhost` → `127.0.0.1`, database name `tripsync_pr05b2c_final_test`; it differed from `tripsync_pr05b2c_final_runtime_test`; scheduler was disabled; mock AI was enabled; membership-header override and provider keys were disabled/empty.

## 14. Destination Normalization Compatibility

Legacy mutates `Trip.destination` before candidate lookup (`generator.py:184-188`). B.2D1 records both original and normalized strings but performs no mutation. Tests verify persisted `nyc` remains `nyc` while the snapshot uses `New York City` (`test_generator_lifecycle.py:289-333`).

This intentionally does **not** decide whether a later B.2D4 Commit should preserve the legacy destination write, move it into a separately reviewed compatibility action, or remove it under an approved product migration. That decision remains explicit; B.2D1 silently changes no public behavior because the legacy route still uses legacy Generator.

## 15. Legacy Generator Regression Results

The modified private injection point preserves the default Legacy planner resolution. Concrete evidence:

- `tests/test_plan_generation.py tests/test_places.py tests/test_planner.py -q`: **66 passed**.
- CI-equivalent Legacy boundary, excluding only the workflow's Pydantic-only/evaluation modules: **827 passed, 10 warnings** in 50.75 seconds. The warnings are existing Alembic `path_separator` deprecations from `test_trip_bound_phase1_migration.py`, not B.2D1 failures.

## 16. Exact Test Commands and Counts

All commands were run from `backend/` with the verified disposable environment above:

```powershell
# Red, before implementation
...python.exe -m pytest tests/test_generator_lifecycle.py -q
# Result: collection error, ModuleNotFoundError: generator_lifecycle (expected TDD red)

...python.exe -m pytest tests/test_generator_lifecycle.py -q
# Result: 12 passed

...python.exe -m pytest tests/test_plan_generation.py tests/test_places.py tests/test_planner.py -q
# Result: 66 passed

...python.exe -m pytest tests/test_idempotency_fingerprint.py tests/test_idempotency_schema.py tests/test_transaction_kernel.py tests/test_durable_idempotency_kernel.py tests/test_trip_transaction_kernel.py tests/test_trip_durable_command_kernel.py -q
# Result: 125 passed

...venv-pydantic-poc\Scripts\python.exe -m pytest -q tests/test_pydantic_ai_poc.py tests/test_pydantic_composition.py tests/test_pydantic_runtime.py tests/test_symmetric_evaluation_harness.py tests/test_formal_ab_runner.py tests/test_runtime_composition.py tests/test_provider_smoke_harness.py tests/test_live_provider_smoke_adapter.py tests/test_evaluation_foundation.py
# Result: 119 passed

...python.exe -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --ignore=tests/test_pydantic_runtime.py --ignore=tests/test_pydantic_composition.py --ignore=tests/test_symmetric_evaluation_harness.py --ignore=tests/test_formal_ab_runner.py --ignore=tests/test_provider_smoke_harness.py --ignore=tests/test_live_provider_smoke_adapter.py
# Result: 827 passed, 10 existing warnings

...python.exe -m py_compile app/domain/plans/generator.py app/domain/plans/generator_lifecycle.py tests/test_generator_lifecycle.py
git diff --check
# Result: passed
```

No tests were skipped in either focused suite. The CI-equivalent Legacy collection was independently verified at 827 tests.

## 17. Remaining B.2D2 Dependencies

B.2D2 can consume `PreparedGeneration` and `calculate_draft`, but must still introduce and prove a narrow cache/provider lifecycle:

1. candidate cache read, remote Geoapify call, and cache persistence must be short-lived and outside a root/write transaction;
2. provider receives no owned Domain transaction Session and no private snapshot logging;
3. current cache fallback, destination failure mapping, candidate ordering/balancing, and no-provider behavior require parity tests;
4. B.2D2 must not add durable replay, Plan persistence, revision increment, route activation, or a real provider test.

## 18. Explicit B.2D3 Protocol Dependencies

B.2D3 remains blocked on accepted B.2D2 evidence and must separately implement the frozen B.2D0A protocol:

- stable Generator command/resource identity and new operation-specific envelope without changing Plan/Trip HMAC bytes;
- Ledger-first Replay Preflight before Prepare, then Ledger-first final commit behavior;
- deterministic zero-Plan candidate creation only in Commit; no Plan creation in Prepare;
- fresh root locking, credential/membership authorization, zero/one/duplicate Plan validation, stale-input validation, and no-overwrite check after External Work;
- strict safe outcome codec, key-version/fingerprint-version compatibility, atomic revision/PlanChange/Ledger rollback, and real PostgreSQL concurrency tests.

None of this is implemented or claimed by B.2D1.

## 19. Final B.2D1 Verdict

**PASS.** The snapshot is genuinely detached and PostgreSQL read-only; the returned values retain no ORM object, Session, Result, relationship, source-owned mutable map/list, PlanItem, or persisted Plan reference. The draft seam owns no database or provider lifecycle and cannot mutate authoritative state. Existing Generator computation is reused with concrete parity/regression evidence. No public or durable integration occurred.

## 20. B.2D2 Readiness

**READY FOR SEPARATE AUTHORIZATION.** The B.2D1 DTO/draft boundary is available for the provider/cache-only slice. B.2D2 must retain all current hard boundaries above, and B.2D3/B.2D4 remain explicitly unstarted.
