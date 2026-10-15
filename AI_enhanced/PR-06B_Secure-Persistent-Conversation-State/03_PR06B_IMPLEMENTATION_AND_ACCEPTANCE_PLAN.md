# PR-06B.0 — Implementation and Acceptance Plan

Status: approved staged plan. Stop after this document review; do not begin PR-06B.1 without architecture approval.

## PR-06B.1 — Schema and authorization

Deliverables:

- SQLAlchemy models and one additive/reviewed Alembic migration for Conversations, Messages, Options, and Submissions.
- Active-owner, scope, sequence, idempotency, active-submission, lease, and purge indexes/constraints.
- A deliberate migration of the account Membership uniqueness index from all account rows to active account rows only.
- Fresh Membership-row creation on rejoin; historical Conversation owner IDs remain unchanged.
- Bearer-authenticated account join/rejoin (not invite-body email), one Trip-root writer, no V1 Guest-to-account adoption, and centralized current-active versus historical-membership query policy.
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
- database-unavailable initial or completion transaction never produces a false success/provider invocation or a false durable terminal claim;
- an authorized device without the original idempotency key discovers an expired submission and uses fenced recovery without replacement text;
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
19. Removal followed by account rejoin creates a new Membership incarnation; the new identity cannot access the old identity's conversations or private child data.
20. The retention purge worker claims, retries, catches up after downtime, and physically purges deleted primary records by the 30-day deadline under a controlled clock.
21. Post-cutover legacy `/chat` history calls receive the specified retirement response and cannot mix with durable Conversations.

## Explicitly deferred / out of scope

- RAG, embeddings, vector databases, semantic retrieval.
- Application-level field encryption and any claim of end-to-end encryption.
- Guest cross-device identity recovery.
- Background provider queue.
- AWS, ECS, CloudWatch, RDS, production migrations, production telemetry proof, and PR-06C changes.

## Remaining design gate

The product/security decisions D1–D7 are now resolved, but the Membership audit has found required implementation design changes. PR-06B.1 must not start until the rejoin migration, authenticated account-join protocol, legacy-writer routing, and current-electorate policy are reviewed and accepted. See [04_PR06B_MEMBERSHIP_REJOIN_IMPACT_AUDIT.md](04_PR06B_MEMBERSHIP_REJOIN_IMPACT_AUDIT.md).

## Verification for this PR-06B.0 documentation change

- Required: `git diff --check`.
- No runtime tests are required or claimed because no executable application code changed.
- No production/AWS/infrastructure validation is claimed.
