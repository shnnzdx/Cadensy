"""PostgreSQL evidence for the Phase 1 additive Trip/ledger migration.

This module deliberately starts from the B.1 Alembic head rather than ORM
metadata.  It creates only a locally named disposable database and refuses all
other endpoints before a destructive recreate.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from app.db.models import MutationIdempotency, Trip
from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]
B1_REVISION = "6f77e775d201"
PHASE1_REVISION = "c2a4b6d8e0f1"
MIGRATION_DATABASE_NAME = "tripsync_phase1_schema_test"


def test_phase1_orm_contract_has_frozen_trip_and_ledger_fields() -> None:
    trip_revision = Trip.__table__.c.planning_facts_revision
    aggregate_scope = MutationIdempotency.__table__.c.aggregate_scope
    trip_result = MutationIdempotency.__table__.c.resulting_trip_planning_facts_revision

    assert trip_revision.nullable is False
    assert trip_revision.default is not None and trip_revision.default.arg == 0
    assert trip_revision.server_default is not None
    assert aggregate_scope.nullable is False
    assert aggregate_scope.default is not None and aggregate_scope.default.arg == "plan"
    assert aggregate_scope.server_default is not None
    assert trip_result.nullable is True


def _require_local_disposable_url(database_url: str) -> URL:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Phase 1 migration tests require PostgreSQL")
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Phase 1 migration tests require a localhost PostgreSQL URL")
    if not url.database:
        raise RuntimeError("Phase 1 migration tests require an explicit database name")
    require_safe_test_database_name(url.database)
    return url


def _migration_database_url(test_database_url: str) -> URL:
    source = _require_local_disposable_url(test_database_url)
    result = source.set(database=MIGRATION_DATABASE_NAME)
    require_safe_test_database_name(MIGRATION_DATABASE_NAME)
    if result.database == source.database:
        raise RuntimeError("Migration database must be separate from pytest's database")
    return result


def _recreate_database(url: URL) -> None:
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin_engine.connect() as connection:
            quoted = quote_ident(url.database or "")
            connection.execute(
                text(
                    "select pg_terminate_backend(pid) from pg_stat_activity "
                    "where datname = :database and pid <> pg_backend_pid()"
                ),
                {"database": url.database},
            )
            connection.execute(text(f"drop database if exists {quoted}"))
            connection.execute(text(f"create database {quoted} template template0 encoding 'UTF8'"))
    finally:
        admin_engine.dispose()


def _drop_database(url: URL) -> None:
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin_engine.connect() as connection:
            quoted = quote_ident(url.database or "")
            connection.execute(
                text(
                    "select pg_terminate_backend(pid) from pg_stat_activity "
                    "where datname = :database and pid <> pg_backend_pid()"
                ),
                {"database": url.database},
            )
            connection.execute(text(f"drop database if exists {quoted}"))
    finally:
        admin_engine.dispose()


@contextmanager
def _alembic_database(database_url: URL):
    """Run Alembic against a fresh, dedicated local PostgreSQL database."""
    _recreate_database(database_url)
    try:
        import app.db.session as session_module

        original_database_url = session_module.DATABASE_URL
        session_module.DATABASE_URL = database_url.render_as_string(hide_password=False)
        try:
            config = Config(str(BACKEND_ROOT / "alembic.ini"))
            command.upgrade(config, B1_REVISION)
            yield config
        finally:
            session_module.DATABASE_URL = original_database_url
    finally:
        _drop_database(database_url)


@pytest.fixture
def phase1_migration_database_url() -> URL:
    from tests.conftest import TEST_DATABASE_URL

    return _migration_database_url(TEST_DATABASE_URL)


def _upgrade(config: Config) -> None:
    command.upgrade(config, PHASE1_REVISION)


def _insert_b1_ledger_fixture(connection, *, plan_revision: int | None = 0) -> None:
    connection.execute(
        text(
            """
            insert into user_account (id, name, email)
            values ('phase1user', 'Phase one user', 'phase1-user@example.invalid')
            """
        )
    )
    connection.execute(
        text(
            """
            insert into trip (id, name, destination, expected_group_size, currency, status, created_by_user_id)
            values ('phase1trip', 'Phase one trip', 'Chicago', 0, 'USD', 'planning', 'phase1user')
            """
        )
    )
    connection.execute(
        text(
            """
            insert into trip_membership (id, trip_id, user_id, role, join_method, status)
            values ('phase1member', 'phase1trip', 'phase1user', 'organizer', 'account', 'joined')
            """
        )
    )
    connection.execute(
        text(
            """
            insert into mutation_idempotency (
                id, trip_id, actor_membership_id, idempotency_key, operation_code,
                resource_kind, resource_id, fingerprint_version, request_hmac,
                hmac_key_version, outcome_contract_version, response_status,
                outcome_json, resulting_plan_revision, completed_at, expires_at
            ) values (
                'phase1ledger', 'phase1trip', 'phase1member', 'phase1-key', 'plan.test.v1',
                'plan', 'phase1plan', 1, :request_hmac,
                'fixture-v1', 1, 200,
                cast(:outcome_json as jsonb), :plan_revision, now(), now() + interval '1 day'
            )
            """
        ),
        {
            "request_hmac": "a" * 64,
            "outcome_json": '{"kind":"safe"}',
            "plan_revision": plan_revision,
        },
    )


def test_clean_upgrade_preserves_b1_rows_and_installs_frozen_schema(phase1_migration_database_url: URL) -> None:
    with _alembic_database(phase1_migration_database_url) as config:
        engine = create_engine(phase1_migration_database_url, future=True)
        try:
            with engine.begin() as connection:
                _insert_b1_ledger_fixture(connection)
            with engine.connect() as connection:
                original_row = connection.execute(
                    text(
                        "select request_hmac, outcome_json::text, resulting_plan_revision "
                        "from mutation_idempotency where id = 'phase1ledger'"
                    )
                ).one()
            _upgrade(config)
            inspector = inspect(engine)
            trip_columns = {column["name"]: column for column in inspector.get_columns("trip")}
            ledger_columns = {
                column["name"]: column
                for column in inspector.get_columns("mutation_idempotency")
            }
            assert trip_columns["planning_facts_revision"]["nullable"] is False
            assert trip_columns["planning_facts_revision"]["type"].__class__.__name__ == "BIGINT"
            assert trip_columns["planning_facts_revision"]["default"] is not None
            assert ledger_columns["aggregate_scope"]["nullable"] is False
            assert ledger_columns["aggregate_scope"]["default"] is not None
            assert ledger_columns["resulting_trip_planning_facts_revision"]["nullable"] is True
            assert {
                constraint["name"] for constraint in inspector.get_check_constraints("trip")
            } >= {"ck_trip_planning_facts_revision_nonnegative"}
            assert {
                constraint["name"]
                for constraint in inspector.get_check_constraints("mutation_idempotency")
            } >= {
                "ck_mutation_idempotency_aggregate_scope",
                "ck_mutation_idempotency_plan_result_nonnegative",
                "ck_mutation_idempotency_trip_result_nonnegative",
                "ck_mutation_idempotency_result_scope",
            }
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "select request_hmac, outcome_json::text, resulting_plan_revision, aggregate_scope "
                        "from mutation_idempotency where id = 'phase1ledger'"
                    )
                ).one()
                trip_revision = connection.execute(
                    text("select planning_facts_revision from trip where id = 'phase1trip'")
                ).scalar_one()
            assert row == (*original_row, "plan")
            assert trip_revision == 0
            assert {index["name"] for index in inspector.get_indexes("mutation_idempotency")} >= {
                "ix_mutation_idempotency_expires_at"
            }
        finally:
            engine.dispose()


def test_upgrade_fails_closed_for_malformed_historical_plan_result(
    phase1_migration_database_url: URL,
) -> None:
    with _alembic_database(phase1_migration_database_url) as config:
        engine = create_engine(phase1_migration_database_url, future=True)
        try:
            with engine.begin() as connection:
                _insert_b1_ledger_fixture(connection, plan_revision=None)
            with pytest.raises(RuntimeError, match="historical Plan ledger"):
                _upgrade(config)
            inspector = inspect(engine)
            assert "planning_facts_revision" not in {
                column["name"] for column in inspector.get_columns("trip")
            }
            assert "aggregate_scope" not in {
                column["name"] for column in inspector.get_columns("mutation_idempotency")
            }
            with engine.connect() as connection:
                assert connection.execute(
                    text("select resulting_plan_revision from mutation_idempotency where id = 'phase1ledger'")
                ).scalar_one() is None
        finally:
            engine.dispose()


def test_clean_downgrade_is_allowed_without_trip_scope_outcomes(phase1_migration_database_url: URL) -> None:
    with _alembic_database(phase1_migration_database_url) as config:
        _upgrade(config)
        command.downgrade(config, B1_REVISION)
        engine = create_engine(phase1_migration_database_url, future=True)
        try:
            inspector = inspect(engine)
            assert "planning_facts_revision" not in {
                column["name"] for column in inspector.get_columns("trip")
            }
            assert "aggregate_scope" not in {
                column["name"] for column in inspector.get_columns("mutation_idempotency")
            }
        finally:
            engine.dispose()


def test_downgrade_refuses_to_discard_trip_scope_outcomes(phase1_migration_database_url: URL) -> None:
    with _alembic_database(phase1_migration_database_url) as config:
        engine = create_engine(phase1_migration_database_url, future=True)
        try:
            _upgrade(config)
            with engine.begin() as connection:
                _insert_b1_ledger_fixture(connection)
                connection.execute(
                    text(
                        """
                        update mutation_idempotency
                        set aggregate_scope = 'trip_planning_facts',
                            resulting_plan_revision = null,
                            resulting_trip_planning_facts_revision = 1
                        where id = 'phase1ledger'
                        """
                    )
                )
            with pytest.raises(RuntimeError, match="Trip-scope"):
                command.downgrade(config, B1_REVISION)
            inspector = inspect(engine)
            assert "planning_facts_revision" in {
                column["name"] for column in inspector.get_columns("trip")
            }
            with engine.connect() as connection:
                assert connection.execute(
                    text("select aggregate_scope from mutation_idempotency where id = 'phase1ledger'")
                ).scalar_one() == "trip_planning_facts"
        finally:
            engine.dispose()
