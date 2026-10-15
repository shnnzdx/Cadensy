# PR-06B.0M — Membership Lifecycle Impact Audit

Status: **CHANGES REQUIRED before PR-06B.1.** This is a documentation-only audit. It does not change application code, schema, migrations, infrastructure, or provider behavior.

## Decision frozen by this audit

`TripMembership.id` is the Membership **incarnation**. An account that rejoins after removal receives a new Membership row and ID; the old row is never reactivated. Historical records keep their old Membership ID. In particular, old private Cadensy conversations never become readable through the new Membership.

This is an ownership boundary, not merely an `active` filter. Active participant queries must exclude `status = 'removed'`; historical/audit foreign keys must retain it.

## Evidence and the confirmed rejoin defect

| Finding | Evidence | Consequence |
|---|---|---|
| Account uniqueness currently covers every non-null account membership, including removed ones. | [models.py:148](../../backend/app/db/models.py#L148) | A fresh account rejoin cannot be inserted until the index changes. |
| Legacy account invite join locates a row by Trip and User without status and returns it. The integrity-error retry repeats that lookup. | [service.py:343](../../backend/app/domain/trips/service.py#L343), [service.py:362](../../backend/app/domain/trips/service.py#L362) | A removed account is returned as if it joined, while remaining unauthorizable. |
| The protected join adapter locks the same unfiltered row and decides whether to advance planning facts from `existing is None`. | [membership_join.py:45](../../backend/app/domain/transactions/membership_join.py#L45), [membership_join.py:55](../../backend/app/domain/transactions/membership_join.py#L55) | A rejoin would neither create an incarnation nor advance dependent Plan revisions. |
| Account auth and Guest auth both reject removed memberships; protected kernels repeat the check after locks. | [auth.py:104](../../backend/app/domain/auth.py#L104), [auth.py:294](../../backend/app/domain/auth.py#L294), [trip_kernel.py:254](../../backend/app/domain/transactions/trip_kernel.py#L254) | Existing revocation is sound at the auth seam, but join currently fails to create a newly active identity. |
| Membership removal preserves the row and revokes Guest sessions in its transaction. | [service.py:386](../../backend/app/domain/trips/service.py#L386), [membership_remove.py:53](../../backend/app/domain/transactions/membership_remove.py#L53) | Preservation is right for audit history; reactivation is not. |

The present repeat-join test proves only an active-account retry returns the same Membership; it does not cover removal then rejoin. [test_invites.py:267](../../backend/tests/test_invites.py#L267)

## Lifecycle state diagram

```text
account absent
    | valid invite + authenticated account identity
    v
ACTIVE Membership A ── remove ──> REMOVED Membership A (immutable historical owner)
    |                               |  auth/read/write/replay denied
    | active repeat join             |  old conversation remains owned by A
    +---------- same A <-------------+
                                        | valid later rejoin
                                        v
                                  ACTIVE Membership B (new ID)
                                        | owns only B's new conversations
                                        +-- remove --> REMOVED Membership B

Guest active membership -- credential expired/revoked --> inaccessible Guest membership
    (V1 deliberately has no Guest cross-device recovery or Guest-to-account adoption)
```

`removed` is a terminal authorization state for that Membership incarnation. It is not an invitation state and must not be made active by invite replay, role change, account login, or a later User row lookup.

## Join-path audit and intended V1 behavior

| Path | Current behavior | Required V1 contract | Writer/concurrency requirement |
|---|---|---|---|
| First account join | Public endpoint accepts an email, finds or creates `User`, then creates membership. [service.py:332](../../backend/app/domain/trips/service.py#L332) | Create one active Membership incarnation only after proving the caller controls the existing account (or completing a separately authenticated sign-up). A supplied email alone is not proof. | Lock Trip root, then Invite, then account's active membership rows; insert or return the one active row. |
| Repeated active account invite | Returns existing row. | Return the active incarnation idempotently; do not advance revisions. | Same lock order and active-only lookup. |
| Removed account rejoin | Returns removed row (bug). | Insert a fresh active Membership, preserve removed row and all its children. | Treat as a membership mutation; increment `Trip.planning_facts_revision` and the sole Plan revision exactly once. |
| Concurrent account joins | Legacy code relies on old global unique index and catches `IntegrityError`; protected code serializes on Trip root but has the wrong lookup. | Exactly one active incarnation results. Both callers receive that active ID; only its creation advances revisions once. | Keep `Trip -> Invite -> membership rows -> Plan` ordering used by [trip_kernel.py:180](../../backend/app/domain/transactions/trip_kernel.py#L180). The legacy unprotected path must be removed/routed to the same writer before the old index is dropped. |
| First/repeated Guest join | Every call creates a Guest membership and bounded Guest session. [service.py:316](../../backend/app/domain/trips/service.py#L316) | Preserve V1 non-recoverable Guest semantics. A valid existing Guest bearer accesses only its own membership; a new invite redemption is a new Guest. | Existing Trip-root join must keep session creation and revision mutation atomic. |
| Expired/revoked invite | `_valid_invite` rejects either. [service.py:258](../../backend/app/domain/trips/service.py#L258) | Reject before any User, Membership, session, conversation, or revision write. | Re-read and lock Invite after Trip root as the protected path already does. |
| Membership removal racing join | Removal preserves/deauthorizes membership. | The root-lock order decides first. If removal wins, a later valid rejoin creates a new incarnation; if join wins, removal removes the newly active target only through its normal authorized operation. | No direct service call may bypass the root writer. |

### Account proof and Guest adoption

The invite join endpoint is public and passes `body.email` to `join_invite`; it is not an authenticated Account-join proof. [main.py:1606](../../backend/app/api/main.py#L1606) A bearer/Membership ID cannot substitute for authentication. The current model comment says a future Guest binding would update a Guest row, but the audit found no production adoption flow assigning `membership.user_id`; only a test mutates that field. [models.py:152](../../backend/app/db/models.py#L152), [test_trip_transaction_kernel.py:663](../../backend/tests/test_trip_transaction_kernel.py#L663)

Therefore V1 must explicitly choose the safe protocol:

1. An existing account joins/rejoins only with a valid Account bearer whose `AuthSession.user_id` is the target User; the server derives the user from the bearer, not email input.
2. A new account must first complete an authenticated account-creation/verification flow outside invite redemption, then use that bearer.
3. Guest-to-account binding is **not implemented and is out of scope for PR-06B.1**. Do not update a Guest Membership's `user_id`, and do not transfer its conversations, preferences, votes, or private text to an account.
4. If product later needs adoption, it requires a separately reviewed, authenticated, one-time transfer protocol and a privacy decision for every historical child row.

## Current-active versus historical reference matrix

| Data/use | Existing relationship/query | V1 treatment after removal/rejoin |
|---|---|---|
| Account dashboard and bearer resolution | Account queries exclude removed rows. [service.py:148](../../backend/app/domain/trips/service.py#L148), [auth.py:294](../../backend/app/domain/auth.py#L294) | **Current-active:** retain active-only behavior; return Membership B after rejoin. |
| Guest sessions | `GuestSession.membership_id` FK; active session is revoked on removal. [models.py:85](../../backend/app/db/models.py#L85), [service.py:406](../../backend/app/domain/trips/service.py#L406) | **Historical/security:** retain old session records, revoke old credentials, never attach them to B. |
| Preferences and private constraints | `Preference` (one per membership), `MemberConstraint` FKs. [models.py:193](../../backend/app/db/models.py#L193), [models.py:215](../../backend/app/db/models.py#L215) | **Private historical:** retain under A; B starts empty. Current constraint reads/classification must use active memberships only. |
| Member roster / counts | Member list and dashboard/invite counts currently include every row. [preferences/service.py:532](../../backend/app/domain/preferences/service.py#L532), [service.py:183](../../backend/app/domain/trips/service.py#L183), [service.py:287](../../backend/app/domain/trips/service.py#L287) | **Current-active:** exclude removed rows, so rejoin does not appear as two current people. An explicit history/audit view may include both. |
| Constraints used by planner | Constraint load joins every Trip membership. [orchestrator.py:114](../../backend/app/domain/decisions/orchestrator.py#L114) | **Current-active:** exclude removed rows; removed constraints cannot influence a current plan. |
| Comments and append-only plan changes | Comment has membership FK; plan change stores a scalar actor membership id. [models.py:407](../../backend/app/db/models.py#L407), [models.py:425](../../backend/app/db/models.py#L425) | **Historical/audit:** preserve attribution to A; B must not be represented as author of A's activity. |
| Votes and decision rounds | Vote FK/unique is per membership; vote denominator currently counts all rows. [models.py:477](../../backend/app/db/models.py#L477), [vote.py:207](../../backend/app/domain/transactions/vote.py#L207) | **Mixed:** preserve A's recorded vote historically. Define the electorate snapshot for already-open rounds before implementation; new/current electorate counts exclude removed members and B is not retroactively a voter. |
| Change proposals / decisions | Proposal requester and decision membership FKs; affected-member selection currently uses every membership. [models.py:492](../../backend/app/db/models.py#L492), [orchestrator.py:873](../../backend/app/domain/decisions/orchestrator.py#L873) | **Mixed:** preserve past requester/decision IDs. Pending proposals require an explicit removal policy (expire/recompute under lock); B does not inherit A's pending decision. |
| Notifications | Recipient is a membership ID. [models.py:548](../../backend/app/db/models.py#L548) | **Historical/delivery:** retain A records but do not deliver A-targeted notifications to B. |
| `MutationIdempotency` / durable commands | Stores actor Membership identity in the ledger. [models.py:276](../../backend/app/db/models.py#L276) | **Security/audit:** never replay A's command result under B. B starts a distinct key namespace. |
| Future conversations | Proposed `owner_membership_id`. [01_PR06B_PERSISTENCE_ARCHITECTURE_DECISION.md](01_PR06B_PERSISTENCE_ARCHITECTURE_DECISION.md) | **Private historical:** A's conversations are inaccessible after removal and never attached to B. |

The two incomplete current-active areas are planner constraints and group decision counts/affected-set selection. They are documented blockers because blindly adding `status != 'removed'` to only one endpoint creates inconsistent outcomes. The implementation must centralize an `active_memberships_for_trip`/electorate policy and apply it where product semantics require current participants.

## Schema and migration design (planned, not executed)

The desired PostgreSQL index is:

```sql
CREATE UNIQUE INDEX CONCURRENTLY ux_trip_membership_active_account
  ON trip_membership (trip_id, user_id)
  WHERE user_id IS NOT NULL AND status <> 'removed';
```

Proposed PR-06B.1 migration/runbook:

1. **Preflight, read-only:** check for more than one active account membership per `(trip_id, user_id)`, null/unknown statuses, orphaned membership foreign keys, and current Alembic/index state. Save only counts/IDs in a restricted migration report; do not log private content.
2. **Deploy writer first or place invite joins in controlled maintenance:** every account join must use the corrected Trip-root writer with active-only lookup. The old direct `join_invite` path cannot coexist safely with a loosened index.
3. **Build the new partial unique index concurrently** outside Alembic's ordinary transaction block, with a validation/retry plan for invalid concurrent indexes. The old stronger index remains until the new index is valid, so there is no uniqueness gap.
4. **Drop `one_membership_per_user_per_trip` only after validation**, then update ORM metadata/name to match. Do not create a global `UNIQUE(trip_id, user_id)` constraint.
5. **Exercise locked rejoin:** insert B only after locking the Trip root/Invite/active membership set; preserve A and all child FKs. Increment planning/Plan revisions once when B is created.
6. **Verify postflight:** index predicate, exactly one active membership for each account/trip, old FKs unchanged, new B has no inherited child records, and A's credentials/content are inaccessible.

Downgrade must fail safely, not delete history. Before recreating the old all-time unique index, a downgrade migration must detect any `(trip_id, user_id)` with more than one non-null Membership and raise a clear exception. Such rows are the expected result of a valid rejoin; an operator must first choose a separately approved archival/reversibility procedure. Dropping the new index or collapsing Membership rows automatically is prohibited.

## Authorization and privacy threat assessment

| Threat | Required control |
|---|---|
| Removed account reuses invite/email to obtain old conversation | Account proof comes from bearer; rejoin creates B; conversation auth requires active owner Membership ID, never User/Trip alone. |
| Organizer reads a removed member's history | Organizer role never widens `owner_membership_id`; no history list/query is Trip-wide. |
| Old Guest bearer works after removal | Revoke Guest sessions in removal transaction and recheck session + membership state for every request. |
| Late provider completion writes into deleted/removed owner | Conversation completion locks and reauthorizes current Membership, Conversation status, lease fence, and deletion marker before persistence. |
| Concurrent join doubles active membership or revision | One Trip-root serialized writer plus partial unique active index; no legacy bypass. |
| Rejoin inherits private preferences, votes, notifications, or conversations | Do not copy/repoint child rows. B begins with no private history; only explicitly designed current-group policy sees B. |
| Email spoofing claims an existing account | Do not treat invite email as account identity; require account-authenticated join/rejoin. |

## Minimum regression suite for PR-06B.1

1. First authenticated account join creates one active Membership and advances each planning revision once.
2. Repeated authenticated join returns the same active Membership and advances neither revision.
3. Remove A, revoke its sessions, then rejoin authenticated account: create B, retain A as removed, and advance revisions exactly once.
4. A's bearer, Guest credential, and durable command/idempotency replay fail after removal; B cannot read A's preferences, constraints, comments-as-self, votes, notifications, or conversations.
5. A concurrent pair of rejoin calls yields one B and one revision increment; test with real PostgreSQL process/lock evidence like existing root-writer tests.
6. Expired/revoked invite creates no User/Membership/session and cannot be used to rejoin.
7. Guest invite behavior remains nonrecoverable across devices; no Guest-to-account transfer occurs.
8. Current roster/counts/constraints/electorate behavior treats removed rows correctly; historical records retain A IDs.
9. Migration upgrade verifies the partial index and FK preservation; downgrade encounters valid A+B history and fails with its intended safety error.
10. Cadensy conversation tests prove delete/removal/rejoin never resurrect or expose A-owned messages, including late fenced completion.

## Blockers and gate recommendation

**CHANGES REQUIRED.** PR-06B.1 must not begin until its implementation design accepts all of the following:

- a bearer-authenticated account join/rejoin protocol (or a separately approved identity-verification design), replacing email-as-identity;
- one corrected, Trip-root serialized account join writer and retirement/routing of legacy bypasses;
- the active-only unique-index migration/runbook with safe downgrade refusal;
- explicit current-electorate behavior for removed members in constraints, votes, and pending proposals;
- a no-adoption V1 decision for Guest-to-account transfer;
- Membership-incarnation ownership enforced in Conversations and every replay/result-persistence path.

No production state was inspected or changed, and no assertion is made that these controls are deployed.
