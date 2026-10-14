# PR-05B.2B — Durable Idempotency & Replay Kernel Implementation Record

## A. Source and Git state

- Worktree: `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`
- Branch: `codex/pr05b1-schema-foundation`
- `HEAD` and `origin/main`: `17026adf4b77ced2280629f2b1584fd6892e420e`.
- `git worktree list` confirmed the canonical `New` worktree remains on `main` and
  was not modified; the Codex visualization worktree was not touched.
- The B.1 schema/fingerprint and B.2A transaction-kernel changes were already
  uncommitted in this isolated worktree and were preserved. This B.2B work builds
  on them; it neither commits nor stages them.
- Alembic head is exactly `6f77e775d201`. B.2B creates no migration.

The final protocol source is report 43. Reports 41 and 42 were read from their
user-owned canonical-worktree copies because those historical design reports are not
copied into this isolated worktree. Reports 44 and 45 in this worktree establish the
actual B.1/B.2A implementation baseline.

**Exact scope:** an internal, existing-Plan durable command/replay protocol only.
No public route, frontend, existing writer, Agent/Runtime, scheduler, provider, or
production configuration was changed.

## B. Implementation

| File | Change |
| --- | --- |
| `backend/app/domain/transactions/kernel.py` | Refactored B.2A's owned Session/transaction lifecycle into package-private `_run_locked_command()`. Public `execute()` still exposes only `LockedPlanContext`; no public Session/commit API was added. |
| `backend/app/domain/transactions/durable.py` | Added the internal command contract, keyring protocol, strict response codec, ledger replay, same-transaction revision/ledger persistence, and typed errors. |
| `backend/app/domain/transactions/__init__.py` | Exports the narrow internal B.2B types. |
| `backend/tests/test_durable_idempotency_kernel.py` | Added PostgreSQL-backed command, replay, authorization, fault, rotation, expiry, response-boundary, and concurrency coverage. |
| `AI_enhanced/phase-0-audit-2026-10-03/46_PR05B2B_DURABLE_IDEMPOTENCY_IMPLEMENTATION.md` | This implementation record. |

### Public internal interfaces

`IdempotentCommand` is primitive/validated input for one logical existing-Plan
mutation. It requires operation/resource identity, bounded idempotency key,
expected Plan revision, validated operation-specific canonical fields,
fingerprint version, server-chosen expiry, and optionally an allowlist for top-level
response payload fields. It validates the key against an ASCII-safe 1–128-character
format before any database use.

`HmacKeyring` is the only B.2B key-management seam:

```text
active_key()                 -> key used for a new committed command
key_for_version(version)     -> retained key used only to verify replay
```

It is injected. No production secret, environment secret configuration, AWS service,
or key material database column was introduced. Tests use fixture-only byte keys.

`CommandResponse` is the only callback return shape eligible for completed-outcome
storage. It has a 2xx status, a JSON-compatible safe payload, and explicit
`state_changed=True`. B.2B rejects no-op outcomes rather than silently committing
an unversioned state mutation. `DurableCommandResult` is a detached response with
status, stored payload, resulting revision, and `replayed` state.

### Same-Session ledger integration and atomic revision contract

`DurableCommandKernel` delegates transaction ownership to the existing B.2A
`PlanTransactionKernel`; it does not create a Session of its own. Its private
collaborator receives the owned Session only inside the B.2A transaction boundary;
the local mutation callback receives only `LockedPlanContext`.

For a newly completed state-changing command, it stores the B.1 ledger row with:

- the frozen `(trip_id, actor_membership_id, idempotency_key)` scope;
- operation/resource identifiers;
- versioned canonical HMAC and HMAC-key version;
- outcome contract version, original status, and strict JSON payload;
- the exactly-one incremented Plan revision; and
- completed/expiry timestamps.

The B.1 `MutationIdempotency` schema is completed-only: no pending row is committed
before effects, and the code never opens a second Session/transaction for revision
or ledger work. `Plan.revision` is checked against the signed expected revision and
guarded against BIGINT overflow before increment.

### Fingerprint, replay, and retention semantics

The B.1 canonical HMAC helper is reused without a second canonicalizer. The signed
command envelope includes fingerprint version, operation code, Trip id, locked Plan
id, actor membership id, stable resource kind/id, expected revision, and validated
operation fields. Consequently operation/resource/body/revision changes produce a
key-reuse conflict. Unicode NFC, normalized finite numeric forms, date handling,
ordered arrays, and null-vs-omission semantics remain those of B.1.

On an existing ledger row, the stored fingerprint/key version selects the retained
verification key. The recomputed HMAC uses constant-time comparison. A missing
historical key, unsupported outcome version, invalid stored payload, or expired
retained row fails closed. An expired row is never silently re-executed while it
still exists and retains its unique key. No cleanup job exists in B.2B; a later
retention owner must decide duration, deletion scheduling, verification-key lifetime,
observability, and post-deletion key reuse semantics.

### Strict safe outcome boundary

Before a ledger row is staged, the codec permits only JSON-compatible primitives,
string-keyed mappings, lists/tuples, and finite floats. It rejects ORM objects,
`LockedPlanContext`, `CredentialProof`, SQLAlchemy Session/Connection objects,
binary values, arbitrary objects, cycles, non-finite floats, undeclared dynamic
attributes on `CommandResponse`, and fields outside a command-defined response
allowlist. The callback’s staged domain work rolls back if this encoding fails.

## C. Transaction proof

For this existing-Plan command path, the actual order is:

```text
fresh owned Session + BEGIN
  -> Trip FOR UPDATE
  -> current AuthSession/GuestSession FOR UPDATE
  -> current TripMembership FOR UPDATE
  -> exactly one Plan FOR UPDATE
  -> Trip/actor/key ledger row FOR UPDATE, if present
  -> replay verification OR expected revision comparison
  -> local callback with LockedPlanContext only
  -> strict safe-result encoding
  -> exactly one Plan.revision increment
  -> completed MutationIdempotency row
  -> flush + COMMIT
close Session and invalidate context
```

- **Authorization precedes replay:** B.2A obtains current credential/membership
  locks before the ledger query. Removed/revoked/expired actors cannot obtain a
  historical payload.
- **Replay precedes stale rejection:** after the locked ledger lookup, an identical
  retained command verifies and returns its stored response before comparison with
  the now-advanced Plan revision. It never calls the mutation callback, increments
  the revision, or rehydrates mutable resources.
- **Atomicity:** local effect, revision increment, row insertion, and outcome are
  inside B.2A's one `session.begin()` block and same connection. Checkpoint failures
  after domain work, after revision increment, after ledger insertion, and a
  `before_commit` failure all leave no durable effect, revision, or row.
- **Concurrent duplicate commands:** B waits at the inherited Trip root lock. When
  A commits, B reauthorizes, locks/reloads the Plan and ledger, validates the stored
  HMAC, and replays. No process-local mutex, polling, or sleep is used.
- **Mismatch confidentiality:** mismatch returns only typed
  `IdempotencyKeyReused`; it does not return the other operation/resource outcome.
- **Plan lifecycle boundary:** this implementation remains limited to one existing
  Plan. B.2A's missing/duplicate Plan failures are preserved. It does not claim Plan
  creation, find-or-create replay, or duplicate remediation support.

## D. PostgreSQL evidence

Before every database test invocation, the command parsed URLs and required a
localhost PostgreSQL `TEST_DATABASE_URL` with an explicitly test-only name, distinct
from `DATABASE_URL`. Processes set `DISABLE_SCHEDULER=1`, `MOCK_AI=1`,
`DEV_ALLOW_MEMBERSHIP_HEADER=0`, and empty DeepSeek/Geoapify keys. No RDS, AWS,
provider, or production database endpoint was used.

The B.2B suite creates committed fixtures solely so independent connections can
observe them, records their identities, and deletes only its own rows in FK-safe
order. It uses events/worker queues and bounded joins, never arbitrary sleeps.

| Evidence | Result |
| --- | --- |
| First success: domain effect, revision `0 -> 1`, one completed safe ledger row | PASS |
| Identical retry after referenced PlanItem removal | PASS — stored snapshot, no callback/revision/row recreation |
| Same key with changed operation/resource/body/expected revision | PASS — typed conflict, no callback |
| New key with stale revision | PASS — no effect/revision/ledger row |
| Concurrent identical requests on independent PostgreSQL Sessions | PASS — one callback/effect/revision/row; follower replay |
| Account and Guest revocation before replay; removed membership replay | PASS — authorization failure before ledger disclosure |
| Checkpoint failures after effect/revision/ledger and injected commit failure | PASS — full rollback |
| Old verify-only HMAC key replay; unavailable stored version | PASS — replay / fail closed respectively |
| Unicode/numeric canonical equivalence and semantic mismatch | PASS |
| ORM/credential/nested/nonfinite/binary/dynamic/extra-response-field rejection | PASS — rollback |
| Expired retained row | PASS — typed failure, no destructive cleanup or repeat execution |
| Key validation, overflow guard, and owned Session closure on success/replay/conflict/stale paths | PASS |
| `pytest tests/test_durable_idempotency_kernel.py -q` | PASS — 25 passed in 1.01s |

## E. Regression evidence

| Command / boundary | Result |
| --- | --- |
| `py_compile` for revised transaction modules and B.2B tests | PASS |
| B.1 fingerprint/schema + B.2A kernel + Account/Guest authentication selection | PASS — 66 passed in 4.30s |
| CI-equivalent Legacy suite with all official Pydantic-only exclusions and JUnit skip check | PASS — 587 passed, 0 skipped |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.38s |
| `git diff --check` and changed-file whitespace check | PASS |

No frozen PR-04 evaluator, observation, golden dataset, Runtime, or privacy artifact
was modified to obtain these results.

## F. Future obligations and non-claims

- Public HTTP `Idempotency-Key`, expected revision, endpoint error mapping, and
  client replay UX remain unimplemented.
- B.2C/B.2D must migrate every relevant writer and establish complete revision
  coverage before public concurrency claims.
- Full authorization linearization remains conditional on legacy removal/revocation
  and all writers joining the protocol.
- Generator still needs Prepare/Validate/Commit; no provider may run under root
  locks. Scheduler still needs root work claims. The Trip-cover provider-under-lock
  path is untouched.
- Missing-Plan creation/find/replay, duplicate-Plan data remediation, and
  revision-bracketed preview are deliberately not supported here.
- Production key ownership/rotation retention, ledger cleanup ownership/retention,
  monitoring, deployment/fleet gates, and live schema preflight are not decided or
  activated.
- This is durable local PostgreSQL outcome/replay behavior for retained rows; it
  does not claim distributed exactly-once effects or application-wide concurrency
  protection.

## G. Verdict

**CONDITIONAL PASS.** The B.2B internal existing-Plan durable idempotency/replay
kernel has deterministic PostgreSQL evidence and remains dormant. Full application
coverage and public activation remain intentionally blocked by later review.

PR-05B.2B implements the internal durable idempotency and replay kernel only.
Existing public mutation routes have NOT been migrated. Full application-wide
concurrency protection and public replay activation have NOT been implemented.

## I. Final trusted outcome-policy correction (2026-10-08)

This final narrow correction supersedes only the caller-selected allowlist wording
in H.3. It does not begin B.2C, migrate a writer, create a migration, or change an
HTTP/Frontend/Runtime contract.

### I.1 Server-owned outcome-contract registry

`IdempotentCommand` no longer contains `allowed_response_fields`; therefore a
command caller cannot omit, widen, or otherwise select a replay allowlist.
`DurableCommandKernel` now receives an `OutcomeContractRegistry` at its internal
composition boundary. The provided `StaticOutcomeContractRegistry` copies its
validated `OutcomeContract(operation_code, version, allowed_response_fields)`
entries into private read-only mappings.

- A new command resolves only the current contract for its operation before its
  callback can run, encodes the response against that contract, and persists that
  contract version in the completed ledger row.
- A replay first verifies the retained HMAC and operation identity, then resolves
  the stored `(operation_code, outcome_contract_version)` from the trusted registry.
  A removed/unknown historical contract fails closed; a retained historical version
  continues to govern its own safe stored representation.
- Both first-write encoding and replay decoding use the resolved contract's
  allowlist. Any unsafe/disallowed outcome raises inside B.2A's one owned
  transaction, so staged callback effects/revision/ledger work rolls back and the
  owned Session closes.

This keeps response policy outside the request HMAC: it is service configuration,
not caller-supplied command semantics. HMAC fingerprint version and HMAC key version
remain separate from outcome-contract version.

### I.2 New acceptance evidence

The new focused coverage proves that an `IdempotentCommand` has no allowlist field,
that a strict server policy rejects an extra first-write field, that a stored payload
outside the trusted policy fails closed without callback/revision/ledger changes,
that retained versioned contracts replay only when registered, and that a new command
with no registered server policy cannot call its mutation.

Before each PostgreSQL run, `TEST_DATABASE_URL` was explicitly set to a localhost
database whose name passed both repository safety guards, and was distinct from the
runtime `DATABASE_URL`. Scheduler and real AI/provider configuration were disabled;
no cloud/RDS, AWS, or real provider access occurred.

| Newly executed command / boundary | Result |
| --- | --- |
| `py_compile app/domain/transactions/durable.py app/domain/transactions/__init__.py tests/test_durable_idempotency_kernel.py` | PASS |
| `pytest tests/test_durable_idempotency_kernel.py -q` | PASS — 33 passed in 1.07s |
| `pytest tests/test_transaction_kernel.py tests/test_idempotency_schema.py tests/test_idempotency_fingerprint.py -q` | PASS — 41 passed in 1.03s |
| Legacy CI-equivalent regression with official Pydantic exclusions and JUnit skip assertion | PASS — 595 passed in 28.66s; 0 skipped |
| Isolated Pydantic fake-compatibility suite | PASS — 119 passed in 6.14s |
| `git diff --check` | PASS |

### I.3 Scope and verdict

**PASS for the B.2B internal Kernel scope.** The final replay outcome allowlist is
now demonstrably trusted server policy, versioned by operation, and transaction-safe
on failure. The kernel remains dormant: public endpoints, writer migration,
application-wide revision coverage, B.2C, production activation, distributed
exactly-once claims, and provider work remain out of scope and unimplemented.

## H. Focused independent-review corrections (2026-10-08)

This section records the focused B.2B correction round. It preserves the original
evidence above and does not begin B.2C, activate a route, create a migration, or
change an external integration.

### H.1 Fingerprint-format compatibility

**Implemented format set:** only fingerprint format **v1** exists. It is the B.1
canonical envelope and canonicalizer already used by this worktree. There is no v2
or other historical canonicalizer in source, so B.2B explicitly rejects every
fingerprint version other than `{1}` with `UnsupportedFingerprintVersion`.

The incoming command format is checked before its transaction begins. On replay,
the stored row's format is checked too, and the incoming and stored versions must be
identical before the stored HMAC is recomputed. Thus a retained row claiming an
unknown historical format fails closed rather than being verified with an imaginary
format. This is distinct from HMAC signing-key rotation: an otherwise supported v1
row can still be verified with its stored old HMAC key version while new writes use a
new active key. The existing old-key replay test remains passing.

### H.2 Immutable canonical command snapshot

`canonical_payload_snapshot()` was added to B.1's idempotency module. It reuses the
existing B.1 normalization rules and returns a deeply immutable projection
(mapping proxies plus tuples), rather than introducing a second canonical format or
a generic serializer. `IdempotentCommand` snapshots `command_fields` in its own
validation. Consequently Unicode NFC, canonical numerics, date normalization,
ordered arrays, explicit nulls, and omissions have the same meaning in the callback
input and in the HMAC envelope.

The durable mutation callback now receives `(locked_context, command_fields)`;
`command_fields` is the frozen canonical snapshot used by the fingerprint. Tests
mutate caller-owned nested dict/list values before execution and during the callback,
and also prove nested snapshot mutation fails. The callback sees the original
canonical fields and an independently constructed equivalent command replays.

### H.3 Replay response contract

Replay now validates stored data against three current trusted conditions before it
can return a snapshot:

1. stored outcome-contract version is one of the implemented versions (`{1}`);
2. stored status remains a 2xx integer; and
3. stored JSON passes the same strict safe-value codec and the incoming command's
   explicit response-field allowlist, when one is defined.

The response allowlist is deliberately outside the request HMAC: it is trusted
server policy, not caller command semantics. A formerly permitted stored field that
is now disallowed fails closed and does not run the mutation callback. Valid replays
still return the original stored status and payload snapshot. No response body has
been added to the request HMAC.

### H.4 Acceptance improvements and newly executed evidence

Before every PostgreSQL invocation, the test command parsed `TEST_DATABASE_URL` and
required an explicit localhost test-only database name different from runtime
`DATABASE_URL`; scheduler and AI were disabled and DeepSeek/Geoapify keys were empty.
No cloud/RDS, AWS, or provider call occurred.

| Newly executed command / boundary | Result |
| --- | --- |
| `pytest tests/test_durable_idempotency_kernel.py -q` | PASS — 30 passed in 1.10s |
| `pytest tests/test_transaction_kernel.py tests/test_idempotency_schema.py tests/test_idempotency_fingerprint.py -q` | PASS — 41 passed in 1.15s |
| Final Legacy CI-equivalent regression | PASS — 592 passed, 0 skipped |
| Final isolated Pydantic fake-compatibility regression | PASS — 119 passed in 6.55s |
| Revised-module compilation and whitespace checks | PASS |

New B.2B acceptance coverage includes supported/unsupported incoming formats,
unknown stored historical format, immutable nested command snapshots, replay under a
stricter stored-response policy, already-expired server-selected retention rejection,
SQL-event proof that the duplicate follower reached `Trip FOR UPDATE`, and concurrent
same-key/different-payload conflict after root-lock waiting. Existing rollback,
rotation, stale revision, and authorization tests remain in the suite.

### H.5 Remaining limits and verdict

The protocol still implements only B.2B's internal existing-Plan command kernel.
No public endpoint parses an idempotency key, no existing writer has been migrated,
and no writer-coverage or application-wide concurrency claim is made. Production
fingerprint evolution requires a separately reviewed canonicalizer plus retained
verification support; B.2B correctly refuses it today. Outcome-contract evolution,
retention cleanup, HMAC operational rotation, Plan-create lifecycle work, preview
bracketing, scheduler/generator/cover compatibility, and B.2C remain later work.

**CONDITIONAL PASS.** The focused corrections make current format/support boundaries,
callback-input ownership, replay response policy, and lock-race evidence explicit
without broadening B.2B scope.

PR-05B.2B implements the internal durable idempotency and replay kernel only.
Existing public mutation routes have NOT been migrated. Full application-wide
concurrency protection and public replay activation have NOT been implemented.
