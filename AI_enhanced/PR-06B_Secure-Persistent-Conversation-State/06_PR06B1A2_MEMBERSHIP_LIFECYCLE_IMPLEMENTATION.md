# PR-06B.1A.2 — Membership Lifecycle Implementation Report

Status: **CHANGES REQUIRED for final integration review.** The PR-06B.1A.2
security acceptance suite passes on local disposable PostgreSQL. The complete
backend suite has one order-dependent AI-tool isolation failure that passes in
isolation and is not attributed to this change; it must be resolved or
explicitly waived before a repository-wide green gate. No frontend work,
Conversation persistence, AWS action, push, or merge was performed.

## Delivered contract

### Join APIs

| Route | Identity authority | Mutation contract |
|---|---|---|
| `POST /api/invites/{token}/join` | Public Guest payload: `display_name` only | Uses the Trip-root Guest writer. An `email` payload receives `422` with the Account-join migration message; no `User` lookup or creation occurs. |
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
  response display, and reopen settlement use it. Historical null-count
  Rounds retain their preexisting fallback semantics; no completed history is
  rewritten.
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
| `python -m compileall -q app tests` and `git diff --check` | passed |

The lifecycle suite proves real PostgreSQL Account rejoin concurrency, exact
once revisions, Decision Window rejection, durable replay and conflicting-key
rejection, Guest credential and invite containment, Account-session retention,
and Account/Guest HTTP identity separation.

## Deferred and remaining work

- PR-06B.1A.3 owns frontend Account Join integration; backend contracts are
  ready but no `trip/` UI was changed here.
- Persistent Conversation, recovery, AI provider changes, telemetry changes,
  deployment, AWS, and PR-06C remain out of scope.
- `pytest -q` currently stops at an existing order-dependent failure in
  `tests/test_agents_tools.py::test_tools_do_not_write_decision_or_plan_tables`:
  it observes writes after prior suite state, while the same test passes in a
  fresh isolated disposable database. This must be investigated as test
  isolation before a full-suite PASS; it is not represented as a lifecycle
  success claim.
