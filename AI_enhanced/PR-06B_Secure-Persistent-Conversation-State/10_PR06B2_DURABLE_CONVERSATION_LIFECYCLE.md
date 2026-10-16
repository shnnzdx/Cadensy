# PR-06B.2 — Durable Conversation Lifecycle

**Status:** implementation checkpoint; local PostgreSQL-focused verification recorded below.

## Scope and compatibility

PR-06B.2 adds an owner-scoped durable API and internal lifecycle boundary. It
does not call a Provider, create a completion HTTP endpoint, change frontend
code, or alter legacy `POST /api/trips/{trip_id}/chat`. The legacy endpoint
continues to process its existing client-history compatibility contract and
does not create any `conversation*` row. PR-06B.3 is responsible for the
server-owned history/runtime adapter cutover.

## Files

- `backend/app/api/main.py` — durable routes and content-free error mapping.
- `backend/app/domain/conversations/service.py` — authorization, HMAC,
  submissions, recovery, internal completion/failure, and deletion.
- `backend/app/jobs/conversation_purge.py` — bounded physical purge worker.
- `backend/app/jobs/scheduler.py` — optional explicit purge-job gate.
- `backend/tests/test_pr06b2_conversation_api.py` — PostgreSQL-backed lifecycle
  coverage using the disposable test database.

## Public API

| Route | Contract |
| --- | --- |
| `POST /api/trips/{trip_id}/conversations` | Creates or discovers an active owner identity (`personal/personal`, `plan_assistant/global`, or a same-Trip `item:<id>`). |
| `GET /api/trips/{trip_id}/conversations` | Lists only current owner metadata; never Trip-wide private metadata. |
| `GET /api/trips/{trip_id}/conversations/{id}` | Owner-only metadata. |
| `GET /api/trips/{trip_id}/conversations/{id}/messages?before=&limit=` | Ascending, bounded server-sequence history, with safe assistant options only. |
| `GET /api/trips/{trip_id}/conversations/{id}/submissions/{submission_id}` | Owner-only durable state. |
| `POST /api/trips/{trip_id}/conversations/{id}/messages` | Requires `Idempotency-Key`; returns `202`. |
| `POST /api/trips/{trip_id}/conversations/{id}/submissions/{submission_id}/recover` | Requires a recovery `Idempotency-Key`; returns `202`. |
| `DELETE /api/trips/{trip_id}/conversations/{id}` | Owner-only logical deletion; returns `204`. |

Private/root-mismatch/deleted/expired/revoked outcomes use a non-disclosing
`404 Conversation not found`. Same submission key with a different canonical
request returns `409 idempotency_key_conflict`; a competing active submission
or non-expired recovery returns `409 conversation_busy`.

## Authorization, locks, retention

The only owner identity is `(trip_id, owner_membership_id)`. Each public call
first uses `current_membership`, then revalidates under locks in this order:

```text
Trip -> AccountSession or GuestSession -> TripMembership -> Conversation
     -> Submission -> Recovery
```

This matches the protected Trip-writer credential-before-membership ordering.
Organizer role does not expand conversation visibility. Removed or quarantined
Memberships, revoked/expired credentials, cross-Trip owners, deleted roots,
and roots past `retention_expires_at` all fail closed. Account-session
revocation is global as before; Membership revocation remains Trip-scoped.

Accepted user messages and successfully persisted assistant completions set
`last_qualifying_activity_at` and `retention_expires_at = now + 180 days`.
Reads, listings, replay, failed completion, and authorization failures do not.
Expiry is checked at the lifecycle boundary even when the purge job is stopped.

## Submission, recovery, and completion

Canonical HMAC input is deterministic JSON containing operation version,
conversation ID, owner Membership ID, scope/context, text, and selected item.
The fingerprint is HMAC-SHA-256 with versioned server material. Production
refuses a missing `CONVERSATION_SUBMISSION_HMAC_KEY`; a clearly development-only
fallback exists solely for local tests.

```text
accepted user message -> running(attempt=1, fence=1, lease)
same payload/key      -> durable replay (or own expired-lease reclaim)
new key while running -> conversation_busy
expired recovery key  -> running(attempt+1, fence+1, lease)
matching internal completion -> completed + exactly one assistant Message
matching internal failure    -> failed + categorical failure only
```

`conversation_submission_recovery` is keyed by `(submission_id, recovery_key)`
and carries no text. Same recovery key replays durable parent state. Recovery
does not create a user Message. A stale/expired/older fence cannot complete;
one parent assistant-result FK prevents duplicate committed assistant effects.
An uncertain future Provider request may be invoked more than once across valid
fenced attempts, so this is not an exactly-once Provider claim.

`complete_submission` and `fail_submission` are internal functions, not routes.
They validate current owner state, current fence/lease, content bounds and the
safe option projection. Patches are limited to the existing Plan field
allowlist; persisted options are display evidence only and carry no apply
authority.

## Deletion and purge

Deletion marks the root deleted immediately, terminally fails active work with
the categorical `conversation_deleted` result, and schedules `purge_due_at` at
exactly 30 days. `app.jobs.conversation_purge.run_once` expires overdue active
roots, locks due rows using `SKIP LOCKED`, then deletes recovery records,
options, submissions, messages, and finally the conversation root. It never
deletes Membership or Trip history. Its content-free counters are `expired`,
`purged`, and `overdue`.

The existing scheduler calls it only when
`CADENSY_CONVERSATION_PURGE_ENABLED=1`; tests disable scheduling. Deployment
still needs one documented scheduler/leader owner, an alert for `overdue > 0`,
and backup-lifecycle operation. This code does not claim backup erasure,
database encryption/TLS, HTTPS, or E2EE validation.

## Local verification

All commands used only
`postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_pr06b2_test`
and `MOCK_AI=1`, `DISABLE_SCHEDULER=1`:

```text
python -m pytest -q tests/test_pr06b2_conversation_api.py \
  tests/test_pr06b1b_conversation_schema_contract.py \
  tests/test_pr06b1b_conversation_migration_postgres.py \
  tests/test_chat.py tests/test_pr06b1a2_membership_lifecycle.py --tb=short
31 passed

python -m compileall -q app tests/test_pr06b2_conversation_api.py
passed
```

The new tests cover owner isolation, duplicate active identity, same-key replay
and conflict, user-message single insertion, history ordering, expired-lease
recovery, stale fence rejection, one committed assistant result, immediate
expiry denial, logical deletion, child-first purge, and Membership-history
preservation. Existing chat regression proves the legacy route remains
operational without durable writes.

## Remaining acceptance work

PR-06B.3 remains responsible for Provider execution outside transactions,
server-owned context-window construction, runtime integration, and frontend
cutover. Before a final PR-06B.2 PASS, the full backend suite and dedicated
multi-session PostgreSQL race cases (simultaneous creation/submission/recovery,
quarantine/removal versus completion, and purge versus read) must be reviewed
against the final committed worktree.
