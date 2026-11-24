# Option C invite cleanup runbook

This runbook describes Rev 2 (`c0b2f9e5a6d3`). Shipping the revision does not
authorize executing it. Production migration, including this cleanup, needs
explicit Owner approval at execution time.

## What Rev 2 does

One Alembic transaction. The first statements are:

```sql
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '60s';
```

Those values are a starting point. They have not been dry-run against
production. Gate M's statement bound is 1800 seconds.

Then lock, in this order, before any data change:

1. `trip`
2. `invite_link`
3. `plan`

Each lock is `ACCESS EXCLUSIVE`.

The marker table `cadensy_invite_cleanup_marker` is created empty by Rev 1
and is not an ORM model. `alembic/env.py` excludes it from autogenerate.
`create_all` does not create it. The application never reads it.

Before changing rows, read the marker:

- If `name = 'rev2_first_apply'` is absent, this is the first apply.
- If that row is present, this is a re-run. A re-run includes Rev 2
  downgrade followed by upgrade, because downgrade drops only
  `ux_invite_link_one_unrevoked_per_trip` and leaves the marker in place.

Both applies:

1. Stamp null `token_scheme` to `legacy_random`.
2. Revoke unrevoked non-HMAC rows in place. The predicate is
   `revoked_at IS NULL AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)`.
   An unscoped `WHERE revoked_at IS NULL` is forbidden.
3. Assert zero unrevoked non-HMAC rows remain.
4. `CREATE UNIQUE INDEX IF NOT EXISTS ux_invite_link_one_unrevoked_per_trip`.

First apply only:

5. Assert `final_plan_approval` is empty. If it is not, abort the whole
   transaction. Do not delete approval rows. A failed first apply leaves no
   marker row.
6. Insert the marker.

Invite rows are never deleted. Activation events stay attached.

## Re-run after go-live

Any post-go-live re-run requires explicit Owner authorization at execution
time. A re-run preserves live HMAC links and existing approvals and revokes
only unrevoked non-HMAC rows. It does not repeat the empty-approval assertion.

## Downgrade

Rev 2 downgrade drops only the unique active-invite index. It does not
un-revoke rows and does not delete the marker.

Rev 1 downgrade refuses when any `hmac_v1` invite row exists or any
`final_plan_approval` row exists. An empty database downgrade drops the
marker table.
