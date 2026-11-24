"""Option C invite cleanup and one-active-invite index.

Revision ID: c0b2f9e5a6d3
Revises: c0a1e8d4b5f2
Create Date: 2026-11-24 00:10:00.000000

First apply revokes unrevoked non-HMAC invites in place, asserts
final_plan_approval is empty, writes the marker, and creates the unique
index. A re-run (marker already present, including Rev 2 down then up)
repeats only the scoped revoke and the index. It does not touch HMAC rows
or approvals, and it does not delete invite or activation rows.

Executing this against production is a destructive data operation and is not
authorized by shipping the revision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.invite_cleanup import (
    ASSERT_APPROVALS_EMPTY_SQL,
    ASSERT_NO_UNREVOKED_NON_HMAC_SQL,
    CREATE_ACTIVE_INVITE_INDEX_SQL,
    INSERT_MARKER_SQL,
    MARKER_NAME,
    SCOPED_REVOKE_SQL,
    STAMP_LEGACY_SCHEME_SQL,
)

revision: str = "c0b2f9e5a6d3"
down_revision: str | Sequence[str] | None = "c0a1e8d4b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("LOCK TABLE trip IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE invite_link IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE plan IN ACCESS EXCLUSIVE MODE")
    bind = op.get_bind()
    marker = bind.execute(
        sa.text(
            "SELECT 1 FROM cadensy_invite_cleanup_marker WHERE name = :name"
        ),
        {"name": MARKER_NAME},
    ).first()
    first_apply = marker is None
    bind.execute(sa.text(STAMP_LEGACY_SCHEME_SQL))
    bind.execute(sa.text(SCOPED_REVOKE_SQL))
    remaining = bind.execute(sa.text(ASSERT_NO_UNREVOKED_NON_HMAC_SQL)).scalar_one()
    if remaining:
        raise RuntimeError(
            "Rev 2 cleanup left unrevoked non-HMAC invite rows; aborting"
        )
    op.execute(CREATE_ACTIVE_INVITE_INDEX_SQL)
    if first_apply:
        approvals = bind.execute(sa.text(ASSERT_APPROVALS_EMPTY_SQL)).scalar_one()
        if approvals:
            raise RuntimeError(
                "final_plan_approval is not empty; refusing Rev 2 first apply"
            )
        bind.execute(sa.text(INSERT_MARKER_SQL))


def downgrade() -> None:
    # The marker row and table survive. A later upgrade must skip step 7.
    op.execute("DROP INDEX IF EXISTS ux_invite_link_one_unrevoked_per_trip")
