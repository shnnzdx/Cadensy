"""Legacy schema initializer for compatibility verification only.

New databases must use ``alembic upgrade head``.  This function remains for
explicit, reviewed legacy-schema compatibility checks while existing databases
are stamped into Alembic history.  It intentionally calls ``create_all()``
only and never calls ``drop_all()`` or demo seed code.
"""

from __future__ import annotations

from sqlalchemy import text

from .models import Base
from .session import engine


LEGACY_BASELINE_TABLE_NAMES = frozenset(
    {
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
)


def init_schema() -> dict:
    # Keep pre-Alembic compatibility verification at the PR-05A baseline.
    # New tables, including GuestSession, must be introduced by their explicit
    # Alembic revision and cannot slip into an existing schema through this
    # legacy helper.
    Base.metadata.create_all(
        engine,
        tables=[Base.metadata.tables[name] for name in sorted(LEGACY_BASELINE_TABLE_NAMES)],
    )
    # create_all does not relax constraints on an existing installation. These
    # idempotent ALTERs let provider-backed places preserve genuinely unknown
    # planning metadata instead of storing fabricated zero/default values.
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            statements = (
                "ALTER TABLE plan ALTER COLUMN estimated_total_per_person DROP NOT NULL",
                "ALTER TABLE plan_item ALTER COLUMN duration_min DROP NOT NULL",
                "ALTER TABLE plan_item ALTER COLUMN price_per_person DROP NOT NULL",
                "ALTER TABLE plan_item ALTER COLUMN title TYPE VARCHAR(300)",
                "ALTER TABLE plan_item ALTER COLUMN place TYPE VARCHAR(500)",
                "ALTER TABLE plan_item ALTER COLUMN photo_url TYPE VARCHAR(1000)",
                "ALTER TABLE plan_item ADD COLUMN IF NOT EXISTS local_title VARCHAR(300)",
                "ALTER TABLE plan ADD COLUMN IF NOT EXISTS needs_refresh BOOLEAN NOT NULL DEFAULT FALSE",
                "ALTER TABLE place ADD COLUMN IF NOT EXISTS english_name VARCHAR(300)",
                "ALTER TABLE place ADD COLUMN IF NOT EXISTS local_name VARCHAR(300)",
                "ALTER TABLE trip ADD COLUMN IF NOT EXISTS cover_image_url VARCHAR(1500)",
                "ALTER TABLE trip ADD COLUMN IF NOT EXISTS cover_image_source VARCHAR(30)",
                "ALTER TABLE trip ADD COLUMN IF NOT EXISTS cover_attribution_name VARCHAR(200)",
                "ALTER TABLE trip ADD COLUMN IF NOT EXISTS cover_attribution_url VARCHAR(1000)",
                "ALTER TABLE trip ADD COLUMN IF NOT EXISTS cover_source_url VARCHAR(1000)",
                "ALTER TABLE trip ADD COLUMN IF NOT EXISTS cover_image_fetched_at TIMESTAMPTZ",
            )
            for statement in statements:
                connection.execute(text(statement))
    # Keep this compatibility helper's reported contract aligned with the
    # tables it is explicitly allowed to create.  Additive schema (including
    # ``guest_session``) is owned by Alembic migrations, not this legacy path.
    return {"tables": sorted(LEGACY_BASELINE_TABLE_NAMES)}


if __name__ == "__main__":
    print("schema ready:", init_schema())
