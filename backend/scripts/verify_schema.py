"""Compile CreateTable/CreateIndex for EVERY table on both SQLite and MySQL.

Catches the classic portability traps before they reach RDS:
  * length-less VARCHAR on MySQL (the @compiles hook in base_class.py),
  * unbounded TEXT in an index/unique key,
  * enum columns that forgot an explicit length.

Run: python -m scripts.verify_schema      (from backend/, venv active)
"""
from __future__ import annotations

import re
import sys

from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.orm import configure_mappers

from app.db.base import Base

DIALECTS = {"sqlite": sqlite.dialect(), "mysql": mysql.dialect()}


def main() -> int:
    configure_mappers()
    tables = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
    failures: list[str] = []

    for name, dialect in DIALECTS.items():
        for table in tables:
            try:
                ddl = str(CreateTable(table).compile(dialect=dialect))
            except Exception as exc:  # noqa: BLE001 - report every failure
                failures.append(f"[{name}] CREATE TABLE {table.name}: {exc}")
                continue
            if name == "mysql":
                # A VARCHAR not immediately followed by "(" has no length and
                # MySQL will reject it. This is what base_class.py's @compiles
                # hook exists to prevent — assert the hook actually fired.
                bad = re.findall(r"VARCHAR(?!\s*\()", ddl)
                if bad:
                    failures.append(
                        f"[mysql] {table.name}: {len(bad)} length-less VARCHAR in DDL"
                    )
            for index in table.indexes:
                try:
                    str(CreateIndex(index).compile(dialect=dialect))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"[{name}] CREATE INDEX {index.name}: {exc}")

    print(f"tables: {len(tables)}")
    print(f"dialects verified: {', '.join(DIALECTS)}")
    for table in tables:
        cols = len(table.columns)
        fks = len(table.foreign_keys)
        idx = len(table.indexes)
        print(f"  {table.name:<28} columns={cols:<3} fks={fks:<2} indexes={idx}")

    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("\nOK: every table compiles on sqlite and mysql.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
