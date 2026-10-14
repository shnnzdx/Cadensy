# PR-05B B.2D2 — Provider / Cache Transaction Separation

## 1. Executive Summary

**PASS — B.2D2 only.** A dormant internal External Work path now separates Place-cache reads, Geoapify execution, cache persistence, and B.2D1 pure-draft calculation. No public route, Generator gate, Plan persistence, revision increment, Ledger behavior, migration, scheduler, or real-provider call was introduced.

Lifecycle:

    PreparedGeneration
      -> owned cache-read Session / transaction / close
      -> optional injected provider(destination: str), with no Session
      -> owned cache-persist Session / transaction / close
      -> detached PlannerPlace tuple
      -> B.2D1 calculate_draft(...)

## 2. B.2D1 Preconditions and Accepted Contracts

B.2D1 is unchanged. prepare_generation() owns a PostgreSQL repeatable, read-only Session and projects facts before closing it (backend/app/domain/plans/generator_lifecycle.py:167-177). Its immutable PreparedGeneration is consumed without reopening that Session. calculate_draft() accepts detached PlannerPlace candidates and explicitly owns neither database nor provider lifecycle (generator_lifecycle.py:340-347).

This phase adds no fields to either B.2D1 value contract and never mutates its captured planning-facts revision or fingerprint.

## 3. Report 54 / B.2D0A Protocol Dependencies

The B.2D0A durable-protocol freeze remains an upstream dependency for later Generator replay and final validation/commit work. B.2D2 deliberately implements neither replay preflight, durable command identity, final stale-snapshot validation, nor plan persistence. Those B.2D3/B.2D4 concerns do not block this scoped transaction-separation phase.

## 4. Current Place Cache / Provider Call Graph

Legacy behavior is retained:

1. places_for_planner() calls _provider_places_for_destination() using the caller Session (backend/app/domain/places/service.py:95-103).
2. The helper normalizes destination, reads Place rows by normalized city, tests the existing count/language/category freshness rules, then calls geoapify.fetch_places() when stale (service.py:121-146).
3. It upserts by the existing (provider, provider_place_id) identity and flushes (service.py:296-324); the post-upsert lookup preserves the old city-canonicalization fallback (service.py:275-293).
4. _balanced_places() performs English-safe filtering, category round-robin, caps, quality ordering, and spatial spread before _database_place() builds PlannerPlace (service.py:340-374).

There is no TTL or updated_at cache policy. Freshness remains only count, legacy language support, and represented category groups (service.py:327-337). Place has created_at but no cache-update timestamp. Geoapify retains its ten-second timeout, geocoding/query shape, and normalized tuple output (backend/app/domain/places/geoapify.py:79-163).

## 5. Changed File Inventory

- backend/app/domain/places/service.py — detached snapshot, short read/persist collaborators, normalized result validation, shared Legacy-equivalent post-upsert fallback.
- backend/app/domain/plans/generator_external_work.py — dormant External Work composition entry point.
- backend/tests/test_generator_external_work.py — real PostgreSQL boundary, concurrency, rollback, parity, and writer tests.
- This report.

generator.generate_plan() and its HTTP route were not changed; Legacy remains default (backend/app/domain/plans/generator.py:160-213).

## 6. Cache Read Transaction Boundary

read_planner_cache() receives a Session factory, opens one owned Session, enters session.begin(), reads the existing city cache and unchanged freshness decision, projects it into PlannerCacheSnapshot, then closes in finally (service.py:149-173). It takes no row/root lock. Default PostgreSQL READ COMMITTED is deliberate: this is one coherent non-authoritative cache read, not a multi-query aggregate snapshot.

The returned snapshot holds only normalized destination text, tuple[PlannerPlace, ...], and needs_refresh (service.py:83-92). It cannot contain ORM rows, Result objects, a Session, connection, query, provider client, or private planning facts.

## 7. Provider Execution Boundary

execute_generator_external_work() invokes the provider only after the cache-read helper returned and closed its Session. It passes only normalized destination text (backend/app/domain/plans/generator_external_work.py:49-62). The provider receives no ORM object, Session, root lock, token, Ledger material, or unnecessary PreparedGeneration fields.

GeoapifyDestinationNotFound maps to B.2D1’s existing destination block; GeoapifyUnavailable uses the detached stale cache; an empty success retains stale candidates or reaches B.2D1’s no-candidate block (generator_external_work.py:63-88). Invalid injected output fails closed as ProviderResultInvalid before persistence.

## 8. Cache Persistence Transaction Boundary

persist_geoapify_cache() validates provider DTOs before a new owned Session. Its own session.begin() runs only existing _upsert_geoapify() semantics, projects candidates, commits, and closes in finally (service.py:176-217). It does not mutate Trip, Membership, Preference, Constraint, Plan, PlanItem, PlanChange, or MutationIdempotency, and increments no revision.

A concurrent uniqueness conflict rolls back, closes the failed Session, then makes one detached read of the winning cache without another provider call (service.py:205-213). Other SQLAlchemyError values raise controlled CachePersistenceFailure; fetched values are never silently accepted as a successful but unpersisted cache result. This preserves the Legacy classification where persistence failure propagates.

## 9. Detached Candidate DTO Contract

PlannerCacheSnapshot.candidates is tuple[PlannerPlace, ...]; PlannerPlace is frozen and its collection fields are tuples (service.py:49-89). Provider validation accepts only a tuple of normalized GeoapifyPlace values with non-empty identity/name and finite numeric coordinates (service.py:220-240). Duplicate provider IDs remain allowed so the existing last-value upsert behavior remains intact.

The output has no raw provider JSON, credential, constraint text, membership ID, prompt, or Ledger data.

## 10. B.2D1 Pure Draft Integration

The new composition delegates only after it holds detached candidates:

    calculate_draft(prepared, candidates, planner_call=...)

(generator_external_work.py:84-89). It neither imports nor copies planning heuristics. Candidate filtering, required-constraint behavior, planner-result validation, rules fallback, provenance, safe notes, totals, and blocked codes remain B.2D1 behavior.

## 11. Cache Freshness and Candidate Ordering Parity

Freshness calls the exact existing _cache_needs_refresh(); candidates call the exact existing _balanced_places() plus _database_place(). The PostgreSQL test test_cache_reader_returns_detached_candidates_with_legacy_order compares the entire detached tuple to places_for_planner(), rather than only asserting an active draft.

The suite also covers fresh no-provider behavior, stale refresh, empty results, non-Latin unusable candidates, duplicate provider IDs, and created_at preservation when an existing provider identity updates.

## 12. Provider Failure and Fallback Behavior

- Geoapify timeout/HTTP failure maps to GeoapifyUnavailable and uses stale candidates when present; both fake failure classes produce a valid draft.
- Missing cache plus provider failure, and missing cache plus an empty provider success, return B.2D1 NO_PLACE_CANDIDATES.
- destination_found=False returns the existing DESTINATION_NOT_FOUND blocked draft.
- Malformed output fails closed before cache persistence.
- Valid but non-Latin/no-English candidates persist as cache facts but are filtered by the existing candidate rule and therefore yield the existing no-candidate block.

No test invokes a real Geoapify endpoint.

## 13. Concurrent Refresh and Upsert Behavior

The existing (provider, provider_place_id) unique key remains the only identity mechanism. The concurrency test starts two independent External Work threads, blocks both providers with Events, then uses a SQLAlchemy after_cursor_execute barrier after each real PostgreSQL persistence SELECT FROM place before releasing both inserts. It proves two simultaneous provider calls, an actual shared-identity race, two successful detached drafts, exactly fourteen cache rows, and zero Trip/Plan/PlanItem effects. No distributed single-flight or migration was added.

## 14. PostgreSQL Session/Transaction Evidence

All database tests used the disposable localhost test database tripsync_pr05b2c_final_test, verified to resolve to 127.0.0.1 and differ from the runtime database. Scheduler and header bypass were disabled; AI and Geoapify keys were empty.

TrackingSession records actual owned Session.close() calls. The stale-cache test asserts the read Session is closed and transaction-free before provider invocation; the planner test asserts read and persistence Sessions are both closed and transaction-free before planner invocation. These are real PostgreSQL-backed Sessions, not mocks.

## 15. Provider-blocked / Constraint-Writer Concurrency Evidence

test_provider_block_does_not_hold_a_trip_lock_or_block_real_constraint_writer completes B.2D1 Prepare, then blocks fake Geoapify after cache-read closure. While blocked, it executes the accepted Phase 4 constraint.create.v1 writer through TripTransactionKernel on an independent Session. The writer commits, advances Trip.planning_facts_revision 0→1 and Plan.revision 0→1, while no PlanItem, PlanChange, or generator Ledger result exists. The only Ledger row is the expected Constraint writer outcome. The subsequent External Work result is detached.

This proves no Generator Trip-root lock or long Generator transaction is held during provider execution. It does **not** claim the now-stale draft can commit; B.2D4 must validate it before a final write.

## 16. Cache Persistence Failure Rollback Evidence

test_persistence_failure_rolls_back_cache_and_never_mutates_generator_aggregates injects SQLAlchemy OperationalError after _upsert_geoapify() flushes. The owned transaction rolls back: no new Place row survives, Trip and Plan revisions stay zero, and no PlanItem or Ledger row exists. The call raises controlled CachePersistenceFailure.

## 17. Planner Invocation Boundary Evidence

The fresh-path test proves its single cache reader closes before planner. The refresh-path test proves both reader and persistence Sessions close before planner. test_external_work_preserves_planner_unavailable_rules_fallback confirms the pre-existing deterministic rules fallback and provenance (generated_by="rules", used_ai=False).

## 18. Legacy Generator Regression Results

Legacy generate_plan() was kept public/default and still calls place_service.places_for_planner() (generator.py:199-213). The Legacy regression partition passed all **840** non-Pydantic tests with no failures/skips. Existing Places, plan-generation, and Planner coverage are included; the direct B.2D1/Place/Generator/Planner group separately passed **91** tests.

## 19. Exact Test Commands and Results

All commands used the regression or isolated Pydantic virtual environment with DISABLE_SCHEDULER=1, MOCK_AI=1, DEV_ALLOW_MEMBERSHIP_HEADER=0, empty DEEPSEEK_API_KEY, and empty GEOAPIFY_API_KEY.

| Command scope | Result |
| --- | --- |
| python -m pytest tests/test_generator_external_work.py -q | 13 passed |
| python -m pytest tests/test_generator_external_work.py tests/test_generator_lifecycle.py tests/test_places.py tests/test_plan_generation.py tests/test_planner.py -q | 91 passed |
| Phase 4 preferences/constraints, Phase 3 trip durable, Phase 2 trip kernel, and protected Plan-writer regression files | 204 passed |
| CI Legacy selector, seven Pydantic-only files excluded, run in three file partitions | 281 + 277 + 282 = 840 passed; 10 existing Alembic deprecation warnings |
| Isolated Pydantic CI selector | 119 passed |
| python -m py_compile app/domain/places/service.py app/domain/plans/generator_external_work.py app/domain/plans/generator_lifecycle.py app/domain/plans/generator.py | passed |
| git diff --check | passed |

No result was reported as passed merely because it was collected. No provider, AWS, RDS, or production database was accessed.

## 20. Remaining Risks

- Place cache is a shared non-authoritative cache; concurrent provider calls are intentionally permitted and may duplicate provider cost.
- Source has no cache TTL or updated_at provenance field; B.2D2 preserves that behavior rather than inventing one.
- Database transactions cannot roll back a provider’s external side effects.
- Durable retry semantics, Generator identity, and stale prepared-fact rejection are deliberately out of scope.

## 21. B.2D2 Acceptance Verdict

**PASS.** External Work has no owned database transaction spanning provider or planner execution; cache reads/writes use independent short-lived Sessions; candidates/drafts are detached; Legacy selection/fallback behavior is covered by parity tests; PostgreSQL tests demonstrate closure, rollback, concurrent upsert recovery, and a real protected writer committing while the provider is blocked.

## 22. B.2D3 Readiness and Dependencies

B.2D3 needs separate authorization. Remaining work:

1. settle the frozen Generator durable command/replay identity and preflight;
2. define locked validation of B.2D1 fingerprint, Trip planning-facts revision, and Plan state;
3. atomically persist/replay Plan/PlanItem/PlanChange/revision/Ledger outcomes;
4. retain this B.2D2 boundary so no provider/model operation runs in final validation/commit.

No B.2D3/B.2D4 implementation was started.
