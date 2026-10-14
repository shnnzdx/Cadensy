# Cadensy PR-05B — Phase 0: Pre-Plan Trip-bound Final Protocol Freeze

**Status:** Protocol freeze only, 2026-10-10. This is the authoritative implementation contract for the future Trip-bound Preferences / Constraints extension. It supersedes the design proposals in report 48; reports 41–47 remain historical/frozen evidence for the existing Plan-bound protocol. No application code, test, Alembic migration, database operation, provider call, commit, or deployment was performed in this phase.

## 1. Executive Decision Summary

The frozen direction is a dedicated TripTransactionKernel, LockedTripContext, TripIdempotentCommand, TripDurableCommandResult, and a sibling Trip durable collaborator. The existing PlanTransactionKernel and DurableCommandKernel remain explicit existing-Plan implementations; they are not made nullable or generic.

The protected Preferences / Constraints writers use X-Expected-Trip-Facts-Revision. A real planning-fact write increments Trip.planning_facts_revision; if exactly one authoritative Plan exists, it also increments Plan.revision in the same transaction, invalidating a captured Plan preview/apply token. Zero Plans is valid. Duplicate Plans fail closed.

The existing Trip/actor/idempotency-key ledger key remains global across both aggregate scopes. The new scope/result representation is additive. A small explicit branch is required in Plan durable replay to reject a found Trip-scope record as key reuse before attempting Plan replay; Plan HMAC bytes do not change.

## 2. Frozen Scope and Non-goals

**In scope for later phases:** only these four Trip-bound writers:

1. PUT /api/trips/{trip_id}/preferences/me
2. POST /api/trips/{trip_id}/constraints
3. PATCH /api/constraints/{constraint_id}
4. DELETE /api/constraints/{constraint_id}

**Not in scope:** Plan creation, generator migration, B.2D Prepare/Validate/Commit, a UNIQUE(plan.trip_id) migration, non-Preferences writers, public activation, provider calls, generalized aggregate abstractions, ledger cleanup, or production deployment.

Confirmed source boundaries:

- PlanTransactionKernel locks Trip then credentials/membership then all Plans, and rejects zero or multiple Plans (backend/app/domain/transactions/kernel.py:249-310).
- B.2B fingerprints plan_id and expected Plan revision, returns a Plan revision, and rejects a stored ledger outcome without one (backend/app/domain/transactions/durable.py:182-219,301-401,404-486).
- Preferences/Constraints intentionally work with no Plan; refresh/conflict work returns early when none exists (backend/app/domain/preferences/service.py:157-184,354-454).
- Trip currently has no revision; Plan.revision is a non-negative BIGINT (backend/app/db/models.py:111-134,239-262).

## 3. Aggregate and Revision Model

### Confirmed facts

The B.1 ledger is unique on (trip_id, actor_membership_id, idempotency_key) and is not operation/resource unique (backend/app/db/models.py:265-301; B.1 migration backend/alembic/versions/6f77e775d201_pr05b1_dormant_schema_foundation.py:36-75). Report 43 froze that scope: same key used for another operation/resource by the same actor in the same Trip is a conflict, not a second command (43_PR05B_FINAL_PROTOCOL_DECISIONS.md:71-81).

### Approved protocol decision

Add Trip.planning_facts_revision BIGINT NOT NULL DEFAULT 0 CHECK (>= 0). It represents mutable requirements facts used for planning: a member’s Preferences, constraints, private companion existence/visibility, and the derived refresh consequence. It is distinct from Plan.revision, which guards the existing Plan aggregate and its preview/apply facts.

| Plan rows locked for Trip | Genuine fact change | True no-op | Same-key identical replay |
| --- | --- | --- | --- |
| 0 | Increment Trip facts revision once. | Increment neither. | Increment neither. |
| 1 | Increment Trip facts revision once **and** Plan revision once in the same outer transaction. | Increment neither. | Increment neither. |
| >1 | Fail closed with typed duplicate-aggregate error before replay or mutation. | Same. | Same. |

The client supplies exactly one expected Trip facts revision for every protected Trip-bound command, regardless of whether a Plan currently exists. Existing Plan-bound commands keep X-Expected-Plan-Revision; no writer substitutes one token for the other.

The two increments for one Plan are required by the frozen preview proof in report 43: a requirements change alters facts used to classify/apply a Plan change, so any captured Plan revision must become stale (43_PR05B_FINAL_PROTOCOL_DECISIONS.md:360-384,471-478).

## 4. Canonical Lock Order

Every Trip-bound command uses a new, owned, short-lived Session and one outer transaction. Request-session ORM objects, raw bearer tokens, and commit APIs do not cross into the callback. The exact order is:

    1. Trip FOR UPDATE
    2. AuthSession or GuestSession FOR UPDATE; fresh validation
    3. authenticated actor TripMembership FOR UPDATE; fresh validation
    4. additional required TripMembership rows, sorted by id
    5. all Plan rows for the Trip FOR UPDATE, sorted by id
       - zero: Trip-only command mode
       - one: authoritative Plan
       - more than one: fail closed
    6. existing MutationIdempotency row FOR UPDATE by (trip, actor, key), if present
    7. owner-scoped Preference / MemberConstraint / MemberConstraintPrivate rows
    8. callback SAVEPOINT
    9. atomic revision increments and completed-ledger insert
    10. COMMIT; otherwise rollback and close

This is compatible with the implemented Plan order, Trip -> credential -> membership -> Plan -> ledger, at kernel.py:258-301 and durable.py:303-402. The Trip root serializes requirements writes with existing protected Plan writers. Credential-only logout/revocation may lock the credential first; as documented in report 45, the Trip command waits and then freshly revalidates rather than assuming every revoker takes Trip first.

On a state-changing callback, check both applicable BIGINT values before increment. Any overflow, callback error, child/refresh failure, ledger failure, flush failure, or commit failure rolls back child rows, private rows, refresh state, Trip facts revision, Plan revision, and ledger together. No in-kernel automatic retry is allowed.

## 5. Guest and Account Authorization Contract

### Approved membership policy

joined and preferences_submitted are the only active Guest membership statuses. removed, any other status, revoked/expired Guest session, wrong Trip or membership binding, a Guest membership with a non-null user_id, wrong credential kind, and invalid bearer all fail closed. Account semantics remain “membership status is not removed,” absent a demonstrated security defect.

This corrects a current inconsistency:

| Current path | Source rule |
| --- | --- |
| Account membership lookup | status != removed: backend/app/domain/auth.py:283-293 |
| Legacy Guest lookup | only status == joined: auth.py:238-259 |
| Locked Plan Guest lookup | only status == joined: backend/app/domain/transactions/kernel.py:366-404 |
| Preference save | writes preferences_submitted: preferences/service.py:157-185 |

### Future implementation requirements

The same frozen allowlist must be applied to all three paths:

1. legacy auth.membership_for_guest_token();
2. PlanTransactionKernel._locked_guest_actor(); and
3. TripTransactionKernel Guest reauthorization.

main.current_membership() remains account-first: an Account token with a guest-like prefix is first validated as an Account, and a Guest fallback occurs only after Account authentication fails (backend/app/api/main.py:139-175). This policy does not authorize any other membership status.

Required future tests: normal Account/Guest behavior; Account token prefix compatibility; joined and preferences-submitted Guest success only with their bound session/Trip/membership; removed, revoked, expired, wrong-Trip, wrong-membership, wrong-kind and Guest-with-user rejection; preliminary-to-locked removal/revocation races; and no ledger replay disclosure after revocation or removal.

## 6. Protected HTTP Request/Response Contract

### Request contract

Each of the four routes receives optional raw header strings at the FastAPI boundary, preserving feature-off parsing behavior. When its dedicated Trip-bound gate is requested in a production environment, return 503. When the gate is enabled outside production, require:

    Idempotency-Key
    X-Expected-Trip-Facts-Revision

The header is parsed only in the feature-on branch, matching current Plan-route adapters’ pattern (backend/app/api/main.py:790-857). Feature-off routes do not parse, reject, or otherwise react to a malformed protected header; their Legacy DTO and status behavior remains unchanged. Protected failures never fall back to Legacy.

| Condition | Frozen HTTP outcome |
| --- | --- |
| Protected gate requested in production | 503, non-diagnostic protected-unavailable message |
| Missing idempotency/revision header | 428 |
| Malformed, boolean-like, negative, or BIGINT out-of-range Trip revision | 422 |
| Stale Trip facts revision | 409 plus X-Current-Trip-Facts-Revision response header |
| Same key, changed command or aggregate scope | 409 |
| Invalid/expired/revoked credential | 401 |
| Freshly unauthorized/removed membership | 403 |
| Absent or foreign-owned constraint | 404 with the existing non-enumerating message |
| Duplicate Plan aggregate / expired ledger / unsupported outcome or fingerprint/key version / revision overflow | 409, controlled non-diagnostic response |
| Lock timeout, deadlock, serialization, integrity/database failure | 503, controlled retry-safe response |

The mapping reuses the established Plan-route categories: current protected adapters map stale/key/expired to 409, invalid durable input to 422, credential/membership to 401/403, aggregate/replay issues to 409, and lock or database availability failures to 503 (backend/app/api/main.py:895-916,1553-1570). New typed Trip stale/overflow errors are justified because StalePlanRevision and PlanRevisionOverflow are semantically Plan-specific (backend/app/domain/transactions/durable.py:61-69).

### Revision discovery and recovery

No new endpoint is needed. The existing authenticated, owner-only GET /api/trips/{trip_id}/preferences/me already enforces current membership and Trip scope (backend/app/api/main.py:2125-2132) and is the frozen discovery path. After the schema/route phase, it returns the current integer in the additive response header:

    X-Trip-Facts-Revision: N

Its JSON body is unchanged, preserving feature-off compatibility. This token is metadata, not a credential. On a 409, the client discards its stale state, calls this same fresh GET, reads the header, and submits a new idempotency key for a new semantic command. It must not silently retry the stale command.

Every successful protected command returns its stored resulting Trip facts revision in the same X-Trip-Facts-Revision response header. A durable replay returns the revision stored with the original completed outcome, not the aggregate’s then-current revision. Constraint response bodies retain their approved legacy shapes; Preference uses the special safe acknowledgement below.

## 7. Preference / Constraint No-op Semantics

| Command | Frozen state-change rule | Safe durable result |
| --- | --- | --- |
| preferences.save.v1 | Every new key is a new submission. save_mine() assigns submitted_at = _now() on every successful execution (preferences/service.py:168-183), so even equal field values are a real persisted change. Update status according to Legacy, increment Trip and (if one Plan) Plan revision. | { "submitted": true } plus response revision header. Same-key replay writes neither timestamp nor revisions. |
| constraint.create.v1 | Always state-changing: insert public constraint and private companion atomically. | { "id": "…", "conflicts": [...] } plus header. |
| constraint.update.v1 | Preserve omitted-field and empty PATCH acceptance. It changes state if params/importance differs **or** any authoritative derived value such as Plan.needs_refresh changes. If neither changes, it is a genuine no-op. | { "id": "…", "conflicts": [...] } plus header; no-op may retain one safe outcome but increments neither revision. |
| constraint.delete.v1 | Successful owned deletion is state-changing; delete private companion then public row as Legacy does (preferences/service.py:408-423). A new key after deletion is the existing non-enumerating missing/foreign 404. | { "deleted": true } plus header. |

Timestamp precision must use the persisted ORM/database value after flush—not Python object identity or a clock guess—to determine the new Preference save is a real write. The protocol does not redefine repeated submits as no-ops.

Every callback must return a server-reviewed CommandResponse; if it reports state_changed=False, it must stage no persistent change. Reuse B.2B’s nested savepoint and no-op guard that observes ORM flushes and PostgreSQL command tags (durable.py:333-354,583-637). Explicit flush, ORM insert/delete, commented DML, CTE DML, or any other observed write with a false no-op result is an error and the savepoint/outer transaction rolls back.

## 8. Stable Resource Identity and HMAC Envelope

constraint.create.v1 has no stable child row before first execution. Its frozen identity is therefore:

    operation_code = "constraint.create.v1"
    resource_kind  = "member_constraint_collection"
    resource_id    = locked_actor_membership_id

The actual MemberConstraint.id is generated only inside the first successful callback, returned in the safe result, and retained for replay. The first key creates one public/private pair. Same key/same immutable command returns that original ID, even after later deletion. Same key/different payload, expected revision, operation, resource, or scope is key reuse and creates no second pair.

The Trip HMAC v1 envelope is immutable canonical JSON over:

    {
      "fingerprint_version": 1,
      "operation_code": "server-selected",
      "aggregate_scope": "trip_planning_facts",
      "trip_id": "locked Trip",
      "actor_membership_id": "locked actor",
      "resource": {"kind": "server-selected", "id": "server-selected"},
      "expected_trip_facts_revision": 0,
      "command": {"operation-specific normalized fields": "..."}
    }

For Preference save, the command includes every accepted request field. For Constraint create it includes kind, normalized params, importance, private original text, and visibility; update includes target plus omitted/present semantics for params and importance; delete includes target ID. Unicode, numeric, date, list order, explicit null, and omission use the existing canonical snapshot rules (backend/app/domain/idempotency.py:35-115,118-170). Private data enters only the keyed HMAC calculation and is not placed in the ledger record beyond its HMAC digest.

The existing Plan HMAC envelope remains byte-for-byte unchanged: it includes Trip ID, Plan ID, actor membership, resource, expected Plan revision, and command (durable.py:464-488). Fingerprint format version is distinct from HMAC key version.

## 9. Exact Ledger Schema Evolution

### Confirmed source shape

MutationIdempotency.resulting_plan_revision is currently **nullable** in both ORM and B.1 migration (backend/app/db/models.py:299; migration 6f77e775d201_pr05b1_dormant_schema_foundation.py:51). Therefore Trip records do not require an unsafe nullability alteration of that existing column.

### Frozen additive DDL contract

The future migration/ORM must implement equivalent PostgreSQL semantics:

    ALTER TABLE trip
      ADD COLUMN planning_facts_revision BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE trip
      ADD CONSTRAINT ck_trip_planning_facts_revision_nonnegative
      CHECK (planning_facts_revision >= 0);

    ALTER TABLE mutation_idempotency
      ADD COLUMN aggregate_scope VARCHAR(32) NOT NULL DEFAULT 'plan',
      ADD COLUMN resulting_trip_planning_facts_revision BIGINT NULL;

    ALTER TABLE mutation_idempotency
      ADD CONSTRAINT ck_mutation_idempotency_aggregate_scope
      CHECK (aggregate_scope IN ('plan', 'trip_planning_facts')),
      ADD CONSTRAINT ck_mutation_idempotency_plan_result_nonnegative
      CHECK (resulting_plan_revision IS NULL OR resulting_plan_revision >= 0),
      ADD CONSTRAINT ck_mutation_idempotency_trip_result_nonnegative
      CHECK (resulting_trip_planning_facts_revision IS NULL
             OR resulting_trip_planning_facts_revision >= 0),
      ADD CONSTRAINT ck_mutation_idempotency_result_scope
      CHECK (
        (aggregate_scope = 'plan'
          AND resulting_plan_revision IS NOT NULL
          AND resulting_trip_planning_facts_revision IS NULL)
        OR
        (aggregate_scope = 'trip_planning_facts'
          AND resulting_plan_revision IS NULL
          AND resulting_trip_planning_facts_revision IS NOT NULL)
      );

ORM/server-default parity is mandatory: BigInteger, non-null Python default and server_default="0" for Trip revision; String(32), non-null Python/default and server default plan for ledger scope; nullable BigInteger for both result values. The existing unique constraint and expiry index remain exactly:

    UNIQUE(trip_id, actor_membership_id, idempotency_key)
    INDEX(expires_at)

The result check intentionally stores only the command aggregate’s result. A Trip-facts command with one Plan atomically increments Plan revision but records only resulting Trip facts revision; it need not add a Plan result to the response or violate mutual exclusion.

### Backfill, malformed rows, upgrade/downgrade

Existing rows receive aggregate_scope='plan'; no old Plan HMAC, outcome, revision, or expiry is rewritten. Before adding the mutual-exclusion check, the migration must preflight existing Plan records with null/negative resulting_plan_revision. Current source already treats a null Plan result as unsafe on replay (durable.py:427-430), but the migration must not silently reclassify, invent a revision, or delete them. If such rows exist, schema completion blocks pending a separately approved quarantine/retention remedy.

Upgrade is additive only and uses a localhost disposable migration harness in a future phase. Downgrade is safe only before any trip_planning_facts ledger row exists; after such rows exist, a downgrade that drops their only result revision would destroy audit/replay meaning and must fail closed or be marked unsupported.

## 10. Legacy Plan Durable Compatibility

Plan durable lookup must retain its current (trip_id, actor_membership_id, idempotency_key) query dimensions (durable.py:303-315), because the key is global across scopes. It requires exactly these future changes:

1. New Plan ledger rows explicitly write aggregate_scope='plan'.
2. After locating an existing record, before Plan HMAC/result handling, reject every scope other than plan as IdempotencyKeyReused.
3. For plan, execute the original fingerprint/replay path unchanged.

The Trip durable collaborator uses the symmetric rule: a found non-Trip scope is deterministic key reuse; only trip_planning_facts records may be verified with the Trip HMAC and returned as TripDurableCommandResult.

Both collaborators preserve stored HMAC-key-version verification, supported fingerprint-version checks, and historical **and current** server-owned outcome-contract validation (durable.py:404-456). Neither reinterprets a result from the other aggregate scope.

## 11. Privacy-Safe Outcome Contracts

The existing owner-only read_mine() response includes personal dates, budgets, visibility, interests, timestamp, constraint params, and private original text (backend/app/domain/preferences/service.py:93-137). It is safe over an authenticated owner response but is not safe to persist as shared ledger JSON.

| Operation / trusted outcome v1 | Frozen body persisted/replayed | Explicitly forbidden from outcome_json |
| --- | --- | --- |
| preferences.save.v1 | { "submitted": true } | all Preference fields, submitted_at, constraints, original text, visibility, budgets, dates, params, credentials |
| constraint.create.v1 | { "id": opaque_id, "conflicts": safe_conflicts } | original text, visibility, raw params, membership identity, credential, diagnostics |
| constraint.update.v1 | { "id": opaque_id, "conflicts": safe_conflicts } | same exclusions |
| constraint.delete.v1 | { "deleted": true } | target history, private data, diagnostics |

safe_conflicts must be an explicit versioned server codec, not merely the current top-level OutcomeContract allowlist. Its allowed nested fields are limited to the legacy scan_conflicts() output that is confirmed non-private: constraint_id, code, generic safe text, scope, item_id, item_title, day_date, and settledness (preferences/service.py:471-519). Before code, the phase must prove no nested value admits private params/identity. The codec rejects unknown fields and invalid types on both first execution and replay.

Feature-on Preference save is intentionally a response difference: it returns the minimal acknowledgement, never full read_mine(). Feature-off returns the unchanged Legacy body. A client fetches current personal details only via freshly authorized GET /preferences/me. All outcome schemas are server-selected, versioned, and validated before ledger persistence/replay; arbitrary service dictionaries are never accepted.

## 12. Expiry, Retention, and Key Reuse

### Confirmed source behavior

Current B.2C writer adapters supply a 24-hour command expiry—for example Booking and Change Proposal use datetime.now(timezone.utc) + timedelta(hours=24) (backend/app/domain/transactions/booking.py:77-95; change_proposal.py:125-136). DurableCommandKernel._replay() verifies HMAC/key/operation then rejects an expired record with IdempotencyRecordExpired (durable.py:404-428). The schema has an expires_at index but source contains no ledger cleanup job (models.py:273-301; migration 6f77e775d201...py:70-75).

### Frozen initial policy

1. **Command expiry:** Trip commands use the same server-chosen 24-hour expiry unless a later reviewed per-operation policy changes it. An expired retained record never replays as success and returns controlled 409.
2. **Ledger retention:** Phase 0 adds no cleanup or automated deletion. A record retained past command expiry remains a key conflict because current replay lookup finds it and rejects it; it is not a license to reuse a key.
3. **HMAC key retention:** because current replay verifies the stored HMAC key before checking expiry, a verification key must remain available for at least as long as its ledger records are retained, unless a later reviewed code change reverses that safe ordering.
4. **Reuse after deletion:** no guarantee is made. If a future cleanup removes a row, the database no longer has evidence to reject reuse; cleanup ownership, retention duration, legal/audit policy, and any reuse policy remain a deferred operational decision. Do not claim distributed exactly-once.

## 13. Generator / B.2D Integration Boundary

All protected requirements writers serialize through Trip so their aggregate facts have a linear order. Existing Plan writers retain Plan revision checks. With one Plan, a requirements change increments Plan revision, so a preview captured before it becomes stale. This does not make the current generator safe.

generate_plan() reads facts, creates/reuses Plan, calls place/provider/planner work in the request Session, and persists items without this root protocol (backend/app/domain/plans/generator.py:160-205,316-324,1712-1757). Provider or model calls must never run while Trip root is locked.

B.2D must later use detached Prepare input, capture Trip facts revision outside the root lock, perform provider/model work outside transaction/root locks, and in a fresh short Commit transaction lock Trip, reauthorize, re-read Plan state, and reject/reprepare if captured facts revision changed. It also owns the duplicate-Plan preflight/policy. No UNIQUE(plan.trip_id) is authorized here.

## 14. Error Mapping Matrix

| Source condition | Domain/transaction category | HTTP |
| --- | --- | --- |
| Missing/foreign constraint | existing owner-scoped NotYours behavior | 404, same non-enumerating public message |
| Invalid request/header/unsupported fingerprint | existing validation categories; Trip revision parser | 422 |
| Missing idempotency or Trip revision header | protected adapter precondition | 428 |
| Stale Trip facts / same-key mismatch / expired record | Trip stale error / IdempotencyKeyReused / IdempotencyRecordExpired | 409 |
| Duplicate Plans / unsupported outcome or retained key version / unsafe outcome / either revision overflow | aggregate or replay safety unavailable | 409 |
| Invalid/revoked/expired credential | InvalidCredential | 401 |
| Removed/unauthorized membership | MembershipNotAuthorized | 403 |
| Lock timeout/deadlock/serialization/integrity/database failure | existing kernel classified errors | 503 |
| Protected gate requested in production | adapter production guard | 503 |

Messages must be stable and non-diagnostic: no HMAC material, raw private payload, SQLSTATE, database details, membership identifiers, or foreign resource existence is exposed. This matrix follows established protected route mappings at main.py:895-916 rather than creating public exception detail.

## 15. PostgreSQL Acceptance Matrix

All concurrency tests use independent PostgreSQL Sessions/connections, Events or queues (not sleeps), distinct backend PIDs, and pg_blocking_pids(waiter_pid) while the first Trip root lock remains held.

| Scenario | Required invariant |
| --- | --- |
| Zero-Plan Preference / constraint create-update-delete | No Plan is created; exactly one Trip facts increment per real change; public/private rows and ledger commit atomically. |
| One-Plan constraint change after Plan preview captured at r | Both revisions increment once; Plan command at r gets stale 409 and produces no effect. |
| Duplicate Plans | Fail before ledger lookup/replay/mutation; no revision or child change. |
| Same create key, same payload | One public/private pair and one ledger record; replay returns original ID and original Trip-revision header. |
| Same create key, different payload or scope | Follower blocks at Trip then gets 409; no second public/private pair. |
| Replay after created constraint deletion | Fresh authorization then original safe result; no recreation or revision increment. |
| Equal Preference values/new key | Fresh persisted submitted_at, membership behavior, and applicable revisions exactly once. |
| Empty constraint PATCH | Existing accepted behavior; increment only if real/derived persisted state changed; otherwise no-op result/replay only. |
| False no-op attack | explicit flush, ORM insert/delete, commented/CTE DML all roll back; no unversioned write. |
| Guest/Account authorization | Account-first prefix test; joined/preferences-submitted Guest success; revoked/removed/cross-Trip/wrong-kind rejects before replay. |
| Outcome privacy | Database ledger contains only approved safe shape; no budgets, dates, raw params, original text, visibility or credentials. |
| Legacy Plan regression | Existing Plan HMAC bytes/replay remain valid; cross-scope key collision is deterministic 409. |
| B.2D generation race (deferred) | Facts change during provider/model work invalidates captured preparation before Commit. |

## 16. Phase 1–5 Implementation Sequence

1. **Phase 1 — schema foundation:** reviewed additive migration/model tests only; preflight ledger historical rows; no public routing.
2. **Phase 2 — Trip transaction kernel:** fresh Session/context, exact lock order, auth allowlist, zero/one/duplicate Plan tests; no HTTP activation.
3. **Phase 3 — Trip durable collaborator and Plan scope branch:** immutable Trip command/HMAC, global ledger collision behavior, savepoint/no-op safety, outcome contracts, expiry/key rotation; preserve Plan HMAC/replay tests.
4. **Phase 4 — four writer vertical slices:** default-off/non-production routes, protected headers/response headers, privacy codecs, owner scope, no-op tests.
5. **Phase 5 — B.2D separately approved:** generator Prepare/Validate/Commit, Plan creation/duplicate Plan policy, then eventual coverage/activation review.

## 17. Deferred Operational Decisions

- Exact ledger retention duration, cleanup owner, audit/legal disposition, and post-deletion idempotency-key reuse policy.
- Production HMAC key custody/rotation runbook, provided it preserves verification keys for retained records.
- Feature-gate variable names and rollout telemetry; all must remain default-off and reject production request for activation until a later authorization.
- Duplicate-Plan data preflight, remediation and any future uniqueness constraint.
- B.2D generator commit/retry policy and generation-specific response/idempotency.

## 18. Final Freeze Readiness Verdict

**PROTOCOL FROZEN.**

The aggregate, revision transitions, lock order, Guest status rule, header/read contract, stable constraint-create identity, ledger schema, replay boundaries, safe outcomes, error mapping, and implementation phases are internally consistent and source-backed. Phase 1 schema work remains **not authorized** until explicitly approved. The deferred operational decisions above do not alter the frozen core protocol, but they must be resolved before production activation or ledger cleanup is proposed.
