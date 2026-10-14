# Cadensy R5 — B.2E Unified Writer Coverage & B.3 Foundation

## 1. Executive summary and verdict

**Verdict: CONDITIONAL PASS.** This implementation adds default-off protected
boundaries for the remaining membership, Scheduler, and organizer writer
families, plus a fail-closed B.3 admission policy. It does not implement B.3
Preview/Apply and does not activate production behavior.

The original primary protocol document was recovered at
`C:\Users\zdxzh\Desktop\capstone\New\AI_enhanced\phase-0-audit-2026-10-03\43_PR05B_FINAL_PROTOCOL_DECISIONS.md`.
Its revision-bracketed read choice, Trip-first authorization/lock order, and
full-writer-coverage prerequisite are retained. No frozen HMAC namespace, UUID
identity protocol, migration, or Generator contract was changed.

The remaining conditional items are the full independent-connection contention
matrix, route-level compatibility matrix, and a complete B.3 protocol decision
record. They must be completed before B.3 can be called ready or exposed.

## 2. Writer 60 baseline and updated coverage

Report 60 found seven uncovered B.3-relevant runtime writers: invite join,
member removal, Scheduler settlement/expiry, and the three organizer actions.
This change provides protected implementations for all seven, while preserving
Legacy selection whenever their gates are off.

| Family | Protected implementation | Gate | Default runtime |
| --- | --- | --- | --- |
| Invite join | `transactions/membership_join.py` | `CADENSY_B2E_PROTECTED_MEMBERSHIP_WRITES` | Legacy |
| Member removal | `transactions/membership_remove.py` | same | Legacy |
| Scheduler settlement/expiry | `transactions/scheduler.py` | `CADENSY_B2E_PROTECTED_SCHEDULER_WRITES` | Legacy |
| Round extension | `transactions/organizer_actions.py` | `CADENSY_B2E_PROTECTED_ORGANIZER_WRITES` | Legacy |
| Proposal escalation | same | same | Legacy |
| Deadlock resolution | same | same | Legacy |

## 3. Exact changed file inventory

- `backend/app/api/main.py`
- `backend/app/jobs/scheduler.py`
- `backend/app/domain/transactions/protected_profile.py`
- `backend/app/domain/transactions/membership_join.py`
- `backend/app/domain/transactions/membership_remove.py`
- `backend/app/domain/transactions/scheduler.py`
- `backend/app/domain/transactions/organizer_actions.py`
- `backend/tests/test_b3_protected_profile.py`
- `backend/tests/test_b2e_unified_writers.py`

## 4. Membership join/remove contracts

Protected join discovers an invite only to find a candidate Trip, then opens a
fresh transaction, locks `Trip`, re-reads and locks the invite, executes the
unchanged `trips.join_invite()` behavior, locks zero/one Plan rows, and advances
`Trip.planning_facts_revision` and the sole `Plan.revision` together only for a
real join. Duplicate Plans roll back and fail closed. Existing-account invite
rejoins remain Legacy-compatible no-ops.

Guest join deliberately has no durable browser replay row. Before first success
there is no authenticated actor for the frozen `(trip, actor, key)` ledger, and
persisting a plaintext newly-issued Guest token to replay a lost response is
forbidden. A retry remains a new anonymous join, matching current semantics;
the new revision invalidates any captured Generator state.

Protected removal uses `TripDurableCommandKernel`: fresh Account/Guest
reauthorization, organizer authorization, all Trip memberships, target
membership, and GuestSessions are locked before the existing removal service
runs. It atomically records the safe `{ "removed": true }` outcome, revokes
Guest sessions, and advances the Trip plus sole Plan revision once. A repeat
already-removed command is a durable no-op outcome.

## 5. Scheduler job authority and transaction design

The Scheduler is server-owned and is not given a fabricated Account,
membership, or browser ledger. With its gate enabled in an approved
non-production environment, it discovers due IDs without locks then processes
each ID in a fresh bounded transaction:

```text
Trip FOR UPDATE -> zero/one Plan FOR UPDATE -> memberships -> child rows
```

The authoritative child state is the deterministic work identity: `open` due
rounds settle once; pending due proposals expire once. A retry observes the
terminal state and does nothing. A real settlement or expiry advances the Plan
revision once because the Round/Proposal lifecycle changes a Preview/Apply
guard; no ledger is introduced for a server-owned job.

## 6. Organizer protected actions

The Plan durable kernel provides fresh authentication, Trip-root serialization,
current Plan revision precondition, replay, and atomic outcome. Round extension
and deadlock resolution require the existing organizer role. Proposal escalation
intentionally does **not** add an affected-member restriction: any valid member
of the same Trip may escalate a `waiting_affected_members` proposal, as directed
by the accepted Legacy compatibility decision. All child selections are scoped
through the locked Plan.

`keep`, `split`, and `clear` still call the existing organizer state machine;
`clear` keeps its `deadlock_remove` audit origin. A successful lifecycle/content
change increments Plan revision exactly once. Durable replays return stored safe
DTOs without repeating notices, PlanChanges, or transitions.

## 7. Trip/Plan revision matrix

| Operation | Trip facts revision | Plan revision | Durable outcome |
| --- | --- | --- | --- |
| New account/guest join | +1 | +1 with exactly one Plan | no Guest durable replay; see §4 |
| Existing account join | 0 | 0 | none |
| Member removal | +1 | +1 with exactly one Plan | Trip ledger |
| Scheduler settle/expire | 0 | +1 | terminal state is job identity |
| Extend/escalate/deadlock | 0 | +1 on real change | Plan ledger |

Zero Plans remain valid only for membership facts. Duplicate Plans fail closed.

## 8. Durable, retry, and no-op semantics

No existing HMAC envelope was modified. Member removal uses
`membership.remove.v1`; organizer actions use the frozen
`round.extend.v1`, `proposal.escalate.v1`, and
`proposal.deadlock.resolve.v1` operation names. Safe outcome allowlists are
minimal and contain no credentials, private constraints, or membership identity.

The no-op savepoint guard remains responsible for Plan/Trip durable callbacks.
Scheduler retries are not presented as distributed exactly-once; the committed
terminal state is the only claim/retry mechanism.

## 9. Legacy compatibility and security

All B.2E gates are false by default and reject requested protected activation in
production. Disabled paths retain their original HTTP response shapes and do not
parse protected headers. Account-first bearer authentication and Guest-session
separation remain in the existing authentication service. Cross-Trip child
selection is Plan-scoped for organizer and Scheduler writers.

## 10. Protected-only B.3 routing profile

`protected_profile.py` exposes `b3_profile_admission()` only; it does not enable
routes. Admission requires every B.2C writer, Generator, Membership, Scheduler,
and Organizer gate to be explicitly true in a recognized non-production
environment. Unknown environments, incomplete profiles, and production fail
closed. A future B.3 entry must call this policy before any Preview/Apply work
and must not fall back to Legacy after protected selection.

## 11. Recovered B.3 protocol decisions

Report 43 section 5 chooses revision-bracketed fresh reads: capture `r0`, use
short detached read Sessions, verify before/after reads, discard preview on
mismatch, then reauthorize/lock/reclassify on Apply. All relevant writer families
must advance the same monotonic Plan revision in their committing transaction.
This report implements writer boundaries only; it does not add the runtime
capability or Apply endpoint.

## 12. PostgreSQL and rollback evidence

Before destructive tests, URLs were checked as:

```text
TEST=localhost/tripsync_b2e_unified_test
RUNTIME=localhost/tripsync_runtime_codex_b2e
safe-separate=true
```

Executed with `DISABLE_SCHEDULER=1`, `MOCK_AI=1`, no provider credentials, and
only the disposable localhost test database:

| Command | Result |
| --- | --- |
| `pytest -q tests/test_b3_protected_profile.py tests/test_b2e_unified_writers.py` | `7 passed in 0.86s` |
| B.2E tests followed by durable-kernel regression (fixture-isolation verification) | `44 passed in 1.84s` |
| B.1/B.2A/B.2B/B.2C/B.2D protected regression selection plus Generator HTTP suite | `280 passed in 9.41s` |
| isolated Pydantic compatibility: `test_pydantic_ai_poc.py test_pydantic_composition.py test_pydantic_runtime.py` | `36 passed in 3.77s` |
| `python -m py_compile` over changed application/test files | passed |
| `git diff --check` | passed |

The PostgreSQL tests prove dual revisions and Guest revocation for removal, dual
revisions for Guest join, Scheduler terminal retry behavior, and broader same-Trip
proposal escalation. They do **not** yet prove `pg_blocking_pids()` contention,
fault-injection rollback for every B.2E writer, or all Account/Guest HTTP routes.

The CI-equivalent Legacy command was also attempted against
`cadensy_ci_legacy_test`. Its first attempt exposed leaked rows from the new B.2E
test fixture (an assertion expected one Ledger row and observed three); the fixture
now performs explicit per-test cleanup. A second full attempt reached 93% without
another reported assertion before this execution environment's 30-second command
window ended, so it produced no final exit/result and is **not claimed as passed**.

## 13. Remaining blockers and B.3 entry readiness

1. Add the required independent-connection `pg_blocking_pids()` tests for join/
   removal, Scheduler, and organizer actions.
2. Add full route feature-on/feature-off, production rejection, safe error mapping,
   and injected rollback coverage.
3. Freeze the exact B.3 decision-lifecycle token table and implement the separate
   revision-bracketed Preview/Apply seam without changing PR-04 contracts.
4. Re-run the requested complete Legacy CI-equivalent suite to a final JUnit result
   (the isolated Pydantic suite already passed) after the missing contention tests
   are in place.

**B.2E Unified verdict: CONDITIONAL PASS.** The protected writer foundations are
present and default-off. **B.3 entry readiness: CONDITIONAL; B.3 implementation
must remain blocked until the listed concurrency/routing proof is complete.**

---

## B.2E Unified — Final Acceptance Closure

### A. Cross-writer lock-order audit

The apparent Scheduler inversion is source-confirmed but is not a reachable
deadlock cycle among the protected writers. `scheduler._locked_plan()` locks
`Trip` then Plan (`backend/app/domain/transactions/scheduler.py:24-31`); its
two operations then lock all memberships followed by scoped children
(`:40-47`, `:61-65`). Interactive Plan and Trip kernels lock Trip first before
credential, membership, Plan, ledger, and children
(`kernel.py:249-310`, `trip_kernel.py:167-214`). Membership join locks Trip,
Invite authority, relevant membership, then Plan (`membership_join.py:42-56`).
Removal uses the Trip durable kernel then target/GuestSession locks
(`membership_remove.py:48-64`). Organizer actions use the Plan kernel and only
then scoped Round/Proposal/PlanItem/ProposalDecision locks
(`organizer_actions.py:63-106`). Generator continues to use its accepted
Trip-root Validate/Commit path (`generator_commit.py:47-106`).

The Scheduler does take Plan before memberships, unlike an interactive writer.
That difference is safe for participating protected operations: both must first
obtain the same `Trip FOR UPDATE`; a blocked second transaction cannot acquire
any secondary row lock before its root lock succeeds. There is therefore no
pair that holds a membership lock while waiting for a Plan lock while a Scheduler
holds the Plan and waits for that membership: the interactive writer would first
be blocked at Trip. This conclusion applies only to the protected profile; a
Legacy bypass remains exactly why B.3 admission is fail-closed.

| Family | Explicit locks after Trip | Root bypass in protected path |
| --- | --- | --- |
| Join | Invite, existing membership, zero/one Plan | No |
| Remove | credential, all memberships, Plan, ledger, target, GuestSessions | No |
| Scheduler | Plan, all memberships, Round/Proposal, item, Vote | No |
| Organizer | credential, memberships, Plan, ledger, scoped child rows | No |
| Generator | accepted Trip/Plan commit locks | No |
| Existing Plan/Trip durable writers | credential, membership, Plan, ledger, child rows | No |

### B. B.3 decision-lifecycle invalidation protocol

Report 43’s revision-bracketed Preview contract is retained: capture detached
`Plan.revision` `r0`; all safe tool/classifier reads use short fresh Sessions;
verify `r1 == r0`; Apply freshly reauthorizes, locks Trip/Plan, compares the
expected revision, and deterministically reclassifies from locked facts. No ORM
object or provider result becomes authoritative. The only additional Trip token
is the accepted `Trip.planning_facts_revision` for Trip-bound facts writers.

| Mutation | Authoritative change | Preview dependency | Invalidation | Apply recheck |
| --- | --- | --- | --- | --- |
| Membership join/remove; preferences/constraints | electorate/requirements | classifier facts, affected people | Trip facts + sole Plan revision | fresh auth, membership and constraints |
| Generator | Plan projection/items | every item/fact | Plan revision | captured facts and Plan projection |
| Booking/manual item | item/booked/settled facts | hard/booked rules | Plan revision | locked item and classifier |
| Notice objection, Vote | decision lifecycle/electorate | pending-decision guard | Plan revision | locked notice/round/votes |
| Round extension | deadline/open lifecycle | pending-decision guard | Plan revision on real extension | locked Round state |
| Round settlement | item/round/audit/notice | item and settledness | Plan revision | locked Round/item/votes |
| Proposal decision/escalation/expiry | proposal terminal or pending state | pending-decision guard | Plan revision on real transition | locked Proposal/decisions/item |
| Deadlock keep/split/clear | proposal plus optional item | pending guard/item content | Plan revision | locked Proposal/item |
| Reminder/comment/cache/cover | auxiliary only | none in frozen classifier | neither | excluded from Preview authority |

No new decision token is introduced. True no-ops and historical durable replays
advance neither revision. A fresh Apply whose expected revision is stale fails;
a completed historical Apply is reauthorized and replayed only after HMAC,
outcome-policy, and scope verification. The protected-only admission profile
requires every relevant writer gate and rejects unknown environment, incomplete
profile, and production (`protected_profile.py:47-64`).

### C. Final CI evidence

The Legacy CI selection from `build-validation.yml` was split into mutually
exclusive, collectively exhaustive sorted-file partitions because the execution
environment terminates a single command at about thirty seconds. The excluded
set was exactly the workflow’s nine Pydantic/evaluator/provider-only modules.

| JUnit artifact | Files | Tests | Failures | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: | ---: |
| `legacy-b2e-part1.xml` | first 26 | 377 | 0 | 0 | 0 |
| `legacy-b2e-part2a.xml` | next 18 | 258 | 0 | 0 | 0 |
| `legacy-b2e-part2b.xml` | final 18 | 294 | 0 | 0 | 0 |
| **Total** | **62** | **929** | **0** | **0** | **0** |

All partitions used `localhost/cadensy_ci_legacy_test`, a separate local runtime
URL, `DISABLE_SCHEDULER=1`, `MOCK_AI=1`, empty provider keys, and no membership
header override. The fixture cleanup was verified by running B.2E immediately
before the durable-kernel suite: `44 passed in 1.84s`.

The complete isolated Pydantic CI selection was re-run against
`localhost/cadensy_ci_pydantic_test`: all nine workflow modules passed,
**119 passed in 6.68s**.

### D. Closure verdict

The CI, fixture-isolation, lock-order, and B.3 invalidation-documentation gaps
are closed. The remaining material gap is the requested per-operation,
independent-connection `pg_blocking_pids()` matrix and fault/HTTP matrix for all
eight B.2E cases. Those tests have not yet been added in this closure, so the
accurate final verdict remains **CONDITIONAL PASS** and **B.3 implementation is
not yet authorized**. No production gate, migration, Preview/Apply code, or
external provider call was introduced.

---

## B.2E Unified — Final Contention, Rollback and HTTP Acceptance

### 1. Scope and changed files

This acceptance pass changed tests and this report only.  No protected writer,
HMAC/UUID envelope, Alembic migration, default gate, or runtime contract was
changed.

| File | Change |
| --- | --- |
| `backend/tests/test_b2e_unified_writers.py` | Real PostgreSQL root-contention matrix (E01–E08), final-transaction rollback tests, HTTP route tests, Scheduler lifecycle test. |
| `backend/tests/test_b3_protected_profile.py` | Complete required-gate-family and fail-closed admission cases. |
| `61_PR05B2E_UNIFIED_WRITER_COVERAGE_IMPLEMENTATION.md` | This evidence append. |

All PostgreSQL tests used only
`postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_b2e_unified_test`.
The runtime URL was separately set to
`localhost/tripsync_runtime_codex_b2e`; no cloud, RDS, provider, or production
database was contacted.

### 2. Actual root-contention evidence

`test_every_b2e_writer_waits_on_the_real_postgresql_trip_root` is parametrized
for every case below.  It starts a first independent Session that owns
`Trip ... FOR UPDATE`, invokes the real named protected operation from a second
independent Session, captures the second connection's `pg_backend_pid()` in the
engine SQL event immediately before its own Trip lock, and observes the first
PID in `pg_blocking_pids(second_pid)` from a third Session.  The holder is then
released through deterministic control flow; the contender completes and the
persisted revision is asserted.  The test does not use elapsed time or a stub
writer as lock evidence.

| Operation | Test / real entrypoint | PID observation | Completion and persisted result |
| --- | --- | --- | --- |
| E01 Account invite join | `account_join` / `join_invite_under_trip_root` | distinct holder/contender PIDs; contender blocked by holder | new Account membership; Trip facts and sole Plan revision `0 → 1` |
| E02 Guest invite join | `guest_join` / `join_invite_under_trip_root` | same three-connection observation | Guest issuance completes after root release; dual revision `0 → 1` |
| E03 Membership removal | `remove` / `execute_protected_membership_remove` | same | removal/revocation and Trip/Plan revisions commit once |
| E04 Due settlement | `settle` / `settle_due_round` | same | due Round closes; Plan revision `0 → 1` |
| E05 Proposal expiry | `expire` / `expire_due_proposal` | same | due Proposal expires; Plan revision `0 → 1` |
| E06 Round extension | `extend` / `execute_protected_organizer_action(round.extend.v1)` | same | extension commits through Plan durable kernel; Plan revision `0 → 1` |
| E07 Proposal escalation | `escalate` / `execute_protected_organizer_action(proposal.escalate.v1)` | same | non-affected same-Trip member may escalate under preserved policy; revision `0 → 1` |
| E08 Deadlock keep | `deadlock` / `execute_protected_organizer_action(proposal.deadlock.resolve.v1)` | same | existing `keep` outcome commits once; Plan revision `0 → 1` |

The existing B.2C Round Settlement/Vote independent-writer test remains the
cross-writer stale-revision proof for a decision writer.  This pass confirms
that every newly covered B.2E writer reaches the same authoritative root before
any of its own secondary locks.  The source-backed scheduler lock-order proof
in the preceding section remains valid: Scheduler's Plan-before-memberships
order is unreachable before it obtains the common Trip root.

### 3. Deterministic fault/rollback evidence

| Family | Test and injected boundary | Observed rollback state |
| --- | --- | --- |
| Account + Guest join | `test_join_final_commit_failure_leaves_no_member_guest_or_revision_partial` injects SQLAlchemy `before_commit` after membership/credential staging | no new membership, no GuestSession, no facts/Plan revision, no Ledger |
| Membership removal | `test_removal_final_commit_failure_rolls_back_revocation_revision_and_ledger` raises at Trip durable `after_ledger_insert` checkpoint | target stays `joined`, GuestSession remains unrevoked, both revisions remain `0`, no Ledger |
| Scheduler settlement + expiry | `test_scheduler_final_commit_failure_restores_due_terminal_state` injects `before_commit` | Round remains `open` or Proposal remains `waiting_affected_members`; revision/notice remain absent, so retry remains eligible |
| Organizer extend/escalate/deadlock | `test_organizer_ledger_failure_rolls_back_every_action_family` raises at durable `after_ledger_insert` for extend, escalation, and deadlock `keep`/`split`/`clear` | child status/extension stays unchanged; no revision, notice, or Ledger survives |

The tests deliberately use no production fault switch.  They also preserve the
accepted distinction: anonymous Guest join has no browser durable replay, while
removal and organizer results are ledger-backed and Scheduler uses committed
terminal state as its idempotency identity.

### 4. Actual HTTP and Scheduler admission evidence

| Surface | Test | Result |
| --- | --- | --- |
| Invite join gate off/on/production | `test_membership_routes_preserve_legacy_off_and_use_protected_transaction_when_enabled` | off retains Legacy (no revision); eligible test environment invokes protected dual-revision join; production request returns `503` without fallback |
| Membership removal | `test_membership_remove_http_requires_protected_headers_and_replays_without_second_revocation` | missing protected headers `428`; first/replay both `200`; exactly one revocation, dual revision and Ledger |
| Extend gate off/on/production | `test_organizer_http_gate_is_default_off_protected_on_and_production_closed` | malformed protected revision ignored while off; protected replay is byte-identical; production returns `503` |
| Escalate + deadlock | `test_protected_escalation_and_deadlock_routes_keep_same_trip_policy_and_replay` | missing headers `428`; non-affected same-Trip Account escalates; deadlock keep replays; exactly two protected Ledger effects/revisions |
| Scheduler job | `test_scheduler_job_gate_selects_protected_due_claim_and_retries_terminal_state` | eligible gate selects actual protected discovery/claim; first run settles due Round; repeat produces empty result; production and unknown environment do not select protected scheduler |

The route tests patch `main.SessionLocal` only to the disposable test factory;
each protected coordinator still creates and owns its own fresh transaction
Session.  They assert no request-Session ORM object crosses that boundary.

### 5. B.3 admission regression

`test_b3_profile_rejects_each_required_writer_family_when_missing` covers a
missing B.2C writer, Generator, Membership, Scheduler, and Organizer switch.
`test_b3_profile_is_fail_closed_for_missing_or_unapproved_environment` covers
missing, blank, unknown, `prod`, and `production` environment values.
`test_b3_profile_rejects_false_and_non_boolean_gate_values_without_enabling_them`
proves that strings such as `"1"` are not silently admitted by this pure
profile helper.  The helper remains an admission check only: no B.3
Preview/Apply route exists or became active.

### 6. Newly executed command and JUnit evidence

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_b2e_unified_test'
$env:DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_runtime_codex_b2e'
$env:DISABLE_SCHEDULER='1'; $env:MOCK_AI='1'; $env:DEV_ALLOW_MEMBERSHIP_HEADER='0'
python -m pytest -q tests/test_b2e_unified_writers.py tests/test_b3_protected_profile.py --junitxml=../b2e-final-acceptance.xml
```

The initial focused invocation was superseded by the final focused rerun:
**44 passed in 2.11s**, exit code 0, with 0 failures, 0 errors, and 0 skips in
`b2e-final-acceptance.xml`.  The final complete Legacy and isolated Pydantic
reruns are recorded in §8.  A PASS remains unavailable only for the disclosed
cross-writer coverage gap, not because CI was left incomplete.

### 7. Per-operation acceptance ledger and honest conclusion

| Operation | Real root contention | Fault rollback | Revision | Retry/replay | HTTP/job admission | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| E01 Account join | PASS | PASS | PASS | account rejoin is covered by prior compatibility tests | PASS | CONDITIONAL PASS |
| E02 Guest join | PASS | PASS | PASS | intentionally no anonymous browser replay | PASS | CONDITIONAL PASS |
| E03 Member removal | PASS | PASS | PASS | PASS | PASS | CONDITIONAL PASS |
| E04 Scheduler settlement | PASS | PASS | PASS | terminal retry PASS | job PASS | CONDITIONAL PASS |
| E05 Scheduler expiry | PASS | PASS | PASS | terminal retry covered by existing B.2E test | job discovery path covered | CONDITIONAL PASS |
| E06 Round extension | PASS | PASS | PASS | protected replay PASS | PASS | CONDITIONAL PASS |
| E07 Proposal escalation | PASS | PASS | PASS | protected replay PASS | PASS | CONDITIONAL PASS |
| E08 Deadlock resolution (`keep`/`split`/`clear`) | PASS | PASS | PASS | PASS for all three branches | PASS | CONDITIONAL PASS |

The remaining reason every row stays conditional is deliberately visible: this
pass has not independently added all requested Generator-versus-join/remove and
decision-writer-versus-scheduler/organizer interleavings.  Therefore the accurate final B.2E verdict is
**CONDITIONAL PASS**, not PASS.  **B.3 implementation is not authorized yet**:
its protected-only admission remains correct, but the final required
cross-writer acceptance cells must be completed first.

### 8. Post-edit complete CI rerun

The complete selections were rerun after the final acceptance-test edits.  The
Legacy workflow selection was again split into exact mutually exclusive sorted
file partitions to remain below the command-window limit.  It excluded only
the workflow's seven Pydantic/provider/evaluation-specific modules and covered
all remaining **62** `test_*.py` files exactly once.

| Artifact | Files | Tests | Failures | Errors | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `legacy-b2e-final-part1.xml` | 0–25 | 414 | 0 | 0 | 0 | 0 |
| `legacy-b2e-final-part2.xml` | 26–51 | 378 | 0 | 0 | 0 | 0 |
| `legacy-b2e-final-part3.xml` | 52–61 | 174 | 0 | 0 | 0 | 0 |
| **Legacy total** | **62** | **966** | **0** | **0** | **0** | **0** |
| `pydantic-b2e-final.xml` | nine isolated workflow modules | 119 | 0 | 0 | 0 | 0 |
| `b2e-final-acceptance.xml` | B.2E contention/fault/HTTP + B.3 profile | 44 | 0 | 0 | 0 | 0 |

Commands used the same explicit local safety environment shown in §6.  Python
compilation for every touched B.2E module and test passed, and `git diff
--check` passed.  The 10 warnings in the final Legacy third partition are the
pre-existing Alembic `path_separator` deprecation warning from
`test_trip_bound_phase1_migration.py`; they are neither failures nor errors.

---

## B.2E Unified — Cross-Writer Interleaving Acceptance and Final Verdict

### Completed new evidence

This pass changed only PostgreSQL tests and this report.  It did not change a
production writer, gate, HMAC/UUID identity, schema, or migration.

| Interaction | Real operations | Deterministic observation | Result |
| --- | --- | --- | --- |
| Generator ↔ Account invite join | `execute_protected_plan_generation` paused in detached fake Provider work; `join_invite_under_trip_root` commits independently | no DB lock remains during Provider pause; final Generator validation sees changed detached snapshot | Account join commits; Trip facts and existing empty Plan revisions each become `1`; Generator raises `StaleGeneratorPreconditions`; no generated Item/Change/Generator Ledger |
| Generator ↔ Guest invite join | same Generator seam; real Guest `join_invite_under_trip_root` | same | Guest membership and GuestSession commit atomically; no Generator effect/ledger; no Guest token is placed in a Ledger |
| Generator ↔ membership removal | paused detached Generator; `execute_protected_membership_remove` | same | target becomes removed, GuestSession revokes, Trip/Plan revisions each become `1`; stale Generator writes nothing; removal Ledger is the sole durable outcome |
| Generator then membership join | real Generator commit first, then real Guest join | sequential reverse-order contract | Generator makes Plan revision `1`; later membership write advances Trip facts `0 → 1` and Plan revision `1 → 2` |
| Scheduler settlement ↔ Booking | `settle_due_round` is paused after its real state-machine settlement while holding its owned transaction; real `execute_protected_booking` starts with revision `0` | distinct PIDs and third-session `pg_blocking_pids()` prove Booking waits behind Scheduler Trip root | Scheduler closes Round and commits Plan revision `1`; Booking fails `StalePlanRevision`, creates no Ledger, and cannot overwrite Scheduler's `settled` Item state |

New tests are in `backend/tests/test_generator_commit.py` and
`backend/tests/test_b2e_unified_writers.py`.  Focused execution against
`localhost/tripsync_b2e_cross_writer_test` passed:

```text
4 passed, 32 deselected  # Generator/Membership interactions
1 passed, 30 deselected  # Scheduler settlement versus Booking
py_compile + git diff --check: passed
```

### Honest final status

The above closes the Generator/Membership proof and one Scheduler/Plan-writer
proof, but it does **not** yet supply all authorized scenarios in the current
cross-writer request: Scheduler Expiry ↔ Proposal Decision, Organizer Extension
↔ Scheduler Settlement in both orders, Organizer Escalation ↔ Scheduler Expiry,
and Deadlock Resolution ↔ a protected Plan-content writer.  Those tests were
not fabricated or silently treated as covered by the shared root-lock tests.

**B.2E UNIFIED — CONDITIONAL PASS.**

**B.3 IMPLEMENTATION READINESS — NOT READY.**  The protected-only admission
profile remains fail-closed, but the listed cross-writer acceptance scenarios
and their post-edit complete CI rerun are still required before default-off B.3
implementation can be authorized.

---

## B.2E Unified — Final Four Cross-Writer Cases and Acceptance Decision

### Four remaining writer-pair state-machine results

| Group | Actual test and order | Persisted outcome | Result |
| --- | --- | --- | --- |
| Scheduler Expiry ↔ Proposal Decision | `test_expiry_and_proposal_decision_terminal_orders_do_not_mix_transitions`: expiry then decision; decision then expiry | expiry first leaves Proposal `expired`, decision remains pending and returns safe no-op at revision 1; decision first advances Plan once and later expiry returns false | PASS for terminal-state/revision compatibility |
| Extension ↔ Settlement | `test_extension_and_settlement_orders_recheck_round_eligibility`: extension then settlement; settlement then extension | extension advances Plan once and makes the deadline future, so Scheduler returns false; settled Round stays closed and later extension is rejected | PASS for eligibility compatibility |
| Escalation ↔ Expiry | `test_escalation_and_expiry_orders_preserve_terminal_proposal_state`: escalation then expiry; expiry then escalation | escalation durable replay is preserved; later expiry transitions escalated proposal to expired and advances Plan twice total; expiry-first rejects later escalation | PASS for terminal-state/replay compatibility |
| Deadlock ↔ Booking | `test_deadlock_and_booking_orders_reject_stale_plan_content_writer`: deadlock then booking; booking then deadlock | first committed content/lifecycle command advances Plan once; second command with revision 0 raises `StalePlanRevision`, so `removed` and `booked` states are never overwritten | PASS for stale-revision compatibility |

The earlier `test_due_settlement_and_real_booking_serialize_then_stale_booking_is_rejected`
remains the real overlapping two-writer proof: it records independent scheduler
and booking PIDs, observes the Scheduler PID in `pg_blocking_pids(booking_pid)`,
then proves the Booking command becomes stale and writes neither state nor a
Ledger outcome.

Focused execution after these additions:

```text
pytest -q tests/test_b2e_unified_writers.py tests/test_b3_protected_profile.py tests/test_generator_commit.py
85 passed in 4.06s; exit 0
py_compile: passed
git diff --check: passed
```

### Final decision

The four requested pairs are now covered with actual protected writer entrypoints
and both meaningful state-machine orders.  However, the new four pair tests are
deterministic sequential order proofs; they do not each independently hold one
writer's transaction open and record a second real writer's blocked PostgreSQL
PID.  The task's evidence rules require that stronger observation wherever a
real lock wait is expected.  Also, the complete Legacy and isolated-Pydantic
CI selections have not been rerun after these latest test edits.

Accordingly, the honest current conclusion remains:

**B.2E UNIFIED — CONDITIONAL PASS.**

**B.3 IMPLEMENTATION READINESS — NOT READY.**

The exact remaining blocker is to convert the four order proofs into
independent-session overlapping transaction tests with `pg_blocking_pids()`
evidence where the second writer waits, then perform one final full CI rerun.

---

## B.2E Unified — Real Overlap and Final CI Acceptance

The preceding blocker is now closed without changing production code.  The
test-only `_real_writer_overlap` helper pauses real writer A in its own
`before_commit` callback after its domain mutation and while its owned Trip
root transaction is still open.  Real writer B runs in a distinct thread and
Session; a third Session verifies the PostgreSQL waiter relation.  The callback
is restricted by A's thread name and removed in `finally`.

| Writer pair | A PID | B PID | `pg_blocking_pids()` observation | Winner/loser behavior | Persisted state | Result |
| --- | --- | --- | --- | --- | --- | --- |
| Expiry / Proposal Decision | distinct runtime PIDs | distinct runtime PIDs | Expiry PID observed blocking Decision PID | expiry commits; Decision with revision 0 raises `StalePlanRevision` | Proposal `expired`, Plan revision 1, no Decision Ledger | PASS |
| Extension / Settlement | distinct runtime PIDs | distinct runtime PIDs | Extension PID observed blocking Scheduler PID | extension commits; Scheduler rereads future deadline and returns false | Round remains `open`, Plan revision 1, one extension Ledger | PASS |
| Escalation / Expiry | distinct runtime PIDs | distinct runtime PIDs | Escalation PID observed blocking Scheduler PID | escalation commits, then server expiry legally expires escalated due Proposal | Proposal `expired`, Plan revision 2, one escalation Ledger | PASS |
| Deadlock / Booking | distinct runtime PIDs | distinct runtime PIDs | Deadlock PID observed blocking Booking PID | deadlock clear commits; Booking revision 0 raises `StalePlanRevision` | Item `removed`, Plan revision 1, one deadlock Ledger | PASS |

The pre-existing reverse-order tests remain retained, now with narrow
`legacy_organizer.NothingToDo` assertions rather than broad `Exception`:
settled Rounds cannot be extended/reopened and expired Proposals cannot be
escalated.  Together with the already accepted Generator/Membership and
Scheduler/Booking overlap proofs, all required writer pairs have real
authoritative-writer coverage.  No test-owned SQL lock holder is used as a
substitute for either writer.

### Final commands and JUnit results

All commands used only explicit disposable localhost test databases, a
different local runtime URL, disabled Scheduler, fake AI, empty Provider keys,
and no membership-header override.

| Artifact | Selection | Tests | Failures | Errors | Skips | Exit |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `b2e-final-gate-focused.xml` | B.2E, B.3 profile, Generator, Durable | 147 | 0 | 0 | 0 | 0 |
| `legacy-b2e-finalgate-part1.xml` | Legacy sorted files 0–25 | 427 | 0 | 0 | 0 | 0 |
| `legacy-b2e-finalgate-part2.xml` | Legacy sorted files 26–51 | 378 | 0 | 0 | 0 | 0 |
| `legacy-b2e-finalgate-part3.xml` | Legacy sorted files 52–61 | 174 | 0 | 0 | 0 | 0 |
| **Legacy total** | **62 mutually exclusive files** | **979** | **0** | **0** | **0** | **0** |
| `pydantic-b2e-finalgate.xml` | isolated workflow selection | 119 | 0 | 0 | 0 | 0 |

`py_compile` and `git diff --check` passed.  The Legacy third partition retains
only ten pre-existing Alembic `path_separator` deprecation warnings; it has no
failures or errors.

### Superseding acceptance decision

**B.2E UNIFIED — PASS**

**B.3 IMPLEMENTATION READINESS — READY FOR DEFAULT-OFF NON-PRODUCTION IMPLEMENTATION**

This authorizes only the separately scoped protected-only B.3 implementation.
It does not authorize production activation, a route default change, external
Provider use, deployment, or any modification of the frozen identity and HMAC
contracts.
