# PR-05B.2A — Transaction Kernel Implementation Record

## A. Source baseline

- Worktree: `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`
- Branch: `codex/pr05b1-schema-foundation`
- `HEAD` and `origin/main` at audit start: `17026adf4b77ced2280629f2b1584fd6892e420e`
- Existing B.1 source was present but intentionally uncommitted: Plan revision,
  idempotency ledger, migration `6f77e775d201`, canonical HMAC helpers, tests, and
  report 44. These files were preserved as the immediate baseline.
- Current Alembic head: `6f77e775d201`. B.2A creates no migration.

### Verified source summary

`backend/app/db/session.py` gives FastAPI a request-scoped Session. Existing API
authentication and mutation routes use that Session and route-level commits.
`domain/auth.py` validates account/guest bearers through `AuthSession` and
`GuestSession`, but its public helpers currently use ordinary reads. Current
`remove_membership()` and existing API writers do not use the new root protocol.
They remain unchanged in B.2A.

`TripScope` is request-session read authorization and is not imported by the
kernel. Existing mutation, generator, scheduler, and cover-provider paths may still
bypass aggregate locks. The known cover provider call under a Trip lock was not
invoked or modified.

## B. Actual implementation

### Files added

| File | Purpose |
| --- | --- |
| `backend/app/domain/transactions/__init__.py` | Narrow internal export surface. |
| `backend/app/domain/transactions/kernel.py` | Fresh Session lifecycle, root locking, fresh authorization, immutable primitive scopes, callback context, and typed database failures. |
| `backend/tests/test_transaction_kernel.py` | PostgreSQL independent-connection, deterministic-interleaving, cleanup, nonactivation, and failure tests. |
| `AI_enhanced/phase-0-audit-2026-10-03/45_PR05B2A_TRANSACTION_KERNEL_IMPLEMENTATION.md` | This record. |

`PlanTransactionKernel.execute()` accepts primitive `PreliminaryActorScope`, private
`CredentialProof`, optional required role, and a callback. Proof construction hashes
the bearer with existing `auth.token_hash()` and retains no raw-token field. The
kernel creates a new injected Session, enters one explicit `session.begin()` block,
then commits once only after a normal callback return. Exceptions roll back and
`finally` closes the Session.

`LockedPlanContext` intentionally has no public Session/commit API. Its Trip/Plan
references are available only during the callback; the kernel invalidates it before
the Session is closed. It is not a response DTO, cache item, or Agent input.

### Root locking, authorization, and failures

The enforced order is:

```text
Trip FOR UPDATE
  -> AuthSession or GuestSession FOR UPDATE
  -> TripMembership FOR UPDATE
  -> Plan FOR UPDATE
```

Account proofs reject revoked, expired, or mismatched-user sessions and then require
an active membership matching the primitive Trip/membership/user identifiers. Guest
proofs reject revoked, expired, wrong-Trip, or wrong-membership sessions and require
a joined anonymous membership. Required role validation occurs after the fresh
membership lock/read.

Plan lookup runs only after root and authorization locks. Missing Trip, missing Plan,
or duplicate Plans produce typed failures; no Plan is created and no arbitrary Plan
is selected. The helper uses `FOR UPDATE`, not `FOR NO KEY UPDATE`.

Typed failures cover missing root state, duplicate Plans, invalid credentials,
membership/role failure, integrity violation, lock unavailable, deadlock, and
serialization failure. SQLSTATE `55P03`, `40P01`, and `40001` are classified, but
B.2A deliberately has no automatic retry or external work under locks.

## C. Lock and authorization proof

- Captured SQL proves Trip `FOR UPDATE` precedes Plan `FOR UPDATE`.
- A stale request Session Plan retains its old value after another kernel command
  commits, while a fresh kernel Session reads the committed value.
- Independent writer B reaches the conflicting Trip lock but cannot enter its
  callback until A releases/commits; B then observes A's committed change.
- A membership removed after preliminary read is denied by fresh reauthorization.
- A compliant Trip-first membership removal or credential revocation waits while an
  authorized kernel callback holds root locks; afterward a new kernel attempt is
  denied.

This proof is intentionally limited to callers using this internal helper and to the
compliant test transactions. Existing public writers, removal/logout paths,
scheduler work, and provider paths are not yet converted, so B.2A does not claim
global revocation linearizability or production-wide concurrency safety.

## D. PostgreSQL test evidence

Before destructive test setup, URL parsing verified:

```text
TEST_DATABASE_URL host=localhost database=cadensy_pr05b2a_test
DATABASE_URL      host=localhost database=cadensy_pr05b2a_runtime_test
distinct=yes; both explicitly disposable *_test databases
```

`pg_isready` reported localhost accepting connections. Tests set
`DISABLE_SCHEDULER=1`, `MOCK_AI=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and empty
DeepSeek/Geoapify keys. No cloud/RDS endpoint was used.

The kernel tests commit seed rows so independently checked-out connections can see
them. They record every seed Trip/user pair and remove only recorded rows after each
test. This preserves legacy empty-database assumptions while avoiding shared Session
or outer-fixture-transaction visibility.

Events and SQL-event observation orchestrate workers; no arbitrary sleeps are used.
Worker exceptions are returned to the coordinator. A local PostgreSQL lock timeout
proves typed `55P03` handling and failed-Session closure.

| Scenario | Result |
| --- | --- |
| Fresh Session/connection ownership; no raw-token proof field | PASS |
| Trip-before-Plan lock order; callback context expiry | PASS |
| Same-root writer serialization and committed-state read | PASS |
| Stale identity-map avoidance | PASS |
| Membership removal before root acquisition | PASS |
| Compliant membership removal waits | PASS |
| Guest/account revocation before authorization and ordered revocation | PASS |
| Missing Trip/Plan, duplicate Plans, insufficient role | PASS |
| Rollback, lock timeout, Session closure | PASS |
| No public route activation; retryable SQLSTATE classification | PASS |
| `pytest tests/test_transaction_kernel.py -q` | PASS — 16 passed |

An initial full Legacy run found three upsert-test failures because committed kernel
fixture rows were not cleaned up. This was a B.2A test-isolation defect, not accepted
as success. The module now records/removes only its own committed rows; 16 kernel
tests and the affected 5 existing tests passed before the final full regression.

## E. Regression evidence

| Command / boundary | Result |
| --- | --- |
| `py_compile` for kernel and test modules | PASS |
| Source-only kernel error/public-route checks | PASS — 3 passed |
| Focused PostgreSQL kernel suite | PASS — 16 passed |
| Affected upsert tests after cleanup correction | PASS — 5 passed |
| CI-equivalent Legacy command with official Pydantic exclusions/JUnit contract | PASS — 550 passed, 0 skipped |
| CI-equivalent isolated Pydantic fake-compatibility command | PASS — 119 passed |
| `alembic heads` | PASS — `6f77e775d201` only |
| `git diff --check` / API diff | PASS — no whitespace error; `backend/app/api/main.py` has no diff |

No final required validation was skipped or not executable. The first Legacy failure
is documented above and the final CI-equivalent Legacy run passed.

## F. Remaining issues and phase boundary

- **B.2B:** no ledger insert, HMAC lookup, idempotency-key parsing, replay, outcome
  storage, HMAC rotation, or retention/cleanup exists yet.
- **B.2C/B.2D:** no existing mutation route/writer uses this kernel; full writer
  coverage and atomic revision advance remain absent.
- **Scheduler:** future Trip/Plan root work claiming and terminal-state rechecks.
- **Generator:** future Prepare/Validate/Commit; no provider call under locks.
- **Trip Cover:** existing provider-under-Trip-lock behavior remains unintegrated.
- **B.3:** revision bracketing before the earliest business read remains absent.
- HMAC rotation, ledger retention, and duplicate-Plan preflight remain later work.

No public route requires expected revision, idempotency key, or a new request field.
No existing route invokes `PlanTransactionKernel`.

## G. Final verdict

**CONDITIONAL PASS.** B.2A's internal kernel, fresh reauthorization, root locking,
typed rollback behavior, and isolated PostgreSQL evidence are ready for independent
review. It remains dormant until separate B.2B/B.2C authorization and review.

PR-05B.2A implements the internal Transaction Kernel only. Existing public mutation
routes have NOT been migrated. Full concurrency protection and idempotency replay
have NOT been implemented or activated.

## H. Focused independent-review corrections (2026-10-08)

This section records the approved B.2A-only corrections. It does not activate the
kernel in a public route, change an Alembic migration, or begin B.2B.

### H.1 P0 — Account-first credential proof binding

**Verified finding.** `current_membership()` in `backend/app/api/main.py` resolves
`user_for_token()` first and attempts Guest authentication only after an Account
failure plus the Guest-prefix check (lines 114–128). The existing source comment
explicitly identifies the collision case: a valid Account bearer can share the
Guest prefix. The previous `CredentialProof.from_bearer_token()` classification
therefore disagreed with the public authentication contract.

**Correction.** `CredentialProof` now contains only the existing private token
HMAC/hash and has no `credential_kind`. The primitive
`PreliminaryActorScope.credential_kind`, produced from the already validated
Account-first request scope, selects the locked `AuthSession` or `GuestSession`
lookup. The locked lookup still binds an Account credential to
`expected_user_id`, and a Guest credential to its Trip/membership pair. Thus a
Guest bearer cannot become an Account merely by supplying an Account scope, and an
Account bearer cannot become a Guest merely by supplying a Guest scope. Raw bearer
handling and hashing are unchanged.

**Proof.** The focused suite covers ordinary Account and Guest success, an Account
token beginning `gst_` succeeding under Account scope, both cross-kind
impersonation attempts failing closed, invalid credential failure, removed
membership denial, and revoked credential denial.

### H.2 P1 — callback return ownership boundary

**Verified finding.** A direct `return operation(context)` could return the
Session-owned `Plan` ORM object or `LockedPlanContext` after the kernel closes its
Session.

**Correction.** Before the enclosing `session.begin()` may commit, the kernel now
validates the callback result. It permits detached scalars and recursively checked
ordinary `list`, `tuple`, and string-keyed `dict` containers. Explicit results may
subclass the lightweight `SafeCommandResult` marker as a dataclass; every field is
checked recursively. ORM instances, `LockedPlanContext`, cyclic containers, and
unrecognized objects raise `UnsafeCallbackResult`. This is deliberately an
ownership guard rather than a serialization framework and exposes neither Session
nor commit APIs.

**Proof.** Primitive/container and explicit DTO returns pass. Direct ORM and
context returns, plus a nested ORM return, fail closed. A rejected ORM return after
staging a Plan change rolls the transaction back and closes the owned Session.
Context-expiry coverage remains passing.

### H.3 P1 — actual credential-only Logout race

**Verified finding.** `POST /api/auth/logout` remains Account-first
(`backend/app/api/main.py`, lines 598–612), then calls `revoke_token()` or
`revoke_guest_token()`; those helpers update only the credential row and do not
take a Trip lock (`backend/app/domain/auth.py`, lines 229–235 and 262–268).

**Correction and test evidence.** For each Account and Guest credential, an
independent revoker connection locks only its `AuthSession`/`GuestSession` row,
without a Trip lock. An independent kernel connection acquires Trip and reaches its
fresh credential `FOR UPDATE` query, where it waits. Event/SQL-query coordination
(no sleeps) releases the revoker, which commits `revoked_at`. The kernel then reads
the committed revocation, raises `InvalidCredential`, never enters the callback,
and both tracked Sessions close. This verifies safe waiting rather than claiming
that existing public logout has been migrated into the kernel's root-lock order.

### H.4 P2 — Account membership-status compatibility

**Verified finding.** `auth.membership_for_trip()` accepts every Account
membership whose status is not `removed` (`backend/app/domain/auth.py`,
lines 283–293). The kernel now intentionally preserves that current contract:
Account `invited` and `joined` memberships authorize; `removed` is rejected.
Guest behavior remains stricter and unchanged: it requires an anonymous,
`joined` membership. Focused tests verify the Account helper and the kernel agree
for invited, joined, and removed states. Any future product policy that disallows
Account `invited` writes requires a separate authorization decision and route
migration; it is not silently introduced in B.2A.

### H.5 Correction validation

Database commands first checked that the test endpoint was a localhost,
explicitly test-only PostgreSQL database distinct from the runtime URL; process
environment forced `DISABLE_SCHEDULER=1`, `MOCK_AI=1`,
`DEV_ALLOW_MEMBERSHIP_HEADER=0`, and empty DeepSeek/Geoapify keys. No provider,
AWS, or RDS connection was made.

| Command / boundary | Result |
| --- | --- |
| `pytest tests/test_transaction_kernel.py -q` | PASS — 28 passed in 1.56s |
| B.1 fingerprint/schema + Account/Guest authentication regression selection | PASS — 38 passed in 4.08s |
| Legacy CI-equivalent suite, with official Pydantic exclusions and JUnit no-skip check | PASS — 562 passed, 0 skipped |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.63s |
| `py_compile` for revised kernel and kernel tests | PASS |
| `git diff --check` | PASS |

### H.6 Remaining boundary and verdict

Remaining B.2B obligations are unchanged: durable ledger insertion/lookup, HMAC
rotation, idempotency-key parsing, concurrent replay semantics, stored outcomes,
and retention. Existing writers still do not use the kernel, revisions are not
advanced atomically, and provider/scheduler/cover paths are not migrated.

**CONDITIONAL PASS.** The focused B.2A corrections close the reviewed kernel
defects with isolated PostgreSQL evidence. They remain dormant pending separately
authorized B.2B and writer-integration work.

PR-05B.2A implements the internal Transaction Kernel only. Existing public mutation
routes have NOT been migrated. Full concurrency protection and idempotency replay
have NOT been implemented or activated.
