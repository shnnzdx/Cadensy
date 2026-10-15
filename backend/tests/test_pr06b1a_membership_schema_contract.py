"""Static PR-06B.1A contract for additive membership lifecycle schema."""

from __future__ import annotations

from pathlib import Path

from app.db.models import DecisionRound, TripMembership


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "9db6f1a1041b_pr06b1a_membership_lifecycle_schema.py"
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

    constraints = {constraint.name: str(constraint.sqltext) for constraint in TripMembership.__table__.constraints if constraint.name}
    assert "ck_trip_membership_quarantine_audit_complete" in constraints
    assert "ck_trip_membership_quarantine_reason" in constraints


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
    constraints = {constraint.name: str(constraint.sqltext) for constraint in DecisionRound.__table__.constraints if constraint.name}
    assert "ck_decision_round_electorate_count_nonnegative" in constraints


def test_membership_lifecycle_migration_uses_concurrent_safe_index_cutover_and_guarded_downgrade() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision: str = "9db6f1a1041b"' in source
    assert 'down_revision: str | Sequence[str] | None = "c2a4b6d8e0f1"' in source
    assert "autocommit_block" in source
    assert "CREATE UNIQUE INDEX CONCURRENTLY" in source
    assert "DROP INDEX CONCURRENTLY" in source
    assert "trg_pr06b1a_decision_round_electorate_count_immutable" in source
    assert "decision_round.electorate_count is immutable once captured" in source
    assert "duplicate active account memberships" in source
    assert "account rejoin history cannot be collapsed" in source
    assert "immutable electorate history would be lost" in source
    assert "quarantine history would be lost" in source
