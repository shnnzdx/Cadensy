from __future__ import annotations

from copy import deepcopy

import pytest

from app.db import legacy_schema_compat as compatibility
from app.db.init_schema import LEGACY_BASELINE_TABLE_NAMES
from app.db.models import Base


class _F9Inspector:
    """A catalog-shaped f9 fixture with only the reviewed legacy drift."""

    def __init__(self) -> None:
        self.columns: dict[str, list[dict]] = {}
        for table_name in LEGACY_BASELINE_TABLE_NAMES:
            table = Base.metadata.tables[table_name]
            self.columns[table_name] = [
                {
                    "name": column.name,
                    "type": column.type,
                    "nullable": column.nullable,
                    "default": None
                    if column.server_default is None
                    else column.server_default.arg,
                }
                for column in table.columns
                if (table_name, column.name) not in compatibility._POST_F9_MODEL_COLUMNS
            ]

        self.columns["trip"].append(
            {"name": "archived_at", "type": "TIMESTAMP WITH TIME ZONE", "nullable": True, "default": None}
        )
        self.columns["change_proposal"].append(
            {"name": "extended_at", "type": "TIMESTAMP WITH TIME ZONE", "nullable": True, "default": None}
        )
        self._column("plan", "needs_refresh")["default"] = "false"

    def _column(self, table_name: str, column_name: str) -> dict:
        return next(column for column in self.columns[table_name] if column["name"] == column_name)

    def get_table_names(self) -> list[str]:
        return sorted(LEGACY_BASELINE_TABLE_NAMES)

    def get_columns(self, table_name: str) -> list[dict]:
        return deepcopy(self.columns[table_name])

    def get_pk_constraint(self, table_name: str) -> dict:
        table = Base.metadata.tables[table_name]
        return {"constrained_columns": [column.name for column in table.primary_key.columns]}

    def get_foreign_keys(self, table_name: str) -> list[dict]:
        table = Base.metadata.tables[table_name]
        return [
            {
                "constrained_columns": [element.parent.name for element in constraint.elements],
                "referred_table": constraint.elements[0].column.table.name,
                "referred_columns": [element.column.name for element in constraint.elements],
            }
            for constraint in table.foreign_key_constraints
        ]

    def get_unique_constraints(self, table_name: str) -> list[dict]:
        table = Base.metadata.tables[table_name]
        return [
            {"column_names": [column.name for column in constraint.columns]}
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ]

    def get_check_constraints(self, _table_name: str) -> list[dict]:
        return []

    def get_indexes(self, table_name: str) -> list[dict]:
        table = Base.metadata.tables[table_name]
        return [
            {
                "name": index.name,
                "unique": index.unique,
                "column_names": [column.name for column in index.columns],
                "dialect_options": {
                    "postgresql_where": index.dialect_options["postgresql"].get("where")
                },
            }
            for index in table.indexes
        ]


def _patch_inspector(monkeypatch: pytest.MonkeyPatch, inspector: _F9Inspector) -> None:
    monkeypatch.setattr(compatibility, "inspect", lambda _bind: inspector)


def test_validator_accepts_only_the_reviewed_legacy_f9_drift(monkeypatch):
    inspector = _F9Inspector()
    _patch_inspector(monkeypatch, inspector)

    report = compatibility.validate_legacy_f9_compatibility(object())

    assert report.baseline_revision == "f9ff23b7b84d"
    assert report.approved_extra_columns == (
        "change_proposal.extended_at",
        "trip.archived_at",
    )
    assert report.approved_default_overrides == ("plan.needs_refresh",)


def test_validator_rejects_an_unapproved_column(monkeypatch):
    inspector = _F9Inspector()
    inspector.columns["trip"].append(
        {"name": "unexpected", "type": "TEXT", "nullable": True, "default": None}
    )
    _patch_inspector(monkeypatch, inspector)

    with pytest.raises(compatibility.LegacySchemaIncompatible, match="unapproved unexpected"):
        compatibility.validate_legacy_f9_compatibility(object())


def test_validator_rejects_a_nonhistorical_needs_refresh_default(monkeypatch):
    inspector = _F9Inspector()
    inspector._column("plan", "needs_refresh")["default"] = "true"
    _patch_inspector(monkeypatch, inspector)

    with pytest.raises(compatibility.LegacySchemaIncompatible, match="default is not approved"):
        compatibility.validate_legacy_f9_compatibility(object())
