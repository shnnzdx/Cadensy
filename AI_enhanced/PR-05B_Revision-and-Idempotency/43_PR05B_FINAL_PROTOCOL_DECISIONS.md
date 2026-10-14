# Cadensy PR-05B — Final Protocol Decisions

**Final review verdict: CONDITIONAL PASS.** The protocol is sufficiently specified
for a narrowly scoped, dormant **PR-05B.1 schema-only** implementation to begin in a
later authorized round. Full writer conversion and public activation remain blocked
by the later gates in this document.

This document finalizes only the six requested protocol decisions. It does not
repeat the complete audits in reports 41 and 42, and it does not modify code,
migrations, tests, configuration, databases, providers, or PR-04 artifacts.

## Review basis and verified source state

- Design basis retained without modification: `41_PR05B_REVISION_IDEMPOTENCY_DESIGN.md`
  and `42_PR05B_DESIGN_REVIEW_CORRECTIONS.md`.
- Source basis: `main` and `origin/main` both resolve to
  `17026adf4b77ced2280629f2b1584fd6892e420e` at review time.
- This target file was confirmed absent before creation.
- This is a source review, not an execution claim. No live RDS/AWS access, real
  provider call, migration creation/execution, or production-code change occurred.

The three scopes below are intentionally distinct:

- **schema-only B.1** — additive data model and deterministic local tests, dormant
  at runtime;
- **writer coverage** — all authoritative mutations join one lock/revision protocol;
- **activation** — clients may rely on revision/replay semantics only after coverage
  and fleet gates pass.

---

## 1. Final idempotency uniqueness contract

### Verified finding

Report 41 intentionally specified:

```text
UNIQUE(trip_id, actor_membership_id, idempotency_key)
```

It states that same actor/trip key reuse for another operation or resource is a
conflict, while another trip or actor is independent. See
`AI_enhanced/phase-0-audit-2026-10-03/41_PR05B_REVISION_IDEMPOTENCY_DESIGN.md:449-501`,
especially lines 462 and 466-490.

Report 42 proposed `(plan_id, actor_membership_id, operation_code,
idempotency_key)` at lines 714-738. That materially widens reuse scope: one raw key
could execute once per operation and Plan. It is not silently compatible with 41 and
is superseded by this explicit final decision.

Current main has no equivalent protocol: mutation inputs have no idempotency key
(`backend/app/api/main.py:184-239`) and current models have no ledger
(`backend/app/db/models.py:233-465`).

### Compared contracts

| Candidate | Cross-operation/resource reuse by same actor/trip | Lifecycle behavior | Decision |
| --- | --- | --- | --- |
| `UNIQUE(trip_id, actor_membership_id, idempotency_key)` | Existing key is found; different operation/resource makes HMAC differ, so it fails closed. | Stable before Plan creation and after resource lifecycle changes. | **Chosen.** |
| `UNIQUE(plan_id, actor_membership_id, operation_code, idempotency_key)` | Same raw key is permitted on another operation or Plan. | Needs a Plan before lookup and widens accidental reuse. | Rejected. |

### Final contract

The unique key is exactly:

```text
UNIQUE(trip_id, actor_membership_id, idempotency_key)
```

`operation_code`, `resource_kind`, and `resource_id` remain required stored fields
and required HMAC components; they are deliberately **not** unique-index columns.

| Reuse attempt | Required result |
| --- | --- |
| Same actor, same trip, operation/resource, and canonical command | Re-authorize, then replay stored safe outcome; no write runs. |
| Same actor/trip, different operation | `409 IDEMPOTENCY_KEY_REUSED`; do not execute or disclose another outcome. |
| Same actor/trip, same operation but different resource/body/revision | `409 IDEMPOTENCY_KEY_REUSED`. |
| Same actor, different trip | Independent scope; normal authorization and command processing apply. |
| Different actor, same trip | Independent scope; no actor can replay/discover another actor's result. |
| Resource later changes, is removed, or becomes terminal | Re-authorize, then replay original stored safe DTO; never re-execute or rehydrate. |

Trip scope is also lock-compatible: the final protocol locks Trip before Plan, and a
Trip-scoped record works for Plan create/find paths without presuming a one-Plan
invariant before duplicate-data preflight.

---

## 2. Final semantic fingerprint policy

### Verified field behavior

| Source evidence | Verified effect |
| --- | --- |
| `backend/app/api/main.py:184-220` | `ChangeOptionIn` carries item id, label, title, body, tradeoff, time/date/duration; `ChangeRequest` carries all patch fields, request, reason, and ordered options. |
| `backend/app/api/main.py:994-1052` | `_validated_change_options()` validates item identity and creates accepted label/title/body/tradeoff/patch data. |
| `backend/app/domain/decisions/orchestrator.py:189-238`, `634-658` | `_options_for()` persists alternative label, generated title/body, **tradeoff**, and patch in `DecisionRound.options`. |
| `backend/app/api/main.py:465-493` | `round_out()` returns stored `options`; label/tradeoff therefore affect stored response DTOs. |
| `backend/app/domain/decisions/orchestrator.py:241-350` | Patches can change PlanItem fields/derived day index and are appended to audit rows. |
| `backend/app/domain/decisions/orchestrator.py:437-502`, `792-908` | Reason can be persisted on a reopen Round; proposal/decision paths persist state, before/after JSON, logs, notices, and final effects. |

Submitted alternative `title` and `body` are currently recomputed in `_options_for()`
instead of being persisted verbatim. That implementation detail is **not** a reason to
omit them. Conversely, label and tradeoff must be included because they demonstrably
persist and are returned.

### Common HMAC envelope and normalization

Every browser-originated durable command uses this HMAC-SHA-256 envelope:

```json
{
  "fingerprint_version": 1,
  "operation_code": "plan.change.submit.v1",
  "trip_id": "server-resolved",
  "plan_id": "server-resolved-or-null-only-where-allowed",
  "actor_membership_id": "server-resolved",
  "resource": {"kind": "plan_item", "id": "server-resolved"},
  "expected_plan_revision": 42,
  "command": {"operation-specific canonical fields": "..."}
}
```

1. Scope/resource identity is server-resolved after fresh authorization.
2. Include every accepted public durable-command field, even where current code
   happens to ignore it. This is intentionally stricter than current write columns.
3. Preserve ordered arrays exactly. Accepted alternative order determines generated
   `alternative-N` ids and decision-option order.
4. Normalize Unicode to NFC, dates to ISO-8601, and numerics through one canonical
   JSON-number representation. Preserve whitespace unless endpoint logic explicitly
   transforms it. Explicit `null` and omitted fields stay distinct when the API makes
   them distinct (`local_title` is one example).
5. Persist only the HMAC and `hmac_key_version`, never raw free text. Exclude bearer
   token, headers, trace/request id, client timestamp, response fields, and the key.
6. Reject unknown mutable fields before canonicalization.

### Per-operation canonical command fields

| Operation code | Canonical fields beyond common envelope |
| --- | --- |
| `plan.change.submit.v1` | Full `ChangeRequest`: exact normalized patch, `request`, `reason`, and ordered full `ChangeOptionIn` objects: item id, label, title, body, tradeoff, start hour, date, duration. |
| `plan.item.add.v1` | `title`, `after_item_id`, `before_item_id`, `start_hour` from `main.py:231-235`. |
| `plan.booking.set.v1` | Target item id and `booked`; booking changes settledness/audit/notice. |
| `round.vote.v1` | Round id and option id; route may settle after vote. |
| `round.settle.v1` | Round id only; scheduler uses locked work-claim rather than browser ledger. |
| `notice.object.v1` | Notice id and any future explicit request text; current route has no body. |
| `proposal.decision.v1` | Proposal id and accepted/declined status. |
| `round.extend.v1` | Round id. |
| `proposal.escalate.v1` | Proposal id. |
| `proposal.deadlock.resolve.v1` | Proposal id and `keep` / `split` / `clear` action. |
| `plan.generate.v1` | Trip/Plan target plus every accepted generation input; only final database Commit is idempotent. |
| `preferences.save.v1` | Membership target and every accepted preference field. |
| `constraint.create.v1`, `constraint.update.v1`, `constraint.delete.v1` | Target/membership plus every accepted constraint field or target id. |
| `membership.remove.v1` | Target membership id and operation intent. |

Trip creation, invitations/authentication, comments, reminders, and dashboard-cover
fetch are outside the initial browser Plan-command ledger. Any one that changes
decision facts still must join writer coverage; cover has its separate protocol in
section 4.

### Required mismatch tests

For one key, a changed operation, target resource, expected revision, any patch
field (including `false`, explicit `null`, tags order, coordinates, or metadata),
request/reason, or **any** alternative field (including label/tradeoff/title/body)
must return `409 IDEMPOTENCY_KEY_REUSED` with no second effect. Alternative order
must also mismatch. Equivalent normalized Unicode, canonical numeric/date forms, and
object-key order must match; trace id, bearer token, and transport headers must not.

---

## 3. Final lock and authorization linearization contract

### Verified finding

Preliminary FastAPI authentication is real access control but is not the write
linearization point:

| Source evidence | Verified behavior |
| --- | --- |
| `backend/app/api/main.py:99-135` | `current_membership` authenticates an account/guest bearer and returns request-Session ORM `TripMembership`. |
| `backend/app/domain/auth.py:212-226` | Account session validity requires unrevoked, unexpired `AuthSession`. |
| `backend/app/domain/auth.py:238-259` | Guest validity requires token hash, Trip binding, unrevoked/unexpired GuestSession, and joined anonymous membership. |
| `backend/app/domain/trips/service.py:386-408` | Membership removal marks membership removed and revokes GuestSessions in one current transaction. |
| `backend/app/api/main.py:1286-1311`, `1396-1438` | Current mutation routes use the same dependency Session as preliminary authentication. |

### Final contract

#### Preliminary authentication is admission only

The request dependency produces a non-ORM `PreliminaryActorScope`: claimed trip id,
membership id, credential kind, and correlation id. The raw bearer remains only in a
private, non-logged credential proof used for revalidation. No request-Session ORM
object, relationship, or identity-map object may enter a fresh write command.

#### Fresh write transaction is the authorization linearization point

The write command opens a new Session and performs this order in one local
transaction:

```text
1. Lock target Trip FOR UPDATE.
2. Revalidate bearer in this Session.
   Account: lock AuthSession; verify not revoked/expired; lock/re-read membership
            for authenticated user + target Trip; verify role/status.
   Guest:   lock GuestSession; verify hash, target Trip, not revoked/expired;
            lock/re-read membership; verify joined anonymous membership.
3. Lock/create the authoritative Plan FOR UPDATE.
4. Locate/lock the Trip-scoped ledger row; compare HMAC or replay only now.
5. Lock child rows in stable order, re-read Domain facts, compare revision, and
   atomically commit domain effects, revision, and completed ledger outcome.
```

The canonical order is:

```text
Trip -> credential-session -> actor/affected membership -> Plan -> ledger
     -> PlanItem -> DecisionRound -> ChangeProposal -> ProposalDecision/Vote
```

Only required rows are locked. Membership removal and guest revocation must use the
same Trip-first order before changing membership/session rows. Account logout may lock
only `AuthSession` and never seek Trip after it; therefore it cannot create a reverse
cycle. A write locks Trip then credential, so the credential-row order defines the
account-session authorization point.

#### Idempotency replay comes after fresh authorization

Replay is never an authorization bypass. A removed member, expired/revoked Guest
credential, or revoked account session receives the current authorization failure and
does not receive historical outcome data. An active authorized retry gets the stored
safe DTO without a domain write, even if the referenced resource has changed.

### Interleaving proof

| Interleaving | Required outcome |
| --- | --- |
| Removal/revocation commits before writer locks Trip | Fresh revalidation sees removed/revoked state; no ledger lookup/effect occurs. |
| Writer locks Trip and revalidates first | Writer authorization linearizes first and may commit; removal waits on Trip, then revokes/removes. No write occurs after completed removal. |
| Guest logout/revoke locks credential before writer step 2 | Writer waits/re-reads revoked row and fails. |
| Writer locks credential first, then logout | Writer linearizes before logout; logout waits then revokes. This is ordered behavior, not a stale-check bypass. |
| Completed-ledger retry races with removal | Trip-root winner linearizes: removal first denies replay; active replay first returns stored DTO and makes no new write. |

This proof is conditional on every membership-removal, credential-revocation, and
aggregate writer joining the protocol. The preliminary dependency read alone does not
protect a later write.

### Required deterministic tests

Use independent PostgreSQL Sessions/connections and one-way events, never a shared
request Session or sleep:

1. Pause writer after preliminary authentication but before fresh root acquisition;
   commit membership removal/guest revocation in another Session; release writer;
   assert authorization failure and no ledger/Plan effect.
2. Pause writer after fresh credential/membership revalidation while it owns Trip;
   start removal and assert it waits; release writer; assert ordered success then
   removal, with no subsequent write after removal.
3. Repeat both orderings for guest logout and account-session revocation.
4. Seed completed ledger result, remove/revoke actor, retry same key, and assert no
   historical DTO leaks. Repeat with active actor and assert replay has no new effect.
5. Assert fresh write commands accept primitive scope/proof only; a request-Session
   `TripMembership`, `Plan`, or `PlanItem` argument fails the architecture test.

---

## 4. Trip cover compatibility with Trip -> Plan locking

### Verified finding

The current cover path holds a root lock across network I/O:

| Source evidence | Verified behavior |
| --- | --- |
| `backend/app/domain/trips/cover_service.py:37-68` | `ensure_trip_cover()` locks Trip `FOR UPDATE` then calls provider before persisting. |
| `backend/app/domain/trips/cover_service.py:72-111` | Unsplash uses an eight-second HTTP client and returns `None` on provider failure. |
| `backend/app/domain/trips/service.py:140-181` | Dashboard loops through trips and invokes the helper in its request Session. |
| `backend/app/api/main.py:638-651` | Dashboard route commits after service completion. |
| `backend/app/domain/trips/cover_service.py:1-6`, `26`, `43-61` | Provider failure leaves neutral cover state and negative-caches failure for seven days. |

Cover is not a Domain-classification fact. It must not advance `Plan.revision`, but
its Trip lock must be compatible with writers that take Trip then Plan.

### Final design: claim, fetch, conditionally persist

This is a later compatibility refactor, not a B.1 ledger feature. It requires its own
additive Trip fields, for example:

```text
cover_fetch_claim_id           nullable opaque UUID
cover_fetch_claimed_at         nullable timestamptz
cover_fetch_destination        nullable text
```

1. **Claim transaction:** in an independent short Session, lock Trip `FOR UPDATE`.
   Return if a cover exists, failure remains negatively cached, or a claim lease is
   active. Otherwise write random claim id, current time, exact normalized
   destination; commit and close.
2. **Provider phase:** with no Session, transaction, Trip lock, or Plan lock open,
   call existing `fetch_unsplash_cover()`. Preserve validation, attribution,
   selection, and `None` fallback semantics.
3. **Conditional persistence:** in a new short Session lock Trip and persist only if
   claim id still matches, no cover is set, and current destination equals claimed
   destination. Success writes current cover/attribution fields, fetched timestamp,
   and clears claim. Failure writes only the existing negative-cache timestamp and
   clears matching claim, preserving neutral fallback. Destination/claim mismatch
   discards stale result and must not negative-cache the new destination.
4. **Lease recovery:** an expired claim can be replaced. A late original provider
   result then fails claim-id predicate and cannot overwrite newer data. This prevents
   duplicate/stale persistence, not distributed exactly-once provider requests after
   crash or lease expiry.

Dashboard code must use a session-factory/service boundary instead of retaining its
request Session across the remote call. A Plan mutation may proceed during provider
latency; it contends only with the short Trip claim/persist transactions. Cover takes
Trip only, never Plan, so it cannot invert Trip -> Plan ordering.

### Required deterministic tests

| Test | Required evidence |
| --- | --- |
| Concurrent dashboard fetch | A commits claim then blocks fake provider. B observes active claim, makes zero provider calls, returns neutral/current cover. Release A: one persistence succeeds. |
| Cover versus Plan mutation | Block provider phase and prove no root lock is held. A Plan writer obtains Trip -> Plan and commits without provider-latency wait. |
| Late/stale result | A claims destination X and blocks; destination changes or B replaces expired claim; release A; it writes neither cover nor failure timestamp for Y. |
| Provider failure fallback | `None` writes matching negative-cache timestamp only; immediate repeat makes no call and UI receives neutral fields. |
| Overwrite protection | Late A and current B race final persistence; claim id + destination predicate permits only B to set cover. |

---

## 5. Final preview consistency decision

### Compared architectures

| Proof dimension | A. Full immutable snapshot-backed tools | B. Revision-bracketed fresh reads |
| --- | --- | --- |
| Authoritative-fact coherence | One captured immutable projection is intrinsically coherent. | Coherent if bounded reads are bracketed by unchanged monotonic Plan revision and every relevant writer advances it atomically. |
| Writer coverage | Required; snapshot cannot repair an unseen writer. | Required; any bypass invalidates proof. |
| ORM identity map | Safe only after DTO conversion and capture-Session closure. | Safe through fresh short Session per tool and final classification; no ORM object crosses boundaries. |
| Legacy compatibility | Needs snapshot implementation for every Legacy tool and new provenance arrangement. | Preserves `ReadTripCapability` / `RuntimeReadTool` shape; handlers become bounded fresh-read closures. |
| Pydantic compatibility | Compatible but overbuilds present one-tool surface. | Compatible without widened authority; Pydantic uses scoped `get_current_plan`. |
| Replacement candidates | Must snapshot candidates/cache/provider data or redefine Legacy tool. | Treat candidate as non-authoritative provenance; locked Domain validation retains sole authority. |
| Privacy | Large projection increases risk of serializing private facts. | Reuses scoped safe serializers per short handler. |
| Provider/cache independence | Requires separate prepare phase to avoid provider inside snapshot transaction. | Provider/cache remain outside root lock and cannot determine Domain verdict. |
| Cost/surface | New snapshot schema for every tool and potentially large request memory. | Small read transactions per tool plus revision checks; no Session spans model latency. |

### Verified source constraints

- Runtime contracts expose scoped read tools, not an ORM Session
  (`backend/app/agents/runtime_contract.py:63-90`), and suggestions lack fresh/write
  authority (`99-105`).
- Current Legacy capability holds one worker Session across the operation
  (`backend/app/agents/legacy_runtime.py:104-119`), so it is not already bracketed.
- Pydantic restricts itself to `get_current_plan`
  (`backend/app/agents/pydantic_runtime.py:196-248`).
- Legacy replacement lookup uses provider-backed place service
  (`backend/app/agents/tools.py:977-1003`; `backend/app/domain/places/service.py:83-132`),
  and chat requires matching Legacy provenance for replacement patches
  (`backend/app/domain/chat/service.py:1070-1156`).

### Chosen contract: B, revision-bracketed fresh reads

**B is the smallest architecture satisfying the complete authoritative-plan
consistency proof.** It is available only after full writer coverage; it must never
be advertised as complete before then.

```text
1. Fresh short read Session captures r0 = Plan.revision; returns no ORM instance.
2. RevisionBracketedReadTripCapability preserves current public descriptors.
   Each tool opens its own short read-only repeatable-read Session, verifies r0,
   reads scoped safe facts, converts to DTO, and closes.
3. Tool revision mismatch is terminal stale; no proposed change may be returned.
4. Fresh short repeatable-read Session verifies r0, loads classifier facts, and runs
   deterministic classification; it closes.
5. Fresh scalar read verifies r1 == r0. Mismatch discards candidate/verdict as
   preview_stale; it never attaches r1 to an r0 verdict.
6. Apply reauthorizes and locks Trip/Plan, compares expected revision, and
   reclassifies from locked facts. A commit after step 5 is caught at apply.
```

The proof is: if all mutations capable of changing any tool/classifier fact advance
the same monotonic revision in their atomic transaction, final `r1 == r0` proves no
such committed change occurred during accepted reads. Separate short snapshots thus
represent one aggregate state. A transaction not committed by final verification is
safe: apply detects/serializes it if it commits first.

Replacement candidates remain non-authoritative: Legacy provenance may be retained
only outside root locks, and returned candidate data cannot decide a Domain verdict or
bypass locked apply revalidation. Pydantic stays fail-closed for replacement fields
as current source does (`backend/app/domain/chat/service.py:1168-1185`). This neither
removes a Legacy tool nor grants a model a revision/snapshot write token.

### Required deterministic tests

1. Multiple Legacy tool calls with no writer commit return revision-`r0` safe facts
   from independent Sessions; assert no Session spans model/provider run.
2. Commit covered mutation between tool calls; later tool/final check sees mismatch
   and response has no candidate/verdict.
3. Commit after final verification but before apply; apply stale-rejects or
   reclassifies under root lock and creates no stale effect.
4. Preload request-Session ORM rows, then run preview; tools/classifier derive only
   from fresh Sessions.
5. Legacy replacement test proves provider/cache outside root lock and provenance
   cannot make a stale preview authoritative; Pydantic replacement remains rejected.
6. Frozen PR-04 runtime, observation, privacy, timeout, and isolated-Pydantic
   regressions pass unchanged before capability behavior is accepted.

---

## 6. Frozen minimum B.1 schema and revision contract

### Final schema contract

The minimum additive B.1 contract is:

```text
plan
  revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0)

mutation_idempotency
  id                       primary key
  trip_id                  FK trip, NOT NULL
  actor_membership_id      FK trip_membership, NOT NULL
  idempotency_key          VARCHAR(128), NOT NULL
  operation_code           VARCHAR(80), NOT NULL
  resource_kind            VARCHAR(40), NOT NULL
  resource_id              VARCHAR(64), NOT NULL
  fingerprint_version      SMALLINT NOT NULL
  request_hmac             CHAR(64), NOT NULL
  hmac_key_version         VARCHAR(32), NOT NULL
  outcome_contract_version SMALLINT NOT NULL
  response_status          SMALLINT NOT NULL
  outcome_json             JSONB NOT NULL  -- safe response only
  resulting_plan_revision  BIGINT NULL
  created_at               TIMESTAMPTZ NOT NULL
  completed_at             TIMESTAMPTZ NOT NULL
  expires_at               TIMESTAMPTZ NOT NULL
  UNIQUE(trip_id, actor_membership_id, idempotency_key)
  INDEX(expires_at)
```

Frozen implications:

- `Plan.revision` is application/domain-owned and advances exactly once per successful
  covered aggregate mutation in the same transaction as effects.
- There is no trigger that guesses relevance. Cover persistence, comments, and
  reminders do not advance it unless product later makes them Domain inputs.
- A ledger row is written only with completed safe outcome in the committing
  transaction; rollback leaves no visible ledger/effect.
- HMAC keys remain outside database. B.1 uses deterministic local key version `v1`;
  production secret rotation/retention configuration is needed before B.2/B.4, not
  an excuse to access a real secret for B.1.
- `plan.trip_id` uniqueness is **not** in B.1. It requires separate duplicate-data
  preflight and remediation before any unique constraint is proposed.

### A. Schema-only B.1 entry gates

These gates are satisfied by this finalized design, subject to a later explicit user
authorization to implement:

1. Uniqueness scope is frozen to report-41 Trip/actor/key semantics.
2. Fingerprint version, HMAC key version, safe outcome boundary, expiry index, and
   expected-revision envelope are frozen above.
3. Revision default/backfill is additive `0`; B.1 exposes no public enforcement.
4. B.1 excludes Plan uniqueness, so disposable PostgreSQL schema work requires no
   live duplicate-data scan and no RDS access.
5. Current tests explicitly use guarded local `TEST_DATABASE_URL`, separate it from
   `DATABASE_URL`, and disable scheduler/AI (`backend/tests/conftest.py:30-45`).
6. B.1 tests cover schema shape, canonical HMAC with a fixture key, composite
   uniqueness semantics, and rollback atomicity; no real provider/database call.

### B. Full writer-coverage gates

Before B.3 makes revision/preview/apply public, all classifier/decision-fact writers
must use fresh authorization, Trip -> Plan lock, fresh re-read, one revision advance,
and applicable ledger/work-claim policy. This includes manual insertion, generation,
booking, notice/round/confirm paths, votes/settlement, proposal decisions/expiry,
constraints/preferences, membership/electorate changes, organizer terminal actions,
and scheduler paths.

Scheduler/system work has no browser idempotency key. Its equivalent is Plan-root
work claiming plus locked terminal-state recheck. Provider/cache work never runs
while root locks are held.

### C. Later production-activation gates

1. Every writer-coverage and independent-connection PostgreSQL race test passes.
2. Revision-bracketed Runtime implementation passes frozen PR-04 regressions in both
   dependency environments; no authority/contract regression is accepted.
3. All application and scheduler fleet versions understand B.1 schema; no old write
   path can bypass kernel.
4. HMAC active/verify-only rotation, retention duration, cleanup ownership, replay
   policy, lock-timeout mapping, and operational monitoring are approved.
5. Duplicate-Plan preflight is complete before any `plan.trip_id` unique constraint.
6. Cover claim refactor, if delivered, passes its compatibility tests and does not
   change Plan revision semantics.

---

## Remaining blockers and final decision

### Remaining blockers

The following do not block dormant schema-only B.1 coding, but they block writer
rollout or production activation:

- implementation and proof of complete writer-coverage matrix;
- local PostgreSQL multi-connection contention tests for every covered family;
- revision-bracketed capability implementation plus frozen PR-04 regression evidence;
- production HMAC secret owner, retention interval, cleanup operation, and rotation
  runbook;
- duplicate-Plan data preflight before any uniqueness migration;
- cover claim refactor if current long Trip lock materially contends with deployed
  Plan writers.

### Final verdict

**CONDITIONAL PASS.**

**Can PR-05B.1 schema-only implementation begin? Yes — in a separately authorized
round, and only with the frozen additive schema/contract in section 6.** It must not
activate expected-revision enforcement, alter public mutation behavior, add
`plan.trip_id` uniqueness, call real providers, or claim full concurrency protection.

**PR-05B implementation has NOT started in this round.**
