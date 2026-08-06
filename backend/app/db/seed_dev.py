"""Minimal bootstrap so an empty database is usable.

    python -m app.db.seed_dev

Creates the schema and exactly ONE row: a Radal platform administrator. Nothing
else — the fixture corpus lives in ``app.db.import_fixtures`` and this module
must stay small enough to run against a production-shaped database without
inventing domain data.

A platform user is deliberately homeless: ``broker_id`` / ``insurer_id`` /
``insured_id`` are all NULL, because Radal staff are not a tenant. They are the
only actor that can exist before the first broker is onboarded.

Idempotent: running it twice updates the existing admin's password and role
rather than failing on the unique email.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import User
from app.models.enums import UserType

DEFAULT_EMAIL = "admin@radal.cl"
DEFAULT_PASSWORD = "radal1234"
DEFAULT_NAME = "Radal Platform Admin"
PLATFORM_ROLE = "platform_admin"


def ensure_platform_admin(
    db: Session,
    *,
    email: str = DEFAULT_EMAIL,
    password: str = DEFAULT_PASSWORD,
    full_name: str = DEFAULT_NAME,
) -> tuple[User, bool]:
    """Create (or refresh) the platform admin. Returns ``(user, created)``."""
    normalized = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized))
    created = user is None
    if user is None:
        user = User(email=normalized)
        db.add(user)
    user.hashed_password = hash_password(password)
    user.full_name = full_name
    user.job_title = "Administrador de plataforma"
    user.user_type = UserType.PLATFORM
    user.role = PLATFORM_ROLE
    user.is_active = True
    user.broker_id = None
    user.insurer_id = None
    user.insured_id = None
    db.flush()
    return user, created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.seed_dev",
        description="Create the schema and one platform admin user.",
    )
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args(argv)

    Base.metadata.create_all(bind=engine)
    print(f"schema ready on {engine.url.render_as_string(hide_password=True)}")

    db = SessionLocal()
    try:
        user, created = ensure_platform_admin(
            db, email=args.email, password=args.password, full_name=args.name
        )
        db.commit()
        verb = "created" if created else "updated"
        print(f"platform admin {verb}: {user.email} / {args.password} (role={user.role})")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
