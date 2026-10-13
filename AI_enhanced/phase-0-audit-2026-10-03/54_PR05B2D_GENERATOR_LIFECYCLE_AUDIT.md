# Cadensy PR-05B — B.2D0 Generator Lifecycle Audit & Implementation Plan

**Status:** Design/audit only, 2026-10-11. No application code, test, migration,
route activation, provider call, database operation, commit, push, or deployment
was performed for this report.

**Design verdict:** **DESIGN FROZEN — PASS** for the B.2D0A protocol correction
in section 11. This is a design freeze only: existing direct writer coverage,
PostgreSQL acceptance evidence, feature-on HTTP integration, and production
activation remain separately blocked.

## 1. Scope and frozen inputs

This is a source-backed audit in the isolated
codex/pr05b1-schema-foundation worktree. Report 49 is the governing protocol
freeze. Reports 50–53 are implementation evidence for the Trip-bound foundation;
report 43 remains the Plan-preview consistency contract; report 47 remains
historical Plan-writer evidence.

The corrected target lifecycle is deliberately narrow:

    Replay Preflight (short owned root transaction; no external work)
      -> Prepare (short read-only snapshot)
      -> External Work (provider/cache/model/rules; no Trip lock and no request Session)
      -> Validate & Commit (fresh owned short transaction)

This report does not authorize a Plan uniqueness migration, generic-kernel change,
Scheduler/Organizer migration, Preview/Apply work, production activation, or a
real provider call.

| Frozen requirement | Evidence | Generator consequence |
| --- | --- | --- |
| A requirements change with one Plan advances Trip facts and Plan revision; with zero Plans it advances Trip only. | 49:39-49 | A generated draft must refuse a changed captured fact/Plan state. Generator output must not be mislabeled a requirements mutation. |
| Canonical locks begin Trip -> credential -> membership -> Plan -> ledger -> children. | 49:51-71; backend/app/domain/transactions/trip_kernel.py:167-219; backend/app/domain/transactions/kernel.py:249-310 | Commit uses this root order; no provider/model or child lookup can move before it. |
| Preview consistency relies on every relevant writer advancing Plan revision atomically. | 43:360-405 | B.2D cannot claim global Preview/Apply coverage while legacy writers remain. |
| Phase 4 requirements writers are revision-owned and final PASS. | 53:231-291 | A real protected requirements mutation can be used to prove stale-draft rejection. |
| Existing Trip/Plan durable HMAC envelopes are frozen. | 49:159-174; backend/app/domain/transactions/durable.py:291-405; backend/app/domain/transactions/trip_durable.py:79-205 | B.2D must compose existing contracts or add one narrow reviewed coordinator; it must not silently alter an accepted envelope. |

## 2. Confirmed current architecture

### 2.1 HTTP and Session graph

POST /api/trips/{trip_id}/plans/generate receives the request Session and
request-Session TripMembership, invokes generator.generate_plan(db, trip_id, me),
then commits that same Session at route completion
(backend/app/api/main.py:1413-1444). get_session yields SessionLocal for the
request (backend/app/db/session.py:37-43); current_membership returns ORM
TripMembership from that Session (backend/app/api/main.py:152-175).

The actual graph is therefore:

    HTTP request / request Session / request membership ORM
      -> scoped Trip read
      -> generator.generate_plan
           -> Trip, Preference, Constraint, Plan, PlanItem reads
           -> in-place Trip.destination normalization
           -> Place cache read -> possible Geoapify HTTP -> Place cache upsert
           -> Planner model calls or deterministic rules fallback
           -> Plan / PlanItem / PlanChange flushes
      -> route db.commit()

No current Generator step owns a fresh Session, a Trip root lock, fresh
authorization, revision validation, or a durable ledger record.

### 2.2 Reads and planning inputs

| Input/fact | Exact source | Actual use |
| --- | --- | --- |
| Trip, organizer role, organizer preference readiness, any existing item | generator.py:160-177, 296-313 | Requires matching organizer role plus preferences_submitted and submitted Preference; rejects any PlanItem joined through any Trip Plan. |
| Destination, currency, dates | generator.py:179-190, 327-333 | Normalizes destination in-place; dates define day slots; currency updates/creates Plan. |
| Plan existence | generator.py:316-324 | First Plan ordered by created_at is used; absent Plan is inserted and flushed before external work. |
| Membership count | generator.py:358-363 | Enables budget-ceiling enforcement only when count is over one. Current query does not filter status. |
| Required structured constraints | generator.py:366-388 | Reads required kind/params only. It does not read private original text. |
| Interests, submitted budgets and availability | generator.py:391-459 | Preferences supply candidate ordering, budget and date availability inputs. |
| Candidate Places | generator.py:199-216; places/service.py:72-132 | Becomes Planner/rules candidate DTO pool. |

MemberConstraintPrivate is deliberately separate from machine constraints
(backend/app/db/models.py:215-244), and Generator does not read it. That remains
a non-negotiable DTO/prompt privacy boundary.

### 2.3 Plan creation, regeneration, and writes

Trip creation normally inserts a Plan with the organizer membership
(backend/app/domain/trips/service.py:104-137). Plan.trip_id is not unique in the
model (backend/app/db/models.py:250-274).

Current Generator behavior is not a replacement workflow:

1. A Plan containing any item returns PlanAlreadyHasItems / HTTP 409 and remains
   unchanged (generator.py:176-177; test_plan_generation.py:515-553).
2. One existing Plan with zero items may be populated.
3. A no-Plan legacy Trip receives a Plan before cache/provider/model work. A
   later blocked result can therefore leave a new empty blocked Plan.
4. There is no source route that deletes a Plan or replaces its item collection.

Successful generation inserts PlanItem rows and append-only PlanChange rows with
ai_generate or rule_generate origin (generator.py:1712-1757), then marks the Plan
active, clears blocked reason and needs_refresh, and sets the total
(generator.py:277-293). A blocked draft changes status, blocked_reason and total,
then returns no items (generator.py:240-275).

Plan has revision/status/blocked/total/currency/needs_refresh; PlanItem and
PlanChange are linked rows (backend/app/db/models.py:250-274, 341-374, 421-442).
Existing _plan_out also takes a scalar Plan rather than rejecting duplicates
(backend/app/api/main.py:506-533). That is legacy behavior, not a safe B.2D
aggregate-selection policy.

### 2.4 External work and fallback

The current cache/provider path is inside the request Session:

1. places_for_planner reads cached Place rows (places/service.py:72-80,
   153-164).
2. On refresh, it calls geoapify.fetch_places (places/service.py:98-132).
   Geoapify uses a ten-second HTTP client and may make geocode plus multiple
   places calls (places/geoapify.py:79-163).
3. Fetched values are upserted and flushed in the same Session
   (places/service.py:167-195). Failure returns existing cache; destination
   failure becomes a blocked Generator outcome (generator.py:199-213).

For every day, _planner_day builds DTO-only Planner input, calls planner.plan_day,
locally validates candidate IDs/times, then falls back to _rules_day on
AgentUnavailable/unusable model output (generator.py:497-577, 612-706).
planner.plan_day calls the model and has one repair attempt for malformed
non-mocked output (backend/app/agents/planner.py:100-132). Its prompt uses
candidate and sanitized structured facts (planner.py:160-234).

Rules fallback is established product behavior, not a rollback condition. If all
attempts are infeasible, the result is blocked. It must be preserved.

### 2.5 Current tests and absent evidence

test_plan_generation.py covers organizer preference gating, no overwrite of an
existing item, deterministic constraints, blocked outcomes, Planner/rules
fallback, and HTTP behavior. Its normal fixture creates a Plan in advance
(test_plan_generation.py:74-164), and its autouse fixture replaces the Place
service with in-memory candidates (252-258). It does not prove a PostgreSQL root
lock, a provider/session boundary, a no-Plan protected commit, or stale draft
rejection.

test_places.py covers cache-only reads, refresh/upsert, and provider-failure cache
fallback (test_places.py:27-103, 397-461). It does not exercise provider latency
against a Generator transaction.

Scheduler currently only settles due rounds and expires proposals
(backend/app/jobs/scheduler.py:25-46); no scheduler source path invokes
Generator. The planner evaluation script directly calls generator.generate_plan
(backend/app/agents/agent-server/run_planner_eval.py:1636), so it is a non-HTTP
bypass that must remain evaluation-only until separately migrated.

## 3. Confirmed lifecycle risks

| Risk | Evidence | Effect |
| --- | --- | --- |
| Request Session spans cache/provider/model work. | Sections 2.1 and 2.4. | It violates short-transaction lifetime and can retain an autobegun transaction/identity map across remote latency. |
| Provider cache writes share later Plan commit. | places/service.py:120-132, 167-195; main.py:1430. | Cache provenance and Plan Domain effects are coupled even though cache is not authoritative Plan state. |
| No fresh root lock or authorization protects the eventual write. | generator.py:160-293 versus trip_kernel.py:143-219. | Requirements changes, credential/membership changes, another generator, and direct writes can race. |
| First-row Plan selection / early Plan creation. | generator.py:316-324. | Concurrent legacy flows can select/create conflicting Plans; failures can leave lifecycle effects. |
| Inputs become stale during model work. | generator.py:182-239, 612-657. | A draft can comply with old facts and persist after current facts changed. |
| Legacy membership paths bypass facts revision. | join_invite directly inserts a membership at trips/service.py:309-373; remove_membership changes status/revokes sessions at 386-408. | Membership count/status is Generator input, but a revision-only check is not a complete proof. |
| Scheduler and legacy Organizer/decision paths bypass Plan revision protocol. | Scheduler commits direct orchestrator calls at jobs/scheduler.py:25-35; settle_round writes PlanItem/PlanChange/notice at decisions/orchestrator.py:757-797; organizer actions mutate round/proposal/item/notice at decisions/organizer.py:115-230. | They remain global Plan-preview coverage gaps; B.2D must not claim to migrate them. |
| Evaluation tooling bypasses HTTP route/gate. | run_planner_eval.py:1636. | Default-off route gating alone is insufficient if such tooling becomes runtime-reachable. |

These are confirmed Generator/lifecycle facts, not defects in the accepted
Trip/Plan kernels.

## 4. Proposed Prepare design

### 4.1 Transaction boundary

prepare_generation is a new short-lived read-only REPEATABLE READ Session
operation. It opens a Session from the factory, sets isolation/read-only before
its reads, materializes immutable dataclasses, closes, and returns no ORM object.
It does not call a provider/model and does not create a Plan.

Preliminary HTTP authentication supplies only primitive PreliminaryActorScope and
private CredentialProof to the later commit. Prepare may assess the claimed
organizer state to avoid wasted work, but Commit is the authorization
linearization point.

The single read snapshot makes its fields internally coherent. It does not claim
they remain current after closing; Commit rechecks revisions and the exact
read-model projection under the root lock.

### 4.2 Detached PreparedGeneration contract

The following is proposed, not implemented:

    PreparedGeneration
      trip_id
      organizer_membership_id
      planning_facts_revision
      plan: None | {id, revision, status, blocked_reason, currency, needs_refresh}
      plan_lifecycle: {item_count, no_existing_items}
      trip: {normalized_destination, start_date, end_date, currency}
      organizer_readiness: {role, status, preference_submitted}
      planning_inputs:
        membership_count
        required structured constraints only
        interests
        submitted budget caps
        submitted availability windows
      planning_input_fingerprint
      planned_plan_id_if_absent

Rules:

1. Select all Plans ordered by id. Zero is permitted, one is authoritative, and
   more than one is a typed DuplicatePlans failure before external work.
2. Check the existing any-item condition during Prepare. Protected B.2D never
   deletes/replaces items.
3. Normalize destination into the DTO; do not write Trip.destination in Prepare.
4. Carry no original constraint text, visibility, credential, identity-map
   object, display name, internal ID, provider payload, or prompt.
5. planning_input_fingerprint is an in-memory canonical digest of exactly the
   DTO facts read by current Generator. It is not a ledger field or credential.
   It is an additional conservative guard while all lifecycle writers are not
   revision-owned.
6. For an absent Plan, planned_plan_id_if_absent uses the deterministic
   server-owned UUID-v5 derivation from (trip_id, actor_membership_id,
   idempotency_key) frozen in section 11.3. It must never be freshly random on
   each retry.

### 4.3 External Work contract

External Work accepts PreparedGeneration and returns an immutable
GeneratedDraft or BlockedDraft: DraftItems, generated-by metadata, safe Planner
notes, and blocked reason/code. No ORM row or Session is present.

The Place path must split into:

1. short cache read -> detached candidate DTOs;
2. provider call with no Session, transaction, Trip lock, or Plan lock;
3. short cache persistence transaction -> detached candidates.

Cache rows are provider provenance, independent of later Plan commit. Preserve
existing cached-place fallback, destination-not-found, candidate ordering, model
repair and deterministic rules behavior. The Planner and rules execute on
detached values only. A timeout may produce current fallback/blocked output but
cannot create partial Plan rows or hold a root lock.

## 5. Proposed Validate & Commit design

### 5.1 Fresh transaction protocol

Commit receives scope, CredentialProof, immutable prepared snapshot and immutable
draft. It opens a fresh owned Session and uses this exact order:

    1. BEGIN short local transaction.
    2. Trip FOR UPDATE.
    3. Freshly lock/revalidate AuthSession or GuestSession and actor membership;
       require organizer role/current authorization.
    4. Read all Plans FOR UPDATE ordered by id:
       zero = creation mode; one = existing mode; more than one = fail closed.
    5. Lock/read the global Trip/actor/key ledger row. A matching completed
       command replays before any current lifecycle/revision comparison; a
       mismatched, expired, foreign-scope, or unsafe record fails closed.
    6. Only when the key is new, rebuild current Generator input/lifecycle
       projection and compare captured Trip facts revision, Plan
       descriptor/revision, item lifecycle, and planning_input_fingerprint.
       Any mismatch is stale.
    7. Create candidate Plan only in zero mode, or use the one locked Plan;
       revalidate currency/status/no-item condition before child writes.
    8. In a callback SAVEPOINT, persist complete blocked state or all PlanItem and
       PlanChange rows; build a safe detached result from persisted values.
    9. On a state change: overflow-check and advance Plan.revision exactly once,
       write completed Plan-scope ledger outcome, flush and commit. Otherwise
       retain only a verified no-op outcome. Exceptions roll back all Domain work.

There is no provider/model/cache invocation, silent retry, or regeneration
inside this transaction.

### 5.2 Authorization and readiness

TripTransactionKernel already owns fresh Session acquisition, Trip-first locking,
credential/membership validation and zero/one/duplicate Plan selection
(trip_kernel.py:97-219). The Coordinator verifies current credential, membership
and organizer role before a replay. Its organizer-preference predicate
(generator.py:296-304) is a new-execution planning precondition and is evaluated
only after the ledger-first replay decision. A revoked/removed credential,
removed membership, or non-organizer fails before replay; an organizer no longer
ready rejects only a new execution without an existing matching outcome.

The minimum current-compatible design re-reads and compares the exact Generator
input projection under the Trip lock. It does not take an arbitrary broad
membership lock merely to hide a missing writer-migration decision.

### 5.3 Plan lifecycle and revisions

| Locked state | Protected result |
| --- | --- |
| Zero Plans at Prepare and Commit | Create exactly the deterministic candidate Plan inside Commit. A generated or blocked first Plan is a state change and has revision 1. |
| One empty Plan at Prepare and Commit | Use the locked same Plan. Generated items or changed blocked state advance revision exactly once. |
| Any item at Prepare or Commit | Existing no-overwrite behavior: reject with no child, ledger or revision effect. |
| More than one Plan | Typed DuplicatePlans; no arbitrary first row, replay or mutation. |
| Plan cardinality/identity changes between phases | Stale lifecycle failure; never attach the draft to an observed replacement Plan. |
| Blocked draft | Preserve present response fields. It changes state only when creating Plan or changing persisted Plan fields; equal already-blocked state is a true no-op. |
| Active draft | Insert all items plus ai_generate/rule_generate PlanChanges atomically; set current active fields and increment once. |

Generator writes derived Plan output, not planning inputs. It therefore advances
Plan.revision once for a state-changing result and does **not** advance
Trip.planning_facts_revision. That differs from Phase 4 input writers, which own
the Trip-and-Plan dual increment when a Plan exists.

### 5.4 Staleness proof

For a fully migrated future, unchanged Trip facts revision plus unchanged Plan
revision proves relevant facts/output did not commit during External Work. That
proof is not yet globally available. B.2D must therefore compare both revisions
and the fresh semantic input/lifecycle projection:

    captured DTO -> external draft -> fresh locked DTO == captured DTO ? commit : stale reject

This conservatively detects a known legacy membership/item/destination lifecycle
bypass even if it did not increment a revision. It does not make that bypass
revision-compliant or authorize a global preview-consistency claim.

## 6. Durable idempotency decision

### 6.1 Required contract

A protected plan.generate.v1 operation needs durable idempotency. It can create
many rows, invoke expensive external work, and face ambiguous network retries.
Replay must return a stored detached safe Generator response without rerunning
Geoapify/Planner or duplicating items/PlanChanges. It is not distributed
exactly-once: external calls are still at-least-once attempts; crash recovery may
begin a fresh no-record execution and repeat external work.

The safe outcome preserves the existing response shape:

    plan_id, status, days, blocked_reason, blocked_code,
    generated_by, used_ai, planner_note

A Generator-specific trusted nested codec must validate these public typed
fields at first write and against both historical/current policy on replay. It
must reject credentials, membership identities/names, private constraint
text/params, raw provider payloads, prompts, traces and diagnostics.

### 6.2 Composition finding

Existing DurableCommandKernel cannot be used unchanged: its Plan kernel rejects
missing Plans (kernel.py:289-310) and advances only an already locked
context.plan revision (durable.py:367-405).

TripDurableCommandKernel permits zero/one Plan but correctly advances
planning_facts_revision for Phase 4 fact writers and only increments a Plan
already present in LockedTripContext (trip_durable.py:105-151). It cannot
unchanged create a first Plan, return Plan-scope durable result, and avoid
incorrectly advancing facts revision.

The smallest safe answer is a **narrow Generator coordinator beside, not a
generalization of, accepted kernels**. Its corrected durable protocol is frozen
in section 11. It must:

- use TripTransactionKernel root acquisition only;
- retain existing MutationIdempotency uniqueness and aggregate_scope=plan;
- leave the frozen existing Plan and Trip HMAC envelopes untouched;
- use the existing canonical_request_hmac/canonical_payload_snapshot primitives
  for one new, operation-specific Generator envelope rather than pretending a
  zero-Plan command has an existing Plan or expected Plan revision;
- record resulting_plan_revision for created/updated Plan;
- use deterministic planned Plan identity for same-key zero-Plan retries;
- leave every accepted Plan HMAC path byte-for-byte unchanged.

Captured Prepare observations are execution preconditions only. They are not
HMAC inputs, are not stored as a command identity, and must never be re-signed
from a retry's latest database state. Do **not** sign/store model output or
response body. A draft is a server-created immutable transient artifact; Commit
validates it. Same-key concurrent prepares can yield different model artifacts,
but first ledger commit wins and the follower replays its result.

### 6.3 HTTP/gate compatibility

Legacy remains default. A later protected Generator gate must be default-off,
non-production, parse protected headers only when enabled, and never fall back
to Legacy after protected validation begins. Feature-off ignores malformed future
headers and preserves current status/body.

Current Generator has no expected-revision header, while _plan_out exposes no
Plan revision (main.py:506-533). The frozen protected contract requires only an
Idempotency-Key. It captures mutable revision/lifecycle state server-side after
Replay Preflight and never treats that state as retry identity. It adds no
revision-discovery endpoint or response header in B.2D; controlled stale
lifecycle rejection is sufficient because a failed no-ledger attempt is a new
execution on retry, not a replay. Section 11 fixes the exact feature-gate and
error policy.

## 7. Dependencies and coverage boundaries

| Dependency | State | B.2D consequence |
| --- | --- | --- |
| Trip root kernel/fresh auth | Implemented Phase 2 | Reuse; no request ORM crosses Commit. |
| Trip facts revision/cross-scope ledger schema | Implemented Phase 1/3 | Use as stale signal; no B.2D0 schema alteration. |
| Phase 4 writers | PASS in report 53 | Use in real stale-draft concurrency tests. |
| Plan durable kernel | Existing-Plan only | Reuse canonical contract, not zero-Plan acquisition. |
| Place lifecycle boundary | Not separated | Must be split before any protected Generator claims no provider under root lock. |
| First-Plan durable coordinator/outcome codec | Protocol frozen in section 11; not implemented | Primary protected-generation implementation blocker. |

B.2D explicitly does not migrate legacy Generator while feature-off, join/remove
membership, Scheduler settlement/expiry, Organizer actions, direct orchestrator
calls, or planner evaluator tooling. The local semantic recheck protects the
Generator's consumed facts, but does not make these paths globally
revision-compliant.

## 8. PostgreSQL acceptance matrix for later implementation

All cases require disposable localhost PostgreSQL, independent Session factories
and backend PIDs, scheduler disabled, no real provider, deterministic
Events/queues, and pg_blocking_pids (or equivalent) rather than sleeps.

| Scenario | Required result |
| --- | --- |
| Fresh zero-Plan generation | Prepare sees zero; Commit creates one Plan and all items/changes atomically; Plan revision 1, one Plan-scope ledger, unchanged Trip facts revision, safe replay. |
| One empty existing Plan | Same locked Plan receives all items/changes; exactly one Plan revision increment; no deletion/replacement. |
| Existing item | Existing 409/no-overwrite behavior, no external work after early rejection where possible, no ledger/revision/new child. |
| Duplicate Plans | Prepare and Commit both fail controlled duplicate aggregate before callback; no arbitrary Plan or ledger effect. |
| Two concurrent generator keys | First holds Trip FOR UPDATE; observer proves second PID blocked; first succeeds and second stale-rejects without a second Plan/items/changes/ledger outcome. |
| Same-key concurrent generator | One commits, one replays; deterministic zero-Plan resource identity yields no duplicate Domain effect/revision. |
| Requirement change during model work | Pause external model after Prepare; commit real protected Constraint/Preference on another connection; release and assert stale Generator draft with no effect. |
| Plan revision change during model work | Use an independent real PostgreSQL update/lifecycle mutation after Prepare, labelled bypass simulation because current business Plan writers require items that make Generator reject. Commit must detect mismatch. This supplements, not replaces, real-writer evidence. |
| Zero-Plan creation versus protected Constraint | Test both root-lock orders. Constraint-first advances Trip facts and makes draft stale/no Plan; Generator-first creates Plan, later constraint advances both revisions. |
| Item appears during External Work | Fresh Commit sees non-empty lifecycle and refuses; no generated child rows/ledger. |
| Mid-item persistence fault | Inject after selected PlanItem/PlanChange flush; assert rollback of every generated child, Plan status/total/revision, first Plan creation and ledger. Cache rows may remain by design. |
| Provider timeout/destination failure | No Trip lock spans provider. Cached fallback is preserved; no-candidate/destination result is blocked with no partial items. |
| Model invalid/unavailable | Rules fallback remains; if infeasible, blocked write is atomic; raw prompt/provider data never enters outcome. |
| Stale retry | No in-transaction retry/regeneration. Because stale rejection has no Ledger outcome, a subsequent same-key or new-key request starts a fresh Prepare/External Work cycle and may repeat external work. |
| Reauthorization race | Revoke/remove organizer between phases; Commit fails before replay/effect and leaks no historical result. |
| Feature-off route | Body/status/errors unchanged; malformed future protected headers do not alter Legacy route. |

## 9. Recommended small implementation sequence

No slice is authorized by this report.

| Slice | Responsibility/candidate files | Required evidence | Compatibility risk |
| --- | --- | --- | --- |
| **B.2D1 — detached snapshot and pure draft seam** | New backend/app/domain/plans/generator_lifecycle.py and focused test_generator_lifecycle.py; minimal reuse from generator.py. | Repeatable-read DTO, no ORM escape, exact input projection, no provider/model/Plan write in Prepare; characterize current outputs. | Extraction can change fallback/blocked behavior; legacy route stays on existing function until parity is proven. |
| **B.2D2 — cache/provider outside Domain transaction** | Narrow places/service.py lifecycle seam plus focused tests. | Provider receives no Session; cache read/persist are short; candidate ordering/fallback preserved; root writer proceeds while fake provider blocks. | Cache refresh/upsert parity. |
| **B.2D3 — Generator durable coordinator** | New narrow domain/transactions Generator coordinator with exact-envelope tests. | Frozen Replay Preflight, stable Generator envelope, zero/one/duplicate selection, deterministic first-Plan identity, plan-scope ledger, strict outcome codec, overflow/no-op/rollback. | May not change existing Plan/Trip durable HMAC bytes or activate HTTP. |
| **B.2D4 — Validate & Commit vertical slice** | Generator lifecycle/commit module and PostgreSQL tests. | Full section 8 evidence; no external call under root lock; exact active/blocked/PlanChange behavior. | Must not expand to Scheduler/Organizer/other writers. |
| **B.2D5 — default-off HTTP adapter** | Narrow main.py gate/error map and route tests. | Feature-off compatibility, feature-on non-production only, no fallback, safe HTTP/header surface, full Legacy/Pydantic regression. | Public/frontend activation remains a separate decision. |

## 10. Superseded B.2D0 decision record

| Category | Decision |
| --- | --- |
| Current Generator audit | Complete: it is request-Session-bound across cache/model work and has no root/revision/durable ownership. |
| Proposed lifecycle | Prepare -> External Work -> Validate & Commit with detached DTOs and no remote call under Trip root lock. |
| Existing Plan behavior | One empty Plan may be populated; no existing item collection is replaced or deleted. |
| No-Plan behavior | Requires narrow first-Plan coordinator; no UNIQUE(plan.trip_id) migration is proposed. |
| Revisions | Generator state changes advance Plan revision once, never Trip facts revision; current input mismatch stale-rejects. |
| Durable replay | Required; its stable identity, first-Plan ID, outcome policy, and no-header HTTP policy are frozen in section 11. |
| Global coverage | Scheduler, Organizer, membership lifecycle, legacy Generator and direct tooling remain outside B.2D and block broader claims. |
| Exact next slice | **B.2D1 — detached snapshot and pure draft seam**, after approval, without route activation or durable commit. |

This section records the original B.2D0 conclusion. Section 11 supersedes its
conditional first-Plan/HMAC/replay/header statements. The following still hold:
semantic input/lifecycle rechecks remain necessary until legacy direct writers
are migrated, and the PostgreSQL matrix plus full regression boundaries are
required before protected activation. No B.2D implementation started in this
round.

---

## 11. B.2D0A — Durable Protocol Correction & Freeze

### 11.1 External review findings and source verification

| Finding | Verified source evidence | Corrected decision |
| --- | --- | --- |
| Report 54 placed the only Ledger lookup after External Work. | The old section 5.1 placed current-projection/stale checks before the Ledger. Existing durable kernels instead find the ledger before stale comparison (durable.py:311-332; trip_durable.py:94-107). | Add Replay Preflight before Prepare and repeat Ledger-first lookup at Commit. |
| Old signed fields were Prepare observations. | The old section 6.2 proposed Trip revision, Plan identity/revision, and input fingerprint in the command HMAC. Those values necessarily change after a successful generation. | They are transient preconditions only; the immutable command identity is separately frozen below. |
| A Commit could reject a historical command as stale before examining its record. | Existing Plan replay verifies a found record before `StalePlanRevision` (durable.py:311-332, 415-480). | Both Generator checkpoints authenticate and inspect the record before any stale test. |
| Existing Plan Durable cannot create a Plan. | Plan kernel rejects no Plan (kernel.py:289-301); its HMAC includes an actual `plan_id` and `expected_plan_revision` (durable.py:483-507). | Do not overload that frozen envelope for `plan.generate.v1`. |
| Trip Durable can select zero/one Plans but owns Trip-facts revisions. | Trip kernel permits zero/one and fails duplicate Plans (trip_kernel.py:202-219); Trip Durable increments facts and, when present, Plan revision (trip_durable.py:128-147). | Do not reuse it unchanged for Plan-output generation. Use a narrow coordinator with Plan-scope outcome ownership. |

The correction leaves Phase 1 schema, Phase 2 root authorization, Phase 3
cross-aggregate uniqueness, Phase 4 writer behavior, existing Plan HMAC bytes,
and existing Trip HMAC bytes unchanged. Report 49 remains the governing protocol
for locking, global key namespace, current authorization before replay, outcome
validation, and duplicate-Plan failure.

### 11.2 Stable `plan.generate.v1` identity and exact HMAC decision

`plan.generate.v1` is a **Plan-scope Ledger operation**, but it does **not** use
the frozen existing-Plan `IdempotentCommand` envelope. That envelope truthfully
means “an already locked Plan at an expected Plan revision”; neither condition
exists for a zero-Plan command. Inventing `expected_plan_revision=0` would be a
misleading precondition token and would make a successful retry unverifiable.

The new coordinator must call the same `canonical_payload_snapshot()` and
`canonical_request_hmac()` primitives in `idempotency.py:30-115`; it may not add
another canonical JSON format. With active fingerprint format `1`, the **exact
payload argument** passed to `canonical_request_hmac` is:

```text
operation_code = "plan.generate.v1"

payload = {
  "aggregate_scope": "plan",
  "trip_id": locked Trip.id,
  "actor_membership_id": freshly locked organizer membership id,
  "resource": {
    "kind": "generator_command",
    "id": generation_resource_id
  },
  "command": {
    "generation_contract": "initial_or_empty_plan.v1"
  }
}
```

`canonical_request_hmac` itself wraps those fields in its frozen outer JSON
envelope `{fingerprint_version, operation_code, command}`. The signed HMAC
therefore also binds `fingerprint_version=1`; its separate
`hmac_key_version` is stored in the Ledger and selects the retained verification
key, exactly as existing replays do (durable.py:426-439). The Coordinator first
applies `canonical_payload_snapshot` to the shown payload, and passes the frozen
snapshot to both HMAC calculation and its own verification path.

`generation_resource_id` is a server-derived lower-case 32-hex UUID-v5 value:

```text
UUIDv5(GENERATOR_COMMAND_NAMESPACE_V1,
       canonical UTF-8 tuple("plan.generate.v1", trip_id,
                             actor_membership_id, idempotency_key)).hex
```

The namespace UUID is a versioned server constant, not a client argument and
not an HMAC signing key. Its literal v1 value and the exact UUID-v5 name bytes
are frozen by **B.2D0B** immediately below; the earlier symbolic constant name
alone was not a complete executable identity specification. The idempotency key
itself remains protected by the global unique lookup; binding its derived opaque
resource ID into the HMAC makes record tampering or cross-key substitution fail
closed.

There is deliberately **no** `plan_id` or `expected_plan_revision` field in this
new Generator envelope. Actual Plan identity/revision are mutable execution
preconditions, not client intent. Existing Plan commands retain their exact
unchanged payload including both fields (durable.py:492-505); existing Trip
commands retain their exact aggregate-scope/expected-facts payload
(trip_durable.py:195-205). This is a new operation-specific use of the existing
canonical algorithm, not a modification or reinterpretation of either frozen
envelope.

The immutable v1 request has no client body today. If a later approved Generator
parameter changes accepted semantics, it must be canonicalized under
`command` before any HMAC/replay check, or use a separately reviewed operation
version. A same key with a changed operation, resource, aggregate scope, or
canonical command is `IdempotencyKeyReused`; it never becomes a second
generation. Prepare-time facts, provider values, model output, cache state,
timestamps, response bodies, and expected revisions are expressly excluded.

### B.2D0B — Exact UUID Identity Constants and Canonical Name Bytes

This is an authorized correction for the previously unspecified UUID-v5 namespace
literals and name-byte encoder. It does not alter the Generator HMAC envelope,
existing Plan/Trip HMAC envelopes, lifecycle/replay ordering, Ledger schema, or
revision ownership defined elsewhere in this report. It supersedes the earlier
incomplete implication that symbolic namespace names were themselves frozen
values.

#### 1. Frozen namespace literals and provenance

```python
GENERATOR_COMMAND_NAMESPACE_V1 = UUID("16bc0061-4ecf-56d5-99a5-f073dc64768b")
GENERATOR_FIRST_PLAN_NAMESPACE_V1 = UUID("550665b9-3451-5dbf-8f40-466f85e1a214")
```

These distinct literal UUIDs are the v1 runtime source of truth. They are
server-owned, immutable, environment-independent, and independent of HMAC
signing-key configuration. The following construction is provenance only, not
runtime configuration:

```text
uuid.uuid5(uuid.NAMESPACE_URL,
           "urn:cadensy:pr05b:plan.generate.v1:command-resource:v1")
  = 16bc0061-4ecf-56d5-99a5-f073dc64768b

uuid.uuid5(uuid.NAMESPACE_URL,
           "urn:cadensy:pr05b:plan.generate.v1:first-plan:v1")
  = 550665b9-3451-5dbf-8f40-466f85e1a214
```

A future namespace change requires an explicit protocol version, migration, and
historical verification policy; it cannot be a silent code or configuration
change.

#### 2. Exact four-element canonical name

Both UUID-v5 operations use exactly four already-validated string elements, in
this order:

```text
("plan.generate.v1", authoritative Trip ID,
 freshly authorized actor membership ID, validated idempotency key)
```

The representation is a JSON **array**, not an object, Python tuple
representation, delimiter-joined string, or HMAC envelope. Normalize each
element independently with Unicode NFC, then serialize and encode exactly as:

```python
json.dumps(
    [unicodedata.normalize("NFC", element) for element in elements],
    ensure_ascii=False,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8", "strict")
```

`elements` must contain exactly four `str` values. There is no indentation,
trailing newline, extra whitespace, sorting, or transformation after NFC; the
standard Python JSON escaping behavior is used. Existing authoritative Trip-ID,
Membership-ID, and Idempotency-Key validation remains unchanged. This encoder
rule does not broaden production identifier syntax and does not normalize or
otherwise alter frozen HMAC command fields.

#### 3. UUID-v5 byte-level calculation

For either namespace, let `name_bytes` be the strict UTF-8 output above:

```text
digest = SHA-1(namespace.bytes || name_bytes)
octets = first 16 digest bytes
octets[6] = (octets[6] & 0x0f) | 0x50  # version 5
octets[8] = (octets[8] & 0x3f) | 0x80  # RFC 4122 variant
result = lower-case 32-hex representation of octets
```

The command namespace produces `generation_resource_id`; the first-Plan
namespace produces the planned `Plan.id`. Their names are identical but their
outputs intentionally differ. Neither UUID derives from a mutable Plan/Trip
revision, generated draft, Provider result, prompt, or active HMAC key.

#### 4. Golden Vector A — valid Generator identity

Input elements and their exact compact canonical JSON name:

```text
["plan.generate.v1","0123456789abcdef0123456789abcdef","fedcba9876543210fedcba9876543210","gen-retry-001"]
```

| Namespace purpose | Expected UUID-v5 `.hex` |
| --- | --- |
| Generator command resource | `691bbfea177855d29f9a99d461752085` |
| Deterministic first Plan | `ca750cd46b0f55a5abb0d566f0adb167` |

Both values contain exactly 32 lower-case hexadecimal characters.

#### 5. Golden Vector B — Unicode encoder behavior

The NFC-normalized canonical name is:

```text
["plan.generate.v1","café","memberé","retry-01"]
```

Its exact strict UTF-8 bytes are:

```text
5b22706c616e2e67656e65726174652e7631222c22636166c3a9222c226d656d626572c3a9222c2272657472792d3031225d
```

The decomposed input using `cafe` + U+0301 and `membere` + U+0301 produces the
same canonical name bytes. This is an encoder-only vector; it does not permit
non-ASCII production Trip IDs, Membership IDs, or idempotency keys.

| Namespace purpose | Expected UUID-v5 `.hex` |
| --- | --- |
| Generator command resource | `9b6560b5530757098a0210cdb416ebb6` |
| Deterministic first Plan | `c219395c22a658a0bb7d8023be303e86` |

#### 6. Independent verification

On 2026-10-11, both namespace literals were parsed as valid and distinct UUIDs.
Vectors A and B were each recomputed by two independent paths:

1. Python standard-library `uuid.uuid5(namespace, canonical_json_string).hex`.
2. A separate RFC 4122 calculation that concatenates `namespace.bytes` and the
   strict UTF-8 name bytes, computes SHA-1, takes the first 16 digest bytes, and
   sets the version and variant bits shown above.

Both paths produced every expected resource and first-Plan identity exactly. The
decomposed Unicode form produced the same name as its NFC form. This verifies
JSON separators, Unicode normalization, UTF-8 bytes, namespace byte order,
version/variant bits, and lower-case output formatting without relying on one
UUID implementation matching itself.

#### 7. Replay, signing-key rotation, and schema compatibility

The resource UUID is a stable input to the approved Generator HMAC identity, but
is not a secret. It does not change under HMAC signing-key rotation; stored
`hmac_key_version` still selects a retained key only for HMAC verification.
Replay retains the frozen requirement to use the historical Ledger row before
Prepare, External Work, or stale lifecycle checks.

Each first-Plan UUID fits `Plan.id` `String(32)` (`models.py:250-257`), and each
resource UUID fits Ledger `resource_id String(64)` (`models.py:304-305`). The
existing global `UNIQUE(trip_id, actor_membership_id, idempotency_key)`, Plan
aggregate scope, outcome contract, expiry, duplicate-Plan failure, and
Plan-only revision transition are unchanged. Existing Plan and Trip HMAC golden
vectors were neither rewritten nor recomputed.

#### 8. Historical compatibility boundary

Repository evidence shows no implemented Generator durable coordinator or other
prior `plan.generate.v1` identity producer: Report 57 documents that B.2D3
stopped before implementation. This is not a claim about an external database or
deployment, which this review did not access. If evidence of a pre-existing
different Generator identity appears later, it requires an explicit historical
compatibility decision rather than overwriting either namespace constant.

#### 9. Remaining prerequisites for B.2D3

The identity blocker is resolved by this B.2D0B correction. B.2D3 itself remains
unimplemented and requires separate engineering authorization to add the narrow
coordinator, outcome codec, replay preflight/final recheck, and focused
disposable-PostgreSQL verification. Report 57 remains valid historical evidence
of the original blocker and must not be rewritten as an implementation result.

### 11.3 Deterministic first-Plan identity and lifecycle policy

For a zero-Plan first execution, the Coordinator derives a separate planned Plan
identifier before External Work:

```text
UUIDv5(GENERATOR_FIRST_PLAN_NAMESPACE_V1,
       canonical UTF-8 tuple("plan.generate.v1", trip_id,
                             actor_membership_id, idempotency_key)).hex
```

This is a server-only value: no request field can select it. Its exact namespace
and canonical name bytes are defined by B.2D0B above. `.hex` is exactly 32
lower-case characters, matching `Plan.id` `String(32)` (models.py:250-257). It
is stable for an interrupted same-key retry and unaffected by HMAC key rotation.
The coordinator must use that exact value only after Final Validate still sees
the captured zero-Plan lifecycle; it does not insert a Plan during Preflight or
Prepare.

UUID-v5 collision with another Plan ID is treated as an integrity/lifecycle
failure and rolls back; the coordinator must not attach its draft to that row.
No `UNIQUE(plan.trip_id)` constraint is introduced. If an attempt crashes before
its atomic Plan/revision/Ledger commit, no completed Ledger outcome exists and a
same-key retry is a new first execution with the same planned ID. If some other
process created a Plan in the interim, the Final lifecycle check fails stale;
the draft is not attached to it. If a historic result's Plan is later missing or
replaced, a zero-or-one-cardinality authorized replay returns the original
detached historical outcome and recreates nothing. A subsequent current-plan
read may naturally show the later state or 404; replay is not a current-resource
lookup. More than one Plan fails before replay, as Report 49 requires.

For one Plan, Prepare captures the authoritative `Plan.id`, revision, status,
currency, `needs_refresh`, and zero-item condition. Final Validate accepts only
the same locked ID and revision with no item; a replacement, an item appearing,
or any cardinality change is stale. The old no-overwrite behavior is preserved.

### 11.4 Two-checkpoint state machine and lock ordering

#### Stage 1 — Replay Preflight

1. Create a short-lived owned Session/transaction. Lock `Trip FOR UPDATE`
   first, then freshly lock and validate Account/Guest credential, actor
   membership, all Trip memberships in ID order where the Generator's
   electorate/input projection needs them, Plans in ID order, and finally the
   shared Ledger row. This preserves the frozen Trip -> credential -> membership
   -> Plan -> Ledger order.
2. Require current credential validity, non-removed/active membership, same-Trip
   scope, and organizer role. These authorization checks happen before a
   historical response is exposed. Organizer-preference readiness is a mutable
   planning precondition evaluated only for a new command after Ledger lookup.
3. Enforce Plan cardinality: zero or one is admissible; duplicate Plans raises
   the existing `DuplicatePlans` before Ledger interpretation, even if a record
   exists.
4. Lookup `(trip_id, actor_membership_id, idempotency_key)` with row lock. If no
   record exists, close/commit this no-mutation preflight transaction and continue
   to Stage 2. There is no Provider, Planner, cache, model, Plan insertion, or
   child mutation in this stage.
5. For a found record: reject foreign `aggregate_scope` before foreign result
   parsing; require operation/resource identity, supported stored and incoming
   fingerprint format, retained `hmac_key_version`, constant-time digest match,
   unexpired retention record, `resulting_plan_revision` non-null with Trip
   facts result null, valid 2xx status, and both historical and current trusted
   outcome contracts. Return the detached result immediately on success.

The exact record-verification order remains safe and compatible with existing
kernels: scope -> supported fingerprint/incoming-version agreement -> retained
verification key -> recomputed stable HMAC/resource/operation equality -> expiry
-> result-scope/status -> historical/current Outcome validation. An unsupported
stored format/key/outcome fails closed; an expired retained match raises existing
`IdempotencyRecordExpired`, rather than authorizing a fresh use of its key.

#### Stage 2 — Prepare

Only a command with no completed record reaches this stage. A short read-only
REPEATABLE READ Session builds detached, immutable DTOs: Trip facts revision;
zero/one Plan descriptor; Plan item count; organizer readiness; member/count,
structured required constraints, interests, budget caps, availability and dates;
canonical destination; and an in-memory canonical input projection/digest. The
digest uses the existing canonical JSON normalization only as a transient
precondition checksum; it is not a command HMAC, Ledger field, or retry input.
The Final check compares the full frozen projection, not a digest alone.

Prepare holds no live ORM object, inserts no Plan, calls no Provider/model, and
does not persist `Trip.destination` normalization. It may reject a newly
submitted command whose captured lifecycle already has items, but it never
blocks a completed historical replay because Stage 1 ran first.

#### Stage 3 — External Work

The pipeline takes detached values only: short independent cache read, provider
call without Session/transaction/root lock, short independent cache persistence,
then Planner and deterministic-rules fallback. It produces immutable
`GeneratedDraft` or `BlockedDraft`. Two same-key requests may both perform this
external work; this design promises one committed Domain mutation and one Ledger
outcome, not exactly-once Geoapify/model execution. A crash before atomic Ledger
commit similarly has no replay evidence and can repeat external work.

#### Stage 4 — Final Validate & Commit

1. Start a new owned short transaction; reacquire Trip, current credential,
   actor/all relevant memberships, Plans, and Ledger in the same frozen order.
2. Recheck cardinality first. Duplicate Plans fails closed before replay.
3. Re-read the Ledger **before** any lifecycle, input, or revision comparison.
   A valid matching completed row returns its detached result without a second
   mutation. A conflict/expired/unsafe record raises its existing typed failure.
4. Only with no record, rebuild the full locked planning projection and verify
   captured Trip facts revision, zero/one cardinality, Plan ID/revision,
   no-item lifecycle, and canonical planning-input projection. Any difference is
   a stale lifecycle/revision rejection with no Plan, child, revision, or Ledger
   write.
5. In the same transaction/savepoint, create the deterministic Plan only in the
   still-zero case or persist the complete blocked/active result on the same
   existing empty Plan. Flush all `PlanItem`/`PlanChange` effects, validate the
   safe detached outcome, overflow-check, and advance `Plan.revision` exactly
   once for a real state change. A genuine no-op retains the current non-null
   Plan revision in its Plan-scope Ledger outcome and must pass the existing
   savepoint/no-op mutation guard.
6. Insert the Plan-scope completed Ledger record, flush, and commit atomically.
   Any error rolls back Plan creation/update, all child rows, revision, and
   Ledger outcome together.

The coordinator does not silently regenerate or retry a stale draft. A caller
whose no-record execution becomes stale must start a new command/Prepare cycle.

### 11.5 Zero/one/duplicate Plan replay and identity matrix

| Case | Preflight/Commit result | Why |
| --- | --- | --- |
| 1. Zero Plans, first key | New first execution; Final Commit creates deterministic planned Plan ID, advances its revision once, writes Plan Ledger. | No record; zero lifecycle remains valid. |
| 2. Same key after first Plan | Replay. | Preflight finds matching record before any item/no-overwrite test. |
| 3. One existing empty Plan, new key | New first execution on that captured Plan. | Actual Plan ID/revision are preconditions only. |
| 4. Same key after later Plan revision | Replay. | Completed record is checked before current revision. |
| 5. Same key after another actor changes Preferences | Replay if the original committed; otherwise a fresh no-record retry is a new first execution with a fresh Prepare snapshot. | Facts are not part of command identity. |
| 6. Same key after items are present | Replay if a completed matching Ledger exists; otherwise new first execution rejects existing items. | Historical outcome is distinct from new no-overwrite validation. |
| 7. Same key, different accepted semantic parameter | `IdempotencyKeyReused`. | Any future accepted parameter is immutable canonical `command` content. |
| 8. Same key used by a Plan/Trip command | `IdempotencyKeyReused`. | Global Trip/actor/key uniqueness and scope check precede foreign HMAC parsing. |
| 9. Matching retained record expired | `IdempotencyRecordExpired`. | Expiry is a conflict, not permission to generate again. |
| 10. Replay after signing-key rotation | Replay when the stored key version remains retained; otherwise `UnknownHmacKeyVersion`. | Resource/Plan identity derives from stable namespaces, not signing keys. |
| Duplicate Plans at either checkpoint | `DuplicatePlans`; no replay/mutation. | This intentionally follows frozen Trip-kernel policy. |
| Revoked/removed/non-organizer retry | Existing credential/membership/role error; no historic body. | Replay never bypasses fresh authorization. |

### 11.6 Ledger ordering, revisions, and cross-aggregate rules

The completed Generator row has `aggregate_scope="plan"`, a non-null
`resulting_plan_revision`, and a null `resulting_trip_planning_facts_revision`,
which satisfies the existing mutual-exclusion check (models.py:284-338). A
state-changing generated or blocked result advances only `Plan.revision` once;
it never advances `Trip.planning_facts_revision`. That preserves the distinction
from Phase 4 input writers, which own Trip facts and dual revision advance when
a Plan exists. No B.1 migration or global uniqueness change is needed.

Plan and Trip scope share exactly `UNIQUE(trip_id, actor_membership_id,
idempotency_key)`. A found row of the other scope is `IdempotencyKeyReused`
before its HMAC, response, or revision fields are interpreted. No Ledger stores
provider payload, prompt, raw planning inputs, private constraint text, budget,
availability, credential, or trace. Key rotation retains verification keys for
the entire row-retention period, as frozen in Report 49.

### 11.7 Generator outcome contract

`plan.generate.v1` registers one trusted server-owned `OutcomeContract` version
1, using the existing `OutcomeContract(..., payload_validator=...)` extension.
First execution applies it before Ledger persistence; replay applies both the
stored historical version and the current trusted policy, exactly as durable.py
does at lines 452-475. An unsupported historical/current contract or a payload
that no longer meets the current allowlist raises `UnsupportedOutcomeContract`
and returns no historic body.

The exact top-level key set is:

```text
plan_id, status, days, blocked_reason, blocked_code,
generated_by, used_ai, planner_note
```

This is the current route's exact output (main.py:1430-1444). `status` is
`active` or `blocked`; `generated_by` is `planner`, `rules`, or `mixed`
(generator.py:1687-1693); and `used_ai` is boolean. `plan_id` is a nonempty
32-character persisted Plan ID. Nullable fields remain nullable only where the
current result does so. `blocked_code`, when non-null, is restricted to the
current Generator codes `DESTINATION_NOT_FOUND`, `NO_PLACE_CANDIDATES`,
`CONSTRAINTS_BLOCKED`, `DATES_INVALID`, or `AVAILABILITY_BLOCKED`; an unknown
code is rejected rather than stored/replayed.

Each `days` element has exactly `day_index` (positive integer), `day_date`
(ISO calendar date), and `items`. Each item has exactly the current `_days_out`
shape: `id`, `day_index`, `day_date`, `start_hour`, `title`, `local_title`,
`place`, `price_per_person`, `source`, `lat`, `lng`, `photo_url`, and `tags`
(generator.py:1793-1821). The validator enforces finite numeric values, date and
day-index consistency, ordered-by-start-hour items, model-backed bounds for
title/local title/place/source/photo URL, nullability matching current output,
and JSON arrays of at most 64 NFC-normalized tag strings of at most 128
characters. Latitude/longitude must be both null or valid geographic
coordinates. It must not invent unproven source enums for `source`.

`planner_note` is null or an array of exact
`{day_index, source, note, used_ai}` objects, as serialized by
`_planner_note_out` (main.py:470-476) from `DayPlannerNote`
(generator.py:139-156). `source` is `planner` or `rules`; `used_ai` is boolean;
each note is an NFC-normalized sanitized public explanation of at most 4,096
characters. `blocked_reason`, when non-null, is similarly NFC-normalized and at
most 2,048 characters. These defensive response-only bounds are part of v1 and
do not expose raw model/provider text; the B.2D3 codec must implement them.

The validator rejects extra/missing fields, private original constraint text,
private params/budgets/availability, member IDs/names, raw Geoapify/model
payloads, prompts, traces, diagnostics, authorization material, and secrets.
It returns an ordinary detached JSON mapping only; it does not introduce a
parallel serialization framework.

### 11.8 Protected HTTP and revision-discovery decision

The later gate is named `CADENSY_B2D_PROTECTED_GENERATOR_WRITES`. It is default
off. If requested while `APP_ENV` is production, the route fails closed with
controlled 503 before protected execution. Feature-off retains the exact Legacy
route body/status/error behavior and ignores malformed future protected headers,
because parsing occurs only inside the enabled branch.

Feature-on requires a valid `Idempotency-Key`; absent key is 428 and malformed
key is 422. It deliberately does **not** require `X-Expected-Plan-Revision`,
`X-Expected-Trip-Facts-Revision`, a Plan ID, or a new discovery endpoint. The
server captures current revision/lifecycle only after a no-record Replay
Preflight; those values are execution preconditions, never immutable retry
identity. This is smaller and correct for a no-body generation request, whereas
a client-supplied mutable revision would either make a replay unverifiable or
needlessly expose a new public contract.

No `X-Plan-Revision` result header is approved in B.2D. First execution and
historical replay preserve Legacy HTTP 200 and the exact body above; an internal
coordinator may return the stored resulting revision only to its adapter. A
new no-record stale lifecycle/revision rejection maps to safe 409; key reuse,
expiry, foreign scope, duplicate aggregate, unsupported outcome/key version,
and revision overflow map to controlled non-diagnostic 409; authorization is
401/403; invalid protected input is 422; lock/deadlock/serialization/database
failures are 503. No protected failure falls back to Legacy.

### 11.9 Destination normalization compatibility and coverage limit

Legacy `generate_plan` assigns `trip.destination = normalize_destination(...)`
before cache/provider lookup (generator.py:179-203), making an otherwise hidden
Trip planning-input write commit with generation. The protected protocol
intentionally does **not** persist that assignment: Prepare normalizes into its
detached DTO, and the Place service already canonicalizes its lookup value
(places/service.py:98-146). This preserves cache/provider behavior and public
Generator output while avoiding an unversioned Trip-facts mutation inside a
Plan-only command. Feature-off retains its Legacy persistence behavior.

If product policy later requires stored destination canonicalization, it must be
an explicit separately reviewed Trip planning-facts writer with its own revision
and durable contract before or apart from generation; B.2D must not silently
smuggle it into a Plan-only commit.

This local protocol also does not claim full preview consistency while legacy
membership, Scheduler, Organizer/decision, direct generator/evaluation, and
other uncovered writers can bypass revisions. It uses a full fresh projection
comparison to reject detected drift, but broader activation still requires the
remaining writer-coverage work.

### 11.10 PostgreSQL acceptance matrix

All listed concurrency cases require disposable localhost PostgreSQL, independent
Sessions/connections/backend PIDs, scheduler disabled, mock provider/model seams,
Events/queues rather than sleeps, and `pg_blocking_pids()` evidence while the
first Trip lock remains open. Mock-only tests are insufficient for row-lock
claims.

| Area / case | Expected result and critical assertion | Layer / blocker |
| --- | --- | --- |
| Zero Plan first -> same-key replay; one empty Plan -> replay | One Plan-scope Ledger/result revision; original detached result; no Provider/Planner on replay. | Real PostgreSQL; B.2D acceptance blocker. |
| Replay after items, later Plan revision, later facts revision, or Plan deletion/replacement | Authorized zero/one-cardinality replay returns original result and makes no write; duplicate Plan fails first. | Real PostgreSQL; B.2D acceptance blocker. |
| Same key changed semantic command; Plan/Trip scope collision; expired key; rotated/unknown HMAC key | Respectively key reuse, key reuse, expired typed error, replay or unknown-key typed error; no callback/external work. | Kernel plus PostgreSQL record tests; blocker. |
| Revoked/removed organizer replay; outcome tampering/current-policy tightening | No outcome body; fresh authorization/strict historical+current codec failure. | Real PostgreSQL plus route; blocker. |
| Two different generator keys / zero Plan | Observer proves second blocks on Trip; one creates/commits; second stale-rejects; no duplicate Plan/items/changes/Ledger. | Real PostgreSQL; blocker. |
| Two same-key commands | Both may perform External Work; only one commits; follower observes ledger and replays. | Real PostgreSQL; blocker. |
| Constraint/Preference or Plan lifecycle change during External Work | Final fresh projection rejects stale draft with no partial Domain row/revision/Ledger. | Real PostgreSQL; blocker. |
| Plan cardinality/items change during External Work | Reject; never attach to replacement/foreign Plan. | Real PostgreSQL; blocker. |
| Mid-item, PlanChange, Ledger insert, or overflow fault | Rollback Plan creation/update, every child, revision, and Ledger together. | Real PostgreSQL fault seam; blocker. |
| Provider block/failure/cache fallback; invalid Planner output | Independent requirements writer progresses; no Session/root lock spans Provider; rules/blocked behavior preserved; no partial items. | Focused fake-provider + PostgreSQL boundary; blocker. |
| Feature-off, existing Plan HMAC vectors, existing Trip HMAC vectors, Legacy and Pydantic suites | No regression; malformed future headers do not alter Legacy. | Route/regression; activation blocker. |

### 11.11 Updated B.2D1–B.2D5 sequence

| Slice | Permitted changes and prerequisites | Required acceptance / out of scope |
| --- | --- | --- |
| **B.2D1 — Detached Snapshot and Pure Draft Seam** | Generator lifecycle DTO/read projection and pure draft seam only. Requires this report; no Coordinator, route, cache ownership, or schema change. | Repeatable-read detached snapshot; no ORM escape, Plan insertion, provider/model call, or persisted normalization. Independently implementable now. |
| **B.2D2 — Provider/Cache Transaction Separation** | Narrow Place cache read/call/persist seams and parity tests. Requires B.2D1 DTO boundary. | No provider Session/root lock; cache fallback/order parity. No Ledger, Plan mutation, or public activation. |
| **B.2D3 — Generator Durable Coordinator and Exact Contract** | One narrow internal Coordinator, deterministic resource/Plan IDs, two checkpoint replay, v1 outcome codec, and command-contract tests. Requires B.2D1/B.2D2 and this frozen identity. | May not modify PlanTransactionKernel, TripTransactionKernel, existing durable envelopes, or routes. |
| **B.2D4 — Validate & Commit vertical slice** | Wire immutable draft to coordinator persistence and real PostgreSQL matrix. Requires accepted B.2D3. | Preserve active/blocked, PlanChange origins, no-overwrite, atomicity. No Scheduler/Organizer/direct-writer migration. |
| **B.2D5 — Default-off HTTP adapter** | Narrow `main.py` gate/error mapping after B.2D4 evidence. | Feature-off parity, non-production guard, header parsing only feature-on, no frontend/public activation. |

### 11.12 Remaining decisions, final verdict, and next authorization

The B.2D0B correction now resolves the formerly unmaterialized namespace and
canonical-name inputs. There is no unresolved identity, HMAC, first-Plan,
replay-order, revision-owner, or HTTP-precondition decision blocking B.2D3.
The retained prerequisites are implementation evidence, the outcome codec's
exact safe size caps, and the acceptance matrix above. Those are deliberately
test/code acceptance gates, not invitations to redesign the frozen protocol.
Full product activation remains blocked by the noted legacy writer-coverage gaps
and by the required PostgreSQL/regression evidence.

**Identity protocol verdict: IDENTITY PROTOCOL FROZEN — PASS.** This is a
documentation-level acceptance only. B.2D3–B.2D5 remain unimplemented; B.2D3
may resume only under separate engineering authorization, using the exact B.2D0B
constants and vectors above.
