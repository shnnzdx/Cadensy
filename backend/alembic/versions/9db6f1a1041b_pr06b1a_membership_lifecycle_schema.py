"""add PR-06B.1A atomic membership lifecycle schema checkpoint

Revision ID: 9db6f1a1041b
Revises: c2a4b6d8e0f1
Create Date: 2026-10-15 13:25:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9db6f1a1041b"
down_revision: str | Sequence[str] | None = "c2a4b6d8e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SUPPORTED_MEMBERSHIP_STATUSES = (
    "invited",
    "joined",
    "preferences_submitted",
    "removed",
)


def _raise_if_preflight_fails() -> None:
    bind = op.get_bind()
    unsupported_statuses = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM trip_membership
            WHERE status IS NULL
               OR btrim(status) = ''
               OR status NOT IN :supported_statuses
            """
        ).bindparams(sa.bindparam("supported_statuses", expanding=True)),
        {"supported_statuses": _SUPPORTED_MEMBERSHIP_STATUSES},
    ).scalar_one()
    if unsupported_statuses:
        raise RuntimeError(
            "PR-06B.1A upgrade refused: trip_membership contains unsupported status rows"
        )


def upgrade() -> None:
    """Atomically add additive lifecycle schema before index cutover."""

    _raise_if_preflight_fails()

    op.add_column(
        "trip_membership",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trip_membership",
        sa.Column("quarantine_reason", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "trip_membership",
        sa.Column("quarantined_by_membership_id", sa.String(length=32), nullable=True),
    )
    op.create_unique_constraint(
        "uq_trip_membership_id_trip",
        "trip_membership",
        ["id", "trip_id"],
    )
    op.create_foreign_key(
        "fk_trip_membership_quarantined_by_same_trip",
        "trip_membership",
        "trip_membership",
        ["quarantined_by_membership_id", "trip_id"],
        ["id", "trip_id"],
    )
    op.create_check_constraint(
        "ck_trip_membership_quarantine_audit_complete",
        "trip_membership",
        "(quarantined_at IS NULL AND quarantine_reason IS NULL "
        "AND quarantined_by_membership_id IS NULL) OR "
        "(quarantined_at IS NOT NULL AND quarantine_reason IS NOT NULL "
        "AND quarantined_by_membership_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_trip_membership_quarantine_reason",
        "trip_membership",
        "quarantine_reason IS NULL OR quarantine_reason IN "
        "('security_incident', 'abuse', 'unauthorized_access', 'organizer_emergency')",
    )
    op.create_check_constraint(
        "ck_trip_membership_status_supported",
        "trip_membership",
        "status IN ('invited', 'joined', 'preferences_submitted', 'removed')",
    )

    # Historical closed rounds cannot receive a reconstructed count. New
    # writers populate this field atomically at round creation in PR-06B.1A.3.
    op.add_column(
        "decision_round",
        sa.Column("electorate_count", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_decision_round_electorate_count_nonnegative",
        "decision_round",
        "electorate_count IS NULL OR electorate_count >= 0",
    )
    # A new count can be recorded exactly once for an otherwise historical
    # NULL row.  It can never be altered or cleared after capture.
    op.execute(
        """
        CREATE FUNCTION pr06b1a_preserve_decision_round_electorate_count()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.electorate_count IS NOT NULL
               AND NEW.electorate_count IS DISTINCT FROM OLD.electorate_count THEN
                RAISE EXCEPTION
                    'decision_round.electorate_count is immutable once captured'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_pr06b1a_decision_round_electorate_count_immutable
        BEFORE UPDATE OF electorate_count ON decision_round
        FOR EACH ROW
        EXECUTE FUNCTION pr06b1a_preserve_decision_round_electorate_count()
        """
    )

def downgrade() -> None:
    """Refuse destructive rollback once new lifecycle history exists."""

    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM decision_round WHERE electorate_count IS NOT NULL)")
    ).scalar_one():
        raise RuntimeError(
            "PR-06B.1A downgrade refused: immutable electorate history would be lost"
        )

    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM trip_membership WHERE quarantined_at IS NOT NULL)")
    ).scalar_one():
        raise RuntimeError(
            "PR-06B.1A downgrade refused: quarantine history would be lost"
        )

    op.execute(
        "DROP TRIGGER trg_pr06b1a_decision_round_electorate_count_immutable "
        "ON decision_round"
    )
    op.execute("DROP FUNCTION pr06b1a_preserve_decision_round_electorate_count()")
    op.drop_constraint(
        "ck_decision_round_electorate_count_nonnegative",
        "decision_round",
        type_="check",
    )
    op.drop_column("decision_round", "electorate_count")
    op.drop_constraint(
        "ck_trip_membership_status_supported",
        "trip_membership",
        type_="check",
    )
    op.drop_constraint(
        "ck_trip_membership_quarantine_reason",
        "trip_membership",
        type_="check",
    )
    op.drop_constraint(
        "ck_trip_membership_quarantine_audit_complete",
        "trip_membership",
        type_="check",
    )
    op.drop_constraint(
        "fk_trip_membership_quarantined_by_same_trip",
        "trip_membership",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_trip_membership_id_trip",
        "trip_membership",
        type_="unique",
    )
    op.drop_column("trip_membership", "quarantined_by_membership_id")
    op.drop_column("trip_membership", "quarantine_reason")
    op.drop_column("trip_membership", "quarantined_at")
