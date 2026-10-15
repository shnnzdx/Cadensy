"""checkpointed PR-06B.1A active-account index cutover

Revision ID: 7a6c8e1d5f04
Revises: 9db6f1a1041b
Create Date: 2026-10-15 17:10:00.000000
"""

from __future__ import annotations

from collections import namedtuple
from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "7a6c8e1d5f04"
down_revision: str | Sequence[str] | None = "9db6f1a1041b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTIVE_ACCOUNT_INDEX = "ux_trip_membership_active_account"
_LEGACY_ACCOUNT_INDEX = "one_membership_per_user_per_trip"
_ACTIVE_PREDICATE = "((user_id IS NOT NULL) AND ((status)::text <> 'removed'::text))"
_LEGACY_PREDICATE = "(user_id IS NOT NULL)"
_INDEX_COLUMNS = ("trip_id", "user_id")


_IndexState = namedtuple(
    "_IndexState", "name is_unique is_valid is_ready predicate columns"
)


def _normalized_sql(value: str) -> str:
    return "".join(value.split())


def _read_index_state(index_name: str) -> _IndexState | None:
    """Read the exact PostgreSQL catalog state; do not infer from its name."""

    row = op.get_bind().execute(
        sa.text(
            """
            SELECT
                index_class.relname AS name,
                index_data.indisunique AS is_unique,
                index_data.indisvalid AS is_valid,
                index_data.indisready AS is_ready,
                coalesce(pg_get_expr(index_data.indpred, index_data.indrelid), '') AS predicate,
                array_agg(attribute.attname ORDER BY index_key.ordinality)
                    FILTER (WHERE index_key.attnum > 0) AS columns
            FROM pg_index AS index_data
            JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
            JOIN pg_namespace AS index_namespace ON index_namespace.oid = index_class.relnamespace
            LEFT JOIN unnest(index_data.indkey) WITH ORDINALITY
                AS index_key(attnum, ordinality) ON TRUE
            LEFT JOIN pg_attribute AS attribute
                ON attribute.attrelid = index_data.indrelid
               AND attribute.attnum = index_key.attnum
            WHERE index_namespace.nspname = current_schema()
              AND index_class.relname = :index_name
              AND index_data.indrelid = 'trip_membership'::regclass
            GROUP BY
                index_class.relname,
                index_data.indisunique,
                index_data.indisvalid,
                index_data.indisready,
                index_data.indpred,
                index_data.indrelid
            """
        ),
        {"index_name": index_name},
    ).mappings().one_or_none()
    if row is None:
        return None
    return _IndexState(
        name=row["name"],
        is_unique=row["is_unique"],
        is_valid=row["is_valid"],
        is_ready=row["is_ready"],
        predicate=row["predicate"],
        columns=tuple(row["columns"] or ()),
    )


def _require_index_shape(state: _IndexState, *, predicate: str) -> None:
    if (
        not state.is_unique
        or state.columns != _INDEX_COLUMNS
        or _normalized_sql(state.predicate) != _normalized_sql(predicate)
    ):
        raise RuntimeError(
            f"PR-06B.1A index recovery refused: {state.name} has an unexpected definition"
        )


def _require_valid_ready(state: _IndexState, *, role: str) -> None:
    if not state.is_valid or not state.is_ready:
        raise RuntimeError(
            f"PR-06B.1A index recovery refused: {role} index {state.name} "
            "is not valid and ready"
        )


def _create_active_index() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            f"{_ACTIVE_ACCOUNT_INDEX} ON trip_membership (trip_id, user_id) "
            "WHERE user_id IS NOT NULL AND status <> 'removed'"
        )


def _create_legacy_index() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            f"{_LEGACY_ACCOUNT_INDEX} ON trip_membership (trip_id, user_id) "
            "WHERE user_id IS NOT NULL"
        )


def _drop_index_concurrently(index_name: str) -> None:
    # index_name is a module constant, never external input.
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY {index_name}")


def _raise_after_validated_target_for_test() -> None:
    """Deliberate non-production failure injection for recovery validation."""

    options = context.get_x_argument(as_dictionary=True)
    if options.get("pr06b1a_fail_after_index_validation") == "1":
        raise RuntimeError(
            "PR-06B.1A injected failure after validated target index; rerun without -x"
        )


def _assert_no_rejoin_history() -> None:
    multiple_incarnations = op.get_bind().execute(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT trip_id, user_id
                FROM trip_membership
                WHERE user_id IS NOT NULL
                GROUP BY trip_id, user_id
                HAVING count(*) > 1
            ) account_rejoin_history
            """
        )
    ).scalar_one()
    if multiple_incarnations:
        raise RuntimeError(
            "PR-06B.1A downgrade refused: account rejoin history cannot be collapsed"
        )


def upgrade() -> None:
    """Replace legacy uniqueness only after a catalog-verified target exists.

    9db6f1a1041b is an atomic, recorded checkpoint.  This revision can be
    rerun after interruption: a valid target plus legacy index resumes at the
    legacy drop; an invalid target is removed only while the valid legacy
    index still protects data, then recreated and revalidated.
    """

    legacy = _read_index_state(_LEGACY_ACCOUNT_INDEX)
    active = _read_index_state(_ACTIVE_ACCOUNT_INDEX)

    if legacy is not None:
        _require_index_shape(legacy, predicate=_LEGACY_PREDICATE)
        _require_valid_ready(legacy, role="legacy")

    if active is not None:
        _require_index_shape(active, predicate=_ACTIVE_PREDICATE)
        if not active.is_valid or not active.is_ready:
            if legacy is None:
                raise RuntimeError(
                    "PR-06B.1A index recovery refused: invalid target has no valid legacy guard"
                )
            _drop_index_concurrently(_ACTIVE_ACCOUNT_INDEX)
            active = None

    if active is None:
        if legacy is None:
            raise RuntimeError(
                "PR-06B.1A index recovery refused: neither active nor legacy index exists"
            )
        _create_active_index()
        active = _read_index_state(_ACTIVE_ACCOUNT_INDEX)
        if active is None:
            raise RuntimeError("PR-06B.1A index recovery refused: target index was not created")
        _require_index_shape(active, predicate=_ACTIVE_PREDICATE)

    _require_valid_ready(active, role="target")
    _raise_after_validated_target_for_test()

    if legacy is not None:
        # A target that is merely named correctly is insufficient.  It has
        # passed both its definition and pg_index validity/ready checks above.
        _drop_index_concurrently(_LEGACY_ACCOUNT_INDEX)

    if _read_index_state(_LEGACY_ACCOUNT_INDEX) is not None:
        raise RuntimeError("PR-06B.1A index recovery refused: legacy index still exists")
    active = _read_index_state(_ACTIVE_ACCOUNT_INDEX)
    if active is None:
        raise RuntimeError("PR-06B.1A index recovery refused: target index disappeared")
    _require_index_shape(active, predicate=_ACTIVE_PREDICATE)
    _require_valid_ready(active, role="target")


def downgrade() -> None:
    """Restore legacy index only when it cannot erase rejoin history."""

    _assert_no_rejoin_history()
    legacy = _read_index_state(_LEGACY_ACCOUNT_INDEX)
    active = _read_index_state(_ACTIVE_ACCOUNT_INDEX)

    if legacy is not None:
        _require_index_shape(legacy, predicate=_LEGACY_PREDICATE)
        _require_valid_ready(legacy, role="legacy")
    else:
        if active is None:
            raise RuntimeError(
                "PR-06B.1A downgrade refused: neither active nor legacy index exists"
            )
        _require_index_shape(active, predicate=_ACTIVE_PREDICATE)
        _require_valid_ready(active, role="target")
        _create_legacy_index()
        legacy = _read_index_state(_LEGACY_ACCOUNT_INDEX)
        if legacy is None:
            raise RuntimeError("PR-06B.1A downgrade refused: legacy index was not created")
        _require_index_shape(legacy, predicate=_LEGACY_PREDICATE)
        _require_valid_ready(legacy, role="legacy")

    if active is not None:
        _require_index_shape(active, predicate=_ACTIVE_PREDICATE)
        _drop_index_concurrently(_ACTIVE_ACCOUNT_INDEX)

    if _read_index_state(_ACTIVE_ACCOUNT_INDEX) is not None:
        raise RuntimeError("PR-06B.1A downgrade refused: target index still exists")
