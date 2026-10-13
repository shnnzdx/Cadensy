"""add PR-05B Phase 1 Trip revision and ledger scope schema

Revision ID: c2a4b6d8e0f1
Revises: 6f77e775d201
Create Date: 2026-10-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2a4b6d8e0f1"
down_revision: str | Sequence[str] | None = "6f77e775d201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight_historical_plan_ledger() -> None:
    """Refuse incompatible B.1 history without exposing sensitive row values."""
    invalid_row_count = op.get_bind().execute(
        sa.text(
            """
            select count(*)
            from mutation_idempotency
            where resulting_plan_revision is null
               or resulting_plan_revision < 0
            """
        )
    ).scalar_one()
    if invalid_row_count:
        raise RuntimeError(
            "Cannot apply PR-05B Phase 1: "
            f"{invalid_row_count} historical Plan ledger row(s) require separately approved "
            "remediation because resulting_plan_revision is null or negative."
        )


def upgrade() -> None:
    """Add scope-aware result representation without altering historical outcomes."""
    # Existing B.1 rows will become plan scope once the column/default is added.
    # Validate their required Plan result before adding the combined result check.
    _preflight_historical_plan_ledger()

    op.add_column(
        "trip",
        sa.Column(
            "planning_facts_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_trip_planning_facts_revision_nonnegative",
        "trip",
        "planning_facts_revision >= 0",
    )

    op.add_column(
        "mutation_idempotency",
        sa.Column(
            "aggregate_scope",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'plan'"),
        ),
    )
    op.add_column(
        "mutation_idempotency",
        sa.Column("resulting_trip_planning_facts_revision", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_mutation_idempotency_aggregate_scope",
        "mutation_idempotency",
        "aggregate_scope IN ('plan', 'trip_planning_facts')",
    )
    op.create_check_constraint(
        "ck_mutation_idempotency_plan_result_nonnegative",
        "mutation_idempotency",
        "resulting_plan_revision IS NULL OR resulting_plan_revision >= 0",
    )
    op.create_check_constraint(
        "ck_mutation_idempotency_trip_result_nonnegative",
        "mutation_idempotency",
        "resulting_trip_planning_facts_revision IS NULL "
        "OR resulting_trip_planning_facts_revision >= 0",
    )
    op.create_check_constraint(
        "ck_mutation_idempotency_result_scope",
        "mutation_idempotency",
        "(aggregate_scope = 'plan' "
        "AND resulting_plan_revision IS NOT NULL "
        "AND resulting_trip_planning_facts_revision IS NULL) "
        "OR (aggregate_scope = 'trip_planning_facts' "
        "AND resulting_plan_revision IS NULL "
        "AND resulting_trip_planning_facts_revision IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove Phase 1 columns only while doing so preserves durable history."""
    trip_scope_row_count = op.get_bind().execute(
        sa.text(
            """
            select count(*)
            from mutation_idempotency
            where aggregate_scope = 'trip_planning_facts'
            """
        )
    ).scalar_one()
    if trip_scope_row_count:
        raise RuntimeError(
            "Cannot downgrade PR-05B Phase 1: "
            f"{trip_scope_row_count} Trip-scope durable outcome(s) would lose their only "
            "result revision representation."
        )

    op.drop_constraint(
        "ck_mutation_idempotency_result_scope", "mutation_idempotency", type_="check"
    )
    op.drop_constraint(
        "ck_mutation_idempotency_trip_result_nonnegative",
        "mutation_idempotency",
        type_="check",
    )
    op.drop_constraint(
        "ck_mutation_idempotency_plan_result_nonnegative",
        "mutation_idempotency",
        type_="check",
    )
    op.drop_constraint(
        "ck_mutation_idempotency_aggregate_scope", "mutation_idempotency", type_="check"
    )
    op.drop_column("mutation_idempotency", "resulting_trip_planning_facts_revision")
    op.drop_column("mutation_idempotency", "aggregate_scope")
    op.drop_constraint("ck_trip_planning_facts_revision_nonnegative", "trip", type_="check")
    op.drop_column("trip", "planning_facts_revision")
