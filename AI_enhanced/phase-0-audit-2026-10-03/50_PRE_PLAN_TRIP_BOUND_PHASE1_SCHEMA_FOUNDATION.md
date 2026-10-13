# Cadensy PR-05B — Phase 1: Trip Revision & Ledger Schema Foundation

**Status:** completed schema-only implementation and local PostgreSQL validation, 2026-10-10. This report does not authorize or claim implementation of a Trip transaction kernel, protected Trip-bound routes, revision increments, or any writer migration.

## 1. Executive Summary

Phase 1 adds the frozen dormant schema representation for future Trip planning-facts commands:

- `trip.planning_facts_revision BIGINT NOT NULL DEFAULT 0` with a nonnegative check;
- scope-aware completed outcomes in the existing `mutation_idempotency` ledger; and
- one additive Alembic revision, `c2a4b6d8e0f1`, following the verified B.1 head.

The migration rejects incompatible historical Plan ledger results before it changes schema, does not expose sensitive row values, and refuses a downgrade that would discard the only result-revision representation of a Trip-scope outcome. No application write path increments the new Trip revision in this phase.

**Phase 1 verdict: PASS.** The next phase remains separately gated by the frozen protocol; schema availability is not activation.

## 2. Frozen Protocol References

- `49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md`, especially sections 3, 8, 9, and 10, is the authoritative Phase 0 contract.
- `48_PRE_PLAN_TRIP_BOUND_TRANSACTION_ARCHITECTURE_AUDIT.md` is historical audit context superseded by report 49 where they differ.
- Historical architecture decisions were read from the canonical worktree: `41_PR05B_REVISION_IDEMPOTENCY_DESIGN.md` and `43_PR05B_FINAL_PROTOCOL_DECISIONS.md`.
- Existing B.1/B.2A/B.2B evidence remains in reports 44–46. This phase neither revises their Plan HMAC envelope nor changes their runtime behavior.

## 3. Verified Starting Schema

Before this change, `Trip` had no planning-facts revision (`backend/app/db/models.py:111-145`). The existing B.1 `Plan.revision` is a separate `BIGINT NOT NULL DEFAULT 0` with `ck_plan_revision_nonnegative` (`backend/app/db/models.py:252-274`).

`MutationIdempotency` was already the sole completed-outcome ledger with:

- nullable `resulting_plan_revision`;
- `UNIQUE(trip_id, actor_membership_id, idempotency_key)`; and
- `ix_mutation_idempotency_expires_at`.

Those B.1 fields and the uniqueness/index remain unchanged. Existing pytest fixtures build the active ORM schema with `Base.metadata.create_all()` (`backend/tests/conftest.py:108-120`); Phase 1 therefore adds a separate migration test that starts at B.1 rather than treating metadata creation as migration evidence.

## 4. Alembic Revision Graph

Verified with `python -m alembic -c alembic.ini history --verbose` and `heads`:

```text
f9ff23b7b84d baseline
  -> 54e8dd1ca624 bounded guest sessions
    -> 6f77e775d201 PR-05B.1 schema foundation
      -> c2a4b6d8e0f1 PR-05B Phase 1 Trip/ledger schema (only head)
```

`c2a4b6d8e0f1` declares `down_revision = "6f77e775d201"` (`backend/alembic/versions/c2a4b6d8e0f1_pr05b_phase1_trip_ledger_schema.py:17-18`). There were no multiple heads or ambiguity. The unmerged B.1 revision is preserved and was not edited.

## 5. Exact Schema Changes

The ORM additions are at `backend/app/db/models.py:111-145` and `276-336`; the database migration is `backend/alembic/versions/c2a4b6d8e0f1_pr05b_phase1_trip_ledger_schema.py:43-103`.

```text
trip
  planning_facts_revision BIGINT NOT NULL DEFAULT 0
  CHECK planning_facts_revision >= 0

mutation_idempotency
  aggregate_scope VARCHAR(32) NOT NULL DEFAULT 'plan'
  resulting_trip_planning_facts_revision BIGINT NULL
  CHECK aggregate_scope IN ('plan', 'trip_planning_facts')
  CHECK resulting_plan_revision IS NULL OR resulting_plan_revision >= 0
  CHECK resulting_trip_planning_facts_revision IS NULL
        OR resulting_trip_planning_facts_revision >= 0
  CHECK exactly one result is populated for the selected scope
```

The existing Plan result stays nullable at the column level; the combined scope/result check gives it the required non-null meaning only for `aggregate_scope='plan'`. The existing global Trip/actor/key uniqueness and expiry index are retained exactly, so scope cannot bypass idempotency-key reuse rules.

## 6. ORM / Server Default Parity

`Trip.planning_facts_revision` uses SQLAlchemy `BigInteger`, `nullable=False`, `default=0`, and `server_default=text("0")`. `MutationIdempotency.aggregate_scope` uses `String(32)`, `nullable=False`, `default="plan"`, and `server_default=text("'plan'")`. Both result revisions are nullable `BigInteger` mappings.

Focused tests inspect ORM defaults and a migrated PostgreSQL database, including type, nullability, server-default presence, check-constraint names, and the existing expiry index (`backend/tests/test_trip_bound_phase1_migration.py:29-42, 179-227`). The current-metadata PostgreSQL tests additionally verify new Trip default, positive/negative/null handling, both valid scope shapes, every invalid result shape, and unchanged uniqueness (`backend/tests/test_idempotency_schema.py:58-80, 177-300`).

## 7. Historical Ledger Preflight

The migration executes a count-only preflight before adding any column or check (`c2a4b6d8e0f1...:23-41`). It rejects every historical B.1 ledger row whose `resulting_plan_revision` is null or negative—the only historical conditions that would violate the new Plan-scope result contract once existing rows receive the server default `aggregate_scope='plan'`.

The failure message reports only the number and non-sensitive class of incompatible rows. It neither prints HMAC/outcome/credential values nor fabricates a revision, relabels data, deletes data, or modifies Plan outcome content. The required remediation is a separately approved retention/quarantine/data-remediation decision followed by a rerun; this migration intentionally offers no automatic repair.

## 8. Upgrade and Downgrade Safety

On upgrade, PostgreSQL supplies `0` to existing Trip rows and `'plan'` to existing ledger rows using server defaults. Existing Plan result/HMAC/outcome values are not updated. The focused migration test captures the B.1 `request_hmac`, canonical PostgreSQL `outcome_json::text`, and Plan result before upgrade, then proves the exact captured values remain while only plan scope is added (`backend/tests/test_trip_bound_phase1_migration.py:179-233`).

Downgrade is allowed only when no `trip_planning_facts` outcome exists. Before it drops the new representation, it performs a count-only check (`c2a4b6d8e0f1...:105-119`). Any Trip-scope row raises a controlled `RuntimeError`; no data is deleted. Clean downgrade removes only the four new checks/columns, in reverse dependency order. These outcomes were exercised against PostgreSQL in the focused suite.

## 9. PostgreSQL Test Evidence

All database commands first used a loopback PostgreSQL URL, a clearly disposable database name, distinct test/runtime database names, `DISABLE_SCHEDULER=1`, `MOCK_AI=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and empty DeepSeek/Geoapify keys. No RDS, AWS, production database, or provider was contacted. The dedicated migration fixture additionally creates and deletes only `tripsync_phase1_schema_test` after requiring a test-only local name.

| Command | Result |
| --- | --- |
| `pytest tests/test_idempotency_schema.py tests/test_trip_bound_phase1_migration.py -q` | **15 passed** in 4.70s; 10 Alembic config deprecation warnings |
| `pytest tests/test_idempotency_fingerprint.py tests/test_idempotency_schema.py tests/test_transaction_kernel.py tests/test_durable_idempotency_kernel.py -q` | **85 passed** in 2.07s |
| Legacy CI-equivalent pytest collection with all Pydantic-only files ignored, plus JUnit no-skip check | **731 collected, 0 failures, 0 errors, 0 skips** |
| `python -m evals.runner --output %TEMP%\\cadensy-phase1-legacy-eval-20261010` | **PASS** — frozen expected summary: 8 total, 6 passed, 2 approved Legacy mismatches, 0 safety violations |
| Isolated Pydantic CI-equivalent suite | **119 passed** in 8.76s |

The migration tests specifically prove clean B.1 upgrade, preservation/backfill, malformed historical Plan result rejection without side effects, clean downgrade, and safe downgrade rejection after a Trip-scope row. They also inspect migrated checks/defaults/indexes, establishing ORM/migration alignment for this frozen schema surface.

The exact test commands were executed from `backend` after setting the local safety environment described above:

```powershell
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest tests\test_idempotency_schema.py tests\test_trip_bound_phase1_migration.py -q
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest tests\test_idempotency_fingerprint.py tests\test_idempotency_schema.py tests\test_transaction_kernel.py tests\test_durable_idempotency_kernel.py -q
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --ignore=tests/test_pydantic_runtime.py --ignore=tests/test_pydantic_composition.py --ignore=tests/test_symmetric_evaluation_harness.py --ignore=tests/test_formal_ab_runner.py --ignore=tests/test_provider_smoke_harness.py --ignore=tests/test_live_provider_smoke_adapter.py --junitxml=$env:TEMP\cadensy-phase1-legacy-junit-localhost.xml
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m evals.runner --output $env:TEMP\cadensy-phase1-legacy-eval-20261010
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-pydantic-poc\Scripts\python.exe -m pytest -q tests\test_pydantic_ai_poc.py tests\test_pydantic_composition.py tests\test_pydantic_runtime.py tests\test_symmetric_evaluation_harness.py tests\test_formal_ab_runner.py tests\test_runtime_composition.py tests\test_provider_smoke_harness.py tests\test_live_provider_smoke_adapter.py tests\test_evaluation_foundation.py
```

TDD evidence: the new ORM-contract test was first run before implementation and failed because `Trip.planning_facts_revision` did not exist; it passed after the model/migration implementation. A first database test run found two mistakes in the new test assertions (not application/schema failures); those assertions were corrected before the final 15-pass run. During an initial Legacy execution, local PostgreSQL was still recovering and `localhost` selected a rejecting IPv6 listener; the one resulting evaluation-foundation host-literal failure was rerun after recovery with the CI-standard `localhost` URL and then passed. No project test remains failed or unexecuted in the listed commands.

## 10. Backward Compatibility Results

The existing B.1 schema/fingerprint and B.2A/B.2B kernel test modules passed unchanged (the 85-test focused command above). Existing Plan-scope kernel writes remain valid because the ORM default supplies `aggregate_scope='plan'` and their completed ledger record supplies `resulting_plan_revision`.

No Plan HMAC bytes, ledger uniqueness tuple, replay code, kernel, authentication, public route, writer, generator, scheduler, or runtime contract was modified. The Phase 1 columns are dormant; the application has no new Trip-scope writer and no new header/API activation.

## 11. Changed File Inventory

| File | Phase 1 change |
| --- | --- |
| `backend/app/db/models.py` | Trip revision/default/check and scoped ledger fields/checks/default. |
| `backend/alembic/versions/c2a4b6d8e0f1_pr05b_phase1_trip_ledger_schema.py` | New additive migration, historical preflight, guarded downgrade. |
| `backend/tests/test_idempotency_schema.py` | Focused PostgreSQL current-schema constraints/default/uniqueness coverage. |
| `backend/tests/test_trip_bound_phase1_migration.py` | B.1-to-Phase-1 PostgreSQL upgrade/downgrade and preflight evidence. |
| `AI_enhanced/phase-0-audit-2026-10-03/50_PRE_PLAN_TRIP_BOUND_PHASE1_SCHEMA_FOUNDATION.md` | This implementation report. |

Pre-existing uncommitted B.1–B.2C files were preserved. No commit, push, merge, migration application outside disposable tests, or worktree cleanup was performed.

## 12. Remaining Risks

1. The schema is not a transaction protocol: no code owns Trip revision increments, expected Trip revision checks, new locks, replay, or authorization revalidation.
2. Plan durable replay does not yet branch on `aggregate_scope`; Phase 0 requires a later explicit Plan-scope mismatch rejection before Trip writers are activated.
3. Existing database deployments with malformed historical Plan result rows cannot use this migration until separately approved remediation is completed.
4. A downgrade after any real Trip-scope outcome is intentionally blocked; this is data preservation, not a cleanup solution.
5. Full writer coverage, Guest status alignment, preview bracketing, generator integration, retention/rotation operations, and production activation remain later gates.

## 13. Phase 1 Acceptance Verdict

**PASS.** The implemented additive schema matches the frozen Phase 0 representation, is PostgreSQL-validated from the actual prior B.1 migration head, fails closed for unsafe historical outcomes, and preserves the existing Plan ledger contract.

This verdict is limited to schema foundation. It does not claim that Preferences, Constraints, any Plan writer, runtime preview, or durable Trip command is operational.

## 14. Phase 2 Readiness

Phase 2 implementation may be considered only after independent approval. Its minimum prerequisites are:

1. Use this migration as the sole schema foundation; do not rewrite B.1 history or alter the global Trip/actor/key uniqueness.
2. Implement the dedicated Trip transaction/durable protocol frozen in report 49, not a nullable/generalized Plan kernel.
3. Add the future explicit Plan replay scope-mismatch guard before any Trip-scope ledger record can be produced by activated writers.
4. Keep all Trip revision/Plan revision increments atomic and writer-owned; this phase deliberately contains no increment logic.
5. Retain disposable-local PostgreSQL, independent-connection, lock/interleaving, authorization, privacy, and rollback evidence as the acceptance standard.

No Phase 2, B.2D, B.3, B.4, production activation, or provider work started here.
