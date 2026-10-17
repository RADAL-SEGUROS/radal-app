"""v12 data step CLI — the closed ramo catalogue + cobranza defaults. Dry-run first.

Run AFTER ``python -m scripts.migrate_case_files --pass v12 --apply`` reports
zero drift (docs/v12/08-infra.md §6):

    python -m scripts.seed_v12                     # dry run vs DATABASE_URL (rolls back)
    python -m scripts.seed_v12 --apply             # commit
    python -m scripts.seed_v12 --url mysql+pymysql://user:pw@host/radal --apply

Idempotent: a second ``--apply`` reports all zeros. The logic lives in
``app.db.seed_v12`` (the backend image ships ``app/`` only); this module
re-exports it so ``from scripts.seed_v12 import seed_catalog`` works too.
No secrets are printed: URLs are rendered with the password hidden.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")  # allow `python -m scripts.seed_v12` from backend/

import app.db.base  # noqa: E402,F401  (registers every model)
from app.db.seed_v12 import (  # noqa: E402,F401  (re-exported)
    cmf_codes_for,
    load_catalog_rows,
    run,
    seed_catalog,
    seed_collection_defaults,
)


def _resolve_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    from app.db.session import DATABASE_URL

    return DATABASE_URL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed_v12",
        description="Upsert the v12 ramo catalogue and cobranza defaults (dry run by default).",
    )
    parser.add_argument("--url", help="database URL (default: the app's DATABASE_URL)")
    parser.add_argument("--apply", action="store_true", help="commit (default: roll back)")
    args = parser.parse_args(argv)

    engine = create_engine(_resolve_url(args.url), future=True)
    print(f"database: {engine.url.render_as_string(hide_password=True)} "
          f"(dialect: {engine.dialect.name})")
    session = sessionmaker(bind=engine, future=True)()
    try:
        stats = run(session)
        for key, value in stats.items():
            print(f"  {key}: {value}")
        if args.apply:
            session.commit()
            print("apply: committed.")
        else:
            session.rollback()
            changed = sum(stats.values())
            print("dry run: rolled back"
                  + (f" — {changed} change(s) pending; re-run with --apply." if changed else
                     " — nothing to do."))
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
