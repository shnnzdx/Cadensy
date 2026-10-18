# PR-06B.1A.3 — Frontend Authenticated Join Integration & E2E Acceptance

Status: **PASS for the PR-06B.1A frontend integration gate, subject to the
normal code-review and deployment gates.** This is local/disposable-database
evidence only; it is not a production-readiness claim.

## Delivered changes

### Account and Guest invite flows

`trip/src/final/FinalApp.jsx` now makes the two server contracts explicit.

| User state | UI operation | Authority and payload | Result adoption |
|---|---|---|---|
| Verified Account session | `POST /api/invites/{token}/account-join` | Existing Account bearer via Account-only transport; body is `{}` and contains no email or User ID | Only the server response supplies `membership_id` and `trip_id`; the Account profile is presentation-only. |
| No Account session | `POST /api/invites/{token}/join` | Public, display-name-only `{ "display_name": ... }` | Requires the returned bounded Guest credential and adopts it only as a Guest Membership. |

The invite page loads `/api/account` through the existing Account-only request
scope before displaying **Join as [Account Name]**. That scope does not need a
Membership in the invite's target Trip, so it is safe to begin Account Join.
The client validates that an Account response contains no Guest credential and
that a Guest response contains one.

The old email field and its public email-bearing join call are removed. The
Trip state method also whitelists the Guest payload, so a future stale caller
cannot forward an email through that public API wrapper.

### Login/register return to invite

`shared/trip-navigation-policy/invite-auth-return.js` produces an internal
`/trip-app/index.html#/join/<encoded token>` return path and only permits a
single-slash, same-origin relative `next` value. It rejects `//host`, absolute
URLs, and backslash paths. Login carries that validated target through to
signup and signup carries it back to login; both navigate to it after Account
authentication. The token remains in Cadensy's own invite route, is not used
as Account proof, and is revalidated by the subsequent Account Join request.

The route is intentionally not placed in telemetry or an unrelated external
URL. Existing cached clients which still send Guest `email` receive the frozen
`409 account_join_requires_authenticated_session` response and cannot create
a User or Membership through this UI path.

### Membership incarnation and session safety

Before any completed join is adopted, `TripAppState` clears Membership-scoped
React data (Trip/Plan/actions/preferences/notices/current member) and then
adopts the returned Trip and Membership IDs. A fresh Account rejoin therefore
cannot show Membership A's cached data while Membership B is being hydrated.

The present `session-runtime` intentionally has one active technical bearer:
adopting a Guest credential would replace an Account credential. To prevent
that unsafe transition, an active Account session is offered only Account
Join; Guest Join is explicitly available only to signed-out users and the UI
directs a signed-in user to a separate browser session for Guest use. This is
a fail-closed V1 choice, not dual-identity storage.

An Account `401` invalidates only the Account session through the established
runtime path and presents reauthentication. A Trip-local quarantine/removal
denial does not globally log an Account out; a deterministic `409` is shown
without retry. Cached Trip state cannot restore access because every refresh
uses the backend authorization path.

## API error mapping

| Backend outcome | UI treatment |
|---|---|
| `404 Invite not found` | Unavailable-invite screen; no retry. |
| `401` Account authentication required/expired | Safe sign-in-again explanation; runtime clears invalid Account material. |
| `409 account_join_requires_authenticated_session` | Sign-in guidance, never Guest fallback. |
| `409 membership_change_pending_decision` | Explains that join/rejoin is temporarily unavailable while a group decision is in progress; no retry. |
| `409 Membership is quarantined` | Access-denial message only; no self-service restore control or private reason. |
| `403 Legacy identity is not an authenticated Account` | Explicit authenticated-Account/legacy-identity denial; no identity adoption. |
| Other non-success | Generic safe failure; no mutation is assumed. |

## Current and historical decision acceptance

No decision writer or state machine was changed in 1A.3. The reusable
disposable-PostgreSQL suite reconfirmed the finalized 1A.2 rules:

- joins/rejoins are rejected during a nonterminal Round or Proposal without a
  partial Membership/GuestSession write;
- new Rounds freeze immutable `electorate_count`; legacy closed null-count
  Rounds report unavailable counts and legacy open null-count Rounds cannot
  settle;
- quarantine does not change a frozen electorate, cast a vote, settle, or
  apply a decision; Vote and ProposalDecision ownership remains immutable;
- quarantined/removed Memberships are excluded from current roster,
  constraints, and new decision participants, while global Account sessions
  remain valid for separately authorized Trips.

Source evidence is in `backend/app/api/main.py` (`/join` and
`/account-join`), `backend/app/domain/transactions/membership_join.py`, and
the focused lifecycle/fairness tests listed below.

## Changed files

- `trip/src/final/FinalApp.jsx`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/invite-join-flow.js`
- `shared/trip-navigation-policy/invite-auth-return.js`
- `frontend/app/login/page.tsx`
- `frontend/app/signup/page.tsx`
- `frontend/tests/pr06b1a3-invite-join.test.mjs` and the affected existing
  session/navigation characterization tests
- `frontend/public/trip-app/index.html`, `embed-manifest.json`, and the
  fingerprinted Trip JavaScript asset, regenerated by `build:trip-preview`

No backend application code, migration, Conversation schema, AWS resource,
Provider integration, or PR-06C file changed.

## Verification evidence

All database tests used `postgresql+psycopg://.../tripsync_test`, which the
test harness validates as disposable and recreates. `MOCK_AI=1`,
`DISABLE_SCHEDULER=1`, and `DEV_ALLOW_MEMBERSHIP_HEADER=0` were explicitly set
for the test process.

| Command | Result |
|---|---:|
| `node --test tests/session-runtime*.test.mjs tests/trip-navigation*.test.mjs tests/trip-preview-integration.test.mjs tests/pr06b1a3-invite-join.test.mjs` from `frontend/` | 131 passed after the final characterization update |
| `npm test` from `frontend/` | 13 passed |
| `npm run build` from `frontend/` | passed (only existing chunk-size advisory) |
| `npm run build:trip-preview` from `frontend/` | passed; generated embedded Trip artifact synchronized |
| Focused PostgreSQL lifecycle/auth/fairness command (`test_invites`, `test_pr06b1a2_membership_lifecycle`, `test_pr06b1a_membership_policy`, protected Round and Proposal writers) | 46 passed |
| Full backend `pytest -q --tb=short` | 1152 passed; 10 known Alembic deprecation warnings |
| `git diff --check` | passed |

`npm run lint` remains **failing** on pre-existing repo lint debt (including
`SessionAwareLink.tsx`, `SiteHeader.tsx`, `privacy/page.tsx`, and linting
generated `public/trip-app` bundles). This change removes the newly introduced
signup synchronous-effect violation; the login page retains its pre-existing
account-created state initialization warning. The lint result is not counted
as a passing check.

The first focused backend attempt was stopped by a pre-existing process
environment setting `DEV_ALLOW_MEMBERSHIP_HEADER=1`; that fail-closed
production safeguard is expected. Re-running the same disposable suite with
the approved value `0` passed 46/46.

## Limitations and release notes

- Browser E2E was **not run**: no local browser harness was started, so this
  report does not claim browser E2E PASS. The frontend evidence is pure-flow,
  source-integration, build, and backend HTTP/database coverage.
- The embedded Trip artifact is synchronized locally. Deployment must deploy
  the frontend containing `/api/invites/{token}/account-join` support before
  enabling this UI; rollback must not revive email-as-identity.
- Account/Guest simultaneous technical identities remain intentionally out of
  scope. The UI prevents the only unsafe overwrite path rather than claiming
  dual-session support.

## Commit and recommendation

Implementation commit: `7cb963bd132bd2f99bd259343edfc05ad23b790d`
(`feat: integrate authenticated invite joins`). Recommended gate: **PASS —
ready for PR-06B.1A final review**, with browser E2E/deployment validation
remaining a separate release activity. Stop here; PR-06B.1B is not started.
