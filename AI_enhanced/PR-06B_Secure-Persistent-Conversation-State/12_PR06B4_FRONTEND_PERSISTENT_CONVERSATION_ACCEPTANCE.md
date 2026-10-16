# PR-06B.4 — Frontend Persistent Conversation Acceptance

## Scope and entry gate

This increment is local-only on `codex/pr06b-independent-development`.  It builds on
PR-06B.3 commits `b1e41c2`, `1700301`, and `40b1776`; PR-06B.3 recorded a local
PASS with 1,180 backend tests.  The durable API was rechecked against
`backend/app/api/main.py`: resolve/create, list/get, paged messages, submit (202),
submission status, recovery, and owner-authorized delete are the only new client
calls.  The request-bound Runtime gate remains default-off
(`CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED`); this PR neither sets it nor calls
a Provider.

## Delivered frontend architecture

- `trip/src/final/durableConversationClient.js` is the sole durable HTTP adapter.
  It is constructed from the existing Trip-scoped request seam, so it does not build
  bearer, Trip, or membership headers and it accepts no client history.
- `trip/src/final/useDurableConversation.js` owns Conversation identity resolution,
  paged server history, bounded 2.5-second status polling (12 attempts), stable
  submission/recovery idempotency keys, and delete state clearing. A generation
  token is bound to Trip ID, membership incarnation, and scope/context. Late
  resolve/history/status results cannot update a replacement identity.
- `FinalApp.jsx` uses `personal/personal`; `useAssistantChangeRequestFlow` uses
  `plan_assistant/global` or `plan_assistant/item:<32-hex-plan-item-id>`. UI mode
  is not part of the identity. The legacy `chatWithTrip` API/endpoint remains for
  old clients, but no migrated Trip UI call site invokes it and there is no dual
  write.
- Server message sequence, IDs, and pagination cursor determine UI ordering. No
  private history, option payload, credential, or conversation ID is persisted in
  browser storage, telemetry, logs, or a URL.

## Submission, recovery, and Runtime-off behavior

One intentional send creates one idempotency key. The key and canonical text stay
with the in-memory pending operation; a retry replays that exact request rather
than creating another user message. The UI refreshes server messages after
admission and only renders assistant text obtained from the completed server
history resource. HTTP 202 is never represented as completion.

Recovery uses a separate stable recovery key and only the server can grant a new
attempt/fence. The client never constructs a fence or a completion. It exposes the
server status plus a recovery action; a live lease conflict remains a safe backend
409. After bounded polling, a still-running request says that the user message is
saved and Runtime delivery may be disabled in this environment. It does not promise
an assistant result or enable the gate.

## Historical options, Preview, and Apply

`durablePlanOption.js` converts the server option projection into an explicitly
`advisory` display object containing its ID, ordinal, assistant-parent rendering
context, target item, safe display patch, and captured revisions. It exports no
Apply/mutation operation. The Plan drawer keeps the option attached to its persisted
assistant message, then selection verifies the target still exists and calls the
existing fresh `classify` path. Only its fresh verdict can expose the existing
protected `submitChange`/confirmation flow. A missing/deleted target refuses safely;
selection never auto-applies an old patch.

## Deletion, retention, and privacy

Personal and Plan Assistant controls identify their target as a private chat, require
a browser confirmation, call the owner-authorized DELETE route, and clear the local
identity/history only after 204. The next render resolves a newly allowed server
Conversation; it never restores deleted contents from stale state. This is an access
and primary-record deletion request, not a statement that backups are erased
immediately.

Removed/quarantined/expired/revoked identities receive the server's deliberately
non-disclosing 404. The hook treats it as terminal and its generation boundary
prevents old tabs from restoring cached private contents. Account sessions remain
global by design; Trip membership denial only blocks this Trip conversation. No
Guest-to-Account adoption or cross-device Guest recovery was added.

## Compatibility and rollout

Durable frontend rollout is source-controlled through the migrated surfaces above;
there is no client-side fallback from a failed durable request to legacy `/chat`.
Older clients retain compatible legacy `/chat` behavior until an explicitly approved
retirement phase. Production Runtime delivery remains disabled until a separate
environment/Provider approval. `frontend/public/trip-app` is generated only through
`frontend`'s `npm run build:trip-preview`; it is not edited directly.

## Changed files

- Durable client/state: `TripAppState.jsx`, `durableConversationClient.js`,
  `useDurableConversation.js`.
- UI integration: `FinalApp.jsx`, `PlanFeature.jsx`,
  `plan-feature/useAssistantChangeRequestFlow.js`, `durablePlanOption.js`.
- Tests: `frontend/tests/pr06b4-durable-conversation-client.test.mjs`,
  `frontend/tests/pr06b4-historical-option.test.mjs`, and the updated Plan boundary
  characterization test.
- Generated, synchronized preview artifact: `frontend/public/trip-app/*`.

## Local verification

| Category | Command / evidence | Result |
| --- | --- | --- |
| Durable adapter + option + Plan boundary | `node --test frontend/tests/pr06b4-durable-conversation-client.test.mjs frontend/tests/pr06b4-historical-option.test.mjs frontend/tests/plan-feature-characterization.test.mjs` | 16 passed |
| Embedded Trip artifact | `cd frontend; npm run build:trip-preview` | passed; Vite built 60 modules and synchronized `frontend/public/trip-app` |
| Frontend production/build regression | `cd frontend; npm test` | 13 passed; build passed (one existing chunk-size warning) |
| Session/membership boundary regression | `cd frontend; node --test tests/session-runtime.test.mjs tests/session-runtime-invalid-session-cutover.test.mjs tests/session-runtime-logout-cutover.test.mjs tests/session-runtime-technical-trip-adoption-cutover.test.mjs tests/trip-preview-integration.test.mjs` | 37 passed |
| PostgreSQL availability | `pg_isready -h localhost -p 5432` | accepting connections |
| Focused durable/lifecycle/context backend group | pytest with `DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, `MOCK_AI=1` over PR-06B.1A/1B/2 API/3 context/runtime tests | 29 passed in 4.17s |
| PostgreSQL concurrency/revocation lifecycle | the five individually collected cases in `tests/test_pr06b2_conversation_concurrency_postgres.py`, under the same local-safe environment | 5 passed (2 in 0.70s; 3 in 0.75s) |
| AI tool isolation | all 31 collected `tests/test_agents_tools.py` cases, split only to keep the runner output bounded | 31 passed (10 in 20.66s; 10 in 20.36s; 11 in 0.75s) |

The completed frontend checks use no Provider. The backend tests use local disposable
`tripsync_test` PostgreSQL through `TEST_DATABASE_URL` and `MOCK_AI=1`.

## Evidence limits and readiness

Local product acceptance is **partial PASS**: source build, embedded artifact,
durable-client contract tests, history/poll/retry semantics, option safety adapter,
and focused PostgreSQL-backed lifecycle/context tests pass. This is not yet proof of
real-browser E2E, live Provider behavior, production infrastructure, or production
rollout.

Required before final release acceptance: complete the full backend suite with a
recorded terminal result; execute browser Journeys 1–10 against a local fake Runtime;
and separately approve Runtime enabling/Provider/production deployment. No such live
Provider, AWS, or production operation was performed here.

Checkpoint commits: `b3d2c05` (4A), `a7eb6e2` (4B), `a0709a9` (4C).  The final 4D
documentation commit is recorded with this file.

**Recommendation: CHANGES REQUIRED for final PR-06B.4 acceptance**, solely because
the required full backend, browser E2E, and production-readiness evidence is not yet
complete. The frontend implementation itself is ready for the next local acceptance
test pass; do not enable Runtime delivery or deploy it from this recommendation.
