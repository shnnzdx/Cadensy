"""Read-only catalog verification for the reviewed PR-06B database shape."""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


PR06B_HEAD = "b7d3a1e9c642"
REQUIRED_TABLES = (
    "conversation",
    "conversation_message",
    "conversation_message_option",
    "conversation_submission",
    "conversation_submission_recovery",
)
ACTIVE_ACCOUNT_INDEX = "ux_trip_membership_active_account"


class SchemaVerificationError(RuntimeError):
    """The database is not the reviewed, promotion-compatible PR-06B shape."""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SchemaVerificationError("DATABASE_URL is required by the schema verifier")
    return value


def _normalize(value: str) -> str:
    return "".join(value.split())


def verify(engine, *, expected_head: str = PR06B_HEAD) -> None:  # type: ignore[no-untyped-def]
    """Verify revision and catalog facts whose absence would make release unsafe."""

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if revision != expected_head:
            raise SchemaVerificationError(
                f"expected Alembic revision {expected_head}, found {revision or 'none'}"
            )
        missing = [
            table
            for table in REQUIRED_TABLES
            if connection.execute(text("SELECT to_regclass(:table_name)"), {"table_name": table}).scalar_one() is None
        ]
        if missing:
            raise SchemaVerificationError(f"missing required PR-06B tables: {', '.join(missing)}")
        index = connection.execute(
            text(
                """
                SELECT index_data.indisunique, index_data.indisvalid, index_data.indisready,
                       index_data.indnkeyatts,
                       coalesce(pg_get_expr(index_data.indpred, index_data.indrelid), '') AS predicate,
                       array_agg(attribute.attname ORDER BY index_key.ordinality)
                           FILTER (WHERE index_key.ordinality <= index_data.indnkeyatts) AS columns,
                       bool_or(index_key.ordinality <= index_data.indnkeyatts AND index_key.attnum = 0)
                           AS has_expression_key
                FROM pg_index AS index_data
                JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
                JOIN pg_namespace AS index_namespace ON index_namespace.oid = index_class.relnamespace
                LEFT JOIN unnest(index_data.indkey) WITH ORDINALITY AS index_key(attnum, ordinality) ON TRUE
                LEFT JOIN pg_attribute AS attribute
                  ON attribute.attrelid = index_data.indrelid AND attribute.attnum = index_key.attnum
                WHERE index_namespace.nspname = current_schema()
                  AND index_class.relname = :index_name
                  AND index_data.indrelid = 'trip_membership'::regclass
                GROUP BY index_data.indisunique, index_data.indisvalid, index_data.indisready,
                         index_data.indnkeyatts, index_data.indpred, index_data.indrelid
                """
            ),
            {"index_name": ACTIVE_ACCOUNT_INDEX},
        ).mappings().one_or_none()
        expected_predicate = "((user_id IS NOT NULL) AND ((status)::text <> 'removed'::text))"
        if (
            index is None
            or not index["indisunique"]
            or not index["indisvalid"]
            or not index["indisready"]
            or index["indnkeyatts"] != 2
            or tuple(index["columns"] or ()) != ("trip_id", "user_id")
            or index["has_expression_key"]
            or _normalize(index["predicate"]) != _normalize(expected_predicate)
        ):
            raise SchemaVerificationError("active-account index is not the reviewed valid PR-06B definition")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", default=PR06B_HEAD)
    args = parser.parse_args()
    if args.expected_head != PR06B_HEAD:
        raise SystemExit("schema verifier only accepts the reviewed PR-06B head")
    engine = create_engine(_database_url(), poolclass=NullPool)
    try:
        verify(engine, expected_head=args.expected_head)
    except SchemaVerificationError as error:
        raise SystemExit(f"PR-06B schema verification failed: {error}") from error
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
