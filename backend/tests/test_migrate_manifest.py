"""The migrator's manifests must stay in step with the models.

``backend/scripts/migrate_case_files.py`` is the only schema-evolution tool in
the repo (there is no Alembic; startup runs ``create_all``, which never
ALTERs). Its manifests are NAMES ONLY — every type, nullability and default is
compiled from the live SQLAlchemy metadata — so the one way they can rot is a
name that stops existing, or a new table/column whose shape the ADD COLUMN /
CREATE TABLE paths cannot render.

This module locks the three properties the RDS window depends on
(docs/v3-groups-accounts-spec.md §3.1 and §7):

1. every manifest table and column of BOTH passes appears in the offline
   ``--dialect mysql`` plan — the reviewable plan is complete;
2. ``case_file.origin`` is emitted as ``ADD COLUMN ... NOT NULL DEFAULT 'new'``
   — without the injected literal SQLite refuses the ALTER and MySQL cannot
   backfill existing rows;
3. no new table declares a ``use_alter`` FK — ``CreateTable`` omits those and
   the create-table path never emits ``AddConstraint``, so such a constraint
   would silently vanish from a migrated database.

Everything here is offline: no database is contacted and no DDL is executed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql as mysql_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect

from app.db.base import Base
from scripts.migrate_case_files import (
    CASE_FILES_MANIFEST,
    GROUPS_ADDED_COLUMNS,
    GROUPS_MANIFEST,
    GROUPS_NEW_TABLES,
    GROUPS_WIDENED_COLUMNS,
    MANIFESTS,
    OFFLINE_PASSES,
    Plan,
    _literal_default,
    build_offline_plan,
    validate_manifest,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sql(plan: Plan) -> str:
    return "\n".join(sql for _, sql in plan.statements)


def _statements(plan: Plan, kind: str) -> list[str]:
    return [sql for k, sql in plan.statements if k == kind]


def _mentions_column(sql: str, column: str) -> bool:
    """Word-boundary match so ``period_start`` never matches ``period_start_at``."""
    return re.search(rf"\b{re.escape(column)}\b", sql) is not None


@pytest.fixture(scope="module")
def mysql_all_plan() -> Plan:
    return build_offline_plan(mysql_dialect.dialect(), passes="all")


@pytest.fixture(scope="module")
def mysql_groups_plan() -> Plan:
    return build_offline_plan(mysql_dialect.dialect(), passes="groups")


# ---------------------------------------------------------------------------
# The manifests themselves
# ---------------------------------------------------------------------------
def test_manifests_match_the_spec_exactly():
    """§3.1 lists these names verbatim; drifting from them is a spec change."""
    assert GROUPS_NEW_TABLES == ["account_group", "account_client"]
    assert GROUPS_ADDED_COLUMNS == {
        "client": ["account_group_id"],
        "case_file": [
            "account_group_id",
            "period_start",
            "period_end",
            "period_label",
            "origin",
            "origin_case_file_id",
        ],
        "endorsement": ["batch_key"],
        "sales_lead": ["account_group_id", "period_start", "period_end"],
    }
    # The groups pass adds no enum member longer than an existing one, so no
    # VARCHAR grows and there is nothing to MODIFY.
    assert GROUPS_WIDENED_COLUMNS == []


def test_both_passes_are_registered_in_order():
    assert [m.key for m in MANIFESTS] == ["case_files", "groups", "agent", "antecedentes"]
    assert set(OFFLINE_PASSES) == {"all", "groups", "agent", "antecedentes"}


def test_validate_manifest_is_clean_against_the_models():
    """Names only: any renamed/removed model attribute must fail loudly here."""
    assert validate_manifest() == []
    assert validate_manifest([GROUPS_MANIFEST]) == []
    assert validate_manifest([CASE_FILES_MANIFEST]) == []


def test_manifest_names_exist_in_metadata():
    tables = Base.metadata.tables
    for manifest in MANIFESTS:
        for name in manifest.new_tables:
            assert name in tables, f"[{manifest.key}] missing table {name}"
        for table_name, columns in manifest.added_columns.items():
            assert table_name in tables, f"[{manifest.key}] missing table {table_name}"
            for column in columns:
                assert column in tables[table_name].columns, (
                    f"[{manifest.key}] missing column {table_name}.{column}"
                )


def test_validate_manifest_reports_an_unknown_name():
    """The guard is real, not decorative."""
    from scripts.migrate_case_files import Manifest

    bogus = Manifest(
        key="bogus",
        new_tables=["not_a_table"],
        added_columns={"case_file": ["not_a_column"]},
    )
    errors = validate_manifest([bogus])
    assert any("not_a_table" in e for e in errors)
    assert any("not_a_column" in e for e in errors)


# ---------------------------------------------------------------------------
# CREATE TABLE path: no use_alter FKs on any new table
# ---------------------------------------------------------------------------
def test_no_new_table_declares_a_use_alter_foreign_key():
    """``CreateTable`` omits use_alter FKs and the plan never re-adds them."""
    for manifest in MANIFESTS:
        for name in manifest.new_tables:
            for constraint in Base.metadata.tables[name].foreign_key_constraints:
                assert not constraint.use_alter, (
                    f"[{manifest.key}] {name} declares a use_alter FK "
                    f"({constraint.name}); the create-table path would drop it"
                )


def test_added_columns_are_nullable_or_carry_a_scalar_default():
    """SQLite refuses ``ADD COLUMN ... NOT NULL`` without a DEFAULT."""
    for manifest in MANIFESTS:
        for table_name, columns in manifest.added_columns.items():
            for name in columns:
                column = Base.metadata.tables[table_name].columns[name]
                if column.nullable or column.server_default is not None:
                    continue
                assert _literal_default(column) is not None, (
                    f"[{manifest.key}] {table_name}.{name} is NOT NULL with no "
                    "renderable scalar default"
                )


# ---------------------------------------------------------------------------
# The offline MySQL plan — the reviewable RDS plan
# ---------------------------------------------------------------------------
def test_offline_mysql_plan_creates_every_manifest_table(mysql_all_plan):
    creates = _statements(mysql_all_plan, "create_table")
    for manifest in MANIFESTS:
        for name in manifest.new_tables:
            assert any(re.match(rf"CREATE TABLE {name}\b", sql) for sql in creates), (
                f"[{manifest.key}] no CREATE TABLE for {name} in the offline plan"
            )


def test_offline_mysql_plan_covers_every_manifest_column(mysql_all_plan):
    """A column is covered by its own ADD COLUMN or by the CREATE TABLE that
    already carries it (metadata-compiled), never by neither."""
    creates = {
        sql.split("(", 1)[0].replace("CREATE TABLE", "").strip(): sql
        for sql in _statements(mysql_all_plan, "create_table")
    }
    adds = _statements(mysql_all_plan, "add_column")
    for manifest in MANIFESTS:
        for table_name, columns in manifest.added_columns.items():
            for column in columns:
                in_create = table_name in creates and _mentions_column(
                    creates[table_name], column
                )
                in_add = any(
                    sql.startswith(f"ALTER TABLE {table_name} ADD COLUMN")
                    and _mentions_column(sql, column)
                    for sql in adds
                )
                assert in_create or in_add, (
                    f"[{manifest.key}] {table_name}.{column} appears nowhere in "
                    "the offline mysql plan"
                )


def test_groups_tables_appear_in_the_account_group_plan(mysql_all_plan):
    """The acceptance line: the printed plan mentions account_group."""
    sql = _sql(mysql_all_plan)
    assert "account_group" in sql
    assert "account_client" in sql
    assert "ix_account_group_broker_slug" in sql
    assert "ix_account_client_case_client" in sql
    assert "ix_case_file_group_period" in sql


def test_groups_only_plan_adds_every_group_column(mysql_groups_plan):
    """``--pass groups`` is the delta for a DB that already has case-files."""
    adds = _statements(mysql_groups_plan, "add_column")
    for table_name, columns in GROUPS_ADDED_COLUMNS.items():
        for column in columns:
            assert any(
                sql.startswith(f"ALTER TABLE {table_name} ADD COLUMN")
                and _mentions_column(sql, column)
                for sql in adds
            ), f"no ADD COLUMN for {table_name}.{column}"
    creates = _statements(mysql_groups_plan, "create_table")
    assert any(sql.startswith("CREATE TABLE account_group") for sql in creates)
    assert any(sql.startswith("CREATE TABLE account_client") for sql in creates)
    # No pre-existing VARCHAR grows in this pass.
    assert _statements(mysql_groups_plan, "modify_column") == []


def test_case_file_origin_add_column_carries_default_new(mysql_groups_plan):
    """Rule: ``origin`` is NOT NULL; the literal default is what makes the
    ALTER legal on SQLite and deterministic on MySQL."""
    add = [
        sql
        for sql in _statements(mysql_groups_plan, "add_column")
        if sql.startswith("ALTER TABLE case_file ADD COLUMN origin ")
    ]
    assert len(add) == 1, add
    assert "NOT NULL" in add[0]
    assert "DEFAULT 'new'" in add[0]


def test_case_file_origin_default_is_injected_on_sqlite_too():
    plan = build_offline_plan(sqlite_dialect.dialect(), passes="groups")
    add = [
        sql
        for sql in _statements(plan, "add_column")
        if sql.startswith("ALTER TABLE case_file ADD COLUMN origin ")
    ]
    assert len(add) == 1, add
    assert "DEFAULT 'new'" in add[0]
    # SQLite cannot ALTER-add FKs and does not MODIFY VARCHAR lengths.
    assert _statements(plan, "add_foreign_key") == []
    assert _statements(plan, "modify_column") == []


def test_groups_columns_are_not_added_twice_in_the_combined_plan(mysql_all_plan):
    """``case_file`` is CREATEd by the case-files pass, so the groups columns
    it carries must not also be ADDed (duplicate-column error on MySQL)."""
    adds = _statements(mysql_all_plan, "add_column")
    assert not [sql for sql in adds if sql.startswith("ALTER TABLE case_file ")]
    assert not [sql for sql in adds if sql.startswith("ALTER TABLE endorsement ")]
    assert not [sql for sql in adds if sql.startswith("ALTER TABLE sales_lead ")]
    # ``client`` predates both passes, so its column IS added.
    assert any(
        sql.startswith("ALTER TABLE client ADD COLUMN account_group_id") for sql in adds
    )


def test_offline_plan_rejects_an_unknown_pass():
    with pytest.raises(ValueError):
        build_offline_plan(mysql_dialect.dialect(), passes="nonexistent")


# ---------------------------------------------------------------------------
# The CLI, end to end (offline)
# ---------------------------------------------------------------------------
def test_cli_offline_mysql_plan_runs_and_mentions_account_group():
    result = subprocess.run(
        [sys.executable, "scripts/migrate_case_files.py", "--dialect", "mysql"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE account_group" in result.stdout
    assert "CREATE TABLE account_client" in result.stdout
    assert "no database contacted" in result.stdout


def test_cli_offline_groups_pass_shows_the_origin_default():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/migrate_case_files.py",
            "--dialect",
            "mysql",
            "--pass",
            "groups",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "ADD COLUMN origin VARCHAR" in result.stdout
    assert "DEFAULT 'new'" in result.stdout


def test_cli_refuses_offline_apply():
    result = subprocess.run(
        [sys.executable, "scripts/migrate_case_files.py", "--dialect", "mysql", "--apply"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2
    assert "offline" in result.stdout
