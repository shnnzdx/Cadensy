"""Option C invite schema, final-plan approval, and itinerary triggers.

Revision ID: c0a1e8d4b5f2
Revises: c8e1d4a90b27
Create Date: 2026-11-24 00:00:00.000000

Rev 1 is safe to commit while the previous application is still running.
It adds nullable anchors, the approval table, the empty cleanup marker, and
the itinerary triggers. It does not revoke invites and does not create the
one-active-invite index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.itinerary_revision_trigger import (
    DROP_FUNCTIONS,
    DROP_PLAN_CHANGE_TRIGGER,
    DROP_PLAN_ITEM_TRIGGERS,
    INSTALL_STATEMENTS,
)

revision: str = "c0a1e8d4b5f2"
down_revision: str | Sequence[str] | None = "c8e1d4a90b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timeouts_and_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute("LOCK TABLE trip IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE invite_link IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE plan IN ACCESS EXCLUSIVE MODE")


def upgrade() -> None:
    _timeouts_and_locks()
    op.add_column(
        "trip",
        sa.Column("first_invite_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trip",
        sa.Column("invite_window_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trip",
        sa.Column("final_plan_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("invite_link", sa.Column("token_scheme", sa.String(length=20), nullable=True))
    op.add_column("invite_link", sa.Column("key_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "invite_link",
        sa.Column("creation_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "ck_invite_link_token_scheme",
        "invite_link",
        "token_scheme IS NULL OR token_scheme IN ('hmac_v1', 'legacy_random')",
    )
    op.create_check_constraint(
        "ck_invite_link_key_id_range",
        "invite_link",
        "key_id IS NULL OR (key_id >= 1 AND key_id <= 4294967295)",
    )
    op.create_check_constraint(
        "ck_invite_link_hmac_key_id",
        "invite_link",
        "token_scheme IS DISTINCT FROM 'hmac_v1' OR key_id IS NOT NULL",
    )
    op.create_index(
        "ux_invite_link_replace_idempotency",
        "invite_link",
        ["trip_id", "creation_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("creation_idempotency_key IS NOT NULL"),
    )
    op.add_column(
        "plan",
        sa.Column(
            "itinerary_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_plan_itinerary_revision_nonnegative",
        "plan",
        "itinerary_revision >= 0",
    )
    op.create_table(
        "final_plan_approval",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("membership_id", sa.String(length=32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bound_itinerary_revision", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trip.id"], name="fk_final_plan_approval_trip", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["membership_id", "trip_id"],
            ["trip_membership.id", "trip_membership.trip_id"],
            name="fk_final_plan_approval_membership_same_trip",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trip_id", "membership_id", name="uq_final_plan_approval_trip_membership"
        ),
    )
    op.create_table(
        "cadensy_invite_cleanup_marker",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    for statement in INSTALL_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    hmac_rows = bind.execute(
        sa.text("SELECT count(*) FROM invite_link WHERE token_scheme = 'hmac_v1'")
    ).scalar_one()
    approval_rows = bind.execute(
        sa.text("SELECT count(*) FROM final_plan_approval")
    ).scalar_one()
    if hmac_rows or approval_rows:
        reasons: list[str] = []
        if hmac_rows:
            reasons.append("hmac_v1 invite rows exist")
        if approval_rows:
            reasons.append("final_plan_approval rows exist")
        raise RuntimeError("Refusing Rev 1 downgrade: " + "; ".join(reasons))

    op.execute(DROP_PLAN_CHANGE_TRIGGER)
    for statement in DROP_PLAN_ITEM_TRIGGERS:
        op.execute(statement)
    for statement in DROP_FUNCTIONS:
        op.execute(statement)
    op.drop_table("cadensy_invite_cleanup_marker")
    op.drop_table("final_plan_approval")
    op.drop_constraint("ck_plan_itinerary_revision_nonnegative", "plan", type_="check")
    op.drop_column("plan", "itinerary_revision")
    op.drop_index("ux_invite_link_replace_idempotency", table_name="invite_link")
    op.drop_constraint("ck_invite_link_hmac_key_id", "invite_link", type_="check")
    op.drop_constraint("ck_invite_link_key_id_range", "invite_link", type_="check")
    op.drop_constraint("ck_invite_link_token_scheme", "invite_link", type_="check")
    op.drop_column("invite_link", "creation_idempotency_key")
    op.drop_column("invite_link", "key_id")
    op.drop_column("invite_link", "token_scheme")
    op.drop_column("trip", "final_plan_approved_at")
    op.drop_column("trip", "invite_window_ends_at")
    op.drop_column("trip", "first_invite_created_at")
