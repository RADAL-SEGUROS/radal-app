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
3. every ``use_alter`` FK on a new table is NAMED and re-emitted by the plan as
   a deferred ``ADD CONSTRAINT`` — MySQL's ``CreateTable`` omits those (v12's
   ``payment.evidence_id`` is the first one), so without the deferred statement
   the constraint would silently vanish from a migrated database.

The v12 pass (docs/v12/01-data-model.md §10) adds a REHEARSAL on top: a real
SQLite file is built with the PRE-v12 schema (a metadata copy that omits the v12
tables and columns), the online planner migrates it, and the result must show
zero drift. Everything else is offline; the rehearsal touches only a temp file.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Index, MetaData, Table, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects import mysql as mysql_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import Session

from app.db.base import Base
from scripts.migrate_case_files import (
    CASE_FILES_MANIFEST,
    GROUPS_ADDED_COLUMNS,
    GROUPS_MANIFEST,
    GROUPS_NEW_TABLES,
    GROUPS_WIDENED_COLUMNS,
    MANIFESTS,
    OFFLINE_PASSES,
    V12_ADDED_COLUMNS,
    V12_MANIFEST,
    V12_NEW_TABLES,
    V12_WIDENED_COLUMNS,
    Plan,
    _literal_default,
    apply_plan,
    build_offline_plan,
    build_online_plan,
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
    assert [m.key for m in MANIFESTS] == [
        "case_files",
        "groups",
        "agent",
        "antecedentes",
        "repurpose",
        "v12",
    ]
    assert set(OFFLINE_PASSES) == {
        "all", "groups", "agent", "antecedentes", "repurpose", "v12",
    }


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
# CREATE TABLE path: use_alter FKs on new tables are deferred, never dropped
# ---------------------------------------------------------------------------
def _use_alter_fks_of_new_tables(manifests) -> list[tuple[str, str]]:
    return [
        (name, constraint.name)
        for manifest in manifests
        for name in manifest.new_tables
        for constraint in Base.metadata.tables[name].foreign_key_constraints
        if constraint.use_alter
    ]


def test_every_use_alter_fk_on_a_new_table_is_named():
    """The deferred ADD CONSTRAINT must be reviewable and re-runnable by name."""
    for table_name, name in _use_alter_fks_of_new_tables(MANIFESTS):
        assert name, f"{table_name} declares an unnamed use_alter FK"
    # v12 introduced the first one; the guard must actually be exercised.
    assert ("payment", "fk_payment_evidence") in _use_alter_fks_of_new_tables([V12_MANIFEST])


@pytest.mark.parametrize("passes", ["all", "v12"])
def test_use_alter_fks_of_new_tables_are_re_added_on_mysql(passes):
    """MySQL's CREATE TABLE omits them; the plan must ADD CONSTRAINT after it."""
    plan = build_offline_plan(mysql_dialect.dialect(), passes=passes)
    selected = MANIFESTS if passes == "all" else [V12_MANIFEST]
    fks = _statements(plan, "add_foreign_key")
    creates = _statements(plan, "create_table")
    last_create = max(i for i, (k, _) in enumerate(plan.statements) if k == "create_table")
    for table_name, name in _use_alter_fks_of_new_tables(selected):
        create = next(sql for sql in creates if re.match(rf"CREATE TABLE {table_name}\b", sql))
        assert name not in create, f"{name} unexpectedly inline in CREATE TABLE {table_name}"
        matches = [
            i for i, (k, sql) in enumerate(plan.statements)
            if k == "add_foreign_key" and f"ADD CONSTRAINT {name} " in sql
        ]
        assert len(matches) == 1, (name, fks)
        # Deferred: both ends of the cycle exist by then.
        assert matches[0] > last_create


def test_sqlite_inlines_use_alter_fks_and_defers_nothing():
    plan = build_offline_plan(sqlite_dialect.dialect(), passes="v12")
    create = next(
        sql for sql in _statements(plan, "create_table") if sql.startswith("CREATE TABLE payment ")
    )
    assert "fk_payment_evidence" in create
    assert _statements(plan, "add_foreign_key") == []


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


# ---------------------------------------------------------------------------
# v12 (docs/v12/01-data-model.md §10)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mysql_v12_plan() -> Plan:
    return build_offline_plan(mysql_dialect.dialect(), passes="v12")


def test_v12_manifest_names_exist_and_validate():
    assert validate_manifest([V12_MANIFEST]) == []
    tables = Base.metadata.tables
    assert len(V12_NEW_TABLES) == len(set(V12_NEW_TABLES)) == 27
    for name in V12_NEW_TABLES:
        assert name in tables, name
    for table_name, columns in V12_ADDED_COLUMNS.items():
        assert table_name in tables, table_name
        assert table_name not in V12_NEW_TABLES, table_name
        for column in columns:
            assert column in tables[table_name].columns, f"{table_name}.{column}"
    # No pre-existing enum VARCHAR grows in v12.
    assert V12_WIDENED_COLUMNS == []


def test_v12_manifest_covers_every_table_added_since_the_previous_passes():
    """A table in the metadata that no manifest creates would never reach RDS."""
    created = {name for manifest in MANIFESTS for name in manifest.new_tables}
    # Tables that predate the first manifest (the v2 baseline on RDS).
    baseline = set(Base.metadata.tables) - created
    for name in V12_NEW_TABLES:
        assert name not in baseline
    assert not (baseline & set(V12_NEW_TABLES))


def test_v12_added_columns_are_nullable_or_server_defaulted():
    """Contract rule: every ALTERed column is nullable OR carries a server_default."""
    for table_name, columns in V12_ADDED_COLUMNS.items():
        for name in columns:
            column = Base.metadata.tables[table_name].columns[name]
            assert column.nullable or column.server_default is not None, f"{table_name}.{name}"


def test_v12_enum_additions_do_not_widen_existing_columns():
    from app.models.document import DocumentCategory
    from app.models.enums import EntityType

    assert max(len(m.value) for m in EntityType) == len("inspection_request")
    assert max(len(m.value) for m in DocumentCategory) == len("conditional_pronouncement")
    for value in ("payment", "installment", "share_link", "status_inquiry", "mail_account",
                  "portal_search", "insurance_subtype", "payment_intent"):
        assert EntityType(value)
    for value in ("payment_receipt", "email_message", "cancellation_letter", "payment_consent",
                  "portal_screenshot", "insurer_statement_upload"):
        assert DocumentCategory(value)


def test_v12_offline_plan_creates_adds_and_never_modifies(mysql_v12_plan):
    creates = _statements(mysql_v12_plan, "create_table")
    for name in V12_NEW_TABLES:
        assert any(re.match(rf"CREATE TABLE {name}\b", sql) for sql in creates), name
    assert len(creates) == len(V12_NEW_TABLES)
    adds = _statements(mysql_v12_plan, "add_column")
    for table_name, columns in V12_ADDED_COLUMNS.items():
        for column in columns:
            assert any(
                sql.startswith(f"ALTER TABLE {table_name} ADD COLUMN {column} ") for sql in adds
            ), f"{table_name}.{column}"
    assert _statements(mysql_v12_plan, "modify_column") == []
    # The NOT NULL additions carry their backfill literal.
    by_column = {sql.split(" ADD COLUMN ")[1].split(" ")[0] + "@" + sql.split()[2]: sql
                 for sql in adds}
    assert "DEFAULT 'UF'" in by_column["currency@policy"]
    assert "DEFAULT 'UF'" in by_column["currency@collection_installment"]
    assert "DEFAULT 'auto'" in by_column["level_preference@collection_plan"]
    assert "DEFAULT false" in by_column["is_skipped@case_file_stage_event"]
    assert "DEFAULT '0'" in by_column["sort_order@insurance_line"]
    # The v3 origin axis is untouched; the team origen is a separate column.
    assert "origin@case_file" not in by_column
    assert "entry_origin@case_file" in by_column


def test_cli_offline_v12_pass_prints_a_plan():
    result = subprocess.run(
        [sys.executable, "scripts/migrate_case_files.py", "--dialect", "mysql", "--pass", "v12"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "pass: v12" in result.stdout
    assert "CREATE TABLE payment " in result.stdout
    assert "ADD CONSTRAINT fk_payment_evidence" in result.stdout
    assert "ADD CONSTRAINT fk_collection_plan_mandate" in result.stdout


# --- The rehearsal: a pre-v12 SQLite database migrated to zero drift ----------
def _pre_v12_metadata() -> MetaData:
    """A copy of the live metadata WITHOUT the v12 tables and v12 columns.

    Built column by column (``Column._copy()``) because SQLite cannot portably
    DROP an indexed / FK column after a full ``create_all``. Column-flag
    indexes and uniques (``index=True`` / ``unique=True``) are re-created by the
    copied columns themselves; only table-level ones are copied by hand.
    """
    pre = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in V12_NEW_TABLES:
            continue
        dropped = set(V12_ADDED_COLUMNS.get(table.name, ()))
        columns = [column._copy() for column in table.columns if column.name not in dropped]
        copy = Table(table.name, pre, *columns)
        for index in table.indexes:
            names = [c.name for c in index.columns]
            if getattr(index, "_column_flag", False) or set(names) & dropped:
                continue
            Index(index.name, *[copy.c[n] for n in names], unique=index.unique)
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint) or getattr(
                constraint, "_column_flag", False
            ):
                continue
            names = [c.name for c in constraint.columns]
            if set(names) & dropped:
                continue
            copy.append_constraint(UniqueConstraint(*names, name=constraint.name))
    return pre


def test_rehearsal_pre_v12_sqlite_database_migrates_to_zero_drift(tmp_path, capsys):
    engine = create_engine(f"sqlite:///{tmp_path / 'pre_v12.db'}", future=True)
    try:
        _pre_v12_metadata().create_all(engine)
        names = set(inspect(engine).get_table_names())
        assert not names & set(V12_NEW_TABLES)
        assert "code" not in {c["name"] for c in inspect(engine).get_columns("insurance_line")}

        # A POPULATED pre-existing table: the NOT NULL additions must backfill.
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO insurance_line (name, requires_inspection, is_active, "
                "created_at, updated_at) VALUES ('Incendio', 0, 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))

        plan = build_online_plan(engine)
        assert plan.has_drift
        # Only the v12 pass is pending — every earlier pass is in the copy.
        assert plan.pending_passes() == ["v12"]
        assert len([k for k, _ in plan.statements if k == "create_table"]) == len(V12_NEW_TABLES)
        assert len([k for k, _ in plan.statements if k == "add_column"]) == sum(
            len(cols) for cols in V12_ADDED_COLUMNS.values()
        )

        apply_plan(engine, plan)
        verify = build_online_plan(engine)
        assert not verify.has_drift, [sql for _, sql in verify.statements]
        assert set(V12_NEW_TABLES) <= set(inspect(engine).get_table_names())

        with engine.connect() as conn:
            row = conn.execute(text("SELECT code, sort_order FROM insurance_line")).one()
        assert row.code is None and row.sort_order == 0

        # The data step runs on the migrated database, and is idempotent.
        from app.db.seed_v12 import run

        with Session(engine) as session:
            first = run(session)
            session.commit()
            second = run(session)
            session.commit()
        assert first["lines_created"] == 18 and first["subtypes_created"] == 54
        assert sum(second.values()) == 0
    finally:
        engine.dispose()
    capsys.readouterr()
