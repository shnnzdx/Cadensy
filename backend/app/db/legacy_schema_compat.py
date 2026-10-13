"""Strict, read-only compatibility validation for the pre-Alembic f9 schema.

This module exists solely for one reviewed transition from the legacy schema to
the immutable ``f9ff23b7b84d`` Alembic root.  It does not stamp, migrate, or
alter a database.  Callers must run it before the separately authorized
``alembic stamp f9ff23b7b84d`` command.

The allowlist is deliberately small and historical:

* ``trip.archived_at`` was created by the retired cloud-demo helper and is no
  longer model-owned;
* ``change_proposal.extended_at`` was created by that same helper and is no
  longer model-owned; and
* the legacy helper installed a PostgreSQL ``false`` server default for
  ``plan.needs_refresh`` while the f9 root deliberately has no server default.

Every other table, column, type, nullability, server default, primary key,
foreign key, unique constraint, check constraint, and index must match the
f9-era model contract.  PostgreSQL physical column ordinal is intentionally not
part of that semantic contract: SQLAlchemy and this application address fields
by name, and no positional SQL dependency is supported.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import Column, Index, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql

from .init_schema import LEGACY_BASELINE_TABLE_NAMES
from .models import Base


LEGACY_F9_REVISION = "f9ff23b7b84d"

# These are the only pre-stamp deviations approved by the R5 reconciliation
# record.  They are checked by exact semantic shape, not merely ignored.
_APPROVED_EXTRA_COLUMNS: dict[tuple[str, str], tuple[str, bool]] = {
    ("trip", "archived_at"): ("timestamp with time zone", True),
    ("change_proposal", "extended_at"): ("timestamp with time zone", True),
}
_APPROVED_DEFAULT_OVERRIDES: dict[tuple[str, str], frozenset[str]] = {
    ("plan", "needs_refresh"): frozenset({"false", "false::boolean"}),
}
_POST_F9_MODEL_COLUMNS = frozenset(
    {
        ("plan", "revision"),
        ("trip", "planning_facts_revision"),
    }
)


class LegacySchemaIncompatible(RuntimeError):
    """Raised when a pre-Alembic database is not the approved f9 shape."""

    def __init__(self, differences: list[str]):
        self.differences = tuple(differences)
        super().__init__("Legacy f9 compatibility failed: " + "; ".join(differences))


@dataclass(frozen=True)
class LegacySchemaCompatibilityReport:
    """Safe structural evidence suitable for a migration-runner log."""

    baseline_revision: str
    approved_extra_columns: tuple[str, ...]
    approved_default_overrides: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline_revision": self.baseline_revision,
            "approved_extra_columns": list(self.approved_extra_columns),
            "approved_default_overrides": list(self.approved_default_overrides),
        }


def validate_legacy_f9_compatibility(
    bind: Connection | Engine,
) -> LegacySchemaCompatibilityReport:
    """Validate the approved legacy f9-equivalent schema without writing it."""

    inspector = inspect(bind)
    differences: list[str] = []
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(LEGACY_BASELINE_TABLE_NAMES)
    _compare_set(differences, "tables", expected_tables, actual_tables)

    # A stamp is a first-history transition only.  A pre-existing revision row
    # requires a separate diagnosis; silently replacing it is unsafe.
    if "alembic_version" in actual_tables:
        differences.append("alembic_version already exists")

    for table_name in sorted(expected_tables & actual_tables):
        table = Base.metadata.tables[table_name]
        _compare_columns(differences, inspector, table)
        _compare_primary_key(differences, inspector, table)
        _compare_foreign_keys(differences, inspector, table)
        _compare_unique_constraints(differences, inspector, table)
        _compare_check_constraints(differences, inspector, table)
        _compare_indexes(differences, inspector, table)

    if differences:
        raise LegacySchemaIncompatible(differences)

    return LegacySchemaCompatibilityReport(
        baseline_revision=LEGACY_F9_REVISION,
        approved_extra_columns=tuple(
            f"{table}.{column}" for table, column in sorted(_APPROVED_EXTRA_COLUMNS)
        ),
        approved_default_overrides=tuple(
            f"{table}.{column}" for table, column in sorted(_APPROVED_DEFAULT_OVERRIDES)
        ),
    )


def _compare_columns(differences: list[str], inspector: Any, table: Table) -> None:
    actual = {column["name"]: column for column in inspector.get_columns(table.name)}
    expected = {
        column.name: column
        for column in table.columns
        if (table.name, column.name) not in _POST_F9_MODEL_COLUMNS
    }
    allowed = {
        column
        for (allowed_table, column) in _APPROVED_EXTRA_COLUMNS
        if allowed_table == table.name
    }
    _compare_set(differences, f"{table.name} columns", set(expected) | allowed, set(actual))

    for column_name, expected_column in expected.items():
        actual_column = actual.get(column_name)
        if actual_column is None:
            continue
        if _type_name(actual_column["type"]) != _type_name(expected_column.type):
            differences.append(f"{table.name}.{column_name} type differs")
        if bool(actual_column["nullable"]) != bool(expected_column.nullable):
            differences.append(f"{table.name}.{column_name} nullability differs")
        _compare_server_default(differences, table.name, expected_column, actual_column)

    for column_name in allowed:
        actual_column = actual.get(column_name)
        if actual_column is None:
            continue
        expected_type, expected_nullable = _APPROVED_EXTRA_COLUMNS[(table.name, column_name)]
        if _type_name(actual_column["type"]) != expected_type or bool(
            actual_column["nullable"]
        ) != expected_nullable:
            differences.append(f"{table.name}.{column_name} is not the approved legacy shape")
        if _normalise_default(actual_column.get("default")) is not None:
            differences.append(f"{table.name}.{column_name} has an unapproved server default")


def _compare_server_default(
    differences: list[str], table_name: str, expected_column: Column[Any], actual_column: dict[str, Any]
) -> None:
    actual_default = _normalise_default(actual_column.get("default"))
    key = (table_name, expected_column.name)
    if key in _APPROVED_DEFAULT_OVERRIDES:
        if actual_default not in _APPROVED_DEFAULT_OVERRIDES[key]:
            differences.append(f"{table_name}.{expected_column.name} default is not approved")
        return
    expected_default = _normalise_default(
        None if expected_column.server_default is None else expected_column.server_default.arg
    )
    if actual_default != expected_default:
        differences.append(f"{table_name}.{expected_column.name} server default differs")


def _compare_primary_key(differences: list[str], inspector: Any, table: Table) -> None:
    actual = tuple(inspector.get_pk_constraint(table.name).get("constrained_columns") or ())
    expected = tuple(column.name for column in table.primary_key.columns)
    if actual != expected:
        differences.append(f"{table.name} primary key differs")


def _compare_foreign_keys(differences: list[str], inspector: Any, table: Table) -> None:
    actual = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
        )
        for item in inspector.get_foreign_keys(table.name)
    }
    expected = {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.elements[0].column.table.name,
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in table.foreign_key_constraints
    }
    if actual != expected:
        differences.append(f"{table.name} foreign keys differ")


def _compare_unique_constraints(differences: list[str], inspector: Any, table: Table) -> None:
    actual = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)}
    expected = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    if actual != expected:
        differences.append(f"{table.name} unique constraints differ")


def _compare_check_constraints(differences: list[str], inspector: Any, table: Table) -> None:
    actual = {_normalise_sql(item["sqltext"]) for item in inspector.get_check_constraints(table.name)}
    # f9 had no application-owned check constraints.  The current model has
    # post-f9 checks, so deriving this set from Base.metadata would be wrong.
    if actual:
        differences.append(f"{table.name} has unapproved check constraints")


def _compare_indexes(differences: list[str], inspector: Any, table: Table) -> None:
    def index_shape(name: str, columns: tuple[str, ...], unique: bool, where: object) -> tuple[object, ...]:
        return (name, columns, unique, _normalise_sql(where))

    actual = {
        index_shape(
            item["name"],
            tuple(item["column_names"]),
            bool(item["unique"]),
            item.get("dialect_options", {}).get("postgresql_where"),
        )
        for item in inspector.get_indexes(table.name)
        # PostgreSQL exposes backing indexes for UNIQUE constraints here.  They
        # are compared through ``get_unique_constraints`` instead of twice.
        if not item.get("duplicates_constraint")
    }
    expected = {
        index_shape(
            index.name,
            tuple(column.name for column in index.columns),
            bool(index.unique),
            index.dialect_options["postgresql"].get("where"),
        )
        for index in table.indexes
        if isinstance(index, Index)
    }
    if actual != expected:
        differences.append(f"{table.name} indexes differ")


def _compare_set(differences: list[str], label: str, expected: set[str], actual: set[str]) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        differences.append(f"{label} missing {','.join(missing)}")
    if extra:
        differences.append(f"{label} has unapproved {','.join(extra)}")


def _type_name(value: object) -> str:
    # PostgreSQL's Inspector and SQLAlchemy metadata differ only in casing for
    # the supported f9 types.  Keep widths such as VARCHAR(300) meaningful.
    compiler = postgresql.dialect()
    try:
        rendered = value.compile(dialect=compiler)  # type: ignore[union-attr]
    except AttributeError:
        rendered = str(value)
    normalised = re.sub(r"\s+", " ", rendered.strip().lower())
    # SQLAlchemy's generic ``Float`` compiles as FLOAT, while PostgreSQL's
    # catalog reports its no-precision representation as DOUBLE PRECISION.
    return "double precision" if normalised == "float" else normalised


def _normalise_default(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip().lower()
    if not rendered:
        return None
    return _normalise_sql(rendered)


def _normalise_sql(value: object) -> str | None:
    if value is None:
        return None
    rendered = re.sub(r"\s+", " ", str(value).strip().lower())
    # PostgreSQL expands partial-index predicates with harmless text casts;
    # model metadata intentionally keeps the readable equivalent expression.
    rendered = re.sub(r"::[a-z_]+", "", rendered)
    rendered = re.sub(r"\(([a-z_][a-z0-9_.]*)\)", r"\1", rendered)
    while rendered.startswith("(") and rendered.endswith(")"):
        rendered = rendered[1:-1].strip()
    return rendered


if __name__ == "__main__":
    from .session import engine

    report = validate_legacy_f9_compatibility(engine)
    print("LEGACY_F9_COMPATIBILITY=" + json.dumps(report.as_dict(), sort_keys=True))
