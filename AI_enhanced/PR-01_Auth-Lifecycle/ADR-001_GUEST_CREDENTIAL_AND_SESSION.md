# ADR-001 — Bounded Guest Credential and Session

Status: accepted and implemented by PR-01B locally. No production migration or deployment is authorized by this ADR.

## Context

PR-01A established that a `TripMembership.id` was previously returned after an
anonymous invite join, persisted in the browser, and accepted through
`X-Membership-Id` when compatibility was enabled. A membership ID is an object
identifier, not proof of identity. `AuthSession` cannot model this access: it
requires an account `user_id` and is account-wide.

PR-01B replaces that mechanism with a persisted, bounded Guest credential.
`DEV_ALLOW_MEMBERSHIP_HEADER` is retired as an authentication switch: the API
does not authenticate it in any environment, and a truthy value fails startup
when `APP_ENV` is `production` or `prod`.

## Three separate lifecycle operations

| Operation | Object changed | Implemented rule |
|---|---|---|
| Invite revocation | `InviteLink` | Blocks future preview and join for that link. It does **not** remove an already joined Guest or revoke that Guest's valid credential. |
| Guest-session revocation | one `GuestSession` | Sets `revoked_at`; that one bearer fails on every later request while the membership may remain joined. Guest logout uses this operation. |
| Membership removal | `TripMembership` plus all bound `GuestSession` rows | Sets membership `status` to `removed` and revokes every unrevoked bound Guest session before the enclosing request commits. Further authorization fails closed. |

These operations are deliberately non-interchangeable. Revoking a link must
not be documented or tested as a way to evict a joined Guest.

An account bearer is intentionally account-wide, so removing one account
membership does not revoke that account's entire login (it may authorize other
trips). The removed membership is excluded from account login/list output and
cannot satisfy trip authorization. Guest credentials are narrower, so all
credentials bound to a removed Guest membership are revoked.

## Final decision

`GuestSession` is a server-side credential record with `membership_id`,
`trip_id`, unique `token_hash`, `expires_at`, nullable `revoked_at`, and audit
timestamps. Alembic revision `54e8dd1ca624` adds it after immutable PR-05A
root revision `f9ff23b7b84d`.

### Credential, hash, and lifetime

* A successful anonymous invite join creates a new anonymous membership and
  returns `gst_` + `secrets.token_urlsafe(32)` exactly once. `token_urlsafe(32)`
  supplies 256 bits of random input before the presentation prefix.
* The database stores only `SHA-256(raw_token)`, never the raw bearer. This is
  appropriate for a non-user-chosen, high-entropy capability: a database dump
  cannot directly replay the hash. It is not a replacement for TLS, XSS
  defenses, or rate limiting.
* The server-enforced TTL is seven days. Missing, expired, revoked, malformed,
  or membership-inconsistent credentials return unauthorized; the browser
  cannot extend validity.
* There is at most one unrevoked Guest session per membership, enforced by a
  PostgreSQL partial unique index. Issuance revokes any active session for that
  membership before inserting the replacement. PR-01B exposes no anonymous
  "membership ID to new token" exchange endpoint, so repeated anonymous joins
  create distinct memberships and distinct credentials. A future authenticated
  reissue endpoint must map a concurrent unique-constraint conflict to a
  defined retry/result rather than treating it as successful rotation.

### Authentication precedence and scope

* A valid account bearer is resolved before Guest classification, including the
  astronomically unlikely case where an account token begins with `gst_`.
* If no account session is valid, only a `gst_` bearer can resolve a Guest
  session. An invalid account bearer never falls back to request headers or
  browser metadata.
* `X-Membership-Id` is ignored for authentication. A valid account bearer
  determines the account; `X-Trip-Id` only selects its trip membership. A
  Guest bearer is bound to its recorded trip, and an attempted different trip
  is forbidden.
* A Guest bearer cannot satisfy account-only endpoints such as `/api/account`.

### Logout, restoration, and browser threat model

`shared/session-runtime` is the only browser owner of the raw Guest token. It
keeps the token in the existing persisted storage mechanism so that a browser
restart can restore a trip session; technical facts and UI components receive
only Guest state, trip, and membership identifiers. A restore sends the bearer
to the server; it does not trust stored membership metadata as authorization.
An authorization invalidation clears the Guest token and trip context.

Guest logout calls `POST /api/auth/logout` with the Guest bearer, which revokes
that exact server record. The runtime clears local material even if that
network request fails. Therefore a failed logout can leave a copied/stolen
credential usable until its TTL, membership removal, or a successful later
revoke. Persisted browser storage has the same XSS and shared-device exposure
class as the existing account bearer; PR-01B does not claim to solve that
browser threat. CSP/XSS hardening, rate limiting, and server-side revoke retry
are follow-up concerns.

### Legacy transition and backout

No raw membership ID is exchanged for a Guest credential. On restore, legacy
membership/trip storage without a Guest token is cleared and produces no
authenticated facts. Existing membership records are preserved; an old Guest
must join with a still-valid/new invite or use a separately approved future
rebind flow. This avoids turning an enumerable object ID into a new secret.

Any rollout backout must keep raw membership headers disabled. If a Guest-flow
application regression occurs, disable Guest entry or use a reviewed
forward-fix. Downgrading `54e8dd1ca624` drops `guest_session` and is only safe
for an empty, disposable database; it is not a production session-preserving
rollback.

## Implementation consequences

1. The additive migration is required; `Base.metadata.create_all()` and the
   legacy compatibility initializer cannot create `guest_session`.
2. All request authorization is server-validated against token hash, expiry,
   revocation, anonymous-membership state, and trip binding.
3. Invite revocation, Guest-session revocation, and membership removal have
   separate routes/service effects and separate tests.
4. New tests use account bearer authentication for organizer actions. A raw
   membership header appears only in explicit negative security cases.
5. The current membership-removal endpoint is API-level foundation, not a
   claim that every product UI now exposes removal controls.

## Verification and non-goals

PR-01B has local disposable-database and frontend-runtime evidence in
`12_PR01B_BOUNDED_GUEST_CREDENTIAL.md`. It does not authorize AWS access,
deployment, real database migration, provider calls, or a broad auth-fixture
rewrite outside its bounded scope.
