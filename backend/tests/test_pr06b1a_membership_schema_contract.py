"""Static PR-06B.1A contract for additive membership lifecycle schema."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint

from app.db.models import DecisionRound, TripMembership


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "9db6f1a1041b_pr06b1a_membership_lifecycle_schema.py"
)
INDEX_CUTOVER_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "7a6c8e1d5f04_pr06b1a_active_account_index_cutover.py"
)


def test_membership_quarantine_fields_are_auditable_and_do_not_change_incarnation() -> None:
    columns = TripMembership.__table__.c
    assert columns.quarantined_at.nullable is True
    assert columns.quarantine_reason.nullable is True
    assert columns.quarantined_by_membership_id.nullable is True
    assert {
        foreign_key.column.table.name
        for foreign_key in columns.quarantined_by_membership_id.foreign_keys
    } == {"trip_membership"}

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in TripMembership.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "ck_trip_membership_quarantine_audit_complete" in constraints
    assert "ck_trip_membership_quarantine_reason" in constraints
    assert "ck_trip_membership_status_supported" in constraints
    assert {
        constraint.name
        for constraint in TripMembership.__table__.constraints
        if constraint.name
    } >= {
        "uq_trip_membership_id_trip",
        "fk_trip_membership_quarantined_by_same_trip",
    }


def test_active_account_index_keeps_quarantine_inside_the_same_incarnation() -> None:
    indexes = {index.name: index for index in TripMembership.__table__.indexes}
    assert set(indexes) == {"ux_trip_membership_active_account"}
    active_index = indexes["ux_trip_membership_active_account"]
    assert active_index.unique is True
    assert (
        str(active_index.dialect_options["postgresql"]["where"])
        == "user_id IS NOT NULL AND status <> 'removed'"
    )


def test_round_electorate_count_is_nullable_only_for_unreconstructable_history() -> None:
    column = DecisionRound.__table__.c.electorate_count
    assert column.nullable is True
    assert str(column.type) == "BIGINT"
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in DecisionRound.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    assert "ck_decision_round_electorate_count_nonnegative" in constraints


def test_membership_lifecycle_migrations_use_checkpointed_cutover_and_guarded_downgrade() -> None:
    schema_source = MIGRATION_PATH.read_text(encoding="utf-8")
    index_source = INDEX_CUTOVER_PATH.read_text(encoding="utf-8")

    assert 'revision: str = "9db6f1a1041b"' in schema_source
    assert 'down_revision: str | Sequence[str] | None = "c2a4b6d8e0f1"' in schema_source
    assert 'revision: str = "7a6c8e1d5f04"' in index_source
    assert 'down_revision: str | Sequence[str] | None = "9db6f1a1041b"' in index_source
    assert "autocommit_block" not in schema_source
    assert "autocommit_block" in index_source
    assert "CREATE UNIQUE INDEX CONCURRENTLY" in index_source
    assert "DROP INDEX CONCURRENTLY" in index_source
    assert "indisvalid" in index_source
    assert "indisready" in index_source
    assert "pr06b1a_fail_after_index_validation" in index_source
    assert "trg_pr06b1a_decision_round_electorate_count_immutable" in schema_source
    assert "decision_round.electorate_count is immutable once captured" in schema_source
    assert "account rejoin history cannot be collapsed" in index_source
    assert "immutable electorate history would be lost" in schema_source
    assert "quarantine history would be lost" in schema_source
