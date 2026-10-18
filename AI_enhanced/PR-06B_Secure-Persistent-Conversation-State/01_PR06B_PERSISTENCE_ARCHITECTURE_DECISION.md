# PR-06B.0 — Persistence Architecture Decision

Status: approved V1 contract. Implementation is deferred to PR-06B.1+.

## Decision

Persist private conversations under the ownership tuple:

```text
Trip + owner Membership incarnation + scope + context key
```

Conversation history is user data and a contextual aid. It is not Current Plan state, not a domain command, and not telemetry.

## D1 — Retention and deletion

- Default retention is 180 days from the latest **qualifying activity**: successful durable user-message acceptance or successfully persisted assistant completion. Reads, pagination, idempotency replay, and failed authorization do not extend retention.
- `deleted_at` immediately makes a Conversation unavailable to API reads, context construction, new submissions, replay, and result persistence.
- User-deleted primary records receive `purge_due_at <= deleted_at + 30 days`; a durable purge job must physically remove primary rows by that deadline.
- Expired undeleted conversations receive a retention purge deadline according to the same worker contract.
- A provider worker completing after deletion, Membership revocation, or lease loss cannot restore the Conversation.
- Backup retention is distinct from primary-record deletion and requires separate operational documentation.

The 180-day and 30-day commitments are technically enforceable only with database fields, an idempotent scheduled purge worker, alerting for overdue rows, and acceptance tests that advance a controlled clock. A SQL constraint alone cannot enforce future-time deletion.

Retention is enforced independently of the purge worker: every conversation authorization, discovery/list/read, context build, submission/replay/recovery, and completion-persistence boundary compares `now` to `retention_expires_at`. A row past that instant is fail-closed as expired (and may be marked/scheduled in a successful transaction), even if a scheduler is late or unavailable. The purge worker controls physical deletion timing; it is not an access-control clock.

## D2 — scope and ownership

Allowed `scope` values are `personal` and `plan_assistant`.

- Personal context key: `personal`.
- Plan Assistant context keys: `global` and `item:<plan_item_id>`.
- UI interaction mode is not persisted as identity.
- The active-conversation natural key is `(trip_id, owner_membership_id, scope, context_key)`.
- Organizer role does not widen this key or permit other members’ reads.

The schema must use a database-backed association between `conversation.trip_id` and `conversation.owner_membership_id`. PR-06B.1 should add the necessary composite uniqueness/reference support instead of trusting an unconstrained pair.

## D5 — Membership incarnation / rejoin decision

`TripMembership.id` is the V1 incarnation identifier.

On account rejoin after removal:

1. Create a new `TripMembership` row with a new ID.
2. Never reactivate the removed Membership row.
3. Keep old Conversations owned by the old Membership inaccessible and subject to retention.
4. Change the current account uniqueness index so it applies only to non-removed rows; audit all rejoin, invite, guest-to-account adoption, foreign-key, and reporting paths before that migration.

The existing index currently applies whenever `user_id IS NOT NULL`, even after removal. [backend/app/db/models.py:156](../../backend/app/db/models.py#L156) This must be migrated deliberately, not silently worked around in conversation code.

The approved migration shape is an active-only PostgreSQL partial unique index, created and validated concurrently **before** the old index is dropped. Downgrade must refuse safely if valid rejoin history has created both an old and new Membership for one account/Trip; it must never collapse those rows. Account join/rejoin must use authenticated account identity rather than an invite-body email. Guest-to-account adoption is not a V1 path. The complete impact/migration review is [04_PR06B_MEMBERSHIP_REJOIN_IMPACT_AUDIT.md](04_PR06B_MEMBERSHIP_REJOIN_IMPACT_AUDIT.md).

The implementation uses a split invite contract: public Guest Join creates only Guest identity; Account Join derives the User from a valid Account Session and never from email. Legacy `User.password_hash IS NULL` rows are unclaimed and require independently verified activation before Account Join; they must not be silently adopted. Membership removal revokes that Trip Membership rather than globally revoking the Account Session.

## Proposed data model

### `conversation`

Required fields:

- `id`, `trip_id`, `owner_membership_id`
- `scope`, `context_key`, `status` (`active | deleted | expired`)
- `last_qualifying_activity_at`, `retention_expires_at`
- `deleted_at`, `purge_due_at`, `created_at`, `updated_at`

Required constraints/indexes:

- allowed scope/status check constraints;
- composite owner/Trip reference;
- partial unique active natural key;
- indexes for owner-scoped retrieval and purge scanning.

### `conversation_message`

Required fields:

- `id`, `conversation_id`, `sequence`, `role` (`user | assistant`)
- text content, selected item reference if present, message state (`pending | completed | failed`)
- captured Plan / planning-facts revisions when relevant;
- timestamps and deletion marker.

`(conversation_id, sequence)` is unique. Sequence is allocated by the server while holding the conversation submission lock; client timestamps never determine ordering.

### `conversation_message_option`

Stores only the safe, user-visible projection of a server-validated assistant option:

- parent assistant message, ordinal, stable option ID, target item reference;
- allowed display fields and an allowlisted safe patch;
- source Plan/planning-facts revisions and creation time.

The option row is evidence of an earlier suggestion, never authorization to apply a change. Its target item and patch must be revalidated against fresh state at selection time.

### `conversation_submission`

This is intentionally separate from `mutation_idempotency`.

- conversation/owner IDs, idempotency key, canonical-payload HMAC/version;
- user and assistant message references;
- state (`running | completed | failed`), durable lease expiry, monotonic fence number, retry/attempt metadata;
- safe failure category and response metadata, never raw provider payload.

One partial unique active-submission index per Conversation enforces D6. A separate unique `(conversation_id, idempotency_key)` supports replay.

### `conversation_submission_recovery`

Recovery is not a second user submission. It is a durable idempotency record keyed by `(submission_id, recovery_key)` and contains the operation version, accepted `attempt_number`, accepted `fence`, state, and timestamps. It never contains replacement user text. The parent Submission owns the original immutable user-message reference, current lease/fence, and the sole assistant-message/result reference.

This shape makes a recovery key replayable without creating a second user message and distinguishes recovery request idempotency from the original message idempotency key. A recovery key is accepted only after the parent lease has expired and under the Conversation/Submission lock; the transaction increments parent attempt/fence, creates the recovery record, and grants the new lease atomically. A unique assistant-result reference on the parent makes the durable effect exactly-once (zero or one committed assistant reply) even though an uncertain timeout can require more than one external Provider invocation across fenced attempts.

## D7 — privacy and encryption posture

V1 requires database encryption at rest, encrypted backups, authenticated TLS for database transport, HTTPS for API transport, and least-privilege access. Application-level field encryption is deferred. This is **not** end-to-end encryption.

These are deployment requirements, not verified properties of this branch. PR-06B.0 cannot claim that AWS/RDS encryption, backups, TLS, or production HTTPS are deployed or validated; PR-06C and production validation remain out of scope.

Persistent content must not enter PR-06A telemetry, ordinary application logs, provider-error logs, trace-safe observation fields, or CloudWatch events. Existing telemetry is intentionally categorical; retain that boundary. [backend/app/api/main.py:1329](../../backend/app/api/main.py#L1329)

## Alternatives rejected

- **Client-owned history:** cannot provide recovery, isolation, deletion guarantees, or trustworthy option provenance.
- **Reuse `MutationIdempotency`:** it represents completed protected domain writes and revision outcomes, not an external-provider lifecycle with pending states and fencing. [backend/app/db/models.py:276](../../backend/app/db/models.py#L276)
- **RAG/vector retrieval:** not required for bounded chronological private history and explicitly outside PR-06B.
- **Organizer-readable Trip transcript:** contradicts Personal Chat’s privacy promise and D4.
