"""Real PostgreSQL migration and integrity checks for PR-06B.1B.

The test owns a disposable database.  It intentionally verifies catalog state
and real constraint failures instead of treating ORM compilation as evidence.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from app.db.models import Base
from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PARENT_REVISION = "7a6c8e1d5f04"
CONVERSATION_REVISION = "b7d3a1e9c642"
CONVERSATION_TABLES = {
    "conversation",
    "conversation_message",
    "conversation_message_option",
    "conversation_submission",
    "conversation_submission_recovery",
}


def _normalized_catalog_predicate(value: str) -> str:
    """Compare PostgreSQL predicates without depending on implicit text casts."""

    return "".join(value.split()).replace("::text", "")


def _migration_database_url() -> URL:
    source_url = make_url(os.environ["TEST_DATABASE_URL"])
    if source_url.get_backend_name() != "postgresql" or not source_url.database:
        raise RuntimeError("PR-06B.1B PostgreSQL migration tests require TEST_DATABASE_URL")
    database = f"{source_url.database}_pr06b1b_migration_test"
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
            connection.execute(text(f"CREATE DATABASE {database} TEMPLATE template0 ENCODING 'UTF8'"))
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


def _run_alembic(url: URL, *arguments: str, succeeds: bool = True) -> str:
    environment = os.environ.copy()
    environment.update({"DATABASE_URL": url.render_as_string(hide_password=False), "DISABLE_SCHEDULER": "1"})
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
    return create_engine(url, future=True)


def _expect_integrity(engine, statement: str, parameters: dict[str, object] | None = None) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(statement), parameters or {})


def _seed_memberships(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_account (id, name, email) VALUES "
                "('p1b_user_a', 'A', 'p1b-a@example.invalid'), "
                "('p1b_user_other', 'Other', 'p1b-other@example.invalid')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trip (id, name, destination, expected_group_size, currency, status, created_by_user_id) VALUES "
                "('p1b_trip', 'Trip', 'Test', 2, 'USD', 'planning', 'p1b_user_a'), "
                "('p1b_other_trip', 'Other Trip', 'Test', 2, 'USD', 'planning', 'p1b_user_other')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) VALUES "
                "('p1b_member_a', 'p1b_trip', 'p1b_user_a', 'organizer', 'test', 'joined'), "
                "('p1b_other_member', 'p1b_other_trip', 'p1b_user_other', 'organizer', 'test', 'joined')"
            )
        )


def _insert_conversation(engine, conversation_id: str, *, owner: str = "p1b_member_a", trip: str = "p1b_trip", scope: str = "personal", context_key: str = "personal", status: str = "active", deleted_at: datetime | None = None, purge_due_at: datetime | None = None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO conversation "
                "(id, trip_id, owner_membership_id, scope, context_key, status, deleted_at, purge_due_at) "
                "VALUES (:id, :trip, :owner, :scope, :context_key, :status, :deleted_at, :purge_due_at)"
            ),
            {
                "id": conversation_id,
                "trip": trip,
                "owner": owner,
                "scope": scope,
                "context_key": context_key,
                "status": status,
                "deleted_at": deleted_at,
                "purge_due_at": purge_due_at,
            },
        )


def test_pr06b1b_postgresql_migration_and_private_ownership_contract() -> None:
    url = _migration_database_url()
    _recreate_database(url)
    engine = _engine(url)
    try:
        _run_alembic(url, "upgrade", "head")
        inspector = inspect(engine)
        assert CONVERSATION_TABLES <= set(inspector.get_table_names())
        assert _run_alembic(url, "current").strip().startswith(CONVERSATION_REVISION)
        assert CONVERSATION_TABLES <= set(Base.metadata.tables)
        for table_name in CONVERSATION_TABLES:
            assert {column["name"] for column in inspector.get_columns(table_name)} == {
                column.name for column in Base.metadata.tables[table_name].columns
            }

        indexes = {index["name"]: index for index in inspector.get_indexes("conversation")}
        assert indexes["ux_conversation_active_natural_identity"]["unique"] is True
        assert _normalized_catalog_predicate(
            indexes["ux_conversation_active_natural_identity"]["dialect_options"]["postgresql_where"]
        ) in {"((status)='active')", "(status='active')"}
        assert {"ix_conversation_retention_expiry_active", "ix_conversation_purge_due"} <= set(indexes)
        submission_indexes = {index["name"]: index for index in inspector.get_indexes("conversation_submission")}
        assert submission_indexes["ux_conversation_submission_one_running"]["unique"] is True
        assert _normalized_catalog_predicate(
            submission_indexes["ux_conversation_submission_one_running"]["dialect_options"]["postgresql_where"]
        ) in {"((state)='running')", "(state='running')"}

        # An empty schema may roll back and upgrade again without data loss.
        _run_alembic(url, "downgrade", PARENT_REVISION)
        assert not (CONVERSATION_TABLES & set(inspect(engine).get_table_names()))
        _run_alembic(url, "upgrade", "head")
        _seed_memberships(engine)

        _insert_conversation(engine, "p1b_conversation_a")
        _expect_integrity(
            engine,
            "INSERT INTO conversation (id, trip_id, owner_membership_id, scope, context_key, status) "
            "VALUES ('p1b_cross_trip', 'p1b_trip', 'p1b_other_member', 'personal', 'personal', 'active')",
        )
        _expect_integrity(
            engine,
            "INSERT INTO conversation (id, trip_id, owner_membership_id, scope, context_key, status) "
            "VALUES ('p1b_invalid_scope', 'p1b_trip', 'p1b_member_a', 'unsafe', 'personal', 'active')",
        )
        _expect_integrity(
            engine,
            "INSERT INTO conversation (id, trip_id, owner_membership_id, scope, context_key, status) "
            "VALUES ('p1b_duplicate_active', 'p1b_trip', 'p1b_member_a', 'personal', 'personal', 'active')",
        )
        _expect_integrity(
            engine,
            "INSERT INTO conversation (id, trip_id, owner_membership_id, scope, context_key, status, deleted_at) "
            "VALUES ('p1b_bad_delete', 'p1b_trip', 'p1b_member_a', 'plan_assistant', 'global', 'deleted', now())",
        )

        deletion_time = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE conversation SET status = 'deleted', deleted_at = :deleted_at, purge_due_at = :purge_due_at "
                    "WHERE id = 'p1b_conversation_a'"
                ),
                {"deleted_at": deletion_time, "purge_due_at": deletion_time + timedelta(days=30)},
            )
        _insert_conversation(engine, "p1b_conversation_new")
        qualifying_activity = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE conversation SET last_qualifying_activity_at = :activity, "
                    "retention_expires_at = :expiry WHERE id = 'p1b_conversation_new'"
                ),
                {"activity": qualifying_activity, "expiry": qualifying_activity + timedelta(days=180)},
            )
        _expect_integrity(
            engine,
            "UPDATE conversation SET retention_expires_at = last_qualifying_activity_at - interval '1 second' "
            "WHERE id = 'p1b_conversation_new'",
        )
        with engine.begin() as connection:
            connection.execute(text("UPDATE trip_membership SET status = 'removed' WHERE id = 'p1b_member_a'"))
            connection.execute(
                text(
                    "INSERT INTO trip_membership (id, trip_id, user_id, role, join_method, status) "
                    "VALUES ('p1b_member_b', 'p1b_trip', 'p1b_user_a', 'organizer', 'test', 'joined')"
                )
            )
        _insert_conversation(engine, "p1b_conversation_b", owner="p1b_member_b")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE trip_membership SET quarantined_at = now(), quarantine_reason = 'abuse', "
                    "quarantined_by_membership_id = id WHERE id = 'p1b_member_b'"
                )
            )
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT owner_membership_id FROM conversation WHERE id = 'p1b_conversation_a'")
            ).scalar_one() == "p1b_member_a"

        _insert_conversation(
            engine,
            "p1b_conversation_plan",
            owner="p1b_member_b",
            scope="plan_assistant",
            context_key="global",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO conversation_message (id, conversation_id, sequence, role, content, state) VALUES "
                    "('p1b_user_message', 'p1b_conversation_new', 0, 'user', 'hello', 'completed'), "
                    "('p1b_assistant_message', 'p1b_conversation_new', 1, 'assistant', 'answer', 'completed'), "
                    "('p1b_second_user_message', 'p1b_conversation_new', 2, 'user', 'again', 'completed'), "
                    "('p1b_third_user_message', 'p1b_conversation_new', 3, 'user', 'later', 'completed'), "
                    "('p1b_plan_message', 'p1b_conversation_plan', 0, 'user', 'hello', 'completed'), "
                    "('p1b_plan_assistant_message', 'p1b_conversation_plan', 1, 'assistant', 'answer', 'completed')"
                )
            )
        _expect_integrity(engine, "INSERT INTO conversation_message (id, conversation_id, sequence, role, content, state) VALUES ('p1b_dup_sequence', 'p1b_conversation_new', 0, 'user', 'x', 'completed')")
        _expect_integrity(engine, "INSERT INTO conversation_message (id, conversation_id, sequence, role, content, state) VALUES ('p1b_bad_role', 'p1b_conversation_new', 2, 'tool', 'x', 'completed')")
        _expect_integrity(engine, "INSERT INTO conversation_message (id, conversation_id, sequence, role, content, state) VALUES ('p1b_unknown_parent', 'missing', 0, 'user', 'x', 'completed')")
        _expect_integrity(engine, "INSERT INTO conversation_message (id, conversation_id, sequence, role, content, state, plan_revision) VALUES ('p1b_bad_revision', 'p1b_conversation_new', 2, 'user', 'x', 'completed', -1)")

        with engine.begin() as connection:
            connection.execute(text("INSERT INTO conversation_message_option (id, assistant_message_id, ordinal, title, safe_patch) VALUES ('p1b_option', 'p1b_assistant_message', 0, 'Suggestion', '{}'::jsonb)"))
        _expect_integrity(engine, "INSERT INTO conversation_message_option (id, assistant_message_id, ordinal, title, safe_patch) VALUES ('p1b_duplicate_option', 'p1b_assistant_message', 0, 'Suggestion', '{}'::jsonb)")
        _expect_integrity(engine, "INSERT INTO conversation_message_option (id, assistant_message_id, ordinal, title, safe_patch) VALUES ('p1b_bad_patch', 'p1b_assistant_message', 1, 'Suggestion', '[]'::jsonb)")
        _expect_integrity(engine, "INSERT INTO conversation_message_option (id, assistant_message_id, ordinal, title, safe_patch) VALUES ('p1b_bad_parent', 'missing', 0, 'Suggestion', '{}'::jsonb)")

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO conversation_submission "
                    "(id, conversation_id, trip_id, owner_membership_id, idempotency_key, canonical_request_hmac, fingerprint_version, hmac_key_version, user_message_id, assistant_message_id, state, attempt_number, fence, response_metadata) "
                    "VALUES ('p1b_submission_done', 'p1b_conversation_new', 'p1b_trip', 'p1b_member_a', 'key-a', repeat('a', 64), 1, 'v1', 'p1b_user_message', 'p1b_assistant_message', 'completed', 1, 1, '{}'::jsonb)"
                )
            )
        _expect_integrity(engine, "INSERT INTO conversation_submission (id, conversation_id, trip_id, owner_membership_id, idempotency_key, canonical_request_hmac, fingerprint_version, hmac_key_version, user_message_id, state, attempt_number, fence, lease_expires_at, response_metadata) VALUES ('p1b_cross_message', 'p1b_conversation_plan', 'p1b_trip', 'p1b_member_b', 'key-a', repeat('a', 64), 1, 'v1', 'p1b_user_message', 'running', 1, 1, now() + interval '1 minute', '{}'::jsonb)")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO conversation_submission (id, conversation_id, trip_id, owner_membership_id, idempotency_key, canonical_request_hmac, fingerprint_version, hmac_key_version, user_message_id, assistant_message_id, state, attempt_number, fence, response_metadata) VALUES ('p1b_submission_other_conv', 'p1b_conversation_plan', 'p1b_trip', 'p1b_member_b', 'key-a', repeat('a', 64), 1, 'v1', 'p1b_plan_message', 'p1b_plan_assistant_message', 'completed', 1, 1, '{}'::jsonb)"))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO conversation_submission (id, conversation_id, trip_id, owner_membership_id, idempotency_key, canonical_request_hmac, fingerprint_version, hmac_key_version, user_message_id, state, attempt_number, fence, lease_expires_at, response_metadata) VALUES ('p1b_submission_running', 'p1b_conversation_new', 'p1b_trip', 'p1b_member_a', 'key-b', repeat('b', 64), 1, 'v1', 'p1b_second_user_message', 'running', 1, 1, now() + interval '1 minute', '{}'::jsonb)"))
        _expect_integrity(engine, "INSERT INTO conversation_submission (id, conversation_id, trip_id, owner_membership_id, idempotency_key, canonical_request_hmac, fingerprint_version, hmac_key_version, user_message_id, state, attempt_number, fence, lease_expires_at, response_metadata) VALUES ('p1b_submission_running_again', 'p1b_conversation_new', 'p1b_trip', 'p1b_member_a', 'key-c', repeat('c', 64), 1, 'v1', 'p1b_third_user_message', 'running', 1, 1, now() + interval '1 minute', '{}'::jsonb)")
        _expect_integrity(engine, "INSERT INTO conversation_submission (id, conversation_id, trip_id, owner_membership_id, idempotency_key, canonical_request_hmac, fingerprint_version, hmac_key_version, user_message_id, state, attempt_number, fence, response_metadata) VALUES ('p1b_bad_attempt', 'p1b_conversation_plan', 'p1b_trip', 'p1b_member_b', 'key-b', repeat('b', 64), 1, 'v1', 'p1b_plan_message', 'failed', 0, 0, '{}'::jsonb)")

        with engine.begin() as connection:
            connection.execute(text("INSERT INTO conversation_submission_recovery (id, submission_id, recovery_key, operation_version, accepted_attempt_number, accepted_fence, state, accepted_at) VALUES ('p1b_recovery', 'p1b_submission_done', 'recover-a', 1, 2, 2, 'running', now())"))
        _expect_integrity(engine, "INSERT INTO conversation_submission_recovery (id, submission_id, recovery_key, operation_version, accepted_attempt_number, accepted_fence, state, accepted_at) VALUES ('p1b_recovery_duplicate', 'p1b_submission_done', 'recover-a', 1, 2, 2, 'running', now())")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO conversation_submission_recovery (id, submission_id, recovery_key, operation_version, accepted_attempt_number, accepted_fence, state, accepted_at) VALUES ('p1b_recovery_other_submission', 'p1b_submission_running', 'recover-a', 1, 2, 2, 'running', now())"))
        _expect_integrity(engine, "INSERT INTO conversation_submission_recovery (id, submission_id, recovery_key, operation_version, accepted_attempt_number, accepted_fence, state, accepted_at) VALUES ('p1b_recovery_bad', 'missing', 'recover-b', 0, 0, 0, 'running', now())")
        recovery_columns = {column["name"] for column in inspect(engine).get_columns("conversation_submission_recovery")}
        assert {"content", "user_message_id"}.isdisjoint(recovery_columns)

        refused = _run_alembic(url, "downgrade", PARENT_REVISION, succeeds=False)
        assert "conversation data would be lost" in refused
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM conversation")).scalar_one() == 4
    finally:
        engine.dispose()
        _drop_database(url)
