"""Schema migration for the case-files branch — additive, idempotent, dry-run first.

Why this exists
---------------
The app has no Alembic. Startup runs ``Base.metadata.create_all()`` only, which
creates MISSING TABLES but never ALTERs existing ones. The case-files branch
adds ~9 new tables (create_all handles those) plus ~38 additive columns on 9
pre-existing tables and widens two enum-backed VARCHARs
(``document.category`` 29->37, ``extraction.kind`` 22->25). Deploying against a
database created before this branch therefore 500s on nearly every query until
the missing columns are added. This script closes that gap.

The GROUPS & ACCOUNTS pass (docs/v3-groups-accounts-spec.md §3.1) rides on the
same tool: 2 new tables (``account_group``, ``account_client``) and 11 additive
columns on ``client``, ``case_file``, ``endorsement`` and ``sales_lead`` — no
widened columns. Both passes are applied in the SAME RDS window: dry-run,
``--apply``, zero-drift, before any push to ``dev``.

Design
------
* The authoritative expected schema is the LIVE SQLALCHEMY METADATA
  (``app.db.base.Base``). Every column spec in every emitted statement is
  compiled from the model ``Column`` with the target dialect — nothing is
  hand-written, so the script cannot drift from the models.
* Introspection goes through ``sqlalchemy.inspect()``, which reads
  ``information_schema`` on MySQL and ``PRAGMA table_info`` on SQLite.
* Emits, in dependency order:
    1. ``CREATE TABLE`` (+ its indexes) for expected tables missing entirely
       (what create_all would do — included so one run fully migrates RDS),
    2. ``ALTER TABLE ... ADD COLUMN`` for missing columns,
    3. ``ALTER TABLE ... MODIFY COLUMN`` (MySQL only) where
       ``information_schema`` shows a narrower VARCHAR than the models expect,
    4. ``CREATE INDEX`` for expected indexes missing by name,
    5. ``ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`` (MySQL only) for
       missing FKs on the columns being added (SQLite cannot ALTER-add FKs).

Modes
-----
* Default (dry run): print the plan; exit 1 if drift exists, 0 if clean.
  Usable as a CI check.
* ``--apply``: execute the plan inside a transaction where the dialect allows
  (SQLite DDL is transactional; MySQL commits each DDL statement implicitly),
  then re-introspect and verify zero drift.
* ``--dialect mysql`` (offline): print the full statement plan (case-files AND
  groups passes) compiled for that dialect WITHOUT connecting to any database.
  This is the reviewable RDS plan; it uses the manifests below because with no
  live DB there is nothing to introspect. ``--pass groups`` narrows it to the
  groups delta for a database that already carries the case-files schema.

Usage (from backend/, venv active)
----------------------------------
    python -m scripts.migrate_case_files                    # dry run vs DATABASE_URL
    python -m scripts.migrate_case_files --apply            # migrate DATABASE_URL
    python -m scripts.migrate_case_files --url mysql+pymysql://user:pw@host/radal --apply
    python -m scripts.migrate_case_files --dialect mysql    # offline MySQL plan (both passes)
    python -m scripts.migrate_case_files --dialect mysql --pass groups   # groups delta only

No secrets are ever printed: URLs are rendered with the password hidden.
"""
from __future__ import annotations

import argparse
import enum as py_enum
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.schema import AddConstraint, Column, CreateColumn, CreateIndex, CreateTable, Table

sys.path.insert(0, ".")  # allow `python -m scripts.migrate_case_files` from backend/

from app.db.base import Base  # noqa: E402  (imports every model + the mysql VARCHAR hook)

# ---------------------------------------------------------------------------
# Case-files manifest — NAMES ONLY. Types/nullability/defaults always come from
# the live metadata; these lists only scope the OFFLINE plan (--dialect) where
# there is no database to introspect. validate_manifest() fails loudly if any
# name stops existing in the models.
# ---------------------------------------------------------------------------
CASE_FILES_NEW_TABLES = [
    "sales_lead",
    "case_file",
    "case_file_stage_event",
    "case_pack",
    "endorsement",
    "collection_plan",
    "collection_installment",
    "warranty",
    "claim_item",
]

CASE_FILES_ADDED_COLUMNS: dict[str, list[str]] = {
    "claim": [
        "case_file_id", "occurred_at", "reported_at", "notice_deadline_days",
        "adjuster_name", "adjuster_registry", "coverage_ruling", "deductible_uf",
        "loss_ratio_pct",
    ],
    "document": ["case_file_id", "section", "document_code"],
    "extraction": ["case_file_id", "category"],
    "inspection": ["case_file_id"],
    "note": ["follow_up_on"],
    "placement": ["case_file_id"],
    "policy": [
        "case_file_id", "source_document_id", "renews_policy_id",
        "period_start_at", "period_end_at", "cover_mode", "cmf_policy_code",
        "insured_amount_semantics", "aggregate_limit_uf", "average_rate_permille",
        "indemnity_limit",
    ],
    "proposal": [
        "case_file_id", "ai_summary", "ai_summary_model",
        "ai_summary_prompt_version", "is_summary_confirmed", "quotation_number",
        "cover_mode", "outcome",
    ],
    "quote_request": ["case_file_id", "round_no"],
}

# Pre-existing enum-backed VARCHARs whose computed length grew on this branch.
CASE_FILES_WIDENED_COLUMNS: list[tuple[str, str]] = [
    ("document", "category"),   # 29 -> 37 (DocumentCategory gained 26 members)
    ("extraction", "kind"),     # 22 -> 25 (ExtractionKind gained case_document)
]

# ---------------------------------------------------------------------------
# Groups & accounts manifest (docs/v3-groups-accounts-spec.md §3.1) — the
# broker-private Group above the expediente and the account folder's period /
# origin axes. Every added column is nullable or carries a scalar default
# (``case_file.origin`` -> DEFAULT 'new' via the inject_default path), so
# ADD COLUMN works on MySQL and SQLite alike. No widened columns: every enum
# member added by the pass is shorter than the longest existing one.
# ---------------------------------------------------------------------------
GROUPS_NEW_TABLES = [
    "account_group",
    "account_client",
]

GROUPS_ADDED_COLUMNS: dict[str, list[str]] = {
    "client": ["account_group_id"],
    "case_file": [
        "account_group_id", "period_start", "period_end", "period_label",
        "origin", "origin_case_file_id",
    ],
    "endorsement": ["batch_key"],
    "sales_lead": ["account_group_id", "period_start", "period_end"],
}

GROUPS_WIDENED_COLUMNS: list[tuple[str, str]] = []

# ---------------------------------------------------------------------------
# Agent manifest (docs/v4-agent-spec.md §3) — the pending-action table and the
# context_refs provenance column on agent_message. ``agent_action`` is a new
# table (create_all handles it on boot); ``agent_message.context_refs`` is an
# additive NULLable JSON column on an existing table and therefore needs this
# script on the dev RDS before the branch is pushed (rule 10). No widened
# columns: ``AgentActionStatus``'s longest member (``discarded``) computes to
# VARCHAR(21), a brand-new column.
# ---------------------------------------------------------------------------
AGENT_NEW_TABLES = [
    "agent_action",
]

AGENT_ADDED_COLUMNS: dict[str, list[str]] = {
    "agent_message": ["context_refs"],
}

AGENT_WIDENED_COLUMNS: list[tuple[str, str]] = []

# ---------------------------------------------------------------------------
# Antecedentes manifest (docs/v6-antecedentes-expediente-spec.md §A) — the
# per-ramo antecedentes schema and the registered expediente instance. Both are
# brand-new tables (create_all handles them on boot); no enum-backed VARCHAR
# grows (DocumentCategory's ``antecedentes_pack`` at 17 chars is well under the
# existing 25-char longest), so there are no MODIFY statements and no widened
# columns.
#
# v7 (docs/v7-lines-and-bases-tecnicas-spec.md §A) rides on the SAME manifest:
# it adds two additive NULLable-or-defaulted columns to PRE-EXISTING tables —
# ``case_file.line_record_schema_id`` (the line assigned to an account, FK SET
# NULL) and ``line_record_schema.is_template`` (NOT NULL DEFAULT False; the
# migrate ADD-COLUMN path injects the ``DEFAULT 0`` SQLite needs). On a database
# that already carries the v6 tables these are the only two statements this pass
# emits; on a fresh boot ``create_all`` folds them into the CREATE TABLEs. v7
# also RELAXED ``line_record_schema``'s unique constraint (dropped the old
# ``(broker_id, insurance_line_id, version)`` unique; added ``(broker_id,
# name)``) — this script never drops constraints, so on the dev RDS the stale
# unique is dropped by hand alongside the --apply (documented in deployment.md).
# ---------------------------------------------------------------------------
ANTECEDENTES_NEW_TABLES = [
    "line_record_schema",
    "record_expediente",
]

ANTECEDENTES_ADDED_COLUMNS: dict[str, list[str]] = {
    "case_file": ["line_record_schema_id"],
    "line_record_schema": ["is_template"],
}

ANTECEDENTES_WIDENED_COLUMNS: list[tuple[str, str]] = []


@dataclass(frozen=True)
class Manifest:
    """One additive schema pass: names only, types always from the metadata."""

    key: str
    new_tables: list[str]
    added_columns: dict[str, list[str]]
    widened_columns: list[tuple[str, str]] = field(default_factory=list)

    def touches_table(self, table_name: str) -> bool:
        return table_name in self.new_tables or table_name in self.added_columns

    def touches_column(self, table_name: str, column_name: str) -> bool:
        if table_name in self.new_tables:
            return True
        return column_name in self.added_columns.get(table_name, ())


CASE_FILES_MANIFEST = Manifest(
    key="case_files",
    new_tables=CASE_FILES_NEW_TABLES,
    added_columns=CASE_FILES_ADDED_COLUMNS,
    widened_columns=CASE_FILES_WIDENED_COLUMNS,
)
GROUPS_MANIFEST = Manifest(
    key="groups",
    new_tables=GROUPS_NEW_TABLES,
    added_columns=GROUPS_ADDED_COLUMNS,
    widened_columns=GROUPS_WIDENED_COLUMNS,
)
AGENT_MANIFEST = Manifest(
    key="agent",
    new_tables=AGENT_NEW_TABLES,
    added_columns=AGENT_ADDED_COLUMNS,
    widened_columns=AGENT_WIDENED_COLUMNS,
)
ANTECEDENTES_MANIFEST = Manifest(
    key="antecedentes",
    new_tables=ANTECEDENTES_NEW_TABLES,
    added_columns=ANTECEDENTES_ADDED_COLUMNS,
    widened_columns=ANTECEDENTES_WIDENED_COLUMNS,
)

# In application order. A later pass may add columns to a table an earlier
# pass creates (``case_file`` is created by case_files and extended by groups):
# because every CREATE TABLE is compiled from the LIVE metadata it already
# carries the later columns, and the offline plan de-duplicates accordingly.
MANIFESTS: list[Manifest] = [
    CASE_FILES_MANIFEST,
    GROUPS_MANIFEST,
    AGENT_MANIFEST,
    ANTECEDENTES_MANIFEST,
]

# Offline plans that make sense on their own. ``case_files`` alone is NOT one
# of them any more: its CREATE TABLE case_file now references account_group,
# so the case-files tables cannot be created without the groups tables. The
# agent pass only touches pre-existing chat tables and its own new table, so
# it stands alone for a database already carrying the earlier passes.
OFFLINE_PASSES = ("all", "groups", "agent", "antecedentes")


class Plan:
    """Ordered migration statements + informational notes."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, str]] = []  # (kind, sql)
        self.notes: list[str] = []
        # (kind, table, column-or-None) per statement — lets the summary say
        # which manifest pass each pending statement belongs to.
        self.targets: list[tuple[str, str, str | None]] = []

    def add(self, kind: str, sql: str, *, table: str = "", column: str | None = None) -> None:
        self.statements.append((kind, sql))
        self.targets.append((kind, table, column))

    def pending_passes(self, manifests: list[Manifest] | None = None) -> list[str]:
        """Keys of the manifest passes with at least one pending statement."""
        pending = []
        for manifest in manifests or MANIFESTS:
            for kind, table, column in self.targets:
                if kind == "create_table" and table in manifest.new_tables:
                    pending.append(manifest.key)
                    break
                if column is not None and manifest.touches_column(table, column):
                    pending.append(manifest.key)
                    break
        return pending

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def has_drift(self) -> bool:
        return bool(self.statements)


# ---------------------------------------------------------------------------
# DDL rendering — everything compiled from metadata with the target dialect.
# ---------------------------------------------------------------------------
def _literal_default(column: Column) -> str | None:
    """SQL literal for a NOT NULL column whose default is Python-side only.

    SQLite refuses ``ADD COLUMN ... NOT NULL`` without a DEFAULT, and being
    explicit also backfills existing MySQL rows deterministically. Only scalar
    defaults are rendered; anything else returns None (caller emits NULL-able
    columns untouched).
    """
    if column.server_default is not None or column.default is None:
        return None
    if not column.default.is_scalar:
        return None
    value = column.default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, py_enum.Enum):
        value = str(value.value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _column_spec(column: Column, dialect: Dialect, *, inject_default: bool = False) -> str:
    """Compile ``name TYPE [NOT NULL] [DEFAULT ...]`` from the model column.

    ``inject_default`` is used for ADD COLUMN only: a NOT NULL column with a
    Python-side default needs a literal DEFAULT so SQLite accepts the ALTER and
    MySQL backfills existing rows deterministically. MODIFY never injects one —
    existing rows already hold values and a fresh create_all schema carries no
    server default for these columns.
    """
    spec = str(CreateColumn(column).compile(dialect=dialect)).strip()
    if inject_default and not column.nullable and "DEFAULT" not in spec:
        literal = _literal_default(column)
        if literal is not None:
            spec += f" DEFAULT {literal}"
    return spec


def _format_table(table: Table, dialect: Dialect) -> str:
    return dialect.identifier_preparer.format_table(table)


def _add_column_sql(table: Table, column: Column, dialect: Dialect) -> str:
    return (
        f"ALTER TABLE {_format_table(table, dialect)} "
        f"ADD COLUMN {_column_spec(column, dialect, inject_default=True)}"
    )


def _modify_column_sql(table: Table, column: Column, dialect: Dialect) -> str:
    return f"ALTER TABLE {_format_table(table, dialect)} MODIFY COLUMN {_column_spec(column, dialect)}"


def _create_table_sql(table: Table, dialect: Dialect) -> str:
    return str(CreateTable(table).compile(dialect=dialect)).strip()


def _index_sqls(table: Table, dialect: Dialect, only_missing: set[str] | None = None) -> list[tuple[str, str]]:
    out = []
    for index in sorted(table.indexes, key=lambda i: i.name or ""):
        if only_missing is not None and index.name not in only_missing:
            continue
        out.append((index.name or "", str(CreateIndex(index).compile(dialect=dialect)).strip()))
    return out


def _fk_constraints_for_columns(table: Table, column_names: set[str]) -> list:
    """FK constraints whose constrained columns are all in ``column_names``."""
    found = []
    for constraint in table.foreign_key_constraints:
        constrained = {c.name for c in constraint.columns}
        if constrained and constrained <= column_names:
            found.append(constraint)
    return found


# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------
def build_online_plan(engine: Engine) -> Plan:
    """Diff live database (information_schema / PRAGMA) against metadata."""
    plan = Plan()
    dialect = engine.dialect
    is_mysql = dialect.name in ("mysql", "mariadb")
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in db_tables:
            plan.add("create_table", _create_table_sql(table, dialect), table=table.name)
            for _, sql in _index_sqls(table, dialect):
                plan.add("create_index", sql, table=table.name)
            continue

        db_columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        added_here: set[str] = set()

        for column in table.columns:
            if column.name not in db_columns:
                plan.add(
                    "add_column", _add_column_sql(table, column, dialect),
                    table=table.name, column=column.name,
                )
                added_here.add(column.name)
                continue
            expected_len = getattr(column.type, "length", None)
            actual_len = getattr(db_columns[column.name]["type"], "length", None)
            if expected_len and actual_len and actual_len < expected_len:
                if is_mysql:
                    plan.add(
                        "modify_column", _modify_column_sql(table, column, dialect),
                        table=table.name, column=column.name,
                    )
                else:
                    plan.note(
                        f"{table.name}.{column.name}: declared VARCHAR({actual_len}) < "
                        f"expected VARCHAR({expected_len}) — SQLite does not enforce "
                        "VARCHAR lengths and cannot MODIFY; no action needed."
                    )
            elif expected_len and actual_len and actual_len > expected_len:
                plan.note(
                    f"{table.name}.{column.name}: database VARCHAR({actual_len}) is wider "
                    f"than the model's VARCHAR({expected_len}); never narrowed — no action."
                )

        extra = set(db_columns) - {c.name for c in table.columns}
        if extra:
            plan.note(
                f"{table.name}: columns in DB but not in models (left untouched): "
                + ", ".join(sorted(extra))
            )

        # Indexes missing by name (create_all names are deterministic: ix_*).
        db_index_names = {i["name"] for i in inspector.get_indexes(table.name)}
        missing_indexes = {
            index.name for index in table.indexes if index.name and index.name not in db_index_names
        }
        for _, sql in _index_sqls(table, dialect, only_missing=missing_indexes):
            plan.add("create_index", sql, table=table.name)

        # FK constraints for the columns being added (MySQL only — SQLite
        # cannot add an FK via ALTER TABLE; it also does not enforce FKs by
        # default, so this is a MySQL-integrity concern only).
        if added_here:
            if is_mysql:
                existing_fks = {
                    (tuple(fk["constrained_columns"]), fk["referred_table"])
                    for fk in inspector.get_foreign_keys(table.name)
                }
                for constraint in _fk_constraints_for_columns(table, added_here):
                    key = (
                        tuple(c.name for c in constraint.columns),
                        list(constraint.elements)[0].column.table.name,
                    )
                    if key not in existing_fks:
                        plan.add(
                            "add_foreign_key",
                            str(AddConstraint(constraint).compile(dialect=dialect)).strip(),
                            table=table.name,
                        )
            elif _fk_constraints_for_columns(table, added_here):
                plan.note(
                    f"{table.name}: FK constraints on added columns "
                    f"({', '.join(sorted(added_here))}) skipped — SQLite cannot "
                    "ALTER-add foreign keys (and does not enforce them by default)."
                )

    # The live diff is metadata-driven, so it covers every pass by construction;
    # the manifests only label what is still pending so the operator can tell
    # "case-files never ran" from "only the groups delta is missing".
    pending = plan.pending_passes()
    if pending:
        plan.note("pending manifest pass(es): " + ", ".join(pending))
    return plan


def validate_manifest(manifests: list[Manifest] | None = None) -> list[str]:
    """Every manifest name must still exist in the models. Returns errors.

    Beyond existence it checks the two properties the ADD COLUMN / CREATE TABLE
    paths rely on: an added NOT NULL column must carry a renderable scalar
    default (SQLite refuses the ALTER otherwise), and no new table may declare
    a ``use_alter`` FK (``CreateTable`` omits those and the create-table path
    never emits ``AddConstraint``, so the constraint would silently vanish).
    """
    errors = []
    tables = Base.metadata.tables
    for manifest in manifests or MANIFESTS:
        prefix = f"[{manifest.key}]"
        for name in manifest.new_tables:
            if name not in tables:
                errors.append(f"{prefix} manifest table not in metadata: {name}")
                continue
            for constraint in tables[name].foreign_key_constraints:
                if constraint.use_alter:
                    errors.append(
                        f"{prefix} new table {name} declares a use_alter FK "
                        f"({constraint.name or ', '.join(c.name for c in constraint.columns)}); "
                        "the create-table path would drop it"
                    )
        for table_name, columns in manifest.added_columns.items():
            if table_name not in tables:
                errors.append(f"{prefix} manifest table not in metadata: {table_name}")
                continue
            for column_name in columns:
                if column_name not in tables[table_name].columns:
                    errors.append(
                        f"{prefix} manifest column not in metadata: {table_name}.{column_name}"
                    )
                    continue
                column = tables[table_name].columns[column_name]
                if (
                    not column.nullable
                    and column.server_default is None
                    and _literal_default(column) is None
                ):
                    errors.append(
                        f"{prefix} added column {table_name}.{column_name} is NOT NULL "
                        "without a scalar default; ADD COLUMN would fail on SQLite"
                    )
        for table_name, column_name in manifest.widened_columns:
            if table_name not in tables or column_name not in tables[table_name].columns:
                errors.append(
                    f"{prefix} manifest widened column not in metadata: {table_name}.{column_name}"
                )
    return errors


def build_offline_plan(dialect: Dialect, passes: str = "all") -> Plan:
    """Full plan from the manifests — no database connection.

    ``passes="all"`` (default) is the plan for a database that predates BOTH
    passes (the pending RDS state): every new table of every manifest in
    dependency order, then the added columns, then the widenings. A column
    whose table is CREATED earlier in the same plan is not ADDed again — the
    CREATE TABLE compiled from the live metadata already carries it, and a
    second ADD COLUMN would fail with a duplicate-column error.

    ``passes="groups"`` is the delta for a database that already carries the
    case-files schema: it emits the ADD COLUMN statements the combined plan
    folds into CREATE TABLE (``case_file.origin ... DEFAULT 'new'`` included).
    """
    if passes not in OFFLINE_PASSES:
        raise ValueError(f"unknown offline pass {passes!r}; choose one of {OFFLINE_PASSES}")
    selected = MANIFESTS if passes == "all" else [m for m in MANIFESTS if m.key == passes]

    plan = Plan()
    is_mysql = dialect.name in ("mysql", "mariadb")
    tables = Base.metadata.tables

    new_table_names = {name for manifest in selected for name in manifest.new_tables}
    for table in Base.metadata.sorted_tables:
        if table.name not in new_table_names:
            continue
        plan.add("create_table", _create_table_sql(table, dialect), table=table.name)
        for _, sql in _index_sqls(table, dialect):
            plan.add("create_index", sql, table=table.name)

    added_by_table: dict[str, list[str]] = {}
    for manifest in selected:
        for table_name, columns in manifest.added_columns.items():
            if table_name in new_table_names:
                plan.note(
                    f"[{manifest.key}] {table_name}.{', '.join(columns)}: already part of "
                    f"CREATE TABLE {table_name} above — no ADD COLUMN needed."
                )
                continue
            bucket = added_by_table.setdefault(table_name, [])
            bucket.extend(c for c in columns if c not in bucket)

    for table_name in sorted(added_by_table):
        table = tables[table_name]
        added = added_by_table[table_name]
        for column_name in added:
            plan.add(
                "add_column", _add_column_sql(table, table.columns[column_name], dialect),
                table=table_name, column=column_name,
            )
        added_set = set(added)
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            if {c.name for c in index.columns} & added_set:
                plan.add(
                    "create_index",
                    str(CreateIndex(index).compile(dialect=dialect)).strip(),
                    table=table_name,
                )
        if is_mysql:
            for constraint in _fk_constraints_for_columns(table, added_set):
                plan.add(
                    "add_foreign_key",
                    str(AddConstraint(constraint).compile(dialect=dialect)).strip(),
                    table=table_name,
                )

    for manifest in selected:
        for table_name, column_name in manifest.widened_columns:
            column = tables[table_name].columns[column_name]
            if is_mysql:
                plan.add(
                    "modify_column", _modify_column_sql(tables[table_name], column, dialect),
                    table=table_name, column=column_name,
                )
            else:
                plan.note(
                    f"[{manifest.key}] {table_name}.{column_name}: widened VARCHAR — "
                    "no-op on SQLite (lengths are not enforced)."
                )
    return plan


# ---------------------------------------------------------------------------
# Output / execution
# ---------------------------------------------------------------------------
def print_plan(plan: Plan) -> None:
    counts: dict[str, int] = {}
    for kind, _ in plan.statements:
        counts[kind] = counts.get(kind, 0) + 1
    if plan.statements:
        print(f"plan: {len(plan.statements)} statement(s) "
              f"({', '.join(f'{k}={v}' for k, v in sorted(counts.items()))})")
        for kind, sql in plan.statements:
            print(f"\n-- [{kind}]")
            print(sql + ";")
    else:
        print("plan: nothing to do — schema matches the models.")
    for note in plan.notes:
        print(f"\nnote: {note}")


def apply_plan(engine: Engine, plan: Plan) -> None:
    is_mysql = engine.dialect.name in ("mysql", "mariadb")
    if is_mysql:
        print("note: MySQL commits each DDL statement implicitly — statements are "
              "individually atomic but the batch is not; the plan is safe to re-run.")
    with engine.begin() as connection:  # transactional on SQLite; best-effort on MySQL
        for i, (kind, sql) in enumerate(plan.statements, 1):
            print(f"applying [{i}/{len(plan.statements)}] {kind}: "
                  f"{sql.splitlines()[0][:100]}")
            connection.exec_driver_sql(sql)
    print("apply: done.")


def resolve_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    # Same resolution as the app (app/db/session.py normalizes relative sqlite
    # paths against backend/ so cwd does not matter).
    from app.db.session import DATABASE_URL
    return DATABASE_URL


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Additive schema migration for the case-files and groups passes "
                    "(dry run by default; exits 1 on drift so it can gate CI)."
    )
    parser.add_argument("--url", help="database URL (default: the app's DATABASE_URL)")
    parser.add_argument("--apply", action="store_true",
                        help="execute the plan (default: print it and exit)")
    parser.add_argument("--dialect", choices=["mysql", "sqlite"],
                        help="OFFLINE mode: print the full plan (case-files + groups) "
                             "compiled for this dialect without connecting to any database")
    parser.add_argument("--pass", dest="passes", choices=list(OFFLINE_PASSES), default="all",
                        help="OFFLINE mode only: 'all' (default) for a database that "
                             "predates both passes; 'groups' for the delta on a database "
                             "that already carries the case-files schema")
    args = parser.parse_args()

    errors = validate_manifest()
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 2

    if args.dialect:
        if args.apply:
            print("error: --dialect is offline (print-only); it cannot be combined with --apply.")
            return 2
        if args.dialect == "mysql":
            from sqlalchemy.dialects import mysql as mysql_dialect
            dialect = mysql_dialect.dialect()
        else:
            from sqlalchemy.dialects import sqlite as sqlite_dialect
            dialect = sqlite_dialect.dialect()
        print(f"offline plan for dialect: {args.dialect}, pass: {args.passes} "
              "(no database contacted)")
        plan = build_offline_plan(dialect, passes=args.passes)
        print_plan(plan)
        return 0

    url = resolve_url(args.url)
    engine = create_engine(url, future=True)
    print(f"database: {engine.url.render_as_string(hide_password=True)} "
          f"(dialect: {engine.dialect.name})")

    plan = build_online_plan(engine)
    print_plan(plan)

    if not plan.has_drift:
        return 0
    if not args.apply:
        print("\ndry run: drift detected — re-run with --apply to execute the plan above.")
        return 1

    apply_plan(engine, plan)
    verify = build_online_plan(engine)
    if verify.has_drift:
        print(f"error: {len(verify.statements)} statement(s) still pending after apply:")
        print_plan(verify)
        return 2
    print("verify: zero drift — schema now matches the models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
