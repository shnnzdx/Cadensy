# Cadensy PR-05B — Independent Design Review Corrections

**Status: CONDITIONAL PASS — design direction is corrected; implementation remains
unauthorized.**

This is a design-only correction to
`41_PR05B_REVISION_IDEMPOTENCY_DESIGN.md`.  It does not replace that report and does
not modify application code, ORM mappings, Alembic, tests, configuration, or any
PR-04 artifact.

## Review basis and scope

The target file was confirmed absent before this report was created.  The source
basis was `main` at `17026adf4b77ced2280629f2b1584fd6892e420e`, equal to
`origin/main` at review time.  The only pre-existing working-tree entry was the
untracked original report `41_PR05B_REVISION_IDEMPOTENCY_DESIGN.md`; it has not been
edited.

This review independently inspected the current source rather than treating the
review comments or report 41 as evidence.  The relevant database semantics below
are also checked against the primary documentation for [SQLAlchemy Sessions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
and [PostgreSQL explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html).

Terms used below:

- **aggregate** means the one authoritative current `Plan`, its items, and the
  trip-scoped decision facts whose change can alter a preview or an apply outcome
  (constraints, membership/electorate facts, rounds, proposals, and booking state).
- **revision** is a proposed, monotonic `Plan.revision`, not a timestamp, ETag, model
  token, or agent-owned capability.
- **prepare** is an unprivileged, externally callable/read-only phase.  **commit** is
  a short local PostgreSQL transaction.  They are deliberately separated.
- A “fresh” read means a newly opened write Session after the aggregate root lock,
  without objects carried over from HTTP authentication or a model worker.

## Corrected conclusions at a glance

| Review area | Independent finding | Corrected decision | Affected phase |
| --- | --- | --- | --- |
| Preview facts | Confirmed: current tool reads, request-side item list, and final classification are not one snapshot. | Runtime tools must consume an immutable application-built snapshot; a final fresh revision check can only validate/discard it, not repair it. | B.2/B.3 |
| Transaction ownership | Confirmed: route authentication already performs ORM reads on the injected Session. | Do authorization read work and the aggregate write unit of work in distinct Sessions; re-authorize inside the latter. | B.2 |
| Incremental rollout | Confirmed: the former B.2-before-B.3 ordering could advertise protection while writers still bypass revision. | Schema and writer coverage may merge dormant; public enforcement activates only after all coverage lands. | B.1–B.4 |
| Generator | Confirmed: the present generator holds one request Session across cache lookup, Geoapify, planner, and persistence. | Prepare / Validate / Commit; no remote call while the Trip/Plan root is locked. | B.2 coverage |
| Locks | Confirmed: current decision and scheduler paths do not acquire aggregate locks or claim work. | Trip then Plan `FOR UPDATE`, then stable child order; scheduler claims Plan roots with `SKIP LOCKED`. | B.2 |
| Idempotency | Confirmed: no current key, ledger, fingerprint, or replay contract exists. | Plan-root-first durable ledger, versioned HMAC fingerprint, bounded retention, and replay-safe result snapshot. | B.1/B.2 |
| Race tests | Confirmed: current paths tests demonstrate sequential idempotence, not independent-connection races. | PostgreSQL-only test fixture with separate connections and one-way events outside contested locks. | B.1–B.4 |

---

## 1. Preview snapshot consistency

### Original finding

Report 41 correctly proposed returning an expected plan revision from preview, but
that alone does not show that the facts read by multiple agent tools, the selected
item, and the final deterministic classification came from one coherent snapshot.
The independent concern is therefore **confirmed and materially stronger**: a
revision attached after the fact cannot make mixed-time tool results consistent.

### Source evidence with exact line references

| Evidence | What the current source proves |
| --- | --- |
| `backend/app/domain/chat/service.py:84-111` | The chat service loads `Trip` and `_trip_items()` into the request Session before invoking the runtime. |
| `backend/app/domain/chat/service.py:158-188` | The runtime gets `LegacyReadTripCapability(..., session_factory=SessionLocal)` rather than the request Session. |
| `backend/app/agents/legacy_runtime.py:104-119` | One runtime execution opens a worker Session and constructs live read tools around it. |
| `backend/app/agents/tools.py:38-139` | The legacy tool surface includes current-plan, trip-facts, classification, option, and replacement reads; each closure reaches the live worker Session. |
| `backend/app/agents/tools.py:469-519` | `get_current_plan()` and `get_trip_facts()` each issue their own ORM reads. |
| `backend/app/domain/chat/service.py:201-226`, `1125-1156`, and `1160-1191` | After the runtime returns, application classification uses the earlier request-side `items` list and `db`, not a freshly captured common snapshot. |
| `backend/app/domain/chat/service.py:1194-1204` | That `items` list is a query result from the original request Session. |
| `backend/app/agents/runtime_contract.py:47-60` and `99-105` | `RuntimeRequest` has no revision/snapshot field, and a suggested change expressly has no fresh-state or transaction authority. |
| `backend/app/api/main.py:300-304`, `854-907` | Chat input/output contains no revision or snapshot metadata. |
| `backend/app/db/session.py:37-43` | Request and worker Sessions come from the same `SessionLocal`, whose configuration explicitly keeps objects unexpired after commit. |

SQLAlchemy's identity map makes this worse than merely “two SELECTs at different
times”: a loaded row can be returned as the same Python object without replacing
already loaded attributes unless the application expires, refreshes, or uses
`populate_existing`.  That behavior and `autobegin` are documented in the
[SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).

### Confirmed issue / rejected concern

- **Confirmed:** currently a first tool call can observe state A, a later tool call
  can observe state B, and the final request-side classification can use state C or
  an identity-map-retained object from state A.  There is no protocol connecting
  those observations.
- **Confirmed:** merely adding `plan_revision` to the final response is insufficient.
  It says nothing about whether all tool facts correspond to that revision.
- **Rejected:** a long-lived `REPEATABLE READ` worker transaction around the model is
  not an acceptable fix.  It would keep a database transaction/connection open
  across provider latency and still conflict with the project’s short-lived
  worker-Session boundary.
- **Rejected:** the model must not receive a revision token that it can turn into
  write authority.  The agent may only read an opaque, application-created snapshot
  projection.

### Corrected architecture

#### 1. Prepare one immutable preview projection

For a request that can produce a proposed change, the application opens a **new,
short-lived snapshot Session**, distinct from both the FastAPI authentication Session
and the runtime worker Session.  In one explicitly bounded read transaction it:

1. authorizes the actor for the trip and reads the authoritative active Plan;
2. captures `plan_id`, `plan_revision`, `captured_at`, a random opaque
   `snapshot_id`, selected-item identity, and every safe fact exposed by every
   enabled read tool;
3. captures the classifier inputs needed for the chosen item: the normalized item
   and peers, plan totals/status, safe constraint projection, trip dates/status,
   and membership count/decision state where it affects classification;
4. serializes those facts to immutable plain DTOs, then closes the Session before
   any model or tool invocation.

The snapshot query itself should be `REPEATABLE READ, READ ONLY` if the database
configuration permits it, so the projection is internally coherent.  Its lifetime
is only the local capture operation; it never spans an agent, Geoapify, or a
provider call.  The system must define an explicit maximum projection size and
return a clarification rather than silently truncate decision-relevant facts.

#### 2. Replace live database tool reads with snapshot-backed reads

Introduce a `PreviewSnapshotReadTripCapability` which exposes the same
framework-neutral `RuntimeReadTool` descriptors, but the handlers read only the
immutable DTO.  It owns no SQLAlchemy `Session`.  Consequently:

- repeated calls such as `get_current_plan("all")` and `get_current_plan("day 2")`
  are filtered views of the same snapshot;
- `classify_change` invoked by a tool uses the snapshot’s deterministic inputs,
  not live database state;
- replacement candidates, if retained in the Legacy tool surface, must be either
  captured as a safe cache-only projection in the snapshot or disabled for this
  preview contract.  A live provider/cache lookup during a tool call is not allowed;
- Pydantic’s present tool restriction to `get_current_plan`
  (`backend/app/agents/pydantic_runtime.py:196-248`) remains compatible, but does
  not weaken the Legacy requirement.

The runtime result remains untrusted syntax.  The application derives its proposed
patch, then re-runs deterministic classification against the *same immutable
snapshot DTO*.  It must not re-use request-side ORM objects.

#### 3. Validate freshness immediately before returning a preview

After the agent has completed, a new short Session reads only the authoritative
Plan revision.  If it differs from the projection’s revision, the application
**discards** the candidate and returns a safe `preview_stale` result (with the new
revision, but no stale verdict).  It must not silently attach the new revision to a
verdict calculated from old facts.  One bounded, explicit re-prepare/re-run is a
product decision; an unbounded automatic model retry is prohibited.

If the revision still equals the captured revision, return:

```json
{
  "preview_snapshot_id": "opaque-server-generated-id",
  "plan_id": "…",
  "expected_plan_revision": 42,
  "expires_at": "…",
  "proposed_change": { "item_id": "…", "patch": { "…": "…" }, "verdict": "…" }
}
```

`expires_at` is a UX/abuse-control bound, not a concurrency guarantee.  At apply,
the server does not trust the verdict, `snapshot_id`, or client history.  It locks
the aggregate, compares `expected_plan_revision`, re-authorizes, and reclassifies
from current locked facts.  A change after the final preview freshness check but
before apply is therefore detected at apply rather than falsely treated as safe.

### Affected PR-05B implementation phase

- **B.1:** add no public preview behavior.  It may establish only dormant revision
  schema/contract primitives after the B.1 gates in section 8 are met.
- **B.2:** build the snapshot DTO/capability and deterministic reclassification
  seam behind a disabled server capability; do not expose it as protected yet.
- **B.3:** make preview and apply public only after every revision-relevant writer
  is covered.  B.3 owns the client contract and stale-preview UX.

### Acceptance test design

1. Capture a snapshot at revision 7.  Commit a booking/constraint change through a
   separate Session before a second runtime tool call.  Both tool calls must return
   the immutable revision-7 facts; final freshness check returns `preview_stale`,
   not a revision-8 verdict derived from revision-7 data.
2. Execute multiple legacy tool calls over one snapshot and assert same snapshot id,
   revision, selected-item facts, and filtered day facts for every response.
3. Preload a stale request Session object, then commit a change through another
   Session.  Snapshot preparation must use a new Session/projection and not the
   preloaded instance.
4. Commit a change after preview freshness succeeds but before apply.  The apply
   command with revision 7 must return `409 PREVIEW_STALE`; no plan change, notice,
   round, proposal, or ledger success is created.
5. Ensure neither adapter receives an ORM Session, raw constraint wording, a
   membership id, or a server write token in tool-visible input/output.

### Open decisions

- Is one explicit re-prepare/re-run allowed after a snapshot becomes stale, or must
  the UI ask the user to retry?  The safe default is ask/retry.
- What bounded safe projection is sufficient for Legacy replacement suggestions?
  Until specified, replacement preview must fail closed rather than reintroduce a
  live cache/provider tool read.
- What TTL and maximum snapshot size balance UX and resource use?  Neither changes
  the mandatory apply-time revision check.

---

## 2. SQLAlchemy transaction ownership and fresh locked reads

### Original finding

The previous design called for an explicit write transaction, but did not fully
account for the fact that dependency-based authentication has already executed ORM
queries on the injected request Session.  The concern is **confirmed**.

### Source evidence with exact line references

| Evidence | What the current source proves |
| --- | --- |
| `backend/app/db/session.py:37-43` | `SessionLocal` is a normal `sessionmaker(..., expire_on_commit=False, future=True)` and `get_session()` yields one Session per dependency invocation.  It does not set `autobegin=False`, `autoflush=False`, or a transaction isolation level. |
| `backend/app/api/main.py:99-135` | `current_membership` runs token/account/membership reads against that same injected Session before endpoint body code. |
| `backend/app/api/main.py:1286-1311`, `1361-1375`, `1396-1438` | Writes receive both `db=Depends(get_session)` and `me=Depends(current_membership)`, then mutate and call `db.commit()` in the endpoint. |
| `backend/app/api/main.py:954-960` | Scoped item lookup returns an ORM `PlanItem` in the request Session. |
| `backend/app/domain/decisions/orchestrator.py:437-536` | Proposal/classification follows relationships and performs constraint/peer reads from the Session it is passed. |
| `backend/app/domain/decisions/orchestrator.py:402-420` | Peer schedule validation is an ordinary non-locking select. |

By SQLAlchemy default, `autobegin=True` and `autoflush=True`; the first authentication
query starts the Session’s transaction.  The same official documentation states that
an outer `Session.begin()` raises when the Session already has a transaction, that
identity-map rows are not automatically repopulated by a later query, and that
`expire_on_commit=False` disables the normal post-commit expiration.  See the
[Session API](https://docs.sqlalchemy.org/en/20/orm/session_api.html) and
[Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).

### Confirmed issue / rejected concern

- **Confirmed:** wrapping the existing endpoint’s `db` with a new outer
  `with db.begin():` after `current_membership` is not a safe universal fix; it can
  fail because `autobegin` has already created a transaction.
- **Confirmed:** retaining the authenticated `PlanItem` object and passing it to a
  new write helper would retain stale identity-map state and cross-Session ownership.
- **Confirmed:** a nested savepoint is not a replacement for a fresh aggregate write
  transaction.  The current `/classify` route invokes mutating `propose_change()`
  within `db.begin_nested()` and rolls it back (`backend/app/api/main.py:1263-1283`);
  that prevents durable effects but does not make its classification a locked,
  current-state preview.
- **Rejected:** FastAPI dependencies make an explicit write unit of work impossible.
  They do not.  The correction is to use the dependency Session for request auth/read
  scope only and open a separately owned Session for the write unit of work.

### Corrected architecture

#### Request scope versus write scope

1. The FastAPI dependency Session resolves only an immutable `ActorScope` value:
   actor membership id, trip id, role/credential type, and request correlation id.
   It must not be passed as an ORM instance to the writer.
2. A write command receives primitive identifiers, expected revision, canonical
   command data, and idempotency-key metadata.
3. The command creates a **new** Session with `with SessionLocal.begin() as write_db:`.
   It never calls `begin()` on the dependency Session and returns only DTO/scalar
   output before its new Session closes.
4. In `write_db`, it re-loads and re-authorizes the actor/membership for the locked
   Trip.  A membership removed after the request dependency ran must not gain a
   successful write merely because an old ORM object was retained.
5. It takes the root locks from section 5, then queries the Plan, target item,
   constraints, membership/electorate, peer items, round/proposal state, and
   idempotency row **after** the lock.  The new Session must contain no previously
   loaded aggregate objects.  Queries used after a helper has already loaded an
   object must explicitly use `populate_existing=True`, or call `refresh`, before
   an authoritative decision.
6. It compares `expected_plan_revision`, performs deterministic validation and
   effects, advances the revision exactly once, records the safe idempotency result,
   and commits as one transaction.

The writer must not invoke a remote model, Geoapify, cache refresh, filesystem work,
or network retry while it owns the root lock.  Any exception before successful
commit rolls back the complete write Session.  A returned response is built from a
detached safe DTO, not from ORM objects whose attributes may be stale or inaccessible
after closure.

#### Why the locked Domain checks are fresh

The proof obligation is procedural, not a comment on `classify_change`:

```
new write Session
  -> authenticate current actor scope
  -> lock Trip FOR UPDATE; lock its one Plan FOR UPDATE
  -> re-read all decision inputs in this Session, after those locks
  -> compare revision; classify/validate; stage effects
  -> bump revision + completed ledger result; COMMIT
```

Every writer capable of changing one of those decision inputs must use the same root
lock.  Thus a competing compliant writer cannot commit a relevant state change
between the locked re-read and commit.  PostgreSQL documents that `SELECT FOR UPDATE`
waits for a concurrent conflicting row lock and then returns the updated row (or no
row), so the waiter reads after the predecessor’s result rather than an old locked
row.  This proof does not apply until section 3’s coverage gate is complete.

### Affected PR-05B implementation phase

- **B.1:** define injectable `SessionFactory`/command boundary interfaces and add
  zero-behavior tests for DTO ownership.  Do not convert public routes yet.
- **B.2:** move every covered command to a new explicit write Session and delete the
  possibility of passing request-Session ORM objects into it.
- **B.3/B.4:** expose and enforce expected revision only after the B.2 writer matrix
  is complete and proven.

### Acceptance test design

1. Force `current_membership`/scoped lookup to load a PlanItem in the request
   Session, mutate it in an independent Session, and verify the command opens a new
   Session and evaluates the updated locked value.
2. Assert a write command does not call outer `begin()` on an already-autobegun
   request Session; it receives primitive scope values only.
3. In two independent Sessions, have writer A lock and change the Plan, then allow
   writer B to acquire the root.  B’s post-lock reads must observe A’s revision and
   state before it decides stale/reclassifies.
4. Inject a flush failure after a proposed domain effect but before ledger completion;
   verify rollback leaves no effect, revision increment, or completed ledger row.

### Open decisions

- Whether the route layer should expose a dedicated write-unit-of-work dependency or
  call an application command factory directly.  Either is acceptable only if it
  opens a new Session after request authentication and owns its commit/rollback.
- Exact endpoint error mapping for a reauthorization failure after the request was
  initially authenticated (recommended: current authorization status, never replay
  an old result to a removed member).

---

## 3. Safe incremental integration and activation gates

### Original finding

The original B.1/B.2/B.3/B.4 proposal permitted a protected direct-preview/apply
contract before complete writer coverage.  The independent concern is **confirmed**:
that order would allow Booking, Generator, constraints, voting, proposal settlement,
or scheduler work to change decision facts without advancing the revision relied on
by the public contract.

### Source evidence with exact line references

| Writer family | Evidence of current bypass path |
| --- | --- |
| Manual plan edits | `backend/app/api/main.py:690-752` checks adjacent items, inserts a `PlanItem`, and commits without aggregate lock/revision. |
| Generator | `backend/app/api/main.py:1229-1260` calls `generate_plan()` and commits; `backend/app/domain/plans/generator.py:160-282` reads/writes Plan state and items without a root lock. |
| Booking | `backend/app/domain/decisions/orchestrator.py:539-576` changes item booking/settledness and logs/notifies without revision. |
| Constraints/preferences | `backend/app/domain/preferences/service.py:157-185`, `354-436` mutate preference/constraint facts and set `plan.needs_refresh`; classifier reads constraints at `orchestrator.py:109-125` and `505-536`. |
| Voting/rounds | `backend/app/api/main.py:1396-1410` casts a vote and may settle in the same request; `orchestrator.py:661-740` writes vote, item, round, log, and notice. |
| Proposals | `backend/app/domain/decisions/orchestrator.py:792-908` creates/decides proposals and can apply item changes. |
| Scheduler | `backend/app/jobs/scheduler.py:25-35` uses its own Session to settle/expire; current scans at `orchestrator.py:743-786` neither claim nor lock work. |
| Organizer terminal actions | `backend/app/domain/decisions/organizer.py:115-145`, `151-230` extend/escalate/resolve decisions and can mutate PlanItem. |

### Confirmed issue / rejected concern

- **Confirmed:** Plan-level revision is a false protection if a writer can change an
  input to a classified operation without incrementing it under the same root lock.
- **Confirmed:** the coverage obligation includes state that changes whether an
  operation is permitted or how it is classified, not just rows visibly rendered as
  a PlanItem.  Constraints, plan status/total, booked state, active round/proposal,
  eligible membership/electorate, and terminal decision status are in scope.
- **Rejected:** partial unique indexes are a sufficient replacement for this
  protocol.  `DecisionRound` and `ChangeProposal` have partial uniqueness
  (`backend/app/db/models.py:358-384`, `402-429`), but that does not serialize the
  preceding cross-item/constraint decision reads or yield a correct replay response.
- **Rejected:** a temporary feature flag by itself permits a correctness claim.  It
  is useful only when the public endpoints and clients cannot activate the behavior
  until coverage is verified across the whole deployed fleet.

### Corrected architecture and safe PR sequence

| Phase | Merge-safe work | Explicitly not allowed yet | Activation/exit condition |
| --- | --- | --- | --- |
| **B.1 — dormant foundation** | Additive schema/migration plan, writer inventory as an executable policy table, versioned fingerprint primitives, and command interfaces.  Revision may exist with default `0`; ledger may exist unused. | No endpoint requires/sends expected revision; no UI says a preview is concurrency-protected; no writer coverage claim. | Duplicate-Plan preflight decision, ledger key/retention decision, and migration review are approved.  A B.1 merge alone is not functional protection. |
| **B.2 — full server writer coverage** | Convert every aggregate-relevant writer to the one command/lock/revision kernel, including scheduler/system callers.  This may be split into narrow PRs, all with public enforcement disabled. | No public compare-and-apply guarantee until the last listed writer, including legacy/background path, is converted. | Coverage matrix is complete, static inventory test finds no direct aggregate mutation bypass, and PostgreSQL race suite passes. |
| **B.3 — preview and command contract** | Build snapshot preview, expected-revision/idempotency request/response formats, compatibility adapters, and client handling behind a server-side disabled capability. | Do not turn it on just because chat/direct `/changes` is converted.  No old client path may bypass the command kernel. | B.2 gate is met; all live application and scheduler versions understand schema; staged capability check confirms no old worker remains. |
| **B.4 — controlled activation and evidence** | Enable enforcement, require key/revision for covered mutations, remove compatibility bypasses only after telemetry/evidence, and complete product/CI regression proof. | Do not call the system “complete” while any recognized mutation route can silently commit without root lock/revision. | Rollout checklist, fleet version barrier, migration head, test evidence, operational retention job, and rollback/forward-fix plan are approved. |

`B.2` may itself be safely merged in sub-PRs only if each one keeps public
revision enforcement disabled.  The correct dependency is therefore **B.1 -> all
B.2 writer coverage -> B.3 public contract -> B.4 activation**, not “B.1 -> direct
apply -> later coverage”.

### Affected PR-05B implementation phase

This correction changes the dependency graph for **all B.1–B.4 phases**.  In
particular, report 41’s former B.2 public goal must move behind B.2 coverage and B.3
gating.

### Acceptance test design

- Maintain a reviewed writer matrix that lists every route, scheduler task, and
  service entry point in the evidence table.  Each row identifies its root resolver,
  lock helper, revision policy, idempotency policy, and test.
- Add a static/architectural test that fails when known aggregate mutations call
  `commit`, mutate `Plan`/`PlanItem`/decision state, or invoke the old services
  outside the approved command kernel.  It is a supplement, not proof by itself.
- For each row, run a PostgreSQL test proving one revision increment for a successful
  logical mutation and no increment for a no-op/read-only/reminder-only operation.
- At activation, exercise an old-client request and old scheduler worker version in a
  staged environment; both must be rejected or remain unable to bypass the kernel.

### Open decisions

- Exact policy for membership changes: because membership count and affected-member
  decisions can change classifications, this review places membership/electorate
  mutations in aggregate scope.  Product must confirm whether a membership change
  should invalidate all outstanding previews immediately (safe default: yes).
- Exact revision policy for reminder-only notices and comments.  They are not
  classifier inputs in the inspected source and should remain out of revision scope
  unless product makes them semantic.
- How a version barrier is established for rolling application/scheduler deployment.
  This is a release design decision; it must not be inferred as complete by code.

---

## 4. Planner transaction boundary: Prepare / Validate / Commit

### Original finding

The original report required no provider call under a write lock but did not fully
separate the existing generator’s cache and remote behavior.  The concern is
**confirmed**.

### Source evidence with exact line references

| Evidence | What the current source proves |
| --- | --- |
| `backend/app/api/main.py:1229-1246` | Generation runs in the request dependency Session and the route commits after it returns. |
| `backend/app/domain/plans/generator.py:160-205` | `generate_plan()` reads trip/preferences/constraints, normalizes `trip.destination`, obtains a Plan, and calls `places_for_planner(db, ...)` using that same Session. |
| `backend/app/domain/places/service.py:72-132` | Cache lookup can call `geoapify.fetch_places()` and then upsert/re-read cache through the same Session. |
| `backend/app/domain/places/service.py:167-195` | Geoapify cache upsert mutates `Place` rows and flushes. |
| `backend/app/domain/places/geoapify.py:79-149` | Geoapify uses synchronous `httpx.Client` requests with a ten-second timeout per client configuration. |
| `backend/app/domain/plans/generator.py:612-658` | Day construction invokes `planner.plan_day(payload)` after candidate/input construction. |
| `backend/app/domain/plans/generator.py:241-282`, `1712-1757` | Generation can mutate Plan status/totals and persist/flushed PlanItems and PlanChange rows. |
| `backend/app/domain/plans/generator.py:316-324` | `_plan_for_trip` uses an unlocked select and creates a Plan if absent. |

### Confirmed issue / rejected concern

- **Confirmed:** present generation can keep its request Session/transactional
  context alive while external cache/provider/planner work occurs.  It does not
  currently use a Plan lock, but adding one around this function unchanged would
  create a long lock around remote work.
- **Confirmed:** `_trip_has_items()` and `_plan_for_trip()` are read-then-write
  checks, not concurrency control.
- **Rejected:** cache refresh is an aggregate plan mutation.  Geoapify `Place` cache
  contents influence candidate selection, but a cache upsert is not itself a
  user-plan decision and must not take the Plan root lock or bump Plan revision.
  It still needs its own uniqueness/transaction handling.

### Corrected architecture

#### Prepare — no aggregate lock, no held database transaction during remote work

1. Resolve request identity to primitive actor scope.
2. In a short read Session, capture a `GenerationPreparation` DTO: current Plan id
   and base revision (if one exists), trip input, organizer eligibility, safe
   planning constraints/preferences/availability, and cache candidate projection.
3. If cache refresh is required, end that read transaction before calling Geoapify.
   Call the provider without an open plan/write Session.  Then use a separate short
   cache transaction to upsert `Place` rows and commit/re-read them.  Concurrent
   cache misses need a conflict-safe uniqueness/re-read policy; they do not justify
   locking a Plan.
4. Invoke the Planner/model with plain candidate/input DTOs only.  Apply its normal
   deadline and syntactic validation outside a database transaction.  Provider/model
   output is a draft, never a persistence authority.

#### Validate — deterministic draft validation without a root lock

Validate draft shape, candidate identity, type/range values, duplicate selections,
and local planner invariants.  This is preliminary: it cannot determine final
authorization, plan emptiness, budget/constraint validity, or schedule validity.

#### Commit — short authoritative transaction

Open a fresh write Session and, in this order:

1. re-authorize organizer membership;
2. lock Trip then its Plan as described in section 5 (or lock Trip and create the
   one Plan safely if legacy data truly has none);
3. compare the current Plan revision with `GenerationPreparation.base_revision`;
4. re-read current trip data, membership/constraints/preferences, plan items, and
   `needs_refresh` state after the lock;
5. revalidate the draft against those locked facts and current `Place` identities;
6. if valid, write PlanItems/PlanChange/Plan totals, advance revision once, store the
   idempotency outcome, and commit.

If the revision or validation facts changed, return a generation-stale/precondition
result and require a new prepare operation.  It must not make a remote call while
holding the lock in an attempt to “fix” the draft.  Retrying the commit-only local
transaction is permitted within the limits in section 6; repeating Prepare may make
additional provider/model calls and is not exactly once.

### Affected PR-05B implementation phase

Generator conversion belongs to **B.2 full writer coverage**.  It is a hard gate for
B.3 public revision enforcement, not a later optional optimization.

### Acceptance test design

1. Instrument a fake Geoapify/planner call to block on an event.  Assert no Trip or
   Plan root lock has been acquired while it is blocked; release it, then assert the
   short Commit phase obtains the lock before persistence.
2. Change a constraint or insert an item through a second Session after Prepare but
   before Commit.  Commit must reject stale/revalidate from fresh locked state; it
   must not persist a draft based on old input.
3. Start two generators for an initially empty legacy trip.  They must not create
   two Plans or two item sets; one wins/commits and the other observes the locked
   state and returns the documented conflict/stale result.
4. Force cache refresh failure: no Plan lock, PlanItem, PlanChange, revision advance,
   or idempotency success must occur.  A successful provider cache upsert alone is
   allowed and is explicitly not a generated plan.

### Open decisions

- Is generation a client-idempotent command in the first supported API surface, or
  only an organizer operation with a server-generated command id?  Either way its
  DB commit needs a durable result key; neither makes provider/model execution
  exactly once.
- What policy applies if cached Place data changes between Prepare and Commit?  The
  conservative default is to validate that selected provider ids still exist and
  otherwise reject/re-prepare, not substitute a new venue invisibly.

---

## 5. Lock ordering, lock strength, scheduler claiming, and recovery

### Original finding

The former design named a Plan lock but did not make a complete decision on
`FOR UPDATE` versus `FOR NO KEY UPDATE`, absence/creation, or scheduler work
claiming.  The concern is **confirmed**.

### Source evidence with exact line references

| Evidence | What the current source proves |
| --- | --- |
| `backend/app/db/models.py:233-248` | `Plan` has no revision and no `trip_id` uniqueness declaration. |
| `backend/app/domain/trips/service.py:104-137` | New trip creation creates a Plan in the caller’s ordinary transaction. |
| `backend/app/domain/plans/generator.py:316-324` | Legacy generator path can select-or-create a Plan without a lock. |
| `backend/app/domain/decisions/orchestrator.py:361-420` | Pending and peer conflict checks are ordinary selects; no aggregate lock is acquired. |
| `backend/app/domain/decisions/orchestrator.py:700-740`, `842-908` | Settlement and proposal decision update multiple child/domain rows after non-locking reads. |
| `backend/app/domain/decisions/orchestrator.py:743-786` | Scheduler scans due child rows without a claim or `SKIP LOCKED`. |
| `backend/app/jobs/scheduler.py:25-35` | Scheduler runs in a distinct Session and commits its batch. |

### Confirmed issue / rejected concern

- **Confirmed:** neither an item-only lock nor a unique child index protects the
  aggregate-wide peer/constraint/member reads made by classification.
- **Confirmed:** duplicate `Plan` rows per Trip are possible from the inspected ORM
  mapping.  Current consumers often select one Plan without a uniqueness proof
  (`backend/app/api/main.py:389-416`, `backend/app/domain/chat/service.py:1194-1204`).
  The database data state has not been inspected in this design-only round.
- **Rejected:** `FOR NO KEY UPDATE` is automatically equivalent to `FOR UPDATE` for
  the aggregate root.  PostgreSQL documents that `FOR NO KEY UPDATE` is weaker: it
  does not conflict with `FOR KEY SHARE`.  While it can be an optimization for a
  proven non-key update, it does not express the required single application root
  mutex as clearly as `FOR UPDATE`.

### Corrected architecture

#### Lock strength

Use `FOR UPDATE` for the Trip and Plan root during a PR-05B aggregate command.  It
is deliberately conservative.  The Plan primary key is not being changed, but the
system needs all compliant aggregate writers to use one unambiguous incompatible
root lock, including plan creation and parent/child decision effects.  Child rows
whose status is read-modify-written use `FOR UPDATE` too.

`FOR NO KEY UPDATE` may be reconsidered only after a PostgreSQL compatibility test
shows it preserves the root-mutex invariant for every FK/insert path.  It is not a
drop-in performance change for PR-05B.  Plain reads remain nonblocking; the command
does not promise serializable read-only pages.

#### Mandatory canonical order

For every aggregate-changing command:

```
Trip rows, ascending trip.id
  -> Plan rows, ascending plan.id
    -> idempotency ledger row for the already locked Plan
      -> PlanItem rows, ascending item.id
        -> DecisionRound rows, ascending round.id
          -> ChangeProposal rows, ascending proposal.id
            -> ProposalDecision/Vote rows, ascending stable child key
```

The idempotency row comes immediately after the Plan root and before any command
effects/child locks.  This prevents an inversion where one request holds a key/ledger
uniqueness lock and waits for Plan while another holds Plan and waits for the same
ledger key.  Normal single-plan commands only acquire the suffix they need; no
helper may acquire a later lock and then go back upward.

#### Plan/Trip creation and existing Plan updates

- **Existing Plan:** resolve identifiers without treating the lookup object as
  authoritative, then lock Trip `FOR UPDATE`, lock its one Plan `FOR UPDATE`, and
  re-read the target/decision inputs.
- **Plan absent:** lock the Trip first; then select the Plan in that transaction.  If
  still absent, create it.  Add a database uniqueness invariant on `plan.trip_id`
  only after a preflight proves/corrects legacy duplicates.  The unique constraint is
  a backstop; the Trip root lock is the operational serialization point.
- **More than one existing Plan:** fail closed as a migration/data-integrity problem.
  PR-05B must not silently select “first” and call the result aggregate protection.

#### Rounds, proposals, settlement, and scheduler

- A vote, object, extension, proposal decision, expiry, or settlement first maps its
  external child id to a Plan id, then follows the Trip -> Plan root order and
  re-loads the Round/Proposal under its child lock.  It does not trust the route’s
  request-Session `round_` or `proposal` instance.
- Scheduler workers may first perform a **non-locking candidate `(trip_id, plan_id)`
  scan**.  They sort those roots and claim Trip rows with `FOR UPDATE SKIP LOCKED`;
  for each claimed Trip they acquire its Plan `FOR UPDATE` and then select due
  Rounds/Proposals with `FOR UPDATE SKIP LOCKED`, recheck status/deadline, and
  settle/expire.  Skipped Trip roots remain for a later tick/worker.
- The scheduler must not lock a Round first and then seek its Plan.  That inverts the
  request path and creates a deadlock risk.  `SKIP LOCKED` is a work-claim mechanism,
  not proof that the earlier candidate scan was current.

#### Multi-plan operations

There is no safe claim that the present product has multiple valid Plans per Trip.
Until the one-Plan invariant and data audit are complete, a command encountering
multiple Plans is a controlled error.  If future operation deliberately touches
multiple aggregates, it must enumerate all `(trip_id, plan_id)` roots, sort
lexicographically, lock all Trip roots then all Plan roots in that order, and only
then load children.  It must never discover/add a second Plan half-way through a
locked single-plan operation.

#### Deadlock and lock-timeout recovery

On PostgreSQL deadlock (`40P01`) or serialization failure (`40001`), discard the
entire write Session and retry only the **local, no-remote-call commit transaction**
a bounded number of times.  Re-run authorization, locks, revision comparison, and
all reads in a new Session.  On lock-not-available/timeout (`55P03`) return a
documented retryable busy response; do not mark a ledger row completed.  The
idempotency ledger makes a retry after an unknown client response replay a committed
result when one exists.  It does not make a remote provider call or an external
side effect exactly once.

### Affected PR-05B implementation phase

- **B.1:** approve the one-Plan data preflight/migration plan and the canonical lock
  ordering specification; no lock behavior is enabled.
- **B.2:** implement root/child lock helpers and move every writer, including
  scheduler, onto them.
- **B.3/B.4:** run complete PostgreSQL contention tests before public activation.

### Acceptance test design

1. Two direct changes to different items attempting an overlap: the second command
   acquires the root after the first and sees either a stale revision or the new
   schedule conflict; two overlapping committed items are impossible.
2. Two generators for a legacy Trip with no Plan: one Plan row survives; duplicate
   Plan data produces a controlled integrity/preflight failure, never arbitrary
   selection.
3. Concurrent final vote/manual settlement/scheduler settlement: exactly one closes
   the round and appends settlement effects; all others observe closed state.
4. Two scheduler workers scan the same due work: one claims the Plan with
   `SKIP LOCKED`; the other skips it, does no duplicate notice/log, and may process
   another unlocked Plan.
5. A purposely reversed test helper is rejected by code review/test instrumentation;
   a real two-root test locks roots in sorted order and completes without deadlock.

### Open decisions

- Whether all aggregate writers will retain the extra Trip root lock, or whether a
  formally proven Plan-only root path can be introduced after the no-Plan creation
  problem is removed.  This report recommends Trip -> Plan initially for uniformity.
- Batch size/fairness for scheduler Plan claims and user-visible mapping of lock
  timeout to `409` versus `503`.  Either must be documented and tested.

---

## 6. Idempotency completeness

### Original finding

Report 41 proposed a durable ledger, but the review correctly required precision on
normalization, HMAC rotation, changed/deleted results, concurrent waiting, failure
boundaries, and retention.  Those omissions are **confirmed**.

### Source evidence with exact line references

| Evidence | What the current source proves |
| --- | --- |
| `backend/app/api/main.py:195-220` | `ChangeRequest` has patch/request/reason/options but no expected revision or idempotency key. |
| `backend/app/api/main.py:223-304` | Vote, booking, and chat request models likewise have no idempotency input. |
| `backend/app/db/models.py:233-465` | Current models contain no revision or mutation ledger table. |
| `backend/app/domain/decisions/orchestrator.py:661-675`, `842-908` | Existing unique vote/decision constraints are local-row constraints, not request replay records. |
| `backend/app/domain/places/geoapify.py:79-149` and `generator.py:612-658` | External provider/planner work exists outside any future DB ledger guarantee. |

### Confirmed issue / rejected concern

- **Confirmed:** an idempotency key without a canonical semantic fingerprint permits
  accidental key reuse for different commands.
- **Confirmed:** a bare SHA-256 request digest is unsuitable when the canonical
  payload can contain private/free-text fields; the persisted comparison value needs
  a keyed HMAC and a rotation version.
- **Confirmed:** no response can be assumed safe to rebuild by re-reading current
  resources: an originally successful proposal/item may later be settled, expired,
  changed, or deleted/removed.
- **Rejected:** the protocol can claim distributed exactly-once behavior.  It can
  guarantee at most one committed local database outcome per retained key scope and
  replay that stored outcome; provider/model/API execution can still be repeated.

### Corrected architecture

#### Ledger scope and stored outcome

For the initial Plan-scoped mutation surface, create a ledger whose unique scope is:

```
(plan_id, actor_membership_id, operation_code, idempotency_key)
```

`operation_code` is a versioned semantic command name, not a raw URL.  Store at
minimum: opaque ledger id; Trip/Plan/actor scope; operation code/version; key;
fingerprint HMAC; `hmac_key_version`; completed response HTTP status; a sanitized,
versioned response DTO; resulting Plan revision; committed timestamp; and
`expires_at`.  The result DTO must exclude bearer credentials, private constraint
wording, internal membership ids other than the current actor’s public-safe context,
provider payloads, and stack/error detail.

The command obtains the Plan root lock before inserting/locking this ledger row.
For a matching completed row it first re-authorizes the current caller, then returns
the stored status/DTO with `replayed: true` and **does no domain write**.  It does not
re-query an item/proposal to reconstruct the original response.

Thus, if the original successful resource was later modified, removed, or no longer
terminal, retry still returns the original recorded result plus the original
`resulting_plan_revision`; clients must refresh current plan state.  If the actor no
longer has current authorization for the Trip, do not replay the historical payload.

#### Canonical fingerprint

Calculate HMAC-SHA-256 over a versioned canonical JSON object containing exactly:

- `fingerprint_schema_version`, `operation_code`, `trip_id`, `plan_id`, target
  resource ids, and actor membership id;
- **`expected_plan_revision`**;
- all semantic command values: normalized allowed patch fields, booking boolean,
  vote option, proposal decision/action, generation request parameters, and any
  alternatives/options that affect the decision;
- exact list order where order is semantic; otherwise an endpoint-specific declared
  sorted/deduplicated set rule;
- normalized dates/timestamps in one ISO format, Unicode NFC text, documented
  trim/empty policy, booleans/nulls, and canonical decimal/string representation for
  monetary/numeric values (never incidental JSON float rendering).

Do **not** include bearer token, display-only labels, traces, request timestamp,
response metadata, or the idempotency key itself.  Reject unknown mutable fields
before canonicalization.  A same key with a different HMAC produces a stable
`409 IDEMPOTENCY_KEY_REUSED`, including when the first resource has subsequently
changed or vanished.  Compare HMACs with a constant-time comparison.

#### HMAC version and rotation

The active HMAC key version is configuration-managed outside the database and is
used for new ledger rows.  The row records which version signed it.  During rotation:

1. deploy active key version N+1 while retaining N as verify-only;
2. on replay, recompute using the row’s stored version and compare;
3. retain every retired verification key until no unexpired row references it;
4. purge expired rows before securely removing the corresponding retired key.

If the required historical key is unavailable, fail closed with an operational
error; never treat a mismatch as a fresh command.  Key material is never stored in
the ledger, trace, response, or test fixture.

#### Concurrent, failed, timeout, and retry semantics

1. **Same key, same payload, concurrent:** request A holds the Plan root, completes
   effects/revision/ledger in one commit.  Request B waits only on the database root
   lock (bounded by a configured statement/lock timeout), then sees and replays the
   completed ledger row.  No polling or sleeping is needed.
2. **A rolls back before commit:** its uncommitted ledger/effects disappear.  B may
   then become the one command execution; no response is falsely replayed.
3. **Domain precondition/validation failure before durable command completion:** do
   not create a completed-success ledger row.  Return the current safe error.  A
   future retry is re-evaluated under its expected revision; same-key/different-body
   remains a conflict only if a completed ledger row exists.
4. **Deadlock/serialization/lock timeout:** roll back/close the whole Session.  A
   bounded retry is allowed only for the local Commit phase.  Do not retry a provider
   or model call under the lock; an exhausted retry returns retryable failure without
   a completed ledger row.
5. **Client timeout or lost response after commit:** a retry with same key/payload
   replays the stored success.  If the server cannot know whether a broken commit
   connection committed, retrying the lookup is safe: it finds a committed row or
   executes exactly one new local transaction.

#### Retention and safe cleanup

Choose and document one retention interval before B.1 schema finalization.  A
dedicated maintenance job deletes only `completed` rows whose `expires_at` is in the
past, in small batches claimed with `FOR UPDATE SKIP LOCKED`; it must not delete
unexpired rows or rows needed by a retained HMAC version.  Cleanup reports counts
and key versions, not fingerprints/payloads.  Retention expiry ends replay support;
it must be explicit in API documentation rather than silently advertised as
permanent exactly-once behavior.

### Affected PR-05B implementation phase

- **B.1:** schema shape, key-version contract, canonicalizer unit tests, retention
  policy, and secret-management approval.  No endpoint behavior is enabled.
- **B.2:** atomic writer/ledger transaction and replay behavior for every covered
  command.
- **B.3/B.4:** client key lifecycle, safe replay UX, maintenance job/evidence, and
  removal of bypassing routes.

### Acceptance test design

1. Unit-test every canonicalizer: reordered non-semantic maps match; semantic list
   order behavior is explicit; equivalent date/decimal forms match; a changed patch,
   target id, or expected revision differs.
2. Use two configured HMAC key versions.  Verify an old unexpired completed row
   replays after rotation, a missing old key fails closed, and a retired key cannot be
   removed while a retained row still references it.
3. Concurrent same-key/same-fingerprint commands create one durable effect and
   return byte-equivalent safe outcome DTOs (apart from `replayed`) to both callers.
4. Same key/different fingerprint returns stable conflict; same payload/different key
   is a new command subject to current revision/domain validation.
5. Commit a command, then mutate/remove its resource by a later valid command.  A
   retry returns the original stored result and creates no new effect.  Remove actor
   membership and confirm historical response is not replayed to unauthorized access.
6. Fault-inject flush, deadlock/serialization outcome, lock timeout, and lost-response
   client retry.  Prove atomic DB effects/ledger or no completed row, as applicable.
7. Cleanup fixture proves only expired completed rows are claimed/deleted and HMAC
   key retirement is blocked until its rows are gone.

### Open decisions

- Retention duration and the legal/product policy for replaying a historical safe
  response after a resource has changed.
- The authoritative secret-management owner and rotation runbook.  This design names
  required behavior, not a production secret store.
- Whether deterministic terminal 4xx outcomes should be ledgered.  This report
  recommends not treating them as completed command results in the first release;
  record only committed local command outcomes to avoid permanently burning a key on
  state that may legitimately change.

---

## 7. Test precision and concurrency harness rules

### Original finding

The prior test matrix named barriers and independent PostgreSQL Sessions but did not
rule out a common false-deadlock pattern: one actor holds a row lock while waiting at
a barrier for another actor that cannot reach that barrier until it obtains the same
row lock.  The concern is **confirmed**.

### Source evidence with exact line references

| Evidence | What the current source proves |
| --- | --- |
| `backend/tests/conftest.py:30-45` | The existing suite is deliberately wired to a disposable localhost test database and disables scheduler/AI.  It does not itself make two test actors use independent Sessions. |
| `backend/tests/test_paths.py:145-153` | Existing settlement “idempotence” calls `settle_round` twice on the same `db` fixture, proving sequential behavior only. |
| `backend/tests/test_paths.py:156-166` | Due-round coverage similarly invokes scheduler domain code sequentially in one Session. |
| `backend/app/jobs/scheduler.py:25-35` | Real scheduler uses a fresh Session, so concurrency correctness must be tested with more than one connection. |

### Confirmed issue / rejected concern

- **Confirmed:** same-Session tests cannot prove PostgreSQL row lock, visibility,
  waiting, deadlock, or `SKIP LOCKED` behavior.
- **Confirmed:** sleeps are not evidence of an intended interleaving; they are flaky
  scheduling guesses.
- **Rejected:** a `Barrier(2)` placed after A acquires `Plan FOR UPDATE` and before A
  commits is safe when B must obtain the same Plan lock before reaching that barrier.
  It creates an artificial test hang, not a product concurrency proof.

### Corrected architecture for the test harness

All concurrency tests use the disposable PostgreSQL `TEST_DATABASE_URL` only.  Each
actor owns a separate `SessionLocal` created inside its own thread/task and therefore
a separate checked-out connection.  Do not share the normal `db` fixture, an outer
test transaction, a `Session`, or a connection across actors.  The test joins workers
with a finite test timeout and reports captured exceptions; it does not use `sleep`
to obtain ordering.

Use one-way `threading.Event`/queue handshakes:

1. Start A; it obtains root lock and signals `a_locked`.
2. Start B only after `a_locked`; B signals **before attempting** the contested lock
   (or test instrumentation signals it has issued the request).
3. The coordinator releases A with `allow_a_commit`.
4. B naturally unblocks, locks/re-reads, and publishes outcome.

A never waits for `b_locked` while holding the root.  B never needs to obtain the
contested root in order to satisfy an event that releases A.

### Acceptance test matrix

| Scenario | Correct deterministic interleaving and assertions |
| --- | --- |
| Stale direct change | A locks Plan and signals; B signals before lock attempt; coordinator releases A.  Assert A commits one revision/effect, B sees new revision and returns stale/reclassified result. |
| Same idempotency key | A locks Plan and creates completed ledger before commit; B waits only on Plan.  Release A; B replays same safe outcome, with one effect/revision. |
| Different-item schedule conflict | A holds root after fresh peer read and stages move; B starts pre-lock; release A.  B sees A’s committed item and cannot create overlap. |
| Last vote versus settlement | A holds Plan/round and signals after its vote; B is started before lock.  Release A; B obtains fresh round and exactly one terminal settlement/log/notice is observed. |
| Proposal decision versus expiry/organizer action | Use the same root-first ordering; winner commits terminal status; follower sees terminal row and adds no contradictory transition/notice. |
| Scheduler claim | A claims Trip root with `FOR UPDATE SKIP LOCKED`, then its Plan, and signals; B scans/attempts claim after signal and returns no work for that Plan without waiting for an impossible barrier. |
| Generator provider boundary | Fake provider blocks before Commit while an instrumentation flag proves no root lock.  Release provider; a separate writer changes revision before Commit; generator rejects stale/revalidates. |
| Rollback/deadlock retry | Inject failure before commit and verify no durable row; for a retriable database error, discard Session and have a new Session re-run commit-only work once. |

The suite must additionally assert connection identity differs between actors and
must run against PostgreSQL rather than SQLite, because the contract depends on
PostgreSQL row locks, `SKIP LOCKED`, partial indexes, and error codes.

### Affected PR-05B implementation phase

- **B.1:** introduce the isolated multi-connection fixture and canonicalizer/schema
  tests, but do not modify existing production behavior in this design round.
- **B.2:** add root-lock, writer coverage, generator boundary, and scheduler tests as
  each writer is converted.
- **B.3/B.4:** add end-to-end stale-preview/replay compatibility tests and make the
  whole PostgreSQL race suite an activation gate.

### Open decisions

- CI capacity/timeout budget for the multi-connection PostgreSQL suite.
- Whether concurrency tests run in one dedicated serial database job or use unique
  disposable database names per worker.  They must preserve independent connections
  either way.

---

## 8. PR-05B.1 entry gate and final readiness verdict

### Required decisions/evidence before implementation authorization

| Gate | Current result in this review | Required before B.1 implementation starts |
| --- | --- | --- |
| One-Plan invariant | **Unverified.** Mapping has no `Plan.trip_id` uniqueness and no database inspection was authorized. | Approve a duplicate-detection/remediation migration plan; decide whether B.1 fails closed on duplicates before adding a uniqueness constraint. |
| Aggregate scope | **Design direction defined, product policy incomplete.** | Approve revision inclusion for membership/electorate, reminder/comment exclusions, and every row in the writer matrix. |
| Session boundary | **Corrected design defined.** | Approve new-Session write UoW and reauthorization contract; prohibit request ORM object handoff. |
| Preview semantics | **Corrected design defined.** | Decide snapshot size/TTL, Legacy replacement-candidate policy, and stale UX/retry policy. |
| Lock strategy | **Corrected design defined.** | Approve `Trip -> Plan -> child -> ledger` order, conservative `FOR UPDATE`, scheduler root-claim batching, and retry/status mapping. |
| Idempotency custody | **Incomplete.** | Select HMAC secret owner, key-version rotation runbook, canonical field rules, response retention duration, and cleanup operator. |
| Incremental activation | **Corrected dependency graph defined.** | Approve that B.3/B.4 cannot activate until all B.2 writer coverage and fleet-version gates pass. |
| Test evidence | **Not implemented or executed by design.** | Approve isolated PostgreSQL connection fixture and no-sleep/event discipline as B.1/B.2 acceptance criteria. |

### Final readiness verdict

**CONDITIONAL PASS.**  The independent audit confirms the overall direction—Plan
aggregate revision, short explicit local transactions, deterministic domain
revalidation, durable scoped idempotency, and PostgreSQL-backed locking—is viable.
It also corrects material gaps in snapshot coherence, request-Session ownership,
incremental rollout, generator boundaries, lock protocol, and replay semantics.

**Does the repository genuinely meet the prerequisites to enter PR-05B.1
implementation? No.**  B.1 is not authorized yet because its supposedly additive
schema/ledger choices depend on unresolved one-Plan data/invariant handling, HMAC
key-version/retention ownership, approved aggregate writer scope, and the explicit
new-Session transaction contract.  These are design and migration-entry gates, not
details safely deferred until after a schema change.

**PR-05B implementation must NOT start in this round.**  No code, ORM, Alembic,
frontend, test, configuration, database, provider, branch, PR, or deployment action
was performed as part of this review.
