"""Alembic environment for Cadensy's SQLAlchemy-owned schema."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base
from app.db.session import DATABASE_URL


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The application environment is authoritative. In CI and runbooks this is
# explicitly set to a disposable test database before Alembic is imported.
config.set_main_option("sqlalchemy.url", DATABASE_URL)
target_metadata = Base.metadata

# cadensy_invite_cleanup_marker is Alembic-owned and intentionally not an ORM
# model. create_all does not create it. Excluding it here keeps autogenerate
# from proposing a drop after Rev 1. Cleanup tests must use an Alembic-built
# database; they cannot see this table on a create_all schema.
_ALEMBIC_ONLY_TABLES = frozenset({"cadensy_invite_cleanup_marker"})


def include_object(obj, name, type_, reflected, compare_to):
    del compare_to
    if type_ == "table" and name in _ALEMBIC_ONLY_TABLES:
        return False
    table = getattr(obj, "table", None)
    if table is not None and getattr(table, "name", None) in _ALEMBIC_ONLY_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations without a live database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the explicitly configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            # PR-06B.1A's concurrent-index checkpoint must not commit an
            # earlier revision without also recording that earlier revision.
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
