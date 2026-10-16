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

## Local checkpoints and gate

- `4442973` — owner API, durable submission/idempotency, recovery, and internal
  completion/failure boundary (PR-06B.2A/B combined because these functions
  share one lifecycle module and one regression suite).
- `0546025` — retention purge and optional scheduler gate (PR-06B.2C).

The working tree was clean after these commits. No push, merge, AWS action,
production database access, paid Provider call, frontend change, or PR-06B.3
work occurred.

**Recommendation: CHANGES REQUIRED before PR-06B.2 final acceptance.** The
focused PostgreSQL lifecycle and selected existing regressions pass, but the
full-suite process did not complete within the local command-window available
for this run, and the required independent multi-session race matrix still
needs dedicated evidence. Neither limitation is waived by the passing focused
tests.

## Final acceptance remediation appendix

### Recovery replay and fence identity

`recover_submission()` now returns two distinct facts for a replayed Recovery
record: the current parent Submission `attempt_number`/`fence`, and that
Recovery key's immutable `recovery_accepted_attempt_number` /
`recovery_accepted_fence` / `recovery_state`. A replay of Recovery A after B
has claimed a later fence cannot claim B's authority. When B is accepted, any
earlier running Recovery record becomes `failed`: this means its lease/fence is
no longer execution authority, while retaining its original accepted identity
as audit evidence. It neither changes user text nor creates an assistant row.

### Completion atomicity and failure lease enforcement

The completion service now validates *all* option projections before adding the
Assistant Message: title and summary bounds, JSON-safe allowlisted patch,
same-Trip target, and nonnegative integer revision fields. Therefore a caller
that catches a validation exception without rolling back cannot retain an
orphan assistant row. Once validation passes, the caller must own one database
transaction; assistant/options, terminal Submission state, recovery state, and
qualifying-retention update are flushed together and are rolled back together
by the normal Session transaction owner.

`fail_submission()` now requires a live lease as well as running state and an
exact current attempt/fence. It remains categorical-only, never extends
retention, and does not create a Message. Deleted/expired Conversation or
removed/quarantined owner checks happen before any terminal write.

### Independent PostgreSQL race evidence

`backend/tests/test_pr06b2_conversation_concurrency_postgres.py` uses a
barrier, bounded futures, and distinct Session/connection per worker. It
verifies committed state for same-natural-key creation, same/conflicting/new
submission keys, recovery-key reclaim, old completion/failure after a newer
fence, quarantine/removal races, two purge workers, and purge/history races.
The tested order is exactly:

```text
Trip -> AccountSession or GuestSession -> TripMembership -> Conversation
     -> ConversationSubmission -> ConversationSubmissionRecovery
```

Conversation deletion and purge retain the original Membership root and never
resurrect a deleted Conversation. `SKIP LOCKED` permits only one concurrent
purge worker to claim the due root.

Because those tests deliberately commit from independent Sessions, their
autouse teardown removes only their recorded private graph in child-to-root
order. This prevents test-fixture leakage into the repository's later demo
seed idempotency tests; that full-suite contamination was reproduced and fixed
as part of this remediation.

### Retention/purge operations

The 180-day clock remains confined to accepted user input and a committed
assistant reply. Read/history/replay/failure paths do not set it. Expiry gates
access independently of a worker. The 30-day deletion deadline is represented
by `purge_due_at`; the purge counter reports `overdue`, but production must
configure a single job owner/leader and alert when this counter is nonzero.
No scheduler was activated in tests; no content is logged by the worker.

### Final verification and disposition

The remediation was committed as `e5d64c7` (`fix: remediate PR-06B.2 lifecycle
acceptance`). Full verification used only the disposable local database
`tripsync_pr06b2_final_test`, with `MOCK_AI=1` and `DISABLE_SCHEDULER=1`:

```text
python -m pytest -q --tb=short
1172 passed, 10 warnings in 119.04s

python -m compileall -q app tests/test_pr06b2_conversation_api.py \
  tests/test_pr06b2_conversation_concurrency_postgres.py
passed

git diff --check
passed
```

The ten warnings are pre-existing Alembic `path_separator` deprecations from
`test_trip_bound_phase1_migration.py`; none are test failures. The original
full-suite run exposed fixture leakage from the new committed concurrency test;
the scoped teardown described above fixed it, and the rerun completed without
excluded tests.

**Final recommendation: PASS for PR-06B.2 final acceptance.** This does not
authorize PR-06B.3: Provider invocation, bounded server-owned runtime context,
frontend durable-history migration, and any infrastructure deployment remain
separate future work.
