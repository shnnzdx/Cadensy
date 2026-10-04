# Cadensy PR-01B — Bounded Guest Credential and Secure Authentication

Status: implemented and verified only against local disposable databases and
synthetic identities. Production migration, deployment, AWS access, and any
subsequent roadmap PR remain out of scope.

## Scope, facts, and safety boundary

PR-01B is the approved remediation for PR-01A's raw membership-header
exposure. It preserves the existing account bearer path, adds a bounded Guest
credential for anonymous invite joins, and removes `X-Membership-Id` as an
authentication mechanism. It does not modify the immutable PR-05A root
revision, introduce a Guest reissue UI, or migrate an external database.

All database commands in this record used an explicitly created local
PostgreSQL database whose name matches `^cadensy_[a-z0-9_]+_test$`; each target
was local `localhost`, previously non-existent, and disposable. Test processes
received `DISABLE_SCHEDULER=1` and `MOCK_AI=1`. No AWS endpoint, project
runtime database, production configuration, deployment, or real provider was
contacted. The documented local PostgreSQL instance was started only after the
local-development instructions were reviewed and remains running; it was not
stopped as part of this work.

## Architecture decision

The final decision is recorded in `ADR-001_GUEST_CREDENTIAL_AND_SESSION.md`.
The relevant authorization path is:

```text
anonymous invite join
  -> validate non-revoked/non-expired InviteLink
  -> create joined, anonymous TripMembership
  -> create GuestSession(membership_id, trip_id, SHA-256(token), expiry)
  -> return raw gst_<256-bit-random> bearer once in join response
  -> session-runtime persists only within its credential boundary
  -> future trip request: Authorization Bearer + X-Trip-Id
  -> server verifies token hash, expiry, revocation, membership status, and trip binding
```

The browser's `membership_id` remains useful display/context metadata, but is
never a credential. No code path accepts it from `X-Membership-Id` as
authentication, regardless of `DEV_ALLOW_MEMBERSHIP_HEADER`.

### Credential policy

| Concern | Final rule | Reasoning / enforcement |
|---|---|---|
| Generation | `gst_` plus `secrets.token_urlsafe(32)` generated only during anonymous join. | Cryptographically strong 256-bit random input; no membership-ID-to-token exchange exists. |
| Database representation | SHA-256 hash only, unique. | A high-entropy capability does not need a password KDF; a database leak cannot directly replay the stored hash. Raw token is not persisted. |
| TTL | Seven days, checked server-side. | A browser clock/storage change cannot extend access. |
| Rotation / concurrency | Partial unique index permits one non-revoked session per membership; issuance revokes active session before replacement. | PR-01B has no anonymous reissue endpoint. Repeated joins yield distinct memberships/tokens. A future reissue endpoint must specify retry/conflict semantics. |
| Guest scope | Exactly the joined anonymous membership and its one recorded trip. | Cross-trip `X-Trip-Id` is rejected; Guest cannot call account-only routes. |
| Account precedence | Resolve a valid account session first, then a `gst_` Guest session. | Prevents even a prefix-shaped random account token from being misclassified. |
| Logout | Revoke only the presented Guest session server-side, then clear local state. | A failed revoke still clears local state; possible credential theft remains a bounded known risk until server invalidation/expiry. |
| Membership removal | Set membership `removed` and revoke all of its active Guest sessions in the request transaction. | Both the credential record and authorization relationship fail closed. |

### Lifecycle behavior matrix

| Input / event | Expected secure behavior | Evidence class |
|---|---|---|
| Valid account bearer + trip | Resolves only that account's active, non-removed membership. | Regression test. |
| Valid Guest bearer + bound trip | Resolves the stored joined anonymous membership. | Join/reopen test. |
| Bare or forged `X-Membership-Id` | `401`, whether compatibility config is unset, `0`, or `1`. | Security characterization. |
| Invalid account bearer + membership header | `401`; no fallback is attempted. | Authentication dependency design and negative header tests. |
| Valid account bearer + forged membership header | Account bearer wins; forged header is ignored. | API test. |
| Valid account bearer beginning `gst_` | Still resolves as account first. | API precedence test. |
| Guest bearer on `/api/account` | `401` account authentication required. | Scope test. |
| Guest bearer + another trip | `403`; bound Guest remains unable to read another trip. | Scope test. |
| Forged / expired / logged-out Guest bearer | `401`. | Lifecycle test. |
| Invite revoked after a join | Blocks later preview/join; existing joined Guest bearer remains valid. | Lifecycle test. |
| Membership removed | Existing Guest bearer is revoked and then returns `401`. | Removal/cascade test. |
| Account membership removed | Account session remains valid for any other trips, but the removed trip disappears from its account listing and trip access returns `403`. | Removal authorization test. |
| Repeated anonymous joins | Different anonymous memberships and different Guest tokens. | Rejoin test. |
| Legacy stored membership/trip without Guest token | Runtime clears it and restores no authenticated facts. | Frontend runtime test. |

## Implementation record

### Backend and migration

* `GuestSession` is a SQLAlchemy model with foreign keys to `trip_membership`
  and `trip`, unique token hash, expiry/revocation fields, a trip lookup index,
  and `one_active_guest_session_per_membership` partial unique index.
* Alembic revision `54e8dd1ca624` is additive and has parent
  `f9ff23b7b84d`. The PR-05A root revision was not edited.
* `app.domain.auth` issues, verifies, and revokes Guest credentials. It checks
  token hash, expiry, revoked status, anonymous membership, joined status, and
  persisted trip binding on every use.
* `current_membership` first validates an account bearer. Only when that fails
  does a `gst_` token enter the Guest validation path. `current_account_user`
  never treats Guest material as an account identity.
* `POST /api/auth/logout` revokes either the validated account session or the
  presented Guest credential. `DELETE /api/trips/{trip_id}/members/{membership_id}`
  supplies the API-level organizer operation for membership removal.
* The legacy compatibility initializer is fixed to its explicit PR-05A table
  list. It cannot silently create `guest_session`; new databases use Alembic.
  The legacy helper no longer adds ad-hoc schema changes.

### Browser/session-runtime transition

`shared/session-runtime` gained `adoptGuestAuth()` and privately owns the
persisted Guest token. Trip requests for a Guest send only `Authorization` and
`X-Trip-Id`; they never assemble `X-Membership-Id`. Account adoption removes
Guest material, Guest adoption removes account material, and account facts win
when both persisted forms are found. Invite-adoption cache is now a routing
hint only, not an authentication source.

`TripAppState`, bootstrap, invalidation, and invite join adoption use that
public runtime seam. They no longer read raw technical storage keys or use
environment-provided membership identity. A `401` Guest authorization
invalidation clears stored Guest material and trip context.

### Configuration and deployment source

`DEV_ALLOW_MEMBERSHIP_HEADER` now defaults to `0` in the checked-in backend
example and all three checked-in deployment workflow values. It is a retired
setting, not a compatibility feature: the server ignores it for authorization
in every environment, and startup rejects a truthy value under `production`
or `prod`. Documentation/runbook language now requires bearer credentials and
does not offer raw-header re-enablement as a rollback.

## Isolated verification results

### Backend HTTP and static contracts

The following ran after the disposable database target and test-process
configuration checks described above:

```powershell
cd backend
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr01b_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_pr01b_guest_credentials.py `
  tests/test_pr01a_auth_characterization.py `
  tests/test_auth.py tests/test_invites.py tests/test_api_config.py `
  tests/test_pr01b_guest_session_migration_contract.py `
  tests/test_alembic_baseline_contract.py
```

| Check | Result | What it proves |
|---|---|---|
| Combined authorized backend command | Pass: 39 passed in 5.96s | The PR-01B HTTP suite, PR-01A security conversion, existing Auth/Invite regressions, config test, and both static migration contracts completed against `cadensy_pr01b_test`. |
| PR-01B public HTTP credential suite | Pass | Hashed issuance, reopen, forged/expired/revoked rejection, scope, account precedence, invite revocation, removal cascade, and rejoin separation. |
| PR-01A security characterization | Pass | Former strict xfail is now a normal secure assertion; header-only identity is rejected even with compatibility setting `1`. |
| Existing auth/invite regression | Pass | Account login/logout and invite management operate via account bearer fixtures. |
| API configuration contract | Pass | Production rejects truthy retired-header configuration. |
| Migration static contracts | Pass | Additive revision/model/index/root lineage are fixed without opening a database. |
| Legacy seed/helper regression | Pass: 5 passed in 1.36s | The affected schema-helper imports and local seed/account upsert behavior remain covered without invoking real schema setup or external services. |

The PR-01A historical record remains historical evidence, not a continuing
authorization for raw-header behavior. New positive organizer tests use an
account bearer. Header use in the suite is limited to named negative security
assertions.

### Frontend session-runtime suite

```powershell
cd frontend
node --test tests/session-runtime.test.mjs `
  tests/pr01a-guest-session-characterization.test.mjs `
  tests/pr01b-guest-session-runtime.test.mjs `
  tests/session-runtime-tripappstate-bootstrap-cutover.test.mjs `
  tests/session-runtime-invalid-session-cutover.test.mjs `
  tests/session-runtime-characterization.test.mjs `
  tests/session-runtime-request-identity-cutover.test.mjs `
  tests/backend-runtime-config-guest-access.test.mjs
```

| Check | Result | What it proves |
|---|---|---|
| Focused session-runtime regressions | Pass: 45 passed, 0 failed | Guest adoption/restore derives bearer headers, never emits a membership credential, clears invalid legacy context, and attempts Guest server revoke on logout. |
| Runtime/config source checks | Pass | UI does not reintroduce identity headers/storage ownership; checked-in runtime workflows use secure `0`. |
| Existing frontend `npm test` | Pass: build plus 13 passed, 0 failed | The project frontend build, rendered HTML, trip-preview integration, and runtime configuration suite remain green. The build emitted only the pre-existing-style chunk-size advisory, not a failure. |

### Migration acceptance evidence

| Disposable target | Procedure | Result |
|---|---|---|
| `cadensy_pr01b_autogen_test` | Upgrade only through PR-05A root, then compare metadata/autogenerate candidate. | Candidate contained only additive `guest_session` table and its indexes. |
| `cadensy_pr01b_fresh_test` | `alembic upgrade head`, `current`, then `check`. | Head is `54e8dd1ca624`; seven `guest_session` columns, two foreign keys, expected unique/index objects; `No new upgrade operations detected.` |
| `cadensy_pr01b_baseline_upgrade_test` | Upgrade to root, insert synthetic sentinel account/trip/membership, then upgrade to PR-01B head and `check`. | Additive upgrade clean; all three sentinel counts remained one. |
| `cadensy_pr01b_baseline_upgrade_test` | Downgrade only `54e8dd1ca624` to root, check sentinel, re-upgrade head, then `check`. | `guest_session` removed by disposable-only downgrade; sentinel retained; re-upgrade clean. |

The prior observed legacy drift database containing
`change_proposal.extended_at` was not stamped, upgraded, repaired, or used in
this PR. PR-01B does not delete that column and does not claim compatibility
for a drifted existing schema.

## Production rollout and backout runbook — pending separate authority

This is a design/runbook only. Do not execute it under PR-01B authorization.

1. Identify the exact authorized target and take an approved, tested backup.
   Stop for AWS, production, an unknown database, or a database without a
   restore plan.
2. Verify it is already at PR-05A root through the approved preflight. Run a
   read-only metadata comparison. Stop on any drift, including
   `change_proposal.extended_at`; do not use `upgrade` to force a drifted
   schema to fit.
3. Review the exact additive revision and apply only
   `54e8dd1ca624` under change control. Confirm `alembic current` and
   `alembic check` afterward.
4. Set `DEV_ALLOW_MEMBERSHIP_HEADER=0`; a truthy value in production should
   prevent startup. Verify the app version uses bearer-only authorization.
5. Run approved synthetic smoke cases: account login/reopen, Guest join,
   Guest reopen after browser restore, logout/replay rejection, cross-trip
   rejection, invite revocation behavior, and membership removal.
6. Monitor authorization failures and Guest join errors without logging raw
   bearer credentials.

Backout does **not** mean setting the retired header flag to `1`. If the
application flow regresses, disable Guest entry at a product/release boundary
or ship a reviewed forward fix while retaining bearer-only authorization.
`downgrade 54e8dd1ca624` drops Guest sessions and is acceptable only for an
empty disposable database; an operational rollback requires the approved
backup/restore plan and explicit data/session-loss decision.

## Known risks and deliberately unverified scope

* Persisted browser bearer credentials remain susceptible to XSS and shared
  devices. PR-01B limits server-side lifetime/revocation but does not eliminate
  client theft; CSP/XSS hardening and device/session management remain work.
* A Guest logout whose network request fails clears local storage but may leave
  a server credential valid until seven-day expiry, membership removal, or a
  successful later revocation.
* There is no authenticated Guest-token rotation/rebind product flow yet. The
  one-active-session partial index provides the database invariant, but any
  future concurrent reissue endpoint needs explicit retry semantics and
  independent-session failure-injection tests.
* Existing broad backend tests outside the authorized Auth/Invite suite still
  contain legacy raw-header fixtures. They were not reclassified as secure and
  were not made green by restoring a compatibility bypass; their fixture
  migration is separately scoped follow-up work.
* No full production-like browser E2E, external database migration, AWS call,
  deployment, real provider request, or production smoke test was performed.

## Changed-files summary

| Area | Files |
|---|---|
| Credential/auth service | `backend/app/domain/auth.py`, `backend/app/domain/trips/service.py`, `backend/app/api/main.py`, `backend/app/db/models.py` |
| Schema foundation | `backend/alembic/versions/54e8dd1ca624_add_bounded_guest_sessions.py`, `backend/app/db/init_schema.py`, `backend/app/db/upsert_demo_login.py` |
| Backend tests | `backend/tests/test_pr01b_guest_credentials.py`, `backend/tests/test_pr01b_guest_session_migration_contract.py`, `backend/tests/test_pr01a_auth_characterization.py`, `backend/tests/test_invites.py`, `backend/tests/test_api_config.py`, `backend/tests/test_alembic_baseline_contract.py` |
| Browser/session runtime | `shared/session-runtime/index.js`, `trip/src/final/TripAppState.jsx`, `trip/src/final/FinalApp.jsx`, `trip/src/final/technicalSessionBootstrap.js`, `trip/src/final/technicalSessionInvalidation.js` |
| Frontend tests | `frontend/tests/pr01b-guest-session-runtime.test.mjs` and the revised session-runtime/config characterization suites |
| Safe configuration/runbooks | `backend/.env.example`, `backend/README.md`, `backend/LOCAL_DEV.md`, checked-in runtime workflow values, and AWS documentation source only |
| Decision records | this record and `ADR-001_GUEST_CREDENTIAL_AND_SESSION.md` |

## PR-01B stop point

PR-01B is complete at the authorized local implementation/documentation/test
boundary. The next work must be separately authorized; no PR-01C, framework,
provider, deployment, AWS, or database operation follows automatically.
