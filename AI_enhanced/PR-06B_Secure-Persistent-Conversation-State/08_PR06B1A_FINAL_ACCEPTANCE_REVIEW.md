# PR-06B.1A Final Integration & Security Acceptance Review

**Review date:** 2026-10-16
**Branch:** `codex/pr06b-independent-development`
**Reviewed range:** `b589fc7786c097d4bfe0eafe430afd90b00ca2b7...6ca353c917040fd10c9c2d2c94b8b5d67996aea9` (11 local commits)
**Final gate:** **CHANGES REQUIRED**
**PR-06B.1B:** must **not** begin until the two P1 remediations below have passed a focused review.

This is an independent implementation review of PR-06B.1A.1, .1A.2, and
.1A.3, against `04_PR06B_MEMBERSHIP_REJOIN_IMPACT_AUDIT.md`,
`05_PR06B1A_MEMBERSHIP_IMPLEMENTATION_PLAN.md`,
`06_PR06B1A2_MEMBERSHIP_LIFECYCLE_IMPLEMENTATION.md`, and
`07_PR06B1A3_FRONTEND_AND_E2E_ACCEPTANCE.md`. It does not authorize
PR-06B.1B, PR-06B.2, deployment, merge, or push.

## Executive result

The Account/Guest identity split, membership-incarnation schema, protected
remove/quarantine operations, frozen Round denominator, and frontend invite
integration are materially implemented and have strong local PostgreSQL and
component/integration-test evidence. However, two reachable or importable
writers still violate the frozen active-member/root-writer contracts. Passing
tests do not compensate for those omissions.

| Severity | Finding | Smallest safe remediation |
| --- | --- | --- |
| P1 | New plan generation includes removed or quarantined Membership data in its current planning facts. | Apply `current_participant_membership_clause()` to every generator membership count and Membership join that supplies constraints, interests, budget caps, availability, or durable generation snapshots; add real PostgreSQL removed/quarantined exclusion tests. |
| P1 | `trip_service.join_invite` remains a direct persistent Guest writer outside the Trip-root protocol. | Delete it, or turn it into a non-writing compatibility shim delegating to `join_guest_invite_under_trip_root`; add a regression that a direct legacy import cannot create a Membership without the root protocol. |

## Accepted security and identity evidence

### Account and Guest identity separation

- `POST /api/invites/{token}/account-join` accepts the empty,
  `extra=forbid` account-join request and derives the account from
  `current_account_user`; it passes the bearer and authenticated `user.id` to
  `join_account_invite_under_trip_root`, not an email or client user ID:
  `backend/app/api/main.py:1651-1688` and
  `backend/app/domain/transactions/membership_join.py:161-221`.
- The writer locks the Trip, then locks and revalidates `AuthSession`, checks
  token expiry/revocation and its `user_id`, and refuses a
  `password_hash IS NULL` user with the documented `403 Legacy identity is
  not an authenticated Account`. This neither claims email ownership nor
  silently adopts the legacy row.
- Public `POST /api/invites/{token}/join` accepts only Guest display name.
  Email-bearing legacy payloads return `409
  account_join_requires_authenticated_session` before a mutation:
  `backend/app/api/main.py:1619-1648`; the UI additionally whitelists
  `{ display_name }` in `trip/src/final/invite-join-flow.js:1-11`.
- The partial unique index enforces one non-removed Account Membership per
  `(trip_id, user_id)`. A removed A can coexist with newly-created B, while a
  quarantined non-removed Membership remains non-rejoinable:
  `backend/app/db/models.py:157-203`,
  `backend/app/domain/transactions/membership_join.py:190-220`.
- Account/Guest authorization uses the explicit active allow-list and excludes
  quarantine. Unexpected state fails closed:
  `backend/app/domain/membership_policy.py:20-86`,
  `backend/app/domain/auth.py:253-310`, and
  `backend/app/domain/transactions/trip_kernel.py:187-296`. Account sessions
  are global; membership removal/quarantine denies only that Trip membership.

### Protected lifecycle and concurrency

- The public Guest and Account invite writers lock `Trip -> Invite/AuthSession
  -> Memberships -> Plan -> nonterminal decisions`; normal join uses
  `require_no_decision_window`:
  `membership_join.py:35-159` and `membership_lifecycle.py:18-50`.
- Protected remove/quarantine use `TripDurableCommandKernel` and
  `TripTransactionKernel`, whose first authoritative lock is the Trip root,
  followed by credential reauthorization and Membership locking:
  `membership_remove.py:36-71`, `membership_quarantine.py:49-128`, and
  `trip_kernel.py:109-246`. The durable command contract supplies
  idempotency/revision behavior.
- Quarantine locks nonterminal decisions and Guest sessions, records a
  same-Trip audit actor, revokes the target's Guest credentials, and revokes
  active Trip invitations in the same transaction. It does not revoke an
  Account's global `AuthSession`: `membership_quarantine.py:95-123`; the
  same-Trip composite FK and audit constraints are in `models.py:157-203`.
- No direct API route calls the old service writer. That is insufficient: the
  callable below remains importable and persists a Membership without root
  lock, decision-window check, or planning-facts revision. It is the P1
  bypass finding, not a hypothetical style concern.

## P1 findings requiring remediation

### P1 — planning facts still use inactive Memberships

The approved contract says current planner constraints and other newly
captured planning facts use the shared current-participant predicate
(`05_PR06B1A_MEMBERSHIP_IMPLEMENTATION_PLAN.md:68-75`). The implementation
does not do so:

- `backend/app/domain/plans/generator.py:354-429` filters its membership
  count, required constraints, interests, meal-budget cap, and availability
  only by `TripMembership.trip_id`.
- `backend/app/domain/plans/generator_lifecycle.py:246-280` repeats the
  unfiltered count, constraints, and preferences when freezing durable
  generation inputs.

Consequently, an A that is removed or quarantined before a new generation can
still constrain the new plan. This conflicts with the required treatment of
removed/quarantined members and can make a plan unavailable or more restrictive
because of a member who has no present Trip access. The small safe repair is a
complete audit of every generator Membership query, applying
`current_participant_membership_clause()` to the joined Membership (including
the count), with PostgreSQL tests that prove exclusion for each planning input.

### P1 — direct legacy Guest join writer bypasses root serialization

`backend/app/domain/trips/service.py:326-355` still exposes
`join_invite(db, token, display_name, email=None)`. It calls `_valid_invite`,
inserts `TripMembership`, and creates `GuestSession` directly. It does not
take the Trip root lock, lock all memberships/Plan, reject the decision window,
or advance Trip/Plan planning-facts revisions. The approved plan explicitly
requires this writer be deleted or rerouted
(`05_PR06B1A_MEMBERSHIP_IMPLEMENTATION_PLAN.md:64-66`).

The HTTP route correctly uses `join_guest_invite_under_trip_root`, but retaining
the alternate persistent domain API leaves an internal bypass and invalidates
the stated “all Join paths” acceptance invariant. Remove it or make it a
delegating non-writing compatibility entry point, then add a direct-import
regression test.

## Schema and historical-decision acceptance

The .1A.1 migration design is accepted subject to the overall P1 gate:

- `9db6f1a1041b_pr06b1a_membership_lifecycle_schema.py` adds status and
  quarantine checks, same-Trip quarantine actor FK, nullable immutable
  `DecisionRound.electorate_count`, and guarded downgrade.
- `7a6c8e1d5f04_pr06b1a_active_account_index_cutover.py` checks catalog
  method, uniqueness, valid/ready state, exact two-key ordering, no expression
  keys, zero INCLUDE attributes, default key options, and exact predicate
  before dropping the legacy index. Recovery retains the legacy guard until
  the target is valid.
- `backend/app/domain/decisions/orchestrator.py:699-706` captures current
  participant count at opening. `orchestrator.py:787-795` refuses to settle a
  legacy open `NULL` count, and `orchestrator.py:870-875` skips such due
  rows. `backend/app/domain/transactions/vote.py:203-240` likewise has no
  live-roster fallback.
- Votes and ProposalDecision rows retain their original Membership IDs;
  quarantine does not settle or apply a pending decision. Normal membership
  changes fail closed during open Rounds or pending/escalated Proposals.

This confirms the frozen Round count is a denominator, not a Membership-ID
snapshot. It does not cure the separate current-planning-facts P1 defect.

## Frontend and session integration

The actual invite UI follows the approved paths:

1. A signed-in Account calls `getAccountProfile()` using account-scoped
   transport, then `joinAccountInvite(token)` with `{}`:
   `trip/src/final/FinalApp.jsx:1386-1509` and
   `TripAppState.jsx:464-468, 796-802`.
2. A signed-out Account is sent through a same-origin safe `/login` or
   `/signup` return URL built by
   `shared/trip-navigation-policy/invite-auth-return.js:1-29`; the invite
   token is routing data, not identity.
3. Guest join uses only the public display-name body. A live Account session
   is not silently replaced: the UI directs the user to Account Join or a
   separate browser session.
4. Before a Guest or Account context is adopted, `clearMembershipScopedState`
   clears Trip/Plan/round/proposal/preferences/user state:
   `TripAppState.jsx:650-705`. This prevents removed A's cached UI data from
   being presented under B.
5. Expired invitations, legacy Account rejection, quarantine, and decision
   window denials map to deterministic UI messages with no unsafe retry:
   `invite-join-flow.js:25-46`.

The embedded artifact was rebuilt successfully and its content hash matches
the checked-in bundle. No browser-driven E2E suite was executed in this review;
the cited frontend evidence is Node component/integration and build coverage,
not a claim of real-browser validation.

## Verification evidence

All commands were run locally with `DEV_ALLOW_MEMBERSHIP_HEADER=0`,
`MOCK_AI=1`, and `DISABLE_SCHEDULER=1` where backend tests require them. The
PostgreSQL migration test owns and drops a separately named disposable
database (`tests/test_pr06b1a_membership_migration_postgres.py:28-84`). No
AWS, production database, or paid Provider was contacted.

| Command / evidence | Result | Coverage and limitation |
| --- | --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest -q tests/test_pr06b1a_membership_schema_contract.py tests/test_pr06b1a_membership_migration_postgres.py tests/test_pr06b1a_membership_policy.py --tb=short` | **8 passed** in 10.37s | Real disposable PostgreSQL migration/index recovery, guarded downgrade, schema checks, and policy predicates. |
| `backend/.venv/Scripts/python.exe -m pytest -q tests/test_invites.py tests/test_pr06b1a2_membership_lifecycle.py tests/test_pr06b1a_membership_policy.py --tb=short` | **22 passed** in 5.08s | Account/Guest API contract, rejoin, quarantine, lifecycle policy. |
| `backend/.venv/Scripts/python.exe -m pytest -q tests/test_b2e_unified_writers.py tests/test_protected_round_settlement_writer.py tests/test_protected_proposal_decision_writer.py tests/test_agents_tools.py --tb=short` | **94 passed** in 45.07s | Protected transactions, durable/idempotent behavior, frozen decisions, and the former AI-tool isolation test after earlier writers. `test_tools_do_not_write_decision_or_plan_tables` passed in this ordered command. |
| `backend/.venv/Scripts/python.exe -m pytest --collect-only -q` | **1152 tests collected** in 1.97s | Current suite inventory. |
| Prior recorded full-suite evidence at identical reviewed code state, `pytest -q --tb=short` | **1152 passed**, 10 known Alembic deprecation warnings | Inspected in `07_PR06B1A3_FRONTEND_AND_E2E_ACCEPTANCE.md:124-126`. The monolithic command was not re-run in this review because the local command harness terminates a single foreground command at roughly 60 seconds; this is not represented as a new full-suite execution. |
| `frontend/node --test tests/session-runtime*.test.mjs tests/trip-navigation*.test.mjs tests/trip-preview-integration.test.mjs tests/pr06b1a3-invite-join.test.mjs` | **131 passed** | Shared session runtime, invite UI integration, safe return route, stale-state adoption behavior. |
| `frontend/npm test` | **13 passed** | Product build plus rendered/preview/runtime integration; only the existing chunk-size advisory. |
| `frontend/npm run build:trip-preview` | **passed** | Built `index-C1mFY76_.css` and `index-DfIIVzXQ.js`; embedded Trip preview synchronized. The timestamp-only manifest change was restored, leaving no generated diff. |
| `git diff --check` | **passed** | Run before this review document was added; re-run required with the final document prior to commit. |

The earlier order-dependent AI-tool report was independently checked through
the ordered 94-test command above. The documented historical investigation
identified no AI authorization/write defect; it corrected an unrelated
later-order lifecycle-test cleanup issue
(`06_PR06B1A2_MEMBERSHIP_LIFECYCLE_IMPLEMENTATION.md:90-123`).

## Scope and release boundary

- The reviewed range contains no Conversation persistence schema or provider
  implementation, no Guest-to-Account ownership adoption, no email-based
  Account identity path, no AWS/production operation, and no PR-06C merge.
- No browser E2E was run. A future release gate should run the ten named
  user journeys in an actual browser after the P1 remediation.
- Existing repository-wide lint debt remains outside this review; it is not
  the reason for this gate result.
- The branch is local-only and ahead of its remote by 11 commits at review
  start. Nothing was pushed or merged.

## Required return-to-review checklist

1. Fix both P1 findings without widening the product contract.
2. Add PostgreSQL regressions that prove removed and quarantined Memberships
   cannot affect every new generator planning input or durable snapshot.
3. Add a direct-import regression proving the legacy Guest writer no longer
   persists outside the root protocol.
4. Re-run the targeted suites, complete backend suite, frontend integration
   suite/build, `git diff --check`, and a clean-worktree check.
5. Re-submit PR-06B.1A for final acceptance. Until then, the correct status
   is **CHANGES REQUIRED**, and PR-06B.1B remains blocked.

## Remediation appendix — 2026-10-16

**Remediation result:** both original P1 findings are **resolved**. This
appendix preserves the original findings above and records the follow-up
implementation and fresh evidence. The final gate is now **PASS**, with the
release limitations stated below.

### P1-01 resolved — active planning facts only

The following Membership-dependent planning reads now use the centralized
`current_participant_membership_clause()` rather than `trip_id` alone:

| Source | Protected current-planning reads |
| --- | --- |
| `backend/app/domain/plans/generator.py` | Synchronous generator organizer admission, membership count, required constraints, interests/travel styles, meal-budget cap, and submitted availability. |
| `backend/app/domain/plans/generator_lifecycle.py` | Detached/durable snapshot organizer admission, membership count, required constraints, and every frozen preference field that derives interests, budget, availability, and day slots. |

`generator_commit._validate_current_generation()` already re-captures the full
snapshot under the final Trip-root lock and compares it to the detached
snapshot (`backend/app/domain/plans/generator_commit.py:97-115`). No new
generation architecture was added. A removal or quarantine advances planning
facts/Plan revisions; the final compare consequently rejects stale provider
work before a PlanItem, PlanChange, or generation ledger can commit.

New real-PostgreSQL coverage:

- `test_generation_uses_only_current_participants_and_preserves_inactive_history`
  creates active Account A and Guest B plus removed Account C and quarantined
  Account D. C/D carry distinctive interests, a $1 budget cap, constraints,
  and limited availability. It proves the B.2D1 detached snapshot has count
  two, only A/B constraints, active-only interests/budget/two-day availability,
  and that the synchronous public generator produces an active plan. It also
  proves C/D Membership and Preference rows remain intact.
- `test_real_protected_membership_quarantine_during_detached_generator_work_stales_draft`
  pauses provider work, performs an actual protected quarantine, and proves
  the generated draft is rejected as `StaleGeneratorPreconditions` with no
  PlanItems or `plan.generate.v1` idempotency record. The existing removal
  counterpart remains green. Frozen Round behavior is unchanged and continues
  to be covered by the protected decision suites.

### P1-02 resolved — one Guest Join writer

`backend/app/domain/trips/service.py` no longer exports `join_invite` or its
legacy email-identity exception. It retains only shared value types and
non-join service functions. The sole Guest writer is
`join_guest_invite_under_trip_root` in
`backend/app/domain/transactions/membership_join.py:119-148`; it locks the
Trip root, rechecks the invite, locks Memberships/Plan, applies the decision
window, creates the Guest credential, and advances Trip/Plan revisions in one
transaction. `join_invite_under_trip_root` remains only as a safe
email-rejecting compatibility entry point that delegates to that writer.

The new `test_trip_service_exposes_no_direct_guest_membership_writer` verifies
the retired service symbol is not importable. Existing PostgreSQL lifecycle
coverage remains green for protected Guest Join, expired/revoked invite denial,
open Round/pending Proposal denial, concurrent join serialization,
exact-once revisions, Account Join, and Guest credential revocation. The
frontend source characterization was updated to assert the canonical writer
instead of expecting the removed unsafe one.

### Fresh regression evidence

All backend commands used the disposable `TEST_DATABASE_URL` harness with
`DEV_ALLOW_MEMBERSHIP_HEADER=0`, `MOCK_AI=1`, and `DISABLE_SCHEDULER=1`.
No provider, AWS, production database, deployment, push, or merge operation
occurred.

| Command group | Actual result |
| --- | --- |
| Generator lifecycle/commit/external/protected HTTP and plan generation | 148 passed in 8.78s |
| Invite, PR-06B lifecycle/schema/migration/policy, protected transaction writers, and Trip kernel | 208 passed in 30.13s |
| Backend regression segment A1 | 201 passed in 47.84s |
| Backend regression segment A2 | 201 passed in 9.70s |
| Backend regression segment B1 | 217 passed, 10 Alembic `path_separator` deprecation warnings, in 14.70s |
| Backend regression segment B2 | 147 passed in 23.21s |
| Previously omitted planner-variety and provider-smoke files | 33 passed in 4.04s |
| `pytest --collect-only -q` after the three new tests | 1155 collected in 3.73s |
| Aggregate of the seven completed non-overlapping backend segments | **1155 passed**, **10 known Alembic deprecation warnings** |
| `frontend/node --test tests/session-runtime*.test.mjs tests/trip-navigation*.test.mjs tests/trip-preview-integration.test.mjs tests/pr06b1a3-invite-join.test.mjs` | 131 passed |
| `frontend/npm test` | 13 passed; existing chunk-size advisory only |
| `frontend/npm run build:trip-preview` | passed; embedded CSS/JS hashes unchanged |

One initial frontend characterization command failed because it asserted that
the intentionally removed direct writer still returned `JoinedInvite`. The
test was updated to assert its absence and the canonical root writer, then the
same 131-test command passed. A first oversized backend segment was stopped by
the local 60-second command-runner limit and is deliberately excluded from the
aggregate; every file in it was re-run in completed smaller segments.

### Remaining limitations and final recommendation

- No browser-driven E2E suite was executed. The frontend evidence is Node
  integration/characterization plus real builds, not a claim of browser E2E.
- Existing repository-wide lint debt remains outside this narrowly scoped
  remediation and was not hidden or changed.
- No Conversation persistence, Guest-to-Account historical adoption, PR-06C
  change, or production infrastructure operation was introduced.

**Final recommendation: PASS.** The two P1 acceptance blockers are remediated
with source-level and real PostgreSQL evidence. PR-06B.1A may be accepted;
PR-06B.1B may begin only under separate explicit authorization.
