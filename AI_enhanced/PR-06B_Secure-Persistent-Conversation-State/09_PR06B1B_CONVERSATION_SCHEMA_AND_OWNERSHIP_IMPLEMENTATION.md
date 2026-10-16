# PR-06B.1B — Conversation Schema and Ownership Implementation

**Status:** **PASS — implementation and local PostgreSQL validation complete.**

**Branch:** `codex/pr06b-independent-development`
**Alembic revision:** `b7d3a1e9c642`
**Dependency:** `7a6c8e1d5f04` (the verified PR-06B.1A active-account index cutover)

## Entry gate

The implementation began only after the following local checks:

- the branch was `codex/pr06b-independent-development` and the worktree was clean;
- `3c291fd` was present. Its final-acceptance appendix records both P1 repairs:
  generator planning facts use `current_participant_membership_clause()` and the
  former direct `trip_service.join_invite` writer is absent;
- the recorded PR-06B.1A PostgreSQL evidence is preserved in
  `08_PR06B1A_FINAL_ACCEPTANCE_REVIEW.md`; it is not represented as PR-06B.1B
  validation;
- `alembic heads` reported `7a6c8e1d5f04` before this migration was added;
- `TripMembership` has `uq_trip_membership_id_trip` over `(id, trip_id)`, and
  its current-access predicates explicitly reject removed, quarantined, null,
  blank, and unknown states.

PR-06B.1B does not alter Membership state, authorization predicates, Account/
Guest joins, quarantine writers, or PR-06B.1A's index cutover.

## Five-table inventory

| Table | Durable purpose | Key database constraints |
| --- | --- | --- |
| `conversation` | Private root owned by one Membership incarnation | Composite owner FK `(owner_membership_id, trip_id) -> trip_membership(id, trip_id)`; scope/status/context checks; active natural-key partial unique index; retention/deletion checks. |
| `conversation_message` | Deterministic user/assistant message ordering | `UNIQUE(conversation_id, sequence)`; role/state/revision checks; Conversation FK; composite unique `(conversation_id, id)` for child references. |
| `conversation_message_option` | Safe historical assistant option projection | Assistant-message FK; `UNIQUE(assistant_message_id, ordinal)`; nonnegative ordinal; JSON object-only safe patch; revision checks. |
| `conversation_submission` | Original request idempotency and execution record | Unique `(conversation_id, idempotency_key)`; one-running partial unique index; Conversation owner tuple FK; same-Conversation message FKs; attempt/fence/state/lease checks. |
| `conversation_submission_recovery` | Recovery-key idempotency for an existing Submission | Unique `(submission_id, recovery_key)`; Submission FK; positive operation/attempt/fence and allowed-state checks. It has no content or user-message column. |

No sixth conversation table, conversation API, runtime worker, provider call, or
frontend behavior was added.

## Ownership and relationship boundary

```text
TripMembership A (id, trip_id) ── composite FK ──> Conversation
                                                   │
                           1 ──────────────────────┼──> ordered Messages
                                                   │       └──> safe Options
                                                   └──> Submission
                                                         │  ├── original user Message
                                                         │  └── optional sole assistant Message
                                                         └──> Recovery records
```

`ConversationSubmission` repeats the Conversation's immutable `(trip_id,
owner_membership_id)` only so PostgreSQL can enforce one composite FK back to
the root. Its user/assistant links are composite FKs on `(conversation_id,
message_id)`, so a Submission cannot attach a Message from another
Conversation. The root's Membership/Trip FK rejects a Membership from another
Trip. Removed A remains referenced by its original rows; a later B is a
different `TripMembership.id` and cannot inherit A's root or children.

Quarantine likewise preserves identity. It does not transfer or rewrite a
Conversation owner reference.

## Concrete schema policy

### Conversation identity, retention, and purge scans

- Only `personal` and `plan_assistant` scopes are permitted.
- `personal` requires `context_key = 'personal'`. `plan_assistant` requires
  `global` or `item:<32 lowercase hexadecimal PlanItem ID>`. UI display mode
  is absent from the schema and therefore cannot become an identity dimension.
- `ux_conversation_active_natural_identity` uniquely enforces `(trip_id,
  owner_membership_id, scope, context_key)` only while `status = 'active'`.
  A logically deleted or expired historical root therefore does not prevent a
  later active root with that natural key.
- `last_qualifying_activity_at` and `retention_expires_at` are both null or
  both present, with expiry no earlier than the qualifying activity. The
  future Writer owns the exact 180-day calculation from accepted user input or
  persisted assistant completion.
- The deletion constraint requires a deleted row to have both `deleted_at` and
  `purge_due_at`, with `purge_due_at <= deleted_at + 30 days`. An expired row
  has a purge deadline but no user-deletion marker. Active rows have neither.
- `ix_conversation_retention_expiry_active` and `ix_conversation_purge_due`
  provide the intended future expiry/purge scan paths. Physical purge must
  delete recovery records, submissions, options, messages, then the root;
  relationships deliberately do not cascade from Membership history.

### Submission/recovery semantics stored, not executed

The Submission record holds canonical request HMAC/version fields, immutable
original user-message identity, optional sole assistant result identity,
attempt number, lease expiry, fence, categorical failure, and safe response
metadata. `ux_conversation_submission_one_running` permits no more than one
`running` Submission per Conversation. A terminal Submission has no current
lease; a running one requires one.

Recovery stores only Submission identity, recovery key, operation version,
accepted attempt/fence, state, acceptance time, and timestamps. The key's
namespace is `(submission_id, recovery_key)`, so the same Recovery Key may
exist for a different Submission. It cannot introduce replacement user text.

The unique assistant-message reference makes one Message unavailable as the
committed result of two Submissions. It does not claim exactly-once Provider
invocation: a later fenced recovery can repeat an externally uncertain call.

## Database guarantees versus deferred work

**Database-enforced now**

- Membership-incarnation/Trip ownership and cross-Trip rejection for roots.
- Active conversation uniqueness, deterministic message sequencing, valid
  enumerations, nonnegative revisions/sequences, and retention/deletion shape.
- Option ordinal uniqueness and JSON-object safe-patch representation.
- Submission idempotency namespace, one running submission, same-Conversation
  user/assistant references, and positive attempt/fence values.
- Recovery-key namespace, Submission reference, and positive
  operation/attempt/fence values.
- A data-bearing downgrade fails before dropping any private table.

**Application authorization required in PR-06B.2**

- Deriving the Membership only from the valid Account/Guest bearer and ensuring
  it is the current active owner; Organizer role never grants another owner's
  read access.
- Rejecting deleted/expired/revoked/quarantined owners at discovery, read,
  context, submission, recovery, and completion boundaries.
- Verifying selected/target Plan Items belong to the Conversation Trip and
  revalidating options through Preview/Apply. An Option row has no SQL apply
  path or mutation authority.

**Transactional lifecycle required in PR-06B.2**

- Conversation locks, sequence allocation, retention calculation, logical
  delete, read/context expiry enforcement, and a purge worker.
- Canonical HMAC comparison, idempotency replay, lease claim/reclaim, fence
  increment/check, result persistence, and recovery-key replay.
- Ensuring referenced message roles/states are appropriate (user original,
  assistant result) and that a parent Option is attached only to an assistant
  Message. These are Writer invariants, not a substitute for the relational
  same-Conversation guarantees.

**Infrastructure not validated**

This local schema change does not prove database encryption at rest, encrypted
backups, database TLS, API HTTPS, least privilege, log/telemetry handling, or
backup-retention behavior. It is not end-to-end encryption.

## Migration and rollback rule

`b7d3a1e9c642` is additive and transactional. It creates only the five new
tables, their named constraints, and ordinary indexes; it does not modify the
PR-06B.1A concurrent index or any Membership, Plan, Vote, Proposal, or durable
command record. It is the only Alembic head after application.

Downgrade first checks all five tables for rows. If any contains data it raises
`PR-06B.1B downgrade refused: conversation data would be lost` before dropping
anything. An empty schema may downgrade to `7a6c8e1d5f04` and upgrade again.
Data export/retention approval is required before any data-bearing rollback.

## Test assets and local evidence

New focused tests:

- `backend/tests/test_pr06b1b_conversation_schema_contract.py`: ORM contract
  for ownership, indexes, lifecycle checks, and submission/recovery keys.
- `backend/tests/test_pr06b1b_conversation_migration_postgres.py`: a separate
  PostgreSQL database test designed to exercise upgrade, catalog inspection,
  empty downgrade/re-upgrade, root ownership, rejoin/quarantine attribution,
  message/option integrity, submission/recovery integrity, and data-bearing
  downgrade refusal using actual insert failures.

Completed local checks:

| Command | Result |
| --- | --- |
| `python -m pytest -q tests/test_pr06b1b_conversation_schema_contract.py --tb=short` | **3 passed** |
| `python -m pytest -q tests/test_pr06b1b_conversation_migration_postgres.py --tb=short` | **1 passed** in 4.95s; genuine disposable PostgreSQL upgrade/catalog/integrity/guarded-downgrade coverage. |
| `python -m compileall -q app alembic tests/test_pr06b1b_conversation_schema_contract.py tests/test_pr06b1b_conversation_migration_postgres.py` | **passed** |
| `python -m alembic heads` | **`b7d3a1e9c642 (head)`** |
| PR-06B.1A schema/lifecycle, invite/auth, protected writer, and policy regressions | **28 + 63 passed** |
| `tests/test_agents_tools.py` | **31 passed** in four isolated groups; includes `test_tools_do_not_write_decision_or_plan_tables`. |
| Full backend suite in non-overlapping PostgreSQL-backed groups | **1159 passed**, matching collection; **10 existing Alembic `path_separator` deprecation warnings**. |

### PostgreSQL validation evidence

The local PostgreSQL 18 service was restored with the documented local
`pg_ctl` command and reached `pg_isready`'s `accepting connections` state.
Tests used only the explicitly disposable
`postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_pr06b1b_test`
source database and its test-created derivative databases. The migration test
uses PostgreSQL catalog inspection plus real insert/update failures to verify
the composite Membership/Trip FK, active Conversation index predicate,
retention/purge indexes, same-Conversation Submission message FKs,
idempotency/running-submission indexes, recovery namespace, and guarded
downgrade behavior.

An offline `alembic upgrade head --sql` attempt also cannot prove this migration:
an earlier existing migration (`c2a4b6d8e0f1`) performs an online historical
preflight and consequently cannot run in Alembic offline SQL mode. This is not
used as a substitute for the completed PostgreSQL catalog or migration evidence.

No AWS, production/shared database, paid Provider, deployment, push, or merge
operation was used. **Final recommendation: PASS for PR-06B.1B schema and
ownership acceptance.** PR-06B.2 lifecycle work remains explicitly deferred.
