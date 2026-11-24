"""Rev 2 pre-launch cleanup SQL.

The revoke predicate is scoped to unrevoked non-HMAC rows on every apply,
including a later re-run. An unscoped ``WHERE revoked_at IS NULL`` is not
used: that form would revoke live HMAC links.

``cadensy_invite_cleanup_marker`` is created empty by Rev 1 and is not an ORM
model. Alembic autogenerate ignores it (see ``alembic/env.py``). The
application never reads it. ``create_all`` schemas therefore do not have it,
and cleanup tests must run on an Alembic-built database.
"""

from __future__ import annotations

MARKER_TABLE = "cadensy_invite_cleanup_marker"
MARKER_NAME = "rev2_first_apply"

# Forbidden control: UPDATE invite_link SET revoked_at = now() WHERE revoked_at IS NULL
SCOPED_REVOKE_SQL = """
UPDATE invite_link
SET revoked_at = COALESCE(revoked_at, now()),
    is_primary = false
WHERE revoked_at IS NULL
  AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)
"""

STAMP_LEGACY_SCHEME_SQL = """
UPDATE invite_link
SET token_scheme = 'legacy_random'
WHERE token_scheme IS NULL
"""

ASSERT_NO_UNREVOKED_NON_HMAC_SQL = """
SELECT count(*)
FROM invite_link
WHERE revoked_at IS NULL
  AND NOT (token_scheme = 'hmac_v1' AND key_id IS NOT NULL)
"""

CREATE_ACTIVE_INVITE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_invite_link_one_unrevoked_per_trip
ON invite_link (trip_id)
WHERE revoked_at IS NULL
"""

ASSERT_APPROVALS_EMPTY_SQL = "SELECT count(*) FROM final_plan_approval"

INSERT_MARKER_SQL = """
INSERT INTO cadensy_invite_cleanup_marker (name, completed_at)
VALUES ('rev2_first_apply', now())
"""
