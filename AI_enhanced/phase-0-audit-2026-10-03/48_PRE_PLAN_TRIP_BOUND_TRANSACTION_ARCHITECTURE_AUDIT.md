# Cadensy — Pre-Plan Trip-bound Durable Transaction Architecture Audit

**Scope:** design audit only, 2026-10-10. This report makes no application,
test, migration, API, or runtime-provider change. It was produced from the
current isolated `codex/pr05b1-schema-foundation` worktree and reports 41–47.
No test or database command was run for this audit.

## 1. Executive Summary

The Preferences / Constraints routes are legitimate pre-Plan writers, but the
implemented B.2A/B.2B protocol is deliberately an **existing-single-Plan**
protocol. `PlanTransactionKernel` rejects both no Plan and multiple Plans;
`DurableCommandKernel` signs, checks, returns, and persists a Plan revision.
Using it for a Trip with no Plan would therefore require a false Plan aggregate
or an unreviewed weakening of the contract. Neither is acceptable.

The smallest safe extension is **Option B: a dedicated `TripTransactionKernel`
and a sibling Trip-bound durable collaborator**, sharing only reviewed lower
level primitives (fresh owned Session, account-first preliminary scope,
credential proof, fresh credential/membership checks, database-error mapping,
canonical payload/HMAC helpers, and trusted outcome registry). The existing
`PlanTransactionKernel` and Plan durable kernel must retain their present
existing-Plan contract.

The new aggregate must have a monotonic `Trip.planning_facts_revision`, not a
constant zero and not `Plan.revision`. A Trip-bound command must lock the Trip,
then credential, actor membership, and any deterministic membership/child rows;
it checks the expected Trip facts revision, atomically changes the owned
Preference/Constraint rows, increments the Trip facts revision exactly once for
a real change, and records a privacy-safe completed outcome. A genuine no-op
records a safe outcome without incrementing the revision.

The existing ledger is *key-unique by Trip and actor*, but its current durable
code and response shape are Plan-dependent. To preserve the frozen key scope
and avoid two tables silently accepting the same Trip/actor/key, the preferred
schema direction is a narrow extension of the existing ledger with an explicit
aggregate scope and a mutually exclusive Trip-facts result revision. This needs
a new reviewed migration and a careful compatibility branch in a new Trip
durable collaborator; it must not retrofit the Plan durable replay path.

Current first-Plan creation is not yet safe to activate with this protocol.
Normal `create_trip()` eagerly creates a blank Plan, but the schema permits no
Plan and duplicate Plans, while the generator can independently create a Plan,
holds its request Session across place/provider/planner work, and has no root
lock or revision snapshot. A later B.2D Prepare / Validate / Commit integration
is required to make generation observe and commit against a facts-revision
snapshot. That dependency is intentionally outside this audit.

**Historical audit verdict: DESIGN CONDITIONAL.** This audit established the
architecture and exposed the decisions that were subsequently frozen in
[`49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md`](./49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md).
Report 49 is now the authoritative implementation contract; this report remains
the source-evidence and option-analysis record.

## 2. Confirmed Source Facts

| Fact | Evidence | Consequence |
| --- | --- | --- |
| `Trip` has no revision/version column. | `backend/app/db/models.py:111-134` | There is no concurrency token suitable for pre-Plan requirements today. |
| `Plan.revision` is a non-negative `BIGINT`, default 0; only `Plan` has it. | `backend/app/db/models.py:239-262`; B.1 migration `backend/alembic/versions/6f77e775d201_pr05b1_dormant_schema_foundation.py:24-34` | `X-Expected-Plan-Revision` has no meaning when no authoritative Plan exists. |
| The model has neither `UNIQUE(plan.trip_id)` nor a one-Plan check. | `backend/app/db/models.py:239-262`; migration comment at `6f77e775d201...py:27-29` | Absence/duplication is representable and must be handled, not hidden by `.first()`. |
| The completed-outcome ledger is unique on `(trip_id, actor_membership_id, idempotency_key)`, not operation/resource. | `backend/app/db/models.py:265-301`; migration `6f77e775d201...py:36-75` | The report-43 key-reuse policy is Trip-and-actor-wide. A new ledger table would weaken it unless coordinated. |
| Ledger columns carry operation/resource and HMAC versions, but only `resulting_plan_revision`. | `models.py:289-301`; migration `...py:42-59` | Table scope is Trip-keyed, whereas current result semantics are Plan-specific. |
| The current durable command input requires `expected_plan_revision`; its class calls itself an existing-Plan command. | `backend/app/domain/transactions/durable.py:182-219`, `241-247` | It cannot be reused unchanged for these four writers. |
| Existing reports explicitly retain missing-Plan creation/replay and generator lifecycle as later work. | `46_PR05B2B_DURABLE_IDEMPOTENCY_IMPLEMENTATION.md:142-143,196-200`; `47_PR05B2C_REAL_WRITER_MIGRATION.md:131-136` | This audit does not reinterpret a deferred boundary as implemented capability. |

The report also relies on frozen design decisions rather than replacing them:
report 41 selected an aggregate-wide Plan revision and Trip/actor/key uniqueness
(`41_PR05B_REVISION_IDEMPOTENCY_DESIGN.md:255-270,466-490`); report 43 finalized
the protected transaction and key policy; reports 44–47 record the subsequent,
still default-off implementation evidence. Where this report proposes a new
Trip aggregate, it is explicitly a proposal, not a claim that those reports
already implement it.

## 3. Root Cause of the Architectural Blocker

The blocker is structural, not a missing route adapter:

1. `PlanTransactionKernel._acquire_locked_plan()` locks Trip, reauthorizes,
   then locks every Plan for the Trip and fails with `MissingPlan` for zero or
   `DuplicatePlans` for more than one (`backend/app/domain/transactions/kernel.py:249-310`).
2. B.2B validates `context.plan.revision` against `expected_plan_revision`,
   includes `plan_id` and that revision in its HMAC envelope, and persists/returns
   `resulting_plan_revision` (`backend/app/domain/transactions/durable.py:301-401,
   404-486`).
3. In contrast, `save_mine`, all constraint mutations, `_mark_existing_plan_for_refresh`,
   and `scan_conflicts` intentionally accept no Plan: both latter helpers return
   without a Plan (`backend/app/domain/preferences/service.py:157-184,354-435,442-454`).

Creating an empty Plan only to satisfy B.2A would change Plan lifecycle, expose
an artificial Plan revision to clients, and conceal rather than solve duplicate
creation. Reusing revision `0` indefinitely would accept concurrent stale
pre-Plan writes. Passing request-session ORM objects into a fresh transaction
would violate the B.2A Session ownership boundary.

There is an important lifecycle nuance: the normal `create_trip()` path already
adds a blank `Plan` alongside the organizer membership
(`backend/app/domain/trips/service.py:104-137`). This does not eliminate the
blocker: the schema permits legacy/no-Plan state; `generate_plan()` has a
no-Plan creation fallback; and the Preferences service's own defined behavior
does not require a Plan. Correctness cannot rely on an incidental current
creation path.

## 4. Existing B.2A/B.2B Reusable Components

### Implemented guarantees

The following are implemented in the current worktree and may be *reused*, not
redefined:

- **Fresh owned Session and one outer transaction.** `_run_locked_command()`
  creates its own Session, enters `with session.begin()`, invalidates the
  context, and closes the Session on every path
  (`backend/app/domain/transactions/kernel.py:209-247`). A request Session is
  not passed into it.
- **Canonical root/security sequence.** The Plan kernel takes `Trip FOR UPDATE`
  first, then locks current account/guest credential and membership, optionally
  locks all memberships, then locks Plan roots
  (`kernel.py:249-301,312-404`). The account-first preliminary authentication
  boundary is preserved by `main.current_membership()`
  (`backend/app/api/main.py:139-175`).
- **Fresh authorization before replay.** The durable ledger lookup occurs only
  after the kernel has reauthorized and acquired roots
  (`durable.py:270-315`). Thus a revoked credential or removed member does not
  learn a stored result.
- **Immutable signed command input.** `IdempotentCommand` deep-normalizes and
  freezes `command_fields`; `canonical_payload_snapshot()` performs the same
  normalized recursive representation used for HMAC semantics
  (`durable.py:182-219`; `backend/app/domain/idempotency.py:35-115,118-170`).
- **Key rotation and outcome policy.** Replay verifies the stored HMAC key
  version and fingerprint format, then validates both the historical and
  current trusted outcome contracts (`durable.py:404-456`).
- **False-no-op containment.** Every callback has a savepoint; a declared no-op
  rolls it back, while an ORM/flush/PostgreSQL completion-tag guard rejects
  staged or flushed mutations (`durable.py:333-354,583-637`).
- **Atomic state/revision/ledger behavior.** A changed command increments the
  Plan exactly once and inserts the ledger in the same outer transaction
  (`durable.py:357-402`). Report 46 records injected-failure and independent
  PostgreSQL evidence for this existing-Plan path.
- **Typed database failure classification and bounded lock-timeout option.**
  `kernel.py:228-233,407-410` and tests including
  `backend/tests/test_transaction_kernel.py:764-799` establish the current
  behavior. The duplicate-command tests observe the follower's Trip lock query
  before release (`backend/tests/test_durable_idempotency_kernel.py:462-525`).

### Inherently Plan-dependent components

`LockedPlanContext`, the Plan root acquisition, `IdempotentCommand`,
`DurableCommandResult.plan_revision`, Plan HMAC envelope, Plan revision overflow
check, and replay requirement for `resulting_plan_revision` are all explicitly
Plan-bound (`kernel.py:289-310`; `durable.py:182-247,321-324,429-486`). They
must not be made nullable or dual-purpose merely to avoid a new explicit type.

### Frozen requirements versus proposals

The frozen reports require fresh authorization, single-owner transactions,
versioned HMACs, trusted outcome policy, and full writer coverage before
activation. This report's Trip facts revision, `TripTransactionKernel`,
aggregate-scope ledger extension, and generation hand-off are **proposed** new
requirements. No current source implements them.

## 5. Pre-Plan Lifecycle and Plan Creation Findings

### Current Preferences / Constraints behavior

| Legacy operation | No-Plan / has-Plan behavior and writes | Authorization, response, and no-op |
| --- | --- | --- |
| `PUT /api/trips/{trip_id}/preferences/me` | Creates/updates one `Preference` keyed by membership; sets `submitted_at`, changes membership status to `preferences_submitted`, flushes, and flags `Plan.needs_refresh` only if an existing Plan has an item. | Scoped current member, trip scope check, 422 for dates, commits at route; returns `read_mine()` DTO. Source: `preferences/service.py:157-184,426-436`; `main.py:2135-2150`. Repeating the same payload still updates submission time/status today; protected no-op equivalence requires an explicit product-compatible decision. |
| `POST /api/trips/{trip_id}/constraints` | Inserts `MemberConstraint`, then `MemberConstraintPrivate` holding `original_text`/visibility; with no Plan, conflict list is `[]`; with a Plan it scans only this owner’s required constraint and may mark refresh. | Scoped current member; validates kind/params, 422 on validation, commits and returns only `{id, conflicts}`. Source: `preferences/service.py:354-387,442-519`; `main.py:2153-2169`. |
| `PATCH /api/constraints/{constraint_id}` | Owner-only read; only provided `params` / `importance` are changed; scans current plan if any; may flag refresh. | `_owned()` returns the same `NotYours` result for missing and foreign IDs, then route maps it to 404. Empty patch is currently possible and flushes; it needs a defined protected no-op classification. Source: `preferences/service.py:219-224,390-405`; `main.py:2172-2188`. |
| `DELETE /api/constraints/{constraint_id}` | Owner-only; deletes private row first, flushes, then deletes constraint and may flag refresh. It does not auto-approve dependent proposals. | Same missing/foreign anti-enumeration treatment; returns `{deleted: true}`. Source: `preferences/service.py:408-436`; `main.py:2191-2202`. |

`MemberConstraintPrivate` intentionally keeps raw original wording outside the
machine-checkable constraint table, and comments say only owner-read/AI
translation code should load it (`backend/app/db/models.py:204-233`).
`scan_conflicts()` returns safe constraint labels and Plan-item facts, not private
wording (`preferences/service.py:471-519`), but a durable outcome policy must
still allow only the existing public response shape.

### Current Plan creation and generator lifecycle

- The regular Trip creation route calls `create_trip()` and commits once at the
  HTTP boundary (`trips/service.py:104-137`; `main.py:1268-1276`); it creates a
  blank Plan before any generator call.
- `POST /api/trips/{trip_id}/plans/generate` calls the synchronous generator in
  the request Session and commits afterward (`main.py:1392-1422`).
- `generate_plan()` reads Trip, organizer state, preferences and constraints,
  normalizes destination, obtains/creates a Plan, then calls the place service
  in the same Session (`generator.py:160-205`). The place service may perform
  provider/cache work; planner work follows in that same request path.
- `_plan_for_trip()` does an unlocked select ordered by creation time and inserts
  a Plan when absent (`generator.py:316-324`). `_trip_has_items()` is an unlocked
  read (`generator.py:307-313`). There is no root lock, no snapshot revision,
  and no duplicate-Plan database invariant.
- Persistence flushes Plan status/total and iteratively adds `PlanItem` and
  `PlanChange` rows (`generator.py:241-282,1712-1757`). It is therefore neither
  a short Plan-create transaction nor a safe place to retain a Trip lock over
  remote/provider/model work.

## 6. Architecture Options and Trade-off Matrix

| Option | Benefits | Material risk / cost | Decision |
| --- | --- | --- | --- |
| **A. Add a Trip mode to `PlanTransactionKernel`** | Fewest apparent files; shares exact root code. | Makes a context named and typed for Plan optionally lack a Plan, forces dual revision/result/HMAC semantics into an accepted Plan kernel, and raises regression risk for every migrated B.2C writer. | Reject. The line between current `MissingPlan` and protected Plan commands is useful and intentional. |
| **B. New `TripTransactionKernel` plus Trip durable collaborator** | Explicit aggregate type; preserves Plan kernel API/invariants; reuses credential/session/error/canonicalization primitives; can be introduced behind separate gates. | Requires a narrow shared internal auth/root helper or carefully duplicated reviewed logic, a migration, contracts, and dedicated tests. | **Recommend.** It is the smallest extension that does not weaken existing Plan contracts. |
| **C. New generic aggregate transaction abstraction** | Could eventually de-duplicate root plumbing. | Rewrites two accepted protocols before their behavior is stable; type/lock/revision distinctions become conditional; broad regression surface with no current third aggregate justification. | Defer. Do not refactor for abstraction alone. |

Option B must be implemented as composition, not inheritance that exposes a
generic Session/commit API. It may share a private credential/membership locker
after a focused review, but it should have distinct `LockedTripContext`,
`TripIdempotentCommand`, and `TripDurableCommandResult` types.

## 7. Recommended Minimal Design

### Proposed aggregate and command protocol

1. Add a non-null, checked, monotonic `Trip.planning_facts_revision BIGINT`
   initialized to zero. It represents mutable inputs used to prepare or refresh
   a plan: Preferences, member-owned Constraints, their private companion rows,
   and the existing `needs_refresh` consequence. It is distinct from
   `Plan.revision`, which guards active Plan/decision aggregate mutations.
2. `TripTransactionKernel` opens its own short Session/transaction and locks
   `Trip FOR UPDATE`, freshly locks/revalidates the bearer credential and actor
   membership, then supplies a transaction-scoped `LockedTripContext`. It never
   requires or creates a Plan.
3. A sibling durable collaborator looks up the Trip/actor/key ledger *after*
   fresh authorization but before stale revision rejection. It fingerprints the
   immutable command snapshot using a Trip-bound envelope, executes the callback
   in the same savepoint/no-op containment protocol, and then commits the domain
   effect, one facts-revision increment (if changed), and safe ledger outcome
   together.
4. Child access remains scoped: preference by locked actor membership;
   constraint by both `constraint.id` and locked actor membership, which itself
   belongs to the locked Trip. No cross-Trip row is locked merely to discover it
   is foreign. For a create, use the stable locked actor-owned constraint
   collection as the HMAC resource; generate the child Constraint ID only during
   first mutation. Report 49, section 8, freezes that corrected contract.
5. If exactly one authoritative Plan exists, requirements writes still run as
   **Trip-facts commands**, but a real decision/planning-fact change advances
   both `Trip.planning_facts_revision` **and** `Plan.revision` in the same outer
   transaction. This is required by reports 41/43: a constraint/preference
   fact can change what a captured Plan preview or decision classification means.
   The operation locks that Plan before its ledger record and refresh read/update.
   It does not substitute one revision for the other. The generator transition
   protocol below binds the two aggregates further.

This deliberately serializes requirements changes across a Trip. It is
conservative but matches the generator’s trip-wide reads of preferences,
constraints, availability, membership count, dates and budget
(`generator.py:366-440`). Fine-grained concurrent preference merges are not
safe until the semantic read/write set is redesigned and proven.

## 8. Revision and Ledger Contract

### Proposed revision contract

| Situation | Required token / result |
| --- | --- |
| Trip has no Plan; Preference/Constraint change | Client supplies an explicit protected Trip-planning revision token, compared to `Trip.planning_facts_revision`. A real change increments it once. |
| Trip has exactly one authoritative Plan; same requirements operation | The Trip-planning token protects the requirements command. A real change increments both the Trip facts revision and locked `Plan.revision` atomically, invalidating captured Plan previews/decisions. `Plan.revision` is not substituted for the Trip token because it cannot represent no-Plan history. |
| Existing Plan-bound B.2C writer | Unchanged `X-Expected-Plan-Revision` and `Plan.revision` protocol. |
| Identical retry, including after Plan creation or constraint deletion | Reauthorize, lock the Trip, find the Trip/actor/key record, verify the original Trip-bound HMAC and replay safe stored response without executing callback or incrementing either revision. |

The exact public header name and legacy feature-off compatibility are **not
approved by this audit**. It must be an explicit new Trip-planning header rather
than reusing `X-Expected-Plan-Revision` before a Plan exists. This report does
not change the current API.

### Proposed ledger evolution

The present ledger is already Trip/actor/key unique but its fields/replay code
assume a Plan result. A new additive migration should, subject to review:

- add `aggregate_scope` with a constrained value such as `plan` or
  `trip_planning_facts`, backfilling/defaulting existing rows to `plan`;
- add nullable `resulting_trip_planning_facts_revision`;
- enforce at database level that a `plan` record has only the Plan result and a
  Trip-facts record has only the Trip-facts result (including neither being
  negative). A Trip-facts command against an existing Plan still increments the
  Plan atomically, but returns/stores only its Trip-facts result; this preserves
  the mutually exclusive durable result shape and the legacy DTO need not expose
  a new Plan revision field;
- retain existing `operation_code`, resource, HMAC, key-version, and outcome
  fields and the **same three-column unique key**.

The Trip HMAC envelope must contain fingerprint format version, operation code,
locked `trip_id`, locked actor membership ID, aggregate scope, resource kind/ID,
expected Trip facts revision, and the immutable canonical command. It must not
contain a `plan_id`. HMAC key version is verified from the stored record as now;
it is not a fingerprint format version. Private raw values may take part in the
keyed HMAC canonical input but are never copied into `outcome_json`.

The existing Plan durable code should retain its exact envelope/replay result.
The Trip collaborator should reject a ledger record of a different aggregate
scope as key reuse, preserving the report-43 Trip/actor/key semantics. A
separate ledger table is not recommended unless it is backed by a global
Trip/actor/key reservation mechanism; otherwise the same key could succeed once
in each table.

### Alternatives considered

- **Dedicated Trip revision:** selected. It captures exactly the planning facts
  used before or during generation, supports stale rejection, and survives Plan
  creation.
- **Shared planning-facts revision as an untyped timestamp/updated_at:** reject.
  Timestamps lack a strict expected-token and overflow/ordering contract.
- **Constant no-Plan revision (zero):** reject. Two writers could both validate
  zero and silently lose updates.
- **Use `Plan.revision` whenever a blank Plan happens to exist:** reject. It
  makes requests depend on incidental lifecycle state and cannot replay a
  pre-Plan command after a Plan appears.

All revision increments must reject `BIGINT` overflow before mutation and use a
non-negative database check. The exact max constant and typed error should match
the existing Plan convention, but no code has been created here.

## 9. Lock Ordering and Concurrency Model

### Proposed order

```text
Trip FOR UPDATE
  -> AuthSession or GuestSession FOR UPDATE
  -> actor TripMembership FOR UPDATE
  -> any operation-required TripMembership rows, sorted by id
  -> all candidate Plans FOR UPDATE, ordered by id (if any)
  -> Trip/actor/idempotency-key ledger FOR UPDATE (if it exists)
  -> owner-scoped Preference or Constraint (+ private companion), stable id order
  -> PlanItem rows only when conflict scan needs them, stable id order
  -> callback savepoint
  -> Trip.planning_facts_revision + completed ledger -> COMMIT
```

The root Trip lock is the compatibility point with all current protected
Plan-bound writers, which already start there (`kernel.py:258-301`). No
operation may take a child, Plan, credential, or membership lock before Trip.
When a Plan exists, taking it before the ledger preserves the current frozen
`Trip -> credential -> membership -> Plan -> ledger` sequence. Zero Plans is a
valid Trip-facts mode; more than one Plan is a typed `DuplicatePlans` failure
before ledger lookup, child access, revision changes, or callback execution.
If all trip memberships are required for a future operation, they must be
selected ordered by ID as the Plan kernel does (`kernel.py:276-287`).

Credential-only legacy logout/revocation remains a known cross-order case:
the B.2A evidence records a credential-row holder without a Trip lock and proves
the kernel waits/revalidates; the Trip protocol must retain that same behavior,
not assume every writer is Trip-first (report 45, section H.3).

### Plan creation race model (proposed, B.2D-owned)

1. **Prepare:** a short read Session reads only DTO facts and captures
   `planning_facts_revision`; it makes no root lock claim and carries no ORM
   objects to provider/model code.
2. **Plan/model work:** cache/provider/model calls run outside root locks and
   transactions holding durable domain mutations.
3. **Validate/commit:** a new short owned Session locks Trip first, freshly
   authorizes organizer, locks/re-reads any existing Plans, compares captured
   facts revision, and refuses/re-prepares on mismatch. Only then can it create
   or use the authoritative Plan and persist the validated draft.

Consequences for the requested scenarios:

- A committed requirements write before Prepare is observed by its snapshot.
- A requirements write during provider/model work advances the facts revision;
  Commit discards the stale draft rather than persisting it.
- A requirements write and Plan creation serialize at the Trip root. The loser
  rereads; it never writes from an old snapshot.
- A post-Plan constraint write still sets `needs_refresh` in the Trip command;
  Plan regeneration is not silently performed.
- Two generators need a reviewed one-authoritative-Plan policy. The present
  unlocked `_plan_for_trip()` and lack of unique `plan.trip_id` are insufficient.

Generation snapshot freshness and Plan creation atomicity are therefore an
explicit B.2D dependency, not a claim of pre-Plan writer completion.

## 10. Privacy and Authorization Model

- The preliminary Account-first resolution must remain exactly as in
  `current_membership()` (`main.py:139-175`); token spelling must never choose
  account versus guest identity.
- Inside the fresh Trip transaction, credential session and membership must be
  locked/revalidated before ledger lookup, mirroring `kernel.py:312-404`.
  Removed membership/revoked or expired credential rejects without replay
  disclosure.
- There is a **source conflict requiring a decision**: legacy guest authentication
  accepts only `membership.status == "joined"` (`auth.py:238-259`), while
  `save_mine()` converts a member to `preferences_submitted`
  (`preferences/service.py:157-184`). The existing Plan kernel likewise accepts
  guests only in `joined` (`kernel.py:366-404`). A Trip implementation must not
  silently expand guest eligibility. Product/auth owners must first decide
  whether guests are expected to submit/modify preferences after submission and
  then make legacy and protected paths consistent in a separately reviewed
  security change.
- Constraint update/delete must preserve `_owned()`'s same-result behavior for
  absent versus foreign constraints (`preferences/service.py:219-224`). The
  protected path may use a plan-scoped/actor-scoped predicate, but cannot return
  a resource-existence oracle.
- `MemberConstraintPrivate.original_text`, private preference budgets, bearer
  material, membership identities of other people, and raw provider payloads
  are forbidden in completed outcomes. The HMAC uses a keyed digest only; safe
  stored outcomes must be explicit server-owned DTOs, with historical and
  current policy validation analogous to `durable.py:437-456`.
- Existing response conflicts reveal Plan item facts and safe generic text
  (`preferences/service.py:471-519`), not raw constraint text. The new outcome
  registry must review that existing DTO field-by-field rather than treating
  arbitrary service dictionaries as replay-safe.

## 11. Schema Migration Assessment

An additive migration is genuinely required: neither a Trip revision nor a
Trip-result ledger representation exists. It must be created only after the
frozen decisions above, and must not rewrite the unmerged B.1 migration history
once it has been shared/applied.

Intended forward-only schema effects to review:

1. `trip.planning_facts_revision BIGINT NOT NULL DEFAULT 0`, with a
   non-negative check and explicit ORM server/default parity.
2. Ledger aggregate discriminator/result fields and a check constraint as
   described in section 8, retaining the current unique index and expiry index.
3. No `UNIQUE(plan.trip_id)` in this phase. Reports 41–43 correctly require a
   duplicate-data preflight before introducing it; the generator/Plan creation
   problem cannot be solved by an unreviewed uniqueness migration.

The migration must be tested only on a disposable localhost PostgreSQL database,
with runtime and test URLs distinct. No RDS/AWS access is needed or authorized.

## 12. Generator / B.2D Dependency Analysis

Trip-bound requirement commands can be designed and tested without provider
calls. They cannot make a generated Plan fresh by themselves. The current
generator's same Session spans facts reads, place loading, planner/model calls,
and persistence (`generator.py:160-282`). Holding the Trip root lock around it
would serialize remote work and violate the frozen lifecycle conclusions in
report 42 (section 4); not holding a version token admits stale generated input.

Therefore B.2D must own the later Prepare / Validate / Commit migration, with:

- detached input/candidate DTOs and no request Session passed to provider work;
- a captured Trip facts revision and, where an authoritative Plan exists, an
  explicit Plan lifecycle/revision snapshot;
- a short Trip-first Commit transaction that rejects stale facts;
- an approved duplicate-Plan policy/preflight; and
- separate durable semantics if generation itself needs a retryable external
  command contract.

This audit neither migrates the generator nor claims that its present retries,
blocked result persistence, or external calls are durable/idempotent.

## 13. Proposed Implementation Phases

1. **Protocol freeze (no code):** approve guest membership-status policy,
   Trip-planning request/response version contract, aggregate-scope ledger
   evolution, no-op equivalence per operation, constraint-create resource ID
   generation, and retention/cleanup ownership.
2. **Schema-only PR:** migration/model tests for Trip revision and ledger scope
   representation; no public route activation and no business writer migration.
3. **Trip kernel PR:** isolated owned-session/root/auth/context implementation
   and PostgreSQL locking/revocation/Session-lifetime tests. No HTTP routing.
4. **Trip durable PR:** canonical command, versioned HMAC, replay, current and
   historical outcome validation, savepoint false-no-op guard, overflow and
   atomic rollback tests. Preserve all Plan durable tests unchanged.
5. **Vertical writer PRs:** migrate Preference save, then constraint
   create/update/delete behind default-off, non-production gates. Build a
   server-owned safe outcome registry before each route is enabled.
6. **B.2D generator PR:** separately implement Prepare / Validate / Commit and
   duplicate-Plan resolution. It is a dependency for claiming safe first Plan
   generation, not a prerequisite for schema/kernel unit work.
7. **Activation PR:** only after writer coverage, compatibility fixtures,
   operational key/retention plan, and regression evidence. Production remains
   fail-closed until separately authorized.

## 14. PostgreSQL Validation Plan

Every concurrency case uses two independent `Session`/connection factories, a
deterministic Event/queue barrier, and PostgreSQL backend PIDs. While writer A
holds `Trip FOR UPDATE`, query `pg_blocking_pids(waiter_pid)` from an independent
observer and assert it contains A's PID; do not use sleeps. After release, join
both workers and assert durable state with a third Session.

| Test | Required invariant |
| --- | --- |
| Pre-Plan preference create/update | One committed Preference, membership status effect preserved, Trip facts revision advances exactly once for a semantic change, safe ledger row/outcome only. |
| Pre-Plan constraint create/update/delete | Owner constraint/private pairing is atomic; no Plan is created; create/delete replay safely; deleted child does not prevent ledger replay. |
| Existing-Plan requirements change | Existing safe conflict response remains compatible; `needs_refresh` semantics remain exactly as legacy; a real fact change advances both Trip facts and Plan revision once, invalidating a captured Plan revision. |
| Same-member concurrent preference writes | Second reaches/block on Trip root; only one expected revision succeeds; stale command creates no effect or ledger completion. |
| Cross-member concurrent preference writes | Conservative Trip-wide serializability: one succeeds, stale expected revision rejects; no lost aggregate planning input. |
| Same constraint / cross-Trip constraint | Owner predicate prevents cross-Trip/foreign child lock and preserves 404 anti-enumeration behavior; no revision or ledger is created on rejection. |
| Account/guest revocation/removal | Credential-only logout/revocation waits safely then causes rejection; no replay disclosure or callback. Include the final approved `joined`/`preferences_submitted` guest policy. |
| Same key replay / different payload | Identical key replays after Plan creation; changed payload or aggregate scope conflicts; HMAC-key rotation works for retained rows. |
| No-op and malicious false no-op | Genuine defined no-op stores exactly one safe result and no facts increment; dirty, flush, insert/delete, commented DML, CTE DML, and failed savepoint cannot commit unversioned domain changes. |
| Failure injection | Effect, private row, refresh flag, Trip revision, and ledger all roll back together for errors after callback, after revision, after ledger insert, and commit failure. |
| Generation snapshot race (B.2D) | Prepare at revision N; requirements change to N+1 during model work; Commit rejects N draft and writes neither Plan/PlanItems/PlanChange nor a false success. |
| Plan creation versus requirement write (B.2D) | Both serialize at Trip; a committed generated Plan reflects one facts revision, never a mixed snapshot; duplicate generator attempts do not produce arbitrary multiple authoritative Plans. |
| Regression | All accepted Manual Item, Booking, Change Proposal, Vote, Proposal Decision, Notice Objection, and Round Settlement protected tests retain their existing Plan revision/ledger behavior. |

## 15. Historical Risks, Open Questions, and Required Decisions

Items 1–4 and 7 are resolved by the Phase-0 freeze in report 49: sections 5–12
freeze Guest active statuses, HTTP/header discovery, additive ledger semantics,
no-op handling, and trusted safe outcomes. Items 5–6 remain deferred B.2D
generator/duplicate-Plan work. Retention duration, cleanup ownership and
post-deletion key reuse are deferred operational decisions in report 49,
section 17.

## 16. Final Recommendation and Implementation Readiness Verdict

Adopt a dedicated, explicitly typed Trip-bound kernel/durable collaborator and
a Trip planning-facts revision. Retain the existing Plan kernel unchanged. Use
the existing ledger's Trip/actor/key uniqueness through a reviewed
aggregate-scope extension, rather than creating an uncoordinated second ledger.
Keep protected routes default-off and non-production until their vertical-slice
tests and the later B.2D generation hand-off are complete.

**Historical final verdict: DESIGN CONDITIONAL.** The Phase-0 protocol was later
frozen by report 49; Phase 1 schema implementation is still not authorized.
Preferences/Constraints writer migration and public activation remain outside this
audit, while first-Plan generation safety remains deferred to B.2D.

---

## 17. Final Design Corrections — Reviewed Resolution Pass

This section supersedes any inconsistent earlier proposal in this report. It
does not authorize code changes. The correction is based on report 43's rule
that all mutations changing facts used by Plan preview/classification must
advance the same Plan revision atomically
(`43_PR05B_FINAL_PROTOCOL_DECISIONS.md:360-384,471-478`) and on the current
Plan durable source rather than an assumption that its storage is generic.

### 17.1 Corrected two-revision contract

`Trip.planning_facts_revision` and `Plan.revision` have different purposes:

| Aggregate state at linearization | Real Preference/Constraint fact change | True no-op | Durable replay | Duplicate Plan rows |
| --- | --- | --- | --- | --- |
| No authoritative Plan | Advance Trip facts revision exactly once. | Advance neither. | Advance neither. | Not applicable. |
| Exactly one authoritative Plan | Advance Trip facts revision and Plan revision exactly once each in the same outer transaction. | Advance neither. | Advance neither. | Not applicable. |
| More than one Plan | Fail closed before ledger lookup, business child locks, callback, or revision mutation. | Same. | A completed replay is also refused because an authoritative aggregate cannot be selected. | Typed `DuplicatePlans`; no arbitrary oldest/first Plan. |

This resolves the conflict in the prior report: `Plan.needs_refresh` is not a
reason to exempt the command from Plan revision. The fact that it may be read by
generation or affect a decision is precisely why a captured Plan revision must
be invalidated. The current service only changes it when a Plan has an item
(`backend/app/domain/preferences/service.py:426-436`), but the protected rule
is based on the semantic requirements change, not solely that derived flag.

The expected token for a Trip-facts command remains the new explicit Trip
planning-facts revision. When a Plan exists, the Trip kernel locks it and reads
its current state before changing requirements, but it does **not** invent a
second required browser Plan token for the same operation. This keeps no-Plan
and has-Plan behavior one command family while still making the resulting Plan
revision stale for pre-existing Plan-preview/decision callers. Existing
Plan-bound commands continue to require only their frozen expected Plan revision.

**Corrected lock/rollback sequence:**

```text
fresh Session; BEGIN
  1. Trip FOR UPDATE
  2. AuthSession or GuestSession FOR UPDATE; verify unrevoked/unexpired binding
  3. actor TripMembership FOR UPDATE; verify active status/Trip binding
  4. any required electorate memberships, stable id order
  5. all Plan rows for the Trip FOR UPDATE, stable id order
       zero -> Trip-facts mode; one -> authoritative Plan; >1 -> fail closed
  6. Trip/actor/key MutationIdempotency row FOR UPDATE, if present
       fresh authorization is already complete; replay/mismatch stops here
  7. owner-scoped Preference/Constraint/private row locks and fresh validation
  8. callback SAVEPOINT; make effect and derived needs_refresh change
  9. for a real change, verify both relevant BIGINTs are below max, then
       increment Trip facts revision and, if present, Plan revision once
 10. store completed safe outcome; flush; COMMIT
exception -> savepoint/outer transaction rollback; close Session
```

The Plan-before-ledger order is intentionally compatible with the implemented
Plan kernel (`backend/app/domain/transactions/kernel.py:258-301`) and existing
durable collaborator (`backend/app/domain/transactions/durable.py:303-402`).
Both overflow checks must occur before the corresponding increment; any
overflow, flush error, ledger error, or commit error rolls back preference,
constraint/private row, refresh flag, both revisions, and ledger together.
No retry is performed inside the transaction kernel.

Required new PostgreSQL evidence includes this deterministic invalidation case:
capture `Plan.revision == r` in an independent preview/read Session; execute one
protected valid Constraint change against the same one-Plan Trip; assert the
commit has `planning_facts_revision == f + 1` and `Plan.revision == r + 1`;
attempt an existing Plan-bound command with expected `r` and assert typed stale
rejection with no child effect/ledger completion. The test must use independent
connections and observe the contender's root lock with `pg_blocking_pids()`.

### 17.2 Corrected `constraint.create.v1` identity

The prior suggestion to choose a constraint row ID before signing was wrong: a
newly generated ID could change between retries, making an otherwise identical
request fingerprint differently. The stable resource for the create operation is
the already-authorized actor-owned collection:

```text
operation_code = "constraint.create.v1"
resource.kind  = "member_constraint_collection"
resource.id    = locked actor_membership_id
```

The canonical Trip envelope contains that stable resource identity, Trip ID,
actor membership ID, aggregate scope, expected Trip facts revision, operation
code, fingerprint version, and the immutable command fields: kind, normalized
params, importance, visibility, and original text. This conforms to report 43's
rule that operation/resource/revision and every accepted field are HMAC
components but not unique-index columns
(`43_PR05B_FINAL_PROTOCOL_DECISIONS.md:71-81,114-120,152-167`).

On first execution only, the callback creates the `MemberConstraint`, lets its
normal server ID be assigned, creates the matching `MemberConstraintPrivate`,
and stores a safe outcome containing the created opaque constraint ID and the
approved conflict DTO. The row ID is not an input or HMAC component. On same-key
same-payload retry, lookup precedes mutation and replays the retained original
ID—even if that constraint has later been deleted. Same-key changed payload,
operation, resource, expected revision, or aggregate scope produces the frozen
key-reuse conflict before a second constraint/private row is created. The ledger
stores only the HMAC digest, never the raw canonical command; it consequently
does not store `original_text`, params, or preference/budget fields.

### 17.3 Final cross-aggregate ledger compatibility

#### Exact proposed additive DDL/ORM contract

The following is a proposal for a **new forward-only migration**, not a migration
created in this task:

```sql
ALTER TABLE trip
  ADD COLUMN planning_facts_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE trip
  ADD CONSTRAINT ck_trip_planning_facts_revision_nonnegative
  CHECK (planning_facts_revision >= 0);

ALTER TABLE mutation_idempotency
  ADD COLUMN aggregate_scope VARCHAR(32) NOT NULL DEFAULT 'plan',
  ADD COLUMN resulting_trip_planning_facts_revision BIGINT NULL;

ALTER TABLE mutation_idempotency
  ADD CONSTRAINT ck_mutation_idempotency_scope
  CHECK (aggregate_scope IN ('plan', 'trip_planning_facts')),
  ADD CONSTRAINT ck_mutation_idempotency_result_scope
  CHECK (
    (aggregate_scope = 'plan'
      AND resulting_plan_revision IS NOT NULL
      AND resulting_trip_planning_facts_revision IS NULL)
    OR
    (aggregate_scope = 'trip_planning_facts'
      AND resulting_plan_revision IS NULL
      AND resulting_trip_planning_facts_revision IS NOT NULL)
  ),
  ADD CONSTRAINT ck_mutation_idempotency_trip_result_nonnegative
  CHECK (resulting_trip_planning_facts_revision IS NULL
         OR resulting_trip_planning_facts_revision >= 0);
```

The SQL must be adapted to the project’s Alembic/PostgreSQL conventions and
reviewed with an explicit `NOT VALID`/validation plan if table size warrants it.
The ORM would add `Trip.planning_facts_revision: BigInteger` and the two
`MutationIdempotency` mapped fields, including `aggregate_scope`. Existing
`resulting_plan_revision` already has a model/migration representation
(`backend/app/db/models.py:265-301`; `6f77e775d201...py:36-75`).

Existing B.2B Plan ledger rows are backfilled/defaulted as `aggregate_scope='plan'`
and retain their existing non-null `resulting_plan_revision`; their HMAC bytes,
key version, outcome contract, expiry, unique key, and replay output are
unchanged. A historical row with nullable `resulting_plan_revision` cannot satisfy
the new `plan` check. Source already refuses it as unsafe at replay
(`backend/app/domain/transactions/durable.py:427-430`). Before adding the check,
the migration must inventory such rows: either prove none exist (the intended
completed-row invariant) or quarantine/delete only under separately approved
retention remediation. It must not silently relabel them as Trip facts records.

The mutual-exclusion check deliberately records the **command aggregate’s
result** only. For a Trip-facts command that also invalidates an existing Plan,
the Plan revision increment is part of the atomic effect but not a second ledger
result field. That preserves an unambiguous replay DTO while the database state
still enforces preview invalidation.

#### Minimal Plan durable change required

The source proves a small change is required; it is not correct to claim zero
Plan durable changes. Current Plan replay lookup deliberately selects by exactly
Trip/actor/key without scope (`durable.py:303-315`) and then assumes a Plan
result (`durable.py:404-460`). It must retain the same lookup dimensions to
preserve global key conflict, then explicitly reject a found
`aggregate_scope != 'plan'` as `IdempotencyKeyReused` before Plan HMAC/result
interpretation. It must write `aggregate_scope='plan'` for new Plan records.
It must **not** add scope to the unique index, change existing Plan HMAC input,
or make Plan replay accept a Trip-facts result.

The new Trip durable collaborator mirrors the same lookup, rejects any found
non-Trip scope as key reuse, uses its separate Trip-bound HMAC envelope, and
requires the Trip result field. Both use the existing historical-then-current
trusted outcome-contract checks and stored HMAC key-version verification
(`durable.py:404-456`). The same `expires_at` index/retention policy applies;
expiry means neither command type may replay an expired retained outcome. Cleanup
must not remove a record before its approved retention horizon, and cleanup must
not defeat the frozen same-key safety contract without a separately approved
retention/reuse policy.

### 17.4 Guest active-status policy (superseded by frozen decision)

The source currently has an inconsistency:

| Path | Current status rule | Evidence |
| --- | --- | --- |
| Account request / locked kernel | Every status except `removed` is active. | `auth.py:283-293`; `kernel.py:346-364` |
| Guest request authentication | Only `joined` is active. | `auth.py:238-259` |
| Guest locked Plan kernel | Only `joined` is active. | `kernel.py:366-404` |
| Preferences save | Sets membership to `preferences_submitted`. | `preferences/service.py:157-185` |

**Frozen product policy:** both `joined` and `preferences_submitted` are active
membership statuses for the same Trip;
`removed`, an expired/revoked credential, wrong Trip, wrong credential kind, and
a Guest membership with a non-null account user remain unauthorized. This is
not merely a Trip-kernel choice: it requires the same allowlist in legacy
`membership_for_guest_token`, `PlanTransactionKernel._locked_guest_actor`, and
the future `TripTransactionKernel`, while retaining account-first resolution in
`main.current_membership()` (`main.py:139-175`).

Before implementing it, security regression tests must prove: normal Account
and Guest behavior; valid account token with guest-like prefix remains Account;
`joined` and `preferences_submitted` Guest acceptance only for the bound guest
session/Trip/membership; removed/revoked/expired/wrong-Trip/wrong-membership
rejection; preliminary-to-locked removal/revocation races; and completed-ledger
replay denial after revocation/removal. This policy is an authorization change
and remains a blocker until product/security approval—not an implicit repair in
Preferences migration.

### 17.5 Sensitive outcome and no-op contracts

`read_mine()` is intentionally an owner-only read, but it returns personal date
windows, ideal/maximum budget, visibility, travel style, interests,
`submitted_at`, every owned constraint’s params, private `original_text`, and
visibility (`preferences/service.py:93-137`). It cannot be persisted verbatim in
`mutation_idempotency.outcome_json` under the privacy rules. Its current legacy
route does return that DTO (`main.py:2135-2150`), so a protected implementation
cannot silently claim response compatibility.

| Operation | Legacy response/effect | Proposed durable stored/replay outcome | Decision status |
| --- | --- | --- | --- |
| `preferences.save.v1` | Full owner `read_mine()` including budget, raw original constraint text, visibility and `submitted_at`; updates timestamp and membership status. | Only a new minimal safe acknowledgement, e.g. `submitted` plus Trip facts revision, if product/API explicitly approves feature-on response divergence. Client can perform fresh authorized `GET /preferences/me` for private values. Do not store the full legacy DTO. | **Blocked on API/product authorization.** |
| `constraint.create.v1` | `{id, conflicts}`; private text is written but not returned. | Same ID plus a reviewed conflict DTO limited to fields already safe in `scan_conflicts()`; no original text, params, visibility, membership identity, credentials, or diagnostics. | Design-ready pending field allowlist review. |
| `constraint.update.v1` | `{id, conflicts}`. | Same restricted DTO, replayed snapshot; no rehydration after later Plan/constraint changes. | Design-ready pending field allowlist review. |
| `constraint.delete.v1` | `{deleted: true}`. | Same boolean only; owner/foreign distinction stays non-diagnostic. | Design-ready. |

No-op rules need explicit semantic definitions, not just a callback declaration:

- A save with fields equal to the stored preference is not automatically a
  no-op today because `save_mine()` always sets a fresh `submitted_at`
  (`preferences/service.py:168-183`). The product must choose whether protected
  identical saves preserve that timestamp change (then they are real changes and
  advance applicable revisions) or intentionally introduce a feature-on
  idempotent no-op (then response/timestamp semantics diverge from legacy).
- An empty `ConstraintPatch` currently calls `flush()` and may set
  `Plan.needs_refresh` via `_mark_existing_plan_for_refresh`
  (`preferences/service.py:390-436`). It cannot be called a no-op until policy
  chooses either legacy effect, a protected 422, or a narrowly defined no-op
  when no persisted/derived field changes.
- A no-op never increments either revision but may store one privacy-safe,
  replayable completed outcome. The current savepoint/flush/DML guard is the
  required failure backstop; no callback may commit writes while reporting
  `state_changed=False` (`durable.py:333-354,583-637`).

### 17.6 Updated implementation sequence and PostgreSQL acceptance matrix

| Phase | Authorized only after | Acceptance focus |
| --- | --- | --- |
| 0. Protocol freeze | Completed by report 49: Guest allowlist, protected Preference response, no-op definitions, Trip revision header/DTO, and initial retention policy. | Written contract only; no code. |
| 1. Additive schema | Phase 0 approval and migration review. | Local disposable PostgreSQL upgrade/backfill checks; Plan-row compatibility; no route activation. |
| 2. Trip kernel | Phase 1. | Fresh Session, Trip/credential/membership/Plan lock order; zero/one/duplicate Plan behavior; revocation races. |
| 3. Trip durable collaborator + minimal Plan branch | Phase 2. | Cross-scope same-key conflict, unchanged Plan HMAC/replay, historic/current outcome contracts, key rotation, expiry, overflow, savepoint rollback. |
| 4. Constraint vertical slices | Phase 3 plus response allowlist. | Create stable collection identity; update/delete ownership; no private ledger content; replay after deletion; both revision increments when Plan exists. |
| 5. Preference vertical slice | Phase 3 plus approved protected response/no-op policy. | Owner-private GET separation; submitted status policy; Plan preview stale test; atomic rollback. |
| 6. B.2D generator integration | Separate approval. | Prepare / Validate / Commit, facts-revision snapshot, no provider under root lock, duplicate Plan policy. |
| 7. Activation | All relevant writer coverage and operations review. | Default-off/non-production gate, legacy compatibility, full regressions, operational retention/key readiness. |

| PostgreSQL scenario | Required assertion |
| --- | --- |
| Constraint on one-Plan Trip versus captured preview | Constraint commit increments both revisions once; a Plan command using captured Plan revision gets `StalePlanRevision`; no second effect. |
| No-Plan requirement mutation | Only Trip revision changes; no Plan is created; retry replays safe outcome. |
| Same create key / same payload | One constraint plus one private row and one ledger; second request returns original ID. |
| Same create key / changed payload | One root lock serialization; follower gets controlled key reuse; no second child/private row. |
| Replay after deletion | Reauthorized owner receives original safe create result, no child recreation and no revision increment. |
| Plan scope versus Trip scope key collision | First completed scope wins; second fails key reuse before callback, while the ledger unique index remains unchanged. |
| Empty patch / identical preference | Exact approved semantic decision is asserted, including timestamp, refresh flag, both revisions and replay. |
| False no-op mutation | ORM writes, explicit flush, insert/delete, commented DML and CTE DML rollback; neither revision nor ledger domain effect is incorrectly committed. |
| Guest active-status policy | Approved `joined`/`preferences_submitted` cases work only for bound valid Guest credentials; removed/revoked/expired/wrong scope fail before replay. |
| Generator race (B.2D) | `pg_blocking_pids()` proves Trip-root contention; a facts change during prepare/provider work causes stale draft refusal, not a mixed Plan. |

### 17.7 Final decision table and verdict

| Category | Final state |
| --- | --- |
| Approved architecture principle | Option B: dedicated Trip kernel/durable collaborator; preserve current Plan kernel’s existing-Plan contract. |
| Corrected revision rule | No Plan: Trip facts revision only. One authoritative Plan: atomically increment both Trip facts and Plan revision for a real fact change. No-op/replay: neither. Duplicate Plans: fail closed. |
| Corrected create identity | `constraint.create.v1` uses the locked actor’s constraint collection, not a generated row ID; original created ID is in safe replay outcome. |
| Corrected ledger compatibility | Retain Trip/actor/key uniqueness; add scope plus mutually exclusive result fields; one explicit Plan durable scope-mismatch branch is required; preserve Plan HMAC bytes. |
| Decisions still requiring authorization | Phase 1 schema/migration authorization; operational retention/cleanup/key-reuse ownership; production activation. Guest policy, protected response, no-op semantics, and revision header/DTO are frozen by report 49. |
| Deferred B.2D | Generator Prepare / Validate / Commit, provider-outside-lock handling, first-Plan/duplicate-Plan lifecycle and activation. |

**Historical corrected-audit verdict: DESIGN CONDITIONAL; superseded by report 49's PROTOCOL FROZEN verdict.**

No Trip-bound extension, migration, route, test, activation, commit, or deployment
is authorized by this report. Report 49 freezes the Phase-0 protocol but does not
authorize Phase 1 schema foundation implementation.
