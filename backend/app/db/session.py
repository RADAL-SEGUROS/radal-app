"""Database engine, session factory and the get_db FastAPI dependency."""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import BASE_DIR, settings


def _normalized_sqlite_url(url: str) -> str:
    """Resolve a relative sqlite path against backend/ so cwd doesn't matter."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix):]
        if raw.startswith("./"):
            raw = raw[2:]
        p = Path(raw)
        if not p.is_absolute():
            p = BASE_DIR / p
        return f"{prefix}{p}"
    return url


DATABASE_URL = _normalized_sqlite_url(settings.DATABASE_URL)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, class_=Session, future=True
)


def get_db() -> Generator[Session, None, None]:
    """Yield a scoped DB session; closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
