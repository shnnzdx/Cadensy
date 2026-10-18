"""add PR-06B.1B private conversation persistence schema

Revision ID: b7d3a1e9c642
Revises: 7a6c8e1d5f04
Create Date: 2026-10-16 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7d3a1e9c642"
down_revision: str | Sequence[str] | None = "7a6c8e1d5f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONVERSATION_TABLES = (
    "conversation",
    "conversation_message",
    "conversation_message_option",
    "conversation_submission",
    "conversation_submission_recovery",
)


def upgrade() -> None:
    """Add the five durable tables without changing existing domain tables."""

    op.create_table(
        "conversation",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("owner_membership_id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("context_key", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("last_qualifying_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('personal', 'plan_assistant')", name="ck_conversation_scope"),
        sa.CheckConstraint("status IN ('active', 'deleted', 'expired')", name="ck_conversation_status"),
        sa.CheckConstraint(
            "(scope = 'personal' AND context_key = 'personal') OR "
            "(scope = 'plan_assistant' AND "
            "(context_key = 'global' OR context_key ~ '^item:[0-9a-f]{32}$'))",
            name="ck_conversation_context_key",
        ),
        sa.CheckConstraint(
            "(last_qualifying_activity_at IS NULL AND retention_expires_at IS NULL) OR "
            "(last_qualifying_activity_at IS NOT NULL AND retention_expires_at IS NOT NULL "
            "AND retention_expires_at >= last_qualifying_activity_at)",
            name="ck_conversation_retention_window",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND deleted_at IS NULL AND purge_due_at IS NULL) OR "
            "(status = 'expired' AND deleted_at IS NULL AND purge_due_at IS NOT NULL) OR "
            "(status = 'deleted' AND deleted_at IS NOT NULL AND purge_due_at IS NOT NULL "
            "AND purge_due_at <= deleted_at + INTERVAL '30 days')",
            name="ck_conversation_deletion_lifecycle",
        ),
        sa.ForeignKeyConstraint(["trip_id"], ["trip.id"]),
        sa.ForeignKeyConstraint(
            ["owner_membership_id", "trip_id"],
            ["trip_membership.id", "trip_membership.trip_id"],
            name="fk_conversation_owner_membership_same_trip",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "trip_id", "owner_membership_id", name="uq_conversation_id_trip_owner"),
    )
    op.create_index(
        "ux_conversation_active_natural_identity",
        "conversation",
        ["trip_id", "owner_membership_id", "scope", "context_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_conversation_owner_created_at", "conversation", ["trip_id", "owner_membership_id", "created_at"])
    op.create_index(
        "ix_conversation_retention_expiry_active",
        "conversation",
        ["retention_expires_at"],
        postgresql_where=sa.text("status = 'active' AND retention_expires_at IS NOT NULL"),
    )
    op.create_index(
        "ix_conversation_purge_due",
        "conversation",
        ["purge_due_at"],
        postgresql_where=sa.text("purge_due_at IS NOT NULL"),
    )

    op.create_table(
        "conversation_message",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("selected_plan_item_id", sa.String(length=32), nullable=True),
        sa.Column("plan_revision", sa.BigInteger(), nullable=True),
        sa.Column("planning_facts_revision", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("sequence >= 0", name="ck_conversation_message_sequence_nonnegative"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_message_role"),
        sa.CheckConstraint("state IN ('pending', 'completed', 'failed')", name="ck_conversation_message_state"),
        sa.CheckConstraint("plan_revision IS NULL OR plan_revision >= 0", name="ck_conversation_message_plan_revision_nonnegative"),
        sa.CheckConstraint("planning_facts_revision IS NULL OR planning_facts_revision >= 0", name="ck_conversation_message_facts_revision_nonnegative"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.ForeignKeyConstraint(["selected_plan_item_id"], ["plan_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_conversation_message_sequence"),
        sa.UniqueConstraint("conversation_id", "id", name="uq_conversation_message_id_conversation"),
    )
    op.create_index("ix_conversation_message_conversation_sequence", "conversation_message", ["conversation_id", "sequence"])

    op.create_table(
        "conversation_message_option",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("target_plan_item_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("safe_patch", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("plan_revision", sa.BigInteger(), nullable=True),
        sa.Column("planning_facts_revision", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ordinal >= 0", name="ck_conversation_message_option_ordinal_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(safe_patch) = 'object'", name="ck_conversation_message_option_patch_object"),
        sa.CheckConstraint("plan_revision IS NULL OR plan_revision >= 0", name="ck_conversation_message_option_plan_revision_nonnegative"),
        sa.CheckConstraint("planning_facts_revision IS NULL OR planning_facts_revision >= 0", name="ck_conversation_message_option_facts_revision_nonnegative"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["conversation_message.id"]),
        sa.ForeignKeyConstraint(["target_plan_item_id"], ["plan_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assistant_message_id", "ordinal", name="uq_conversation_message_option_ordinal"),
    )

    op.create_table(
        "conversation_submission",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("owner_membership_id", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("canonical_request_hmac", sa.CHAR(length=64), nullable=False),
        sa.Column("fingerprint_version", sa.SmallInteger(), nullable=False),
        sa.Column("hmac_key_version", sa.String(length=32), nullable=False),
        sa.Column("user_message_id", sa.String(length=32), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default=sa.text("'running'")),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("response_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('running', 'completed', 'failed')", name="ck_conversation_submission_state"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_conversation_submission_attempt_positive"),
        sa.CheckConstraint("fence >= 1", name="ck_conversation_submission_fence_positive"),
        sa.CheckConstraint(
            "(state = 'running' AND lease_expires_at IS NOT NULL) OR "
            "(state IN ('completed', 'failed') AND lease_expires_at IS NULL)",
            name="ck_conversation_submission_lease_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "trip_id", "owner_membership_id"],
            ["conversation.id", "conversation.trip_id", "conversation.owner_membership_id"],
            name="fk_conversation_submission_conversation_owner",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "user_message_id"],
            ["conversation_message.conversation_id", "conversation_message.id"],
            name="fk_conversation_submission_user_message_same_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "assistant_message_id"],
            ["conversation_message.conversation_id", "conversation_message.id"],
            name="fk_conversation_submission_assistant_message_same_conversation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_conversation_submission_idempotency"),
        sa.UniqueConstraint("user_message_id", name="uq_conversation_submission_user_message"),
        sa.UniqueConstraint("assistant_message_id", name="uq_conversation_submission_assistant_message"),
    )
    op.create_index(
        "ux_conversation_submission_one_running",
        "conversation_submission",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_index("ix_conversation_submission_owner_created_at", "conversation_submission", ["trip_id", "owner_membership_id", "created_at"])

    op.create_table(
        "conversation_submission_recovery",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("submission_id", sa.String(length=32), nullable=False),
        sa.Column("recovery_key", sa.String(length=128), nullable=False),
        sa.Column("operation_version", sa.SmallInteger(), nullable=False),
        sa.Column("accepted_attempt_number", sa.Integer(), nullable=False),
        sa.Column("accepted_fence", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('running', 'completed', 'failed')", name="ck_conversation_submission_recovery_state"),
        sa.CheckConstraint("operation_version >= 1", name="ck_conversation_submission_recovery_operation_version_positive"),
        sa.CheckConstraint("accepted_attempt_number >= 1", name="ck_conversation_submission_recovery_attempt_positive"),
        sa.CheckConstraint("accepted_fence >= 1", name="ck_conversation_submission_recovery_fence_positive"),
        sa.ForeignKeyConstraint(["submission_id"], ["conversation_submission.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "recovery_key", name="uq_conversation_submission_recovery_key"),
    )
    op.create_index("ix_conversation_submission_recovery_created_at", "conversation_submission_recovery", ["submission_id", "created_at"])


def downgrade() -> None:
    """Refuse to discard persisted private conversations during rollback."""

    bind = op.get_bind()
    populated_tables = [
        table_name
        for table_name in _CONVERSATION_TABLES
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})")).scalar_one()
    ]
    if populated_tables:
        raise RuntimeError(
            "PR-06B.1B downgrade refused: conversation data would be lost; "
            "use an approved export/retention procedure before rollback"
        )

    op.drop_table("conversation_submission_recovery")
    op.drop_table("conversation_submission")
    op.drop_table("conversation_message_option")
    op.drop_table("conversation_message")
    op.drop_table("conversation")
