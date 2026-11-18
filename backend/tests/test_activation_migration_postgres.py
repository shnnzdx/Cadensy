"""PostgreSQL migration for activation events and first_submitted_at."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PARENT_REVISION = "b7d3a1e9c642"
ACTIVATION_REVISION = "c8e1d4a90b27"


def _migration_database_url() -> URL:
    source_url = make_url(os.environ["TEST_DATABASE_URL"])
    if source_url.get_backend_name() != "postgresql" or not source_url.database:
        raise RuntimeError("Activation migration tests require TEST_DATABASE_URL")
    database = f"{source_url.database}_activation_migration_test"
    require_safe_test_database_name(database)
    return source_url.set(database=database)


def _recreate_database(url: URL) -> None:
    admin = create_engine(url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
    database = quote_ident(url.database or "")
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": url.database},
            )
            connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
            connection.execute(
                text(f"CREATE DATABASE {database} TEMPLATE template0 ENCODING 'UTF8'")
            )
    finally:
        admin.dispose()


def _drop_database(url: URL) -> None:
    admin = create_engine(url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
    database = quote_ident(url.database or "")
    try:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {database}"))
    finally:
        admin.dispose()


def _run_alembic(url: URL, *arguments: str) -> str:
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
        timeout=120,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    return output


def test_activation_migration_upgrade_downgrade_and_privacy_constraint():
    url = _migration_database_url()
    _recreate_database(url)
    engine = create_engine(url, future=True)
    try:
        _run_alembic(url, "upgrade", ACTIVATION_REVISION)
        inspected = inspect(engine)
        assert "activation_event" in inspected.get_table_names()
        columns = {column["name"] for column in inspected.get_columns("activation_event")}
        assert columns == {
            "id",
            "event_name",
            "trip_id",
            "invite_id",
            "membership_id",
            "actor_kind",
            "occurred_at",
            "dedup_key",
            "metadata",
        }
        preference_columns = {column["name"] for column in inspected.get_columns("preference")}
        assert "first_submitted_at" in preference_columns
        with engine.begin() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == ACTIVATION_REVISION

        with engine.begin() as connection:
            user_id = "m1guser0000000000000000000000001"
            trip_id = "m1gtrip0000000000000000000000001"
            connection.execute(
                text(
                    "INSERT INTO user_account (id, name, email) VALUES "
                    "(:id, 'Migration', 'activation-migration@example.invalid')"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO trip (id, name, destination, expected_group_size, currency, "
                    "status, created_by_user_id) VALUES "
                    "(:id, 'Trip', 'Test', 1, 'USD', 'planning', :user_id)"
                ),
                {"id": trip_id, "user_id": user_id},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO activation_event "
                        "(id, event_name, trip_id, actor_kind, dedup_key, metadata) VALUES "
                        "('m1gevent000000000000000000000001', 'trip_created', :trip_id, "
                        "'system', 'trip_created:privacy-probe', "
                        """'{"token":"raw-token"}'::jsonb)"""
                    ),
                    {"trip_id": trip_id},
                )

        _run_alembic(url, "downgrade", PARENT_REVISION)
        downgraded = inspect(engine)
        assert "activation_event" not in downgraded.get_table_names()
        assert "first_submitted_at" not in {
            column["name"] for column in downgraded.get_columns("preference")
        }
        _run_alembic(url, "upgrade", "head")
        with engine.begin() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == ACTIVATION_REVISION
    finally:
        engine.dispose()
        _drop_database(url)
