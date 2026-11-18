"""activation events and preference first-submitted time

Revision ID: c8e1d4a90b27
Revises: b7d3a1e9c642
Create Date: 2026-11-17 21:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c8e1d4a90b27"
down_revision: str | Sequence[str] | None = "b7d3a1e9c642"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add activation facts and a stable first-preference timestamp.

    Existing invite rows keep a null ``expires_at``. The new unique pair on
    ``invite_link`` only supports a same-trip foreign key; it does not revoke
    links that are already active.
    """

    op.create_unique_constraint(
        "uq_invite_link_id_trip", "invite_link", ["id", "trip_id"]
    )
    op.add_column(
        "preference",
        sa.Column("first_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "activation_event",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("event_name", sa.String(length=40), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("invite_id", sa.String(length=32), nullable=True),
        sa.Column("membership_id", sa.String(length=32), nullable=True),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "event_name IN ("
            "'trip_created', 'invite_created', 'invite_link_copied', "
            "'invite_shared_wechat_template', 'invite_sent', 'invite_opened', "
            "'member_joined', 'preferences_submitted', 'preferences_updated', "
            "'first_plan_created', 'first_decision_completed', 'notice_auto_applied'"
            ")",
            name="ck_activation_event_name",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('account', 'guest', 'system')",
            name="ck_activation_event_actor_kind",
        ),
        sa.CheckConstraint(
            "char_length(dedup_key) BETWEEN 1 AND 200",
            name="ck_activation_event_dedup_key_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_activation_event_metadata_object",
        ),
        sa.CheckConstraint(
            "NOT jsonb_exists_any(metadata, ARRAY["
            "'token', 'token_hash', 'invite_token', 'email', 'name', "
            "'display_name', 'ip', 'user_agent', 'original_text', 'url', "
            "'ideal_budget', 'maximum_budget', 'budget', 'password', "
            "'authorization', 'preference', 'params', 'constraint'"
            "]::text[])",
            name="ck_activation_event_metadata_privacy",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trip.id"],
            name="fk_activation_event_trip",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invite_id", "trip_id"],
            ["invite_link.id", "invite_link.trip_id"],
            name="fk_activation_event_invite_same_trip",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "trip_id"],
            ["trip_membership.id", "trip_membership.trip_id"],
            name="fk_activation_event_membership_same_trip",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_activation_event_dedup_key"),
    )
    op.create_index(
        "ix_activation_event_trip_name",
        "activation_event",
        ["trip_id", "event_name"],
    )
    op.create_index("ix_activation_event_invite", "activation_event", ["invite_id"])
    op.create_index(
        "ix_activation_event_membership", "activation_event", ["membership_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_activation_event_membership", table_name="activation_event")
    op.drop_index("ix_activation_event_invite", table_name="activation_event")
    op.drop_index("ix_activation_event_trip_name", table_name="activation_event")
    op.drop_table("activation_event")
    op.drop_column("preference", "first_submitted_at")
    op.drop_constraint("uq_invite_link_id_trip", "invite_link", type_="unique")
