# PR-06B — Final Integration Acceptance

**Scope:** `codex/pr06b-independent-development`, local integration audit only
**Audit baseline:** `bdf546f55a5cea35ccc503df1f920bc77048bea9` (`test: close PR-06B.4 browser acceptance`)
**Decision date:** 2026-10-17
**No external operations:** no AWS, production database, paid Provider, push, merge, or deployment was used.

## 1. Executive summary

PR-06B's implemented membership-lifecycle, durable-conversation, server-owned
context/runtime, and frontend restoration paths form one coherent local product
slice. The final local evidence is the PR-06B.4D.2 execution on a disposable
PostgreSQL database: the ten required Chromium journeys (J01–J10) and the
runtime-off journey passed. The latest full backend baseline passed **1182 tests,
10 warnings, exit 0, in 110.19 seconds**; frontend tests and the Trip production
build also passed.

This is a local-product result, not a production-infrastructure claim. In
particular, database-at-rest encryption, encrypted backup retention, TLS,
deployment migration control, live Provider behaviour/costs, AWS health, purge
scheduler operations, and production observability are not assessed here.

**Gate A — PR-06B Local Integration Acceptance: PASS.**
**Gate B — Production Readiness: NOT ASSESSED / NOT APPROVED.**

## 2. Repository and commit inventory

| Check | Observed result |
| --- | --- |
| Branch | `codex/pr06b-independent-development` |
| Audit HEAD | `bdf546f55a5cea35ccc503df1f920bc77048bea9` |
| Alembic declared head | `b7d3a1e9c642` — `add PR-06B.1B private conversation persistence schema` |
| Tracked source state before this report | Clean; the only pending material was intentionally untracked local acceptance evidence. |
| Generated Trip assets | No tracked diff under `frontend/public/trip-app/`; the PR-06B.4 runner built and synced the Trip preview before browser execution. |
| Latest local acceptance evidence | Present under the untracked `backend/test-results-pr06b4-*` artifacts; see Sections 6–7. |

Implementation history reviewed from the current branch:

| Stage | Representative implementation / closure commits |
| --- | --- |
| Contract freeze and membership analysis | `2158c06`, `478f622`, `cae8fbb`, `d584750` |
| PR-06B.1A membership lifecycle | `b3e079d`, `99a4a05`, `4c887e0`, `943caab`, `7cb963b`, `3c291fd` |
| PR-06B.1B conversation schema | `be5512e`, `69a4cb1` |
| PR-06B.2 durable lifecycle and purge | `4442973`, `0546025`, `e5d64c7`, `a216cf2` |
| PR-06B.3 context/runtime and historical options | `49b0563`, `b1e41c2`, `1700301`, `40b1776` |
| PR-06B.4 frontend and final browser closure | `b3d2c05`, `a7eb6e2`, `a0709a9`, `e6729a5`, `8da37d8`, `c62523d`, `08cefaf`, `bdf546f` |

The apparent older BLOCKED entries in `12_PR06B4_FRONTEND_PERSISTENT_CONVERSATION_ACCEPTANCE.md` are chronological execution checkpoints. Its final **PR-06B.4D.2** section supersedes them with the local PostgreSQL recovery and J01–J10 terminal PASS evidence.

## 3. Frozen D1–D7 traceability

| Product requirement | Implemented enforcement | Security boundary | Test evidence | Final local status |
| --- | --- | --- | --- | --- |
| **D1 — 180-day retention, delete now, purge within 30 days** | Conversation lifecycle stores qualifying activity, `retention_expires_at`, `deleted_at`, and `purge_due_at`; reads/context admission reject expired or deleted records; B.2 purge implementation owns physical removal. | Enforcement occurs at authorization/read/context boundaries independently of a later purge run. A running submission is fenced failed on deletion. | B.2 lifecycle/purge tests; J09 performs browser delete, reload, and authenticated non-disclosing 404. | PASS for local product semantics; operational scheduler/SLO evidence is outside Gate A. |
| **D2 — personal versus Plan context scope** | `Conversation` identity is resolved by server `scope` plus validated `context_key`; client derives personal/global/item requests but cannot choose an owner. | Owner membership and Trip are part of every conversation and submission query. Historic option selection goes through fresh classification, not direct Apply. | J07 proves distinct personal/global/item IDs and persisted isolated content; J08 proves advisory option provenance and fresh classify only. | PASS. |
| **D3 — bounded Guest recovery** | Public Guest Join has no email identity claim; it issues only a bounded Guest session. Account Join uses `/account-join` and `current_account_user`. | `current_membership()` resolves a valid Account bearer first, otherwise an actual Guest token; membership ID, invite, trip ID, or email are not credentials. Expired/revoked Guest credentials fail. | PR-06B.1A account/Guest auth regressions and final lifecycle tests; J05 checks revocation of bound Guest credentials during quarantine. | PASS. |
| **D4 — immediate removal revocation / private history stays private** | `remove_membership` uses the protected Trip transaction route; conversation services re-authorize the owner membership at each access/context/completion boundary. | Removal is Trip-local; it does not revoke the Account's global session or grant an Organizer access to a former member's records. | J04 removes A, confirms A loses Trip access, and confirms rejoined B cannot discover A's Conversation while the Account remains usable. | PASS. |
| **D5 — rejoin means a fresh membership incarnation** | Active-only Account uniqueness and `join_account_invite_under_trip_root()` create a new row after A is removed; legacy password-less Users cannot be silently adopted. | A remains terminal historical owner; a quarantined incarnation is not replaceable. Trip-root lock serializes join with lifecycle changes. | PostgreSQL lifecycle/index tests in B.1A; J04 asserts removed A, active B, exactly one active Account membership, and immutable A Conversation owner. | PASS. |
| **D6 — one active submission, idempotency, recovery and fencing** | `submit_message()` persists one user message/submission under a unique idempotency contract; `recover_submission()` persists a recovery key with incremented attempt/fence and durable lease; `complete_submission()` requires the current attempt/fence. | Identical replay returns original state; distinct overlap is busy; stale/late completion cannot write. Exactly-once is for committed durable effects, not a guarantee that an uncertain external Provider invocation happens once. | B.2 PostgreSQL lifecycle/concurrency tests; J06 exact before/after DB assertion proves two browser HTTP 202 requests yielded one submission and one user message; J05 proves held completion loses after quarantine. | PASS. |
| **D7 — security and privacy boundary** | Conversation text is kept in the private durable model/context path; failures store categorical status, not Provider payload. Production-only fake runtime selection is rejected. | Product does not claim E2EE or application field encryption. Telemetry/runtime paths are designed not to expose private conversation content. Infrastructure encryption/TLS/least privilege remain deployment requirements. | Privacy/idempotency suite, source inspection of runtime selection, J05/J10, and final backend suite. | PASS for application contract; production controls NOT ASSESSED. |

Additional frozen fairness contract: `DecisionRound.electorate_count` is captured for new rounds and deliberately differs from a membership-ID snapshot. Membership access/current-roster predicates are separated from historical ownership predicates in `app/domain/membership_policy.py`; terminal historical votes/proposal decisions are not rewritten. B.1A PostgreSQL trigger and decision tests were accepted before this final audit. Quarantine only denies access/revokes bounded credentials; it does not settle or apply a pending decision (J05).

## 4. Production-code architecture and authorization trace

The following trace is source review of the current code, supplemented by the tests cited below. It is not a claim that a design diagram alone enforces behaviour.

```text
verified bearer
  -> current_membership / current_account_user
  -> Trip-scoped active Membership
  -> owner-scoped Conversation
  -> durable Submission (idempotency / lease / fence)
  -> server-owned bounded Context
  -> ConversationRuntimeCoordinator
  -> RevisionBracketedPreview
  -> fence-aware durable completion
  -> persisted messages/options
  -> durable frontend client restores history
```

| Boundary | Actual implementation and enforcement point |
| --- | --- |
| Account and Guest credential resolution | `backend/app/api/main.py:current_membership()` resolves Account bearer sessions first and uses the Guest-token path only after Account resolution fails; it verifies the requested Trip. `current_account_user()` is the Account-only pre-membership seam used by Account Invite Join. `backend/app/domain/membership_policy.py:is_membership_access_authorized()` permits only recognized joined/preferences-submitted Account or Guest states and requires no quarantine. Unknown/corrupt states fail closed. |
| Join, rejoin, removal, and quarantine | `backend/app/api/main.py:join_invite()` rejects email-bearing Guest Join with `409 account_join_requires_authenticated_session`; `account_join_invite()` supplies verified Account identity to `backend/app/domain/transactions/membership_join.py:join_account_invite_under_trip_root()`. That writer locks the Trip, session, invite, memberships, and plan, denies `password_hash IS NULL` legacy identities, and creates B after removed A. Protected removal/quarantine are Trip-root durable transactions; `membership_quarantine.execute_protected_membership_quarantine()` locks actor/target and revokes Trip-bound entry paths atomically. |
| Conversation ownership/read/delete | Every public conversation route at `backend/app/api/main.py` uses `Depends(current_membership)` plus `_require_scoped_trip()`. `backend/app/domain/conversations/service.py:_locked_owned_conversation()` (used by read, submit, recover, complete, and delete) repeats Trip, owner Membership, active-access, retention, and deletion checks under lock. An Organizer has no alternate owner bypass. |
| Durable admission and recovery | `service.submit_message()` locks the owner Conversation, returns an idempotent replay for the same key/fingerprint, rejects a competing running submission, creates the User Message once, and grants attempt 1/fence 1 with a lease. `service.recover_submission()` records `ConversationSubmissionRecovery(submission_id, recovery_key, accepted_attempt_number, accepted_fence)` and only grants a newer attempt after expiry. It never creates another User Message. |
| Server-owned bounded context | `backend/app/domain/conversations/context.py:build_context()` accepts only a server-issued `ExecutionIdentity`. It locks/validates current submission attempt, Conversation ownership, active Membership, deletion/expiry, reads persisted history only, enforces a bounded newest contiguous suffix, and excludes unfinished historical user turns. Client-provided history/options cannot enter the Runtime projection. |
| Runtime and protected Preview | `backend/app/domain/conversations/runtime.py:ConversationRuntimeCoordinator.execute()` separates context, Runtime/Preview, and completion into short transactions. It calls `RevisionBracketedPreview.preview()` with server-built text; Preview retains existing protected capability and revision checking. Persisted options are advisory snapshots, not a writer. |
| Completion fencing | `service.complete_submission()` validates submission state, unexpired lease, exact attempt, and exact fence before creating one Assistant Message/options and setting terminal completed state. `fail_submission()` has the same fence check. A deleted, revoked, expired, or superseded completion is discarded. |
| Frontend restoration and runtime-off behaviour | `trip/src/final/TripAppState.jsx` constructs `createDurableConversationClient()` through the existing session-runtime seam. `trip/src/final/useDurableConversation.js` creates/discovers, reads paginated persisted messages, submits/recoveries, and disables the composer when `runtime_available !== true`; it does not fall back to legacy chat. |

### Alternative-route review

The public durable routes (`create`, discover/list, messages, submission status,
submit, recover, and delete) were reviewed in `backend/app/api/main.py` and all
enter `current_membership()` plus Trip scoping. Completion has deliberately no
HTTP route. The legacy `POST /api/trips/{trip_id}/chat` remains a distinct,
membership-protected compatibility endpoint; it is not a durable writer. J10
posted that legacy route and asserted unchanged durable Conversation count.

This source review found no public route that writes a Conversation Assistant
result without owner authorization and the current fence, and no route that
turns historical options into a direct plan mutation. This is a bounded audit of
the reviewed public paths, not a replacement for future route-review discipline.

### Runtime parity and local fake-runtime containment

Both selected Legacy and Pydantic runtimes enter the same
`RevisionBracketedPreview` contract from the coordinator; the durable context,
membership authorization, revision bracket, and fenced persistence are outside
runtime-specific composition. `backend/app/agents/runtime_factory.py:build_chat_runtime()` selects `LocalE2ERuntime` only for
`CADENSY_E2E_FAKE_RUNTIME=1` and explicitly raises when `APP_ENV` is
`production` or `prod`. The local adapter therefore exercises the real durable
and Preview boundaries but is not evidence for a live Provider.

## 5. Stage-by-stage acceptance reconciliation

| Stage | Final accepted scope | Reconciled result |
| --- | --- | --- |
| PR-06B.0 / .0M | D1–D7 contract, privacy/retention, membership incarnation and decision-window policy | Frozen before implementation; reflected in later code and tests. |
| PR-06B.1A | Account/Guest Join split, active-only Account uniqueness, lifecycle locks, removal/quarantine safety, electorate immutability | PASS local. The final B.1A review closed index-shape, status, and guarded-downgrade concerns. |
| PR-06B.1B | Conversation ownership schema, constraints, migration checkpoints | PASS local PostgreSQL validation at declared Alembic head `b7d3a1e9c642`. |
| PR-06B.2 | Durable Conversation lifecycle, admission/idempotency/recovery/lease/fence, retention/delete/purge | PASS local. The final full suite and J06/J09/J05 provide later integration evidence. |
| PR-06B.3 | Server-owned bounded context, coordinator, Preview parity, historical option safety | PASS local. J05 and J08 exercise the race/option boundaries through actual protected HTTP and PostgreSQL assertions. |
| PR-06B.4 / .4D.2 | Durable frontend client, refresh/re-login/cross-context restoration, browser harness, runtime-off and legacy compatibility | PASS local. J01–J10 and Runtime-off reached terminal PASS. |

## 6. Final automated local evidence

| Command / evidence set | Result and interpretation |
| --- | --- |
| Disposable PostgreSQL PR-06B.4 browser runner: `backend/scripts/run_pr06b4_local_e2e.ps1` | PASS. It recreated only `cadensy_pr06b4_e2e_local_test`, migrated through `b7d3a1e9c642`, used installed Chromium and an explicitly local fake Runtime, and made protected HTTP calls with committed SQL assertions. It did not use AWS, a live Provider, or a production DB. |
| Full backend suite, isolated `cadensy_pr06b4_full_test` with `APP_ENV=test`, `DISABLE_SCHEDULER=1`, `MOCK_AI=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0` | **1182 passed, 10 warnings, exit 0, 110.19s.** The retained terminal log is `backend/test-results-pr06b4-full-pytest.log`; warning is the known Alembic `path_separator` deprecation. This run is the final reviewed-code baseline, performed after the PR-06B.4D.2 closure. |
| Targeted Membership/Conversation/Runtime/Preview pytest selection | PASS — 59 tests. |
| `cd frontend; npm test` | PASS — 13 tests. |
| `cd trip; npm run build` and runner preview sync | PASS — Vite production build, 60 modules transformed; `frontend/public/trip-app` synced. |
| Syntax/diff hygiene | `compileall` for E2E components, `node --check frontend/tests/pr06b4-local-browser-e2e.mjs`, and `git diff --check` passed at final PR-06B.4 closure. |

No full matrix was rerun during this document-only audit because the evidence had
no concrete contradiction and the task explicitly disallowed unnecessary
re-execution.

## 7. Browser acceptance: final terminal results

| Journey | Final result | What the evidence establishes |
| --- | --- | --- |
| J01 | PASS | Real login UI, durable send/completion, refresh, and committed owner/history. |
| J02 | PASS | Product logout then fresh Account login restores server-owned history. |
| J03 | PASS | Separate Chromium context and Account bearer restore authorized history. |
| J04 | PASS | Removed A loses access; Account rejoin creates B; historical A owner is retained and hidden from B. |
| J05 | PASS | A submission commits, Runtime is held, protected quarantine commits, and late completion creates neither Assistant Message nor Option; Account remains valid in another Trip. |
| J06 | PASS | Two browser-origin submits with one idempotency key produce exactly one Submission and one User Message. |
| J07 | PASS | Personal, Plan-global, and item contexts have distinct IDs, content, and retained owner isolation after refresh. |
| J08 | PASS | Coordinator-persisted option records revisions; after plan revision changes, selection performs fresh classification and emits no direct Apply. |
| J09 | PASS | Delete followed by browser reload leaves the Conversation unavailable. |
| J10 | PASS | Actual legacy `/chat` remains compatible and creates no durable Conversation row. |
| Runtime-off | PASS | Existing persisted history loads, composer is disabled, and no legacy fallback or fabricated response occurs. |

The J05 and J08 evidence is especially important: they use real protected
HTTP routes and PostgreSQL committed-state assertions, not direct lifecycle table
mutation or UI-only interpretation.

## 8. Privacy, idempotency, and durable-effect guarantees

- Private Conversation access is owner-Membership scoped. A role such as
  Organizer does not substitute for that ownership tuple.
- A removed/quarantined/deleted/retention-expired Conversation cannot be read,
  used to reconstruct runtime context, or receive a late persisted completion.
- A user message is created once at durable admission. Replaying the same
  idempotency key returns the original Submission state; a recovery key
  identifies a durable recovery record for the original Submission, not another
  message.
- At most one Assistant result can become committed for a Submission because
  completion requires the currently stored attempt/fence/lease under lock.
  External Provider invocation may still repeat after an uncertain remote
  failure; the contract is exactly-once **durable output effect**, not
  exactly-once Provider execution.
- Context is server reconstructed from persisted eligible turns, bounded before
  the runtime call, and does not contain client-supplied history or option data.
- Stored historical options carry provenance/revisions but are only advisory;
  every Plan change remains behind a fresh protected classification/confirmation
  path.
- Application-level field encryption and end-to-end encryption are not claimed.

## 9. Residual risk and known limitations

No P0 or P1 application defect was found in this final audit. No closed finding
is reopened merely because it has an older intermediate report.

The following are real boundaries, not local Gate-A failures:

1. The browser harness intentionally uses a deterministic local `ChatAgentRuntime`;
   it does not validate a live Provider's availability, policy, latency, cost, or
   uncertain-failure behaviour.
2. A 30-day physical-purge promise requires production scheduler ownership,
   observable overdue detection, and operational response; authorization-side
   expiry protections are already part of the local product boundary.
3. Production encryption at rest, backup encryption/lifecycle, database TLS,
   API HTTPS, and least-privilege service/database access require infrastructure
   evidence. They are not inferred from SQLAlchemy or local PostgreSQL tests.
4. This report does not prove deployment ordering/rollback against a production
   data volume, nor assess capacity, connection pools, timeouts, alerts, or
   incident runbooks.

## 10. Production-readiness checklist and required approvals

None of the following was executed in this audit. Each needs separately
authorized evidence before Gate B can be approved.

| Required approval/evidence | Gate-B requirement |
| --- | --- |
| Runtime / Provider enablement | Select the production runtime, authorize credentials and spend, validate timeouts, retries, safety failures, and recovery without exposing conversation text. |
| Deployment and migration | Approved migration plan, backup/restore rehearsal, rollout sequencing, rollback/guarded-downgrade decision, and production data-owner approval. |
| Data security | Verify RDS/database encryption at rest, encrypted backup lifecycle, authenticated TLS database transport, HTTPS API transport, key/access posture, and least privilege. Do not call this E2EE. |
| Purge operations | Assign scheduler ownership, retention/purge monitoring, overdue alerting, audit procedure, and a documented backup-retention lifecycle. |
| Runtime operations | Capacity/load budget, request/provider timeouts, worker/crash recovery observability, redacted telemetry/logging, dashboards, alert routes, and incident/rollback runbooks. |
| Production acceptance | Authorized staging/production smoke plan with no paid Provider or production data action until separately approved. |

## 11. Working-tree accounting

At audit start, tracked source had no pending changes. The following untracked
artifacts are intentional local evidence and must remain out of the source
commit:

```text
backend/test-results-pr06b4-e2e-control/
backend/test-results-pr06b4-e2e-fixture.json
backend/test-results-pr06b4-full-pytest.exit
backend/test-results-pr06b4-full-pytest.log
backend/test-results-pr06b4-local-e2e.stderr.log
backend/test-results-pr06b4-local-e2e.stdout.log
```

This report is the only tracked change created by the final audit. It is
committed locally after the checks in this report; no push or merge is part of
the decision.

## 12. Final decisions

### Gate A — PR-06B Local Integration Acceptance: **PASS**

The branch contains all reviewed B.1A–B.4 implementation/remediation commits,
the declared schema head, a clean tracked code baseline before this report, and
consistent cross-stage evidence. The final local PostgreSQL/Chromium acceptance
passes J01–J10 and Runtime-off; the final full backend baseline applies to the
reviewed code state. Frozen security contracts for ownership, membership
incarnation, revocation, idempotency/recovery/fencing, bounded context,
historical-option freshness, deletion, and legacy compatibility remain intact.

### Gate B — Production Readiness: **NOT ASSESSED / NOT APPROVED**

Gate A is not deployment authorization. The approvals and infrastructure/runtime
evidence in Section 10 are required before any production readiness decision.
