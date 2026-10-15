# PR-06B.1A — Membership Authentication & Lifecycle Safety Plan

Status: **PR-06B.1A.1 remediation in review; PR-06B.1A.2 remains unauthorized.** The approved quarantine policy authorizes only the reviewed schema/security primitives in 1A.1. It does not authorize Account Join, Guest Join, protected lifecycle Writers, frontend work, infrastructure changes, or Provider calls.

## Scope and non-goals

PR-06B.1A owns Membership identity and lifecycle safety only:

- Account-Session-derived Account Invite Join and public Guest Join;
- fresh Account Membership incarnations after removal;
- Trip-root serialization, active-only account uniqueness, active-member reads, and historical ownership preservation;
- `DecisionRound.electorate_count` and the non-emergency decision-window gate;
- the existing invite frontend's authenticated Account Join path.

It does **not** create `conversation`, `conversation_message`, `conversation_submission`, or `conversation_submission_recovery` tables. The latter is recorded in the PR-06B planned schema inventory for PR-06B.1B+: [01_PR06B_PERSISTENCE_ARCHITECTURE_DECISION.md](01_PR06B_PERSISTENCE_ARCHITECTURE_DECISION.md). No AWS, production database, PR-06C, or paid Provider work is authorized.

## Read-only preflight findings

| Area | Evidence | Implementation consequence |
|---|---|---|
| Invite UI | `FinalApp` asks for email and calls the same public join function for Guest and Account choices. [FinalApp.jsx:1406](../../trip/src/final/FinalApp.jsx#L1406), [TripAppState.jsx:761](../../trip/src/final/TripAppState.jsx#L761) | Split the UI and request seams; no email proves identity. |
| Account Session | `accountRequestJson` already supplies account-scoped session headers. [TripAppState.jsx:451](../../trip/src/final/TripAppState.jsx#L451) `auth.user_for_token` checks expiration/revocation. [auth.py:223](../../backend/app/domain/auth.py#L223) | Account Join can use existing account transport/session validation without an account email field. |
| Current unsafe service | Public join receives an email and finds/creates `User`; it returns a removed Membership on rejoin. [main.py:1604](../../backend/app/api/main.py#L1604), [service.py:309](../../backend/app/domain/trips/service.py#L309), [service.py:343](../../backend/app/domain/trips/service.py#L343) | Replace with separate Guest/Account service entry points and one root writer. |
| Root locking | The Trip kernel locks `Trip -> credential -> memberships -> Plan`; existing invite join locks the Trip root but uses a different direct adapter. [trip_kernel.py:5](../../backend/app/domain/transactions/trip_kernel.py#L5), [membership_join.py:29](../../backend/app/domain/transactions/membership_join.py#L29) | Route all state-changing joins/removals through a reviewed root protocol; never retain the direct service path as a bypass. |
| Current electorate | Round creation stores no electorate count; vote and settlement dynamically count all Memberships. [orchestrator.py:675](../../backend/app/domain/decisions/orchestrator.py#L675), [vote.py:207](../../backend/app/domain/transactions/vote.py#L207), [orchestrator.py:757](../../backend/app/domain/decisions/orchestrator.py#L757) | Add immutable `DecisionRound.electorate_count`; use it for all-voted, reopen majority, and historical response display. |
| Proposal electorate | Proposal creation materializes `ProposalDecision` rows, then applies only after every row accepts. [orchestrator.py:868](../../backend/app/domain/decisions/orchestrator.py#L868), [orchestrator.py:899](../../backend/app/domain/decisions/orchestrator.py#L899) | Treat those rows as immutable pending electorate; no rejoin inheritance or dynamic recomputation. |
| Account removal | Removal marks the Membership removed and revokes Guest sessions only; Account-session revocation is a separate function. [service.py:386](../../backend/app/domain/trips/service.py#L386), [auth.py:241](../../backend/app/domain/auth.py#L241) | Explicitly preserve: removing a Membership revokes only that Trip access, not the global Account Session. |

## Baseline V1 behavior ready to implement after gate approval

### Account and Guest Join APIs

| API | Authentication | Input | Outcome |
|---|---|---|---|
| `POST /api/invites/{token}/join` | Public | `display_name` only | Creates a new Guest Membership plus bounded GuestSession. `email` is rejected with stable `409 account_join_requires_authenticated_session`; no User lookup/write occurs. |
| `POST /api/invites/{token}/account-join` | Valid Account bearer, never Guest bearer | No email or account ID; optional presentation-free request body | Derives User from locked `AuthSession`, creates/returns active Account Membership, and returns server-derived profile/Membership data. |

`User.password_hash IS NULL` is an unclaimed legacy identity. Account Join must return `409 account_activation_required`; it must not create a password, session, Membership, merge Guest history, or infer ownership from email. A future verified email credential-activation flow is separately scoped.

### Controlled frontend cutover

1. Ship the backend capability and frontend feature support in the same versioned release train; the secure backend route exists before frontend Account Join uses it.
2. `TripAppState` adds `joinInviteAsAccount(token)`, implemented through `accountRequestJson`, not `publicRequestJson`. It sends no email and preserves the existing account bearer.
3. `FinalApp` shows Guest Join as public and Account Join as either “Join as <server account name>” or a sign-in/register return flow. The invite token is only a routing hint during that return flow, not a credential.
4. The public route keeps Guest compatibility. Old cached clients that submit email receive the explicit `409` contract response, make no mutation, and are directed to refresh/sign in; this is the controlled security cutover, not a silent fallback to email identity.
5. Only server-returned profile data is passed to `adoptTechnicalTripContext`; client email/name is display input, not authority.

### Membership lifecycle writer

Normal creation, rejoin, and removal use one owner-session/Trip-root protocol. A repeat Account Join that finds the same active Membership is a no-op. A removed account creates a new Membership B and increments `Trip.planning_facts_revision` and the sole Plan revision once. It never changes Membership A or its child foreign keys.

Canonical lock order for state-changing join/removal:

```text
Trip root
  -> Account AuthSession (Account Join/removal actor only)
  -> InviteLink (join only)
  -> all TripMembership rows ordered by id
  -> sole Plan ordered by id
  -> nonterminal DecisionRound / ChangeProposal rows ordered by id
  -> Vote / ProposalDecision child rows ordered by id
  -> target GuestSession rows ordered by id
  -> mutation + Trip/Plan revision + durable outcome
```

The Invite token may be read before the transaction only to discover the Trip ID; it is re-read and locked after the Trip root. The root lock serializes the otherwise different Account/Guest/remove paths. The API must delete/reroute the direct legacy `trip_service.join_invite` write so it cannot bypass this protocol.

### Active versus historical rules

- **Access-authorized Account Membership:** only `status='joined'` and no quarantine marker. **Access-authorized Guest Membership:** only `status in ('joined', 'preferences_submitted')` and no quarantine marker, preserving the pre-existing bounded Guest credential policy. `invited`, `removed`, quarantined, null, blank, and unknown/corrupt statuses fail closed. The migration preflight rejects unsupported stored statuses and the database check prevents new unsupported values.
- **Current roster, dashboard/invite counts, planner constraints, newly created Proposal affected sets:** use the shared current-participant predicate, not `status != 'removed'`. It uses the same explicit status/identity allow-list and quarantine exclusion as Trip access.
- **Closed Round/Proposal, Votes, ProposalDecisions, comments, notices, preferences, constraints, GuestSessions, durable command records:** preserve original Membership IDs. No copy, reparenting, or visibility grant to Membership B.
- **Round:** `DecisionRound.electorate_count` is an immutable nonnegative **count**, not a stored Membership-ID snapshot. It is set from the active count while the Round is opened under root locks. Existing closed Rounds retain `NULL` and expose `historical_electorate_count_available: false`, never a recomputed live count.
- **Proposal:** existing `ProposalDecision` rows are the immutable Membership-ID snapshot. New active filtering applies only while creating a new Proposal.

### Quarantine audit authority boundary

The 1A.1 schema stores `quarantined_at`, categorical `quarantine_reason`, and `quarantined_by_membership_id`. A composite self-reference `(quarantined_by_membership_id, trip_id) -> (id, trip_id)` proves only that the audit actor belongs to the same Trip; it does **not** prove authorization, current access, Organizer role, target eligibility, idempotency, or lock ordering.

The future protected Writer must lock the Trip root and re-read: (1) the actor's currently access-authorized Membership in that Trip, (2) `role='organizer'`, (3) the target Membership in the same locked Trip, and (4) that the target is not an Organizer. It must then atomically apply the audit fields with Guest credential/invite revocation and durable outcome handling. A route-level claim or the foreign key alone is never sufficient.

## Remaining security decision — emergency removal

### Why normal removal cannot simply be allowed

The frozen normal policy returns `409 membership_change_pending_decision` if any Round is open or Proposal is waiting/escalated. That avoids changing an in-flight electorate. Removing that guard without a new protocol is unsafe: current all-voted logic uses a live membership count, so removing a silent voter could settle early; current Proposal acceptance can apply as soon as all stored decisions are accepted. [vote.py:207](../../backend/app/domain/transactions/vote.py#L207), [orchestrator.py:917](../../backend/app/domain/decisions/orchestrator.py#L917)

An emergency override must also decide what to do with every affected open Round: preserving it until deadline retains a revoked actor in a frozen electorate; aborting it safely requires a new terminal reason/state and immutable audit evidence. Neither exists today. The present `DecisionRound` has only `open|closed`, and `settle_round` can apply a winning patch. [models.py:448](../../backend/app/db/models.py#L448), [orchestrator.py:757](../../backend/app/domain/decisions/orchestrator.py#L757)

### Smallest safe alternative proposed for approval

Add a separate **emergency Trip-access quarantine** operation, not an emergency Membership Removal:

1. A currently authorized Organizer invokes it under the canonical lock order.
2. It atomically marks the target Membership access-revoked for authorization, revokes every bound GuestSession, and records a categorical audit reason/time. It does **not** change the active Membership electorate used by already-nonterminal Rounds/Proposals.
3. The target immediately loses Trip reads, writes, replays, conversation access, and new voting/decision submissions. Their existing Vote/ProposalDecision rows remain historical facts.
4. Open Rounds continue against their frozen electorate until normal terminalization; pending affected Proposals remain unable to apply without the already-created decision and expire/escalate under existing rules. No unfinished change is applied by the quarantine itself, and no silent consent is counted.
5. After all decision-window records are terminal, the Organizer performs the ordinary Membership Removal, creating the standard removed state and allowing later B rejoin subject to normal gates.

This is the smallest alternative because it restores urgent access safety without inventing emergency Round cancellation, silently changing a Proposal's affected set, or retroactively deleting a vote. It still requires an additive access-revocation field and auth checks, which changes the Membership state model. A direct **emergency removal** instead requires a separate approved design for Round cancellation/terminal reason and per-Proposal forced-expiry evidence.

### Gate outcome

**BLOCKED — architecture approval required.** Choose one explicitly before application/schema changes:

- **Approve quarantine (recommended):** PR-06B.1A expands to introduce the narrow access-revocation state and later ordinary removal.
- **Approve emergency terminalization:** define Round cancellation status/reason, Proposal expiry reason, notice/audit records, revision semantics, and whether a removed member's already-cast open-round vote can count.
- **Reject emergency path:** retain the frozen `409` normal-removal gate throughout all decision windows.

No option is selected silently by this plan.

## Exact planned files

| Area | Files to modify/create after gate approval | Purpose |
|---|---|---|
| ORM and migrations | `backend/app/db/models.py`; new `backend/alembic/versions/<revision>_pr06b1a_membership_lifecycle.py` | Active-only index metadata, `DecisionRound.electorate_count`, and only the approved emergency-state field. Do not add Conversation tables. |
| Auth and public API | `backend/app/domain/auth.py`; `backend/app/api/main.py` | Account-session-only Account Join, Guest/email rejection, null-password outcome, response/error mapping, and session-vs-Membership semantics. |
| Join/remove writer | `backend/app/domain/trips/service.py`; `backend/app/domain/transactions/membership_join.py`; `backend/app/domain/transactions/membership_remove.py`; new focused transaction helper if needed | Split pure service facts from one root-locked writer, rejoin incarnation, decision gate, Guest revocation, and revisions. |
| Current-membership policy | `backend/app/domain/preferences/service.py`; `backend/app/domain/decisions/orchestrator.py`; `backend/app/domain/transactions/vote.py`; `backend/app/domain/transactions/change_proposal.py`; `backend/app/domain/transactions/scheduler.py` | Shared active filter, electorate capture/use, Proposal active-set capture, and terminal display/settlement behavior. |
| Existing lock seam | `backend/app/domain/transactions/trip_kernel.py` and/or `backend/app/domain/transactions/kernel.py` only if a reviewed child-lock helper is necessary | Preserve canonical root order; do not duplicate session/auth assembly in routes. |
| Invite frontend | `trip/src/final/TripAppState.jsx`; `trip/src/final/FinalApp.jsx`; only the existing session-runtime boundary if a return-route helper is truly missing | Account-scoped request and safe Guest/Account UI split; no raw credential persistence outside `shared/session-runtime`. |
| Tests | `backend/tests/test_invites.py`, `test_auth.py`, `test_b2e_unified_writers.py`, `test_trip_transaction_kernel.py`, `test_protected_vote_writer.py`, `test_protected_proposal_decision_writer.py`, `test_protected_round_settlement_writer.py`; targeted Trip frontend tests if present/added | Contract, PostgreSQL race, history, fairness, revision/idempotency, and actual UI request-flow coverage. |

## Migration and rollout sequence

1. **Read-only preflight:** inspect Alembic head, old index predicate, duplicate/nonstandard Membership rows, null-password User count, and nonterminal Round/Proposal count. Do not log private content. If existing nonterminal decisions exist, wait for ordinary terminalization before enabling the active-filter/decision-window behavior.
2. **Additive migration:** add nullable `decision_round.electorate_count` with a nonnegative check. Do not invent values for historical closed Rounds. If emergency quarantine is approved, add only its reviewed field/index here.
3. **Index migration:** create `ux_trip_membership_active_account` concurrently (`trip_id, user_id WHERE user_id IS NOT NULL AND status <> 'removed'`) outside Alembic's normal transaction. Validate it before dropping `one_membership_per_user_per_trip`; never leave an index-free window.
4. **Code release behind the documented lifecycle cutover:** activate the root writer, active filters, electorate capture, Account/Guest split, and normal decision gate together. The old email path may only return the stable migration error; it cannot create/claim a User.
5. **Frontend release:** enable Account Join only when the account route is available; preserve Guest Join and invite return routing. Observe only categorical error/usage counters.
6. **Postflight:** inspect the partial-index predicate, one-active-account invariant, nullable historical electorate behavior, rejoin B ownership isolation, and absence of direct legacy join calls.

### Guarded downgrade

Downgrade must refuse before destructive loss:

- refuse recreation of the old all-time account uniqueness index if any account/Trip has more than one Membership incarnation;
- refuse dropping `electorate_count` if any new Round has a non-null count, unless an explicitly approved archival migration preserves the value;
- if quarantine is approved, refuse to drop its state while any target remains access-revoked.

No downgrade may delete/repoint Membership children, convert B into A, or rewrite historical decisions.

### Checkpointed index-cutover recovery (PR-06B.1A.1 remediation)

The additive schema revision is an atomic Alembic checkpoint. `alembic/env.py` uses `transaction_per_migration=True`, so its revision record is committed before the next revision enters PostgreSQL `autocommit_block()` for concurrent index operations.

The following revision alone performs the unique-index cutover. It reads `pg_index` and refuses to trust an index name by itself:

| Observed catalog state | Recovery action |
|---|---|
| Legacy unique index valid/ready; target absent | Create target concurrently, then re-read exact columns/predicate plus `indisvalid`/`indisready`. |
| Legacy valid/ready; target valid/ready | Resume by dropping legacy only after target validation. |
| Legacy valid/ready; target invalid/not-ready but exact expected definition | Drop only that invalid target concurrently, recreate it, and validate before any legacy drop. |
| Target valid/ready; legacy absent | The physical cutover already completed; re-run records the checkpoint without rebuilding data. |
| Missing guard or an unexpected index definition | Fail closed. Stop writers, inspect catalog, and use an explicitly reviewed recovery; do not use `IF NOT EXISTS` or drop data. |

The migration contains a test-only `-x pr06b1a_fail_after_index_validation=1` injection point. It proves that a failure after target validation but before legacy drop leaves both valid indexes and the index revision unrecorded; a normal rerun completes safely. A real canceled `CREATE INDEX CONCURRENTLY` test also proves recovery from an invalid `pg_index` target while legacy uniqueness remains live.

No Account Join writer is introduced or deployable in 1A.1. PR-06B.1A.2 may only be considered after this final active-only uniqueness checkpoint is at head and after explicit review authorization.

## Required validation matrix after approval

| Category | Required evidence |
|---|---|
| Database | Disposable local PostgreSQL `alembic upgrade head`; index/constraint inspection from PostgreSQL catalogs; guarded downgrade refusal/success evidence as applicable. |
| Join/rejoin | Real multi-process PostgreSQL concurrent first join/rejoin; exactly one active row, one revision advance, and no direct legacy bypass. |
| Auth | Existing Guest/Account auth regressions plus Account Join bearer, expired/revoked/Guest bearer rejection, email rejection, and `password_hash IS NULL` activation-required tests. |
| History | A removed; B fresh; A-owned preferences/constraints/comments/votes/proposal decisions/notices/command records never transfer or become readable as B. |
| Fairness | Open Round/Proposal blocks ordinary lifecycle mutation; `electorate_count` prevents early settlement; reopen majority/display use immutable count; Proposal snapshot is not recomputed. |
| Emergency, only if approved | Organizer authorization, target credential revocation, concurrent vote/accept/remove/join race, no unintended apply, terminal evidence, and revision/idempotency tests. |
| Frontend | Actual `FinalApp`/`TripAppState` Guest and Account request paths, sign-in return route, stale-client migration error, and account context adoption. |
| Regressions | Protected transaction, root lock, revision, scheduler, idempotency, and existing invite/auth suites. No Provider or infrastructure calls. |

## Expected implementation commits after gate approval

1. `schema: add PR-06B.1A membership lifecycle contracts`
2. `backend: split authenticated account and guest invite joins`
3. `backend: enforce active membership and immutable electorate`
4. `frontend: migrate invite account join flow`
5. `test: cover PR-06B.1A lifecycle races and fairness`

If quarantine is approved, insert a separately reviewable commit: `backend: add emergency trip access quarantine`; do not hide it inside normal removal work.
