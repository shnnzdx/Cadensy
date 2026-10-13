"""add PR-05B.1 dormant schema foundation

Revision ID: 6f77e775d201
Revises: 54e8dd1ca624
Create Date: 2026-10-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "6f77e775d201"
down_revision: str | Sequence[str] | None = "54e8dd1ca624"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dormant revision and completed-outcome ledger storage."""

    # PostgreSQL applies the constant default to existing rows; it remains the
    # server default for later Plan inserts. No existing Plan is rewritten by
    # application code and no trip-level uniqueness is introduced here.
    op.add_column(
        "plan",
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint("ck_plan_revision_nonnegative", "plan", "revision >= 0")

    op.create_table(
        "mutation_idempotency",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("trip_id", sa.String(length=32), nullable=False),
        sa.Column("actor_membership_id", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("operation_code", sa.String(length=80), nullable=False),
        sa.Column("resource_kind", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint_version", sa.SmallInteger(), nullable=False),
        sa.Column("request_hmac", sa.CHAR(length=64), nullable=False),
        sa.Column("hmac_key_version", sa.String(length=32), nullable=False),
        sa.Column("outcome_contract_version", sa.SmallInteger(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("outcome_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resulting_plan_revision", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_membership_id"], ["trip_membership.id"]),
        sa.ForeignKeyConstraint(["trip_id"], ["trip.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trip_id",
            "actor_membership_id",
            "idempotency_key",
            name="uq_mutation_idempotency_trip_actor_key",
        ),
    )
    op.create_index(
        "ix_mutation_idempotency_expires_at",
        "mutation_idempotency",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the objects introduced by this additive revision."""

    op.drop_index("ix_mutation_idempotency_expires_at", table_name="mutation_idempotency")
    op.drop_table("mutation_idempotency")
    op.drop_constraint("ck_plan_revision_nonnegative", "plan", type_="check")
    op.drop_column("plan", "revision")
