"""Real PostgreSQL recovery and contract checks for PR-06B.1A.1.

This suite owns a separately named disposable database.  It deliberately
exercises interrupted concurrent-index recovery rather than treating the
migration source text as evidence of safety.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_CHECKPOINT = "9db6f1a1041b"
ACTIVE_INDEX = "ux_trip_membership_active_account"
LEGACY_INDEX = "one_membership_per_user_per_trip"


def _migration_database_url() -> URL:
    source_url = make_url(os.environ["TEST_DATABASE_URL"])
    if source_url.get_backend_name() != "postgresql" or not source_url.database:
        raise RuntimeError("PR-06B.1A PostgreSQL migration tests require TEST_DATABASE_URL")
    database = f"{source_url.database}_pr06b1a_migration_test"
    require_safe_test_database_name(database)
    return source_url.set(database=database)


def _recreate_database(url: URL) -> None:
    admin = create_engine(
        url.set(database="postgres"),
        future=True,
        isolation_level="AUTOCOMMIT",
        connect_args={"client_encoding": "utf8"},
    )
    database = quote_ident(url.database or "")
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database AND pid <> pg_backend_pid()
                    """
                ),
                {"database": url.database},
            )
            connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
            connection.execute(text(f"CREATE DATABASE {database} TEMPLATE template0 ENCODING 'UTF8'"))
    finally:
        admin.dispose()


def _drop_database(url: URL) -> None:
    admin = create_engine(
        url.set(database="postgres"),
        future=True,
        isolation_level="AUTOCOMMIT",
        connect_args={"client_encoding": "utf8"},
    )
    database = quote_ident(url.database or "")
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database AND pid <> pg_backend_pid()
                    """
                ),
                {"database": url.database},
            )
            connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
    finally:
        admin.dispose()


def _run_alembic(url: URL, *arguments: str, succeeds: bool = True) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": url.render_as_string(hide_password=False),
            "DISABLE_SCHEDULER": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if succeeds:
        assert completed.returncode == 0, output
    else:
        assert completed.returncode != 0, output
    return output


def _engine(url: URL):
    return create_engine(
        url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )


def _index_catalog(engine, index_name: str) -> dict | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                """
                SELECT
                    access_method.amname AS access_method,
                    index_data.indisunique AS is_unique,
                    index_data.indisvalid AS is_valid,
                    index_data.indisready AS is_ready,
                    pg_get_expr(index_data.indpred, index_data.indrelid) AS predicate,
                    index_data.indnkeyatts AS key_attribute_count,
                    index_data.indnatts - index_data.indnkeyatts AS included_attribute_count,
                    array_agg(attribute.attname ORDER BY index_key.ordinality)
                        FILTER (WHERE index_key.ordinality <= index_data.indnkeyatts)
                        AS key_columns,
                    array_agg(index_option.option ORDER BY index_key.ordinality)
                        FILTER (WHERE index_key.ordinality <= index_data.indnkeyatts)
                        AS key_options,
                    bool_or(
                        index_key.ordinality <= index_data.indnkeyatts
                        AND index_key.attnum = 0
                    ) AS has_expression_key
                FROM pg_index AS index_data
                JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
                JOIN pg_am AS access_method ON access_method.oid = index_class.relam
                LEFT JOIN unnest(index_data.indkey) WITH ORDINALITY
                    AS index_key(attnum, ordinality) ON TRUE
                LEFT JOIN unnest(index_data.indoption) WITH ORDINALITY
                    AS index_option(option, ordinality)
                    ON index_option.ordinality = index_key.ordinality
                LEFT JOIN pg_attribute AS attribute
                    ON attribute.attrelid = index_data.indrelid
                   AND attribute.attnum = index_key.attnum
                WHERE index_class.relname = :index_name
                  AND index_data.indrelid = 'trip_membership'::regclass
                GROUP BY
                    access_method.amname,
                    index_data.indisunique,
                    index_data.indisvalid,
                    index_data.indisready,
                    index_data.indpred,
                    index_data.indrelid,
                    index_data.indnkeyatts,
                    index_data.indnatts
                """
            ),
            {"index_name": index_name},
        ).mappings().one_or_none()


def _execute_concurrently(engine, statement: str) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(statement))


def _seed_account_and_trip(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_account (id, name, email) VALUES "
                "('pr06b1a_user', 'PR06B1A Test User', 'pr06b1a@example.invalid'), "
                "('pr06b1a_actor', 'PR06B1A Actor', 'pr06b1a-actor@example.invalid')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trip (id, name, destination, expected_group_size, currency, status, created_by_user_id) VALUES "
                "('pr06b1a_trip', 'PR06B1A Trip', 'Test', 2, 'USD', 'planning', 'pr06b1a_user'), "
                "('pr06b1a_other_trip', 'PR06B1A Other Trip', 'Test', 2, 'USD', 'planning', 'pr06b1a_actor')"
            )
        )


def _create_cancelled_target_index(url: URL) -> None:
    """Leave a real invalid concurrent index while legacy uniqueness remains."""

    dsn = url.render_as_string(hide_password=False).replace("postgresql+psycopg", "postgresql")
    holder = psycopg.connect(dsn)
    creator = psycopg.connect(dsn, autocommit=True)
    errors: list[Exception] = []
    try:
        with holder.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute(
                """
                INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status)
                VALUES ('pr06b1a_index_holder', 'pr06b1a_trip', 'pr06b1a_user', 'participant', 'test', 'joined')
                """
            )

        def create_index() -> None:
            try:
                with creator.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE UNIQUE INDEX CONCURRENTLY ux_trip_membership_active_account
                        ON trip_membership (trip_id, user_id)
                        WHERE user_id IS NOT NULL AND status <> 'removed'
                        """
                    )
            except Exception as exc:  # cancellation is the expected injection
                errors.append(exc)

        worker = threading.Thread(target=create_index)
        worker.start()
        deadline = time.monotonic() + 10
        cancelled = False
        while time.monotonic() < deadline:
            with psycopg.connect(dsn, autocommit=True) as admin:
                with admin.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT query
                        FROM pg_stat_activity
                        WHERE pid = %s
                        """,
                        (creator.info.backend_pid,),
                    )
                    row = cursor.fetchone()
                    if row is not None and "CREATE UNIQUE INDEX CONCURRENTLY" in row[0]:
                        cursor.execute("SELECT pg_cancel_backend(%s)", (creator.info.backend_pid,))
                        cancelled = cursor.fetchone()[0]
                        break
            time.sleep(0.05)
        worker.join(timeout=15)
        assert cancelled is True
        assert errors, "the concurrent create unexpectedly completed before cancellation"
    finally:
        holder.rollback()
        holder.close()
        creator.close()


def test_pr06b1a_postgresql_migration_recovery_and_schema_contract() -> None:
    url = _migration_database_url()
    _recreate_database(url)
    engine = _engine(url)
    try:
        # Preflight recognizes bad historical state rather than silently
        # granting it access through a broad "not removed" predicate.
        _run_alembic(url, "upgrade", "c2a4b6d8e0f1")
        _seed_account_and_trip(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                    "VALUES ('pr06b1a_corrupt', 'pr06b1a_trip', 'pr06b1a_user', 'participant', 'test', 'corrupted')"
                )
            )
        rejected_preflight = _run_alembic(url, "upgrade", SCHEMA_CHECKPOINT, succeeds=False)
        assert "contains unsupported status rows" in rejected_preflight

        engine.dispose()
        _recreate_database(url)
        engine = _engine(url)
        # The atomic structural checkpoint is independently recorded.
        _run_alembic(url, "upgrade", SCHEMA_CHECKPOINT)
        _seed_account_and_trip(engine)
        assert _index_catalog(engine, ACTIVE_INDEX) is None
        legacy = _index_catalog(engine, LEGACY_INDEX)
        assert legacy is not None and legacy["is_valid"] and legacy["is_ready"]

        # A same-name, valid B-tree index with an expression key must fail
        # closed; silently accepting it would weaken the uniqueness contract.
        _execute_concurrently(
            engine,
            """
            CREATE UNIQUE INDEX CONCURRENTLY ux_trip_membership_active_account
            ON trip_membership (trip_id, user_id, lower(status))
            WHERE user_id IS NOT NULL AND status <> 'removed'
            """,
        )
        malformed_shape = _run_alembic(url, "upgrade", "head", succeeds=False)
        assert "has an unexpected definition" in malformed_shape
        assert _index_catalog(engine, LEGACY_INDEX)["is_valid"] is True
        _execute_concurrently(engine, "DROP INDEX CONCURRENTLY ux_trip_membership_active_account")

        # A same-name index with a changed predicate is equally unsafe and
        # must leave the legacy uniqueness guard intact.
        _execute_concurrently(
            engine,
            """
            CREATE UNIQUE INDEX CONCURRENTLY ux_trip_membership_active_account
            ON trip_membership (trip_id, user_id)
            WHERE user_id IS NOT NULL AND status = 'joined'
            """,
        )
        malformed_predicate = _run_alembic(url, "upgrade", "head", succeeds=False)
        assert "has an unexpected definition" in malformed_predicate
        assert _index_catalog(engine, LEGACY_INDEX)["is_valid"] is True
        _execute_concurrently(engine, "DROP INDEX CONCURRENTLY ux_trip_membership_active_account")

        # Cancel an actual CREATE INDEX CONCURRENTLY while the old unique index
        # remains authoritative. The next upgrade must repair, not destroy rows.
        _create_cancelled_target_index(url)
        invalid = _index_catalog(engine, ACTIVE_INDEX)
        assert invalid is not None
        assert not invalid["is_valid"] or not invalid["is_ready"]
        assert _index_catalog(engine, LEGACY_INDEX)["is_valid"] is True
        _run_alembic(url, "upgrade", "head")

        active = _index_catalog(engine, ACTIVE_INDEX)
        assert active is not None
        assert active["is_unique"] and active["is_valid"] and active["is_ready"]
        assert active["access_method"] == "btree"
        assert active["key_attribute_count"] == 2
        assert active["included_attribute_count"] == 0
        assert tuple(active["key_columns"]) == ("trip_id", "user_id")
        assert tuple(active["key_options"]) == (0, 0)
        assert active["has_expression_key"] is False
        assert active["predicate"] == "((user_id IS NOT NULL) AND ((status)::text <> 'removed'::text))"
        assert _index_catalog(engine, LEGACY_INDEX) is None

        # A controlled interruption after target validation leaves both valid
        # indexes and the index revision unrecorded; a plain rerun resumes.
        _run_alembic(url, "downgrade", SCHEMA_CHECKPOINT)
        legacy = _index_catalog(engine, LEGACY_INDEX)
        assert legacy is not None and legacy["is_valid"] and legacy["is_ready"]
        assert _index_catalog(engine, ACTIVE_INDEX) is None
        injected = _run_alembic(
            url,
            "-x",
            "pr06b1a_fail_after_index_validation=1",
            "upgrade",
            "head",
            succeeds=False,
        )
        assert "injected failure after validated target index" in injected
        assert _index_catalog(engine, ACTIVE_INDEX)["is_valid"] is True
        assert _index_catalog(engine, LEGACY_INDEX)["is_valid"] is True
        _run_alembic(url, "upgrade", "head")

        with engine.begin() as connection:
            # A removed incarnation permits B; two active incarnations do not.
            connection.execute(
                text(
                    "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                    "VALUES ('pr06b1a_a', 'pr06b1a_trip', 'pr06b1a_user', 'participant', 'test', 'joined')"
                )
            )
            connection.execute(text("UPDATE trip_membership SET status = 'removed' WHERE id = 'pr06b1a_a'"))
            connection.execute(
                text(
                    "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                    "VALUES ('pr06b1a_b', 'pr06b1a_trip', 'pr06b1a_user', 'participant', 'test', 'joined'), "
                    "('pr06b1a_actor_member', 'pr06b1a_other_trip', 'pr06b1a_actor', 'organizer', 'test', 'joined')"
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                        "VALUES ('pr06b1a_duplicate', 'pr06b1a_trip', 'pr06b1a_user', 'participant', 'test', 'joined')"
                    )
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                        "VALUES ('pr06b1a_bad_status', 'pr06b1a_other_trip', 'pr06b1a_actor', 'participant', 'test', 'corrupted')"
                    )
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE trip_membership SET quarantined_at = now() WHERE id = 'pr06b1a_b'")
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE trip_membership
                        SET quarantined_at = now(), quarantine_reason = 'invalid',
                            quarantined_by_membership_id = id
                        WHERE id = 'pr06b1a_b'
                        """
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE trip_membership
                        SET quarantined_at = now(), quarantine_reason = 'abuse',
                            quarantined_by_membership_id = 'pr06b1a_actor_member'
                        WHERE id = 'pr06b1a_b'
                        """
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE trip_membership
                    SET quarantined_at = now(), quarantine_reason = 'abuse',
                        quarantined_by_membership_id = id
                    WHERE id = 'pr06b1a_b'
                    """
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                        "VALUES ('pr06b1a_quarantine_duplicate', 'pr06b1a_trip', 'pr06b1a_user', 'participant', 'test', 'joined')"
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO plan (id, trip_id, status, currency, needs_refresh) "
                    "VALUES ('pr06b1a_plan', 'pr06b1a_trip', 'draft', 'USD', false)"
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO plan_item
                        (id, plan_id, day_index, day_date, start_hour, title, place, tags, dietary_tags, is_meal, source, settledness)
                    VALUES
                        ('pr06b1a_item', 'pr06b1a_plan', 1, DATE '2026-01-01', 9, 'Test', 'Test', '[]', '[]', false, 'test', 'loose')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO decision_round (id, plan_item_id, kind, options, deadline, status, electorate_count)
                    VALUES ('pr06b1a_round', 'pr06b1a_item', 'test', '[]', now() + interval '1 hour', 'open', 2)
                    """
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE decision_round SET electorate_count = 3 WHERE id = 'pr06b1a_round'"))

        # New incarnations make the index rollback destructive, so it fails
        # before attempting to recreate the historical all-time uniqueness.
        refused = _run_alembic(url, "downgrade", SCHEMA_CHECKPOINT, succeeds=False)
        assert "account rejoin history cannot be collapsed" in refused

        # Once rejoin history is removed, the index checkpoint can safely
        # revert, but the atomic schema checkpoint still refuses to erase a
        # captured electorate count.
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM trip_membership WHERE id = 'pr06b1a_b'"))
        immutable_refused = _run_alembic(url, "downgrade", "c2a4b6d8e0f1", succeeds=False)
        assert "immutable electorate history would be lost" in immutable_refused
    finally:
        engine.dispose()
        _drop_database(url)
