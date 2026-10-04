# Cadensy PR-01A — Authentication / Guest Identity Baseline

Status: closed after PR-01A sign-off corrections. This document preserves the PR-01A historical characterization and its then-intended target. PR-01B later implemented the remediation; see `12_PR01B_BOUNDED_GUEST_CREDENTIAL.md` and ADR-001 for the current contract.

## Confirmed public seams

The authorized seams are the public FastAPI HTTP API and the public `shared/session-runtime` API. Tests do not call `current_membership()`, `user_for_token()`, or storage-key helpers directly.

| Seam | Covered flows | Why it is the correct boundary |
|---|---|---|
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/account`, `GET /api/me` | account login, bearer restoration, expiry, revocation/logout | Browser/API contract, rather than auth-service internals. |
| `GET /api/invites/{token}`, `POST /api/invites/{token}/join`, `POST /api/invites/{id}/revoke`, `GET /api/trips/{trip_id}` | invite preview, guest join/reopen, invite revocation, cross-trip access | Makes the Guest identity proof and trip authorization observable. |
| `createSessionRuntime()` | guest technical-session restore and logout | Frozen owner of browser persistence and request-header derivation. |

All database cases use synthetic users, trips, invite tokens and the dedicated `cadensy_pr01a_test` database name only. The test harness drops/recreates that database; no shared/local runtime database is targeted.

## Authentication flow map

```text
Account login
email/password -> POST /api/auth/login -> raw bearer returned once
  -> AuthSession(token_hash, expires_at, revoked_at) -> browser session-runtime
  -> Authorization bearer + X-Trip-Id -> server resolves account membership for that trip

PR-01A observed legacy Guest invite
invite token -> public preview -> POST join(display_name)
  -> TripMembership(user_id=NULL, join_method=invite_guest)
  -> response exposes membership_id only
  -> browser persists trip_id + raw membership_id
  -> current compatibility path sends X-Membership-Id
  -> DEV_ALLOW_MEMBERSHIP_HEADER unset/1 accepts any existing membership id

PR-01A intended secure Guest invite (implemented later by PR-01B)
invite token -> GuestCredential returned once
  -> persisted GuestSession(token_hash, membership_id, trip_id, expires_at, revoked_at)
  -> scoped bearer/capability credential -> server verifies active record + trip binding
  -> revocation/expiry rejects subsequent reopen/access
```

## Guest identity behavior matrix

| Flow / input | Behavior observed by PR-01A | Intended secure behavior at that time | Classification |
|---|---|---|---|
| Account login | Returns bearer token; server stores only `AuthSession.token_hash` with expiry. | Retain. | Secure baseline. |
| Account reopen a trip | Bearer + `X-Trip-Id` resolves that account's membership; another user's trip is rejected. | Retain. | Secure baseline. |
| Account logout | Logout writes `AuthSession.revoked_at`; the same token is rejected afterward. | Retain. | Secure baseline. |
| Expired account token | Request is rejected. | Retain. | Secure baseline. |
| Invite preview | Valid public invite reveals safe trip summary and creates no membership. | Retain, with expiry/revocation checks. | Secure baseline. |
| Guest join | Creates a `TripMembership` with no user account and returns raw `membership_id`; no bounded Guest credential exists. | Return a one-time Guest credential bound to trip/membership. | Expected remediation. |
| Guest reopen / session restore | `session-runtime` restores raw trip/membership IDs and emits `X-Membership-Id`; server accepts it when flag is unset/`1`. | Verify a revocable Guest credential; never authenticate a raw membership ID. | Security characterization. |
| `DEV_ALLOW_MEMBERSHIP_HEADER=0` | Raw membership header is rejected. | Production/default behavior after PR-01B. | Secure configuration behavior. |
| Missing / forged membership ID | Rejected even when compatibility flag is on, unless it exactly names an existing row. | Reject. | Partial protection only. |
| Cross-trip request with a known header ID | Scoped route rejects access to a different trip. | Retain. | Defense in depth; does not fix identity forgery. |
| Invite revocation after Guest joined | Blocks preview/new join only; existing Guest continues under raw membership-header path. | Keep the joined Guest membership and its valid Guest credential active; invite revocation does not itself revoke a Guest session. | Expected remediation. |

## Verified findings and remaining risks

1. `AuthSession` already provides persistent token hash, expiry and revocation for accounts.
2. A Guest has no equivalent credential record. `TripMembership.id` is an identifier, not a secret proof, yet is currently accepted as one in compatibility mode.
3. Invite revocation and Guest credential revocation are different lifecycle events. The former invalidates the invite token; it does not invalidate an already-created Guest membership.
4. Trip-scoped resource checks prevent a known membership from reading another trip, but do not protect against possession of a membership ID for the same trip.
5. At the PR-01A snapshot, `DEV_ALLOW_MEMBERSHIP_HEADER` defaulted to enabled when unset. That P0 risk was not normalized: PR-01B retired header authentication, defaults checked-in configuration to `0`, and makes a truthy production setting fail startup.

## Isolated test execution record

The following command is permitted only after the named local database safety checks pass:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr01a_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_pr01a_auth_characterization.py -rxX
```

Execution evidence:

| Check | Result | Evidence / interpretation |
|---|---|---|
| Guest session-runtime characterization | Pass: 1/1 | `frontend/tests/pr01a-guest-session-characterization.test.mjs` proves current browser restore emits raw `X-Membership-Id` and Guest logout has no server credential to revoke. |
| Backend test source syntax | Pass | AST parse of `backend/tests/test_pr01a_auth_characterization.py` completed without importing the app or touching a DB. |
| Disposable DB target check | Pass | Target was explicitly constructed as local `localhost:5432/cadensy_pr01a_test`; name satisfies the harness test-only rule; scheduler and AI are disabled by test configuration. |
| PostgreSQL readiness | Pass after local documented startup | `pg_ctl -D D:\PostgreSQL\18\data start`, followed by `pg_isready -h localhost -p 5432`, confirmed the local instance was accepting connections before pytest. No cloud connection or runtime database was selected. |
| Backend authentication characterization | Pass: 8 passed, 1 strict xfailed | The nine synthetic HTTP cases ran only against `cadensy_pr01a_test`. The xfail proves the known raw-membership-header exposure and is intentionally not counted as a secure pass. |
| Existing auth/invite regression | Pass: 16/16 | `tests/test_auth.py tests/test_invites.py` passed against the same disposable database after the characterization suite. |

At PR-01A close, expected remediation cases were strict `xfail` records, not passing security claims. PR-01B converted the raw-header case into a normal passing security assertion; the historical result above is retained as migration evidence.

## PR-01A closure rules

* These legacy characterization results are evidence for migration design, **not** an unconditional PR-01B regression contract. PR-01B must preserve intended user outcomes while removing the raw-membership credential mechanism.
* The strict `xfail` that demonstrated the unsafe unset-default was converted by PR-01B into a normal, passing security test. It was not deleted or left xfailed.
* New non-legacy tests must use Account bearer authentication for Organizer actions. Raw `X-Membership-Id` is allowed only in an explicitly named legacy/security-characterization case that demonstrates the exposure being removed.
* Concurrency and Session Lifetime tests belong to their own safe seam: each concurrent worker owns an independent Session created from the factory. A shared request or pytest Session is not evidence of concurrent-session safety and must not be used as such.

## Changed-files summary

Added or revised in this PR:

* `backend/tests/test_pr01a_auth_characterization.py`
* `frontend/tests/pr01a-guest-session-characterization.test.mjs`
* `04_PYDANTIC_AI_INTEGRATION_DESIGN.md`, `05_RUNTIME_AB_EVALUATION_PLAN.md`, `07_IMPLEMENTATION_ROADMAP.md`, `09_PHASE0_REVIEW_AND_CORRECTIONS.md`
* this baseline and `ADR-001_GUEST_CREDENTIAL_AND_SESSION.md`

This PR does not modify `backend/app/`, production configuration, cloud resources, deployments, or real provider settings.
