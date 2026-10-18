# PR-06B.0M — Membership Lifecycle Impact Audit

Status: **FINAL CONTRACT CLOSED — READY FOR PR-06B.1 IMPLEMENTATION PLANNING.** This is a documentation-only audit. It does not change application code, schema, migrations, infrastructure, or provider behavior.

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
ACTIVE Membership A ── active repeat join ──> ACTIVE Membership A (same ID)
    |
    | remove
    v
REMOVED Membership A (terminal, immutable historical owner)
    | auth/read/write/replay denied; old conversation remains owned by A
    | valid later rejoin creates a different identity
    v
ACTIVE Membership B (new ID) ── remove ──> REMOVED Membership B

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

### A. Authenticated Account Join — final contract

**Decision.** Account Join is a distinct authenticated operation. Add `POST /api/invites/{token}/account-join`, requiring a valid non-Guest `Authorization` bearer. The endpoint resolves `AuthSession.user_id` with `auth.user_for_token`, derives the target User exclusively from that session, and passes that User ID to the one Trip-root join/rejoin writer. It accepts neither `email` nor a client-chosen account ID. It does not require an existing Membership in the target Trip, so an authenticated account can accept a new invite or rejoin after removal.

Guest Join remains `POST /api/invites/{token}/join`, public and limited to `display_name`; it creates only a Guest Membership/session. A supplied `email` on that public route receives `409 account_join_requires_authenticated_session`, not an email-based lookup or User creation. A Membership ID, Trip ID, invite token, or a Guest bearer is never account proof.

**Source evidence.** The current route is public, accepts `email`, and calls the email-based service path. [main.py:565](../../backend/app/api/main.py#L565), [main.py:1604](../../backend/app/api/main.py#L1604), [service.py:309](../../backend/app/domain/trips/service.py#L309) The current UI sends exactly that public request for both choices. [TripAppState.jsx:761](../../trip/src/final/TripAppState.jsx#L761), [FinalApp.jsx:1406](../../trip/src/final/FinalApp.jsx#L1406) `user_for_token` already validates session expiration/revocation and returns the server-owned User. [auth.py:223](../../backend/app/domain/auth.py#L223)

**Safe `password_hash IS NULL` handling.** Existing null-password Users were created by the unsafe historical invite path; their email is not ownership proof. The `User` model permits the null state, while registration rejects a duplicate email and login cannot establish a password credential for it. [models.py:54](../../backend/app/db/models.py#L54), [auth.py:196](../../backend/app/domain/auth.py#L196), [auth.py:213](../../backend/app/domain/auth.py#L213) V1 classifies these rows as *unclaimed legacy identities*: Account Join must return `409 account_activation_required`, even if the caller knows that email. It must not attach a password, mint an AuthSession, merge a Guest, or reuse the row. A future account-activation/credential-recovery flow requires independently verified email possession and separate security approval. Until then, the person may use the Guest path; no account continuity is promised for that legacy email.

**Frontend/backend migration sequence.**

1. Release the authenticated backend route and corrected Trip-root writer first. Keep public Guest Join working; make legacy public email payloads fail explicitly and safely.
2. Update `TripAppState` with an account-authenticated request seam for Account Join; do not use `publicRequestJson` for it. It sends the current Account bearer but no email.
3. Update `FinalApp` so an existing signed-in account sees “Join as <account name>”; a signed-out user is sent through normal sign-in/registration with the invite token preserved as a routing hint, then returns to Account Join. Guest remains a separate explicit action.
4. Use only server-returned profile/Membership data to adopt Trip context. No client echo of email/name establishes identity.
5. Remove the legacy email field after all shipped clients use the split paths. No compatibility flag may revive email-as-identity.

**Regression tests.** Cover: valid account session joins/rejoins as its session User; missing/expired/revoked/Guest bearer cannot Account Join; public email payload creates no User/Membership; Guest Join still returns only a bounded Guest bearer; an active account retry is idempotent; removed A becomes new B; and null-password User receives activation-required with no session/membership mutation.

Guest-to-account binding remains **not implemented and out of scope**. The model comment describes a future update, but the audit found no production adoption flow assigning `membership.user_id`; only a test mutates it. [models.py:152](../../backend/app/db/models.py#L152), [test_trip_transaction_kernel.py:663](../../backend/tests/test_trip_transaction_kernel.py#L663)

## Current-active versus historical reference matrix

| Data/use | Existing relationship/query | V1 treatment after removal/rejoin |
|---|---|---|
| Account dashboard and bearer resolution | Account queries exclude removed rows. [service.py:148](../../backend/app/domain/trips/service.py#L148), [auth.py:294](../../backend/app/domain/auth.py#L294) | **Current-active:** retain active-only behavior; return Membership B after rejoin. |
| Guest sessions | `GuestSession.membership_id` FK; active session is revoked on removal. [models.py:85](../../backend/app/db/models.py#L85), [service.py:406](../../backend/app/domain/trips/service.py#L406) | **Historical/security:** retain old session records, revoke old credentials, never attach them to B. |
| Preferences and private constraints | `Preference` (one per membership), `MemberConstraint` FKs. [models.py:193](../../backend/app/db/models.py#L193), [models.py:215](../../backend/app/db/models.py#L215) | **Private historical:** retain under A; B starts empty. Current constraint reads/classification must use active memberships only. |
| Member roster / counts | Member list and dashboard/invite counts currently include every row. [preferences/service.py:532](../../backend/app/domain/preferences/service.py#L532), [service.py:183](../../backend/app/domain/trips/service.py#L183), [service.py:287](../../backend/app/domain/trips/service.py#L287) | **Current-active:** exclude removed rows, so rejoin does not appear as two current people. An explicit history/audit view may include both. |
| Constraints used by planner | Constraint load joins every Trip membership. [orchestrator.py:114](../../backend/app/domain/decisions/orchestrator.py#L114) | **Current-active:** exclude removed rows; removed constraints cannot influence a current plan. |
| Comments and append-only plan changes | Comment has membership FK; plan change stores a scalar actor membership id. [models.py:407](../../backend/app/db/models.py#L407), [models.py:425](../../backend/app/db/models.py#L425) | **Historical/audit:** preserve attribution to A; B must not be represented as author of A's activity. |
| Votes and decision rounds | Vote FK/unique is per membership; vote denominator currently counts all rows. [models.py:477](../../backend/app/db/models.py#L477), [vote.py:207](../../backend/app/domain/transactions/vote.py#L207) | **Mixed:** preserve A's recorded vote historically. The final V1 decision-window policy below prevents an electorate change mid-round; new/current counts exclude removed members and B is never retroactively a voter. |
| Change proposals / decisions | Proposal requester and decision membership FKs; affected-member selection currently uses every membership. [models.py:492](../../backend/app/db/models.py#L492), [orchestrator.py:873](../../backend/app/domain/decisions/orchestrator.py#L873) | **Mixed:** `ProposalDecision` rows are the immutable affected-member snapshot. While nonterminal, membership changes are rejected; B never inherits A's pending decision. |
| Notifications | Recipient is a membership ID. [models.py:548](../../backend/app/db/models.py#L548) | **Historical/delivery:** retain A records but do not deliver A-targeted notifications to B. |
| `MutationIdempotency` / durable commands | Stores actor Membership identity in the ledger. [models.py:276](../../backend/app/db/models.py#L276) | **Security/audit:** never replay A's command result under B. B starts a distinct key namespace. |
| Future conversations | Proposed `owner_membership_id`. [01_PR06B_PERSISTENCE_ARCHITECTURE_DECISION.md](01_PR06B_PERSISTENCE_ARCHITECTURE_DECISION.md) | **Private historical:** A's conversations are inaccessible after removal and never attached to B. |

### B. Current electorate and historical decisions — final V1 policy

**Decision.** A Trip enters a **decision window** while it has either an `open` `DecisionRound` or a `ChangeProposal` in `waiting_affected_members`/`escalated`. During that window, any operation that would change the active Membership set — first Account Join, Account rejoin, Guest Join, or Membership removal — returns `409 membership_change_pending_decision`. An idempotent retry that returns the same already-active account Membership is not a set change and may succeed. Invite preview/revocation remains separate.

This is the minimal fail-closed policy: a former member is never dynamically removed from an in-flight denominator, a new Membership is never added to one, and no current-member query must reinterpret a historical vote. The protected writer must test this predicate under the existing Trip-root transaction and lock the relevant nonterminal Round/Proposal children after its established `Trip -> memberships -> Plan` locks; no legacy direct writer may bypass it.

**Historical semantics.** New Rounds record immutable `DecisionRound.electorate_count` (nonnegative; nullable only for pre-cutover historical rows) at opening from active Memberships. All-voted checks, reopened-round majority, and closed-round response display use that stored count, not a later live roster. Votes retain their original `trip_membership_id`. New Proposals already materialize their affected electorate as `ProposalDecision` rows; completion continues to require every recorded decision to be accepted, and removal/rejoin cannot rewrite those rows. Completed/declined/expired/resolved decisions are append-only historical facts and are never changed by later Membership lifecycle events.

For existing open/pending rows, deployment must fail closed: do not enable active-only decision queries or lifecycle mutations until a preflight confirms there are no nonterminal legacy Rounds/Proposals (or they have reached their ordinary deadline/terminal domain outcome). Existing closed Rounds lacking `electorate_count` must expose an explicit unavailable historical-count field rather than recomputing against today's roster.

**Why this policy.** The current code dynamically counts every Membership for early settlement and result display, and dynamically builds the Proposal affected set. [vote.py:207](../../backend/app/domain/transactions/vote.py#L207), [vote.py:222](../../backend/app/domain/transactions/vote.py#L222), [orchestrator.py:133](../../backend/app/domain/decisions/orchestrator.py#L133), [orchestrator.py:873](../../backend/app/domain/decisions/orchestrator.py#L873) Simply filtering `removed` would let a non-voter's removal make `len(votes) >= total`, causing accidental early settlement; recomputing a closed round would rewrite history. `settle_round` also uses the live count for reopened-majority semantics. [orchestrator.py:757](../../backend/app/domain/decisions/orchestrator.py#L757) Membership removal is already a Trip-root durable mutation, so the gate fits the current transaction architecture. [membership_remove.py:35](../../backend/app/domain/transactions/membership_remove.py#L35)

**Alternatives rejected for V1.** Dynamic active denominators are unsafe for early settlement. Recomputing/auto-expiring affected Proposal decisions on removal changes an already-issued confirmation contract. A full per-round Membership-ID snapshot would be valid but is more invasive than the decision-window gate plus an immutable count. An emergency-removal override would need separate product approval because it must define whether to expire each affected proposal and how to handle an open Round; it is not silently introduced here.

**Regression tests.** Cover a blocked remove/join/rejoin during open Round and waiting/escalated Proposal; no new Membership/revision/session is written on `409`; an active retry remains idempotent; open-round early settlement cannot be triggered by lifecycle change; reopened majority uses opening count; pending proposals cannot apply after a membership-change attempt; and later removal/rejoin leaves closed Round counts, Vote ownership, ProposalDecision ownership, and terminal outcomes unchanged.

### C. Recovery idempotency — final contract

**Decision.** Recovery is a retry of one durable Submission, never a second message. The original `conversation_submission` owns its immutable `user_message_id`, optional sole `assistant_message_id`, original submission idempotency key, current `attempt_number`, `lease_expires_at`, and monotonic `fence`. A new `conversation_submission_recovery` row is keyed by `(submission_id, recovery_key)` and records the accepted attempt/fence; it has no replacement text or new user-message reference.

**Implementation implication.** Under the Conversation/Submission lock, the same Recovery Key is a pure durable replay. A different key is rejected while the lease is live. Only after expiry can a new key atomically create one recovery record, increment attempt/fence, and claim a new lease. Completion must match both current attempt and fence before it can attach the one assistant result. Thus the durable effect is exactly-once (at most one committed assistant reply), while an ambiguous crash after sending a Provider request can produce repeated external Provider calls across fenced attempts. Provider invocation is deliberately not claimed exactly-once.

**Source evidence.** Today the client sends mutable history to `/chat`, not a durable submission identity. [TripAppState.jsx:1032](../../trip/src/final/TripAppState.jsx#L1032) The only existing durable idempotency model is `MutationIdempotency`, which belongs to protected domain mutations rather than Provider execution. [models.py:276](../../backend/app/db/models.py#L276) There is therefore no source implementation to reinterpret; the new recovery record is required rather than an extension of client history or `MutationIdempotency`.

**Regression tests.** Verify repeated/concurrent same Recovery Keys return one attempt, different keys before expiry get `409`, a reclaimed attempt fences the prior completion, no recovery inserts a second user message, and two Provider responses can result in only one committed assistant message. Inject an uncertain Provider timeout and assert that a second Provider invocation is possible but is not represented as duplicate durable output.

### D. Expiry and session scope — final contract

**Decision.** `retention_expires_at` is an authorization/read/context boundary, not a best-effort cleanup signal: after expiry, list/discovery/read/context/submission/replay/recovery/completion fail closed even if the purge scheduler has not run. Membership removal immediately removes Trip access for that Membership and its Guest credential, but does not globally revoke the Account Session.

**Implementation implication.** One shared Conversation eligibility predicate must compare the authoritative clock, active owner Membership, deletion marker, and retention expiry before any content read or Provider invocation and again before result persistence. The purge worker only performs later physical erasure. The Account Join route can still authenticate an account for another Trip/rejoin; it cannot use that session to read the removed Membership's data.

**Source evidence.** Existing removal changes only Membership status and revokes Guest sessions. [service.py:386](../../backend/app/domain/trips/service.py#L386), [auth.py:282](../../backend/app/domain/auth.py#L282) Account-session revocation is a separate explicit function. [auth.py:241](../../backend/app/domain/auth.py#L241) The present `/chat` path has no durable conversation expiry boundary. [main.py:1329](../../backend/app/api/main.py#L1329)

**Regression tests.** With the purge worker disabled and a controlled clock past retention, every Conversation read/context/submission/recovery attempt returns the expired result and makes no Provider call; a late completion cannot persist. Separately, remove one account Membership and prove the Account Session still accesses another authorized Trip while the removed Trip and its conversations remain denied.

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

Membership removal revokes **Trip access for that Membership** and Guest credentials bound to it; it does not globally revoke the User's `AuthSession`. That is intentional: the same authenticated account may still use other Trips and can later perform the separate Account Rejoin operation if the decision-window gate permits it. [service.py:406](../../backend/app/domain/trips/service.py#L406), [auth.py:282](../../backend/app/domain/auth.py#L282)

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
11. Account/Guest split integration exercises the actual `FinalApp`/`TripAppState` request paths and proves a public email can neither discover nor claim a User.
12. Decision-window preflight and writer tests prove no active Membership mutation occurs while a Round/Proposal is nonterminal and no closed historical count is recomputed from the live roster.

## Blockers and gate recommendation

**READY FOR PR-06B.1 (contract gate).** Implementation must faithfully deliver all of the following; reopening any item requires a new architecture review:

- a bearer-authenticated account join/rejoin protocol (or a separately approved identity-verification design), replacing email-as-identity;
- one corrected, Trip-root serialized account join writer and retirement/routing of legacy bypasses;
- the active-only unique-index migration/runbook with safe downgrade refusal;
- explicit current-electorate behavior for removed members in constraints, votes, and pending proposals;
- a no-adoption V1 decision for Guest-to-account transfer;
- Membership-incarnation ownership enforced in Conversations and every replay/result-persistence path.

No production state was inspected or changed, and no assertion is made that these controls are deployed.
