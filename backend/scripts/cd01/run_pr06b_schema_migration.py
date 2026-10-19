"""Run the reviewed PR-06B Alembic chain under one PostgreSQL advisory lock.

This is an operational entry point, not application startup behaviour. The
dedicated migration-runner image invokes it only after the manual Gate M
workflow has checked the exact input revision. It does not stamp, downgrade,
or retry an uncertain migration.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


LOCK_NAMESPACE = 906_061
DEFAULT_TARGET = "b7d3a1e9c642"


class MigrationContractError(RuntimeError):
    """The runner cannot prove its bounded migration preconditions."""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise MigrationContractError("DATABASE_URL is required by the migration runner")
    return value


def _revision(connection) -> str:  # type: ignore[no-untyped-def]
    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if not isinstance(revision, str) or not revision:
        raise MigrationContractError("database has no single Alembic revision")
    return revision


def run(*, expected_start: str, target: str, timeout_seconds: int) -> None:
    """Assert the start revision, lock it, then execute exactly one upgrade."""

    root = Path(__file__).resolve().parents[2]
    engine = create_engine(_database_url(), poolclass=NullPool, isolation_level="AUTOCOMMIT")
    lock_target = int(target, 16) % (2**31 - 1)
    try:
        with engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :target)"),
                {"namespace": LOCK_NAMESPACE, "target": lock_target},
            ).scalar_one()
            if acquired is not True:
                raise MigrationContractError("another PR-06B migration runner holds the database lock")
            try:
                actual = _revision(connection)
                if actual != expected_start:
                    raise MigrationContractError(
                        f"refusing migration: expected start {expected_start}, found {actual}"
                    )
                completed = subprocess.run(
                    [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", target],
                    cwd=root,
                    check=False,
                    timeout=timeout_seconds,
                )
                if completed.returncode != 0:
                    raise MigrationContractError(
                        "Alembic upgrade failed; preserve task evidence and start a newly approved recovery run"
                    )
                final = _revision(connection)
                if final != target:
                    raise MigrationContractError(
                        f"Alembic returned success but database is at {final}, not {target}"
                    )
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :target)"),
                    {"namespace": LOCK_NAMESPACE, "target": lock_target},
                )
    except subprocess.TimeoutExpired as error:
        raise MigrationContractError("Alembic migration exceeded its approved time bound") from error
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-start", required=True)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.expected_start not in {"c2a4b6d8e0f1", "9db6f1a1041b", "7a6c8e1d5f04"}:
        raise SystemExit("unsupported approved PR-06B migration start revision")
    if args.target != DEFAULT_TARGET or args.timeout_seconds < 1 or args.timeout_seconds > 1800:
        raise SystemExit("unsupported PR-06B migration target or timeout")
    try:
        run(expected_start=args.expected_start, target=args.target, timeout_seconds=args.timeout_seconds)
    except MigrationContractError as error:
        raise SystemExit(f"PR-06B migration contract failed: {error}") from error


if __name__ == "__main__":
    main()
