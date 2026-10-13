# Cadensy PR-05B — B.2D3 Generator Durable Coordinator

## 1. Executive Summary

**Verdict: PASS for the narrow B.2D3 coordinator; B.2D4 is not started.**

B.2D0B later froze the two UUID-v5 namespaces, the bare canonical JSON-array
name encoding, and golden vectors missing when this report was first created.
The original blocker is recorded in the prior version of this report and is
resolved by the approved B.2D0B correction in Report 54.

This implementation adds a Generator-only durable coordinator. It performs
historical replay preflight before any B.2D1 prepare or B.2D2 external-work
step, then does a separate final owned transaction with a fresh replay check.
B.2D3 permits only a controlled synthetic Plan mutation seam: it does not
persist `GeneratedDraft`, `PlanItem`, `PlanChange`, or `UpdateNotice`, call a
provider, or add HTTP integration.

## 2. Original Blocker and B.2D0B Freeze Verification

The original report correctly declined implementation because Report 54 named
but did not provide literal namespace UUIDs. B.2D0B is now the authoritative
protocol correction:

| Frozen detail | Value / evidence | Implementation |
| --- | --- | --- |
| Command namespace | `16bc0061-4ecf-56d5-99a5-f073dc64768b` | `generator_durable.py:64` |
| First-Plan namespace | `550665b9-3451-5dbf-8f40-466f85e1a214` | `generator_durable.py:65` |
| UUID name bytes | NFC strings in compact, UTF-8 bare JSON array; `ensure_ascii=False`, `separators=(',', ':')`, `allow_nan=False` | `generator_durable.py:92-106` |
| Golden vectors | ASCII and NFC-sensitive tuples and lower-case UUID hex | `test_generator_durable_coordinator.py:271-299` |
| Existing ledger schema | Plan-scope result revision / Trip result revision contract | `db/models.py:276-338` |

Reports 55 and 56 remain unchanged: B.2D1 owns detached facts and pure draft
preparation; B.2D2 owns provider/cache separation. B.2D3 consumes only their
detached boundary.

## 3. Changed File Inventory

| File | B.2D3 change |
| --- | --- |
| `backend/app/domain/transactions/generator_durable.py` | New narrow identity, replay, final-transaction, outcome-codec, and synthetic-mutation coordinator. |
| `backend/app/domain/transactions/__init__.py` | Re-exports explicit public B.2D3 contracts only. |
| `backend/tests/test_generator_durable_coordinator.py` | Real PostgreSQL contract, rollback, replay, and contention tests. |
| This report | Implementation evidence and scope boundary. |

No migration, route, feature gate, provider, real draft persistence, or frozen
PR-04 artifact changed.

## 4. Exact Command and Identity Contract

`GeneratorIdempotentCommand` (`generator_durable.py:146-167`) contains exactly
the validated idempotency key, fingerprint version, future expiry, and frozen
`initial_or_empty_plan.v1` contract. It has no client-supplied mutable
generation payload.

The immutable HMAC envelope at `generator_durable.py:640-671` is:

```json
{
  "aggregate_scope": "plan",
  "trip_id": "<locked trip>",
  "actor_membership_id": "<locked actor>",
  "resource": {"kind": "generator_command", "id": "<uuid-v5>"},
  "command": {"generation_contract": "initial_or_empty_plan.v1"}
}
```

It uses the existing versioned canonical snapshot/HMAC primitives. Captured
facts and Plan revision are deliberately not HMAC input; they are final-lock
stale checks. `generation_resource_id` and `deterministic_first_plan_id`
(`generator_durable.py:110-143`) use separate namespaces, so command and Plan
identities cannot collide.

## 5. B.2D1 / B.2D2 Boundary and Preflight

`GeneratorExecutionPreconditions.from_prepared()` at
`generator_durable.py:244-260` adapts B.2D1's immutable `PreparedGeneration`
without rebuilding the projection. It captures Trip planning-facts revision,
zero/one-Plan lifecycle facts, item count, and B.2D1's input fingerprint.

`GeneratorDurableCoordinator.preflight()` (`generator_durable.py:319-356`) has
no draft, provider, cache, or mutation callback parameter. It acquires a fresh
owned transaction, authenticates/re-authorizes the organizer under the Trip
root lock, locks only the candidate ledger row, and yields either a detached
historical result or explicit unreserved `NEW`. A missing ledger creates no
Plan and no reservation, so preparation/external work can start only after
`NEW`; a replay is returned before either stage.

## 6. Final Transaction, Locking, and Fresh Authorization

`execute()` (`generator_durable.py:358-500`) composes the existing
`TripTransactionKernel._run_locked_command()` (`trip_kernel.py:133-169`). The
fresh Session owns commit, rollback, and close. Its ordered sequence is:

```text
Trip FOR UPDATE
  -> AuthSession / GuestSession FOR UPDATE
  -> actor TripMembership FOR UPDATE
  -> all TripMembership rows in ascending id (final execution only)
  -> all Plan rows for Trip in ascending id
  -> generator ledger row FOR UPDATE
  -> Plan child facts / controlled synthetic mutation
```

The Trip lock is first (`trip_kernel.py:181-190`); credentials and memberships
are freshly checked afterward (`trip_kernel.py:218-300`). `execute()` requests
ordered all-membership locking. Duplicate Plans fail closed before replay or
effects.

The final transaction reads the ledger before captured facts. A matching ledger
replays despite later Plan/item/planning-fact changes. A new command checks every
captured root fact and item count (`generator_durable.py:510-541`) and invokes
the future B.2D4 locked projection comparator only in a rollback-only,
write-guarded savepoint (`generator_durable.py:543-563`).

## 7. Ledger, First-Plan, Revision, and No-op Rules

For a zero-Plan Trip, only the final transaction creates the deterministic Plan
using locked Trip currency (`generator_durable.py:400-418`). A cross-Trip
identity collision is an integrity failure and rolls back.

For a state-changing mutation, the callback savepoint commits, then exactly one
Plan revision advances with overflow protection, and the completed Plan-scope
`MutationIdempotency` row flushes in the same outer transaction
(`generator_durable.py:421-492`). Trip planning-facts revision remains unchanged.

A declared no-op must pass `_NoOpMutationGuard`; its callback savepoint is then
rolled back. A zero-Plan command cannot complete as a no-op, preventing an
unversioned new Plan. The coordinator does not trust the callback's declared
`state_changed` value.

## 8. Strict Generator Outcome and Replay Policy

`generator_outcome_contract()` and `_validate_generator_outcome()`
(`generator_durable.py:291-300`, `680-806`) are trusted server policy. They
allow exactly these top-level fields:

```text
plan_id, status, days, blocked_reason, blocked_code,
generated_by, used_ai, planner_note
```

The codec rejects unknown top-level and nested fields, invalid dates/order,
non-finite numbers, invalid coordinates, non-NFC strings, and invalid
blocked-state combinations. This stops a synthetic callback or stored ledger
payload from injecting private constraints, identities, credentials, provider
payloads, or arbitrary fields.

Replay (`generator_durable.py:564-638`) requires fresh authorization; Plan
scope; operation/resource identity; supported and matching fingerprint versions;
HMAC verification with the stored signing-key version; unexpired retention;
valid result scope; and both historical and current trusted outcome policies.
Key rotation succeeds while the retained historical key exists; a missing key,
expired record, key reuse, scope mismatch, or newly forbidden outcome fails
closed.

## 9. PostgreSQL Evidence

`backend/tests/test_generator_durable_coordinator.py` contains **24 passing
tests** using independent SQLAlchemy Sessions/connections against disposable
`localhost/tripsync_b2d3_test`, distinct from runtime
`localhost/tripsync_runtime_codex_b2d3`.

| Acceptance area | Test evidence |
| --- | --- |
| Frozen UUID protocol and NFC | `test_frozen_generator_uuid_identity_golden_vectors` (271-299) |
| B.2D1 DTO adapter | `test_execution_preconditions_adapt_the_detached_b2d1_snapshot_without_projection_copy` (300-348) |
| Missing-ledger preflight is effect-free | `test_preflight_missing_ledger_returns_new_command_without_reserving_key` (350-367) |
| Zero-Plan creation, Plan-only revision, replay | `test_zero_plan_final_execution_creates_one_plan_then_preflight_replays` (368-418) |
| Replay precedes stale validation | `test_preflight_replays_before_current_plan_or_trip_facts_stale_checks` (419-456) |
| Existing Plan no-op / exactly-once revision | tests at 457-511 |
| False no-op mutation rollback | `test_false_noop_plan_mutation_rolls_back_effect_and_ledger` (512-534) |
| New-command stale facts | `test_final_execution_rejects_stale_captured_facts_before_mutation_or_ledger` (535-556) |
| Same-key wait/recheck/replay | test at 557-653 |
| Different-key wait then stale fail | test at 654-742 |
| Fresh authorization and duplicate-Plan failure | tests at 743-786 |
| Expiry, private field, HMAC rotation | tests at 787-855 |
| Cross-kernel and deterministic-ID collisions | tests at 856-938 |
| Atomic checkpoints / validator write guard | tests at 939-983 |
| Malformed nested outcome rollback | test at 984 onward |

The two concurrency tests prove separate PostgreSQL backend PIDs, retain the
first transaction under the Trip root lock, and query
`pg_blocking_pids(second_pid)` before release (`test...py:217-229, 557-742`).
They use Events and SQL observation, not arbitrary sleeps.

## 10. Exact Validation Commands and Results

All test commands used a verified local PostgreSQL-only environment:

```text
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_b2d3_test
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_runtime_codex_b2d3
DISABLE_SCHEDULER=1 MOCK_AI=1 DEV_ALLOW_MEMBERSHIP_HEADER=0
DEEPSEEK_API_KEY='' GEOAPIFY_API_KEY=''
```

| Command | Result |
| --- | --- |
| `C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m py_compile app/domain/transactions/generator_durable.py tests/test_generator_durable_coordinator.py` | PASS |
| `...\.venv-regression\Scripts\python.exe -m pytest -q tests/test_generator_durable_coordinator.py` | **24 passed in 1.13s** |
| Legacy CI-equivalent `pytest -q` with CI's seven Pydantic-only paths ignored | PASS; full collection completed without failures |
| `...\.venv-pydantic-poc\Scripts\python.exe -m pytest -q` across CI's nine isolated Pydantic contract files | **119 passed in 7.73s** |
| `git diff --check` | PASS |
| Targeted trailing-whitespace scan of B.2D3 code/test/report | PASS (`NONE`) |

## 11. Known Limits and B.2D4 Boundary

This phase deliberately does not call B.2D1 preparation or B.2D2 external work;
persist a real `GeneratedDraft`, `PlanItem`, `PlanChange`, or `UpdateNotice`;
expose an API route, public feature flag, scheduler, or provider; or alter
existing HMAC formats, schema, or other writers.

The internal synthetic callback is protected by the strict codec, savepoint,
ledger, and rollback rules above. B.2D4 may replace it only with a separately
authorized/reviewed persistence adapter that retains these final precondition,
locking, replay, outcome, and atomicity contracts.

## 12. Final Verdict

**B.2D3: PASS.** Frozen generator identity, early historical replay, final
fresh-owned transaction, Plan-scope ledger completion, Plan-only revision,
strict safe outcome representation, rollback, and real PostgreSQL root-lock
contention are implemented and verified inside the approved boundary.

**B.2D4: NOT STARTED — separate authorization and review required.**
