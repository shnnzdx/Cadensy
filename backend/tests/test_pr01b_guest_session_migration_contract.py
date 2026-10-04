"""Static contract for the PR-01B additive GuestSession migration."""

from __future__ import annotations

from pathlib import Path

from app.db.models import GuestSession


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "54e8dd1ca624_add_bounded_guest_sessions.py"
)


def test_guest_session_model_has_bounded_credential_constraints() -> None:
    assert GuestSession.__tablename__ == "guest_session"
    assert GuestSession.__table__.c.token_hash.unique is True
    assert GuestSession.__table__.c.expires_at.nullable is False
    assert GuestSession.__table__.c.revoked_at.nullable is True
    assert {foreign_key.column.table.name for foreign_key in GuestSession.__table__.foreign_keys} == {
        "trip_membership",
        "trip",
    }
    indexes = {index.name: index for index in GuestSession.__table__.indexes}
    assert set(indexes) == {
        "ix_guest_session_trip_id",
        "one_active_guest_session_per_membership",
    }
    active_index = indexes["one_active_guest_session_per_membership"]
    assert active_index.unique is True
    assert str(active_index.dialect_options["postgresql"]["where"]) == "revoked_at IS NULL"


def test_guest_session_migration_is_additive_after_the_pr05a_root() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "revision: str = '54e8dd1ca624'" in source
    assert "down_revision: str | Sequence[str] | None = 'f9ff23b7b84d'" in source
    assert "op.create_table('guest_session'" in source
    assert "op.drop_table('guest_session')" in source
    assert "one_active_guest_session_per_membership" in source
    assert "postgresql_where='revoked_at IS NULL'" in source
