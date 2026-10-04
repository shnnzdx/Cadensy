# Cadensy PR-01B — Post-sign-off Verification Follow-up

Status: tracked only. This record does not extend PR-01C into Authentication,
does not change a database, and does not claim that PR-01B has been deployed.

## Required independent follow-up

PR-01B's focused credential tests established the bounded credential behavior
against disposable PostgreSQL. The following acceptance work remains a
separately authorized verification item because its value is specifically in
cross-request persistence, not in reusing the shared test transaction.

| Follow-up | Required test design | Acceptance condition |
|---|---|---|
| Cross-request credential lifecycle | Use an independent SQLAlchemy `SessionLocal`/factory and separate committed transactions for Guest join, reopen, logout, server-side revocation, and membership removal. | A newly created session observes the persisted expected state; logout/revocation/removal credentials fail closed in a subsequent request. |
| Corrupt binding fail-closed | Deliberately create a GuestSession whose stored `trip_id` and `membership_id` point at different memberships/trips, then authenticate it through the public dependency. | Authentication returns `401`; it must not infer a corrected scope, repair the record, or authorize either trip. |

Neither case is implemented or run under PR-01C. They must use a named,
local, disposable PostgreSQL database and synthetic identities only. They are
not permission to re-enable `X-Membership-Id`, alter account authentication,
or operate an existing project database.

## Retained known risks

* A browser-persisted bearer remains exposed to XSS and shared-device use.
* If a Guest logout request fails on the network, the server credential can
  remain valid temporarily until expiry, membership removal, or a later
  successful revocation.
* Broad backend suites still contain legacy raw Membership Header fixtures;
  these are historical fixture debt, not authorization for the retired path.
* `change_proposal.extended_at` remains an observed historical schema-drift
  risk and has not been normalized, stamped, migrated, or modified here.

## Exit criterion

Complete both tests under a future, explicit PR-01B verification authorization,
record their disposable-database evidence, and retain the bearer-only security
model. Until then PR-01B is locally implemented and verified in its approved
scope only; it is not a production-deployment assertion.
