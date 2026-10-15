# PR-06B.0 — Implementation and Acceptance Plan

Status: approved staged plan. Stop after this document review; do not begin PR-06B.1 without architecture approval.

## PR-06B.1 — Schema and authorization

Deliverables:

- SQLAlchemy models and one additive/reviewed Alembic migration for Conversations, Messages, Options, and Submissions.
- Active-owner, scope, sequence, idempotency, active-submission, lease, and purge indexes/constraints.
- A deliberate migration of the account Membership uniqueness index from all account rows to active account rows only.
- Fresh Membership-row creation on rejoin; historical Conversation owner IDs remain unchanged.
- authorization service/seam tests for cross-Trip, cross-Member, Organizer, Guest expiry/revocation, removal, and rejoin isolation.

Required migration validation:

- `alembic upgrade head` and downgrade in a disposable local PostgreSQL database;
- inspect constraints/indexes with PostgreSQL, not only SQLAlchemy metadata;
- no AWS/RDS migration;
- no destructive seed against a shared database.

## PR-06B.2 — durable lifecycle

Deliverables:

- Conversation creation/resolution, list/read pagination, deletion, retention fields, purge worker contract.
- submission idempotency HMAC, payload mismatch rejection, durable state, leases, fences, and late-completion rejection.
- short-transaction lifecycle with post-Runtime authorization/deletion checks.

Required tests:

- exact idempotency replay does not invoke provider twice;
- same key/different payload is rejected;
- two keys concurrently submitted to one Conversation produce one active execution and one `409 conversation_busy`;
- lease reclaim increments fence and old completion is rejected;
- failure/persistence error reaches durable failed state safely;
- deletion during execution and Membership removal during execution cannot persist an assistant result.

## PR-06B.3 — server-owned context and Runtime integration

Deliverables:

- bounded chronological context builder;
- deterministic option-history adapter and separate Runtime text projection;
- shared behavior for Legacy, Pydantic, and protected Preview route;
- fresh tool/domain facts remain capability-scoped and authoritative;
- persisted option revalidation prior to any proposal/Preview/Apply behavior.

Required tests:

- history is recovered from database while client-provided history is ignored/rejected by durable endpoint;
- limits truncate deterministically without leaking content through telemetry;
- Legacy and Pydantic observe the same owner/history scope and fail closed after revocation;
- historical options work only when still current and authorized;
- existing chat history-reference, candidate-option, protected Preview, scoped-tool, and Preview/Apply tests remain passing.

## PR-06B.4 — frontend and end-to-end acceptance

Deliverables:

- Personal Chat recovery and pagination;
- isolated Plan Assistant `global` and `item:<item_id>` conversations;
- loading/replay/busy/error states corresponding to durable submission states;
- immediate deletion UX and non-resurrection after refresh;
- embedded Trip build synchronized only after source verification.

Required acceptance scenarios:

1. Refresh restores a completed Trip A Personal Conversation.
2. Account re-login restores that same authorized Membership conversation.
3. Authorized second Account device continues it.
4. Trip A content never appears in Trip B.
5. Member A cannot read Member B content.
6. Organizer has no role-derived access to another member’s content.
7. Expired/revoked Guest bearer loses access.
8. Membership removal blocks read, write, replay, and late persistence.
9. Exact retry creates no duplicate user/assistant output or provider invocation.
10. Same key/different payload is rejected.
11. Two-device new submissions follow deterministic `409 conversation_busy` behavior.
12. Crash/timeout lease recovery rejects late fenced output.
13. Historical option selection checks current Trip state.
14. Stale option clarifies or suggests anew; it does not apply.
15. Legacy and Pydantic share server-owned scope/context semantics.
16. Deleted conversation is absent from reads and model context immediately.
17. Telemetry/log assertions prove no private text/options/provider body is emitted.
18. Existing protected domain write, Preview/Apply, revision, and idempotency regressions pass.

## Explicitly deferred / out of scope

- RAG, embeddings, vector databases, semantic retrieval.
- Application-level field encryption and any claim of end-to-end encryption.
- Guest cross-device identity recovery.
- Background provider queue.
- AWS, ECS, CloudWatch, RDS, production migrations, production telemetry proof, and PR-06C changes.

## Remaining design gate

The product/security decisions D1–D7 are now resolved. The only implementation-critical gate is review of the Membership rejoin migration across all existing membership/invite/adoption paths. PR-06B.1 must not start until that impact audit confirms a fresh Membership incarnation can be created without violating existing auth, data ownership, or historical foreign-key behavior.

## Verification for this PR-06B.0 documentation change

- Required: `git diff --check`.
- No runtime tests are required or claimed because no executable application code changed.
- No production/AWS/infrastructure validation is claimed.
