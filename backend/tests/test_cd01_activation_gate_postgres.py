"""Synthetic PostgreSQL proof for the b7d3 -> c8e1 production migration gate."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from tests._db_test_harness import quote_ident, require_safe_test_database_name


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PARENT_REVISION = "b7d3a1e9c642"
ACTIVATION_REVISION = "c8e1d4a90b27"
RUNNER_PATH = BACKEND_ROOT / "scripts" / "cd01" / "run_pr06b_schema_migration.py"
VERIFIER_PATH = BACKEND_ROOT / "scripts" / "cd01" / "verify_pr06b_schema.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database_url() -> URL:
    source_url = make_url(os.environ["TEST_DATABASE_URL"])
    if source_url.get_backend_name() != "postgresql" or not source_url.database:
        raise RuntimeError("Activation gate tests require TEST_DATABASE_URL")
    database = f"{source_url.database}_activation_gate_test"
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


def _alembic(url: URL, *arguments: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=_child_env(url),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"


def _child_env(url: URL) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url.render_as_string(hide_password=False)
    environment["DISABLE_SCHEDULER"] = "1"
    return environment


def _revision(url: URL) -> str:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()
    assert isinstance(revision, str)
    return revision


def _run_runner(url: URL, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER_PATH), *arguments],
        cwd=BACKEND_ROOT,
        env=_child_env(url),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_b7d3_start_reaches_c8e1_and_verifier_requires_activation_event():
    url = _database_url()
    _recreate_database(url)
    engine = create_engine(url, future=True)
    verifier = _load(VERIFIER_PATH, "cd01_verify_pr06b_schema_activation_gate")
    try:
        _alembic(url, "upgrade", PARENT_REVISION)
        assert _revision(url) == PARENT_REVISION
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('activation_event')")).scalar_one() is None

        mismatched = _run_runner(
            url,
            "--expected-start",
            "c2a4b6d8e0f1",
            "--target",
            ACTIVATION_REVISION,
            "--timeout-seconds",
            "120",
        )
        assert mismatched.returncode != 0
        assert "refusing migration: expected start c2a4b6d8e0f1, found b7d3a1e9c642" in (
            mismatched.stderr + mismatched.stdout
        )
        assert _revision(url) == PARENT_REVISION

        migrated = _run_runner(
            url,
            "--expected-start",
            PARENT_REVISION,
            "--target",
            ACTIVATION_REVISION,
            "--timeout-seconds",
            "120",
        )
        assert migrated.returncode == 0, f"{migrated.stdout}\n{migrated.stderr}"
        assert _revision(url) == ACTIVATION_REVISION
        verifier.verify(engine, expected_head=ACTIVATION_REVISION)

        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE activation_event DROP CONSTRAINT uq_activation_event_dedup_key"))
        with pytest.raises(verifier.SchemaVerificationError, match="dedup_key unique index"):
            verifier.verify(engine, expected_head=ACTIVATION_REVISION)

        with engine.begin() as connection:
            connection.execute(text("DROP TABLE activation_event"))
        with pytest.raises(verifier.SchemaVerificationError, match="activation_event"):
            verifier.verify(engine, expected_head=ACTIVATION_REVISION)
    finally:
        engine.dispose()
        _drop_database(url)
