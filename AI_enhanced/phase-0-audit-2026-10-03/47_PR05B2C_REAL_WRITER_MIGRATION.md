# PR-05B.2C — Real Business Writer Migration

## A. Baseline and B.2B gate

- Worktree: `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`
- Branch: `codex/pr05b1-schema-foundation`; baseline `17026adf4b77ced2280629f2b1584fd6892e420e`.
- B.1, B.2A, and B.2B remain uncommitted and preserved. Alembic head is
  `6f77e775d201`; B.2C creates no migration.
- The canonical `New/main` and Codex visualization worktrees were not modified.

### B.2B source preflight

Inspected `idempotency.py`, `transactions/kernel.py`, `transactions/durable.py`,
`transactions/__init__.py`, and `test_durable_idempotency_kernel.py`. The actual
source matches report 46: only fingerprint v1 is supported; canonical fields are
deeply frozen before HMAC/callback use; authorization precedes ledger replay; a
trusted operation/version outcome registry validates stored status and JSON; and
effect, revision, and completed ledger share one owned transaction. The normal API
has no default-on protocol activation. Focused evidence: B.2B **33 passed** and
B.2A/B.1 **41 passed** on a guarded localhost disposable PostgreSQL database.

## B. Complete writer inventory

| Writer / entry point | Records changed | Classification / revision | B.2C status |
| --- | --- | --- | --- |
| `POST /plans/{plan}/items` `main.add_plan_item` | `plan_item` | Direct / required | **Migrated behind feature gate** |
| `POST /plans/items/{item}/changes`; `orchestrator.propose_change` | item, change, notice/round/proposal | Direct but multi-state / required | Legacy; remaining B.2C |
| `PATCH /plans/items/{item}/booking` | item, change, notice | Direct / required | Legacy; remaining B.2C |
| notice objection, vote, settle, proposal decision | notice/round/vote/proposal/item/change | State-machine writers / required | Legacy; remaining B.2C |
| organizer extend/escalate/deadlock | round/proposal/item/notices | State-machine writers / required | Legacy; remaining B.2C |
| preferences/constraints | preference/constraint/private row/plan refresh | Decision facts / required | Legacy; remaining B.2C |
| comments/reminders | comment/notice | Exempt from current Plan facts | Not revision-protected; duplicate UX separate |
| generator | plan/item/change | Deferred B.2D: provider/prepare-validate-commit and possible Plan creation | Deferred |
| scheduler settlement/expiry | round/proposal/item/change/notice | Deferred B.2D: work claim and terminal recheck | Deferred |
| trip create, invites, membership/credential changes | trip/membership/session/invite/plan | Different aggregate lifecycle | Deferred |
| trip cover | trip cover/cache fields | Deferred: external HTTP currently under root lock | Deferred |
| seed/reset/eval scripts | administrative tables | Operator-only, not product writer | Exempt |

The inventory was derived from actual route commits in `backend/app/api/main.py`,
ORM writes in `decisions/orchestrator.py`, `decisions/organizer.py`,
`preferences/service.py`, `trips/service.py`, `plans/generator.py`, and the
scheduler. No claim of full writer coverage is made.

## C. Selected migration scope

The first safe real vertical slice is the existing manual PlanItem insertion route.
It is local and deterministic, operates on exactly one existing Plan, has no
provider call, and its existing adjacent-gap state rules can be re-read under the
locked aggregate. Generator, scheduler, membership and decision-state-machine work
need additional protocols and were not improvised here.

## D. Implementation

| File | Change |
| --- | --- |
| `app/domain/plans/manual_items.py` | Extracted the real adjacent-gap insertion rules into a no-commit domain service; legacy route and protected writer share it. |
| `app/domain/transactions/durable.py` | Added private package-only session-mutation hook; public durable callback remains Session-free. |
| `app/domain/transactions/manual_plan_item.py` | Added the real protected manual-item command, fixed outcome policy, and child-row locking. |
| `app/api/main.py` | Refactored legacy route to shared service; added explicit default-off `CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES` adapter. |
| `tests/test_protected_manual_plan_item.py` | Added real PostgreSQL and route integration evidence. |

When the gate is off (default), headers and response shape are unchanged. When the
gate is explicitly enabled, both `Idempotency-Key` and `X-Expected-Plan-Revision`
are mandatory; missing inputs fail closed and never fall back to the legacy write.
The gate also requires explicitly configured HMAC material. This is a dormant test
integration seam, not B.4 public activation.

## E. Atomicity and replay proof

The protected route converts request authentication only into primitive preliminary
scope plus private bearer proof. A fresh owned Session then locks:

```text
Trip -> AuthSession/GuestSession -> TripMembership -> Plan -> ledger
     -> ordered active PlanItems -> real adjacent-gap insertion
     -> Plan.revision + completed outcome -> COMMIT
```

The manual-item service performs no commit. It checks that the locked Plan identity
matches the requested resource, then re-reads/locks all active child items and
applies the pre-existing adjacency/time-gap rules. The durable kernel owns exactly
one revision increment and the completed outcome. Replay happens before child lookup,
so an authorized matching retry can return a stored snapshot after child deletion.

## F. Real PostgreSQL evidence

The B.2C suite uses committed fixtures, independent owned Sessions for protected
commands, and FK-safe cleanup of only its own seed rows. It verifies:

- actual `PlanItem` insertion and all expected persisted fields;
- one revision increment and one Trip/actor/key ledger row;
- identical replay without a second business write;
- same-key changed-body conflict and stale-revision rejection;
- invalid adjacent-gap business validation rolls back child/revision/ledger together;
- feature-off route compatibility; and
- feature-on FastAPI route execution through the protected adapter, including
  replay and no-header fail-closed behavior.

This slice does not yet supply a child-writer concurrency proof for every remaining
writer family; that is why the overall verdict is conditional.

## G. Regression results

All database commands first verified localhost, an explicitly disposable `*_test`
database distinct from runtime `DATABASE_URL`, disabled scheduler/AI, and empty
provider credentials. No AWS/RDS or real provider was accessed.

| Command / boundary | Result |
| --- | --- |
| B.2C + B.2B + B.2A + B.1 focused suite | PASS — 80 passed in 2.00s |
| Legacy CI-equivalent suite, official Pydantic exclusions | PASS — 601 tests, 0 failures, 0 skipped |
| Isolated Pydantic fake compatibility | PASS — 119 passed in 6.55s |
| `py_compile` modified modules/tests; `git diff --check` | PASS |

One initial combined-run failure was corrected in the new test: its ledger assertions
counted rows from other committed test fixtures. The assertion and cleanup were made
Trip-scoped; no business/kernel failure was accepted as success.

## H. Writer coverage matrix

| Writer family | Classification | B.2C migrated | Revision protected | Tests | Remaining issue |
| --- | --- | --- | --- | --- | --- |
| Manual PlanItem insert | Direct | Yes, feature-off adapter | Yes when gate enabled | Real DB + route | Gate remains off |
| Change/booking/notice | Direct | No | No | Existing only | Migrate/review |
| Vote/round/proposal/organizer | Direct state machine | No | No | Existing only | Preserve transitions/settlement |
| Preference/constraint | Direct decision fact | No | No | Existing only | Fresh locked revalidation |
| Generator/scheduler/cover | Deferred | No | No | Existing only | B.2D lifecycle protocol |
| Membership/credential | Deferred | No | No | Existing auth tests | Aggregate lifecycle |
| Comments/reminders | Exempt from classifier facts | No | Not required | Existing only | Separate duplicate policy |

## I. Remaining risks

Unmigrated direct writers, generator Prepare/Validate/Commit, scheduler work
claiming, membership/credential lifecycle, Trip Cover provider-under-lock, missing
Plan creation/replay, duplicate-Plan preflight, preview revision bracketing,
production HMAC rotation, ledger retention/cleanup, and B.4 public rollout gates all
remain unresolved. No distributed exactly-once or application-wide concurrency claim
is made.

## J. Final verdict

**CONDITIONAL PASS.** A real, deterministic existing-Plan writer is now protected
and testable through a default-off application adapter, but many eligible direct
writers remain legacy paths.

PR-05B.2C migrates the identified direct business writers to the protected internal command protocol within the documented scope. Remaining legacy, complex, and background writers are not automatically protected. Full application-wide concurrency protection and public idempotency activation have NOT been enabled.

## K. Focused vertical-slice corrections (2026-10-08)

This correction round preserves the original evidence and does not begin B.2D,
B.3, or B.4.

- `X-Expected-Plan-Revision` is now an optional string at the HTTP boundary. It is
  parsed only after the protected feature gate is selected. Therefore malformed
  optional headers do not cause FastAPI 422 on the default legacy route.
- The legacy route again performs whitespace-only title rejection before plan
  lookup, preserving its original validation order.
- A requested protected gate under `APP_ENV=production|prod` now returns a
  controlled 503 before any legacy or protected writer can run. Local explicit test
  enablement remains available; default remains off.
- The adapter maps fresh invalid credentials to non-diagnostic 401, membership
  authorization failures to non-diagnostic 403, missing plan/trip to 404,
  duplicate aggregate and stale/key/replay-contract failures to controlled 409, and
  malformed protected inputs to 422. It exposes no ledger/HMAC/database details.
- New route regressions prove feature-off malformed-header compatibility and
  production-gate rejection. Existing real-writer tests continue to cover actual
  mutation, single revision/ledger outcome, replay, conflict/stale, rollback,
  feature-on route execution, and feature-off behavior.

Before each test, a localhost disposable `*_test` database distinct from runtime
`DATABASE_URL` was selected; scheduler/AI/providers were disabled. Results:

| Boundary | Result |
| --- | --- |
| B.2C + B.2B + B.2A + B.1 focused suite | PASS — 82 passed in 2.35s |
| Legacy CI-equivalent exclusions | PASS — completed with no failures shown |
| Isolated Pydantic compatibility | PASS — 119 passed in 6.60s |
| `py_compile` and `git diff --check` | PASS |

The migration remains a **CONDITIONAL PASS** vertical slice. Concurrent
same-gap writer races, Guest adapter coverage, and the remaining direct writer
families are explicit B.2C coverage gaps; they were not represented as complete
writer protection.

## L. Manual Item final acceptance and Booking writer migration (2026-10-08)

This follow-up continues only the approved B.2C vertical-slice scope. It does
not start B.2D/B.3/B.4, add a migration, activate either gate in production, or
migrate proposals, voting, decision rounds, preferences, generator, or scheduler.

### L.1 Manual Item acceptance evidence

The existing protected Manual Item command remains intact. New independent
PostgreSQL Sessions coordinate a same-adjacent-gap race with Events and observe
the contender's actual `Trip FOR UPDATE` statement before releasing the first
writer. One command commits the child, one Plan revision, and one ledger record;
the waiting command sees the new revision and receives the safe stale-revision
failure. No timing sleep is used.

The route is also exercised with a real `gst_` Guest bearer, not a FastAPI
dependency override: first mutation succeeds, matching retry replays, a revoked
`GuestSession` is rejected, and a subsequently removed Guest membership is
rejected. The replay test deletes the child after first success and confirms that
the authorized retry returns its retained safe DTO without recreating it or
advancing the Plan revision.

Manual protected-route errors now map lock timeout/deadlock/serialization to a
generic 503, missing aggregate to 404, duplicate aggregate, HMAC/outcome safety,
and revision exhaustion to generic 409, and malformed commands to 422. No
database, ledger, HMAC, or credential detail is returned.

### L.2 Booking writer: verified call graph and preserved behavior

The original `PATCH /api/plans/items/{item_id}/booking` route performed:

```text
route -> scoped PlanItem -> orchestrator.set_booking_status ->
  PlanItem.settledness / settled_at + PlanChange(origin=booking) + UpdateNotice -> commit
```

The feature-gated replacement is
`domain.transactions.booking.execute_protected_booking`. It locks and freshly
reauthorizes `Trip -> credential session -> TripMembership -> Plan -> ledger`,
then locks the target `PlanItem` in the same owned Session and verifies that it
belongs to that authoritative Plan. It invokes the extracted
`orchestrator.set_booking_status_for_actor()` implementation, preserving the
legacy Book/Unbook state, `PlanChange`, and `UpdateNotice` behavior. There are no
provider or network calls in the transaction.

`CADENSY_B2C_PROTECTED_BOOKING_WRITES` is default-off and rejects a requested
protected path under `APP_ENV=production|prod` before mutation. When off, the
route remains the legacy header-free implementation. When on, both
`Idempotency-Key` and `X-Expected-Plan-Revision` are mandatory and parsing happens
only after selection of the protected path. The existing B.2C HMAC injection seam
is reused; no key is persisted.

The immutable B.2B command snapshot contains exactly `{booked}` and is both the
fingerprint input and the mutation input. A state-changing Book or Unbook advances
the Plan revision once and atomically persists its safe detached item DTO.
Identical retries replay it without a new booking/audit/notice write. A real
no-op (the requested state already exists) is recorded as a safe replayable
outcome but deliberately does **not** increment the Plan revision or invent a
`PlanChange`/`UpdateNotice`. This narrow no-op semantics is now asserted at the
durable-kernel level and with the real Booking writer.

### L.3 PostgreSQL and HTTP evidence

All PostgreSQL invocations checked a credential-redacted URL parse first:
`host=localhost`, database `tripsync_test` (explicit disposable name), and a
distinct local disposable runtime name. Scheduler and real AI/provider keys were
disabled. No AWS/RDS, production database, or real provider was accessed.

| Boundary | Result |
| --- | --- |
| Manual/Booking + B.2B/B.2A/B.1 focused PostgreSQL suite | PASS — 108 passed in 2.88s |
| Booking Book → replay → Unbook → no-op | PASS — exactly 2 revisions, 2 `PlanChange`, 2 `UpdateNotice`, 3 durable outcomes |
| Booking multi-row injected rollback | PASS — item, audit, notice, revision, and ledger all rolled back |
| Booking same-key replay, changed-payload conflict, stale revision | PASS |
| Booking concurrent competing commands | PASS — independent connections; contender observed at `Trip FOR UPDATE`; one effect/ledger/revision |
| Real Guest API route for Manual and Booking | PASS — success/replay plus revoked credential and removed membership rejection |
| Booking typed HTTP mapping | PASS — lock timeout, deadlock, missing/duplicate Plan, unsupported HMAC/outcome, overflow, unsafe outcome, and generic DB failure return controlled non-diagnostic 404/409/503 responses |
| Legacy CI-equivalent (official Pydantic exclusions) | PASS — 629 tests, 0 failures, 0 errors, 0 skips (JUnit; 30.963s) |
| Isolated Pydantic fake compatibility suite | PASS — 119 passed in 6.31s |
| Changed Python `py_compile` and `git diff --check` | PASS |

### L.4 Updated writer coverage matrix

| Writer family | Classification | B.2C state | Revision / durable replay | Evidence | Remaining risk |
| --- | --- | --- | --- | --- | --- |
| Manual PlanItem insert | Direct | Migrated, default-off | Yes; safe child-deletion replay | real DB, Guest route, same-gap contention | Gate remains dormant |
| PlanItem booking / unbooking | Direct | Migrated, default-off | Yes for state changes; replayable no-op does not fabricate revision | real DB, audit/notice rollback, Account/Guest, contention | Gate remains dormant |
| Change/proposal/vote/round/organizer | Direct state machine | Legacy | No | existing regressions only | transition and settlement protocol still needed |
| Preferences / constraints | Decision facts | Legacy | No | existing regressions only | locked fresh revalidation still needed |
| Generator / scheduler / cover | Deferred | Legacy | No | existing regressions only | B.2D lifecycle/provider work |
| Membership / credential lifecycle | Separate aggregate | Legacy | No | kernel authorization races only | lifecycle activation work |
| Comments / reminders | Exempt from current classifier facts | Legacy | N/A | existing regressions | separate duplicate policy |

### L.5 Final B.2C status

**CONDITIONAL PASS.** Manual Item acceptance and the real Booking writer are
implemented behind default-off, non-production gates with PostgreSQL evidence for
atomic effects, authorization, replay, stale/conflict handling, and deterministic
root-lock contention. The project must **not** claim full B.2C or application-wide
concurrency protection: the listed state-machine, preference, generator,
scheduler, membership, and cover writers remain direct legacy paths.

## M. Booking focused fixes and final-acceptance review (2026-10-09)

This narrow follow-up makes no new writer migration, schema migration, API
activation, provider call, cloud/RDS access, commit, or deployment.

### M.1 Cross-Trip child-lock correction

The original protected Booking child lookup used only `PlanItem.id` with
`FOR UPDATE`, then compared `item.plan_id` after materializing and locking the
row. That unnecessarily acquired a foreign Trip's child lock before rejecting the
command. The lookup now scopes the lock predicate itself:

```text
PlanItem.id == command.item_id
AND PlanItem.plan_id == context.plan.id
FOR UPDATE
```

The existing root order is unchanged: `Trip -> credential -> membership -> Plan ->
ledger -> scoped PlanItem`. A new PostgreSQL test seeds a second Trip/Plan/Item,
uses that foreign item id in the first Trip's protected command, and asserts the
emitted child query contains both predicates. It expects no returned/locked foreign
row, no state change on either Plan, and no `PlanChange`, `UpdateNotice`, or ledger
record. Same-Plan Booking tests remain in the focused suite.

### M.2 No-op invariant correction

Source inspection found that `state_changed=False` previously skipped revision
increment but trusted the callback: an in-package callback could dirty ORM state,
call `flush()`, add/delete a row, then return a false no-op. Such a callback could
commit its domain effect with a non-advancing revision, which is unacceptable.

`DurableCommandKernel` now installs a short-lived `_NoOpMutationGuard` only around
the private callback. It fail-closes a false no-op if any of the following occurred
before the kernel writes its own ledger row:

- unflushed `Session.new`, `dirty`, or `deleted` state;
- an ORM flush (including an explicit callback `session.flush()`); or
- connection-level `INSERT`, `UPDATE`, `DELETE`, or `MERGE` SQL.

It is not a general database auditing system: the listeners exist only for the
single callback window and are removed before the normal durable ledger insert.
On rejection, the existing owned transaction rolls back the callback effect and no
ledger result is committed. The valid Booking no-op remains narrow and safe because
its existing orchestrator helper returns before changing or flushing anything.

New tests cover genuine no-op plus replay; dirty state; explicit flushed update;
flushed insert; and flushed delete followed by false no-op, each asserting original
item state, Plan revision, child count, and ledger are restored/absent after
rollback.

### M.3 Validation state

The local disposable-database safety check still verified a credential-redacted
`TEST_DATABASE_URL` with `host=localhost`, `database=tripsync_test`, and a distinct
local runtime test name; scheduler and real-provider variables were disabled.

However, immediately before the new PostgreSQL run the local server stopped
listening on port 5432: no Postgres process, Windows service, Docker container, or
local listener was available. A focused pytest run therefore blocked during the
repository fixture's localhost-admin connection and was terminated; it is **not** a
passing result. No remote endpoint was attempted.

| Check | Result |
| --- | --- |
| Changed-module `py_compile` | PASS |
| Focused test collection | PASS — 53 tests collected (`test_protected_booking_writer.py`, `test_durable_idempotency_kernel.py`) |
| `git diff --check` | PASS |
| New focused PostgreSQL execution | BLOCKED — local PostgreSQL unavailable before fixture setup |
| B.1/B.2A/B.2B, Legacy CI-equivalent, isolated Pydantic reruns | NOT RUN — dependent on the same unavailable local PostgreSQL service |

### M.4 Focused verdict

**BLOCKED for final acceptance evidence.** The cross-Trip predicate and false-no-op
rollback safeguards are implemented and statically checked, but the new
PostgreSQL-backed behavior, full Legacy regression, and isolated Pydantic regression
must be rerun successfully once the approved local disposable PostgreSQL service is
available. This is an execution-environment block, not authorization to activate
either gate or to claim full B.2C completion.

## N. Local PostgreSQL recovery and final focused evidence (2026-10-09)

The approved machine-local PostgreSQL 18 instance documented in `LOCAL_DEV.md`
was started from `D:\PostgreSQL\18\data` and then independently reported
`localhost:5432 - accepting connections`. No cloud or production endpoint was used.
The final shell used distinct password-bearing localhost-only URLs:

```text
TEST_DATABASE_URL = .../tripsync_pr05b2c_final_test
DATABASE_URL      = .../tripsync_pr05b2c_final_runtime_test
```

Both names are explicitly disposable test names, differ from each other, and the
test harness recreates only the former. Scheduler was disabled, mock AI enabled,
and real-provider keys were empty.

An initial full Legacy run against the equivalent host spelling `127.0.0.1` had one
evaluation-manifest assertion failure because that established contract explicitly
requires the literal host value `localhost`; it was not accepted as passing evidence.
The focused evaluation test passed after restoring the contract-preserving
`localhost` spelling, followed by the full successful run below.

| Final command / boundary | Result |
| --- | --- |
| Focused B.2C/B.2B/B.2A/B.1 PostgreSQL suite | PASS — 113 passed in 2.82s |
| Cross-Trip Booking child scope | PASS — foreign item query has both `id` and `plan_id` predicates; no foreign lock/effect/revision/audit/notice/ledger outcome |
| Durable false-no-op guard | PASS — dirty, explicit flushed update, insert, and delete each fail closed and roll back effect/revision/ledger |
| Genuine Booking no-op and replay | PASS — no revision/audit/notice fabrication; one safe completed ledger outcome replays |
| Legacy CI-equivalent, official Pydantic exclusions | PASS — 634 tests, 0 failures, 0 errors, 0 skips (JUnit; 31.485s) |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 7.11s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### N.1 Final focused verdict

**CONDITIONAL PASS for the Manual Item and Booking vertical slices.** The original
cross-Trip lock-scope and false-no-op defects are corrected and now have real local
PostgreSQL evidence. This verdict is limited to the two default-off, non-production
protected writers. It does **not** claim full B.2C, application-wide revision
coverage, or activation readiness while the remaining direct writers stay legacy.

## O. Final no-op and replay security fixes (2026-10-09)

This final narrow correction changes only durable no-op/replay behavior and its
tests. It creates no schema migration, new writer migration, public activation,
provider call, cloud/RDS access, commit, or deployment.

### O.1 False no-op cannot commit transactional callback work

The previous guard checked the submitted SQL string's first token. That was not a
reliable security boundary: comments and `WITH ... UPDATE` forms can hide the DML
verb. The durable callback now always executes inside a transaction-local
SAVEPOINT.

- A `state_changed=True` callback releases that SAVEPOINT and continues with the
  ordinary revision/ledger write.
- A `state_changed=False` callback first passes the clean-state guard, then always
  rolls back the SAVEPOINT before the durable ledger outcome is created.
- Any callback error also rolls the SAVEPOINT back before the owned outer
  transaction handles its existing rollback path.
- The short-lived guard still rejects staged ORM state and flushes. It now observes
  PostgreSQL's actual cursor `statusmessage` command tag after execution rather
  than parsing SQL input. Consequently both `/* comment */ UPDATE ...` and
  `WITH ... UPDATE ...` report `UPDATE n`, are rejected as unsafe false no-ops,
  and their SAVEPOINT effects are rolled back.

The SAVEPOINT is the fail-closed backstop for transactional writes whose command tag
is not a conventional DML tag (for example, a write-capable function invoked via a
`SELECT` command tag): it cannot merge into the durable outer transaction. This is
not a claim to undo non-transactional database behavior or external side effects
(network calls, provider calls, files, message publication, or a deliberately
misbehaving internal callback that escapes the owned transaction). Those remain
forbidden at this internal callback boundary; neither Manual Item nor Booking makes
such a call.

### O.2 Replay must satisfy historical and current trusted policy

Replay retains the historical contract lookup so a stored format must still be
understood. It now also resolves `current_for(operation_code)` and validates the
stored payload a second time against that current server-owned allowlist. Therefore
a field permitted by a retained historical contract but forbidden by current policy
fails closed before any response is returned. Request HMAC bytes, fingerprint
versions, HMAC-key rotation, stored status, and outcome-contract persistence remain
unchanged.

### O.3 Final verification

The local safety check confirmed a password-bearing `localhost` disposable test URL
(`tripsync_pr05b2c_final_test`), a distinct disposable runtime test name, disabled
scheduler, mock AI, and empty provider keys. No cloud/RDS or provider endpoint was
used.

| Final command / boundary | Result |
| --- | --- |
| Focused Manual/Booking + B.2B/B.2A/B.1 PostgreSQL suite | PASS — 116 passed in 3.04s |
| False no-op attack cases | PASS — dirty, explicit flush, ORM insert/delete, commented UPDATE, and CTE UPDATE all reject and roll back item/revision/ledger state |
| Genuine Booking no-op + replay | PASS — no state/revision/audit/notice mutation; safe ledger replay remains available |
| Replay current-policy tests | PASS — stricter current allowlist rejects historical-only field; unsupported historical contract rejects; mutually safe historical replay succeeds |
| Legacy CI-equivalent, official Pydantic exclusions | PASS — 637 tests, 0 failures, 0 errors, 0 skips (JUnit; 30.509s) |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.93s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### O.4 Final focused verdict

**CONDITIONAL PASS for Manual Item and Booking only.** The two protected,
default-off vertical slices now have cross-Trip child lock scoping, no-op rollback
integrity, stricter replay response policy, and final local PostgreSQL regression
evidence. This is not full B.2C completion or production activation authorization;
the remaining direct legacy writers and the documented external/non-transactional
callback limitations remain outside this slice.

## P. Change Proposal / Decision submission writer migration (2026-10-09)

This authorized vertical slice migrates only the real initial submission route:
`POST /api/plans/items/{item_id}/changes` and its call to
`orchestrator.propose_change()`. It does **not** migrate the later vote, round
settlement, notice objection, proposal decision, organizer, scheduler, preference,
or generator writers.

### P.1 Preserved state machine and locked transaction protocol

The new `domain/transactions/change_proposal.py` adapter calls the existing
`orchestrator.propose_change()` inside the B.2A/B.2B owned Session; it does not
reimplement any classifier or transition. As a result, the established behavior is
preserved:

- `NOTICE` still applies the eligible patch, appends `PlanChange`, and creates the
  anonymous `UpdateNotice`.
- `ROUND` still opens a normal `DecisionRound`, rather than applying a contested
  change early.
- `REOPEN_ROUND` still requires nonblank reason text and opens a `kind=reopen`
  round; missing reason rolls the whole transaction back.
- `CONFIRM` still creates the original `ChangeProposal` / affected-member
  `ProposalDecision` records and leaves the item unchanged until every affected
  member approves through the existing, still-legacy later writer.

The protected command takes the canonical order:

```text
Trip -> credential -> actor membership -> all Trip memberships -> Plan -> ledger
     -> scoped PlanItem/all PlanItems -> MemberConstraint -> DecisionRound
     -> ChangeProposal -> Vote/ProposalDecision -> propose_change()
     -> exactly one Plan revision -> completed safe ledger outcome -> COMMIT
```

The all-membership lock is a private `PlanTransactionKernel` option used only by
this adapter; it is acquired before the Plan root lock so CONFIRM's electorate is
fresh and stable. Manual Item and Booking retain their accepted actor-only root
scope. The item lookup includes both `PlanItem.id` and `PlanItem.plan_id`, so a
foreign Trip item is not materialized or child-locked. Constraints, PlanItems,
open/closed rounds, proposals, votes, and proposal decisions are re-read with
row locks before authoritative classification and pending-decision validation.

All writes, the single revision increment, and the completed
`MutationIdempotency` record share the same owned Session and transaction. There
is no callback commit, independent Session, provider call, or network operation.
An injected post-domain checkpoint failure rolls back item/round/proposal/notice,
revision, and ledger together.

### P.2 Immutable semantic command and safe replay outcome

Operation code is `plan.change.submit.v1`, resource is the PlanItem, and the
immutable B.2B canonical snapshot contains:

```text
patch (including explicit local_title clear semantics)
request
reason
ordered full alternatives: item_id, label, title, body, tradeoff,
                            start_hour, day_date, duration_min
expected_plan_revision (in the common signed envelope)
```

The callback receives only this frozen snapshot. Alternative validation is repeated
after locking using the legacy acceptance rules (same item, first five in order,
time/duration validation, read-only classification); accepted labels/tradeoffs and
patches retain their existing `DecisionRound.options` behavior. A change to an
alternative title/tradeoff is therefore a same-key HMAC conflict, not a silent
replay.

The stored response is only the existing detached sanitized outcome: path,
headline/detail, fixed public check copy, anonymized findings, `applied`, and
round/proposal identifiers. It excludes raw request/reason text, alternative text,
credentials, membership identifiers, and raw constraint data. B.2B replay still
reauthorizes before ledger lookup, validates historical plus current trusted outcome
policy, and returns the original safe outcome without repeating effects.

### P.3 Default-off HTTP adapter

`CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES` is disabled by default. When off,
the original request-Session route remains unchanged, including its ordinary option
validation and commit behavior; a malformed optional revision header is ignored as
before. When enabled locally, both `Idempotency-Key` and
`X-Expected-Plan-Revision` are mandatory and parsed only in the protected branch.
The protected branch requires configured injected HMAC material and converts the
request identity to primitive preliminary scope plus private bearer proof before
opening the fresh owned Session.

Requested protected writes fail closed in `APP_ENV=production|prod`. Typed root,
ledger, revision, outcome-policy, lock/deadlock/serialization, and database errors
map to controlled 401/403/404/409/422/503 responses without HMAC, ledger,
credential, or database diagnostics.

### P.4 Local PostgreSQL evidence and regression

Before database execution, URL parsing required `host=localhost`, disposable
`tripsync_pr05b2c_final_test`, and a distinct disposable runtime URL. PostgreSQL
reported `localhost:5432 - accepting connections`; scheduler was disabled, mock AI
enabled, membership-header bypass disabled, and provider keys empty. No cloud/RDS,
AWS, production database, or real provider was used.

| Command / boundary | Result |
| --- | --- |
| Dedicated protected Change Proposal PostgreSQL + route suite | PASS — 24 passed in 1.66s |
| NOTICE / ROUND / REOPEN_ROUND / CONFIRM initial submission | PASS — direct apply/audit/notice; normal/reopen round; pending affected-member confirmation all preserve existing states |
| Reason, pending decision, stale revision, same-key replay, changed alternative payload | PASS — controlled failure/no duplicate state; identical replay returns retained safe outcome |
| Account and real Guest bearer | PASS — Account kernel command; Guest HTTP success/replay; revoked credential and removed membership rejected |
| Plan-scoped children and lock ordering | PASS — foreign item query carries item + Plan predicate; no foreign effect/ledger; SQL observation proves all-Trip membership lock occurs before Plan root lock |
| Concurrent same-item commands | PASS — independent connections; follower reached `Trip FOR UPDATE`; one effect/revision/ledger, follower stale-rejected |
| Atomic rollback / privacy / safe HTTP mapping | PASS — checkpoint rollback removed all effects; ledger excludes request/reason/alternative text; production gate and typed non-diagnostic errors covered |
| Focused B.2C/B.2B/B.2A/B.1 plus existing decision-state-machine regression | PASS — 247 passed in 16.85s |
| Legacy CI-equivalent with official Pydantic exclusions | PASS — 661 tests, 0 failures, 0 errors, 0 skips (JUnit; 29.892s) |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.12s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### P.5 Updated writer coverage matrix

| Writer family | B.2C state | Revision / replay status | Remaining boundary |
| --- | --- | --- | --- |
| Manual PlanItem insert | Migrated, default-off | one revision + durable replay | Gate remains dormant |
| Booking / unbooking | Migrated, default-off | state changes versioned; verified no-op outcome | Gate remains dormant |
| Change Proposal initial submission (`POST .../changes`) | **Migrated, default-off** | one revision + durable replay for NOTICE/ROUND/REOPEN_ROUND/CONFIRM submission effect | Later state-machine actions remain legacy |
| Notice objection, vote, round settlement, proposal decision | Legacy | not protected by this slice | Must join their own reviewed protocol; do not claim lifecycle-wide coverage |
| Organizer actions, preferences, constraints | Legacy | no aggregate replay/revision claim | Separate writer migration |
| Generator, scheduler, cover | Deferred | no aggregate replay/revision claim | B.2D lifecycle/provider protocol |
| Membership / credential lifecycle | Separate aggregate | authorization kernel evidence only | Activation/lifecycle coverage later |

### P.6 Verdict and remaining risks

**CONDITIONAL PASS for the Manual Item, Booking, and initial Change Proposal
submission vertical slices.** The new submission path has deterministic local
PostgreSQL evidence for state-machine preservation, current authorization,
Plan-scoped child locking, atomic revision/ledger behavior, safe replay, and
default-off HTTP compatibility.

This is **not** full B.2C completion or production activation authorization. The
subsequent decision writers listed above can still bypass revision/durable replay;
therefore this slice does not claim complete proposal lifecycle safety, full writer
coverage, application-wide concurrency protection, preview correctness, or
distributed exactly-once behavior. No B.2D/B.3/B.4 work has begun.

## Q. Change Proposal final Kernel integration verification (2026-10-09)

This focused verification changes no business rule, schema, feature-gate state, or
writer scope. It confirms that the accepted protected Change Proposal implementation
actually traverses the complete transaction chain at runtime.

### Q.1 Verified parameter propagation

Source inspection verified this exact private integration chain:

```text
change_proposal.execute_protected_change_proposal()
  -> DurableCommandKernel._execute_with_session_mutation(
       lock_all_trip_memberships=True)
  -> PlanTransactionKernel._run_locked_command(
       lock_all_trip_memberships=lock_all_trip_memberships)
  -> _acquire_locked_plan(...)
  -> Trip -> credential -> actor membership -> all Trip memberships -> Plan
```

`durable.py` already accepts the optional parameter and forwards it without
renaming, inversion, or fallback. No production-code correction was necessary in
this review round. A real PostgreSQL test invokes the public protected Change
Proposal helper (not a direct Kernel substitute), observes the two membership lock
queries before Plan, and verifies one Plan revision, one pending CONFIRM proposal,
and one completed ledger outcome.

### Q.2 Deterministic concurrent-root-lock evidence

The Change Proposal concurrency test now uses two worker threads, independently
checked-out PostgreSQL connections, one-way Events, and `before_cursor_execute` /
`after_cursor_execute` observation:

1. The first connection obtains `Trip FOR UPDATE` and pauses only after the real
   business mutation has run while its transaction remains open.
2. The second connection is proved distinct by underlying PostgreSQL connection
   identity, reaches its own `Trip FOR UPDATE`, and has not fired the matching
   `after_cursor_execute` event before the first is released.
3. Releasing the first transaction permits the second root-lock statement to
   complete. It then sees the fresh revision and safely raises stale revision.
4. Assertions confirm exactly one successful mutation, `Plan.revision == 1`, one
   `PlanChange`, and one completed `MutationIdempotency` outcome.

No sleep or process-local serialization mechanism is used. This supplements, rather
than replaces, the existing cross-Trip child-scope, replay, rollback, guest, and
state-machine tests.

### Q.3 Final verification

Before every PostgreSQL command, the test harness verified
`localhost:5432`, disposable `tripsync_pr05b2c_final_test`, and a distinct local
runtime URL; scheduler/real AI/provider configuration stayed disabled. No AWS/RDS,
production database, or provider endpoint was accessed.

| Command / boundary | Result |
| --- | --- |
| Protected Change Proposal integration/concurrency suite | PASS — 24 passed in 1.84s |
| Change Proposal + Booking + Manual Item + B.1/B.2A/B.2B focused suite | PASS — 140 passed in 3.59s |
| Legacy CI-equivalent with official Pydantic exclusions | PASS — 661 tests, 0 failures, 0 errors, 0 skips (JUnit; 31.625s) |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.02s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### Q.4 Final Change Proposal verdict

**CONDITIONAL PASS for the initial Change Proposal submission vertical slice.**
The Kernel -> Durable -> business-writer path, full-membership lock option,
single-revision/ledger commit, and independent-connection root-lock contention are
now directly evidenced on disposable PostgreSQL.

This is not a claim of full B.2C completion: notice objection, voting, round
settlement, proposal decisions, organizer actions, scheduler work, and other
unmigrated writers remain outside this accepted slice. No B.2D/B.3/B.4 work has
started.

## R. Vote Writer migration (2026-10-09)

### R.1 Scope and preserved behavior

This increment migrates only the real `POST /api/rounds/{round_id}/votes` route.
The standalone `POST /api/rounds/{round_id}/settle` endpoint and the scheduler
remain legacy writers.  The new default-off adapter is
`backend/app/domain/transactions/vote.py`; `main.py` calls it only when
`CADENSY_B2C_PROTECTED_VOTE_WRITES` is truthy outside a production environment.
Production requests with that gate requested fail closed with `503`; gate-off
requests continue through the original route before optional durable headers are
interpreted.

The adapter uses the existing `orchestrator.cast_vote()`,
`_round_has_all_votes()` rule, `orchestrator.settle_round()`, and `_winner()`
semantics rather than duplicating decision policy. In particular, it preserves
vote replacement, normal-round ties keeping the current item, no automatic
settlement while a member has not voted, and a reopened-round change requiring
strictly more than half of all memberships.

### R.2 Transaction, lock, and idempotency protocol

The protected command is `round.vote.v1` with resource
`decision_round/<round_id>` and immutable command field `option_id`.  Its HMAC
therefore includes the Trip, sole Plan, actor membership, resource, expected
Plan revision, and frozen option.  The Durable Kernel takes its existing
Trip -> credential -> actor membership -> Plan -> ledger sequence and is
explicitly asked to lock all Trip memberships before Plan, because electorate
size is authoritative to all-voted and reopened-majority evaluation.

After that root order, the adapter locks the Plan-scoped DecisionRound, its
PlanItem, then existing Votes in a stable membership/id order. A Round lookup
joins PlanItem and requires `PlanItem.plan_id == context.plan.id`, so a foreign
Round is rejected before its child state can be changed. The writer then
reauthorizes through the Kernel, rechecks that the Round is open, changes or
creates the actor Vote if needed, and invokes the existing settlement routine
inside the same owned Session if every membership has voted.

Thus a final vote commits, or rolls back together:

```text
Vote + DecisionRound + PlanItem + PlanChange + UpdateNotice
     + exactly one Plan.revision increment + completed idempotency ledger
```

An equal existing vote is a genuine no-op: it does not call the legacy mutator,
does not advance revision, and stores a safely replayable completed ledger
outcome. An unusual pre-existing legacy state that is still open despite all
members having voted follows the legacy route behavior and settles on the next
vote request, therefore is state-changing and versioned.

### R.3 PostgreSQL and route evidence

`backend/tests/test_protected_vote_writer.py` adds real PostgreSQL coverage for:

| Behavior | Evidence |
| --- | --- |
| First vote, changed vote, same-key replay, and genuine no-op | Revision transitions `0 -> 1 -> 2`, replay has no duplicate Vote/ledger, and no-op remains at revision `2`. |
| Final vote settlement | Account then Guest vote produces one closed Round, applied PlanItem patch, one PlanChange, one UpdateNotice, revision `2`, and two durable outcomes; final replay changes none. |
| Decision semantics | Normal tie keeps current; a lone vote with another member silent remains open; a reopened Round changes only with a strict 3-of-4 majority. |
| Cross-Trip scope | Foreign Round query contains both Round id and Plan id, then fails without Vote or ledger effect. |
| Concurrent final vote | Two worker threads use distinct PostgreSQL connections. SQL events prove the second reaches `Trip FOR UPDATE` but cannot acquire it before the first is released. Afterwards exactly one settles and the other gets stale revision. |
| Atomic rollback | A fault after real Vote/settlement effects leaves the Round open, only pre-existing Vote intact, item unchanged, revision `0`, and no PlanChange/notice/ledger. |
| API/authorization safety | Real Guest bearer route supports replay; revoked Guest credential and removed membership fail closed. Gate-off ignores malformed optional revision header; requested production gate returns `503`. Typed lock/deadlock/aggregate/replay/revision/database failures map to controlled HTTP responses without injected diagnostic text. |

Before each database command, the shell verified `TEST_DATABASE_URL` was the
distinct disposable localhost database `tripsync_pr05b2c_final_test` and the
runtime URL was the different local
`tripsync_pr05b2c_final_runtime_test`; `pg_isready` reported
`localhost:5432 - accepting connections`. Scheduler and real AI/provider keys
were disabled. No cloud database, AWS/RDS endpoint, or provider was used.

### R.4 Executed validation

| Command / boundary | Result |
| --- | --- |
| Protected Vote PostgreSQL suite | PASS — 18 passed in 1.51s (final rerun) |
| Vote + Change Proposal + Booking + Manual Item + B.1/B.2A/B.2B focused suite | PASS — 158 passed in 3.69s |
| Legacy CI-equivalent suite with official Pydantic exclusions | PASS — 679 passed in 31.36s |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.40s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### R.5 Writer Coverage Matrix after this increment

| Writer | B.2C status | Remaining limitation |
| --- | --- | --- |
| Manual PlanItem add | Protected, default-off | Other PlanItem writers remain legacy. |
| Booking/unbooking | Protected, default-off | Only this booking route is covered. |
| Initial Change Proposal submission | Protected, default-off | Proposal decisions and notice objections remain legacy. |
| Round Vote | Protected, default-off | Manual settlement and scheduler settlement remain legacy. |
| Vote-triggered immediate settlement | Protected only when reached from protected Vote | Direct settlement writers can still bypass revision/ledger. |
| Vote / Proposal Decision / Organizer / Preferences / Generator / Scheduler outside named slices | Legacy | No global writer-coverage or production-activation claim. |

### R.6 Vote vertical-slice verdict

**CONDITIONAL PASS for the Vote Writer vertical slice.** The protected vote
route preserves the existing state machine while evidencing atomic final-vote
settlement, durable replay, fresh authorization, root-lock contention, and
single-revision behavior on disposable PostgreSQL. It is intentionally
default-off and must not be treated as production activation or full B.2C
completion. Standalone settlement, scheduler, proposal-decision and all other
unmigrated writers remain explicit prerequisites for any later coverage or
activation decision. No B.2D/B.3/B.4 work has started.

## S. Vote final hardening (2026-10-09)

### S.1 P1 — authoritative Vote option validation

Review reproduced the defect in both paths: a value outside
`DecisionRound.options` could be written as a Vote. With two members, the second
such legacy request could then reach settlement without a matching option and
surface an internal error.

The smallest shared correction is `orchestrator.validate_vote_option()`. It
accepts only a non-empty string which exactly equals a non-empty `id` from the
authoritative stored `DecisionRound.options` list. It deliberately does not
normalize IDs, reinterpret client input, or alter any valid option semantics.

`orchestrator.cast_vote()` now invokes that helper before querying or mutating a
Vote, covering legacy/direct callers. The protected adapter invokes the same
helper after it has acquired the authoritative `DecisionRound FOR UPDATE` and
before it calls `cast_vote()`, explicitly satisfying the protected lock-time
validation requirement. Both paths map `InvalidVoteOption` to a controlled
`422`. Missing or wrong-typed request fields remain FastAPI/Pydantic `422`s;
blank and unknown strings are domain `422`s.

Real PostgreSQL tests prove normal and reopened invalid votes leave unchanged:
Vote, DecisionRound, PlanItem, PlanChange, UpdateNotice, Plan revision, and
MutationIdempotency. Two separate legacy members submitting an unknown option
both receive `422`; no settlement exception is reached.

### S.2 Idempotency and voting-semantics regressions

The Vote suite now additionally proves:

| Case | Verified outcome |
| --- | --- |
| Same Idempotency-Key, different valid option ID | Controlled `IdempotencyKeyReused`; first Vote/revision/ledger remains the only effect. |
| Reopened round without strict majority | Four-member 2-to-2 result closes as `keep`; original PlanItem remains unchanged. |
| Same-option no-op | No Plan revision increment and no duplicate business records; one safely replayable no-op outcome remains. |
| Valid final vote | One atomic settlement with exactly one PlanChange, UpdateNotice, revision advancement, and completed ledger outcome. |

### S.3 PostgreSQL lock-state evidence

The two-worker final-vote contention test still uses independently checked-out
connections and Event ordering. It now records each psycopg backend PID and,
before releasing the first transaction, uses a third observer connection to
assert that PostgreSQL `pg_blocking_pids(second_pid)` contains `first_pid`.
This is direct server lock-state evidence that the second command is blocked on
the first Trip root lock, not merely a client-side timing inference. No sleep is
used. After release, exactly one command settles and advances revision; the
competing different-key command safely receives stale revision. Assertions
confirm no duplicate Vote, settlement, PlanChange, UpdateNotice, revision, or
ledger outcome.

### S.4 Validation

Every database invocation rechecked a distinct local disposable
`tripsync_pr05b2c_final_test` URL against a separate local runtime database,
with `localhost:5432` accepting connections. Scheduler and providers stayed
disabled; no AWS/RDS or real provider was contacted.

| Command / boundary | Result |
| --- | --- |
| Protected Vote real-PostgreSQL suite | PASS — 27 passed in 2.19s |
| Vote + Change Proposal + Booking + Manual Item + B.1/B.2A/B.2B focused suite | PASS — 167 passed in 4.54s |
| Legacy CI-equivalent suite with official Pydantic exclusions | PASS — 688 passed in 34.80s |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.78s |
| Final changed-module `py_compile` and `git diff --check` | PASS |

### S.5 Vote final-hardening verdict

**PASS for the Vote Writer vertical slice.** Option admission is now shared by
Legacy and protected paths, invalid choices fail before any durable or domain
write, and PostgreSQL proves actual root-lock blocking for the competing-final
vote path. This verdict applies only to the Vote slice and does not change the
overall B.2C limitation: standalone settlement, scheduler, proposal decision,
notice objection, and other direct writers remain legacy. It is not production
activation or full writer coverage authorization; no B.2D/B.3/B.4 work began.

## T. Proposal Decision Writer migration (2026-10-09)

This increment migrates only `POST /api/proposals/{proposal_id}/decisions`.
`proposal_decision.py` uses durable operation `plan.proposal.decision.v1`, a
frozen validated `accepted|declined` command field, a detached anonymous
equivalent of `_proposal_out()`, and a default-off non-production gate
`CADENSY_B2C_PROTECTED_PROPOSAL_DECISION_WRITES`.

After the Kernel locks Trip -> credential -> memberships -> Plan -> ledger, the
adapter locks the Plan-scoped authoritative Proposal, all PlanItems used by
conflict validation, and ProposalDecision rows. It then calls the existing
`orchestrator.decide_proposal()` as the sole confirmation state machine. Thus
eligibility, decline, unanimous approval, conflict escalation, `_apply()`,
PlanChange and UpdateNotice semantics are unchanged. Repeated identical normal
decisions are recognized as durable no-ops before the legacy function's
otherwise harmless flush; inconsistent legacy waiting rows with all approvals
or a decline still traverse the authoritative state machine for recovery.

Real PostgreSQL coverage verifies partial acceptance, final application/replay,
decline, schedule-conflict escalation, changed-key conflict, stale revision,
no-op, Guest route/privacy DTO, non-affected and cross-Trip isolation, injected
rollback, concurrent final attempts, and acceptance-versus-decline races.
Final application atomically includes decision/proposal/item effects,
PlanChange, UpdateNotice, one revision increment, and ledger outcome. Tests use
independent connections and Events; competing commands observe the root lock
then safely fail stale after release, with no duplicate effects.

| Command / boundary | Result |
| --- | --- |
| Protected Proposal Decision PostgreSQL suite | PASS — 11 passed in 1.35s |
| Proposal Decision + Vote + prior B.1/B.2A/B.2B/B.2C slices | PASS — 178 passed in 4.27s |
| Legacy CI-equivalent with official Pydantic exclusions | PASS — 699 passed in 30.81s |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.62s |
| Changed-module `py_compile` and `git diff --check` | PASS |

**PASS for the Proposal Decision vertical slice.** This is not full B.2C:
Notice Objection, standalone Round Settlement, Organizer Actions, Preferences,
Generator, Scheduler, and other direct writers remain legacy. No production
activation, B.2D, B.3, or B.4 work has begun.

## U. Proposal Decision final concurrency acceptance (2026-10-09)

### U.1 Distinct-member concurrent acceptance proof

A new real PostgreSQL test begins with Account already accepted, Guest and
Extra pending, `proposal.status == waiting_affected_members`, and Plan revision
`0`. Guest and Extra use independent checked-out PostgreSQL connections and
different idempotency keys, both initially expecting revision `0`.

The first Guest acceptance is paused after the real state machine has made its
partial decision but before transaction commit. SQL event capture records both
driver-connection identities and psycopg backend PIDs. A third observer
connection proves `pg_blocking_pids(extra_pid)` contains `guest_pid` while the
Guest transaction is held. There are no sleeps; Event ordering and server lock
state are the evidence.

After release, Guest commits exactly one partial decision and revision `0 -> 1`.
Extra's original revision-0 command safely raises `StalePlanRevision`; the Kernel
does not retry it. Extra then submits a new key at expected revision `1`, which
performs the only unanimous application and advances revision to `2`. Assertions
confirm exactly two successful completed ledger outcomes, one PlanChange, one
UpdateNotice, and no duplicate application/proposal transition/audit record.

### U.2 API integration verification

Current `main.py` inspection confirms the protected gate is default-off,
feature-off retains the legacy route before protected-header parsing, requested
activation in `production` returns controlled `503`, and feature-on requires
both `Idempotency-Key` and `X-Expected-Plan-Revision`. The adapter constructs
only primitive preliminary scope; the Transaction Kernel performs fresh
Account-first/Guest credential and membership reauthorization in its own Session.
Typed failures map to controlled public responses. The stored and returned DTO
is the anonymous `_proposal_out()`-compatible shape: no membership IDs,
credentials, or private constraint material are present.

The existing Accept-versus-Decline independent-connection test remains: the
first Decline becomes the single terminal effect, the competing Accept becomes
stale, and PlanItem/revision/proposal state cannot become inconsistent.

### U.3 Final validation

Disposable localhost safety was rechecked before every database run:
`tripsync_pr05b2c_final_test` is distinct from the local runtime URL,
`localhost:5432` accepted connections, scheduler and providers remained disabled.
No AWS/RDS, provider call, production database, migration, commit, push, or
activation occurred.

| Command / boundary | Result |
| --- | --- |
| Focused Proposal Decision PostgreSQL suite | PASS — 12 passed in 1.94s |
| Proposal Decision + Vote + Change + Booking + Manual Item + B.1/B.2A/B.2B | PASS — 179 passed in 4.80s |
| Legacy CI-equivalent suite with official Pydantic exclusions | PASS — 700 passed in 38.60s |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 7.90s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### U.4 Final Proposal Decision verdict

**PASS for the Proposal Decision vertical slice.** Distinct affected-member
acceptance, explicit stale retry, PostgreSQL root-lock blocking, replay/no-op,
authorization, API isolation, and atomic terminal outcomes are now directly
evidenced. This remains only a vertical-slice acceptance: Notice Objection,
standalone Round Settlement, Organizer Actions, Preferences, Generator,
Scheduler, and other direct writers are still legacy. It is not complete B.2C
or production activation authorization; no B.2D/B.3/B.4 work has started.

## V. Notice Objection protected-writer migration (2026-10-09)

### V.1 Narrow implementation and preserved state machine

Only `POST /api/updates/{notice_id}/object` is migrated. The new
`domain/transactions/notice_objection.py` adapter calls the existing
`orchestrator.object_to_notice()` as the sole authority for the pending-decision
guard, Notice mutation, normal-round construction, option generation, deadline,
and sanitized classification outcome. It does not add a request body, settlement,
or a second objection state machine.

The durable command is `notice.object.v1`, with `update_notice` resource identity,
expected Plan revision, locked actor scope, and an immutable empty command-field
mapping (the endpoint has no semantic business input). Its server-owned response
allowlist is exactly the existing sanitized outcome shape: classification fields,
`applied`, `round_id`, and `proposal_id`; it contains no credential, membership,
raw constraint, or provider material.

After the canonical Kernel locks Trip -> Credential -> Memberships -> Plan ->
Ledger, the adapter locates the Notice in one `FOR UPDATE OF update_notice`
statement constrained by both `UpdateNotice.trip_id == locked_trip.id` and
`PlanItem.plan_id == locked_plan.id`. Thus a foreign or mismatched Notice has no
matching child row to lock. It then locks the authoritative PlanItem, all its
DecisionRound facts, and all its ChangeProposal facts in that stable order before
rechecking eligibility and entering the unchanged orchestrator. A successful
objection atomically changes the Notice to `can_object=False` and the prescribed
body, opens one normal Round, advances Plan revision once, and stores one completed
Ledger outcome. `AlreadyPending`, disabled Notice, stale revision, changed-key
reuse, and foreign-resource paths raise and roll back rather than inventing a
successful no-op.

### V.2 HTTP gate and error boundary

`CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES` is default-off. With the gate
off, the legacy route is selected before protected header parsing, so a malformed
optional revision header preserves legacy behavior. With the gate on, local
non-production execution requires both `Idempotency-Key` and
`X-Expected-Plan-Revision`; malformed revision input receives controlled `422`.
Requested activation under `APP_ENV=production|prod` receives `503` before any
writer selection. The adapter receives primitive preliminary Account-first/Guest
scope facts only, while the owned transaction Session freshly locks and validates
the bearer session and membership. Typed kernel/durable errors map to controlled,
non-diagnostic HTTP responses.

### V.3 PostgreSQL and HTTP evidence

`test_protected_notice_objection_writer.py` adds real PostgreSQL coverage for:

- Eligible Account and Guest bearer objections; exactly one open normal Round;
  original option IDs (`keep`, `requested`, `split`) and deadline policy retained.
- Required Notice body/eligibility transition, exactly one Plan revision and one
  completed Ledger record, and detached sanitized response output.
- Identical replay, stale revision, changed-key reuse, and a new key against an
  already consumed Notice.
- Existing open Round and pending ChangeProposal rejection without a Notice,
  revision, or Ledger effect; cross-Trip and mismatched PlanItem relationships
  reject without a business effect.
- Real Guest route replay plus revoked credential and removed-membership rejection;
  real Account route success; feature-off compatibility, production rejection,
  missing protected headers, and malformed protected headers.
- Fault injection after the authoritative mutation verifies rollback of Notice,
  Round, revision, and Ledger.

The deterministic same-Notice concurrency test uses Account and Guest commands on
independent psycopg connections with different backend PIDs. The first command is
paused after the real orchestrator effect but before commit. SQL events show the
second reaches `Trip FOR UPDATE`; a third connection verifies
`pg_blocking_pids(second_pid)` contains the first PID. After Event-based release,
one command commits revision `0 -> 1` and the other receives `StalePlanRevision`.
Exactly one Round and Ledger row exist. No arbitrary sleep is used.

### V.4 Validation

Before each database run, `TEST_DATABASE_URL` was verified as disposable local
`postgresql+psycopg://...@localhost:5432/tripsync_pr05b2c_final_test`, distinct
from the local runtime URL; `pg_isready` confirmed localhost PostgreSQL. Scheduler
and providers stayed disabled. No AWS/RDS, real provider, migration, commit, push,
or production activation occurred.

| Command / boundary | Result |
| --- | --- |
| Focused Notice Objection PostgreSQL + HTTP suite | PASS — 10 passed in 1.38s |
| B.1/B.2A/B.2B plus all five B.2C writer slices | PASS — 189 passed in 4.85s |
| Legacy CI-equivalent suite with official Pydantic exclusions | PASS — exit 0; 710 tests collected |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.79s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### V.5 Writer coverage and remaining risk

Protected B.2C vertical slices are now Manual PlanItem, Booking/Unbooking, Change
Proposal submission, Vote with immediate final settlement, Proposal Decision, and
Notice Objection. Standalone Round Settlement, Organizer Actions, Preferences /
Constraints, Generator, Scheduler, Membership lifecycle, and other direct writers
remain legacy and can still bypass aggregate revision advancement. Therefore this
is not full B.2C writer coverage or authorization for production activation.

### V.6 Final Notice Objection verdict

**PASS for the Notice Objection vertical slice only.** The existing state machine,
fresh authorization, plan-scoped child locking, durable replay, atomic rollback,
default-off API boundary, and PostgreSQL root-lock contention are directly covered.
No B.2D, B.3, or B.4 work has started.

## W. Standalone Round Settlement protected-writer migration (2026-10-09)

Only `POST /api/rounds/{round_id}/settle` is migrated. The new
`round_settlement.py` adapter uses the canonical Trip -> Credential ->
Memberships -> Plan -> Ledger roots, then locks the Plan-scoped Round, its
PlanItem, and Votes in the same Round -> PlanItem -> Votes order as the protected
Vote writer. It delegates winner choice and every effect to the existing
`orchestrator.settle_round()`; Scheduler settlement remains untouched.

`round.settle.v1` has no invented command fields. Open -> closed settlement
atomically writes Round metadata, PlanItem effect, PlanChange, UpdateNotice,
one revision advancement, and the completed Ledger outcome. A closed Round is a
durable no-op: it returns the existing `_round_out`-compatible DTO without a
revision or another business effect. The default-off
`CADENSY_B2C_PROTECTED_ROUND_SETTLEMENT_WRITES` gate preserves legacy behavior;
protected activation requires headers, rejects production with 503, and maps
typed failures without database diagnostics.

Real PostgreSQL tests cover normal majority, tie/keep-current, reopened strict
majority and non-majority, zero-vote settlement, removal, replay, closed no-op,
stale/key conflict, Account/Guest route behavior, revocation, gate behavior, and
post-domain rollback. Cross-path tests cover both orders: standalone Settlement
first leaves final Vote stale; final Vote first leaves stale Settlement rejected,
then a fresh Settlement is a no-op. The latter records independent backend PIDs
and proves `pg_blocking_pids()` reports the first Trip lock while the second
command waits; no sleep is used.

| Command / boundary | Result |
| --- | --- |
| Settlement + Vote focused PostgreSQL suite | PASS — 38 passed in 2.10s |
| B.1/B.2A/B.2B plus all migrated B.2C writer slices | PASS — 200 passed in 5.30s |
| Legacy CI-equivalent suite with official Pydantic exclusions | PASS — exit 0 |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.65s |
| `py_compile` and `git diff --check` | PASS |

**CONDITIONAL PASS for the Standalone Round Settlement vertical slice only.**
The adapter and its primary safety/atomicity behavior are implemented and the
focused, combined, Legacy, and Pydantic runs pass. Before an independent final
acceptance, add the same `pg_blocking_pids()` proof to the Settlement-first /
Vote-stale scenario (the inverse Vote-first scenario has that proof) and add a
dedicated cross-Trip Round lock-observation assertion. It is not full B.2C:
Scheduler-driven settlement, Organizer Actions, Preferences/Constraints,
Generator, membership lifecycle, and other direct writers remain legacy. No
B.2D/B.3/B.4 or production activation has begun.

### W.1 Final acceptance corrections

The Settlement-first test now preloads the Account's valid `requested` Vote, so
the competing Guest command is genuinely the final Vote. Settlement is paused
only after the authoritative state machine executes. SQL events capture distinct
psycopg connections and backend PIDs; before release, an observer verifies
`pg_blocking_pids(guest_vote_pid)` contains the Settlement PID. The `finally`
path always releases and joins both workers. After release, Settlement alone
commits: Round closes with `requested`, PlanItem becomes `Changed item`, revision
is exactly `1`, one PlanChange/Notice/Ledger exists, and the Guest Vote is absent.

A dedicated foreign-Round test now submits Trip A actor scope with Trip B Round
ID. The observed `FOR UPDATE` Round SQL contains `plan_item.plan_id`, the adapter
raises `RoundSettlementRoundMissing`, and neither Trip's revision nor the foreign
Round/PlanItem/Votes changes; no PlanChange, Notice, or Ledger is created.

Removal assertions now require `winning_option_id == remove` and
`PlanItem.settledness == removed`. Closed-Round new-key no-op behavior remains
covered: no extra revision/effect, one retained no-op ledger outcome, and replay
does not add a record. Guest revocation/removal and default-off/production/header
gate tests remain in the focused suite.

| Command / boundary | Result |
| --- | --- |
| Settlement + Vote + Notice focused PostgreSQL regression | PASS — 49 passed in 2.68s |
| Changed-module `py_compile` and `git diff --check` | PASS |

### W.2 Final Standalone Round Settlement verdict

**PASS for the Standalone Round Settlement vertical slice only.** Both protected
writers now have direct PostgreSQL Trip-root blocking evidence in both execution
orders, and foreign Round access is plan-scoped before child-row locking. This is
still not full B.2C or production activation: Scheduler and the remaining legacy
writers remain outside this slice.
