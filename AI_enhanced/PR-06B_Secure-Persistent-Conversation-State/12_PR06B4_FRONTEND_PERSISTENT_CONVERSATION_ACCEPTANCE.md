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
assistant message, then selection calls the owner-scoped durable resolver for that
exact Conversation, Assistant Message, and Option. The resolver re-authorizes the
owner and parent chain, validates scope/target/patch, requires captured revisions to
match the current Plan and facts, and rejects an open Round or waiting Proposal
before returning a fresh classification. Only a ready server result can expose the
existing protected `submitChange`/confirmation flow. A missing, deleted, stale, or
decision-conflicted target refuses safely; selection never auto-applies an old patch.

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

## Final acceptance remediation — runtime capability and pending recovery

The focused final review found two real gaps in the original frontend integration:
the default-off Runtime still allowed the UI to submit a message that could remain
pending, and a refreshed browser had no server-issued Submission ID from which to
read status. Both are remediated locally.

- `_conversation_metadata` now returns the server-calculated `runtime_available`
  capability. It is true only for the explicitly admitted, non-production
  request-bound Runtime. Personal and Plan composers show saved history but disable
  new sends when it is false; they neither call legacy `/chat` nor enable the gate.
- The owner-authorized history response now attaches a minimal Submission projection
  only to its persisted User Message: ID, state, attempt, lease expiry, and assistant
  ID. The query repeats conversation, Trip, and membership ownership predicates.
  Another member gets the existing non-disclosing 404 rather than this projection.
- On hydration, `useDurableConversation` derives pending state only from that
  server-issued projection and immediately reads the authoritative status. A restored
  pending operation has no client text or original idempotency key, so it cannot be
  replayed as a new send. After lease expiry it can use a newly scoped recovery key
  through the existing recovery route; the server alone owns attempt/fence.

The new HTTP lifecycle test simulates admission, browser loss, owner history reload,
lease expiry, recovery, and one durable completion: `1 passed in 1.44s`. The updated
durable frontend/client, option, and Plan tests report `17 passed`; the synchronized
Trip build completed with 60 Vite modules.

### Historical Option call-path review

The server supplies Options only under their assistant message in
`conversation_messages`. `advisoryCandidatesFromPersisted` retains server option ID,
ordinal, target, captured revisions, and safe display patch without an Apply method.
`useAssistantChangeRequestFlow.selectCandidateOption` calls
`POST /api/trips/{trip_id}/conversations/{conversation_id}/messages/{assistant_message_id}/options/{option_id}/resolve`.
The domain resolver verifies the persisted parent/owner/scope, target and captured
revisions, then applies a read-only pending-decision guard before fresh
classification. It returns a safe no-op/stale/unavailable/conflict note rather than
an executable candidate when that proof fails. Only `applyProposal` invokes the
existing protected `submitChange` path after user action. There is no client replay
of the stored patch to a generic classify endpoint, cross-conversation lookup, or
direct Plan mutation.

### Browser matrix and final status

Playwright Chromium is installed, but the repository contains only
`frontend/tests/aws-public-e2e.mjs`, an AWS-public test. There is no local browser
fixture/server harness for Account sessions, disposable PostgreSQL, or the explicitly
enabled fake Runtime required by Journeys 1–10. It was not run because this review
forbids AWS/production access. Browser acceptance therefore remains **pending**, not
claimed from component or Node tests.

**PR-06B.4 Local Product Acceptance: CHANGES REQUIRED** — focused source, API,
PostgreSQL and frontend checks pass, but browser Journey evidence and an end-to-end
full backend-suite terminal record remain required. **Production Readiness: NOT
ASSESSED / NOT APPROVED** — no Provider, AWS, deployment, or production database was
used. This remediation is recorded in the subsequent local commit.

## PR-06B.4D.1 — local Browser E2E harness and execution attempt

The former missing-harness finding is remediated in source.  The new harness is
deliberately local-only:

- `backend/scripts/run_pr06b4_local_e2e.ps1` accepts only a local host and an
  explicitly named `cadensy_pr06b4_e2e_*_test` database. It validates TCP
  reachability before its one database reset, runs Alembic to `head`, seeds a
  synthetic Account/Trip/Membership/Plan fixture, starts hidden local FastAPI and
  frontend processes, and tears them down.
- `backend/scripts/pr06b4_e2e_fixture.py` provides fixture creation and database
  assertions; it never creates or drops databases.
- `backend/app/agents/e2e_runtime.py` is a deterministic implementation of the
  existing `ChatAgentRuntime` interface. Its selection requires
  `CADENSY_E2E_FAKE_RUNTIME=1` and the runtime factory refuses it in production.
  It still crosses the real scoped capability, PR-06B context builder,
  submission/coordinator, Preview, and fenced completion path. Its file barrier is
  not a public HTTP control endpoint.
- `frontend/tests/pr06b4-local-browser-e2e.mjs` opens installed Chromium, signs in
  using the actual `/login` UI, drives the embedded Trip UI, records real HTTP
  response evidence, and invokes only fixture-side SQL assertions for durable
  effects. It contains J01, J02, J03, J06, J07, J09, J10 and the separate
  runtime-off scenario; J04/J05/J08 are deliberately recorded as non-passing until
  they can call protected lifecycle/Preview-Apply operations without direct-table
  shortcuts.

### Execution result

The authorized local execution command was:

```powershell
& .\backend\scripts\run_pr06b4_local_e2e.ps1
```

It stopped before any database mutation with the exact result:

```text
Local PostgreSQL is not reachable at 127.0.0.1:5432; no database was changed.
```

`Test-NetConnection 127.0.0.1 -Port 5432` returned `False`. Therefore no browser
journey is marked PASS, no Alembic migration was run by this harness, and no
PostgreSQL catalog or durable-row evidence is claimed. This is a local-environment
blocker, not an inference from unit tests. The independently recorded backend full
suite terminal evidence remains `1181 passed, 10 warnings in 124.02s` with exit
code 0 under `MOCK_AI=1`, `DISABLE_SCHEDULER=1`, and
`DEV_ALLOW_MEMBERSHIP_HEADER=0`.

### Browser acceptance matrix

| Journey | Status | Evidence / next executable action |
| --- | --- | --- |
| J01 | BLOCKED | Start local PostgreSQL on `127.0.0.1:5432`, then run the harness command; the test performs real UI login, send, refresh and database assertion. |
| J02 | BLOCKED | Included in the harness: product sign-out then UI re-login and history restore. |
| J03 | BLOCKED | Included: second isolated Chromium context and authenticated history read. |
| J04 | BLOCKED (design gap) | There is no organizer removal UI. The harness refuses to mutate Membership tables directly; add a test-only caller of the existing protected removal kernel or authorize a protected API test flow. |
| J05 | BLOCKED (design gap) | The deterministic Runtime barrier exists, but quarantining must call the protected writer rather than a fixture SQL update. |
| J06 | BLOCKED | Included: real durable HTTP idempotency replay from the browser context plus durable row count assertion. |
| J07 | BLOCKED | Included: personal/global/item resolution and distinct Conversation IDs. |
| J08 | BLOCKED (design gap) | The deterministic Runtime is reply-only. A fixture candidate option plus a real protected stale Preview/Apply route is needed before this can be a genuine browser journey. |
| J09 | BLOCKED | Included: delete contract followed by authenticated non-restoration read. |
| J10 | BLOCKED | Included: captured browser HTTP trace asserts no legacy `/api/chat` call. |
| Runtime-off | BLOCKED | Included as a separately restarted backend process; it checks server history/readability, disabled composer, no running submission, and no legacy fallback. |

### Local verification of the harness code

| Command | Result |
| --- | --- |
| `cd backend; .\.venv\Scripts\python.exe -m compileall -q app\agents scripts\pr06b4_e2e_fixture.py` | passed |
| `cd backend; .\.venv\Scripts\python.exe -c "... build_chat_runtime(...) ..."` with explicit E2E env | passed; selected `LocalE2ERuntime` |
| `node --check frontend/tests/pr06b4-local-browser-e2e.mjs` | passed |
| `& .\backend\scripts\run_pr06b4_local_e2e.ps1` | blocked safely before database creation because local PostgreSQL was unreachable |

**Gate A: CHANGES REQUIRED.** The E2E harness now exists and fails closed, but its
browser matrix cannot be executed until local PostgreSQL is running; J04/J05/J08
also require a reviewed protected-operation test seam. **Gate B: NOT ASSESSED /
NOT APPROVED.** No AWS, production database, Provider, deployment, push, or merge
was used.

### Execution completion — local PostgreSQL recovered

The machine-local PostgreSQL instance documented in `backend/LOCAL_DEV.md` was
started with its documented `pg_ctl` command and reached `accepting connections`.
The runner then recreated only `cadensy_pr06b4_e2e_local_test`, applied Alembic
through `b7d3a1e9c642`, seeded its synthetic fixture, used the explicitly local
Runtime adapter, and ran installed Chromium from the existing local Playwright
cache. No AWS, Provider, production database, or application production setting was
used.

| Journey | Final local result | Evidence |
| --- | --- | --- |
| J01 | PASS | Real login UI → dashboard → Plan → Chat; send, fake-Runtime completion, refresh; PostgreSQL asserted 1 Conversation, 1 User Message, 1 Submission, 1 Assistant Message. |
| J02 | PASS | Product sign-out followed by a fresh Account login restored the persisted history. |
| J03 | PASS | A second isolated Chromium context with a separate Account bearer read the same authorized private history. |
| J04 | BLOCKED | No current organizer removal UI and no approved protected test caller; direct Membership table mutation remains forbidden. |
| J05 | BLOCKED | The deterministic Runtime barrier exists, but it still needs an approved protected quarantine caller. |
| J06 | PASS | Browser-origin durable replay returned HTTP 202 twice for one idempotency key; database assertion remained 2 user messages, 2 submissions, 2 assistant messages total. |
| J07 | PASS | Browser-origin resolves returned separate HTTP 201 Conversation identities for personal, Plan global, and item contexts. |
| J08 | BLOCKED | The deterministic Runtime deliberately returns reply-only output; a fixture advisory option plus a protected stale Preview/Apply caller is still required. |
| J09 | PASS | Browser-origin DELETE returned 204 and subsequent authenticated GET returned the required non-disclosing 404. |
| J10 | PASS | Captured browser/API trace contained no legacy `/api/chat` request. |
| Runtime-off | PASS | Existing history rendered, composer was disabled, no legacy fallback appeared, and no fabricated assistant result was displayed. |

Runner reliability corrections made during the execution: the fixture script now
bootstraps the backend import root, the PowerShell runner fails on native command
errors and terminates its spawned process tree, uses a dedicated local frontend
port, and waits for both backend and frontend before Chromium begins. The browser
harness now follows the actual UI path (dashboard `Review current plan`, then Trip
`Chat`) and explicitly uses an already installed Chromium executable rather than
downloading one.

Artifacts from this execution are local test evidence only:
`backend/test-results-pr06b4-local-e2e.stdout.log` and
`backend/test-results-pr06b4-local-e2e.stderr.log`.

**Gate A remains CHANGES REQUIRED** solely for J04, J05, and J08. **Gate B remains
NOT ASSESSED / NOT APPROVED.**

## PR-06B.4D.2 — Protected Lifecycle and Historical Option Final Acceptance

### Scope and changed files

This closure uses only the explicitly authorized local seams: an authenticated
Organizer invokes existing protected Membership/Plan HTTP routes; affected
Accounts use actual Chromium and the embedded Trip UI; fixture SQL is restricted
to initial synthetic data and committed-state assertions. No test-only public
route, direct lifecycle mutation, Provider, AWS, RDS, production secret, push, or
merge was used.

- `backend/app/agents/e2e_runtime.py` — local-only fake Runtime returns one
  deterministic safe option only for the `[E2E-OPTION]` marker and otherwise
  preserves the existing scoped capability, Preview, and coordinator path.
- `backend/scripts/pr06b4_e2e_fixture.py` — synthetic Account A and unrelated
  Trip setup plus read-only ownership, idempotency, option, quarantine and
  revision assertions.
- `backend/scripts/run_pr06b4_local_e2e.ps1` — local test-only protected booking
  gate and HMAC material in the isolated process.
- `frontend/tests/pr06b4-local-browser-e2e.mjs` — authenticated protected
  lifecycle scenarios and strengthened browser evidence.

### J04 — Removed A and rejoined B

Chromium authenticated Account Owner A, created private history, then a separate
Organizer bearer called `DELETE /api/trips/{trip_id}/members/{A}` with a valid
idempotency key and `X-Expected-Trip-Facts-Revision: 0`. The writer returned 200
and revision 1. A's old browser context immediately received a non-successful
read (403). The Organizer created a real invite; A used
`POST /api/invites/{token}/account-join`; the response returned a distinct B ID.
After browser reload B's `/api/me` identity was B, B could not hydrate A's text,
and B's attempted read of A's conversation returned the non-disclosing 404.

The committed PostgreSQL assertion recorded: A `removed`, exactly one non-removed
Account Membership for the same User/Trip, and the Conversation owner still A.
This also proves no guest-to-account adoption or historical owner rewrite. The
Account token itself remained valid until ordinary logout; removal only revoked
Trip access.

### J05 — Quarantine fencing a held Runtime completion

The browser posted one durable user message, received committed admission, and the
local Runtime reached its non-HTTP file barrier. Before release, the separate
Organizer created a sentinel invite and invoked the existing protected quarantine
route with its bearer, idempotency key, and current facts revision 2. The route
returned 200 and facts revision 3. Only then did the harness release the Runtime.

The ordered committed evidence is:

```text
Submission (one User Message) committed
  -> Runtime barrier entered
  -> protected quarantine committed
  -> Runtime released
  -> coordinator completion discarded
```

The database assertion found the target quarantined for `security_incident`, the
sentinel Trip invitation revoked, zero applicable active bound Guest sessions,
zero Assistant Messages and zero Options in that Conversation. The participant
browser lost Trip-X Conversation access (403) after reload, while `/api/account`
and a GET of the pre-authorized unrelated Trip both returned 200. Quarantine did
not settle or apply a decision.

### J08 — persisted historical option and owner-scoped revalidation

The participant used the real Plan item assistant drawer. `[E2E-OPTION]` crossed
the existing coordinator and `RevisionBracketedPreview`, creating an Assistant
Message with one persisted safe option. PostgreSQL confirmed its generated option
ID, Assistant parent, target PlanItem, `{ "start_hour": 15.0 }` patch and captured
`plan_revision=2` / `planning_facts_revision=2`; browser history returned it.

The Organizer then used the existing protected booking API for that target with
the option's captured plan revision and an idempotency key. It returned 200; the
read-only database assertion proved the authoritative Plan revision advanced to
3. On browser reload, selecting the historical option issued the existing
owner-scoped durable resolve call (200), which returned the safe stale-option note.
No `/changes` request was issued and the Plan stayed at revision 3. Thus the stored
option is advisory: selection rechecks its owner, parent, revisions, and decision
window before it can ever expose protected Apply.

### Strengthened existing journeys

- **J06:** fixture snapshots were taken for the exact conversation and
  `e2e-network-replay-key`: 0 Submissions/User Messages before, then exactly 1
  of each after two 202 requests with the same key.
- **J07:** Personal, Plan Global, and Plan Item conversations received
  distinguishable persisted messages, had three different server IDs, survived a
  browser refresh, and each read-only assertion retained the same owner identity.
- **J09:** the deletion 204 was followed by actual browser reload and a 404 read.
- **J10:** the old client actually posted
  `/api/trips/{trip_id}/chat` and received 200; the durable Conversation count
  stayed 3 before and after. The durable browser trace itself made no legacy post.

### Final local browser matrix

| Journey | Result | Evidence |
| --- | --- | --- |
| J01 | PASS | Real login/UI send/refresh and committed owner/history assertion. |
| J02 | PASS | Product logout and fresh Account login restore history. |
| J03 | PASS | Separate Chromium context with separate bearer restores authorized history. |
| J04 | PASS | Protected removal/rejoin creates B, preserves A ownership, denies B history. |
| J05 | PASS | Held Runtime completion is fenced by protected quarantine; no assistant/option commits. |
| J06 | PASS | Exact idempotency-key before/after assertion. |
| J07 | PASS | Personal/global/item identities, messages, refresh, and owner isolation. |
| J08 | PASS | Coordinator-persisted option, protected revision change, owner-scoped stale rejection, no direct Apply. |
| J09 | PASS | Delete persists across browser reload. |
| J10 | PASS | Actual legacy route works without a durable write; durable UI has no fallback. |
| Runtime-off | PASS | Saved history loads; composer disabled; no legacy chat fallback. |

### Local verification

| Command | Result |
| --- | --- |
| `backend/scripts/run_pr06b4_local_e2e.ps1` | PASS. Local-only disposable DB `cadensy_pr06b4_e2e_local_test`; Alembic to `b7d3a1e9c642`; J01–J10 and Runtime-off all PASS. |
| `cd frontend; npm test` | PASS — 13 tests. |
| `cd trip; npm run build` | PASS — Vite build, 60 modules. |
| Targeted Membership/Conversation/Runtime/Preview pytest selection | PASS — 59 tests. |
| Full backend suite under local `cadensy_pr06b4_full_test` | PASS — 1182 passed, 10 warnings, exit 0, 110.19s. |
| `python -m compileall -q backend/app/agents/e2e_runtime.py backend/scripts/pr06b4_e2e_fixture.py` | PASS. |
| `node --check frontend/tests/pr06b4-local-browser-e2e.mjs` and `git diff --check` | PASS. |

The lone test warning category is the established Alembic `path_separator`
deprecation warning in `tests/test_trip_bound_phase1_migration.py`; no acceptance
failure or external integration was observed. Raw browser logs, fixture JSON,
control files, and full-pytest logs remain local untracked evidence and are not
source-commit artifacts.

**Gate A — Local Product Acceptance: PASS.** All required browser journeys and
relevant local regressions now have genuine Chromium, protected HTTP, and/or
committed PostgreSQL evidence. **Gate B — Production Readiness: NOT ASSESSED /
NOT APPROVED.** This result does not validate production infrastructure, backup
encryption, deployment, a live Provider, or runtime enablement.
