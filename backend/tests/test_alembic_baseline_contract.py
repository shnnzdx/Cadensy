"""Static cutover contract for the immutable Alembic baseline.

Database-level acceptance is deliberately run through the Alembic CLI against
explicit disposable PostgreSQL databases and is recorded in the PR-05A
runbook.  These tests keep that cutover revision rooted and complete without
opening a database connection.
"""

from __future__ import annotations

from pathlib import Path

from app.db.models import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "f9ff23b7b84d_baseline_existing_schema.py"
)
BASELINE_REVISION = "f9ff23b7b84d"
BASELINE_TABLES = {
    "auth_session",
    "change_proposal",
    "decision_round",
    "invite_link",
    "member_constraint",
    "member_constraint_private",
    "place",
    "plan",
    "plan_change",
    "plan_item",
    "plan_item_comment",
    "preference",
    "proposal_decision",
    "trip",
    "trip_membership",
    "update_notice",
    "user_account",
    "vote",
}
BASELINE_PARTIAL_INDEXES = {
    "one_membership_per_user_per_trip",
    "one_open_round_per_item",
    "one_pending_proposal_per_item",
}


def test_baseline_is_a_root_revision_for_the_current_schema_cutover() -> None:
    source = BASELINE_PATH.read_text(encoding="utf-8")

    assert "revision: str = 'f9ff23b7b84d'" in source
    assert "down_revision: str | Sequence[str] | None = None" in source
    assert BASELINE_REVISION in source
    # The root revision is intentionally frozen. Later additive migrations may
    # add tables, but they must not mutate this cutover snapshot.
    assert BASELINE_TABLES <= set(Base.metadata.tables)


def test_baseline_contains_every_current_table_and_partial_index() -> None:
    source = BASELINE_PATH.read_text(encoding="utf-8")

    for table_name in BASELINE_TABLES:
        assert f"op.create_table('{table_name}'" in source
    for index_name in BASELINE_PARTIAL_INDEXES:
        assert f"op.create_index('{index_name}'" in source
