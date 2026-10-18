# PR-06B.1A.2 — Membership Lifecycle Implementation Report

Status: **PASS for the PR-06B.1A.2 integration review.** All backend tests
pass against a disposable local PostgreSQL database. No frontend work,
Conversation persistence, AWS action, push, or merge was performed.

## Delivered contract

### Join APIs

| Route | Identity authority | Mutation contract |
|---|---|---|
| `POST /api/invites/{token}/join` | Public Guest payload: `display_name` only | Uses the Trip-root Guest writer. An `email` payload receives `409 account_join_requires_authenticated_session`; no `User` lookup or creation occurs. |
| `POST /api/invites/{token}/account-join` | Verified Account bearer only | Empty, extra-forbidden body; the writer locks and revalidates `AuthSession`, derives its `user_id`, and never trusts email or a body User ID. |
| `DELETE /api/trips/{trip_id}/members/{membership_id}` | Active Organizer + credential reauthorization | Always uses the protected durable writer; idempotency and expected Trip-facts revision headers are mandatory. |
| `POST /api/trips/{trip_id}/members/{membership_id}/quarantine` | Active non-quarantined Organizer | Protected durable emergency operation; categorical reason, idempotency, and expected revision are mandatory. |

`User.password_hash IS NULL` is explicitly denied by Account Join with no
Membership mutation. A compatibility `join_invite_under_trip_root` symbol
accepts only the former Guest shape and rejects email-bearing calls, so an old
internal import cannot recreate the email-to-Account bypass.

## Writer protocol and lifecycle semantics

```text
Trip root
  -> Account AuthSession (Account join / Organizer operation)
  -> InviteLink (join)
  -> TripMembership rows, ascending ID
  -> sole Plan
  -> nonterminal DecisionRound / ChangeProposal rows, ascending ID
  -> GuestSession / InviteLink child rows, ascending ID
  -> domain effect + revision + durable idempotency outcome
```

- Active Account Join is an idempotent no-op and returns the same Membership.
- Removed Account Membership A remains terminal. A later valid Account Join
  creates B; no preferences, constraints, votes, decisions, credentials,
  command outcomes, or future Conversation ownership is copied.
- The active partial account index plus Trip root serialization makes concurrent
  joins produce one active incarnation and one revision increment.
- Normal Account/Guest join and ordinary removal lock/check an open Round or a
  `waiting_affected_members`/`escalated` Proposal and return
  `409 membership_change_pending_decision` without a partial write.
- Quarantine is not removal. It retains the Membership and historical rows,
  marks categorical audit fields, revokes all target Guest sessions and all
  currently valid Trip invites atomically, and leaves the global Account
  session valid.

## Authorization and frozen decisions

- Central membership policy now gates Account/Guest bearer resolution,
  dashboard/roster reads, Trip and Plan transaction kernels, and Preview
  revalidation. Quarantined, removed, unknown, and invited Memberships fail
  closed. `preferences_submitted` remains an explicitly active state for both
  Account and Guest writers.
- Current roster, dashboard/invite counts, newly classified constraints, and
  new Proposal affected sets use the current-participant predicate.
- New Rounds write immutable `DecisionRound.electorate_count`; all-voted,
  response display, and reopen settlement use it. A legacy closed null-count
  Round exposes `total_members: null` and `electorate_count_available: false`;
  it is never recomputed from today's roster. A legacy open null-count Round
  cannot settle (manually, by all-voted, or by the scheduler) until a separately
  approved recovery procedure exists.
- Quarantine locks nonterminal decisions but does not settle, apply, reparent,
  or recalculate them. Existing Vote and ProposalDecision rows remain bound to
  their original Membership IDs.

## Verification evidence

All commands used a disposable local PostgreSQL database and `MOCK_AI=1`.

| Command scope | Result |
|---|---:|
| PR-06B.1A.1 entry gate: schema/policy/migration | 8 passed |
| PR-06B.1A.2 lifecycle + invite/auth/kernel + B2E + schema/policy/migration | 92 passed |
| Protected vote/proposal/change/idempotency/preference/Guest credential/profile regressions | 186 passed |
| Isolated AI read-only tool regression | 1 passed |
| Exact collected prefix through the former AI-tool failure | 47 passed |
| Full backend suite (`pytest -q --tb=short`) | 1152 passed, 10 Alembic deprecation warnings |
| `python -m compileall -q app tests` and `git diff --check` | passed |

The lifecycle suite proves real PostgreSQL Account rejoin concurrency, exact
once revisions, Decision Window rejection, durable replay and conflicting-key
rejection, Guest credential and invite containment, Account-session retention,
and Account/Guest HTTP identity separation.

## Final integration remediation

- **AI-tool failure:** the former `test_tools_do_not_write_decision_or_plan_tables`
  report was not reproducible. The exact collected predecessor sequence is
  green and the complete suite is green. No application-side tool write or
  authorization bypass was found. The real test-isolation correction is a
  teardown for this suite's separately committed PostgreSQL writer Sessions;
  it cannot be the source of that earlier target because this file is collected
  later.
- **Guest error contract:** the frozen response is restored to `409
  account_join_requires_authenticated_session`. HTTP tests prove that a
  rejected email-bearing Guest request creates neither a User nor a Membership.
- **Legacy decisions:** Open Rows with a null immutable count cannot settle;
  their all-voted shortcut is disabled. Closed null-count Rows expose an
  unavailable count, not a derived current roster. Quarantine regression tests
  preserve the original Vote and ProposalDecision Membership IDs and leave
  incomplete decisions nonterminal.
- **Authorization graph:** Account/Guest bearer resolution, Trip reads, Trip
  and Plan kernels, Preview revalidation, and durable replay use the central
  active-membership policy. Global Account sessions remain usable for other
  Trips; revoked Guest tokens and revoked invite links do not regain access.
  No remaining backend authorization gap was found in the audited paths.

## Deferred and remaining work

- PR-06B.1A.3 owns frontend Account Join integration; backend contracts are
  ready but no `trip/` UI was changed here.
- Persistent Conversation, recovery, AI provider changes, telemetry changes,
  deployment, AWS, and PR-06C remain out of scope.
- The previously reported AI-tool failure could not be reproduced. Its exact
  collected prefix and the complete suite pass. No preceding contaminator or
  AI authorization write path was found. The investigation did find a real,
  later-order test-isolation flaw: this lifecycle suite used independently
  committed writer Sessions without teardown. It now deletes its complete
  owned graph after each test. Its position after `test_agents_tools.py` means
  it could not have caused the earlier AI-tool report.
